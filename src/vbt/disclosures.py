"""Official pipeline disclosures, kept apart from trial-registry observations.

Only configured public HTTPS sources can be collected. Parsers are source-specific;
unsupported layouts retain an archived capture and an explicit parse_blocked state.
"""
from __future__ import annotations

import ast
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
from urllib.parse import urlsplit
import uuid

from .io import now

SOURCES = {
    'hengrui_pipeline': {
        'name': '恒瑞医药官方临床开发管线（英文）',
        'companies': ['A:600276', 'HK:01276'],
        'url': 'https://www.hengrui.com/en/pipeline.html',
        'parser': 'hengrui_javascript_literals_v1',
        'scope': 'Official clinical development chart; not all preclinical assets or territorial rights',
    },
    'pfizer_pipeline': {
        'name': 'Pfizer official pipeline', 'companies': ['US:PFE'],
        'url': 'https://www.pfizer.com/science/drug-product-pipeline',
        'parser': 'unsupported',
        'scope': 'Official pipeline page; structured extraction pending',
    },
    'innovent_interim_pipeline_2025': {
        'name': '信达生物官方 2025 中期业绩公告及管线图', 'companies': ['HK:01801'],
        'url': 'https://investor.innoventbio.com/media/1327/hkex-eps_20250827_11811743_0.pdf',
        'parser': 'unsupported_pdf',
        'scope': 'Historical interim disclosure, not a current complete pipeline',
    },
    'ascentage_annual_pipeline_2025': {
        'name': 'Ascentage 官方2025年报管线图（历史披露）',
        'companies': ['HK:06855'],
        'url': 'https://ir.ascentage.com/static-files/cc6eb0ea-f444-4765-9efb-bb117b570f7b',
        'parser': 'frozen_official_pdf_observations_v1',
        'intake_mode': 'offline_reviewed_bundle',
        'bundle_relative': 'examples/ascentage_pipeline_2025_observations.json',
        'scope': 'Historical selected annual-report chart, not current complete pipeline; scientific review pending',
    },
    'carsgen_annual_pipeline_2024': {
        'name': 'CARsgen 官方2024年报管线图（历史披露）',
        'companies': ['HK:02171'],
        'url': 'https://www.carsgen.com/media/3vsj00ph/2024-annual-report.pdf',
        'parser': 'frozen_official_pdf_observations_v1',
        'intake_mode': 'offline_reviewed_bundle',
        'bundle_relative': 'examples/carsgen_pipeline_2024_observations.json',
        'scope': 'Historical selected annual-report chart, not current complete pipeline; scientific review pending',
    },
}
NETWORK_URLS = frozenset(x['url'] for x in SOURCES.values()
                         if x.get('intake_mode') != 'offline_reviewed_bundle')
ALLOWED_HOSTS = frozenset(urlsplit(url).hostname for url in NETWORK_URLS)
MAX_BYTES = 20_000_000


def validate_url(url):
    """Reject arbitrary paths, credentials, redirects, ports, and unlisted hosts."""
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except (ValueError, TypeError):
        raise ValueError('Disclosure source URL rejected') from None
    if (parsed.scheme != 'https' or parsed.hostname not in ALLOWED_HOSTS
            or parsed.username or parsed.password or port not in (None, 443)
            or parsed.fragment or url not in NETWORK_URLS):
        raise ValueError('Disclosure source URL rejected')
    return parsed.hostname


def fetch(url):
    """System-trusted TLS to exact configured URLs; never follow a redirect.

    DNS may be managed by the user's network (including a corporate proxy).
    The caller cannot supply arbitrary hosts, paths, query strings or credentials.
    """
    validate_url(url)
    with tempfile.TemporaryDirectory(prefix='vbt-disclosure-fetch-') as folder:
        body = Path(folder) / 'body'
        headers = Path(folder) / 'headers'
        args = ['curl', '--disable', '--silent', '--show-error', '--max-time', '45',
                '--max-filesize', str(MAX_BYTES), '--proto', '=https',
                '--user-agent', 'EmerisBiotechResearch/0.3',
                '--dump-header', str(headers), '--output', str(body), '--write-out', '%{http_code}', url]
        result = subprocess.run(args, capture_output=True, timeout=55,
                                env={k: v for k, v in os.environ.items() if k in ('PATH', 'HOME', 'SSL_CERT_FILE', 'CURL_CA_BUNDLE', 'HTTPS_PROXY', 'HTTP_PROXY', 'NO_PROXY')})
        if result.returncode:
            raise OSError('Disclosure fetch failed: ' + result.stderr.decode(errors='replace')[-200:])
        status = int(result.stdout.decode().strip())
        raw = body.read_bytes()
        if len(raw) > MAX_BYTES:
            raise ValueError('Disclosure response exceeds 20 MB')
        # Do not follow redirects, including redirects within the allowlist.
        if not 200 <= status < 300:
            raise OSError(f'Disclosure HTTP {status}; redirects and access challenges are not bypassed')
        media = re.findall(r'^content-type:\s*([^\r\n]+)', headers.read_text(errors='replace'), re.I | re.M)
        return {'raw': raw, 'url': url, 'status': status, 'content_type': media[-1] if media else 'unknown'}


class ParseBlocked(ValueError):
    pass


class LiteralReader:
    """A small JS data-literal reader; functions, property access and eval rejected."""
    def __init__(self, text, offset):
        self.text, self.pos = text, offset
        self.tokens = 0

    def skip(self):
        while self.pos < len(self.text):
            if self.text[self.pos].isspace():
                self.pos += 1
            elif self.text.startswith('//', self.pos):
                end = self.text.find('\n', self.pos + 2)
                self.pos = len(self.text) if end < 0 else end + 1
            elif self.text.startswith('/*', self.pos):
                end = self.text.find('*/', self.pos + 2)
                if end < 0:
                    raise ParseBlocked('Unclosed data comment')
                self.pos = end + 2
            else:
                break

    def value(self, depth=0):
        self.tokens += 1
        if depth > 10 or self.tokens > 100_000:
            raise ParseBlocked('Data literal is too complex')
        self.skip()
        ch = self.text[self.pos:self.pos + 1]
        if ch in ('"', "'"):
            start = self.pos
            self.pos += 1
            while self.pos < len(self.text):
                if self.text[self.pos] == '\\':
                    self.pos += 2
                elif self.text[self.pos] == ch:
                    self.pos += 1
                    try:
                        result = ast.literal_eval(self.text[start:self.pos])
                    except (ValueError, SyntaxError):
                        raise ParseBlocked('Unsupported string literal') from None
                    if not isinstance(result, str):
                        raise ParseBlocked('Expected string literal')
                    return result
                else:
                    self.pos += 1
            raise ParseBlocked('Unclosed string literal')
        if ch not in ('[', '{'):
            raise ParseBlocked('Only string, array and object data are supported')
        self.pos += 1
        result = [] if ch == '[' else {}
        closing = ']' if ch == '[' else '}'
        self.skip()
        while self.text[self.pos:self.pos + 1] != closing:
            if ch == '{':
                self.skip()
                key_match = re.match(r'[A-Za-z_][A-Za-z_0-9]*', self.text[self.pos:])
                if not key_match:
                    raise ParseBlocked('Unsupported object key')
                key = key_match[0]
                self.pos += len(key)
                self.skip()
                if self.text[self.pos:self.pos + 1] != ':' or key in result:
                    raise ParseBlocked('Invalid or repeated object key')
                self.pos += 1
                result[key] = self.value(depth + 1)
            else:
                result.append(self.value(depth + 1))
            self.skip()
            if self.text[self.pos:self.pos + 1] == ',':
                self.pos += 1
                self.skip()
            elif self.text[self.pos:self.pos + 1] != closing:
                raise ParseBlocked('Unsupported expression in data literal')
        self.pos += 1
        return result


class _DisclosureDocument(HTMLParser):
    """Separate inline scripts and visible text without rendering or executing."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.scripts, self.visible, self.stack = [], [], []
        self.current, self.script_index = None, 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'script':
            self.script_index += 1
            if self.current is not None:
                raise ParseBlocked('Nested script element')
            self.current = {'index': self.script_index, 'line': self.getpos()[0], 'text': '',
                            'usable': not attrs.get('src') and not any(tag in ('template','noscript') for tag, hidden in self.stack) and attrs.get('type', '').lower() in ('', 'text/javascript', 'application/javascript')}
        if tag not in ('area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'):
            hidden = ('hidden' in attrs or attrs.get('aria-hidden') == 'true'
                      or bool(re.search(r'(?:display\s*:\s*none|visibility\s*:\s*hidden)', attrs.get('style', ''), re.I))
                      or bool({'hidden','undis'} & set(attrs.get('class', '').split())))
            self.stack.append((tag, hidden))

    def handle_endtag(self, tag):
        if tag == 'script' and self.current is not None:
            if self.current['usable']:
                self.scripts.append(self.current)
            self.current = None
        indices = [index for index, (open_tag, hidden) in enumerate(self.stack) if open_tag == tag]
        if indices:
            self.stack = self.stack[:indices[-1]]

    def handle_data(self, data):
        if self.current is not None:
            self.current['text'] += data
        elif not any(hidden or tag in ('style','template','noscript') for tag, hidden in self.stack):
            self.visible.append(data)


def _string_end(text, pos, quote):
    pos += 1
    while pos < len(text):
        if text[pos] == '\\':
            pos += 2
        elif text[pos] == quote:
            return pos + 1
        else:
            pos += 1
    raise ParseBlocked('Unclosed script string')


def _template_end(text, pos):
    pos += 1
    while pos < len(text):
        if text[pos] == '\\':
            pos += 2
        elif text[pos] == '`':
            return pos + 1
        elif text.startswith('${', pos):
            pos, depth = pos + 2, 1
            while pos < len(text) and depth:
                if text[pos] in ('"', "'"):
                    pos = _string_end(text, pos, text[pos])
                elif text[pos] == '`':
                    pos = _template_end(text, pos)
                elif text.startswith('//', pos):
                    end = text.find('\n', pos + 2)
                    pos = len(text) if end < 0 else end + 1
                elif text.startswith('/*', pos):
                    end = text.find('*/', pos + 2)
                    if end < 0:raise ParseBlocked('Unclosed script comment')
                    pos = end + 2
                else:
                    if text[pos] == '{':depth += 1
                    elif text[pos] == '}':depth -= 1
                    pos += 1
            if depth:raise ParseBlocked('Unclosed template expression')
        else:
            pos += 1
    raise ParseBlocked('Unclosed script template')


def _script_tokens(text):
    """Lex outside comments and strings; template contents cannot declare data."""
    tokens, pos = [], 0
    while pos < len(text):
        ch = text[pos]
        if ch.isspace():
            pos += 1
        elif text.startswith('//', pos):
            end = text.find('\n', pos + 2)
            pos = len(text) if end < 0 else end + 1
        elif text.startswith('/*', pos):
            end = text.find('*/', pos + 2)
            if end < 0:raise ParseBlocked('Unclosed script comment')
            pos = end + 2
        elif ch in ('"', "'"):
            end = _string_end(text, pos, ch)
            tokens.append(('string', text[pos:end], pos, end))
            pos = end
        elif ch == '`':
            end = _template_end(text, pos)
            tokens.append(('template', '', pos, end))
            pos = end
        else:
            identifier = re.match(r'[A-Za-z_$][A-Za-z_$0-9]*', text[pos:])
            end = pos + (len(identifier[0]) if identifier else 1)
            tokens.append(('identifier' if identifier else 'punctuation', text[pos:end], pos, end))
            pos = end
    return tokens


def parse_hengrui(raw):
    text = raw.decode('utf-8', errors='strict')
    document = _DisclosureDocument()
    document.feed(text)
    document.close()
    if document.current is not None:
        raise ParseBlocked('Unclosed official script block')
    visible = ' '.join(document.visible)
    if 'Clinical Development Pipeline' not in visible:
        raise ParseBlocked('Official clinical chart heading missing')
    # Four named declarations must occur as code in real inline JS scripts. HTML
    # comments, JS comments, quoted text and template text cannot supply them.
    declarations, bindings = {}, set()
    for script in document.scripts:
        tokens = _script_tokens(script['text'])
        depth = 0
        for index, token in enumerate(tokens):
            top_level = depth == 0
            if token[0] == 'punctuation' and token[1] in ('{','['):depth += 1
            elif token[0] == 'punctuation' and token[1] in ('}',']'):depth -= 1
            if top_level and token[1] == 'const' and token[0] == 'identifier' and index + 2 < len(tokens):
                name, equals = tokens[index + 1], tokens[index + 2]
                if name[0] == 'identifier' and name[1] in ('dataIII','dataNDA','dataII','dataI') and equals[1] == '=':
                    declarations.setdefault(name[1], []).append((script, equals[3]))
            if top_level and token[1] == 'renderGrid' and (not index or tokens[index - 1][1] != '.') and index + 5 < len(tokens):
                args = tokens[index + 1:index + 6]
                if args[0][1] == '(' and args[1][0] == 'string' and args[2][1] == ',' and args[3][0] == 'identifier' and args[4][1] == ')':
                    try:grid = ast.literal_eval(args[1][1])
                    except (SyntaxError,ValueError):continue
                    bindings.add((grid, args[3][1]))
    arrays = [('dataIII', 'Phase III', 'grid-iii'), ('dataNDA', 'NDA/BLA', 'grid-nda'),
              ('dataII', 'Phase II', 'grid-ii'), ('dataI', 'Phase I', 'grid-i')]
    rows = []
    for name, phase, grid in arrays:
        found = declarations.get(name, [])
        if len(found) != 1 or (grid, name) not in bindings:
            raise ParseBlocked('Chart array or rendering binding changed: ' + name)
        script, offset = found[0]
        reader = LiteralReader(script['text'], offset)
        columns = reader.value()
        reader.skip()
        if script['text'][reader.pos:reader.pos + 1] != ';':
            raise ParseBlocked('Chart declaration requires a literal terminated by a semicolon: ' + name)
        if not isinstance(columns, list) or not columns:
            raise ParseBlocked('Chart columns missing: ' + name)
        for col, records in enumerate(columns):
            if not isinstance(records, list):
                raise ParseBlocked('Chart column schema changed')
            for idx, row in enumerate(records):
                if (not isinstance(row, dict) or set(row) != {'name', 'target', 'indications', 'type'}
                        or not all(isinstance(row[x], str) for x in ('name', 'target', 'type'))
                        or not row['name'].strip() or not isinstance(row['indications'], list)
                        or not row['indications'] or any(not isinstance(x, str) or not x.strip() for x in row['indications'])):
                    raise ParseBlocked('Chart record schema changed: ' + name)
                descriptor = row['target'].strip()
                target = descriptor if descriptor not in ('', '-', '—') else None
                modality = None
                if descriptor.endswith(' ADC'):
                    modality, target = 'ADC', descriptor[:-4].strip()
                for ind_index, indication in enumerate(row['indications']):
                    rows.append({'drug_code': row['name'] if re.fullmatch(r'(?:HRS|SHR)-?[A-Z0-9]+', row['name']) else None,
                                 'drug_name': row['name'], 'target': target, 'modality': modality,
                                 'indication': indication, 'phase': phase, 'therapeutic_area': row['type'],
                                 'source_locator': f'script const {name}[{col}][{idx}].indications[{ind_index}]',
                                 'source_script_index': script['index'],
                                 'source_line': script['line'] + script['text'][:offset].count('\n'),
                                 'source_descriptor': descriptor,
                                 'missing_fields': [x for x, value in [('drug_code', row['name'] if re.fullmatch(r'(?:HRS|SHR)-?[A-Z0-9]+', row['name']) else None), ('target', target), ('modality', modality)] if value is None]})
    if not rows:
        raise ParseBlocked('Chart yielded no observations')
    dates = list(re.finditer(r'\*By\s+([A-Za-z]+),\s*(\d{4})', visible))
    date = dates[0] if len(dates) == 1 else None
    month_map = {'Jan': '01', 'Feb': '02', 'Mar': '03', 'Apr': '04', 'May': '05', 'Jun': '06', 'Jul': '07', 'Aug': '08', 'Sep': '09', 'Oct': '10', 'Nov': '11', 'Dec': '12'}
    asof = date[2] + '-' + month_map[date[1][:3]] if date and date[1][:3] in month_map else None
    return rows, {'published_at': None, 'as_of': asof, 'as_of_precision': 'month' if asof else 'unknown',
                  'date_locator': date[0] if date else None,
                  'scope': 'Four explicit current clinical development chart arrays only; not the earlier hidden chart, preclinical portfolio, drug rights, or marketed portfolio'}


def _schema(market):
    with market.db() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS disclosure_captures (
          id TEXT PRIMARY KEY, source_id TEXT, company TEXT, started TEXT, finished TEXT,
          status TEXT, url TEXT, sha256 TEXT, bytes INTEGER, archive_path TEXT,
          content_type TEXT, published_at TEXT, as_of TEXT, metadata TEXT, count INTEGER, error TEXT);
        CREATE INDEX IF NOT EXISTS disclosure_capture_source ON disclosure_captures(source_id,started);
        CREATE TABLE IF NOT EXISTS disclosure_observations (
          id TEXT PRIMARY KEY, capture_id TEXT, source_id TEXT, company TEXT,
          drug_code TEXT, drug_name TEXT, modality TEXT, target TEXT, indication TEXT,
          phase TEXT, source_locator TEXT, data TEXT,
          UNIQUE(capture_id, source_locator));
        ''')
        if 'lease_owner' not in {r[1] for r in db.execute('PRAGMA table_info(disclosure_captures)')}:
            db.execute('ALTER TABLE disclosure_captures ADD COLUMN lease_owner TEXT')
        db.execute("UPDATE disclosure_captures SET status='interrupted',finished=?,error='Collection lease expired; retry required' WHERE status='running' AND NOT EXISTS (SELECT 1 FROM leases WHERE leases.key='disclosure:' || disclosure_captures.source_id AND leases.owner=disclosure_captures.lease_owner AND leases.until>?)", (now(), time.time()))


def initialize_storage(market):
    """Additive capture storage shared by network and frozen offline adapters."""
    _schema(market)


def capture_integrity(market, capture):
    """Check retained bytes; execution success alone is not evidence integrity."""
    if SOURCES.get(capture.get('source_id'), {}).get('intake_mode') == 'offline_reviewed_bundle':
        from .official_disclosure_intake import verify_capture
        return verify_capture(market, capture['id'])
    result = {'integrity_status': 'blocked', 'errors': []}
    try:
        if capture.get('status') != 'succeeded':
            raise ValueError('Capture execution did not publish a successful observation')
        if capture.get('source_id') not in SOURCES or capture.get('url') != SOURCES[capture['source_id']]['url']:
            raise ValueError('Capture source binding is not the configured official URL')
        relative = Path(capture.get('archive_path') or '')
        expected = Path('disclosures/captures') / capture['id'] / 'source.bin'
        if relative != expected or relative.is_absolute() or not re.fullmatch('[a-f0-9]{32}', capture['id']):
            raise ValueError('Capture archive path is outside its source namespace')
        root = market.root.resolve()
        path = root / relative
        if path.resolve() != path.absolute() or not path.is_file():
            raise ValueError('Capture archive is missing or uses a symbolic link')
        size = path.stat().st_size
        if type(capture.get('bytes')) is not int or not 0 < size == capture['bytes'] <= MAX_BYTES:
            raise ValueError('Capture archive size does not match its recorded source')
        if not re.fullmatch('[a-f0-9]{64}', str(capture.get('sha256', ''))):
            raise ValueError('Capture has no valid recorded SHA-256')
        from .io import digest
        if digest(path) != capture['sha256']:
            raise ValueError('Capture archive SHA-256 no longer matches its recorded source')
        source = SOURCES[capture['source_id']]
        if source['parser'] != 'hengrui_javascript_literals_v1':
            raise ValueError('Capture has no independently repeatable structured parser')
        expected_rows, expected_metadata = parse_hengrui(path.read_bytes())
        metadata = {'parser': source['parser'], 'scope': source['scope'], 'published_at': None,
                    'as_of': None, 'as_of_precision': 'unknown', 'date_locator': None,
                    **expected_metadata}
        if (type(capture.get('count')) is not int or capture['count'] != len(expected_rows)
                or capture.get('as_of') != expected_metadata.get('as_of')
                or capture.get('published_at') != expected_metadata.get('published_at')
                or json.loads(capture.get('metadata')) != metadata):
            raise ValueError('Capture count/date no longer agrees with its archived official source')
        expected = {row['source_locator']: row for row in expected_rows}
        fields = ('drug_code', 'drug_name', 'modality', 'target', 'indication', 'phase', 'source_locator')
        with market.db() as db:
            observed = [dict(r) for r in db.execute(
                'SELECT * FROM disclosure_observations WHERE capture_id=?', (capture['id'],))]
        if len(observed) != len(expected_rows):
            raise ValueError('Published observation count differs from the retained source')
        for row in observed:
            original = expected.get(row['source_locator'])
            if (original is None or any(row[key] != original[key] for key in fields)
                    or row['source_id'] != capture['source_id'] or row['company'] != source['companies'][0]
                    or json.loads(row['data']) != original):
                raise ValueError('Published observation no longer reproduces from its archived source')
        result['integrity_status'] = 'verified'
    except (KeyError, TypeError, ValueError, OSError) as exc:
        result['errors'].append('Retained official source is unavailable' if isinstance(exc, OSError) else str(exc))
    return result


def sources(market=None):
    result = []
    latest = {}
    if market is not None:
        _schema(market)
        with market.db() as db:
            for source_id in SOURCES:
                capture = db.execute('SELECT * FROM disclosure_captures WHERE source_id=? ORDER BY started DESC,id DESC LIMIT 1', (source_id,)).fetchone()
                if capture:
                    latest[source_id] = dict(capture)
    for source_id, source in SOURCES.items():
        result.append({'id': source_id, **source, 'latest': latest.get(source_id),
                       'published_at': None, 'coverage': 'configured_source_not_complete_portfolio'})
    return result


def captures(market, company='', limit=200, offset=0):
    _schema(market)
    if not 1 <= limit <= 5000 or offset < 0:
        raise ValueError('Invalid disclosure pagination')
    ids = [key for key, source in SOURCES.items() if company in source['companies']] if company else []
    if company and not ids:
        return []
    clause, params = (' WHERE source_id IN (' + ','.join('?' for _ in ids) + ')', ids) if company else ('', [])
    # One capture is associated with a canonical security; companies[] in source
    # configuration preserves other listings without duplicating observations.
    with market.db() as db:
        return [dict(r) for r in db.execute('SELECT * FROM disclosure_captures' + clause + ' ORDER BY started DESC,id DESC LIMIT ? OFFSET ?', params + [limit, offset])]


def ingest(market, source_id, fetch=fetch, cancelled=lambda: False, on_start=lambda run_id: None):
    """Capture one configured source; failures never remove earlier observations."""
    if source_id not in SOURCES:
        raise ValueError('Unknown disclosure source')
    source = SOURCES[source_id]
    if source.get('intake_mode') == 'offline_reviewed_bundle':
        from .official_disclosure_intake import ingest_offline
        return ingest_offline(market, source_id, cancelled=cancelled, on_start=on_start)
    url = source['url']
    validate_url(url)
    _schema(market)
    lease_key = 'disclosure:' + source_id
    if not market.claim(lease_key):
        return None
    lease_token = market.lease_tokens()[lease_key]
    capture_id, started = uuid.uuid4().hex, now()
    canonical_company = source['companies'][0]
    metadata = {'parser': source['parser'], 'scope': source['scope'], 'published_at': None,
                'as_of': None, 'as_of_precision': 'unknown', 'date_locator': None}
    try:
        with market.db() as db:
            db.execute('INSERT INTO disclosure_captures(id,source_id,company,started,finished,status,url,sha256,bytes,archive_path,content_type,published_at,as_of,metadata,count,error,lease_owner) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       (capture_id, source_id, canonical_company, started, None, 'running', url, None, 0, None,
                        None, None, None, json.dumps(metadata, ensure_ascii=False), 0, '', lease_token))
    except Exception:
        market.release(lease_key, lease_token)
        raise
    status, error, rows = 'fetch_failed', '', []
    digest, relative, content_type, byte_count = None, None, None, 0
    try:
        on_start(capture_id)
        if cancelled():
            status = 'cancelled'
            raise InterruptedError('Disclosure cancelled before fetch')
        market.touch(lease_key, lease_token)
        response = fetch(url)
        if isinstance(response, bytes):
            raw, final_url, content_type, http_status = response, url, 'unknown', 200
        elif isinstance(response, dict):
            raw, final_url = response['raw'], response.get('url', url)
            content_type, http_status = response.get('content_type', 'unknown'), response.get('status', 200)
        else:
            raise ValueError('Unsupported fetch response')
        validate_url(final_url)
        if final_url != url or not isinstance(raw, bytes) or not 200 <= http_status < 300:
            raise ValueError('Disclosure response URL, body or HTTP status rejected')
        if not raw or len(raw) > MAX_BYTES:
            raise ValueError('Empty or oversized disclosure response')
        folder = market.root / 'disclosures' / 'captures' / capture_id
        folder.mkdir(parents=True, exist_ok=False)
        path = folder / 'source.bin'
        path.write_bytes(raw)
        digest, byte_count = hashlib.sha256(raw).hexdigest(), len(raw)
        relative = str(path.relative_to(market.root))
        provenance = {'source_id': source_id, 'companies': source['companies'], 'url': url,
                      'retrieved_at': now(), 'sha256': digest, 'bytes': byte_count,
                      'content_type': content_type, 'published_at': None,
                      'publication_date_status': 'unknown_until_source_explicitly_states_date',
                      **metadata, 'metadata': metadata}
        (folder / 'provenance.json').write_text(json.dumps(provenance, ensure_ascii=False, indent=2))
        if cancelled():
            status = 'cancelled'
            raise InterruptedError('Disclosure cancelled after capture; observations not published')
        status = 'parse_blocked'
        if source['parser'] == 'hengrui_javascript_literals_v1':
            if raw.lstrip().startswith(b'%PDF'):
                raise ParseBlocked('Expected official HTML, received PDF')
            rows, parsed_metadata = parse_hengrui(raw)
            metadata = {**metadata, **parsed_metadata}
        elif source['parser'] == 'unsupported_pdf':
            if not raw.lstrip().startswith(b'%PDF'):
                raise ParseBlocked('Expected official PDF; response may be an access challenge')
            raise ParseBlocked('PDF chart archived; layout-aware extraction and source-page verification required')
        else:
            raise ParseBlocked('Official page archived; source-specific structured parser not yet validated')
        status = 'succeeded'
    except Exception as exc:
        error = type(exc).__name__ + ': ' + str(exc)[:500]
    try:
      with market.db() as db:
        db.execute('BEGIN IMMEDIATE')
        current = db.execute('SELECT * FROM disclosure_captures WHERE id=?', (capture_id,)).fetchone()
        if current['status'] != 'running' or current['lease_owner'] != lease_token:
            return {**dict(current), 'run_id': capture_id}
        if not db.execute('SELECT 1 FROM leases WHERE key=? AND owner=? AND until>?', (lease_key, lease_token, time.time())).fetchone():
            status, error, rows = 'superseded', 'Collection lease lost; observations not published', []
        elif cancelled():
            status, error, rows = 'cancelled', 'Cancelled before publication; observations not published', []
        # All parsed observations and the capture completion become visible together.
        for row in rows if status == 'succeeded' else []:
            serial = json.dumps(row, ensure_ascii=False, sort_keys=True)
            db.execute('INSERT INTO disclosure_observations VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                       (uuid.uuid4().hex, capture_id, source_id, canonical_company,
                        row['drug_code'], row['drug_name'], row['modality'], row['target'], row['indication'],
                        row['phase'], row['source_locator'], serial))
        db.execute('UPDATE disclosure_captures SET finished=?,status=?,sha256=?,bytes=?,archive_path=?,content_type=?,published_at=?,as_of=?,metadata=?,count=?,error=? WHERE id=? AND status=\'running\' AND lease_owner=?',
                   (now(), status, digest, byte_count, relative, content_type, metadata.get('published_at'),
                    metadata.get('as_of'), json.dumps(metadata, ensure_ascii=False), len(rows) if status == 'succeeded' else 0,
                    error, capture_id, lease_token))
        result = dict(db.execute('SELECT * FROM disclosure_captures WHERE id=?', (capture_id,)).fetchone())
        result['run_id'] = capture_id
        if relative:
            provenance.update({**metadata, 'metadata': metadata})
            pending = folder / 'provenance.json.tmp'
            pending.write_text(json.dumps(provenance, ensure_ascii=False, indent=2))
            pending.replace(folder / 'provenance.json')
    finally:
        market.release(lease_key, lease_token)
    return result


def observations(market, company='', history=False, limit=2000, offset=0):
    """Latest successful chart per source, with visible last-attempt/staleness fields."""
    _schema(market)
    if not 1 <= limit <= 10000 or offset < 0:
        raise ValueError('Invalid disclosure pagination')
    source_ids = [key for key, value in SOURCES.items() if not company or company in value['companies']]
    result = []
    checked_captures = {}
    with market.db() as db:
        for source_id in source_ids:
            latest = db.execute('SELECT * FROM disclosure_captures WHERE source_id=? ORDER BY started DESC,id DESC LIMIT 1', (source_id,)).fetchone()
            successful = db.execute("SELECT * FROM disclosure_captures WHERE source_id=? AND status='succeeded' ORDER BY started DESC,id DESC LIMIT 1", (source_id,)).fetchone()
            if not successful:
                continue
            checked_captures[successful['id']] = capture_integrity(market, dict(successful))['integrity_status']
            if checked_captures[successful['id']] != 'verified':
                continue
            clause, args = ('source_id=?', [source_id]) if history else ('capture_id=?', [successful['id']])
            for row in db.execute('SELECT * FROM disclosure_observations WHERE ' + clause + ' ORDER BY source_locator', args):
                item = dict(row)
                cap = db.execute('SELECT * FROM disclosure_captures WHERE id=?', (item['capture_id'],)).fetchone()
                if cap['id'] not in checked_captures:
                    checked_captures[cap['id']] = capture_integrity(market, dict(cap))['integrity_status']
                if checked_captures[cap['id']] != 'verified':
                    continue
                try:
                    metadata = json.loads(cap['metadata'])
                except (TypeError, ValueError):
                    continue
                if not isinstance(metadata, dict):
                    continue
                item.update({'url': cap['url'], 'sha256': cap['sha256'], 'retrieved_at': cap['finished'],
                             'published_at': cap['published_at'], 'as_of': cap['as_of'],
                             'metadata': metadata,
                             'companies': SOURCES[source_id]['companies'], 'latest_attempt_status': latest['status'],
                             'prior_capture_retained': latest['id'] != successful['id'],
                             'attribution': 'company-disclosed chart; ownership, territory and completeness not independently verified'})
                if metadata.get('historical_disclosure'):
                    item['retrieved_at'] = metadata.get('original_retrieved_at') or metadata.get('retrieved_at')
                    item['imported_at'] = metadata.get('imported_at')
                item['data'] = json.loads(item['data'])
                result.append(item)
    return result[offset:offset + limit]
