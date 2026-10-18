"""Offline English issuer names from sealed SZ/SH/STAR directory captures.

Names are query seeds, never accepted sponsor aliases. Only ``enrich`` writes,
and only to append-only receipt/audit tables. Query helpers revalidate source
bytes and the current snapshot, listing and candidate on every call. No HTTP.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
import zipfile
import io
from contextlib import nullcontext
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

from . import market_sources
from .io import now
from .market import universe_digest

SOURCES = ('SZ', 'SH', 'STAR')
VERSION = 'issuer-directory-names-2'
MAX_BYTES = 20_000_000
_HEX = re.compile(r'[a-f0-9]{64}')
_RUN = re.compile(r'[a-f0-9]{32}')
_NON_ENGLISH = re.compile(r'[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af<>]')
_SOURCE_ERRORS = (ValueError, OSError, KeyError, TypeError, AttributeError, zipfile.BadZipFile,
                  ET.ParseError, IndexError, OverflowError, RecursionError)


def _serial(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _unique(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError('Ambiguous duplicate JSON field')
        out[key] = value
    return out


def _json(raw):
    return json.loads(raw, object_pairs_hook=_unique)


def _stamp(value):
    if not isinstance(value, str):
        raise ValueError('Missing directory timestamp')
    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError('Directory timestamp lacks timezone')
    return dt


def _text(value, *, english=False):
    if not isinstance(value, str) or not value.strip() or value.strip() in ('-', '--') or len(value) > 400:
        raise ValueError('Missing or invalid named directory field')
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError('Control character in directory field')
    if english and (_NON_ENGLISH.search(value) or not re.search('[A-Za-z]', value)):
        raise ValueError('Directory English-name field is not an English name')
    return value  # Exact field text; normalization belongs to the query builder.


def _local(market):
    if Path(market.state).resolve().is_relative_to('/Volumes'):
        raise ValueError('Directory name registry state must be local')


def _schema(market):
    _local(market)
    with market.db() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS issuer_directory_name_receipts (
          id TEXT PRIMARY KEY, created TEXT NOT NULL, candidate_id TEXT NOT NULL,
          candidate_hash TEXT NOT NULL, source TEXT NOT NULL, source_run_id TEXT NOT NULL,
          payload_json TEXT NOT NULL, payload_sha256 TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_directory_names_candidate
          ON issuer_directory_name_receipts(candidate_id,candidate_hash);
        CREATE TABLE IF NOT EXISTS issuer_directory_name_audits (
          id TEXT PRIMARY KEY, created TEXT NOT NULL, source TEXT NOT NULL,
          status TEXT NOT NULL, payload_json TEXT NOT NULL
        );
        CREATE TRIGGER IF NOT EXISTS directory_names_no_update BEFORE UPDATE ON issuer_directory_name_receipts
          BEGIN SELECT RAISE(ABORT,'Directory name receipts are append-only'); END;
        CREATE TRIGGER IF NOT EXISTS directory_names_no_delete BEFORE DELETE ON issuer_directory_name_receipts
          BEGIN SELECT RAISE(ABORT,'Directory name receipts are append-only'); END;
        CREATE TRIGGER IF NOT EXISTS directory_names_no_replace BEFORE INSERT ON issuer_directory_name_receipts
          WHEN EXISTS (SELECT 1 FROM issuer_directory_name_receipts WHERE id=NEW.id)
          BEGIN SELECT RAISE(ABORT,'Directory name receipts are append-only'); END;
        CREATE TRIGGER IF NOT EXISTS directory_names_audit_no_update BEFORE UPDATE ON issuer_directory_name_audits
          BEGIN SELECT RAISE(ABORT,'Directory name audits are append-only'); END;
        CREATE TRIGGER IF NOT EXISTS directory_names_audit_no_delete BEFORE DELETE ON issuer_directory_name_audits
          BEGIN SELECT RAISE(ABORT,'Directory name audits are append-only'); END;
        CREATE TRIGGER IF NOT EXISTS directory_names_audit_no_replace BEFORE INSERT ON issuer_directory_name_audits
          WHEN EXISTS (SELECT 1 FROM issuer_directory_name_audits WHERE id=NEW.id)
          BEGIN SELECT RAISE(ABORT,'Directory name audits are append-only'); END;
        ''')


def _capture(market, run_id):
    if not isinstance(run_id, str) or not _RUN.fullmatch(run_id):
        raise ValueError('Invalid directory source run identifier')
    root = Path(market.root)
    folder = root / 'captures' / run_id
    if folder.absolute() != folder.resolve():
        raise ValueError('Directory archive namespace contains a symlink')
    paths = [folder / '0.bin', folder / '0.json']
    values = []
    for path, maximum in zip(paths, (MAX_BYTES, 64_000)):
        if path.is_symlink() or path.resolve() != path.absolute() or not path.is_file():
            raise ValueError('Missing or aliased directory archive')
        if not 0 < path.stat().st_size <= maximum:
            raise ValueError('Directory archive is empty or oversized')
        value = path.read_bytes()
        if not 0 < len(value) <= maximum:
            raise ValueError('Directory archive changed while reading')
        values.append(value)
    return values[0], _json(values[1]), paths[0], _sha(values[1])


def _extract(source, raw):
    """Extract explicitly named full-name fields; short English names never substitute."""
    out = {}
    if source == 'SZ':
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)) or len(names) > 200 or sum(i.file_size for i in archive.infolist()) > 100_000_000:
                raise ValueError('Ambiguous or oversized directory workbook')
            worksheet = archive.read('xl/worksheets/sheet1.xml')
            if b'<!DOCTYPE' in worksheet.upper() or b'<!ENTITY' in worksheet.upper():
                raise ValueError('Directory worksheet entity declaration rejected')
            cells = ET.fromstring(worksheet).findall('.//{http://schemas.openxmlformats.org/spreadsheetml/2006/main}c')
            references = [cell.get('r') for cell in cells]
            if (len(set(references)) != len(references)
                    or any(not isinstance(ref, str) or not re.fullmatch(r'[A-Z]+[1-9][0-9]*', ref) for ref in references)):
                raise ValueError('Ambiguous worksheet cell references')
        sheet = market_sources.xlsx_rows(raw)
        headers = [(i, row) for i, row in enumerate(sheet) if 'A股代码' in row.values()]
        if len(headers) != 1:
            raise ValueError('Missing or ambiguous SZSE header')
        index, header = headers[0]
        required = ('A股代码', 'A股简称', '公司全称', '英文名称', '公司网址')
        if any(list(header.values()).count(key) != 1 for key in required):
            raise ValueError('Missing or duplicate SZSE named field')
        columns = {v: k for k, v in header.items()}
        rows = [(str(i + 1), row.get(columns['A股代码']), row.get(columns['A股简称']),
                 row.get(columns['公司全称']), row.get(columns['英文名称']), row.get(columns['公司网址'], ''))
                for i, row in enumerate(sheet) if i > index and row.get(columns['A股代码'])]
    else:
        data = _json(raw)
        records = data.get('result', data.get('pageHelp', {}).get('data', []))
        rows = []
        if not isinstance(records, list):
            raise ValueError('Missing SSE directory rows')
        for i, row in enumerate(records):
            if not isinstance(row, dict) or not {'A_STOCK_CODE', 'COMPANY_ABBR', 'FULL_NAME', 'FULL_NAME_IN_ENGLISH'}.issubset(row):
                raise ValueError('Missing named SSE identity field')
            rows.append((f'result[{i}]', row['A_STOCK_CODE'], row['COMPANY_ABBR'],
                         row['FULL_NAME'], row['FULL_NAME_IN_ENGLISH'], ''))
    for locator, code, short_name, chinese, english, website in rows:
        if not isinstance(code, str) or not re.fullmatch('[0-9]{6}', code):
            raise ValueError('Invalid explicit directory security code')
        if code in out:
            raise ValueError('Duplicate security in issuer-name directory')
        _text(short_name)
        # A missing row field stays unknown for that security; it is never filled
        # from a short name, website, translated title or another issuer.
        try:
            _text(chinese)
            _text(english, english=True)
            if not isinstance(website, str) or len(website) > 2000 or any(ord(c) < 32 for c in website):
                raise ValueError('Invalid directory website reference')
            error = ''
        except ValueError as exc:
            error = str(exc)
        out[code] = {'ticker': code, 'listing_name': short_name, 'chinese_name': chinese,
                     'english_name': english, 'website_reference': website, 'source_locator': locator,
                     'field_error': error}
    return out


def _source(market, db, source):
    snapshot = db.execute('SELECT * FROM universe_snapshots WHERE source=?', (source,)).fetchone()
    if not snapshot:
        raise ValueError('No sealed directory snapshot')
    snapshot = dict(snapshot)
    run = db.execute('SELECT * FROM runs WHERE id=?', (snapshot['run_id'],)).fetchone()
    if not run or run['source'] != source or run['company'] != '' or run['status'] != 'succeeded':
        raise ValueError('Directory snapshot is not bound to a successful universe run')
    if _stamp(run['finished']) < _stamp(run['started']):
        raise ValueError('Invalid directory run chronology')
    _stamp(snapshot['published'])
    raw, meta, path, meta_sha = _capture(market, snapshot['run_id'])
    if (not isinstance(meta, dict) or meta.get('url') != market_sources.URLS[source]
            or meta.get('sha256') != _sha(raw) or type(meta.get('bytes')) is not int
            or meta['bytes'] != len(raw) or meta.get('method', 'GET') != 'GET'
            or meta.get('status', 200) != 200):
        raise ValueError('Directory capture URL, bytes, SHA or HTTP metadata mismatch')
    _stamp(meta.get('retrieved_at'))
    # Parse JSON with duplicate detection even though the shared parser is used
    # for an independent replay of the exact normalized listing digest.
    if source != 'SZ':
        _json(raw)
    rows, total = market_sources.parse_universe(source, raw)
    count = len(rows)
    if (type(snapshot['count']) is not int or type(run['count']) is not int
            or not count or count != snapshot['count'] or count != run['count']
            or (total is not None and count != total)):
        raise ValueError('Incomplete or inconsistent source directory count')
    keys = [(r['market'], r['exchange'], r['ticker']) for r in rows]
    if len(set(keys)) != count or any(r['market'] != 'A' for r in rows):
        raise ValueError('Ambiguous directory security keys')
    current = [dict(r) for r in db.execute('SELECT * FROM listings WHERE source=?', (source,))]
    digest = universe_digest(rows)
    if (not isinstance(snapshot['rows_sha256'], str) or not _HEX.fullmatch(snapshot['rows_sha256'])
            or digest != snapshot['rows_sha256'] or len(current) != count
            or universe_digest(current) != digest):
        raise ValueError('Directory replay/current listing snapshot hash mismatch')
    names = _extract(source, raw)
    if len(names) != count or any(names.get(r['ticker'], {}).get('listing_name') != r['name'] for r in rows):
        raise ValueError('Full-name records do not exactly bind parsed listings')
    return {'source': source, 'source_run_id': snapshot['run_id'], 'raw_sha256': _sha(raw),
            'raw_bytes': len(raw), 'metadata_sha256': meta_sha,
            'source_url': meta['url'], 'retrieved_at': meta['retrieved_at'],
            'rows_sha256': digest, 'directory_count': count,
            'capture_reference': str(path.relative_to(Path(market.root))),
            'names': names, 'listing_by_ticker': {r['ticker']: r for r in current}}


def _payload(source, candidate):
    if (candidate['market'] != 'A' or candidate['id'] != 'A:' + candidate['ticker']
            or not _HEX.fullmatch(candidate['content_hash'])):
        raise ValueError('Invalid current candidate identity')
    row = source['names'].get(candidate['ticker'])
    listing = source['listing_by_ticker'].get(candidate['ticker'])
    if not row or row['field_error'] or not listing or row['listing_name'] != candidate['name']:
        raise ValueError('Missing full name or current candidate/listing conflict')
    return {k: v for k, v in source.items() if k not in ('names', 'listing_by_ticker')} | {
        k: v for k, v in row.items() if k != 'field_error'} | {
        'schema_version': VERSION, 'candidate_id': candidate['id'], 'candidate_hash': candidate['content_hash'],
        'listing_id': listing['id'], 'scope': 'exchange_published_issuer_legal_name_query_seed',
        'registry_alias_proven': False, 'drug_ownership_verified': False, 'human_sign_off': False}


def enrich(market, source='all'):
    """Append source-bound name receipts; never modify candidate/company/trial rows.

    Each source is atomic. Missing fields fail closed per security; malformed or
    incomplete source snapshots block that whole source. Old receipts are retained.
    """
    if source != 'all' and source not in SOURCES:
        raise ValueError('Unsupported issuer directory source')
    _schema(market)
    result = {'status': 'succeeded', 'added': 0, 'unchanged': 0, 'sources': []}
    for code in SOURCES if source == 'all' else (source,):
        outcome = {'source': code, 'status': 'succeeded', 'added': 0, 'unchanged': 0, 'unresolved': []}
        try:
            with market.db() as db:
                db.execute('BEGIN IMMEDIATE')
                checked = _source(market, db, code)
                candidates = [dict(r) for r in db.execute("SELECT * FROM issuer_candidates WHERE market='A'")]
                for candidate in candidates:
                    if candidate['ticker'] not in checked['names']:
                        continue
                    try:
                        payload = _payload(checked, candidate)
                    except ValueError as exc:
                        outcome['unresolved'].append({'candidate_id': candidate['id'], 'reason': str(exc)})
                        continue
                    serial = _serial(payload)
                    rid = _sha(serial.encode())
                    old = db.execute('SELECT * FROM issuer_directory_name_receipts WHERE id=?', (rid,)).fetchone()
                    if old:
                        if (not old or old['payload_json'] != serial or old['payload_sha256'] != rid
                                or old['candidate_id'] != candidate['id'] or old['candidate_hash'] != candidate['content_hash']
                                or old['source'] != code or old['source_run_id'] != checked['source_run_id']):
                            raise ValueError('Existing directory receipt integrity conflict')
                    else:
                        db.execute('INSERT INTO issuer_directory_name_receipts VALUES (?,?,?,?,?,?,?,?)',
                            (rid, now(), candidate['id'], candidate['content_hash'], code, checked['source_run_id'], serial, rid))
                    outcome['unchanged' if old else 'added'] += 1
                outcome['source_run_id'] = checked['source_run_id']
                outcome['directory_count'] = checked['directory_count']
                db.execute('INSERT INTO issuer_directory_name_audits VALUES (?,?,?,?,?)',
                           (uuid.uuid4().hex, now(), code, 'succeeded', _serial(outcome)))
        except _SOURCE_ERRORS + (sqlite3.DatabaseError,) as exc:
            outcome = {'source': code, 'status': 'blocked', 'error': str(exc), 'added': 0, 'unchanged': 0}
            with market.db() as db:
                db.execute('INSERT INTO issuer_directory_name_audits VALUES (?,?,?,?,?)',
                           (uuid.uuid4().hex, now(), code, 'blocked', _serial(outcome)))
        result['sources'].append(outcome)
        result['added'] += outcome['added']
        result['unchanged'] += outcome['unchanged']
    if any(r['status'] == 'blocked' for r in result['sources']):
        result['status'] = 'partial' if any(r['status'] == 'succeeded' for r in result['sources']) else 'blocked'
    return result


def _query_evidences(market, candidates, *, db=None):
    """One DB read snapshot and one source replay per source, with no DDL/writes."""
    _local(market)
    requested = {}
    for candidate in candidates:
        try:
            row = dict(candidate)
            if isinstance(row.get('id'), str) and _HEX.fullmatch(row.get('content_hash', '')):
                if row['id'] in requested and requested[row['id']] != row['content_hash']:
                    requested[row['id']] = None
                else:
                    requested[row['id']] = row['content_hash']
        except (ValueError, TypeError):
            continue
    if not requested:
        return {}
    own_connection = db is None
    with (market.db() if own_connection else nullcontext(db)) as db:
        if own_connection:
            db.execute('PRAGMA query_only=ON')
            db.execute('BEGIN')
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='issuer_directory_name_receipts'").fetchone():
            return {}
        current, receipts = {}, []
        ids = list(requested)
        for offset in range(0, len(ids), 400):
            part = ids[offset:offset + 400]
            marks = ','.join('?' for _ in part)
            current.update((r['id'], dict(r)) for r in db.execute('SELECT * FROM issuer_candidates WHERE id IN (' + marks + ')', part))
            receipts.extend(dict(r) for r in db.execute('SELECT * FROM issuer_directory_name_receipts WHERE candidate_id IN (' + marks + ')', part))
        sources = {}
        for code in {r['source'] for r in receipts} & set(SOURCES):
            try:
                sources[code] = _source(market, db, code)
            except _SOURCE_ERRORS:
                continue
        found = {}
        for receipt in receipts:
            cid = receipt['candidate_id']
            candidate = current.get(cid)
            checked = sources.get(receipt['source'])
            if not candidate or requested[cid] != candidate['content_hash'] or not checked:
                continue
            try:
                expected = _payload(checked, candidate)
                serial = _serial(expected)
                digest = _sha(serial.encode())
                if (receipt['payload_json'] != serial or receipt['id'] != digest
                        or receipt['payload_sha256'] != digest or receipt['candidate_hash'] != candidate['content_hash']
                        or receipt['source_run_id'] != checked['source_run_id']):
                    continue
                found.setdefault(cid, []).append(expected | {'receipt_id': receipt['id'], 'ledger_id': receipt['id']})
            except (ValueError, KeyError, TypeError):
                continue
        return {cid: sorted(rows, key=lambda r: (r['source'], r['receipt_id']))[0]
                for cid, rows in found.items() if len({r['english_name'] for r in rows}) == 1}


def query_evidences(market, candidates, *, db=None):
    """Batch verified receipts; optional caller transaction is never committed here."""
    try:
        return _query_evidences(market, candidates, db=db)
    except (ValueError, TypeError, KeyError, OSError, sqlite3.DatabaseError):
        return {}


def query_evidence(market, candidate, *, db=None):
    """Return a reverified receipt, or unknown; caller payload cannot self-attest."""
    try:
        row = dict(candidate)
        return query_evidences(market, [row], db=db).get(row.get('id'))
    except (ValueError, TypeError, KeyError, OSError, sqlite3.DatabaseError):
        return None


def query_name(market, candidate):
    evidence = query_evidence(market, candidate)
    return evidence['english_name'] if evidence else None


def query_names(market, candidates):
    """Batch string map; replay each involved successful source only once."""
    try:
        return {cid: row['english_name'] for cid, row in query_evidences(market, candidates).items()}
    except (ValueError, TypeError, KeyError, OSError, sqlite3.DatabaseError):
        return {}
