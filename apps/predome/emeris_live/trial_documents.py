"""Explicit, bounded collection of public registry protocol/SAP attachments.

Inventory projection is pure standard-library code. Registry document dates are
retained as metadata, never treated as historical public-availability evidence.
Network and PDF parsing occur only through the explicitly called functions.
"""
from datetime import datetime, timezone
import hashlib
from http.client import HTTPException
import json
import os
from pathlib import Path
import re
import ssl
import subprocess
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener


INVENTORY_VERSION = 'predome.registry-documents-1'
ACQUISITION_VERSION = 'predome.registry-document-acquisition-1'
PDF_VERSION = 'predome.pdf-pages-1'
MAX_REGISTRY_BYTES = 6 * 1024 * 1024
MAX_DOCUMENT_BYTES = 16 * 1024 * 1024
MAX_PDF_PAGES = 400
MAX_TEXT_BYTES = 2 * 1024 * 1024
NETWORK_TIMEOUT_SECONDS = 20
PDF_TIMEOUT_SECONDS = 60
_NCT = re.compile(r'NCT[0-9]{8}\Z', re.ASCII)
_FILENAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.pdf\Z', re.ASCII)


def _valid_nct(value):
    return type(value) is str and _NCT.fullmatch(value) is not None


def _safe_filename(value):
    return type(value) is str and _FILENAME.fullmatch(value) is not None and '..' not in value


def _empty_document():
    return dict.fromkeys(('filename', 'type_abbrev', 'label', 'document_date',
                         'upload_date', 'has_protocol', 'has_sap', 'has_icf',
                         'size_bytes', 'download_url'))


def document_inventory(study):
    """Project the actual attachment listing without inferring missing documents.

    Invalid rows retain their place. URLs are derived only from a valid registry
    identity and a safe literal listed filename, never an input URL or IPD pledge.
    Unknown booleans and dates remain unknown/original, respectively.
    """
    result = {'schema_version': INVENTORY_VERSION, 'nct_id': None,
              'listing_status': 'unreadable_listing', 'no_sap': None,
              'documents': [], 'warnings': []}

    def warn(category):
        if category not in result['warnings']:
            result['warnings'].append(category)

    if type(study) is not dict:
        warn('study_structure_invalid')
        return result
    protocol = study.get('protocolSection')
    identity = protocol.get('identificationModule') if type(protocol) is dict else None
    nct_id = identity.get('nctId') if type(identity) is dict else None
    if _valid_nct(nct_id):
        result['nct_id'] = nct_id
    else:
        warn('registry_identity_unavailable')
    section = study.get('documentSection')
    if 'documentSection' not in study:
        result['listing_status'] = 'not_listed_in_snapshot'
        warn('listing_not_present_in_snapshot')
        return result
    if type(section) is not dict:
        warn('listing_structure_invalid')
        return result
    if 'largeDocumentModule' not in section:
        result['listing_status'] = 'not_listed_in_snapshot'
        warn('listing_not_present_in_snapshot')
        return result
    module = section['largeDocumentModule']
    if type(module) is not dict:
        warn('listing_structure_invalid')
        return result
    if 'noSap' in module:
        if type(module['noSap']) is bool:
            result['no_sap'] = module['noSap']
        else:
            warn('no_sap_invalid')
    if 'largeDocs' not in module:
        result['listing_status'] = 'not_listed_in_snapshot'
        warn('listing_not_present_in_snapshot')
        return result
    rows = module['largeDocs']
    if type(rows) is not list:
        warn('listing_structure_invalid')
        return result
    result['listing_status'] = 'listed'
    invalid = result['nct_id'] is None or 'no_sap_invalid' in result['warnings']
    seen = set()
    for row in rows:
        item = _empty_document()
        result['documents'].append(item)
        if type(row) is not dict:
            warn('document_entry_invalid')
            invalid = True
            continue
        filename = row.get('filename')
        if _safe_filename(filename):
            item['filename'] = filename
            if filename in seen:
                warn('document_filename_duplicate')
                invalid = True
            seen.add(filename)
            if result['nct_id'] is not None:
                item['download_url'] = ('https://cdn.clinicaltrials.gov/large-docs/'
                    + nct_id[-2:] + '/' + nct_id + '/' + filename)
        else:
            warn('document_filename_unsafe')
            invalid = True
        for source, destination, limit in (
            ('typeAbbrev', 'type_abbrev', 80), ('label', 'label', 1024),
            ('date', 'document_date', 256), ('uploadDate', 'upload_date', 256),
        ):
            value = row.get(source)
            if value is None:
                continue
            if type(value) is str and len(value) <= limit:
                item[destination] = value
            else:
                warn('document_metadata_invalid')
                invalid = True
        for source, destination in (('hasProtocol', 'has_protocol'), ('hasSap', 'has_sap'), ('hasIcf', 'has_icf')):
            value = row.get(source)
            if value is None:
                continue
            if type(value) is bool:
                item[destination] = value
            else:
                warn('document_metadata_invalid')
                invalid = True
        value = row.get('size')
        if value is not None:
            if type(value) is int and 0 <= value <= 2**63 - 1:
                item['size_bytes'] = value
            else:
                warn('document_metadata_invalid')
                invalid = True
    if result['no_sap'] is True and any(row['has_sap'] is True for row in result['documents']):
        warn('no_sap_conflicts_with_listed_document')
        invalid = True
    if invalid:
        result['listing_status'] = 'unreadable_listing'
    return result


class _FetchError(Exception):
    def __init__(self, category, http_status=None):
        self.category = category
        self.http_status = http_status if type(http_status) is int and 100 <= http_status <= 599 else None
        super().__init__(category)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if fp is not None:
            fp.close()
        raise _FetchError('redirect_rejected', code)


def _allowed_url(url):
    if type(url) is not str:
        return False
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        return False
    if parsed.scheme != 'https' or parsed.username or parsed.password or port not in (None, 443) or parsed.query or parsed.fragment:
        return False
    if parsed.hostname == 'clinicaltrials.gov':
        return re.fullmatch(r'/api/v2/studies/NCT[0-9]{8}', parsed.path, re.ASCII) is not None
    if parsed.hostname == 'cdn.clinicaltrials.gov':
        parts = parsed.path.split('/')
        return (len(parts) == 5 and parts[:2] == ['', 'large-docs']
                and _valid_nct(parts[3]) and parts[2] == parts[3][-2:]
                and _safe_filename(parts[4]))
    return False


def _fetch(url, maximum):
    if not _allowed_url(url):
        raise _FetchError('source_url_rejected')
    opener = build_opener(ProxyHandler({}), _NoRedirect(), HTTPSHandler(context=ssl.create_default_context()))
    request = Request(url, headers={'User-Agent': 'PredomeResearch/registry-documents-1',
                                   'Accept': 'application/json,application/pdf'})
    observed_status = None
    try:
        with opener.open(request, timeout=NETWORK_TIMEOUT_SECONDS) as response:
            if type(response.status) is int and 100 <= response.status <= 599:
                observed_status = response.status
            if response.geturl() != url:
                raise _FetchError('redirect_rejected', observed_status)
            if response.status != 200:
                raise _FetchError('http_error', observed_status)
            raw = response.read(maximum + 1)
            if len(raw) > maximum:
                raise _FetchError('response_size_limit', observed_status)
            return raw
    except HTTPError as error:
        category = 'redirect_rejected' if type(error.code) is int and 300 <= error.code < 400 else 'http_error'
        error.close()
        raise _FetchError(category, error.code) from None
    except (TimeoutError, URLError) as error:
        timed_out = isinstance(error, TimeoutError) or isinstance(error.reason, TimeoutError)
        raise _FetchError('timeout' if timed_out else 'transport_error', observed_status) from None
    except (OSError, HTTPException):
        raise _FetchError('transport_error', observed_status) from None


def _utc_now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def _write_new(path, raw):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(raw)


def _json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + '\n').encode()


def _source(url, raw=None, retrieved=None):
    return {'url': url, 'sha256': hashlib.sha256(raw).hexdigest() if raw is not None else None,
            'bytes': len(raw) if raw is not None else None,
            'retrieved_at_utc': retrieved, 'available_at': retrieved}


def _strict_json(raw):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError('duplicate_key')
            value[key] = item
        return value
    def invalid_constant(value):
        raise ValueError('nonfinite_constant')
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid_constant)


def acquire_documents(nct_id, output_dir, filenames=()):
    """Fetch one fresh registry snapshot and only explicitly selected Prot/SAPs.

    A new output directory is mandatory. Failures persist fixed categories, never
    exception text. No extraction, model call, database or ledger write occurs.
    """
    if not _valid_nct(nct_id):
        raise ValueError('invalid_nct_id')
    if (type(filenames) not in (tuple, list) or len(filenames) > 32
            or any(not _safe_filename(name) for name in filenames) or len(set(filenames)) != len(filenames)):
        raise ValueError('invalid_selected_filenames')
    output = Path(output_dir)
    output.mkdir(mode=0o700, parents=False, exist_ok=False)
    registry_url = 'https://clinicaltrials.gov/api/v2/studies/' + nct_id
    manifest = {'schema_version': ACQUISITION_VERSION, 'nct_id': nct_id,
                'started_at_utc': _utc_now(), 'status': 'failed',
                'registry': {'status': 'failed', 'path': None, 'source': _source(registry_url),
                             'error_category': None, 'error_http_status': None},
                'inventory': None, 'inventory_path': 'inventory.json', 'documents': [],
                'warnings': ['document_dates_are_not_historical_availability_evidence'],
                'model_calls': 0, 'ledger_writes': 0, 'extraction_performed': False}
    study = None
    registry_ok = False
    try:
        raw = _fetch(registry_url, MAX_REGISTRY_BYTES)
    except _FetchError as error:
        manifest['registry']['error_category'] = error.category
        manifest['registry']['error_http_status'] = error.http_status
    else:
        retrieved = _utc_now()
        _write_new(output / 'registry.json', raw)
        manifest['registry'].update(path='registry.json', source=_source(registry_url, raw, retrieved))
        try:
            study = _strict_json(raw)
        except (ValueError, UnicodeError, RecursionError):
            manifest['registry']['error_category'] = 'registry_json_invalid'
            manifest['registry']['error_http_status'] = 200
        else:
            inventory = document_inventory(study)
            if inventory['nct_id'] != nct_id:
                manifest['registry']['error_category'] = 'registry_identity_mismatch'
                manifest['registry']['error_http_status'] = 200
            else:
                manifest['registry'].update(status='acquired', error_category=None)
                registry_ok = True
    inventory = document_inventory(study)
    manifest['inventory'] = inventory
    _write_new(output / 'inventory.json', _json_bytes(inventory))
    for filename in filenames:
        row = {'filename': filename, 'status': 'failed', 'path': None,
               'source': _source(None), 'error_category': None, 'error_http_status': None}
        manifest['documents'].append(row)
        if not registry_ok:
            row['error_category'] = 'registry_unavailable_or_mismatched'
            continue
        matching = [item for item in inventory['documents'] if item['filename'] == filename]
        if len(matching) != 1:
            row['error_category'] = 'document_not_listed' if not matching else 'document_listing_ambiguous'
            continue
        item = matching[0]
        if item['download_url'] is None:
            row['error_category'] = 'document_url_unavailable'
            continue
        if item['has_protocol'] is not True and item['has_sap'] is not True:
            row['error_category'] = 'document_not_protocol_or_sap'
            continue
        url = item['download_url']
        row['source'] = _source(url)
        try:
            raw = _fetch(url, MAX_DOCUMENT_BYTES)
        except _FetchError as error:
            row['error_category'] = error.category
            row['error_http_status'] = error.http_status
            continue
        retrieved = _utc_now()
        directory = output / 'documents'
        directory.mkdir(mode=0o700, exist_ok=True)
        _write_new(directory / filename, raw)
        row.update(path='documents/' + filename, source=_source(url, raw, retrieved))
        if not raw.startswith(b'%PDF-'):
            row['error_category'] = 'document_not_pdf'
            row['error_http_status'] = 200
            continue
        row.update(status='acquired', error_category=None)
    if registry_ok:
        incomplete = inventory['listing_status'] == 'unreadable_listing' or any(row['status'] != 'acquired' for row in manifest['documents'])
        manifest['status'] = 'partial' if incomplete else 'complete'
    manifest['completed_at_utc'] = _utc_now()
    _write_new(output / 'manifest.json', _json_bytes(manifest))
    return manifest


_PDF_PROGRAM = r'''
import io,json,sys
if sys.platform.startswith('linux'):
 import resource
 resource.setrlimit(resource.RLIMIT_AS,(536870912,536870912))
 resource.setrlimit(resource.RLIMIT_CPU,(30,30))
from pypdf import PdfReader
maximum=16777216
raw=sys.stdin.buffer.read(maximum+1)
if len(raw)>maximum:raise ValueError('input_limit')
reader=PdfReader(io.BytesIO(raw),strict=True)
if reader.is_encrypted:
 from pypdf.constants import UserAccessPermissions
 opened=reader.decrypt('');permissions=reader.user_access_permissions
 if not opened or permissions is None or not (permissions & UserAccessPermissions.EXTRACT_TEXT_AND_GRAPHICS):
  raise ValueError('encrypted_pdf_unavailable')
count=len(reader.pages)
if count>400:
 print(json.dumps({'status':'failed','page_count':count,'pages':[],'warnings':['pdf_page_limit'],'error_category':'pdf_page_limit'}))
 sys.exit(0)
pages=[];size=0;limited=False;warnings=[]
for index,page in enumerate(reader.pages):
 item={'page_index':index,'page_number':index+1,'status':None,'text':None}
 if limited:item['status']='not_extracted_text_limit'
 else:
  try:text=page.extract_text() or ''
  except Exception:
   item['status']='page_extraction_failed'
   if 'page_extraction_failed' not in warnings:warnings.append('page_extraction_failed')
  else:
   length=len(text.encode('utf-8'))
   if size+length>2097152:
    limited=True;item['status']='text_limit_exceeded'
    warnings.append('pdf_text_limit')
   elif not text.strip():
    item.update(status='empty',text=text);size+=length
    if 'empty_pages_may_be_blank_or_scanned' not in warnings:warnings.append('empty_pages_may_be_blank_or_scanned')
   else:item.update(status='text',text=text);size+=length
 pages.append(item)
status='complete' if pages and all(p['status']=='text' for p in pages) else 'partial'
if not pages:warnings.append('pdf_has_no_pages')
print(json.dumps({'status':status,'page_count':count,'pages':pages,'warnings':warnings,'error_category':None},ensure_ascii=False))
'''


def extract_pdf_pages(raw):
    """Explicit isolated parsing; empty/failed pages never become complete text."""
    if type(raw) is not bytes:
        raise ValueError('pdf_bytes_required')
    result = {'schema_version': PDF_VERSION, 'source_sha256': hashlib.sha256(raw).hexdigest(),
              'source_bytes': len(raw), 'status': 'failed', 'page_count': None,
              'pages': [], 'warnings': [], 'error_category': None}
    if len(raw) > MAX_DOCUMENT_BYTES:
        result['error_category'] = 'pdf_size_limit'
        return result
    if not raw.startswith(b'%PDF-'):
        result['error_category'] = 'invalid_pdf_header'
        return result
    try:
        process = subprocess.run([sys.executable, '-B', '-I', '-c', _PDF_PROGRAM],
            input=raw, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=PDF_TIMEOUT_SECONDS, check=False,
            env={'PATH': os.defpath, 'LANG': 'C.UTF-8', 'PYTHONDONTWRITEBYTECODE': '1', 'OMP_NUM_THREADS': '1'})
    except subprocess.TimeoutExpired:
        result['error_category'] = 'pdf_parse_timeout'
        return result
    except OSError:
        result['error_category'] = 'pdf_parser_unavailable'
        return result
    if process.returncode != 0:
        result['error_category'] = 'pdf_parse_failed'
        return result
    if len(process.stdout) > MAX_TEXT_BYTES * 4 + 256 * 1024:
        result['error_category'] = 'pdf_parser_output_limit'
        return result
    try:
        parsed = _strict_json(process.stdout)
        if type(parsed) is not dict or set(parsed) != {'status', 'page_count', 'pages', 'warnings', 'error_category'}:
            raise ValueError('invalid_output')
        count = parsed['page_count']
        pages = parsed['pages']
        if type(count) is not int or count < 0 or type(pages) is not list:
            raise ValueError('invalid_output')
        if count > MAX_PDF_PAGES:
            if pages or parsed['status'] != 'failed' or parsed['error_category'] != 'pdf_page_limit' or parsed['warnings'] != ['pdf_page_limit']:
                raise ValueError('invalid_output')
        else:
            statuses = {'text', 'empty', 'page_extraction_failed', 'text_limit_exceeded', 'not_extracted_text_limit'}
            if len(pages) != count or parsed['status'] not in ('complete', 'partial') or parsed['error_category'] is not None:
                raise ValueError('invalid_output')
            text_size = 0
            for index, page in enumerate(pages):
                if (type(page) is not dict or set(page) != {'page_index', 'page_number', 'status', 'text'}
                        or type(page['page_index']) is not int or page['page_index'] != index
                        or type(page['page_number']) is not int or page['page_number'] != index + 1
                        or page['status'] not in statuses):
                    raise ValueError('invalid_output')
                if page['status'] in ('text', 'empty'):
                    if type(page['text']) is not str or (page['status'] == 'text') != bool(page['text'].strip()):
                        raise ValueError('invalid_output')
                    text_size += len(page['text'].encode('utf-8'))
                elif page['text'] is not None:
                    raise ValueError('invalid_output')
            if text_size > MAX_TEXT_BYTES or (parsed['status'] == 'complete') != bool(pages and all(page['status'] == 'text' for page in pages)):
                raise ValueError('invalid_output')
        allowed_warnings = {'pdf_page_limit', 'pdf_text_limit', 'page_extraction_failed', 'empty_pages_may_be_blank_or_scanned', 'pdf_has_no_pages'}
        if (type(parsed['warnings']) is not list or any(type(w) is not str or w not in allowed_warnings for w in parsed['warnings'])):
            raise ValueError('invalid_output')
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        result['error_category'] = 'pdf_parser_output_invalid'
        return result
    result.update(parsed)
    return result
