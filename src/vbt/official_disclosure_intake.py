"""Offline publication of two frozen, visually reviewed historical PDF charts.

No network, model, arbitrary issuer binding or source discovery is available here.
Files are sealed before the existing disclosure tables publish all rows together.
The immutable version ledger is separate from repeatable intake audit events.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import time
import uuid

from . import disclosures, official_pipeline_observations as observations
from .io import now, within


PARSER = 'frozen_official_pdf_observations_v1'
MODE = 'offline_reviewed_bundle'
_CONTRACTS = {
    'ascentage_annual_pipeline_2025': {
        'company': 'HK:06855', 'ticker': '06855', 'listing_name': 'ASCENTAGE',
        'issuer_name': 'Ascentage Pharma Group International', 'issuer_group': 'ascentage',
        'aliases': ['Ascentage Pharma Group Inc.'], 'relationship': 'subsidiary',
        'url': 'https://ir.ascentage.com/static-files/cc6eb0ea-f444-4765-9efb-bb117b570f7b',
        'bundle_relative': 'examples/ascentage_pipeline_2025_observations.json',
    },
    'carsgen_annual_pipeline_2024': {
        'company': 'HK:02171', 'ticker': '02171', 'listing_name': 'CARSGEN-B',
        'issuer_name': 'CARsgen Therapeutics Holdings Limited', 'issuer_group': 'carsgen',
        'aliases': ['CARsgen Therapeutics Co., Ltd.'], 'relationship': 'consolidated_affiliated_entity',
        'url': 'https://www.carsgen.com/media/3vsj00ph/2024-annual-report.pdf',
        'bundle_relative': 'examples/carsgen_pipeline_2024_observations.json',
    },
}


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _read_bounded(path, limit):
    with Path(path).open('rb') as handle:
        raw = handle.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('Offline evidence grew beyond its bounded read limit')
    return raw


def _source(source_id):
    if source_id not in _CONTRACTS:
        raise ValueError('Unknown frozen official disclosure source')
    contract = _CONTRACTS[source_id]
    source = disclosures.SOURCES.get(source_id, {})
    expected = {'url': contract['url'], 'companies': [contract['company']], 'parser': PARSER,
                'intake_mode': MODE, 'bundle_relative': contract['bundle_relative']}
    if (any(source.get(key) != value for key, value in expected.items())
            or not isinstance(source.get('scope'), str) or not source['scope'].strip()):
        raise ValueError('Frozen disclosure source configuration/binding changed')
    return contract, source


def initialize_storage(market):
    """Prepare shared public disclosure schema, then immutable offline ledgers."""
    disclosures.initialize_storage(market)
    with market.db() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS official_disclosure_versions (
          source_id TEXT PRIMARY KEY, version_id TEXT UNIQUE NOT NULL,
          capture_id TEXT UNIQUE NOT NULL, company TEXT NOT NULL,
          bundle_sha256 TEXT NOT NULL, raw_sha256 TEXT NOT NULL, raw_bytes INTEGER NOT NULL,
          manifest_path TEXT NOT NULL, manifest_sha256 TEXT NOT NULL, created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS official_disclosure_intake_events (
          id TEXT PRIMARY KEY, source_id TEXT NOT NULL, capture_id TEXT,
          version_id TEXT, status TEXT NOT NULL, created TEXT NOT NULL, payload TEXT NOT NULL);
        CREATE TRIGGER IF NOT EXISTS official_versions_no_update BEFORE UPDATE ON official_disclosure_versions
          BEGIN SELECT RAISE(ABORT,'Official disclosure versions are append only'); END;
        CREATE TRIGGER IF NOT EXISTS official_versions_no_delete BEFORE DELETE ON official_disclosure_versions
          BEGIN SELECT RAISE(ABORT,'Official disclosure versions are append only'); END;
        CREATE TRIGGER IF NOT EXISTS official_versions_no_replace BEFORE INSERT ON official_disclosure_versions
          WHEN EXISTS (SELECT 1 FROM official_disclosure_versions
            WHERE source_id=NEW.source_id OR version_id=NEW.version_id OR capture_id=NEW.capture_id)
          BEGIN SELECT RAISE(ABORT,'Official disclosure versions are append only'); END;
        CREATE TRIGGER IF NOT EXISTS official_events_no_update BEFORE UPDATE ON official_disclosure_intake_events
          BEGIN SELECT RAISE(ABORT,'Official intake events are append only'); END;
        CREATE TRIGGER IF NOT EXISTS official_events_no_delete BEFORE DELETE ON official_disclosure_intake_events
          BEGIN SELECT RAISE(ABORT,'Official intake events are append only'); END;
        CREATE TRIGGER IF NOT EXISTS official_events_no_replace BEFORE INSERT ON official_disclosure_intake_events
          WHEN EXISTS (SELECT 1 FROM official_disclosure_intake_events WHERE id=NEW.id)
          BEGIN SELECT RAISE(ABORT,'Official intake events are append only'); END;
        CREATE TRIGGER IF NOT EXISTS official_capture_no_update BEFORE UPDATE ON disclosure_captures
          WHEN EXISTS (SELECT 1 FROM official_disclosure_versions WHERE capture_id=OLD.id)
          BEGIN SELECT RAISE(ABORT,'Published official capture is immutable'); END;
        CREATE TRIGGER IF NOT EXISTS official_capture_no_delete BEFORE DELETE ON disclosure_captures
          WHEN EXISTS (SELECT 1 FROM official_disclosure_versions WHERE capture_id=OLD.id)
          BEGIN SELECT RAISE(ABORT,'Published official capture is immutable'); END;
        CREATE TRIGGER IF NOT EXISTS official_capture_no_replace BEFORE INSERT ON disclosure_captures
          WHEN EXISTS (SELECT 1 FROM official_disclosure_versions WHERE capture_id=NEW.id)
          BEGIN SELECT RAISE(ABORT,'Published official capture is immutable'); END;
        CREATE TRIGGER IF NOT EXISTS official_capture_no_rebind BEFORE UPDATE ON disclosure_captures
          WHEN EXISTS (SELECT 1 FROM official_disclosure_versions WHERE capture_id=NEW.id)
          BEGIN SELECT RAISE(ABORT,'Published official capture is immutable'); END;
        CREATE TRIGGER IF NOT EXISTS official_rows_no_update BEFORE UPDATE ON disclosure_observations
          WHEN EXISTS (SELECT 1 FROM official_disclosure_versions WHERE capture_id=OLD.capture_id)
          BEGIN SELECT RAISE(ABORT,'Published official observations are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS official_rows_no_delete BEFORE DELETE ON disclosure_observations
          WHEN EXISTS (SELECT 1 FROM official_disclosure_versions WHERE capture_id=OLD.capture_id)
          BEGIN SELECT RAISE(ABORT,'Published official observations are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS official_rows_no_append BEFORE INSERT ON disclosure_observations
          WHEN EXISTS (SELECT 1 FROM official_disclosure_versions WHERE capture_id=NEW.capture_id)
          BEGIN SELECT RAISE(ABORT,'Published official observations are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS official_rows_no_replace BEFORE INSERT ON disclosure_observations
          WHEN EXISTS (SELECT 1 FROM disclosure_observations o JOIN official_disclosure_versions v
            ON v.capture_id=o.capture_id WHERE o.id=NEW.id)
          BEGIN SELECT RAISE(ABORT,'Published official observations are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS official_rows_no_rebind BEFORE UPDATE ON disclosure_observations
          WHEN EXISTS (SELECT 1 FROM official_disclosure_versions WHERE capture_id=NEW.capture_id)
            OR EXISTS (SELECT 1 FROM disclosure_observations o JOIN official_disclosure_versions v
              ON v.capture_id=o.capture_id WHERE o.id=NEW.id)
          BEGIN SELECT RAISE(ABORT,'Published official observations are immutable'); END;
        ''')


def _payload(market, source_id, contract):
    workspace = market.root.parent.parent.resolve()
    path = within(workspace / contract['bundle_relative'], workspace)
    checked = observations.inspect_bundle(path, workspace)
    if checked['integrity_status'] != 'verified':
        raise ValueError('; '.join(checked['errors']))
    bundle, source = checked['bundle'], checked['source']
    if (bundle['source_id'] != source_id or source['url'] != contract['url']
            or bundle['issuer']['securities'] != [contract['company']]
            or bundle['issuer']['display_name'] != contract['issuer_name']
            or bundle['issuer']['issuer_group'] != contract['issuer_group']):
        raise ValueError('Reviewed bundle is not bound to the configured issuer/source')
    raw_path = within(workspace / source['raw_reference'], workspace / observations._CAPTURE_ROOT)
    with raw_path.open('rb') as handle:
        raw = handle.read(disclosures.MAX_BYTES + 1)
    if (len(raw) != source['raw_bytes'] or len(raw) > disclosures.MAX_BYTES
            or _sha(raw) != source['raw_sha256'] or not raw.startswith(b'%PDF-')):
        raise ValueError('Actual source bytes changed after bundle inspection')
    rows, metadata = observations.project_bundle(bundle)
    if not observations.reviewed_seal(source_id, observations.bundle_digest(bundle), current=True):
        raise ValueError('Reviewed bundle seal changed')
    seal = observations.bundle_digest(bundle)
    version_id = _sha(_json([source_id, source['raw_sha256'], seal]).encode())
    metadata.update(parser=PARSER, intake_mode=MODE, published_at=None, published_at_precision='unknown',
                    human_sign_off=False, drug_ownership_verified=False, scientific_review='pending',
                    current_pipeline_complete=False, bundle_sha256=seal, raw_reference=source['raw_reference'],
                    original_retrieved_at=source['retrieved_at'], reporting_period_year=source['reporting_period_year'],
                    relationship_to_issuer=contract['relationship'], version_id=version_id)
    return {'bundle': bundle, 'raw': raw, 'rows': rows, 'metadata': metadata,
            'version_id': version_id, 'raw_sha256': source['raw_sha256'], 'bundle_sha256': seal}


def _binding(db, contract):
    company = db.execute('SELECT * FROM companies WHERE id=?', (contract['company'],)).fetchone()
    if not company:
        raise ValueError('Configured issuer has no accepted collection mapping')
    company = dict(company)
    aliases = json.loads(company['aliases'])
    if (company['market'] != 'HK' or company['ticker'] != contract['ticker']
            or company['name'] not in (contract['listing_name'], contract['issuer_name'])
            or not isinstance(aliases, list) or aliases != contract['aliases']):
        raise ValueError('Current company/exact sponsor mapping differs from reviewed issuer binding')
    listings = [dict(row) for row in db.execute(
        'SELECT * FROM listings WHERE market=? AND ticker=? ORDER BY id', ('HK', contract['ticker']))]
    if not listings or any(row['name'] != contract['listing_name'] or row['exchange'] != 'HKEX'
                           or row['source'] != 'HK' for row in listings):
        raise ValueError('Current HKEX security identity does not match the reviewed issuer')
    return {'company': company, 'listings': listings, 'scope': 'configured_security_to_exact_source_name_only',
            'human_sign_off': False, 'drug_ownership_verified': False}


def _lease(db, key, token):
    if not db.execute('SELECT 1 FROM leases WHERE key=? AND owner=? AND until>?',
                      (key, token, time.time())).fetchone():
        raise ValueError('Offline intake lease lost; observations not published')


def _event(db, source_id, capture_id, version_id, status, payload):
    db.execute('INSERT INTO official_disclosure_intake_events VALUES (?,?,?,?,?,?,?)',
               (uuid.uuid4().hex, source_id, capture_id, version_id, status, now(), _json(payload)))


def _start(market, source_id, source, token, metadata):
    capture_id = uuid.uuid4().hex
    with market.db() as db:
        db.execute('''INSERT INTO disclosure_captures
          (id,source_id,company,started,finished,status,url,sha256,bytes,archive_path,
           content_type,published_at,as_of,metadata,count,error,lease_owner)
          VALUES (?,?,?,?,?,'running',?,NULL,0,NULL,NULL,NULL,NULL,?,0,'',?)''',
                   (capture_id, source_id, source['companies'][0], now(), None, source['url'], _json(metadata), token))
    return capture_id


def _archive(market, capture_id, source_id, payload, metadata):
    folder = within(market.root / 'disclosures' / 'captures' / capture_id, market.root)
    folder.mkdir(parents=True, exist_ok=False)
    provenance = {'source_id': source_id, 'url': payload['bundle']['source']['url'],
                  'companies': payload['bundle']['issuer']['securities'], 'sha256': payload['raw_sha256'],
                  'bytes': len(payload['raw']), 'content_type': 'application/pdf',
                  'retrieved_at': payload['bundle']['source']['retrieved_at'], 'imported_at': metadata['imported_at'],
                  'published_at': None, 'as_of': metadata['as_of'], 'as_of_precision': metadata['as_of_precision'],
                  'bundle_sha256': payload['bundle_sha256'], 'metadata': metadata,
                  'publication_date_status': 'unknown_not_inferred_from_report_year_or_intake_time'}
    files = {'source.bin': payload['raw'], 'observation_bundle.json': _json(payload['bundle']).encode(),
             'observations.json': _json(payload['rows']).encode(), 'provenance.json': _json(provenance).encode()}
    manifest = {'schema_version': 'official-disclosure-archive-v1', 'source_id': source_id,
                'version_id': payload['version_id'], 'bundle_sha256': payload['bundle_sha256'],
                'original_capture_reference': payload['bundle']['source']['raw_reference'],
                'files': {name: {'sha256': _sha(raw), 'bytes': len(raw)} for name, raw in files.items()}}
    files['manifest.json'] = _json(manifest).encode()
    for name, raw in files.items():
        with (folder / name).open('xb') as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
    directory = os.open(folder, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    # SQLite visibility is the commit point; partial folders never count as published.
    manifest_ref = str((folder / 'manifest.json').relative_to(market.root.resolve()))
    return {'path': manifest_ref, 'sha256': _sha(files['manifest.json']), 'files': manifest['files']}


def _verify_archive(market, capture_id, manifest_meta, payload, expected_metadata=None):
    expected = Path('disclosures') / 'captures' / capture_id / 'manifest.json'
    if manifest_meta['path'] != str(expected):
        raise ValueError('Offline archive manifest path changed')
    manifest_path = within(market.root / expected, market.root)
    if manifest_path.is_symlink() or manifest_path.stat().st_size > 100_000:
        raise ValueError('Unsafe offline archive manifest')
    raw_manifest = _read_bounded(manifest_path, 100_000)
    if _sha(raw_manifest) != manifest_meta['sha256']:
        raise ValueError('Offline archive manifest checksum changed')
    manifest = json.loads(raw_manifest)
    if (manifest['schema_version'] != 'official-disclosure-archive-v1'
            or manifest['source_id'] != payload['bundle']['source_id']
            or manifest['version_id'] != payload['version_id']
            or manifest['bundle_sha256'] != payload['bundle_sha256']
            or manifest['original_capture_reference'] != payload['bundle']['source']['raw_reference']
            or manifest['files'] != manifest_meta['files']
            or set(manifest['files']) != {'source.bin', 'observation_bundle.json', 'observations.json', 'provenance.json'}):
        raise ValueError('Offline archive manifest no longer describes the reviewed version')
    decoded = {}
    for name, entry in manifest['files'].items():
        path = manifest_path.parent / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size != entry['bytes'] or entry['bytes'] > disclosures.MAX_BYTES:
            raise ValueError('Offline archive file missing, unsafe or changed: ' + name)
        raw = _read_bounded(path, disclosures.MAX_BYTES)
        if _sha(raw) != entry['sha256']:
            raise ValueError('Offline archive file checksum changed: ' + name)
        decoded[name] = raw
    if decoded['source.bin'] != payload['raw'] or observations.bundle_digest(json.loads(decoded['observation_bundle.json'])) != payload['bundle_sha256']:
        raise ValueError('Archived PDF/bundle differs from the independently verified bytes')
    if json.loads(decoded['observations.json']) != payload['rows']:
        raise ValueError('Archived rows differ from the reviewed projection')
    provenance = json.loads(decoded['provenance.json'])
    if (provenance['source_id'] != payload['bundle']['source_id']
            or provenance['url'] != payload['bundle']['source']['url']
            or provenance['companies'] != payload['bundle']['issuer']['securities']
            or provenance['sha256'] != payload['raw_sha256'] or provenance['bytes'] != len(payload['raw'])
            or provenance['retrieved_at'] != payload['bundle']['source']['retrieved_at']
            or provenance['published_at'] is not None
            or provenance['as_of'] != payload['metadata']['as_of']
            or provenance['as_of_precision'] != payload['metadata']['as_of_precision']
            or provenance['bundle_sha256'] != payload['bundle_sha256']):
        raise ValueError('Archived provenance differs from the reviewed source')
    if expected_metadata is not None and provenance['metadata'] != {k: v for k, v in expected_metadata.items() if k != 'manifest'}:
        raise ValueError('Capture/provenance metadata differ')


def _existing(db, market, source_id, payload, contract):
    ledger = db.execute('SELECT * FROM official_disclosure_versions WHERE source_id=?', (source_id,)).fetchone()
    if not ledger:
        return None
    if (ledger['version_id'] != payload['version_id'] or ledger['company'] != contract['company']
            or ledger['bundle_sha256'] != payload['bundle_sha256'] or ledger['raw_sha256'] != payload['raw_sha256']
            or ledger['raw_bytes'] != len(payload['raw'])):
        raise ValueError('Source already has an immutable version; use a separately reviewed new source ID')
    cap = db.execute('SELECT * FROM disclosure_captures WHERE id=?', (ledger['capture_id'],)).fetchone()
    if not cap or cap['status'] != 'succeeded' or cap['company'] != contract['company'] or cap['source_id'] != source_id:
        raise ValueError('Published offline capture/ledger mismatch')
    cap = dict(cap)
    meta = json.loads(cap['metadata'])
    if (cap['sha256'] != payload['raw_sha256'] or cap['bytes'] != len(payload['raw'])
            or cap['url'] != contract['url'] or cap['content_type'] != 'application/pdf'
            or cap['archive_path'] != str(Path('disclosures') / 'captures' / cap['id'] / 'source.bin')
            or cap['published_at'] is not None or cap['as_of'] != payload['metadata']['as_of']
            or cap['count'] != len(payload['rows']) or meta['bundle_sha256'] != payload['bundle_sha256']
            or meta['manifest']['sha256'] != ledger['manifest_sha256']
            or meta['manifest']['path'] != ledger['manifest_path']
            or any(meta.get(key) != value for key, value in payload['metadata'].items())):
        raise ValueError('Published offline capture metadata changed')
    _verify_archive(market, cap['id'], meta['manifest'], payload, meta)
    stored = [dict(row) for row in db.execute('SELECT * FROM disclosure_observations WHERE capture_id=? ORDER BY source_locator', (cap['id'],))]
    expected = sorted(payload['rows'], key=lambda row: row['source_locator'])
    if len(stored) != len(expected):
        raise ValueError('Published observation count changed')
    for stored_row, original in zip(stored, expected):
        if (stored_row['company'] != contract['company'] or stored_row['source_id'] != source_id
                or json.loads(stored_row['data']) != original
                or any(stored_row[key] != original[key] for key in
                       ('drug_code', 'drug_name', 'modality', 'target', 'indication', 'phase', 'source_locator'))):
            raise ValueError('Published observation differs from the archived reviewed row')
    return cap


def ingest_offline(market, source_id, *, cancelled=lambda: False, on_start=lambda run_id: None):
    """Atomically publish one fixed historical source, or record a blocked attempt.

    Returns the existing capture shape, plus ``idempotent`` and ``version_id``.
    Repeats revalidate input/archive/issuer and append an event, not duplicate rows.
    No model, HTTP request, arbitrary path or caller-provided company is accepted.
    """
    contract, source = _source(source_id)
    initialize_storage(market)
    key = 'disclosure:' + source_id
    if not market.claim(key):
        return None
    token = market.lease_tokens()[key]
    capture_id, payload, archive = None, None, None
    metadata = {'parser': PARSER, 'intake_mode': MODE, 'scope': source['scope'], 'historical_disclosure': True,
                'published_at': None, 'published_at_precision': 'unknown', 'as_of': None, 'as_of_precision': 'unknown',
                'human_sign_off': False, 'scientific_review': 'pending', 'drug_ownership_verified': False,
                'current_pipeline_complete': False, 'imported_at': now()}
    try:
        if cancelled():
            raise InterruptedError('Offline intake cancelled before evidence inspection')
        payload = _payload(market, source_id, contract)
        metadata.update(payload['metadata'])
        with market.db() as db:
            db.execute('BEGIN IMMEDIATE')
            _lease(db, key, token)
            binding = _binding(db, contract)
            old = _existing(db, market, source_id, payload, contract)
        if old:
            on_start(old['id'])
            if cancelled():
                raise InterruptedError('Offline verification cancelled; published version retained')
            # Callback or another writer may have changed identity/lease after inspection.
            with market.db() as db:
                db.execute('BEGIN IMMEDIATE')
                _source(source_id)
                _lease(db, key, token)
                _binding(db, contract)
                old = _existing(db, market, source_id, _payload(market, source_id, contract), contract)
                _event(db, source_id, old['id'], payload['version_id'], 'verified_existing',
                       {'bundle_sha256': payload['bundle_sha256'], 'human_sign_off': False, 'scientific_review': 'pending'})
            return {**old, 'run_id': old['id'], 'idempotent': True, 'version_id': payload['version_id']}
        metadata['issuer_binding'] = binding
        capture_id = _start(market, source_id, source, token, metadata)
        on_start(capture_id)
        if cancelled():
            raise InterruptedError('Offline intake cancelled before archive/publication')
        archive = _archive(market, capture_id, source_id, payload, metadata)
        metadata['manifest'] = archive
        with market.db() as db:
            db.execute('BEGIN IMMEDIATE')
            _source(source_id)
            _lease(db, key, token)
            if _binding(db, contract) != binding:
                raise ValueError('Current issuer identity changed before publication; retry required')
            final_payload = _payload(market, source_id, contract)
            if final_payload['version_id'] != payload['version_id'] or final_payload['rows'] != payload['rows']:
                raise ValueError('Reviewed inputs changed before publication')
            _verify_archive(market, capture_id, archive, payload, metadata)
            if cancelled():
                raise InterruptedError('Offline intake cancelled before publication')
            if db.execute('SELECT 1 FROM official_disclosure_versions WHERE source_id=?', (source_id,)).fetchone():
                raise ValueError('Offline version was already published by another owner')
            current = db.execute('SELECT status,lease_owner FROM disclosure_captures WHERE id=?', (capture_id,)).fetchone()
            if not current or current['status'] != 'running' or current['lease_owner'] != token:
                raise ValueError('Offline capture no longer belongs to this intake')
            for row in payload['rows']:
                db.execute('INSERT INTO disclosure_observations VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                           (uuid.uuid4().hex, capture_id, source_id, contract['company'], row['drug_code'], row['drug_name'],
                            row['modality'], row['target'], row['indication'], row['phase'], row['source_locator'], _json(row)))
            relative = str(Path(archive['path']).with_name('source.bin'))
            db.execute('''UPDATE disclosure_captures SET finished=?,status='succeeded',sha256=?,bytes=?,archive_path=?,
                          content_type='application/pdf',published_at=NULL,as_of=?,metadata=?,count=?,error='' WHERE id=?''',
                       (now(), payload['raw_sha256'], len(payload['raw']), relative, metadata['as_of'], _json(metadata), len(payload['rows']), capture_id))
            db.execute('INSERT INTO official_disclosure_versions VALUES (?,?,?,?,?,?,?,?,?,?)',
                       (source_id, payload['version_id'], capture_id, contract['company'], payload['bundle_sha256'],
                        payload['raw_sha256'], len(payload['raw']), archive['path'], archive['sha256'], now()))
            _event(db, source_id, capture_id, payload['version_id'], 'published',
                   {'manifest': archive, 'human_sign_off': False, 'scientific_review': 'pending'})
            result = dict(db.execute('SELECT * FROM disclosure_captures WHERE id=?', (capture_id,)).fetchone())
        return {**result, 'run_id': capture_id, 'idempotent': False, 'version_id': payload['version_id']}
    except Exception as exc:
        status = 'cancelled' if isinstance(exc, InterruptedError) else 'parse_blocked'
        error = type(exc).__name__ + ': ' + str(exc)[:500]
        if capture_id is None:
            capture_id = _start(market, source_id, source, token, metadata)
            try:
                on_start(capture_id)
            except Exception as callback_error:
                error += '; on_start: ' + type(callback_error).__name__ + ': ' + str(callback_error)[:100]
        with market.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT 1 FROM leases WHERE key=? AND owner=? AND until>?', (key, token, time.time())).fetchone():
                status = 'superseded'
            metadata['intake_error'] = error
            if archive:
                metadata['manifest'] = archive
            relative = str(Path(archive['path']).with_name('source.bin')) if archive else None
            db.execute('''UPDATE disclosure_captures SET finished=?,status=?,sha256=?,bytes=?,archive_path=?,
                          content_type=?,published_at=NULL,as_of=?,metadata=?,count=0,error=? WHERE id=? AND status='running' ''',
                       (now(), status, payload['raw_sha256'] if archive else None, len(payload['raw']) if archive else 0,
                        relative, 'application/pdf' if archive else None, metadata['as_of'], _json(metadata), error, capture_id))
            _event(db, source_id, capture_id, payload['version_id'] if payload else None, status,
                   {'error': error, 'human_sign_off': False, 'scientific_review': 'pending'})
            result = dict(db.execute('SELECT * FROM disclosure_captures WHERE id=?', (capture_id,)).fetchone())
        return {**result, 'run_id': capture_id, 'idempotent': False, 'version_id': payload['version_id'] if payload else None}
    finally:
        market.release(key, token)


def intake_history(market, source_id='', limit=200):
    """Append-only local verification/publication history, including repeat checks."""
    if source_id and source_id not in _CONTRACTS or type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError('Invalid official intake history query')
    initialize_storage(market)
    where, args = (' WHERE source_id=?', [source_id]) if source_id else ('', [])
    with market.db() as db:
        rows = [dict(row) for row in db.execute('SELECT * FROM official_disclosure_intake_events' + where + ' ORDER BY created DESC,id DESC LIMIT ?', args + [limit])]
    for row in rows:
        row['payload'] = json.loads(row['payload'])
    return rows


def verify_capture(market, capture_id):
    """Read-only verification of a published offline capture and all evidence.

    Does not recreate schema, change capture status, repair files, acquire leases,
    or trust a ``succeeded`` flag. Exact archived bytes can be restored externally;
    this verifier never overwrites an immutable version to hide missing evidence.
    """
    result = {'integrity_status': 'blocked', 'errors': [], 'capture_id': capture_id,
              'historical_disclosure': True, 'human_sign_off': False,
              'drug_ownership_verified': False, 'scientific_review': 'pending'}
    try:
        if not isinstance(capture_id, str) or len(capture_id) != 32 or any(ch not in '0123456789abcdef' for ch in capture_id):
            raise ValueError('Invalid offline capture identifier')
        with market.db() as db:
            db.execute('BEGIN')
            cap = db.execute('SELECT * FROM disclosure_captures WHERE id=?', (capture_id,)).fetchone()
            if not cap or cap['status'] != 'succeeded':
                raise ValueError('No successful frozen official capture to verify')
            source_id = cap['source_id']
            contract, _ = _source(source_id)
            _binding(db, contract)
            folder = market.root / 'disclosures' / 'captures' / capture_id
            bundle_path = folder / 'observation_bundle.json'
            raw_path = folder / 'source.bin'
            within(bundle_path, market.root)
            within(raw_path, market.root)
            if bundle_path.is_symlink() or bundle_path.stat().st_size > 500_000 or raw_path.is_symlink():
                raise ValueError('Unsafe or oversized sealed PDF/bundle path')
            bundle = json.loads(_read_bounded(bundle_path, 500_000))
            observations.validate_contract(bundle)
            seal = observations.bundle_digest(bundle)
            source = bundle['source']
            if (bundle['source_id'] != source_id or not observations.reviewed_seal(source_id, seal)
                    or source['url'] != contract['url'] or bundle['issuer']['securities'] != [contract['company']]
                    or bundle['issuer']['display_name'] != contract['issuer_name']
                    or bundle['issuer']['issuer_group'] != contract['issuer_group']):
                raise ValueError('Sealed observation bundle no longer matches the independent review')
            if raw_path.stat().st_size != source['raw_bytes'] or source['raw_bytes'] > disclosures.MAX_BYTES:
                raise ValueError('Sealed source PDF size changed')
            raw = _read_bounded(raw_path, disclosures.MAX_BYTES)
            if _sha(raw) != source['raw_sha256'] or not raw.startswith(b'%PDF-'):
                raise ValueError('Sealed source PDF checksum/magic changed')
            rows, metadata = observations.project_bundle(bundle)
            metadata.update(parser=PARSER, intake_mode=MODE, published_at=None, published_at_precision='unknown',
                            human_sign_off=False, drug_ownership_verified=False, scientific_review='pending',
                            current_pipeline_complete=False, bundle_sha256=seal, raw_reference=source['raw_reference'],
                            original_retrieved_at=source['retrieved_at'], reporting_period_year=source['reporting_period_year'],
                            relationship_to_issuer=contract['relationship'])
            version_id = _sha(_json([source_id, source['raw_sha256'], seal]).encode())
            metadata['version_id'] = version_id
            payload = {'bundle': bundle, 'raw': raw, 'rows': rows, 'metadata': metadata,
                       'version_id': version_id, 'raw_sha256': source['raw_sha256'], 'bundle_sha256': seal}
            verified = _existing(db, market, source_id, payload, contract)
            if not verified or verified['id'] != capture_id:
                raise ValueError('Capture is not the sealed immutable source version')
        result.update(integrity_status='verified', source_id=source_id, company=contract['company'],
                      version_id=version_id, count=len(rows), bundle_sha256=seal, sha256=source['raw_sha256'],
                      as_of=metadata['as_of'], as_of_precision=metadata['as_of_precision'])
    except Exception as exc:
        result['errors'].append(type(exc).__name__ + ': ' + str(exc)[:500])
    return result
