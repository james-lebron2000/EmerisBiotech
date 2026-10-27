"""Local PDF registration from an unverified browser-UI observation declaration.

No network request or response is reconstructed. A hash binds local bytes only;
an operator-observed UI completion notice is not a captured download event or a
PDF HTTP response. Registration does not qualify evidence for forecasting.
"""
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
import unicodedata
from urllib.parse import urlsplit


WORKSPACE = Path(__file__).resolve().parents[1]
OBSERVATION_VERSION = 'predome.browser-trial-document-observation-1'
REGISTRATION_VERSION = 'predome.local-trial-document-registration-1'
MAX_PDF_BYTES = 16 * 1024 * 1024
MAX_OBSERVATION_BYTES = 64 * 1024
OBSERVATION_FIELDS = frozenset(('schema_version', 'origin_page_url', 'nct_id',
    'eu_ct_number', 'protocol_code', 'listed_document_title', 'document_type',
    'observation_at_utc', 'browser_download_completion_observed'))


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + '\n').encode()


def _strict_json(raw):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError('duplicate_json_key')
            value[key] = item
        return value
    def invalid_constant(value):
        raise ValueError('nonfinite_json_value')
    return json.loads(raw, object_pairs_hook=unique, parse_constant=invalid_constant)


def _plain(value, maximum):
    return (type(value) is str and bool(value.strip()) and len(value) <= maximum
            and not any(unicodedata.category(char) in ('Cc', 'Cf', 'Cs') or char in '\u2028\u2029' for char in value))


def _public_page_url(value):
    if not _plain(value, 2048) or any(char.isspace() for char in value):
        return False
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
                or parsed.port not in (None, 443) or parsed.query or parsed.fragment):
            return False
        ipaddress.ip_address(parsed.hostname)
    except ValueError:
        # A DNS name, rather than an address literal, is required. Malformed
        # URL/port structures are checked below without making any DNS request.
        try:
            host = urlsplit(value).hostname
            port = urlsplit(value).port
        except ValueError:
            return False
        return (port in (None, 443) and type(host) is str
                and re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}', host, re.ASCII) is not None
                and not host.endswith(('.local', '.localhost', '.internal', '.test', '.invalid')))
    return False


def _validate_observation(value, now):
    if type(value) is not dict or set(value) != OBSERVATION_FIELDS or value['schema_version'] != OBSERVATION_VERSION:
        raise ValueError('invalid_browser_observation_schema')
    if not _public_page_url(value['origin_page_url']):
        raise ValueError('invalid_observed_public_page')
    if type(value['nct_id']) is not str or re.fullmatch(r'NCT[0-9]{8}', value['nct_id'], re.ASCII) is None:
        raise ValueError('invalid_declared_nct_id')
    if type(value['eu_ct_number']) is not str or re.fullmatch(r'[0-9]{4}-[0-9]{6}-[0-9]{2}-[0-9]{2}', value['eu_ct_number'], re.ASCII) is None:
        raise ValueError('invalid_declared_eu_ct_number')
    if not _plain(value['protocol_code'], 200) or not _plain(value['listed_document_title'], 1000):
        raise ValueError('invalid_declared_document_metadata')
    if type(value['document_type']) is not str or value['document_type'] not in ('protocol', 'synopsis', 'sap'):
        raise ValueError('invalid_declared_document_type')
    if type(value['browser_download_completion_observed']) is not bool:
        raise ValueError('invalid_browser_ui_completion_observation')
    if not _plain(value['observation_at_utc'], 80):
        raise ValueError('invalid_observation_time')
    try:
        stamp = datetime.fromisoformat(value['observation_at_utc'].replace('Z', '+00:00'))
    except ValueError:
        raise ValueError('invalid_observation_time') from None
    if stamp.tzinfo is None or stamp > now:
        raise ValueError('invalid_observation_time')


def _confined(path, *, must_exist=True):
    path = Path(path)
    if path.is_symlink():
        raise ValueError('workspace_non_symlink_path_required')
    resolved = path.resolve(strict=must_exist)
    if not resolved.is_relative_to(WORKSPACE.resolve()):
        raise ValueError('workspace_path_required')
    return path, resolved


def _read_regular(path, maximum):
    original, resolved = _confined(path)
    before = original.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > maximum:
        raise ValueError('bounded_single_link_regular_input_required')
    descriptor = os.open(resolved, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(descriptor, 'rb') as source:
        opened = os.fstat(source.fileno())
        if ((opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
                or not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1):
            raise ValueError('input_changed_during_read')
        raw = source.read(maximum + 1)
        after = os.fstat(source.fileno())
    if (len(raw) > maximum or len(raw) != opened.st_size
            or (after.st_size, after.st_mtime_ns, after.st_nlink) != (opened.st_size, opened.st_mtime_ns, 1)):
        raise ValueError('input_changed_or_exceeds_limit')
    return raw


def _write_new(path, raw):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'wb') as output:
        output.write(raw)


def _file_record(path, raw):
    return {'path': path, 'sha256': _sha(raw), 'bytes': len(raw)}


def _extract_registered(output, manifest_raw, source_sha):
    from .trial_documents import extract_pdf_pages

    original = _read_regular(output / 'original.pdf', MAX_PDF_BYTES)
    if _sha(original) != source_sha or _read_regular(output / 'manifest.json', MAX_OBSERVATION_BYTES) != manifest_raw:
        raise ValueError('registered_source_changed_before_extraction')
    result = extract_pdf_pages(original)
    if (type(result) is not dict or result.get('source_sha256') != source_sha
            or type(result.get('source_bytes')) is not int or result['source_bytes'] != len(original)
            or result.get('status') not in ('complete', 'partial', 'failed')):
        raise ValueError('extraction_source_binding_invalid')
    directory = output / 'document-extractions'
    directory.mkdir(mode=0o700)
    relative = 'document-extractions/original.pdf.pages.json'
    pages_raw = _json_bytes(result)
    _write_new(output / relative, pages_raw)
    report = {'schema_version': 'predome.local-trial-document-extraction-1',
        'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'registration_manifest_sha256': _sha(manifest_raw), 'source_sha256': source_sha,
        'source_path': 'original.pdf', 'status': result['status'], 'page_count': result.get('page_count'),
        'pages_json': _file_record(relative, pages_raw), 'review_status': 'needs_human_review',
        'eligible_for_forecast': False, 'clinical_prediction_validated': False,
        'model_calls': 0, 'ledger_writes': 0, 'human_scientific_reviews': 0,
        'original_registration_modified': False, 'ocr_text_merged': False}
    _write_new(output / 'extraction-manifest.json', _json_bytes(report))
    if _read_regular(output / 'manifest.json', MAX_OBSERVATION_BYTES) != manifest_raw:
        raise ValueError('registration_changed_during_extraction')
    return report


def register_local_document(pdf_path, observation_path, output_dir, *, extract=False):
    """Register exact local bytes plus a closed, unverified operator declaration.

    Validate/read both inputs before exclusive directory creation. Optional page
    extraction is separate and cannot alter registration or promote eligibility.
    """
    if type(extract) is not bool:
        raise ValueError('extract_flag_must_be_boolean')
    raw = _read_regular(pdf_path, MAX_PDF_BYTES)
    if not raw.startswith(b'%PDF-'):
        raise ValueError('pdf_header_required')
    observation_raw = _read_regular(observation_path, MAX_OBSERVATION_BYTES)
    observation = _strict_json(observation_raw)
    _validate_observation(observation, datetime.now(timezone.utc))
    requested, output = _confined(output_dir, must_exist=False)
    if requested.exists() or requested.is_symlink():
        raise FileExistsError('new_output_directory_required')
    output.mkdir(mode=0o700, parents=False, exist_ok=False)
    _write_new(output / 'original.pdf', raw)
    _write_new(output / 'observation.json', observation_raw)
    if (_read_regular(output / 'original.pdf', MAX_PDF_BYTES) != raw
            or _read_regular(output / 'observation.json', MAX_OBSERVATION_BYTES) != observation_raw):
        raise ValueError('registration_copy_readback_failed')
    registered_at = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    manifest = {'schema_version': REGISTRATION_VERSION,
        'method': 'local_file_registration_from_browser_observation', 'registration_status': 'registered',
        'registered_at_utc': registered_at, 'available_at': registered_at,
        'origin_page_url': observation['origin_page_url'],
        'download_url': None, 'http_status': None, 'retrieved_at_utc': None,
        'original': _file_record('original.pdf', raw),
        'observation_source': _file_record('observation.json', observation_raw),
        'observation': observation, 'review_status': 'needs_human_review',
        'identity_status': 'unverified_operator_observation',
        'source_provenance_status': 'not_independently_verified',
        'hash_scope': 'local_file_bytes_only',
        'browser_completion_scope': 'Operator-observed browser UI completion notice only; not a captured download event or a PDF HTTP response.',
        'provenance_note': 'The local PDF may be an entry extracted from a downloaded archive. Archive membership and source authenticity are not established by this registration.',
        'eligible_for_forecast': False, 'clinical_prediction_validated': False,
        'model_calls': 0, 'ledger_writes': 0, 'human_scientific_reviews': 0}
    manifest_raw = _json_bytes(manifest)
    _write_new(output / 'manifest.json', manifest_raw)
    if _read_regular(output / 'manifest.json', MAX_OBSERVATION_BYTES) != manifest_raw:
        raise ValueError('registration_manifest_readback_failed')
    receipt = {'schema_version': 'predome.local-trial-document-registration-receipt-1',
        'registered_at_utc': registered_at, 'manifest': _file_record('manifest.json', manifest_raw),
        'original': manifest['original'], 'observation_source': manifest['observation_source'],
        'byte_copy_readback_verified': True, 'source_authenticity_verified': False,
        'review_status': 'needs_human_review'}
    _write_new(output / 'registration-receipt.json', _json_bytes(receipt))
    extraction = _extract_registered(output, manifest_raw, _sha(raw)) if extract else None
    return {'registration': manifest, 'registration_manifest_sha256': _sha(manifest_raw),
            'extraction': extraction}
