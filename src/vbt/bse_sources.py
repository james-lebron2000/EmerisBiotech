"""Bounded, fail-closed connector for the BSE public listed-stock directory.

The public endpoint and named schema were verified against all 18 pages on
2026-10-17 (351 listings).  Future snapshots require the same completeness checks.
No positional field mapping, JavaScript execution, challenge handling, or
unofficial mirror is used.  Transport failures never produce a partial snapshot.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timezone
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

DIRECTORY_URL = 'https://www.bse.cn/nq/listedcompany.html'
ENDPOINT = 'https://www.bse.cn/nqxxController/nqxxCnzq.do'
MAX_BYTES = 20_000_000
MAX_PAGES = 100
MAX_ROWS = 10_000
_FIELDS = {'typejb': ['T'], 'xxfcbj[]': ['2'], 'xxzqdm': [''],
           'sortfield': ['xxzqdm'], 'sorttype': ['asc']}


def request_body(page: int) -> bytes:
    if type(page) is not int or not 0 <= page < MAX_PAGES:
        raise ValueError('BSE page outside fixed request bounds')
    return urlencode({'page': str(page), **{k: v[0] for k, v in _FIELDS.items()}}).encode('ascii')


def validate_request(url: str, data: bytes) -> None:
    if url != ENDPOINT or not isinstance(data, bytes) or len(data) > 300:
        raise ValueError('BSE request outside fixed public endpoint')
    try:
        fields = parse_qs(data.decode('ascii'), keep_blank_values=True, strict_parsing=True)
        page = fields.pop('page')
        valid = len(page) == 1 and page[0].isdigit() and 0 <= int(page[0]) < MAX_PAGES
    except (UnicodeError, ValueError, KeyError):
        raise ValueError('Invalid BSE request body') from None
    if not valid or fields != _FIELDS:
        raise ValueError('BSE query outside listed-stock directory scope')


class BSEFetchError(OSError):
    """HTTP failure retains its bounded public response for source auditing."""
    def __init__(self, status: int, raw: bytes):
        self.status = status
        self.raw = raw
        super().__init__(f'BSE public directory HTTP {status}; previous snapshot must be retained')


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch_page(url: str, *, data: bytes) -> bytes:
    """POST only the fixed directory form, with TLS and no redirects or secrets."""
    validate_request(url, data)
    req = Request(url, data=data, method='POST', headers={
        'User-Agent': 'EmerisBiotechResearch/0.2',
        'Content-Type': 'application/x-www-form-urlencoded',
        'Accept': 'application/json, text/javascript',
        'Referer': DIRECTORY_URL,
    })
    try:
        with build_opener(_NoRedirect()).open(req, timeout=30) as response:
            raw = response.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise ValueError('BSE response exceeds 20 MB bound')
            if response.geturl() != ENDPOINT:
                raise ValueError('BSE response URL changed; redirects are not allowed')
            if response.getcode() != 200:
                raise BSEFetchError(response.getcode(),raw)
    except HTTPError as exc:
        raw = exc.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError('BSE response exceeds 20 MB bound') from None
        raise BSEFetchError(exc.code, raw) from None
    if len(raw) > MAX_BYTES:
        raise ValueError('BSE response exceeds 20 MB bound')
    return raw


_DEFAULT_FETCH = fetch_page

def _integer(value, label: str, minimum=0, maximum=MAX_ROWS) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f'BSE invalid {label}; current schema must be reviewed')
    return value


def _text(value, label: str, required=False, maximum=300) -> str:
    if value is None and not required:
        return ''
    if not isinstance(value, str) or len(value) > maximum or any(ord(c) < 32 for c in value):
        raise ValueError(f'BSE invalid {label}; current schema must be reviewed')
    text = value.strip()
    if required and not text:
        raise ValueError(f'BSE missing {label}')
    return text


def parse(raw: bytes) -> dict:
    """Read a JSON/JSONP Spring page without evaluating executable content.

    Missing named fields are rejected; field order is never used to infer names.
    Six-digit published codes are retained exactly, including legacy 4/8 series.
    """
    if not isinstance(raw, bytes) or len(raw) > MAX_BYTES:
        raise ValueError('BSE response is not bounded bytes')
    text = raw.decode('utf-8-sig').strip()
    if not text.startswith(('[', '{')):
        wrapped = re.fullmatch(r'[A-Za-z_$][A-Za-z0-9_$]*\s*\((.*)\)\s*;?', text, re.DOTALL)
        if not wrapped:
            raise ValueError('BSE response is neither JSON nor a single safe JSONP wrapper')
        text = wrapped.group(1).strip()
    try:
        parsed = json.loads(text)
    except (ValueError, RecursionError):
        raise ValueError('BSE invalid JSON response') from None
    if isinstance(parsed, list) and len(parsed) == 1:
        parsed = parsed[0]
    if not isinstance(parsed, dict):
        raise ValueError('BSE changed pagination envelope')
    required = {'content', 'totalPages', 'totalElements', 'size', 'number',
                'numberOfElements', 'firstPage', 'lastPage'}
    if not required.issubset(parsed):
        raise ValueError('BSE missing explicit pagination metadata; completeness unverified')
    total = _integer(parsed['totalElements'], 'totalElements', 1)
    pages = _integer(parsed['totalPages'], 'totalPages', 1, MAX_PAGES)
    size = _integer(parsed['size'], 'size', 1, 1000)
    page = _integer(parsed['number'], 'number', 0, MAX_PAGES - 1)
    count = _integer(parsed['numberOfElements'], 'numberOfElements', 1, size)
    content = parsed['content']
    if pages != math.ceil(total / size) or page >= pages or not isinstance(content, list):
        raise ValueError('BSE contradictory pagination metadata')
    if count != len(content) or count != min(size, total - page * size):
        raise ValueError('BSE truncated or overfull page; previous snapshot must be retained')
    if parsed['firstPage'] is not (page == 0):
        raise ValueError('BSE contradictory first-page flag')
    if parsed['lastPage'] is not (page == pages - 1):
        raise ValueError('BSE contradictory last-page flag')
    rows = []
    for record in content:
        if not isinstance(record, dict) or not {'xxzqdm', 'xxzqjc'}.issubset(record):
            raise ValueError('BSE missing named security code/name fields; no positional fallback')
        if record.get('xxfcbj') != '2' or record.get('xxzqjb') != 'T':
            raise ValueError('BSE row outside current listed-stock directory scope')
        code = _text(record['xxzqdm'], 'xxzqdm', required=True, maximum=6)
        if not re.fullmatch(r'(?:920[0-9]{3}|[48][0-9]{5})', code):
            raise ValueError('BSE unexpected published security code; no automatic code conversion')
        rows.append({'market': 'A', 'exchange': 'BSE', 'ticker': code,
                     'name': _text(record['xxzqjc'], 'xxzqjc', required=True),
                     'industry': _text(record.get('xxhyzl'), 'xxhyzl')})
    codes = [r['ticker'] for r in rows]
    if codes != sorted(set(codes)):
        raise ValueError('BSE duplicate or unsorted page')
    return {'rows': rows, 'total': total, 'page': page, 'page_size': size, 'total_pages': pages}


def fetch_universe(fetch=fetch_page, *, state_dir=None, cancelled=lambda: False, on_capture=lambda capture: None) -> dict:
    """Return a complete checked snapshot or raise; never publish partial rows.

    Injected fetch has the signature ``fetch(ENDPOINT, data=request_body(page))``.
    Default real POST traffic requires an explicit local ``state_dir`` and uses
    the shared platform host scheduler. Injected synthetic fetches skip waiting.
    ``on_capture`` runs before parsing (also for HTTP failures) so callers can
    archive partial/failure evidence without promoting it to listing state.
    """
    captures = []

    def read(page):
        if cancelled():
            raise InterruptedError('BSE capture cancelled; previous snapshot retained')
        body = request_body(page)
        if fetch is _DEFAULT_FETCH:
            if state_dir is None:
                raise ValueError('Real BSE capture requires a local state directory for shared source scheduling')
            from .source_rate import wait_slot
            wait_slot(state_dir, ENDPOINT, cancelled=cancelled)
        if cancelled():
            raise InterruptedError('BSE capture cancelled before POST; previous snapshot retained')
        error = None
        try:
            raw = fetch(ENDPOINT, data=body)
            status = 200
        except BSEFetchError as exc:
            raw, status, error = exc.raw, exc.status, exc
        if not isinstance(raw, bytes) or len(raw) > MAX_BYTES:
            raise ValueError('BSE fetch returned invalid or oversized bytes')
        capture = {'url': ENDPOINT, 'method': 'POST', 'request_body': body.decode('ascii'),
                   'raw': raw, 'status': status, 'page': page,
                   'retrieved_at': datetime.now(timezone.utc).isoformat(),
                   'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}
        captures.append(capture)
        on_capture(capture)
        if error:
            raise error
        if cancelled():
            raise InterruptedError('BSE capture cancelled; previous snapshot retained')
        parsed = parse(raw)
        if parsed['page'] != page:
            raise ValueError('BSE returned a different page; previous snapshot retained')
        return parsed

    first = read(0)
    rows = list(first['rows'])
    signature = (first['total'], first['page_size'], first['total_pages'])
    for page in range(1, first['total_pages']):
        result = read(page)
        if (result['total'], result['page_size'], result['total_pages']) != signature:
            raise ValueError('BSE directory changed during pagination; previous snapshot retained')
        rows.extend(result['rows'])
    codes = [r['ticker'] for r in rows]
    if len(rows) != first['total'] or codes != sorted(set(codes)):
        raise ValueError('BSE page overlap, missing rows or inconsistent ordering; previous snapshot retained')
    # A second first-page read detects common directory changes during collection.
    # It does not imply the remote endpoint offers a transactional snapshot.
    if read(0) != first:
        raise ValueError('BSE first page changed during collection; retry from a new run')
    if cancelled():
        raise InterruptedError('BSE publication cancelled; previous snapshot retained')
    return {'rows': rows, 'total': first['total'], 'captures': captures,
            'total_pages': first['total_pages'], 'page_size': first['page_size'],
            'source': 'BSE', 'source_url': DIRECTORY_URL,
            'scope': 'listed_stock_directory_fixed_official_query',
            'remote_transactional_snapshot': False}
