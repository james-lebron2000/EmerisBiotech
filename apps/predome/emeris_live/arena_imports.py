"""Offline, operator-pinned import of an already executed Arena cohort.

The host ledger stores one wrapper. Original event hashes and times stay in their
own ledger epoch. This module grants no execution, result or reviewer authority.
"""
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import stat
import subprocess
import tarfile
import tempfile
import zlib

from .arena_store import (canonical, sha, instant, https, validate_packet,
                          FROZEN_RUN_FIELDS, TRACKS, CONDITIONS, TERMINAL)

IMPORT_KIND = 'sealed_cohort_imported'
VERSION = 'predome-sealed-cohort-import-1'
BUNDLE_VERSION = 'predome-sealed-cohort-bundle-1'
ASSET_VERSION = 'predome-arena-source-assets-1'
DIRECTORY = 'arena_imports'
PINS = {'tsa.crt': '8bfb0305bb64e2571ca507552ef3245cb1c2fee8728e0ff8689225081ea13467',
        'cacert.pem': '2151b61137ffa86bf664691ba67e7da0b19f98c758e3d228d5d8ebf27e044438'}
PROOF_FILES = frozenset(('manifest.json', 'manifest.tsq', 'manifest.tsr', 'tsa.crt',
                         'cacert.pem', 'verification.txt', 'receipt.json'))
ALLOWED_KINDS = frozenset(('cohort_registered', 'target_registered', 'slot_registered',
                           'run_started', 'run_completed', 'timestamp_attested', 'citation_review'))
MAX_ARCHIVE = 32 * 1024 * 1024
MAX_EXPANDED = 64 * 1024 * 1024
MAX_MEMBER = 20 * 1024 * 1024
MAX_EVENTS = 512
HASH = re.compile(r'[a-f0-9]{64}')
IDENT = re.compile(r'[A-Za-z0-9_-]{1,100}')
CONTENT_TYPES = frozenset(('application/json', 'application/pdf', 'text/html', 'text/plain', 'application/xml', 'text/xml'))
PAYLOAD_FIELDS = {'schema_version', 'bundle_sha256', 'source_events', 'source_ledger_sha256',
                  'source_cohort_id', 'proofs', 'assets', 'clinical_prediction_validated'}
PROOF_FIELDS = {'purpose', 'ledger_sha256', 'covered_event_count', 'generated_at_utc',
                'manifest_sha256', 'timestamp_response_sha256', 'query_sha256',
                'authority_cert_sha256', 'ca_cert_sha256', 'files'}


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def decode(raw):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError('Duplicate JSON key')
            value[key] = item
        return value
    try:
        return json.loads(raw, object_pairs_hook=unique,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite JSON')))
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        raise ValueError('Invalid import JSON') from None


def _hash(value):
    return isinstance(value, str) and HASH.fullmatch(value) is not None


def _regular(path, maximum=MAX_ARCHIVE):
    path = Path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not 0 < info.st_size <= maximum:
            raise ValueError('A bounded single regular import file is required')
        raw = stream.read(maximum + 1)
    if len(raw) != info.st_size or len(raw) > maximum:
        raise ValueError('Import file changed or exceeded its bound')
    return raw


def _safe_name(value):
    return (isinstance(value, str) and '\\' not in value and '\x00' not in value
        and str(PurePosixPath(value)) == value and not value.startswith('/')
        and all(p not in ('', '.', '..') for p in value.split('/')))


def _members(raw):
    try:
        inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
        unpacked = inflater.decompress(raw, MAX_EXPANDED + 1)
        if (len(unpacked) > MAX_EXPANDED or inflater.unconsumed_tail or not inflater.eof
                or inflater.unused_data):
            raise ValueError('Import compression exceeds bounds or contains trailing content')
        files = {}
        with tarfile.open(fileobj=io.BytesIO(unpacked), mode='r:') as archive:
            end = 0
            for entry in archive:
                if (len(files) >= 256 or not _safe_name(entry.name) or entry.name in files
                        or not entry.isfile() or entry.type not in (tarfile.REGTYPE, tarfile.AREGTYPE)
                        or entry.pax_headers or entry.sparse or not 0 < entry.size <= MAX_MEMBER
                        or unpacked[entry.offset + 156:entry.offset + 157] != entry.type):
                    raise ValueError('Invalid or duplicate import member')
                value = archive.extractfile(entry).read(MAX_MEMBER + 1)
                if len(value) != entry.size:
                    raise ValueError('Truncated import member')
                files[entry.name] = value
                end = entry.offset_data + ((entry.size + 511) // 512) * 512
            if not files or any(unpacked[end:]):
                raise ValueError('Import archive contains hidden trailing content')
        return files
    except (tarfile.TarError, zlib.error, EOFError, OSError):
        raise ValueError('Invalid import archive') from None


def _check_event_chain(events):
    if not isinstance(events, list) or not 1 <= len(events) <= MAX_EVENTS:
        raise ValueError('A bounded complete source event chain is required')
    previous, previous_time, ids, hashes = '0' * 64, None, set(), set()
    for seq, event in enumerate(events, 1):
        if (not isinstance(event, dict) or set(event) != {'seq', 'id', 'kind', 'entity_id', 'created_at', 'payload', 'previous_hash', 'event_hash'}
                or type(event['seq']) is not int or event['seq'] != seq
                or not isinstance(event['id'], str) or not re.fullmatch(r'[a-f0-9]{32}', event['id'])
                or event['id'] in ids or event['event_hash'] in hashes
                or event['kind'] not in ALLOWED_KINDS or not isinstance(event['payload'], dict)
                or not isinstance(event['entity_id'], str) or not IDENT.fullmatch(event['entity_id'])
                or event['previous_hash'] != previous or not _hash(event['event_hash'])
                or sha({k: v for k, v in event.items() if k not in ('seq', 'event_hash')}) != event['event_hash']):
            raise ValueError('Invalid, unsupported or incomplete source event chain')
        at = instant(event['created_at'])
        if at > datetime.now(timezone.utc) or previous_time is not None and at < previous_time:
            raise ValueError('Source event time is future or out of order')
        previous, previous_time = event['event_hash'], at
        ids.add(event['id']); hashes.add(event['event_hash'])


def _target(target, event):
    required = ('id', 'project_id', 'nct_id', 'endpoint', 'population', 'comparison',
                'success_definition', 'analysis_timepoint', 'information_cutoff_utc', 'submission_deadline_utc')
    if (any(not isinstance(target.get(k), str) or not target[k].strip() for k in required)
            or target['id'] != event['entity_id'] or not re.fullmatch(r'NCT\d{8}', target['nct_id'])
            or target.get('result') is not None):
        raise ValueError('Exact outcome-free original target is required')
    at = instant(event['created_at'])
    cutoff, deadline = instant(target['information_cutoff_utc']), instant(target['submission_deadline_utc'])
    if not cutoff <= at < deadline:
        raise ValueError('Original registration must follow cutoff and precede original deadline')
    screen = target.get('readout_screen', {})
    checked = instant(screen.get('checked_at_utc'))
    if (screen.get('status') != 'no_decisive_public_result_found' or not isinstance(screen.get('sources'), list)
            or not screen['sources'] or not cutoff <= checked <= at or (at - checked).total_seconds() > 86400
            or any(not isinstance(s, dict) or not https(s.get('url')) for s in screen['sources'])):
        raise ValueError('Contemporaneous original readout screening is required')
    if target.get('human_review_count', 0) != 0 or target.get('clinical_prediction_validated', False) is not False:
        raise ValueError('Import cannot introduce human review or clinical validation')


def _completion(slot, started, event, target):
    result = event['payload']; status = result.get('status'); p = result.get('probability')
    if FROZEN_RUN_FIELDS.intersection(result) or 'input_sha256' in result:
        raise ValueError('Completion cannot overwrite original frozen identities or fields')
    if status not in TERMINAL or (status == 'predicted' and
            (type(p) not in (int, float) or not math.isfinite(p) or not 0 <= p <= 1)) or (status != 'predicted' and p is not None):
        raise ValueError('Terminal status or original probability is invalid')
    if result.get('clinical_prediction_validated', False) is not False:
        raise ValueError('Imported model output is not clinical validation')
    complete = status in ('predicted', 'abstained')
    if (result.get('model_id') != slot['model_id'] or result.get('requested_model') != slot['requested_model']
            or result.get('workflow_version') != slot['workflow_version']
            or result.get('provider') != slot.get('provider')
            or result.get('config_sha256') != slot.get('provider_config_snapshot', {}).get('config_sha256')
            or result.get('contract_complete') is not complete):
        raise ValueError('Completion differs from original frozen model contract')
    expected = slot.get('expected_provider_input_sha256')
    if not _hash(expected) or (complete or result.get('provider_input_sha256') is not None) and result.get('provider_input_sha256') != expected:
        raise ValueError('Completion input differs from the registered provider input')
    if complete and result.get('prompt_sha256') != slot.get('expected_prompt_sha256'):
        raise ValueError('Completion prompt differs from original contract')
    if not instant(started['created_at']) <= instant(event['created_at']) <= instant(target['submission_deadline_utc']):
        raise ValueError('Original completion exceeds the original submission deadline')
    if result.get('submission_on_time') is not True:
        raise ValueError('Original on-time state contradicts the sealed completion')
    if result.get('output_sha256') != sha({k: v for k, v in result.items() if k != 'output_sha256'}):
        raise ValueError('Original output hash mismatch')
    evidence = {e['id'] for e in slot['packet']['evidence']}
    cited = result.get('evidence_ids', [])
    if (not isinstance(cited, list) or any(i not in evidence for i in cited)
            or status == 'predicted' and not cited
            or any(not isinstance(result.get(k, []), list) or any(i not in cited for i in result.get(k, []))
                   for k in ('supporting_evidence_ids', 'opposing_evidence_ids'))):
        raise ValueError('Completion cites an unregistered evidence source')
    attempts = result.get('attempts', [])
    if not isinstance(attempts, list) or len(attempts) > 2:
        raise ValueError('Original bounded request attempts are required')
    if attempts:
        if (result.get('prompt_sha256') != sha([a.get('prompt_sha256') for a in attempts])
                or result.get('request_sha256') != sha([a.get('request_sha256') for a in attempts])
                or any(a.get('requested_model') != slot['requested_model'] for a in attempts)
                or any(not _hash(a.get(k)) for a in attempts for k in ('prompt_sha256', 'request_sha256'))
                or attempts[0].get('input_sha256') != expected):
            raise ValueError('Original attempt hashes or model identities differ')
        responses = [a.get('response_sha256') for a in attempts]
        if all(responses) and result.get('provider_response_sha256') != sha(responses):
            raise ValueError('Original response hash differs from actual attempts')
    if result.get('evidence_review') is not None and result.get('evidence_review_sha256') != sha(result['evidence_review']):
        raise ValueError('Original evidence review hash differs')


def _semantics(events, cohort_id):
    cohorts, targets, slots, starts, finishes, reviews, stamps = {}, {}, {}, {}, {}, {}, []
    for event in events:
        kind, ident, p = event['kind'], event['entity_id'], event['payload']
        if kind == 'cohort_registered':
            if cohorts or ident != cohort_id or p.get('clinical_prediction_validated') is not False:
                raise ValueError('Exactly one unvalidated source cohort is required')
            cohorts[ident] = event
        elif kind == 'target_registered':
            if ident in targets or p.get('cohort_id') not in cohorts:
                raise ValueError('Target is duplicate or precedes its cohort')
            _target(p, event); targets[ident] = event
        elif kind == 'slot_registered':
            if (ident in slots or p.get('cohort_id') != cohort_id or p.get('target_id') not in targets
                    or p.get('track') not in TRACKS or p.get('condition') not in CONDITIONS
                    or p.get('scope') != 'prospective_challenge'
                    or not isinstance(p.get('model_id'), str) or not p['model_id']
                    or not isinstance(p.get('requested_model'), str) or not p['requested_model']
                    or not isinstance(p.get('workflow_version'), str) or not p['workflow_version']
                    or not _hash(p.get('provider_config_snapshot', {}).get('config_sha256'))):
                raise ValueError('Unbound, duplicated or invalid source slot')
            validate_packet(p.get('packet', {}), targets[p['target_id']]['payload'], p['condition'])
            if p.get('input_sha256') != sha(p['packet']) or not all(_hash(p.get(k)) for k in
                    ('expected_provider_input_sha256', 'expected_prompt_sha256', 'execution_budget_sha256')):
                raise ValueError('Registered input and execution contract hashes are required')
            protocol = cohorts[cohort_id]['payload']['protocol']
            if p['execution_budget_sha256'] != sha(protocol.get('execution_budget')):
                raise ValueError('Frozen execution budget differs from original protocol')
            slots[ident] = event
        elif kind == 'run_started':
            if ident not in slots or ident in starts or ident in finishes:
                raise ValueError('Run must start exactly once after its assigned slot')
            slot = slots[ident]['payload']; target = targets[slot['target_id']]['payload']
            if (set(p) != {'input_sha256', 'execution_budget_sha256'} or p['input_sha256'] != slot['input_sha256']
                    or p['execution_budget_sha256'] != slot['execution_budget_sha256']
                    or instant(event['created_at']) > instant(target['submission_deadline_utc'])):
                raise ValueError('Original run start violates its frozen contract')
            starts[ident] = event
        elif kind == 'run_completed':
            if ident not in starts or ident in finishes:
                raise ValueError('Every imported completion needs exactly one original start')
            slot = slots[ident]['payload']
            _completion(slot, starts[ident], event, targets[slot['target_id']]['payload'])
            limit = cohorts[cohort_id]['payload']['protocol']['execution_budget'].get('max_requests')
            if type(limit) is not int or not 1 <= limit <= 2 or len(p.get('attempts', [])) > limit:
                raise ValueError('Original requests exceed the registered execution allowance')
            finishes[ident] = event
        elif kind == 'citation_review':
            from .arena_citations import validate_review
            if (ident not in finishes or p.get('reviewer_kind') != 'ai'
                    or set(p) != {'assessment', 'reviewer_id', 'reviewer_kind', 'supersedes', 'review_sha256'}
                    or not isinstance(p['reviewer_id'], str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', p['reviewer_id'])
                    or p['review_sha256'] != sha({k: v for k, v in p.items() if k != 'review_sha256'})):
                raise ValueError('Only source-bound original AI citation reviews may be imported')
            clean = validate_review({**slots[ident]['payload'], **finishes[ident]['payload']}, p['assessment'])
            if clean != p['assessment']:
                raise ValueError('AI citation assessment fields or anchors differ')
            key = (ident, p['reviewer_id']); prior = reviews.get(key)
            if p['supersedes'] != (prior['review_sha256'] if prior else None):
                raise ValueError('AI citation correction must identify the original preceding review')
            reviews[key] = p
        elif kind == 'timestamp_attested':
            stamps.append(event)
    if not cohorts or not targets or not slots or set(slots) != set(starts) or set(slots) != set(finishes):
        raise ValueError('Import requires the entire completed assigned cohort, including failures')
    cp = cohorts[cohort_id]['payload']
    if (cp.get('target_ids') != sorted(targets) or type(cp.get('slot_count')) is not int or cp['slot_count'] != len(slots)
            or type(cp.get('protocol', {}).get('maximum_slots')) is not int
            or cp['protocol']['maximum_slots'] < len(slots)):
        raise ValueError('Original cohort denominator or protocol differs from its assigned events')
    occupied = [cohort_id, *targets, *slots]
    if len(set(occupied)) != len(occupied):
        raise ValueError('Source entity identities collide')
    combinations = [(s['payload']['target_id'], s['payload']['model_id'], s['payload']['track'], s['payload']['condition']) for s in slots.values()]
    if len(set(combinations)) != len(combinations):
        raise ValueError('Duplicate assigned comparison slot')
    for target_id in targets:
        groups = {}
        for s in slots.values():
            p = s['payload']
            if p['target_id'] == target_id and p['track'] in ('same_evidence', 'shared_agent'):
                groups.setdefault((p['track'], p['condition']), set()).add((p['input_sha256'], p['workflow_version'],
                    p['expected_prompt_sha256'], p['execution_budget_sha256']))
        if any(len(values) != 1 for values in groups.values()):
            raise ValueError('Controlled models do not share the same comparison contract')
    return targets, slots, starts, finishes, stamps


def _proof_metadata(metadata, purpose, events, slots, starts, finishes):
    if not isinstance(metadata, dict) or set(metadata) != PROOF_FIELDS or metadata['purpose'] != purpose:
        raise ValueError('Strict verified proof metadata is required')
    count = metadata['covered_event_count']
    if type(count) is not int or not 1 <= count <= len(events) or events[count - 1]['event_hash'] != metadata['ledger_sha256']:
        raise ValueError('Proof does not bind this exact source ledger prefix')
    files = metadata['files']
    if (not isinstance(files, dict) or set(files) != PROOF_FILES or not all(_hash(v) for v in files.values())
            or files['manifest.json'] != metadata['manifest_sha256']
            or files['manifest.tsr'] != metadata['timestamp_response_sha256']
            or files['manifest.tsq'] != metadata['query_sha256']
            or metadata['authority_cert_sha256'] != PINS['tsa.crt'] or metadata['ca_cert_sha256'] != PINS['cacert.pem']
            or any(files[k] != v for k, v in PINS.items())):
        raise ValueError('Proof file hashes or independently pinned authority differ')
    generated = instant(metadata['generated_at_utc'])
    if not instant(events[count - 1]['created_at']) <= generated <= datetime.now(timezone.utc):
        raise ValueError('Authenticated proof time contradicts the original ledger prefix')
    if purpose == 'registration':
        registered = [e for e in events if e['kind'] in ('cohort_registered', 'target_registered', 'slot_registered')]
        if (any(e['seq'] > count for e in registered) or any(e['kind'] not in
                ('cohort_registered', 'target_registered', 'slot_registered') for e in events[:count])
                or generated >= min(instant(e['created_at']) for e in starts.values())):
            raise ValueError('Registration proof must bind all assignments before any original call')
    else:
        if any(e['seq'] > count for e in finishes.values()):
            raise ValueError('Prediction proof omits an assigned completed record')
        deadlines = [instant(e['payload']['submission_deadline_utc']) for e in events if e['kind'] == 'target_registered']
        if generated > min(deadlines):
            raise ValueError('Prediction proof was signed after the original deadline')


def _validate_import_payload(payload):
    """Pure structural/semantic check of an operator-created sealed wrapper.

    No client boolean confers cryptographic trust. Only verify_bundle constructs
    the derived proof fields after offline signature+nonce verification; the
    immutable archive retains those bytes for independent checking.
    """
    if (not isinstance(payload, dict) or set(payload) != PAYLOAD_FIELDS or payload['schema_version'] != VERSION
            or not _hash(payload['bundle_sha256']) or not IDENT.fullmatch(str(payload['source_cohort_id']))
            or payload['clinical_prediction_validated'] is not False):
        raise ValueError('Unsupported sealed cohort import payload')
    events = payload['source_events']; _check_event_chain(events)
    if events[-1]['event_hash'] != payload['source_ledger_sha256']:
        raise ValueError('Imported original ledger hash differs')
    targets, slots, starts, finishes, stamps = _semantics(events, payload['source_cohort_id'])
    proofs = payload['proofs']
    if not isinstance(proofs, dict) or set(proofs) != {'registration', 'predictions'}:
        raise ValueError('Both independent original proof purposes are required')
    for purpose, metadata in proofs.items():
        _proof_metadata(metadata, purpose, events, slots, starts, finishes)
    if len(stamps) != 2 or {e['payload'].get('purpose') for e in stamps} != set(proofs):
        raise ValueError('Exactly both original timestamp attestation events are required')
    for event in stamps:
        p = event['payload']; m = proofs[p['purpose']]
        if (any(p.get(k) != m[k] for k in ('purpose', 'ledger_sha256', 'covered_event_count',
                    'generated_at_utc', 'manifest_sha256', 'timestamp_response_sha256', 'authority_cert_sha256', 'ca_cert_sha256'))
                or p.get('files') != {k: v for k, v in m['files'].items() if k != 'receipt.json'}
                or instant(event['created_at']) < instant(m['generated_at_utc'])
                or p['covered_event_count'] >= event['seq']):
            raise ValueError('Original attestation event differs from independently verified proof')
    assets = payload['assets']
    required = {e['source_sha256'] for s in slots.values() for e in s['payload']['packet']['evidence']}
    if not isinstance(assets, list) or len(assets) != len(required):
        raise ValueError('All and only frozen public source asset versions are required')
    seen = set()
    for asset in assets:
        if (not isinstance(asset, dict) or set(asset) != {'sha256', 'bytes', 'content_type', 'source_url', 'bundle_path'}
                or not _hash(asset['sha256']) or asset['sha256'] in seen
                or type(asset['bytes']) is not int or not 0 < asset['bytes'] <= MAX_MEMBER
                or asset['content_type'] not in CONTENT_TYPES or not https(asset['source_url'])
                or asset['bundle_path'] != 'assets/' + asset['sha256'] + '.bin'):
            raise ValueError('Invalid public source asset reference')
        seen.add(asset['sha256'])
        matching = [e for s in slots.values() for e in s['payload']['packet']['evidence'] if e['source_sha256'] == asset['sha256']]
        if not any(e['source_url'] == asset['source_url'] for e in matching):
            raise ValueError('Asset source is not a frozen public evidence URL')
    if seen != required:
        raise ValueError('Frozen public source assets are missing')
    return payload


def validate_import_payload(payload):
    try:
        return _validate_import_payload(payload)
    except (TypeError, KeyError, AttributeError, OverflowError, RecursionError):
        raise ValueError('Malformed sealed cohort import payload') from None


def imported_streams(events):
    """Preserve host physical order; never flatten child hashes into its chain."""
    result = []; entities = set(); bundles = set(); source_ids = set(); source_hashes = set()
    # Check every host event, including those following the import. IDs retain
    # their original provenance and may not acquire an ambiguous second origin.
    host_ids = {event['id'] for event in events if isinstance(event.get('id'), str)}
    host_hashes = {event['event_hash'] for event in events if _hash(event.get('event_hash'))}
    for event in events:
        if event.get('kind') != IMPORT_KIND:
            if event.get('kind') in ('cohort_registered', 'target_registered', 'slot_registered'):
                if event['entity_id'] in entities:
                    raise ValueError('Native/imported entity collision')
                entities.add(event['entity_id'])
            continue
        payload = validate_import_payload(event['payload'])
        if payload['bundle_sha256'] in bundles:
            raise ValueError('Sealed cohort bundle imported more than once')
        source_entities = {e['entity_id'] for e in payload['source_events'] if e['kind'] in
            ('cohort_registered', 'target_registered', 'slot_registered')}
        incoming_ids = {e['id'] for e in payload['source_events']}
        incoming_hashes = {e['event_hash'] for e in payload['source_events']}
        if entities & source_entities:
            raise ValueError('Native/imported entity collision')
        if incoming_ids & (host_ids | source_ids) or incoming_hashes & (host_hashes | source_hashes):
            raise ValueError('Native/imported event identity or event hash collision')
        if instant(event['created_at']) < instant(payload['source_events'][-1]['created_at']):
            raise ValueError('Import time cannot precede the original source execution')
        entities.update(source_entities); bundles.add(payload['bundle_sha256'])
        source_ids.update(incoming_ids); source_hashes.update(incoming_hashes)
        result.append((event, payload))
    return result


def _run_openssl(args):
    env = {k: os.environ[k] for k in ('PATH', 'SYSTEMROOT', 'TMPDIR') if k in os.environ}
    env.update(LANG='C', LC_ALL='C')
    try:
        return subprocess.check_output(['openssl', *map(str, args)], env=env, stderr=subprocess.PIPE, text=True, timeout=20)
    except (subprocess.SubprocessError, OSError):
        raise ValueError('Offline timestamp signature verification failed') from None


def _verify_proof(files, purpose, events):
    prefix = 'proofs/' + purpose + '/'
    contents = {name: files[prefix + name] for name in PROOF_FILES}
    for name, pin in PINS.items():
        if digest(contents[name]) != pin:
            raise ValueError('Timestamp certificate differs from fixed FreeTSA pin')
    manifest = decode(contents['manifest.json']); receipt = decode(contents['receipt.json'])
    count = manifest.get('covered_event_count')
    if (manifest.get('schema_version') != 'predome-arena-attestation-1' or manifest.get('purpose') != purpose
            or type(count) is not int or not 1 <= count <= len(events)
            or manifest.get('ledger_sha256') != events[count - 1]['event_hash']
            or not isinstance(manifest.get('snapshot'), dict)
            or manifest['snapshot'].get('events') != events[:count]
            or manifest['snapshot'].get('ledger_sha256') != manifest['ledger_sha256']):
        raise ValueError('Signed manifest does not contain the exact original source prefix')
    with tempfile.TemporaryDirectory(prefix='predome-proof-verify-') as tmp:
        directory = Path(tmp)
        for name, raw in contents.items():
            (directory / name).write_bytes(raw)
        common = ['-in', directory / 'manifest.tsr', '-CAfile', directory / 'cacert.pem', '-untrusted', directory / 'tsa.crt']
        for basis in (['-data', directory / 'manifest.json'], ['-queryfile', directory / 'manifest.tsq']):
            if 'Verification: OK' not in _run_openssl(['ts', '-verify', *basis, *common]):
                raise ValueError('Timestamp data or original nonce did not verify')
        reply = _run_openssl(['ts', '-reply', '-in', directory / 'manifest.tsr', '-text'])
        query = _run_openssl(['ts', '-query', '-in', directory / 'manifest.tsq', '-text'])
    for document in (query, reply):
        if not re.search(r'^Hash Algorithm:\s*sha256\s*$', document, re.M):
            raise ValueError('Original timestamp message imprint must use SHA-256')
    query_nonce = re.search(r'^Nonce:\s*(0x[0-9A-Fa-f]+)\s*$', query, re.M)
    reply_nonce = re.search(r'^Nonce:\s*(0x[0-9A-Fa-f]+)\s*$', reply, re.M)
    if not query_nonce or not reply_nonce or int(query_nonce.group(1), 16) != int(reply_nonce.group(1), 16):
        raise ValueError('Original timestamp query and signed response require the same explicit nonce')
    match = re.search(r'^Time stamp: (.+)$', reply, re.M)
    if not match:
        raise ValueError('Authenticated timestamp genTime is missing')
    generated = datetime.strptime(match.group(1).strip(), '%b %d %H:%M:%S %Y GMT').replace(tzinfo=timezone.utc).isoformat()
    hashes = {k: digest(v) for k, v in contents.items()}
    result = {'purpose': purpose, 'ledger_sha256': manifest['ledger_sha256'], 'covered_event_count': count,
        'generated_at_utc': generated, 'manifest_sha256': hashes['manifest.json'],
        'timestamp_response_sha256': hashes['manifest.tsr'], 'query_sha256': hashes['manifest.tsq'],
        'authority_cert_sha256': PINS['tsa.crt'], 'ca_cert_sha256': PINS['cacert.pem'], 'files': hashes}
    if (not isinstance(receipt, dict) or any(receipt.get(k) != result[k] for k in
            ('purpose', 'ledger_sha256', 'covered_event_count', 'generated_at_utc', 'manifest_sha256',
             'timestamp_response_sha256', 'authority_cert_sha256', 'ca_cert_sha256'))
            or receipt.get('files') != {k: v for k, v in hashes.items() if k != 'receipt.json'}
            or not instant(generated) <= instant(receipt.get('verified_at_utc')) <= datetime.now(timezone.utc)):
        raise ValueError('Original unsigned receipt differs from independently verified timestamp claims')
    return result


def _bundle(raw):
    files = _members(raw)
    if 'manifest.json' not in files or 'source-events.json' not in files:
        raise ValueError('Missing import manifest or original events')
    manifest = decode(files['manifest.json'])
    if (not isinstance(manifest, dict) or set(manifest) != {'schema_version', 'source_cohort_id', 'source_ledger_sha256', 'files', 'assets'}
            or manifest['schema_version'] != BUNDLE_VERSION or not isinstance(manifest['files'], dict)
            or set(manifest['files']) != set(files) - {'manifest.json'}):
        raise ValueError('Unsupported import manifest or member set')
    for name, entry in manifest['files'].items():
        if not isinstance(entry, dict) or entry != {'sha256': digest(files[name]), 'bytes': len(files[name])}:
            raise ValueError('Import member hash or size mismatch')
    events = decode(files['source-events.json']); _check_event_chain(events)
    names = {'manifest.json', 'source-events.json'}
    names.update('proofs/' + purpose + '/' + name for purpose in ('registration', 'predictions') for name in PROOF_FILES)
    if not isinstance(manifest['assets'], list):
        raise ValueError('Source asset manifest is required')
    for asset in manifest['assets']:
        if not isinstance(asset, dict) or not _safe_name(asset.get('bundle_path')):
            raise ValueError('Invalid source asset member')
        names.add(asset['bundle_path'])
        raw_asset = files.get(asset['bundle_path'])
        if raw_asset is None or digest(raw_asset) != asset.get('sha256') or len(raw_asset) != asset.get('bytes'):
            raise ValueError('Original frozen source asset hash or size differs')
    if names != set(files):
        raise ValueError('Unexpected or missing import archive member')
    return manifest, events, files


def _verify_bundle(path, expected_sha256):
    if not _hash(expected_sha256):
        raise ValueError('An independently pinned whole-bundle SHA-256 is required')
    raw = _regular(path)
    if digest(raw) != expected_sha256:
        raise ValueError('Selected import archive differs from operator SHA-256')
    manifest, events, files = _bundle(raw)
    proofs = {purpose: _verify_proof(files, purpose, events) for purpose in ('registration', 'predictions')}
    payload = {'schema_version': VERSION, 'bundle_sha256': expected_sha256, 'source_events': events,
        'source_ledger_sha256': manifest['source_ledger_sha256'], 'source_cohort_id': manifest['source_cohort_id'],
        'proofs': proofs, 'assets': manifest['assets'], 'clinical_prediction_validated': False}
    return validate_import_payload(payload)


def verify_bundle(path, expected_sha256):
    try:
        return _verify_bundle(path, expected_sha256)
    except (TypeError, KeyError, AttributeError, OverflowError, RecursionError):
        raise ValueError('Malformed sealed cohort import bundle') from None


@contextmanager
def _private_directory(data_dir):
    root_fd = os.open(data_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    directory_fd = None
    try:
        owner = os.fstat(root_fd)
        try:
            os.mkdir(DIRECTORY, mode=0o700, dir_fd=root_fd)
            os.chown(DIRECTORY, owner.st_uid, owner.st_gid, dir_fd=root_fd, follow_symlinks=False)
        except FileExistsError:
            pass
        directory_fd = os.open(DIRECTORY, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
        info = os.fstat(directory_fd)
        if (stat.S_IMODE(info.st_mode) != 0o700 or
                (info.st_uid, info.st_gid) != (owner.st_uid, owner.st_gid)):
            raise ValueError('Import archive directory has invalid privacy or ownership')
        yield directory_fd, owner
    finally:
        if directory_fd is not None:
            os.close(directory_fd)
        os.close(root_fd)


def stage_bundle(data_dir, path, payload):
    """Stage exact operator-verified bytes, never overwrite an archive or DB."""
    validate_import_payload(payload)
    raw = _regular(path)
    if digest(raw) != payload['bundle_sha256']:
        raise ValueError('Bundle changed between verification and staging')
    _matching_files(raw, payload)
    name = payload['bundle_sha256'] + '.tar.gz'
    with _private_directory(data_dir) as (directory_fd, owner):
        try:
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory_fd)
        except FileExistsError:
            fd = None
        if fd is not None:
            with os.fdopen(fd, 'wb') as stream:
                os.fchown(stream.fileno(), owner.st_uid, owner.st_gid)
                stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
        except OSError:
            raise ValueError('Private content-addressed import archive differs') from None
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1
                    or (info.st_uid, info.st_gid) != (owner.st_uid, owner.st_gid)
                    or info.st_size != len(raw) or stream.read(MAX_ARCHIVE + 1) != raw):
                raise ValueError('Private content-addressed import archive differs')
        os.fsync(directory_fd)
    return {'relative_path': DIRECTORY + '/' + name, 'sha256': payload['bundle_sha256'], 'bytes': len(raw)}


def _stored_files(data_dir, payload):
    directory = Path(data_dir) / DIRECTORY
    root_info = Path(data_dir).lstat(); info = directory.lstat()
    if (not stat.S_ISDIR(root_info.st_mode) or not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o700
            or (info.st_uid, info.st_gid) != (root_info.st_uid, root_info.st_gid)):
        raise ValueError('Private import archive directory is invalid')
    path = directory / (payload['bundle_sha256'] + '.tar.gz'); info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600
            or (info.st_uid, info.st_gid) != (root_info.st_uid, root_info.st_gid)):
        raise ValueError('Private import archive is invalid')
    raw = _regular(path)
    if digest(raw) != payload['bundle_sha256']:
        raise ValueError('Archived import bundle hash differs')
    return _matching_files(raw, payload)


def _matching_files(raw, payload):
    manifest, events, files = _bundle(raw)
    if (events != payload['source_events'] or manifest['assets'] != payload['assets']
            or manifest['source_cohort_id'] != payload['source_cohort_id']
            or manifest['source_ledger_sha256'] != payload['source_ledger_sha256']):
        raise ValueError('Archived original events differ from the imported wrapper')
    for purpose, proof in payload['proofs'].items():
        if any(digest(files['proofs/' + purpose + '/' + name]) != value for name, value in proof['files'].items()):
            raise ValueError('Archived original proof bytes differ')
    return files



def verify_stored_bundle(data_dir, payload):
    """Check archived bytes against a previously verified host wrapper, offline.

    No new cryptographic assurance or execution is created. Missing/corrupt
    storage fails instead of projecting a dangling original proof reference.
    """
    validate_import_payload(payload)
    try:
        _stored_files(data_dir, payload)
    except OSError:
        raise ValueError('Archived import bundle is missing or unreadable') from None
    return {'bundle_sha256': payload['bundle_sha256'],
        'source_event_count': len(payload['source_events']), 'asset_count': len(payload['assets'])}


def read_asset(data_dir, events, source_sha256):
    if not _hash(source_sha256):
        return None
    for host, payload in imported_streams(events):
        asset = next((a for a in payload['assets'] if a['sha256'] == source_sha256), None)
        if asset is None:
            continue
        files = _stored_files(data_dir, payload)
        return {'raw': files[asset['bundle_path']], 'content_type': asset['content_type'],
            'source_url': asset['source_url'], 'sha256': source_sha256, 'bytes': asset['bytes'],
            'bundle_sha256': payload['bundle_sha256'], 'imported_at_utc': host['created_at']}
    return None


def proof_files(data_dir, events, bundle_sha256, purpose):
    if not _hash(bundle_sha256) or purpose not in ('registration', 'predictions'):
        return None
    for _, payload in imported_streams(events):
        if payload['bundle_sha256'] == bundle_sha256:
            files = _stored_files(data_dir, payload)
            return {name: files['proofs/' + purpose + '/' + name] for name in PROOF_FILES}
    return None


def _source_events(source_data_dir):
    """Read a stable DB+WAL copy, so SQLite cannot create source SHM files.

    Query-only mode alone may still create a WAL shared-memory file. Read the
    existing bytes twice and reject concurrent changes, then let SQLite recover
    only the temporary copy. The original DB/WAL are never opened for writing.
    """
    from .arena_store import ArenaLedger
    source = Path(source_data_dir) / 'arena.sqlite3'
    if source.is_symlink() or not source.is_file():
        raise ValueError('An existing source ledger is required; prepare never initializes a DB')

    def read_copy():
        result = {'arena.sqlite3': _regular(source)}
        wal = source.with_name(source.name + '-wal')
        if wal.exists() or wal.is_symlink():
            fd = os.open(wal, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, 'rb') as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not 0 <= info.st_size <= MAX_ARCHIVE:
                    raise ValueError('Bounded original WAL is required')
                raw = stream.read(MAX_ARCHIVE + 1)
            if len(raw) != info.st_size:
                raise ValueError('Original source WAL changed')
            result['arena.sqlite3-wal'] = raw
        return result

    originals = read_copy()
    if read_copy() != originals:
        raise ValueError('Source DB or WAL changed; retry after its current writer has finished')
    with tempfile.TemporaryDirectory(prefix='predome-source-read-') as tmp:
        directory = Path(tmp)
        for name, raw in originals.items():
            (directory / name).write_bytes(raw)
        with closing(sqlite3.connect((directory / 'arena.sqlite3').as_uri() + '?mode=ro', uri=True)) as db:
            db.row_factory = sqlite3.Row; db.execute('PRAGMA query_only=ON'); db.execute('BEGIN')
            events = ArenaLedger._verified_events(None, db)
    if read_copy() != originals:
        raise ValueError('Source DB or WAL changed while preparing; no output published')
    return events


def prepare_bundle(source_data_dir, proof_directories, source_manifest, source_root, output):
    """Read an existing complete source ledger and originals; no DB init/network."""
    events = _source_events(source_data_dir)
    _check_event_chain(events)
    cohorts = [e['entity_id'] for e in events if e['kind'] == 'cohort_registered']
    if len(cohorts) != 1:
        raise ValueError('Select a complete isolated single-cohort source ledger')
    assets_spec = decode(_regular(source_manifest, 1024 * 1024))
    if (not isinstance(assets_spec, dict) or set(assets_spec) != {'schema_version', 'assets'}
            or assets_spec['schema_version'] != ASSET_VERSION or not isinstance(assets_spec['assets'], list)):
        raise ValueError('Explicit public source asset manifest is required')
    files = {'source-events.json': (canonical(events) + '\n').encode()}
    if set(proof_directories) != {'registration', 'predictions'}:
        raise ValueError('Both original proof directories are required')
    for purpose, location in proof_directories.items():
        directory = Path(location)
        if directory.is_symlink() or not directory.is_dir() or {p.name for p in directory.iterdir()} != PROOF_FILES:
            raise ValueError('Original proof directory must contain exactly seven files')
        for name in PROOF_FILES:
            files['proofs/' + purpose + '/' + name] = _regular(directory / name, 2 * 1024 * 1024)
    assets = []; root = Path(source_root).resolve(strict=True)
    for item in assets_spec['assets']:
        if not isinstance(item, dict) or set(item) != {'path', 'sha256', 'content_type', 'source_url'} or not _safe_name(item['path']):
            raise ValueError('A source path, hash, content type and original URL are required')
        path = root / item['path']
        if path.resolve(strict=True) != path or not path.is_relative_to(root):
            raise ValueError('Source root escape or symlink is forbidden')
        raw = _regular(path, MAX_MEMBER)
        if digest(raw) != item['sha256']:
            raise ValueError('Public source original differs from manifest')
        asset = {k: item[k] for k in ('sha256', 'content_type', 'source_url')}
        asset.update(bytes=len(raw), bundle_path='assets/' + item['sha256'] + '.bin')
        if asset['bundle_path'] in files:
            raise ValueError('Duplicate public source original')
        files[asset['bundle_path']] = raw; assets.append(asset)
    manifest = {'schema_version': BUNDLE_VERSION, 'source_cohort_id': cohorts[0],
        'source_ledger_sha256': events[-1]['event_hash'], 'assets': assets,
        'files': {name: {'sha256': digest(raw), 'bytes': len(raw)} for name, raw in sorted(files.items())}}
    files['manifest.json'] = (canonical(manifest) + '\n').encode()
    packed = io.BytesIO()
    with gzip.GzipFile(fileobj=packed, mode='wb', mtime=0) as zipped:
        with tarfile.open(fileobj=zipped, mode='w', format=tarfile.USTAR_FORMAT) as archive:
            for name, raw in sorted(files.items()):
                entry = tarfile.TarInfo(name); entry.size = len(raw); entry.mode = 0o600
                archive.addfile(entry, io.BytesIO(raw))
    raw = packed.getvalue()
    if len(raw) > MAX_ARCHIVE:
        raise ValueError('Prepared import bundle exceeds size bound')
    # Verify the final exact bytes before publishing an output. A rejected proof
    # leaves no apparently accepted bundle at the requested destination.
    with tempfile.TemporaryDirectory(prefix='predome-import-prepare-') as tmp:
        path = Path(tmp) / 'candidate.tar.gz'; path.write_bytes(raw)
        payload = verify_bundle(path, digest(raw))
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    return payload
