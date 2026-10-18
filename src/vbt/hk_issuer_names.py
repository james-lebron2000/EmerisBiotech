"""Read-only HKEX source audits and bounded official issuer-name parsing.

The securities workbook contains security short names, not legal full names.
The separate Southbound holdings page supplies original English issuer-name
query seeds for a subset of securities. Neither source proves drug ownership,
business classification, sponsor association, or complete market coverage.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
import hashlib
import io
import re
import sqlite3
import uuid
import xml.etree.ElementTree as ET
import zipfile

from . import issuer_directory_names as sealed_names, market_sources
from .market import universe_digest

VERSION = 'hk-issuer-names-1'
AUDIT_SCHEMA_VERSION = 1
SOUTHBOUND_URL = 'https://www3.hkexnews.hk/sdw/search/mutualmarket.aspx?t=hk'
MAX_HTML_BYTES = 4_000_000
_ERRORS = (ValueError, OSError, KeyError, TypeError, AttributeError, IndexError,
           OverflowError, RecursionError, sqlite3.DatabaseError, zipfile.BadZipFile, ET.ParseError)


def _content_date(raw):
    # HKEX prints the workbook date in A2. Read that explicit header cell only;
    # do not build a second full securities worksheet for every UI request.
    ns = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries = archive.namelist()
        if len(entries) != len(set(entries)):
            raise ValueError('Duplicate HK workbook ZIP member')
        target = None
        for event, cell in ET.iterparse(io.BytesIO(archive.read('xl/worksheets/sheet1.xml')), events=('end',)):
            if cell.tag != ns + 'c':
                continue
            if cell.get('r') == 'A2':
                value = cell.find(ns + 'v')
                if cell.get('t') == 's' and value is not None and value.text:
                    index = int(value.text)
                    for number, (_, string) in enumerate((event, item) for event, item in
                            ET.iterparse(io.BytesIO(archive.read('xl/sharedStrings.xml')), events=('end',))
                            if item.tag == ns + 'si'):
                        if number == index:
                            target = ''.join(string.itertext()); break
                else:
                    inline = cell.find(ns + 'is')
                    target = ''.join(inline.itertext()) if inline is not None else value.text if value is not None else None
                break
            cell.clear()
    match = re.fullmatch(r'Updated as at\s+(\d{2})/(\d{2})/(\d{4})', target.strip()) if isinstance(target, str) else None
    try:
        return (date(int(match[3]), int(match[2]), int(match[1])), 'sheet1!A2') if match else (None, None)
    except ValueError:
        return None, 'sheet1!A2'


def _directory(market, db):
    snapshot = db.execute("SELECT * FROM universe_snapshots WHERE source='HK'").fetchone()
    if not snapshot:
        raise ValueError('No current sealed HK directory snapshot')
    snapshot = dict(snapshot)
    rid = snapshot['run_id']
    run = db.execute('SELECT * FROM runs WHERE id=?', (rid,)).fetchone()
    if not run or run['source'] != 'HK' or run['company'] != '' or run['status'] != 'succeeded':
        raise ValueError('HK snapshot is not a successful universe run')
    started = sealed_names._stamp(run['started'])
    finished = sealed_names._stamp(run['finished'])
    sealed_names._stamp(snapshot['published'])
    if finished < started:
        raise ValueError('HK universe run chronology is invalid')
    raw, meta, path, meta_sha = sealed_names._capture(market, rid)
    if (not isinstance(meta, dict) or meta.get('url') != market_sources.URLS['HK']
            or meta.get('sha256') != sealed_names._sha(raw)
            or type(meta.get('bytes')) is not int or meta['bytes'] != len(raw)
            or meta.get('method', 'GET') != 'GET' or meta.get('status', 200) != 200):
        raise ValueError('HK directory raw/source metadata mismatch')
    sealed_names._stamp(meta.get('retrieved_at'))
    rows, _ = market_sources.parse_universe('HK', raw)
    count = len(rows)
    if (type(snapshot['count']) is not int or type(run['count']) is not int
            or not count or count != snapshot['count'] or count != run['count']
            or len({(r['market'], r['exchange'], r['ticker']) for r in rows}) != count
            or any(r['market'] != 'HK' or r['exchange'] != 'HKEX' for r in rows)):
        raise ValueError('HK directory count/key mismatch')
    current = [dict(r) for r in db.execute("SELECT * FROM listings WHERE source='HK'")]
    digest = universe_digest(rows)
    if (digest != snapshot['rows_sha256'] or len(current) != count
            or universe_digest(current) != digest):
        raise ValueError('HK directory/current listing snapshot hash mismatch')
    content_date, locator = _content_date(raw)
    date_status = ('unknown' if content_date is None else 'future_dated'
                   if content_date > datetime.now(timezone.utc).date() else 'known')
    return {'schema_version': AUDIT_SCHEMA_VERSION, 'adapter_version': VERSION,
        'source': 'HK', 'source_run_id': rid, 'raw_sha256': sealed_names._sha(raw),
        'raw_bytes': len(raw), 'metadata_sha256': meta_sha,
        'retrieved_at': meta['retrieved_at'], 'source_url': meta['url'],
        'capture_reference': str(path.relative_to(Path(market.root))),
        'directory_count': count, 'rows_sha256': digest,
        'content_as_of': content_date.isoformat() if content_date else None,
        'date_locator': locator, 'date_status': date_status,
        'status': 'verified' if date_status == 'known' else 'verified_bytes_date_anomaly',
        'scope': 'current_local_hk_equity_security_snapshot_only',
        'issuer_legal_names_present': False, 'error': ''}


def directory_audit(market, db=None):
    """Verify current HK bytes and listings using SELECTs only; never run DDL.

    Version 1 statuses are ``verified``, ``verified_bytes_date_anomaly``, and
    ``blocked``. Date status is ``known``, ``future_dated``, or ``unknown``.
    Blocked sources have an unknown count, never a fabricated zero. The date
    printed inside the workbook remains separate from capture ``retrieved_at``.
    An optional caller connection is never committed, closed, or recovered here.
    """
    own = None
    result = {'schema_version': AUDIT_SCHEMA_VERSION, 'adapter_version': VERSION,
        'source': 'HK', 'source_run_id': None, 'status': 'blocked',
        'date_status': 'unknown', 'content_as_of': None, 'date_locator': None,
        'directory_count': None, 'raw_sha256': None, 'raw_bytes': None,
        'retrieved_at': None, 'error': ''}
    try:
        if db is None:
            path = Path(market.path).resolve()
            own = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
            own.row_factory = sqlite3.Row
            own.execute('BEGIN')
            db = own
        return _directory(market, db)
    except _ERRORS as exc:
        result['error'] = f'{type(exc).__name__}: {exc}'
        return result
    finally:
        if own is not None:
            own.close()


class _HoldingsParser(HTMLParser):
    """Read a single explicit server-rendered results table, never scripts."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.table_count = 0
        self.active = False
        self.closed = False
        self.html_closed = False
        self.row = None
        self.cell = None
        self.body_depth = 0
        self.tbody_open = False
        self.tbody_count = 0
        self.tbody_closed = False
        self.headers = []
        self.rows = []
        self.text = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ('script', 'style'):
            self.skip += 1
        if tag == 'table':
            if self.active:
                raise ValueError('Nested HK results table')
            if attrs.get('id') == 'mutualmarket-result':
                self.table_count += 1
                self.active = True
        if not self.active:
            return
        if tag == 'tbody':
            if self.tbody_open:
                raise ValueError('Nested HK results body')
            self.tbody_open = True
            self.tbody_count += 1
        elif tag == 'tr':
            if self.row is not None:
                raise ValueError('Unclosed HK results row')
            self.row = []
        elif tag in ('td', 'th'):
            if self.row is None or self.cell is not None:
                raise ValueError('Ambiguous HK results cell')
            self.cell = {'kind': tag, 'class': attrs.get('class', '').split(), 'text': [], 'bodies': 0}
        elif tag == 'div' and self.cell is not None:
            if 'mobile-list-body' in attrs.get('class', '').split():
                if self.body_depth:
                    raise ValueError('Nested HK issuer field')
                self.body_depth = 1
                self.cell['bodies'] += 1
            elif self.body_depth:
                self.body_depth += 1

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.skip = max(self.skip - 1, 0)
        if tag == 'html':
            self.html_closed = True
        if not self.active:
            return
        if tag == 'div' and self.body_depth:
            self.body_depth -= 1
        elif tag in ('td', 'th'):
            if self.cell is None or self.cell['kind'] != tag or self.body_depth:
                raise ValueError('Unclosed HK results field')
            self.row.append(self.cell)
            self.cell = None
        elif tag == 'tr':
            if self.row is None or self.cell is not None:
                raise ValueError('Unclosed HK results row')
            if all(c['kind'] == 'th' for c in self.row):
                self.headers.append([' '.join(''.join(c['text']).split()) for c in self.row])
            else:
                self.rows.append(self.row)
            self.row = None
        elif tag == 'tbody':
            if not self.tbody_open or self.row is not None or self.cell is not None:
                raise ValueError('Unclosed HK results body')
            self.tbody_open = False
            self.tbody_closed = True
        elif tag == 'table':
            if self.row is not None or self.cell is not None or self.tbody_open:
                raise ValueError('Unclosed HK results table')
            self.active = False
            self.closed = True

    def handle_data(self, data):
        if self.skip:
            return
        self.text.append(data)
        if self.cell is not None and (self.cell['kind'] == 'th' or self.body_depth):
            self.cell['text'].append(data)


def parse_southbound(raw):
    """Parse original Stock Code/Name fields; this is a holdings subset only."""
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_HTML_BYTES:
        raise ValueError('HK issuer-name HTML is empty or exceeds 4 MB')
    parser = _HoldingsParser()
    parser.feed(raw.decode('utf-8-sig'))
    parser.close()
    expected = ['Stock Code', 'Name', 'Shareholding in CCASS',
                '% of the total number of Issued Shares/Units']
    if (parser.table_count != 1 or not parser.closed or parser.active
            or parser.tbody_count != 1 or not parser.tbody_closed or parser.tbody_open
            or not parser.html_closed or parser.headers != [expected]
            or not 1 <= len(parser.rows) <= 20_000):
        raise ValueError('HK issuer-name table is missing, truncated or changed')
    dates = re.findall(r'Shareholding Date:\s*(\d{4}/\d{2}/\d{2})', ' '.join(parser.text))
    if len(dates) != 1:
        raise ValueError('HK holdings source date is absent or ambiguous')
    as_of = datetime.strptime(dates[0], '%Y/%m/%d').date().isoformat()
    result = {}
    classes = ['col-stock-code', 'col-stock-name', 'col-shareholding', 'col-shareholding-percent']
    for index, row in enumerate(parser.rows, 1):
        if len(row) != 4 or any(c['kind'] != 'td' or c['bodies'] != 1 or classes[i] not in c['class'] for i,c in enumerate(row)):
            raise ValueError('HK issuer-name row fields changed')
        code, name, shares, percent = [''.join(c['text']).strip() for c in row]
        if (not re.fullmatch(r'[0-9]{1,5}', code) or not 0 < int(code) <= 99999
                or not re.fullmatch(r'[0-9]{1,3}(?:,[0-9]{3})*|[0-9]+', shares)
                or (percent and not re.fullmatch(r'(?:[0-9]{1,2}(?:\.[0-9]+)?|100(?:\.0+)?)%', percent))):
            raise ValueError('HK issuer-name row identity/numeric field invalid')
        ticker = code.zfill(5)
        if ticker in result:
            raise ValueError('Duplicate HK source security code')
        sealed_names._text(name, english=True)
        # Preserve source text. A security-class qualifier cannot be silently
        # removed to invent a legal entity or normalized sponsor alias.
        eligible = not re.search(r'\bTRADED (?:SHARES|UNITS)\b|\bCLASS [A-Z]\b', name, re.I)
        result[ticker] = {'ticker': ticker, 'english_name': name,
            'source_locator': f'#mutualmarket-result tbody tr[{index}] td.col-stock-name',
            'query_seed_eligible': eligible}
    return {'schema_version': 1, 'source': 'HKEX_SOUTHBOUND', 'source_url': SOUTHBOUND_URL,
        'content_as_of': as_of, 'row_count': len(result), 'rows': result,
        'scope': 'observed_ccass_southbound_holdings_subset_names_only'}


def _capture_metadata(raw, metadata):
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_HTML_BYTES:
        raise ValueError('HK issuer-name HTML is empty or exceeds 4 MB')
    if (not isinstance(metadata, dict) or metadata.get('url') != SOUTHBOUND_URL
            or metadata.get('method') != 'GET' or type(metadata.get('status')) is not int
            or metadata['status'] != 200 or type(metadata.get('bytes')) is not int
            or metadata['bytes'] != len(raw) or metadata.get('sha256') != sealed_names._sha(raw)):
        raise ValueError('HK issuer-name capture must bind exact HTTPS URL, GET/200, bytes and SHA')
    retrieved = sealed_names._stamp(metadata.get('retrieved_at'))
    current = datetime.now(timezone.utc)
    age = (current - retrieved).total_seconds()
    if not -300 <= age <= 7 * 86400:
        raise ValueError('HK issuer-name capture is stale or future-dated')
    parsed = parse_southbound(raw)
    content_date = date.fromisoformat(parsed['content_as_of'])
    if not 0 <= (retrieved.date() - content_date).days <= 7:
        raise ValueError('HK holdings content date is stale or after its retrieval date')
    return parsed


def _name_schema(market):
    if Path(market.state).resolve().is_relative_to('/Volumes'):
        raise ValueError('HK name receipt SQLite must be local')
    with market.db() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS hk_issuer_name_captures (
          id TEXT PRIMARY KEY, created TEXT NOT NULL, payload_json TEXT NOT NULL,
          payload_sha256 TEXT NOT NULL UNIQUE);
        CREATE TABLE IF NOT EXISTS hk_issuer_name_receipts (
          id TEXT PRIMARY KEY, created TEXT NOT NULL, candidate_id TEXT NOT NULL,
          candidate_hash TEXT NOT NULL, capture_id TEXT NOT NULL,
          payload_json TEXT NOT NULL, payload_sha256 TEXT NOT NULL UNIQUE);
        CREATE INDEX IF NOT EXISTS hk_name_receipt_candidate
          ON hk_issuer_name_receipts(candidate_id,candidate_hash);
        ''')
        # A BEFORE INSERT conflict guard is needed because INSERT OR REPLACE
        # bypasses delete triggers with SQLite's default recursive_triggers=OFF.
        for table in ('hk_issuer_name_captures', 'hk_issuer_name_receipts'):
            for action in ('UPDATE', 'DELETE'):
                db.execute(f'''CREATE TRIGGER IF NOT EXISTS {table}_no_{action.lower()}
                  BEFORE {action} ON {table} BEGIN
                  SELECT RAISE(ABORT,'HK issuer names are append-only'); END''')
            db.execute(f'''CREATE TRIGGER IF NOT EXISTS {table}_no_replace
              BEFORE INSERT ON {table} WHEN EXISTS
              (SELECT 1 FROM {table} WHERE id=NEW.id OR payload_sha256=NEW.payload_sha256)
              BEGIN SELECT RAISE(ABORT,'HK issuer names are append-only'); END''')


def _folder(market, capture_id):
    if not isinstance(capture_id, str) or not re.fullmatch(r'[a-f0-9]{32}', capture_id):
        raise ValueError('Invalid HK name capture identity')
    folder = Path(market.root) / 'hk_issuer_names' / 'captures' / capture_id
    if folder.resolve() != folder.absolute():
        raise ValueError('HK name archive namespace contains a symlink')
    return folder


def _read_file(folder, name, limit):
    path = folder / name
    if path.resolve() != path.absolute() or path.is_symlink() or not path.is_file():
        raise ValueError('HK name archive missing or aliased')
    with path.open('rb') as file:
        raw = file.read(limit + 1)
    if not 0 < len(raw) <= limit:
        raise ValueError('HK name archive is empty or oversized')
    return raw


def _load_capture(market, row):
    payload = row['payload_json']
    if not isinstance(payload, str) or hashlib.sha256(payload.encode()).hexdigest() != row['payload_sha256']:
        raise ValueError('HK source capture ledger digest mismatch')
    proof = sealed_names._json(payload)
    if (proof.get('schema_version') != VERSION or proof.get('capture_id') != row['id']
            or proof.get('capture_reference') != f'hk_issuer_names/captures/{row["id"]}/source.html'):
        raise ValueError('HK source capture ledger identity/version mismatch')
    folder = _folder(market, row['id'])
    raw = _read_file(folder, 'source.html', MAX_HTML_BYTES)
    metadata_raw = _read_file(folder, 'metadata.json', 64_000)
    metadata = sealed_names._json(metadata_raw)
    if (proof.get('metadata_sha256') != sealed_names._sha(metadata_raw)
            or proof.get('raw_sha256') != sealed_names._sha(raw)
            or proof.get('raw_bytes') != len(raw)):
        raise ValueError('HK source original differs from its frozen ledger')
    parsed = _capture_metadata(raw, metadata)
    if (proof.get('row_count') != parsed['row_count'] or proof.get('content_as_of') != parsed['content_as_of']
            or proof.get('rows_sha256') != sealed_names._sha(sealed_names._serial(parsed['rows']).encode())
            or proof.get('source_url') != SOUTHBOUND_URL or proof.get('retrieved_at') != metadata['retrieved_at']):
        raise ValueError('HK source table replay differs from its frozen ledger')
    return proof, parsed


def _name_proof(candidate, listing, directory, capture, source_row):
    if (candidate['market'] != 'HK' or candidate['id'] != 'HK:' + candidate['ticker']
            or not isinstance(candidate['content_hash'], str)
            or not re.fullmatch(r'[a-f0-9]{64}', candidate['content_hash'])
            or candidate['review_status'] == 'rejected' or listing['market'] != 'HK'
            or listing['source'] != 'HK' or listing['exchange'] != 'HKEX'
            or candidate['ticker'] != listing['ticker'] or candidate['name'] != listing['name']
            or not source_row['query_seed_eligible']):
        raise ValueError('HK name does not bind a current unrejected exact security identity')
    return {'schema_version': VERSION, 'candidate_id': candidate['id'],
        'candidate_hash': candidate['content_hash'], 'market': 'HK', 'ticker': candidate['ticker'],
        'listing_name': listing['name'], 'english_name': source_row['english_name'],
        'source': 'HKEX_SOUTHBOUND', 'source_url': SOUTHBOUND_URL,
        'name_kind': 'hkex_reported_english_issuer_name',
        'source_locator': source_row['source_locator'], 'capture_id': capture['capture_id'],
        'source_run_id': capture['capture_id'], 'raw_sha256': capture['raw_sha256'],
        'raw_bytes': capture['raw_bytes'], 'retrieved_at': capture['retrieved_at'],
        'content_as_of': capture['content_as_of'], 'capture_reference': capture['capture_reference'],
        'directory_run_id': directory['source_run_id'], 'directory_rows_sha256': directory['rows_sha256'],
        'directory_raw_sha256': directory['raw_sha256'], 'directory_date_status': directory['date_status'],
        'scope': 'exact_hk_security_to_official_english_name_query_seed_only',
        'registry_alias_proven': False, 'human_sign_off': False,
        'drug_ownership_verified': False, 'drug_developer_status_verified': False,
        'full_pipeline_verified': False}


def seal_southbound(market, raw, metadata):
    """Seal already fetched public HTML and name receipts; performs no HTTP.

    This writes only independent HK name tables and archive files. It does not
    change candidate classification/name, companies, aliases, trials, or queues.
    A future date in the securities workbook remains visible on the receipt and
    must be independently resolved before any future automated identity review.
    """
    parsed = _capture_metadata(raw, metadata)
    _name_schema(market)
    meta_raw = sealed_names._serial(metadata).encode()
    # Identical original bytes and metadata have one recoverable capture ID.
    cid = sealed_names._sha(VERSION.encode() + raw + meta_raw)[:32]
    folder = _folder(market, cid)
    folder.mkdir(parents=True, exist_ok=True)
    for name, value in (('source.html', raw), ('metadata.json', meta_raw)):
        try:
            with (folder / name).open('xb') as file:
                file.write(value)
        except FileExistsError:
            if _read_file(folder, name, MAX_HTML_BYTES if name == 'source.html' else 64_000) != value:
                raise ValueError('Existing immutable HK source capture differs')
    capture = {'schema_version': VERSION, 'capture_id': cid, 'source_url': SOUTHBOUND_URL,
        'raw_sha256': sealed_names._sha(raw), 'raw_bytes': len(raw),
        'metadata_sha256': sealed_names._sha(meta_raw), 'retrieved_at': metadata['retrieved_at'],
        'content_as_of': parsed['content_as_of'], 'row_count': parsed['row_count'],
        'rows_sha256': sealed_names._sha(sealed_names._serial(parsed['rows']).encode()),
        'capture_reference': f'hk_issuer_names/captures/{cid}/source.html'}
    text = sealed_names._serial(capture)
    added = 0
    with market.db() as db:
        db.execute('BEGIN IMMEDIATE')
        directory = _directory(market, db)
        prior = db.execute('SELECT * FROM hk_issuer_name_captures WHERE id=?', (cid,)).fetchone()
        if prior:
            if prior['payload_json'] != text or prior['payload_sha256'] != sealed_names._sha(text.encode()):
                raise ValueError('Existing immutable HK capture ledger differs')
            _load_capture(market, prior)
        else:
            db.execute('INSERT INTO hk_issuer_name_captures VALUES (?,?,?,?)',
                       (cid, datetime.now(timezone.utc).isoformat(), text, sealed_names._sha(text.encode())))
        listings = {r['ticker']: dict(r) for r in db.execute("SELECT * FROM listings WHERE source='HK'")}
        for candidate in db.execute("SELECT * FROM issuer_candidates WHERE market='HK'").fetchall():
            candidate = dict(candidate)
            row = parsed['rows'].get(candidate['ticker'])
            listing = listings.get(candidate['ticker'])
            if not row or not listing:
                continue
            try:
                proof = _name_proof(candidate, listing, directory, capture, row)
            except ValueError:
                continue
            payload = sealed_names._serial(proof)
            if db.execute('SELECT 1 FROM hk_issuer_name_receipts WHERE payload_sha256=?',
                          (sealed_names._sha(payload.encode()),)).fetchone():
                continue
            db.execute('INSERT INTO hk_issuer_name_receipts VALUES (?,?,?,?,?,?,?)',
                (uuid.uuid4().hex, datetime.now(timezone.utc).isoformat(), candidate['id'],
                 candidate['content_hash'], cid, payload, sealed_names._sha(payload.encode())))
            added += 1
    return {'status': 'sealed_query_seeds_only', 'capture_id': cid, 'added': added,
        'source_row_count': parsed['row_count'], 'directory_date_status': directory['date_status'],
        'human_sign_off': False, 'companies_added': 0}


def query_evidences(market, candidates, *, db=None):
    """Read-only batch replay, each source capture once; supplied names untrusted."""
    own = None
    try:
        candidates = [dict(r) for r in candidates]
        if not candidates:
            return {}
        if len(candidates) > 10_000:
            raise ValueError('HK candidate batch exceeds 10000')
        if db is None:
            own = sqlite3.connect(Path(market.path).resolve().as_uri() + '?mode=ro', uri=True)
            own.row_factory = sqlite3.Row
            own.execute('BEGIN')
            db = own
        directory = _directory(market, db)
        current = {r['id']: dict(r) for r in db.execute("SELECT * FROM issuer_candidates WHERE market='HK'")}
        requested = {r['id']: r for r in candidates if r.get('market') == 'HK'
            and r.get('id') in current and r.get('content_hash') == current[r['id']]['content_hash']
            and r.get('name') == current[r['id']]['name'] and r.get('ticker') == current[r['id']]['ticker']}
        listings = {r['ticker']: dict(r) for r in db.execute("SELECT * FROM listings WHERE source='HK'")}
        captures = {r['id']: dict(r) for r in db.execute('SELECT * FROM hk_issuer_name_captures')}
        checked = {}
        found = {}
        for receipt in db.execute('SELECT * FROM hk_issuer_name_receipts ORDER BY created,id'):
            candidate = requested.get(receipt['candidate_id'])
            if not candidate or receipt['candidate_hash'] != candidate['content_hash']:
                continue
            try:
                if sealed_names._sha(receipt['payload_json'].encode()) != receipt['payload_sha256']:
                    continue
                cap_id = receipt['capture_id']
                if cap_id not in checked:
                    checked[cap_id] = _load_capture(market, captures[cap_id])
                capture, parsed = checked[cap_id]
                expected = _name_proof(current[candidate['id']], listings[candidate['ticker']], directory,
                                       capture, parsed['rows'][candidate['ticker']])
                if sealed_names._json(receipt['payload_json']) != expected:
                    continue
                found.setdefault(candidate['id'], []).append(expected | {'receipt_id': receipt['id']})
            except _ERRORS:
                continue
        return {cid: rows[-1] for cid, rows in found.items()
                if len({r['english_name'] for r in rows}) == 1}
    except _ERRORS:
        return {}
    finally:
        if own is not None:
            own.close()


def query_evidence(market, candidate, *, db=None):
    try:
        row = dict(candidate)
        return query_evidences(market, [row], db=db).get(row.get('id'))
    except (ValueError, TypeError, KeyError):
        return None


def query_names(market, candidates, *, db=None):
    return {cid: proof['english_name'] for cid, proof in query_evidences(market, candidates, db=db).items()}
