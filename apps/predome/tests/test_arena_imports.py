"""Offline contracts for existing executed cohorts, never actual research writes."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tarfile
import time
import uuid

import pytest

from emeris_live import arena_imports as imports
from emeris_live.arena_store import canonical, sha


def run(*args):
    return subprocess.check_output(['openssl', *map(str, args)], stderr=subprocess.PIPE, text=True)


def add(events, kind, ident, payload, at):
    item = {'id': uuid.uuid4().hex, 'kind': kind, 'entity_id': ident, 'created_at': at.isoformat(),
        'payload': payload, 'previous_hash': events[-1]['event_hash'] if events else '0' * 64}
    item['event_hash'] = sha(item); item['seq'] = len(events) + 1
    events.append(item)
    return item


def repack(files):
    out = io.BytesIO()
    with gzip.GzipFile(fileobj=out, mode='wb', mtime=0) as zipped:
        with tarfile.open(fileobj=zipped, mode='w', format=tarfile.USTAR_FORMAT) as tar:
            for name, raw in sorted(files.items()):
                item = tarfile.TarInfo(name); item.size = len(raw); item.mode = 0o600
                tar.addfile(item, io.BytesIO(raw))
    return out.getvalue()


def update_manifest(files):
    value = imports.decode(files['manifest.json'])
    value['files'] = {name: {'sha256': imports.digest(raw), 'bytes': len(raw)}
        for name, raw in files.items() if name != 'manifest.json'}
    files['manifest.json'] = canonical(value).encode()
    return files


@pytest.fixture(scope='module')
def signed(tmp_path_factory):
    """A local test-only authority. Production pins have no CLI override."""
    root = tmp_path_factory.mktemp('import-test-ca')
    run('req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', root/'ca.key', '-out', root/'cacert.pem',
        '-subj', '/CN=Predome test authority', '-days', '2')
    run('req', '-new', '-newkey', 'rsa:2048', '-nodes', '-keyout', root/'tsa.key', '-out', root/'tsa.csr',
        '-subj', '/CN=Predome test timestamp signer')
    (root/'ext.cnf').write_text('basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\nextendedKeyUsage=critical,timeStamping\n')
    run('x509', '-req', '-in', root/'tsa.csr', '-CA', root/'cacert.pem', '-CAkey', root/'ca.key',
        '-CAcreateserial', '-out', root/'tsa.crt', '-days', '2', '-extfile', root/'ext.cnf')
    (root/'serial').write_text('01\n')
    (root/'ts.cnf').write_text(f'''[tsa]
default_tsa=testing
[testing]
dir={root}
serial={root}/serial
crypto_device=builtin
signer_cert={root}/tsa.crt
certs={root}/cacert.pem
signer_key={root}/tsa.key
signer_digest=sha256
default_policy=1.2.3.4
digests=sha256
accuracy=secs:1
ordering=yes
tsa_name=yes
ess_cert_id_chain=no
ess_cert_id_alg=sha256
''')
    pins = {name: imports.digest((root/name).read_bytes()) for name in imports.PINS}
    origin = datetime.now(timezone.utc) - timedelta(minutes=5)
    cutoff = origin - timedelta(minutes=5)
    deadline = origin + timedelta(days=1)
    events = []
    cohort = 'source-cohort'; target_id = 'source-target'; run_id = 'run-source'
    asset_raw = b'{"original_public_source":true}'
    asset_sha = imports.digest(asset_raw); source_url = 'https://example.org/clinical-source'
    budget = {'max_requests': 1}
    add(events, 'cohort_registered', cohort, {'protocol': {'maximum_slots': 1, 'execution_budget': budget},
        'slot_count': 1, 'target_ids': [target_id], 'clinical_prediction_validated': False}, origin)
    target = {'id': target_id, 'project_id': 'drug-a', 'nct_id': 'NCT12345678',
        'endpoint': 'Prespecified primary endpoint', 'population': 'Registered primary population',
        'comparison': 'Drug A versus control', 'success_definition': 'Official primary endpoint success',
        'analysis_timepoint': 'Week 80', 'information_cutoff_utc': cutoff.isoformat(),
        'submission_deadline_utc': deadline.isoformat(), 'cohort_id': cohort, 'result': None,
        'readout_screen': {'status': 'no_decisive_public_result_found', 'checked_at_utc': origin.isoformat(),
            'sources': [{'url': source_url}]}, 'human_review_count': 0, 'clinical_prediction_validated': False}
    add(events, 'target_registered', target_id, target, origin)
    packet = {'target': {k: target[k] for k in ('endpoint', 'population', 'comparison', 'success_definition')},
        'data_cutoff': cutoff.isoformat(), 'evidence': [{'id': 'public-1', 'kind': 'trial_registry',
            'text': 'Observed prespecified previous study.', 'source_url': source_url,
            'source_sha256': asset_sha, 'available_at': cutoff.isoformat()}]}
    prompt = sha(['a' * 64]); input_hash = 'b' * 64
    slot = {'cohort_id': cohort, 'target_id': target_id, 'model_id': 'glm-5.2', 'requested_model': 'glm-5.2',
        'provider': 'openai_compatible', 'provider_config_snapshot': {'config_sha256': 'c' * 64},
        'track': 'same_evidence', 'condition': 'clinical_only', 'scope': 'prospective_challenge',
        'workflow_version': 'test-workflow-1', 'packet': packet, 'input_sha256': sha(packet),
        'expected_provider_input_sha256': input_hash, 'expected_prompt_sha256': prompt,
        'execution_budget_sha256': sha(budget)}
    add(events, 'slot_registered', run_id, slot, origin)
    proof_dirs = {}; proof_fields = {}

    def sign(purpose):
        generated, receipt, metadata = _sign_test_proof(root, events, purpose)
        proof_dirs[purpose] = root/purpose
        proof_fields[purpose] = metadata
        return generated, receipt

    reg_time, reg_receipt = sign('registration')
    add(events, 'timestamp_attested', 'attestation-registration', reg_receipt, reg_time)
    # Wait only for the next second so an OpenSSL second-precision genTime can
    # cover a genuinely later start and completion without inventing a clock.
    while datetime.now(timezone.utc).replace(microsecond=0) <= reg_time:
        time.sleep(.02)
    completed_at = datetime.now(timezone.utc).replace(microsecond=0)
    add(events, 'run_started', run_id, {'input_sha256': sha(packet), 'execution_budget_sha256': sha(budget)}, completed_at)
    attempt = {'prompt_sha256': 'a' * 64, 'request_sha256': 'd' * 64, 'response_sha256': 'e' * 64,
        'requested_model': 'glm-5.2', 'input_sha256': input_hash}
    completed = {'status': 'predicted', 'probability': .68, 'model_id': 'glm-5.2', 'requested_model': 'glm-5.2',
        'returned_model': 'glm-5.2', 'workflow_version': slot['workflow_version'], 'provider': slot['provider'],
        'config_sha256': 'c' * 64, 'provider_input_sha256': input_hash, 'prompt_sha256': prompt,
        'contract_complete': True, 'submission_on_time': True, 'evidence_ids': ['public-1'],
        'supporting_evidence_ids': ['public-1'], 'opposing_evidence_ids': [],
        'reasoning': 'Observed previous study.', 'key_assumptions': [], 'key_unknowns': [], 'attempts': [attempt],
        'request_sha256': sha(['d' * 64]), 'provider_response_sha256': sha(['e' * 64]), 'clinical_prediction_validated': False}
    completed['output_sha256'] = sha(completed)
    add(events, 'run_completed', run_id, completed, completed_at)
    pred_time, pred_receipt = sign('predictions')
    add(events, 'timestamp_attested', 'attestation-predictions', pred_receipt, pred_time)
    from emeris_live.arena_citations import validate_review
    review = validate_review({**slot, **completed}, {'scope': 'frozen_packet_claim_source_pairs',
        'claim_inventory_complete': False, 'run_output_sha256': completed['output_sha256'],
        'inventory_method': 'Selected synthetic statement-source check',
        'pairs': [{'claim_id': 'claim-a', 'claim_text': 'Observed previous study.', 'source_id': 'public-1',
            'source_sha256': asset_sha, 'verdict': 'partially_supports', 'note': 'Prior study does not resolve this endpoint.',
            'packet_excerpt': 'Observed prespecified previous study.'}]})
    review = {'assessment': review, 'reviewer_id': 'ai-source-a', 'reviewer_kind': 'ai', 'supersedes': None}
    review['review_sha256'] = sha(review)
    add(events, 'citation_review', run_id, review, pred_time)
    assets = [{'sha256': asset_sha, 'bytes': len(asset_raw), 'content_type': 'application/json',
        'source_url': source_url, 'bundle_path': 'assets/' + asset_sha + '.bin'}]
    files = {'source-events.json': canonical(events).encode(), assets[0]['bundle_path']: asset_raw}
    for purpose, directory in proof_dirs.items():
        files.update({'proofs/' + purpose + '/' + p.name: p.read_bytes() for p in directory.iterdir()})
    manifest = {'schema_version': imports.BUNDLE_VERSION, 'source_cohort_id': cohort,
        'source_ledger_sha256': events[-1]['event_hash'], 'assets': assets, 'files': {}}
    files['manifest.json'] = canonical(manifest).encode(); update_manifest(files)
    raw = repack(files); path = root/'bundle.tar.gz'; path.write_bytes(raw)
    payload = {'schema_version': imports.VERSION, 'bundle_sha256': imports.digest(raw), 'source_events': events,
        'source_ledger_sha256': events[-1]['event_hash'], 'source_cohort_id': cohort,
        'proofs': proof_fields, 'assets': assets, 'clinical_prediction_validated': False}
    return {'root': root, 'path': path, 'raw': raw, 'pins': pins, 'events': events, 'payload': payload,
        'files': files, 'asset_raw': asset_raw, 'proof_dirs': proof_dirs}


@pytest.fixture
def authorized(signed, monkeypatch):
    monkeypatch.setattr(imports, 'PINS', signed['pins'])
    return signed


def rehash(events):
    previous = '0' * 64
    for index, event in enumerate(events, 1):
        event['previous_hash'] = previous; event['seq'] = index
        event['event_hash'] = sha({k: v for k, v in event.items() if k not in ('seq', 'event_hash')})
        previous = event['event_hash']


def test_real_offline_signature_nonce_and_original_epochs(authorized):
    value = imports.verify_bundle(authorized['path'], authorized['payload']['bundle_sha256'])
    assert value == authorized['payload']
    assert len(value['source_events']) == 8
    assert value['proofs']['predictions']['covered_event_count'] == 6
    assert value['source_events'][-1]['kind'] == 'citation_review'
    assert 'signature_verified' not in value['proofs']['predictions']


def test_production_pins_are_not_caller_selectable(signed):
    with pytest.raises(ValueError, match='fixed FreeTSA pin'):
        imports.verify_bundle(signed['path'], signed['payload']['bundle_sha256'])


@pytest.mark.parametrize('change', ['response', 'query', 'self_report', 'prefix', 'original_asset'])
def test_false_claims_cannot_replace_signed_bytes(authorized, tmp_path, change):
    files = dict(authorized['files'])
    if change == 'response':
        files['proofs/predictions/manifest.tsr'] = b'not a signature'
    elif change == 'query':
        # Valid DER, with a different actual nonce and message imprint.
        files['proofs/predictions/manifest.tsq'] = files['proofs/registration/manifest.tsq']
    elif change == 'self_report':
        receipt = imports.decode(files['proofs/predictions/receipt.json'])
        receipt['signature_verified'] = True; receipt['generated_at_utc'] = '2001-01-01T00:00:00+00:00'
        files['proofs/predictions/receipt.json'] = canonical(receipt).encode()
    elif change == 'prefix':
        manifest = imports.decode(files['proofs/predictions/manifest.json'])
        manifest['snapshot']['events'][2]['payload']['requested_model'] = 'another-model'
        files['proofs/predictions/manifest.json'] = canonical(manifest).encode()
    else:
        files[authorized['payload']['assets'][0]['bundle_path']] = b'changed public source'
    update_manifest(files); raw = repack(files); path = tmp_path/'changed.tar.gz'; path.write_bytes(raw)
    with pytest.raises(ValueError):
        imports.verify_bundle(path, imports.digest(raw))


@pytest.mark.parametrize('kind', ['result_review', 'result_adjudicated', 'reviewer_enrollment',
    'scientific_adjudication_acceptance', 'scientific_adjudication_revocation', imports.IMPORT_KIND])
def test_rejects_imported_results_authority_and_nested_streams(authorized, kind):
    payload = deepcopy(authorized['payload']); payload['source_events'][-1]['kind'] = kind
    rehash(payload['source_events']); payload['source_ledger_sha256'] = payload['source_events'][-1]['event_hash']
    with pytest.raises(ValueError, match='unsupported'):
        imports.validate_import_payload(payload)


@pytest.mark.parametrize('change', ['human_review', 'missing_slot', 'duplicate_complete', 'identity_override',
    'failed_nonnull', 'output_hash', 'input_contract', 'budget_contract', 'late_complete', 'claim_anchor'])
def test_source_semantic_rejections_even_with_rehashed_chain(authorized, change):
    payload = deepcopy(authorized['payload']); events = payload['source_events']
    if change == 'human_review':
        events[-1]['payload']['reviewer_kind'] = 'human'
    elif change == 'missing_slot':
        events.pop(2)
    elif change == 'duplicate_complete':
        events.insert(6, deepcopy(events[5])); events[6]['id'] = uuid.uuid4().hex
    elif change == 'identity_override':
        events[5]['payload']['target_id'] = 'replacement-target'
    elif change == 'failed_nonnull':
        events[5]['payload']['status'] = 'failed'
    elif change == 'output_hash':
        events[5]['payload']['probability'] = .99
    elif change == 'input_contract':
        events[5]['payload']['provider_input_sha256'] = 'f' * 64
    elif change == 'budget_contract':
        events[2]['payload']['execution_budget_sha256'] = 'f' * 64
    elif change == 'late_complete':
        events[5]['created_at'] = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    else:
        events[-1]['payload']['assessment']['pairs'][0]['packet_excerpt'] = 'invented source content'
        review = events[-1]['payload']; review['review_sha256'] = sha({k: v for k, v in review.items() if k != 'review_sha256'})
    rehash(events); payload['source_ledger_sha256'] = events[-1]['event_hash']
    with pytest.raises(ValueError):
        imports.validate_import_payload(payload)


def test_expired_now_is_not_reenrollment(authorized, monkeypatch):
    class Later(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(timezone.utc) + timedelta(days=2)
    monkeypatch.setattr(imports, 'datetime', Later)
    assert imports.validate_import_payload(deepcopy(authorized['payload']))['source_cohort_id'] == 'source-cohort'


def test_archive_digest_duplicate_json_unknown_members_and_trailing_data(authorized, tmp_path):
    with pytest.raises(ValueError, match='operator SHA'):
        imports.verify_bundle(authorized['path'], '0' * 64)
    with pytest.raises(ValueError, match='Duplicate JSON'):
        imports.decode(b'{"scope":1,"scope":2}')
    for extra in [b'hidden', authorized['raw']]:
        path = tmp_path/('trailing-' + str(len(extra))); path.write_bytes(authorized['raw'] + extra)
        with pytest.raises(ValueError, match='trailing content'):
            imports.verify_bundle(path, imports.digest(path.read_bytes()))
    files = dict(authorized['files']); files['other.txt'] = b'unknown'
    raw = repack(update_manifest(files)); path = tmp_path/'extra.tar.gz'; path.write_bytes(raw)
    with pytest.raises(ValueError, match='Unexpected'):
        imports.verify_bundle(path, imports.digest(raw))


def host_event(payload):
    return {'kind': imports.IMPORT_KIND, 'payload': payload, 'entity_id': 'import-' + payload['bundle_sha256'][:20],
        'created_at': datetime.now(timezone.utc).isoformat()}


def test_private_archive_idempotence_original_assets_and_original_proof_bytes(authorized, tmp_path):
    payload = imports.verify_bundle(authorized['path'], authorized['payload']['bundle_sha256'])
    before = authorized['path'].read_bytes()
    info = imports.stage_bundle(tmp_path, authorized['path'], payload)
    archive = tmp_path/info['relative_path']; stat_before = archive.stat()
    assert imports.stage_bundle(tmp_path, authorized['path'], payload) == info
    assert archive.stat().st_ino == stat_before.st_ino
    assert archive.stat().st_mtime_ns == stat_before.st_mtime_ns
    assert archive.stat().st_mode & 0o777 == 0o600
    assert archive.parent.stat().st_mode & 0o777 == 0o700
    events = [host_event(payload)]; asset = imports.read_asset(tmp_path, events, payload['assets'][0]['sha256'])
    assert asset['raw'] == authorized['asset_raw']
    assert imports.read_asset(tmp_path, events, '0' * 64) is None
    proof = imports.proof_files(tmp_path, events, payload['bundle_sha256'], 'predictions')
    assert set(proof) == imports.PROOF_FILES
    assert proof['receipt.json'] == authorized['files']['proofs/predictions/receipt.json']
    assert authorized['path'].read_bytes() == before
    assert not (tmp_path/'arena.sqlite3').exists()


def test_archive_refuses_overwrite_symlink_and_tampering(authorized, tmp_path):
    payload = authorized['payload']; archive = tmp_path/imports.DIRECTORY
    archive.mkdir(mode=0o700); path = archive/(payload['bundle_sha256'] + '.tar.gz')
    path.write_bytes(b'conflict'); path.chmod(0o600)
    with pytest.raises(ValueError, match='differs'):
        imports.stage_bundle(tmp_path, authorized['path'], payload)
    assert path.read_bytes() == b'conflict'
    path.unlink(); target = tmp_path/'outside'; target.write_bytes(b'outside')
    path.symlink_to(target)
    with pytest.raises(ValueError):
        imports.stage_bundle(tmp_path, authorized['path'], payload)
    assert target.read_bytes() == b'outside'
    path.unlink(); imports.stage_bundle(tmp_path, authorized['path'], payload)
    path.write_bytes(b'corrupt')
    with pytest.raises(ValueError, match='hash differs'):
        imports.read_asset(tmp_path, [host_event(payload)], payload['assets'][0]['sha256'])


def test_stream_duplicate_and_native_collision_are_not_new_executions(authorized):
    payload = authorized['payload']; host = host_event(payload)
    with pytest.raises(ValueError, match='more than once'):
        imports.imported_streams([host, deepcopy(host)])
    native = {'kind': 'target_registered', 'entity_id': 'source-target'}
    for events in ([native, host], [host, native]):
        with pytest.raises(ValueError, match='collision'):
            imports.imported_streams(events)


def test_prepare_reads_existing_source_db_only_and_preserves_raw_proofs(authorized, tmp_path):
    source = tmp_path/'source'; source.mkdir(); database = source/'arena.sqlite3'
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE arena_events(seq INTEGER,id TEXT,kind TEXT,entity_id TEXT,created_at TEXT,payload TEXT,previous_hash TEXT,event_hash TEXT)')
        for event in authorized['events']:
            db.execute('INSERT INTO arena_events VALUES(?,?,?,?,?,?,?,?)',
                tuple(canonical(event[k]) if k == 'payload' else event[k] for k in
                    ('seq', 'id', 'kind', 'entity_id', 'created_at', 'payload', 'previous_hash', 'event_hash')))
    original = database.read_bytes(); source_root = tmp_path/'originals'; source_root.mkdir()
    (source_root/'public.json').write_bytes(authorized['asset_raw'])
    asset = authorized['payload']['assets'][0]
    specification = {'schema_version': imports.ASSET_VERSION, 'assets': [{'path': 'public.json',
        **{k: asset[k] for k in ('sha256', 'content_type', 'source_url')}}]}
    source_manifest = tmp_path/'source-assets.json'; source_manifest.write_text(canonical(specification))
    output = tmp_path/'prepared.tar.gz'
    payload = imports.prepare_bundle(source, authorized['proof_dirs'], source_manifest, source_root, output)
    assert database.read_bytes() == original
    assert payload['source_events'] == authorized['events']
    assert imports.verify_bundle(output, imports.digest(output.read_bytes())) == payload
    assert not (source/'arena.sqlite3-wal').exists()
    assert imports._members(output.read_bytes())['proofs/registration/receipt.json'] == authorized['files']['proofs/registration/receipt.json']
    with pytest.raises(FileExistsError):
        imports.prepare_bundle(source, authorized['proof_dirs'], source_manifest, source_root, output)
    missing = tmp_path/'not-created'
    with pytest.raises(ValueError, match='never initializes'):
        imports.prepare_bundle(missing, authorized['proof_dirs'], source_manifest, source_root, tmp_path/'absent.tar.gz')
    assert not missing.exists()


def test_cli_missing_host_integration_never_constructs_a_ledger(tmp_path, monkeypatch, capsys):
    path = Path(__file__).parents[1]/'scripts/sync_arena_records.py'
    specification = importlib.util.spec_from_file_location('sync_arena_records_test', path)
    module = importlib.util.module_from_spec(specification); specification.loader.exec_module(module)
    from emeris_live.arena_store import ArenaLedger
    monkeypatch.delattr(ArenaLedger, 'import_sealed_cohort', raising=False)
    with pytest.raises(SystemExit) as exc:
        module.main(['import', '--data-dir', str(tmp_path/'host'), '--bundle', str(tmp_path/'absent.tar.gz'), '--sha256', 'a' * 64])
    assert exc.value.code == 2
    assert 'integration is not installed' in capsys.readouterr().err
    assert not (tmp_path/'host').exists()


@pytest.mark.parametrize('kind', ['symlink', 'duplicate', 'traversal', 'gnu_longname'])
def test_archive_rejects_extended_special_and_unsafe_members(kind):
    packed = io.BytesIO()
    with gzip.GzipFile(fileobj=packed, mode='wb', mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode='w', format=tarfile.GNU_FORMAT) as archive:
            name = '../outside' if kind == 'traversal' else 'a' * 101 if kind == 'gnu_longname' else 'regular.txt'
            entry = tarfile.TarInfo(name); entry.size = 1
            if kind == 'symlink':
                entry.type = tarfile.SYMTYPE; entry.linkname = '/tmp/outside'; entry.size = 0
                archive.addfile(entry)
            else:
                archive.addfile(entry, io.BytesIO(b'x'))
                if kind == 'duplicate':
                    archive.addfile(entry, io.BytesIO(b'x'))
    with pytest.raises(ValueError):
        imports._members(packed.getvalue())


def test_expanded_data_is_bounded(authorized, monkeypatch):
    monkeypatch.setattr(imports, 'MAX_EXPANDED', 100)
    with pytest.raises(ValueError, match='exceeds bounds'):
        imports._members(authorized['raw'])


@pytest.mark.parametrize('value', [None, [], True, {}, {'schema_version': imports.VERSION}])
def test_malformed_payload_fails_with_explicit_contract_error(value):
    with pytest.raises(ValueError):
        imports.validate_import_payload(value)


def test_wal_source_is_read_without_creating_or_updating_source_state(authorized, tmp_path):
    source = tmp_path/'source-wal'; source.mkdir(); database = source/'arena.sqlite3'
    db = sqlite3.connect(database)
    try:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE arena_events(seq INTEGER,id TEXT,kind TEXT,entity_id TEXT,created_at TEXT,payload TEXT,previous_hash TEXT,event_hash TEXT)')
        db.commit()
        for event in authorized['events']:
            db.execute('INSERT INTO arena_events VALUES(?,?,?,?,?,?,?,?)',
                tuple(canonical(event[k]) if k == 'payload' else event[k] for k in
                    ('seq', 'id', 'kind', 'entity_id', 'created_at', 'payload', 'previous_hash', 'event_hash')))
        db.commit()
        before = {p.name: p.read_bytes() for p in source.iterdir()}
        assert imports._source_events(source) == authorized['events']
        assert {p.name: p.read_bytes() for p in source.iterdir()} == before
    finally:
        db.close()


def _sign_test_proof(root, events, purpose, *, authority_root=None, nonce=True):
    """Shared test-only signer; never selectable by production CLI input."""
    authority_root = root if authority_root is None else authority_root
    pins = {name: imports.digest((authority_root/name).read_bytes()) for name in imports.PINS}
    directory = root/purpose; directory.mkdir()
    manifest = {'schema_version': 'predome-arena-attestation-1', 'purpose': purpose,
        'ledger_sha256': events[-1]['event_hash'], 'covered_event_count': len(events),
        'snapshot': {'events': deepcopy(events), 'ledger_sha256': events[-1]['event_hash']},
        'trust_note': 'Synthetic test authority; not research evidence.'}
    (directory/'manifest.json').write_text(canonical(manifest))
    for name in pins:
        (directory/name).write_bytes((authority_root/name).read_bytes())
    args = ['ts', '-query', '-data', directory/'manifest.json', '-sha256', '-cert', '-out', directory/'manifest.tsq']
    if not nonce:
        args.append('-no_nonce')
    run(*args)
    run('ts', '-reply', '-config', authority_root/'ts.cnf', '-section', 'testing',
        '-queryfile', directory/'manifest.tsq', '-out', directory/'manifest.tsr')
    reply = run('ts', '-reply', '-in', directory/'manifest.tsr', '-text')
    text = next(line.split(': ', 1)[1] for line in reply.splitlines() if line.startswith('Time stamp: '))
    generated = datetime.strptime(text, '%b %d %H:%M:%S %Y GMT').replace(tzinfo=timezone.utc)
    (directory/'verification.txt').write_text('Synthetic fixture source verification\n')
    files = {p.name: imports.digest(p.read_bytes()) for p in directory.iterdir()}
    receipt = {'id': purpose + '-source', 'purpose': purpose, 'ledger_sha256': manifest['ledger_sha256'],
        'covered_event_count': len(events), 'tsa': 'test-only', 'generated_at_utc': generated.isoformat(),
        'verified_at_utc': datetime.now(timezone.utc).isoformat(), 'signature_verified': False,
        'verification_method': 'untrusted_self_report', 'manifest_sha256': files['manifest.json'],
        'timestamp_response_sha256': files['manifest.tsr'], 'authority_cert_sha256': pins['tsa.crt'],
        'ca_cert_sha256': pins['cacert.pem'], 'public_url': 'https://example.org/proof/' + purpose, 'files': files}
    (directory/'receipt.json').write_text(canonical(receipt))
    metadata = {k: receipt[k] for k in ('purpose', 'ledger_sha256', 'covered_event_count',
        'generated_at_utc', 'manifest_sha256', 'timestamp_response_sha256', 'authority_cert_sha256', 'ca_cert_sha256')}
    metadata.update(query_sha256=files['manifest.tsq'],
        files={**files, 'receipt.json': imports.digest((directory/'receipt.json').read_bytes())})
    return generated, receipt, metadata


@pytest.fixture
def bundle_factory(signed, monkeypatch, tmp_path):
    """Reusable real-signature fixtures for host/API integration tests.

    Import this fixture in another test module via ``from test_arena_imports
    import bundle_factory, signed``. Call factory(model_ids=(...),
    include_patents=True, statuses={('kimi-k3', 'clinical_only'): 'failed'}).
    Each call returns path/raw/payload/events/files/proof_dirs/pins/asset_raw,
    target_id and run_ids. Every scientific value is explicit synthetic input.
    ``query_nonce=False`` creates a deliberately unacceptable proof fixture.
    """
    monkeypatch.setattr(imports, 'PINS', signed['pins'])

    def factory(*, model_ids=('glm-5.2',), include_patents=False, cohort_id=None, statuses=None, query_nonce=True):
        cohort_id = cohort_id or 'test-import-' + uuid.uuid4().hex
        target_id = cohort_id + '-target'; root = tmp_path/uuid.uuid4().hex; root.mkdir()
        source = deepcopy(signed['events']); origin = datetime.now(timezone.utc) - timedelta(minutes=5)
        target = deepcopy(source[1]['payload']); target.update(id=target_id, cohort_id=cohort_id,
            trial_name='Synthetic TRIUMPH comparison', drug='Synthetic study drug', phase='Phase 3',
            study_design='Randomized double-blind active comparator', trial_status='ACTIVE_NOT_RECRUITING')
        target['information_cutoff_utc'] = (origin - timedelta(minutes=5)).isoformat()
        target['submission_deadline_utc'] = (origin + timedelta(days=1)).isoformat()
        target['readout_screen']['checked_at_utc'] = origin.isoformat()
        registry = {'protocolSection': {'identificationModule': {'nctId': target['nct_id'],
            'briefTitle': target['trial_name'], 'acronym': 'SYNTHETIC-TRIUMPH'},
            'statusModule': {'overallStatus': 'ACTIVE_NOT_RECRUITING'},
            'designModule': {'phases': ['PHASE3'], 'studyType': 'INTERVENTIONAL',
                'designInfo': {'allocation': 'RANDOMIZED', 'interventionModel': 'PARALLEL',
                    'maskingInfo': {'masking': 'DOUBLE'}}},
            'armsInterventionsModule': {'interventions': [{'type': 'DRUG', 'name': 'Synthetic study drug'}]},
            'outcomesModule': {'primaryOutcomes': [{'measure': target['endpoint'], 'timeFrame': 'Week 80'}]}}}
        registry_raw = canonical(registry).encode(); registry_sha = imports.digest(registry_raw)
        public_url = 'https://clinicaltrials.gov/api/v2/studies/' + target['nct_id']
        target['readout_screen']['sources'] = [{'url': public_url}]
        packet = deepcopy(source[2]['payload']['packet'])
        packet['data_cutoff'] = target['information_cutoff_utc']
        packet['target'] = {k: target[k] for k in ('endpoint', 'population', 'comparison', 'success_definition')}
        packet['evidence'][0].update(source_url=public_url, source_sha256=registry_sha,
            available_at=packet['data_cutoff'], kind='trial_registry')
        assets = [{'sha256': registry_sha, 'bytes': len(registry_raw), 'content_type': 'application/json',
            'source_url': public_url, 'bundle_path': 'assets/' + registry_sha + '.bin'}]
        asset_data = {assets[0]['bundle_path']: registry_raw}
        patent_packet = deepcopy(packet)
        if include_patents:
            patent_raw = b'%PDF-1.4\nSynthetic test patent original\n%%EOF\n'; patent_sha = imports.digest(patent_raw)
            patent_url = 'https://example.org/synthetic-patent.pdf'
            patent_packet['evidence'].append({'id': 'synthetic-patent-1', 'kind': 'patent',
                'source_sha256': patent_sha, 'source_url': patent_url, 'available_at': packet['data_cutoff'],
                'mapping_status': 'verified', 'review_status': 'verified', 'page': 1,
                'review_receipt': 'f' * 64, 'section_type': 'reported_experiment',
                'text': 'Synthetic reported cellular assay, not a clinical result.'})
            asset = {'sha256': patent_sha, 'bytes': len(patent_raw), 'content_type': 'application/pdf',
                'source_url': patent_url, 'bundle_path': 'assets/' + patent_sha + '.bin'}
            assets.append(asset); asset_data[asset['bundle_path']] = patent_raw
        events = []; budget = source[0]['payload']['protocol']['execution_budget']
        conditions = ('clinical_only', 'with_patents') if include_patents else ('clinical_only',)
        add(events, 'cohort_registered', cohort_id, {'protocol': {'maximum_slots': len(model_ids)*len(conditions),
            'execution_budget': budget, 'season_kind': 'registered_patent_pair_pilot' if include_patents else 'synthetic_test'},
            'slot_count': len(model_ids)*len(conditions), 'target_ids': [target_id], 'clinical_prediction_validated': False}, origin)
        add(events, 'target_registered', target_id, target, origin)
        slots = {}
        for model_id in model_ids:
            for condition in conditions:
                run_id = 'run-' + uuid.uuid4().hex
                slot = deepcopy(source[2]['payload']); chosen_packet = patent_packet if condition == 'with_patents' else packet
                slot.update(cohort_id=cohort_id, target_id=target_id, model_id=model_id, requested_model=model_id,
                    condition=condition, track='patent_ablation' if condition == 'with_patents' else 'same_evidence',
                    packet=deepcopy(chosen_packet), input_sha256=sha(chosen_packet),
                    expected_provider_input_sha256=sha({'test_packet': chosen_packet}))
                slot['provider_config_snapshot']['config_sha256'] = sha(model_id)
                add(events, 'slot_registered', run_id, slot, origin); slots[run_id] = slot
        reg_time, receipt, reg_meta = _sign_test_proof(root, events, 'registration', authority_root=signed['root'], nonce=query_nonce)
        add(events, 'timestamp_attested', 'attestation-' + uuid.uuid4().hex, receipt, reg_time)
        while datetime.now(timezone.utc).replace(microsecond=0) <= reg_time:
            time.sleep(.02)
        at = datetime.now(timezone.utc).replace(microsecond=0); outputs = {}
        for run_id, slot in slots.items():
            add(events, 'run_started', run_id, {'input_sha256': slot['input_sha256'],
                'execution_budget_sha256': slot['execution_budget_sha256']}, at)
            result = deepcopy(source[5]['payload']); status = (statuses or {}).get((slot['model_id'], slot['condition']), 'predicted')
            result.update(model_id=slot['model_id'], requested_model=slot['requested_model'], returned_model=slot['requested_model'],
                config_sha256=slot['provider_config_snapshot']['config_sha256'], status=status,
                probability=.68 if status == 'predicted' else None, contract_complete=status in ('predicted', 'abstained'),
                provider_input_sha256=slot['expected_provider_input_sha256'])
            if status == 'failed':
                result['error_code'] = 'invalid_json'
            result['attempts'][0].update(requested_model=slot['requested_model'], input_sha256=slot['expected_provider_input_sha256'])
            result['output_sha256'] = sha({k: v for k, v in result.items() if k != 'output_sha256'})
            add(events, 'run_completed', run_id, result, at); outputs[run_id] = result
        pred_time, receipt, pred_meta = _sign_test_proof(root, events, 'predictions', authority_root=signed['root'], nonce=query_nonce)
        add(events, 'timestamp_attested', 'attestation-' + uuid.uuid4().hex, receipt, pred_time)
        for run_id, result in outputs.items():
            if result['status'] != 'predicted':
                continue
            review = deepcopy(source[-1]['payload']); review['assessment']['run_output_sha256'] = result['output_sha256']
            review['assessment']['pairs'][0]['source_sha256'] = registry_sha
            review['review_sha256'] = sha({k: v for k, v in review.items() if k != 'review_sha256'})
            add(events, 'citation_review', run_id, review, pred_time)
        proof_dirs = {purpose: root/purpose for purpose in ('registration', 'predictions')}
        files = {'source-events.json': canonical(events).encode(), **asset_data}
        for purpose, directory in proof_dirs.items():
            files.update({'proofs/' + purpose + '/' + p.name: p.read_bytes() for p in directory.iterdir()})
        manifest = {'schema_version': imports.BUNDLE_VERSION, 'source_cohort_id': cohort_id,
            'source_ledger_sha256': events[-1]['event_hash'], 'assets': assets, 'files': {}}
        files['manifest.json'] = canonical(manifest).encode(); update_manifest(files)
        raw = repack(files); path = root/'bundle.tar.gz'; path.write_bytes(raw)
        payload = {'schema_version': imports.VERSION, 'bundle_sha256': imports.digest(raw), 'source_events': events,
            'source_ledger_sha256': events[-1]['event_hash'], 'source_cohort_id': cohort_id,
            'proofs': {'registration': reg_meta, 'predictions': pred_meta}, 'assets': assets, 'clinical_prediction_validated': False}
        return {'root': root, 'path': path, 'raw': raw, 'pins': signed['pins'], 'events': events,
            'payload': payload, 'files': files, 'asset_raw': registry_raw, 'proof_dirs': proof_dirs,
            'target_id': target_id, 'run_ids': list(slots)}
    return factory


def test_reusable_multi_model_patent_pair_factory(bundle_factory):
    value = bundle_factory(model_ids=('glm-5.2', 'kimi-k3'), include_patents=True,
        statuses={('kimi-k3', 'clinical_only'): 'failed'})
    payload = imports.verify_bundle(value['path'], imports.digest(value['raw']))
    assert payload == value['payload']
    assert sum(e['kind'] == 'run_completed' for e in payload['source_events']) == 4
    assert sum(e['kind'] == 'citation_review' for e in payload['source_events']) == 3
    assert payload['source_events'][0]['payload']['protocol']['season_kind'] == 'registered_patent_pair_pilot'


def test_timestamp_query_without_nonce_cannot_borrow_the_original_signed_response(authorized, tmp_path):
    """OpenSSL -queryfile alone accepts this; our explicit nonce gate must not."""
    files = dict(authorized['files']); directory = tmp_path/'nonce-free'; directory.mkdir()
    (directory/'manifest.json').write_bytes(files['proofs/predictions/manifest.json'])
    run('ts', '-query', '-data', directory/'manifest.json', '-sha256', '-cert', '-no_nonce', '-out', directory/'query.tsq')
    files['proofs/predictions/manifest.tsq'] = (directory/'query.tsq').read_bytes()
    receipt = imports.decode(files['proofs/predictions/receipt.json'])
    receipt['files']['manifest.tsq'] = imports.digest(files['proofs/predictions/manifest.tsq'])
    files['proofs/predictions/receipt.json'] = canonical(receipt).encode()
    events = imports.decode(files['source-events.json'])
    events[6]['payload'] = receipt; rehash(events)
    files['source-events.json'] = canonical(events).encode()
    manifest = imports.decode(files['manifest.json']); manifest['source_ledger_sha256'] = events[-1]['event_hash']
    files['manifest.json'] = canonical(manifest).encode(); update_manifest(files)
    raw = repack(files); path = tmp_path/'no-query-nonce.tar.gz'; path.write_bytes(raw)
    with pytest.raises(ValueError, match='explicit nonce'):
        imports.verify_bundle(path, imports.digest(raw))


def test_timestamp_query_and_response_cannot_both_omit_nonce(bundle_factory):
    sample = bundle_factory(query_nonce=False)
    with pytest.raises(ValueError, match='explicit nonce'):
        imports.verify_bundle(sample['path'], sample['payload']['bundle_sha256'])


@pytest.mark.parametrize('position', ['before', 'after'])
def test_source_event_ids_cannot_collide_with_any_host_event(authorized, position):
    payload = authorized['payload']; imported = host_event(payload)
    native = {'kind': 'unrelated_host_event', 'id': payload['source_events'][-1]['id'], 'entity_id': 'unrelated'}
    events = [native, imported] if position == 'before' else [imported, native]
    with pytest.raises(ValueError, match='event identity'):
        imports.imported_streams(events)


def test_event_ids_cannot_collide_between_independent_imported_streams(bundle_factory):
    first = bundle_factory(); second = bundle_factory()
    payload = deepcopy(second['payload']); events = payload['source_events']
    events[-1]['id'] = first['payload']['source_events'][-1]['id']
    rehash(events); payload['source_ledger_sha256'] = events[-1]['event_hash']
    imports.validate_import_payload(payload)
    with pytest.raises(ValueError, match='event identity'):
        imports.imported_streams([host_event(first['payload']), host_event(payload)])


def test_staging_and_stored_reads_bind_exact_wrapper_metadata(authorized, tmp_path):
    payload = deepcopy(authorized['payload']); payload['assets'][0]['content_type'] = 'text/plain'
    imports.validate_import_payload(payload)
    with pytest.raises(ValueError, match='differ'):
        imports.stage_bundle(tmp_path, authorized['path'], payload)
    assert not (tmp_path/imports.DIRECTORY).exists()
    original = authorized['payload']; imports.stage_bundle(tmp_path, authorized['path'], original)
    assert imports.verify_stored_bundle(tmp_path, original)['source_event_count'] == len(original['source_events'])
    with pytest.raises(ValueError, match='differ'):
        imports.verify_stored_bundle(tmp_path, payload)
    path = tmp_path/imports.DIRECTORY/(original['bundle_sha256'] + '.tar.gz'); path.unlink()
    with pytest.raises(ValueError, match='missing'):
        imports.verify_stored_bundle(tmp_path, original)
