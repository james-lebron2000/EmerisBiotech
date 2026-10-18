"""Small isolated synthetic intake tests; not clinical/issuer validation."""
import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import shutil
import threading
from http.server import ThreadingHTTPServer
from urllib.request import urlopen

import pytest

from vbt import disclosures
from vbt import official_disclosure_intake as intake
from vbt import official_pipeline_observations as observations
from vbt.market import Market
from vbt.disclosure_view import select_observations
from vbt.workbench import Workbench, handler


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    workspace = tmp_path / 'workspace'
    market = Market(workspace, tmp_path / 'state')
    source_id = 'synthetic_official_annual_test'
    contract = {'company': 'HK:09999', 'ticker': '09999', 'listing_name': 'SYNTHETIC',
                'issuer_name': 'Synthetic Issuer Test Only', 'issuer_group': 'synthetic',
                'aliases': ['Synthetic Sponsor Test Only'], 'relationship': 'issuer_legal_name',
                'url': 'https://fixture.example.test/annual.pdf', 'bundle_relative': 'examples/synthetic-observations.json'}
    monkeypatch.setitem(intake._CONTRACTS, source_id, contract)
    source = {'name': 'Explicit synthetic test only', 'companies': [contract['company']],
              'url': contract['url'], 'parser': intake.PARSER, 'intake_mode': intake.MODE,
              'bundle_relative': contract['bundle_relative'], 'scope': 'Synthetic historical test only'}
    monkeypatch.setitem(disclosures.SOURCES, source_id, source)
    market.add_company('HK', '09999', 'SYNTHETIC', contract['aliases'], 'Synthetic fixture only')
    with market.db() as db:
        db.execute('INSERT INTO listings VALUES (?,?,?,?,?,?,?,?)',
                   ('HK:HKEX:09999', 'HK', 'HKEX', '09999', 'SYNTHETIC', '', 'HK', '2026-10-17T00:00:00+00:00'))
    template = Path(__file__).resolve().parents[1] / 'examples/ascentage_pipeline_2025_observations.json'
    bundle = json.loads(template.read_text())
    bundle['source_id'] = source_id
    bundle['issuer'] = {'display_name': contract['issuer_name'], 'securities': [contract['company']], 'issuer_group': 'synthetic'}
    bundle['rows'] = [copy.deepcopy(bundle['rows'][0])]
    bundle['rows'][0].update(row_id=source_id + ':r01', drug_original='SYNTHETIC-001', drug_code_original='SYNTHETIC-001',
                             indication_original='Synthetic test indication', source_url=contract['url'])
    bundle['figure'].update(visible_row_count=1, visible_compound_count=1)
    bundle['review'].update(rows_visually_reviewed=1, claim_scope='Explicit synthetic test; not real clinical evidence')
    raw = b'%PDF-1.4\nSynthetic integrity test; no real PDF/clinical content.\n'
    raw_path = workspace / observations._CAPTURE_ROOT / 'synthetic-fixture.pdf'
    raw_path.parent.mkdir(parents=True)
    raw_path.write_bytes(raw)
    bundle['source'].update(url=contract['url'], raw_reference=str(raw_path.relative_to(workspace)),
                            raw_bytes=len(raw), raw_sha256=hashlib.sha256(raw).hexdigest())
    bundle['rows'][0]['source_sha256'] = bundle['source']['raw_sha256']
    bundle_path = workspace / contract['bundle_relative']
    bundle_path.parent.mkdir(parents=True)

    def save(seal=True):
        bundle_path.write_text(json.dumps(bundle))
        if seal:
            approved = observations.bundle_digest(bundle)
            monkeypatch.setitem(observations._APPROVED, source_id, approved)
            # Explicit synthetic approvals preserve earlier reviewed seals.
            monkeypatch.setitem(observations._REVIEWED_SEALS, source_id,
                                observations._REVIEWED_SEALS.get(source_id, frozenset()) | {approved})
    save()
    return market, source_id, contract, bundle, raw_path, bundle_path, save


def counts(market):
    with market.db() as db:
        return tuple(db.execute('SELECT count(*) FROM ' + table).fetchone()[0] for table in
                     ('disclosure_captures', 'disclosure_observations', 'official_disclosure_versions'))


def test_verified_pdf_download_preserves_source_bytes_and_browser_protections(prepared):
    market, source_id, _, _, raw_path, *_ = prepared
    result = intake.ingest_offline(market, source_id)
    from vbt.disclosure_view import render
    app = Workbench(market.root.parent.parent, market.state)
    body = render(app, {'company': 'HK:09999'})
    assert '下载封存 PDF' in body and 'PDF 作为原文附件下载' in body
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler(app))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{server.server_port}/market/disclosure-evidence?id={result["id"]}&file='
    try:
        with urlopen(base + 'source') as response:
            assert response.status == 200 and response.read() == raw_path.read_bytes()
            assert response.headers['Content-Type'] == 'application/pdf'
            assert response.headers['Content-Disposition'] == f'attachment; filename="disclosure-{result["id"]}.pdf"'
            assert response.headers['Content-Security-Policy'].startswith('sandbox; ')
            assert response.headers['X-Content-Type-Options'] == 'nosniff'
        with urlopen(base + 'record') as response:
            assert response.headers['Content-Disposition'] is None
            assert json.loads(response.read())['capture']['id'] == result['id']
        (market.root / result['archive_path']).write_bytes(b'%PDF-tampered')
        from urllib.error import HTTPError
        with pytest.raises(HTTPError) as error:
            urlopen(base + 'source')
        assert error.value.code == 404
    finally:
        server.shutdown();server.server_close()


def test_publishes_shared_contract_atomically_with_conservative_dates(prepared):
    market, source_id, contract, _, _, _, _ = prepared
    result = intake.ingest_offline(market, source_id)
    assert result['status'] == 'succeeded' and result['count'] == 1
    assert result['company'] == contract['company'] and not result['idempotent']
    assert counts(market) == (1, 1, 1)
    metadata = json.loads(result['metadata'])
    assert metadata['historical_disclosure'] and metadata['as_of'] == '2025-12-31'
    assert result['published_at'] is None and metadata['published_at_precision'] == 'unknown'
    assert metadata['human_sign_off'] is False and metadata['drug_ownership_verified'] is False
    assert metadata['scientific_review'] == 'pending'
    assert metadata['original_retrieved_at'] != metadata['imported_at']
    rows = disclosures.observations(market, contract['company'])
    assert len(rows) == 1 and rows[0]['phase'] == 'Marketed'
    assert rows[0]['data']['clinical_phase_code'] is None
    assert rows[0]['data']['human_sign_off'] is False and rows[0]['data']['historical_disclosure'] is True
    assert intake.verify_capture(market, result['id'])['integrity_status'] == 'verified'
    assert len(intake.intake_history(market, source_id)) == 1


def test_repeated_intake_is_idempotent_but_verification_history_is_retained(prepared):
    market, source_id, *_ = prepared
    first = intake.ingest_offline(market, source_id)
    seen = []
    second = intake.ingest_offline(market, source_id, on_start=seen.append)
    assert second['id'] == first['id'] and second['idempotent']
    assert seen == [first['id']] and counts(market) == (1, 1, 1)
    assert [r['status'] for r in intake.intake_history(market, source_id)] == ['verified_existing', 'published']


@pytest.mark.parametrize('workspace_form', ['relative', 'equivalent_root_alias'])
def test_archive_accepts_equivalent_workspace_roots_without_changing_evidence_scope(prepared, tmp_path, monkeypatch, workspace_form):
    market, source_id, *_ = prepared
    if workspace_form == 'relative':
        monkeypatch.chdir(tmp_path)
        market.root = Path('workspace/artifacts/market')
    else:
        alias = tmp_path / 'workspace-root-alias'
        alias.symlink_to(market.root.parent.parent, target_is_directory=True)
        market.root = alias / 'artifacts/market'
    first = intake.ingest_offline(market, source_id)
    assert first['status'] == 'succeeded', first['error']
    checked = intake.verify_capture(market, first['id'])
    assert checked['integrity_status'] == 'verified', checked['errors']
    second = intake.ingest_offline(market, source_id)
    assert second['idempotent'] and second['id'] == first['id']
    assert counts(market) == (1, 1, 1)
    with market.db() as db:
        reference = db.execute('SELECT manifest_path FROM official_disclosure_versions').fetchone()[0]
    assert reference == str(Path('disclosures/captures') / first['id'] / 'manifest.json')


def test_unknown_report_date_and_original_combined_phase_are_never_completed(prepared):
    market, source_id, _, bundle, _, _, save = prepared
    bundle['source'].update(content_as_of=None, content_as_of_precision='unresolved_report_date',
                            content_as_of_original='as of the date of this report')
    header = bundle['figure']['stage_columns'][-1]
    header['label_original'] = 'Phase II/III'
    bundle['rows'][0]['phase_original'] = 'Phase II/III'
    save()
    result = intake.ingest_offline(market, source_id)
    assert result['status'] == 'succeeded' and result['as_of'] is None
    assert json.loads(result['metadata'])['as_of_precision'] == 'unresolved_report_date'
    row = disclosures.observations(market)[0]
    assert row['phase'] == 'Phase II/III' and row['data']['clinical_phase_code'] is None
    assert intake.verify_capture(market, result['id'])['as_of'] is None


@pytest.mark.parametrize('kind', ['missing', 'truncated', 'same_size_tamper', 'bundle_change'])
def test_failed_input_never_replaces_published_version(prepared, kind):
    market, source_id, _, bundle, raw, _, save = prepared
    good = intake.ingest_offline(market, source_id)
    if kind == 'missing':
        raw.unlink()
    elif kind == 'truncated':
        raw.write_bytes(b'%PDF-')
    elif kind == 'same_size_tamper':
        data = raw.read_bytes()
        raw.write_bytes(data[:-1] + b'x')
    else:
        bundle['rows'][0]['indication_original'] = 'Unreviewed replacement'
        save(seal=False)
    failed = intake.ingest_offline(market, source_id)
    assert failed['status'] == 'parse_blocked' and failed['count'] == 0
    assert counts(market) == (2, 1, 1)
    assert disclosures.observations(market)[0]['capture_id'] == good['id']
    # A separate accurate sealed archive remains verifiable without the input folder.
    assert intake.verify_capture(market, good['id'])['integrity_status'] == 'verified'
    assert disclosures.observations(market)[0]['prior_capture_retained']


@pytest.mark.parametrize('kind', ['missing_company', 'wrong_alias', 'wrong_listing', 'wrong_code', 'wrong_source'])
def test_current_security_and_exact_sponsor_binding_are_required(prepared, kind):
    market, source_id, *_ = prepared
    with market.db() as db:
        if kind == 'missing_company':
            db.execute('DELETE FROM companies')
        elif kind == 'wrong_alias':
            db.execute('UPDATE companies SET aliases=?', (json.dumps(['Unrelated sponsor']),))
        elif kind == 'wrong_listing':
            db.execute("UPDATE listings SET name='UNRELATED ISSUER'")
        elif kind == 'wrong_code':
            db.execute("UPDATE companies SET ticker='08888'")
        else:
            db.execute("UPDATE listings SET source='unverified-upload'")
    result = intake.ingest_offline(market, source_id)
    assert result['status'] == 'parse_blocked' and result['count'] == 0
    assert counts(market) == (1, 0, 0)


def test_binding_is_rechecked_inside_publication_transaction(prepared):
    market, source_id, *_ = prepared
    def change_identity(_):
        with market.db() as db:
            db.execute("UPDATE listings SET name='RECYCLED STOCK CODE'")
    result = intake.ingest_offline(market, source_id, on_start=change_identity)
    assert result['status'] == 'parse_blocked' and counts(market) == (1, 0, 0)


@pytest.mark.parametrize('stage', ['before', 'after_start', 'after_archive'])
def test_cancellation_is_distinct_and_never_publishes_partial_rows(prepared, monkeypatch, stage):
    market, source_id, *_ = prepared
    state = {'cancel': stage == 'before'}
    def on_start(_):
        if stage == 'after_start':
            state['cancel'] = True
    original = intake._archive
    def archive(*args):
        result = original(*args)
        if stage == 'after_archive':
            state['cancel'] = True
        return result
    monkeypatch.setattr(intake, '_archive', archive)
    result = intake.ingest_offline(market, source_id, cancelled=lambda: state['cancel'], on_start=on_start)
    assert result['status'] == 'cancelled' and counts(market) == (1, 0, 0)
    assert market.claim('disclosure:' + source_id)


def test_expired_lease_prevents_publication(prepared):
    market, source_id, *_ = prepared
    def lose(_):
        with market.db() as db:
            db.execute('UPDATE leases SET until=0')
    result = intake.ingest_offline(market, source_id, on_start=lose)
    assert result['status'] == 'superseded' and counts(market) == (1, 0, 0)


def test_interrupt_then_retry_publishes_only_one_complete_version(prepared):
    market, source_id, *_ = prepared
    def interrupt(_):
        raise KeyboardInterrupt('Synthetic crash after capture allocation')
    with pytest.raises(KeyboardInterrupt):
        intake.ingest_offline(market, source_id, on_start=interrupt)
    result = intake.ingest_offline(market, source_id)
    assert result['status'] == 'succeeded' and counts(market) == (2, 1, 1)
    assert any(row['status'] == 'interrupted' for row in disclosures.captures(market))


def test_sql_failure_rolls_back_all_observations_and_version(prepared):
    market, source_id, *_ = prepared
    intake.initialize_storage(market)
    with market.db() as db:
        db.execute("CREATE TRIGGER synthetic_abort BEFORE INSERT ON official_disclosure_versions BEGIN SELECT RAISE(ABORT,'Synthetic failure'); END")
    result = intake.ingest_offline(market, source_id)
    assert result['status'] == 'parse_blocked' and counts(market) == (1, 0, 0)
    with market.db() as db:
        db.execute('DROP TRIGGER synthetic_abort')
    assert intake.ingest_offline(market, source_id)['status'] == 'succeeded'
    assert counts(market) == (2, 1, 1)


@pytest.mark.parametrize('name', ['source.bin', 'observation_bundle.json', 'observations.json', 'provenance.json', 'manifest.json'])
def test_any_missing_sealed_evidence_blocks_reads_and_never_self_overwrites(prepared, name):
    market, source_id, *_ = prepared
    first = intake.ingest_offline(market, source_id)
    file = (market.root / first['archive_path']).with_name(name)
    original = file.read_bytes()
    file.unlink()
    assert intake.verify_capture(market, first['id'])['integrity_status'] == 'blocked'
    assert disclosures.capture_integrity(market, first)['integrity_status'] == 'blocked'
    assert disclosures.observations(market) == []
    assert disclosures.observations(market, history=True) == []
    visible, total, state = select_observations(market, disclosures.sources(market), source_id=source_id)
    assert visible == [] and total == 0
    assert state['integrity'][source_id]['integrity_status'] == 'blocked'
    second = intake.ingest_offline(market, source_id)
    assert second['status'] == 'parse_blocked' and not file.exists()
    assert counts(market) == (2, 1, 1)
    file.write_bytes(original)  # Simulates explicit restoration of the exact evidence bytes.
    assert intake.verify_capture(market, first['id'])['integrity_status'] == 'verified'
    assert len(disclosures.observations(market)) == 1
    repeat = intake.ingest_offline(market, source_id)
    assert repeat['idempotent'] and repeat['id'] == first['id']


def test_archive_tampering_between_copy_and_commit_is_rejected(prepared, monkeypatch):
    market, source_id, *_ = prepared
    original = intake._archive
    def tamper(*args):
        result = original(*args)
        path = (market.root / result['path']).with_name('source.bin')
        data = path.read_bytes()
        path.write_bytes(data[:-1] + b'x')
        return result
    monkeypatch.setattr(intake, '_archive', tamper)
    result = intake.ingest_offline(market, source_id)
    assert result['status'] == 'parse_blocked' and counts(market) == (1, 0, 0)


def test_changed_approved_bundle_cannot_overwrite_source_version(prepared):
    market, source_id, _, bundle, _, _, save = prepared
    first = intake.ingest_offline(market, source_id)
    bundle['rows'][0]['indication_original'] = 'Separately reviewed but new version'
    save(seal=True)
    result = intake.ingest_offline(market, source_id)
    assert result['status'] == 'parse_blocked' and 'immutable version' in result['error']
    assert counts(market) == (2, 1, 1)
    assert disclosures.observations(market)[0]['capture_id'] == first['id']
    assert intake.verify_capture(market, first['id'])['integrity_status'] == 'verified'


def test_current_intake_pointer_does_not_revoke_known_historical_seal(prepared, monkeypatch):
    market, source_id, *_ = prepared
    first = disclosures.ingest(market, source_id)
    monkeypatch.setitem(observations._APPROVED, source_id, '0' * 64)
    assert intake.verify_capture(market, first['id'])['integrity_status'] == 'verified'
    assert len(disclosures.observations(market)) == 1
    # An intake pointer alone cannot approve new bytes; old version stays intact.
    blocked = disclosures.ingest(market, source_id)
    assert blocked['status'] == 'parse_blocked' and counts(market) == (2, 1, 1)


def test_ledger_or_current_pointer_cannot_self_authorize_an_unknown_seal(prepared, monkeypatch):
    market, source_id, *_ = prepared
    first = disclosures.ingest(market, source_id)
    # Keep succeeded capture, matching ledger and current pointer, but remove
    # the explicit source-code review authority. Nothing in SQLite may replace it.
    monkeypatch.setitem(observations._REVIEWED_SEALS, source_id, frozenset())
    assert intake.verify_capture(market, first['id'])['integrity_status'] == 'blocked'
    assert disclosures.observations(market) == []
    assert disclosures.ingest(market, source_id)['status'] == 'parse_blocked'


@pytest.mark.parametrize('sql', [
    "UPDATE official_disclosure_versions SET raw_bytes=1",
    'DELETE FROM official_disclosure_versions',
    "UPDATE disclosure_captures SET metadata='{}' WHERE status='succeeded'",
    'DELETE FROM disclosure_observations',
    "UPDATE disclosure_observations SET phase='Phase III'",
    'DELETE FROM official_disclosure_intake_events',
])
def test_published_version_and_history_are_immutable(prepared, sql):
    market, source_id, *_ = prepared
    intake.ingest_offline(market, source_id)
    with pytest.raises(sqlite3.IntegrityError):
        with market.db() as db:
            db.execute(sql)
    assert counts(market) == (1, 1, 1)


@pytest.mark.parametrize('table,changes', [
    ('official_disclosure_versions', {'created': '1900-01-01T00:00:00+00:00'}),
    ('official_disclosure_intake_events', {'status': 'falsified', 'payload': '{"human_sign_off":true}'}),
    ('disclosure_captures', {'metadata': '{}'}),
    ('disclosure_observations', {'capture_id': 'unpublished', 'source_locator': 'falsified:row'}),
])
def test_replace_cannot_silently_overwrite_published_evidence_with_default_sqlite(prepared, table, changes):
    market, source_id, *_ = prepared
    capture = intake.ingest_offline(market, source_id)
    with market.db() as db:
        assert db.execute('PRAGMA recursive_triggers').fetchone()[0] == 0
        before = dict(db.execute('SELECT * FROM ' + table).fetchone())
    changed = {**before, **changes}
    with pytest.raises(sqlite3.IntegrityError):
        with market.db() as db:
            db.execute('INSERT OR REPLACE INTO ' + table + ' (' + ','.join(changed) + ') VALUES ('
                       + ','.join('?' for _ in changed) + ')', tuple(changed.values()))
    with market.db() as db:
        assert dict(db.execute('SELECT * FROM ' + table).fetchone()) == before
    assert intake.verify_capture(market, capture['id'])['integrity_status'] == 'verified'


@pytest.mark.parametrize('conflicting_key', ['source_id', 'version_id', 'capture_id'])
def test_version_replace_rejects_each_unique_conflict_key(prepared, conflicting_key):
    market, source_id, *_ = prepared
    intake.ingest_offline(market, source_id)
    with market.db() as db:
        before = dict(db.execute('SELECT * FROM official_disclosure_versions').fetchone())
    changed = {**before, 'source_id': 'new-source', 'version_id': 'new-version', 'capture_id': 'new-capture'}
    changed[conflicting_key] = before[conflicting_key]
    with pytest.raises(sqlite3.IntegrityError):
        with market.db() as db:
            db.execute('INSERT OR REPLACE INTO official_disclosure_versions VALUES ('
                       + ','.join('?' for _ in changed) + ')', tuple(changed.values()))
    with market.db() as db:
        assert dict(db.execute('SELECT * FROM official_disclosure_versions').fetchone()) == before


@pytest.mark.parametrize('operation', ['row_into_published', 'row_replace_published_id', 'capture_replace_published_id'])
def test_update_cannot_move_or_replace_records_in_a_published_namespace(prepared, operation):
    market, source_id, *_ = prepared
    capture = intake.ingest_offline(market, source_id)
    with market.db() as db:
        original_capture = dict(db.execute('SELECT * FROM disclosure_captures').fetchone())
        original_row = dict(db.execute('SELECT * FROM disclosure_observations').fetchone())
        unpublished_capture = {**original_capture, 'id': 'a' * 32, 'status': 'running'}
        unpublished_row = {**original_row, 'id': 'b' * 32, 'capture_id': 'a' * 32, 'source_locator': 'unpublished:row'}
        for table, row in [('disclosure_captures', unpublished_capture), ('disclosure_observations', unpublished_row)]:
            db.execute('INSERT INTO ' + table + ' VALUES (' + ','.join('?' for _ in row) + ')', tuple(row.values()))
    with pytest.raises(sqlite3.IntegrityError):
        with market.db() as db:
            if operation == 'row_into_published':
                db.execute('UPDATE disclosure_observations SET capture_id=? WHERE id=?', (capture['id'], unpublished_row['id']))
            elif operation == 'row_replace_published_id':
                db.execute('UPDATE OR REPLACE disclosure_observations SET id=? WHERE id=?', (original_row['id'], unpublished_row['id']))
            else:
                db.execute('UPDATE OR REPLACE disclosure_captures SET id=? WHERE id=?', (capture['id'], unpublished_capture['id']))
    assert intake.verify_capture(market, capture['id'])['integrity_status'] == 'verified'
    assert len(disclosures.observations(market)) == 1


def test_read_only_verifier_detects_database_row_tamper_even_if_status_succeeded(prepared):
    market, source_id, *_ = prepared
    result = intake.ingest_offline(market, source_id)
    with market.db() as db:
        db.execute('DROP TRIGGER official_rows_no_update')
        db.execute('DROP TRIGGER official_rows_no_rebind')
        db.execute("UPDATE disclosure_observations SET phase='FALSIFIED PHASE'")
    assert intake.verify_capture(market, result['id'])['integrity_status'] == 'blocked'
    with market.db() as db:
        assert db.execute('SELECT status FROM disclosure_captures WHERE id=?', (result['id'],)).fetchone()[0] == 'succeeded'


def test_read_only_verifier_does_not_initialize_missing_schema_or_download(prepared, monkeypatch):
    market, _, *_ = prepared
    monkeypatch.setattr(disclosures, 'fetch', lambda *_: pytest.fail('Network must never be used'))
    with market.db() as db:
        before = list(db.execute('SELECT name FROM sqlite_master ORDER BY name'))
    assert intake.verify_capture(market, 'a' * 32)['integrity_status'] == 'blocked'
    with market.db() as db:
        assert list(db.execute('SELECT name FROM sqlite_master ORDER BY name')) == before


def test_dynamic_source_configuration_cannot_rebind_issuer_or_file(prepared, monkeypatch):
    market, source_id, *_ = prepared
    config = copy.deepcopy(disclosures.SOURCES[source_id])
    config['companies'] = ['US:PFE']
    monkeypatch.setitem(disclosures.SOURCES, source_id, config)
    with pytest.raises(ValueError, match='configuration/binding changed'):
        intake.ingest_offline(market, source_id)


def test_real_bundles_with_restored_local_evidence_publish_28_historical_rows(tmp_path, monkeypatch):
    """Optional real-source replay; missing ignored evidence is an explicit skip."""
    repo = Path(__file__).resolve().parents[1]
    source_bundles = [(key, json.loads((repo / cfg['bundle_relative']).read_text()))
                      for key, cfg in intake._CONTRACTS.items()]
    snapshot_path = repo / observations._CAPTURE_ROOT / 'current-listings.json'
    needed = [snapshot_path] + [repo / bundle['source']['raw_reference'] for _, bundle in source_bundles]
    if any(not path.is_file() for path in needed):
        pytest.skip('Ignored original official captures/listing snapshot must be restored; no download fallback')
    snapshot_raw = snapshot_path.read_bytes()
    assert hashlib.sha256(snapshot_raw).hexdigest() == 'acce515b9e3aeb914ecfdad96a62ad6954ed8eca1bf7195128ee2fd33f4dd35c'
    listing_snapshot = json.loads(snapshot_raw)
    workspace = tmp_path / 'real-source-replay-workspace'
    market = Market(workspace, tmp_path / 'real-source-replay-state')
    monkeypatch.setattr(disclosures, 'fetch', lambda *_: pytest.fail('Offline real-source replay must not fetch'))
    results = []
    for source_id, bundle in source_bundles:
        cfg = intake._CONTRACTS[source_id]
        assert source_id in disclosures.SOURCES
        assert disclosures.SOURCES[source_id]['intake_mode'] == intake.MODE
        bundle_target = workspace / cfg['bundle_relative']
        bundle_target.parent.mkdir(parents=True, exist_ok=True)
        bundle_target.write_bytes((repo / cfg['bundle_relative']).read_bytes())
        original, target = repo / bundle['source']['raw_reference'], workspace / bundle['source']['raw_reference']
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original, target)
        listing = next(row for row in listing_snapshot if row['market'] == 'HK' and row['ticker'] == cfg['ticker'])
        with market.db() as db:
            db.execute('INSERT INTO listings VALUES (?,?,?,?,?,?,?,?)', tuple(listing[k] for k in
                       ('id', 'market', 'exchange', 'ticker', 'name', 'industry', 'source', 'observed')))
        market.add_company('HK', cfg['ticker'], cfg['listing_name'], cfg['aliases'],
                           'Isolated integration replay of previously independently reviewed source-name binding')
        result = disclosures.ingest(market, source_id, fetch=lambda *_: pytest.fail('Offline public entry must not fetch'))
        assert result['status'] == 'succeeded', result['error']
        assert intake.verify_capture(market, result['id'])['integrity_status'] == 'verified'
        repeat = disclosures.ingest(market, source_id, fetch=lambda *_: pytest.fail('Offline public entry must not fetch'))
        assert repeat['id'] == result['id'] and repeat['idempotent']
        results.append(result)
    assert sorted(row['count'] for row in results) == [10, 18]
    assert counts(market) == (2, 28, 2)
    assert len(disclosures.observations(market)) == 28
    visible, total, _ = select_observations(market, disclosures.sources(market))
    assert total == len(visible) == 28
    dates = {row['source_id']: (row['as_of'], json.loads(row['metadata'])['as_of_precision']) for row in results}
    assert dates['ascentage_annual_pipeline_2025'] == ('2025-12-31', 'day')
    assert dates['carsgen_annual_pipeline_2024'] == (None, 'unresolved_report_date')
    # A disposable local HTTP listener verifies the same customer-facing read
    # route; it neither touches the live service nor starts any collector.
    app = Workbench(workspace, market.state)
    app.market = market
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler(app))
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()
    try:
        with urlopen('http://127.0.0.1:' + str(server.server_port) + '/disclosures', timeout=10) as response:
            assert response.status == 200
            body = response.read().decode()
        assert '药物—适应症—阶段观察 · 28 条' in body
        assert 'Pre-clinical' in body and 'Phase II/III' in body
        assert '2025/12/31' in body and '历史披露' in body
    finally:
        server.shutdown()
        server.server_close()
        serving.join(timeout=5)
