"""Real filesystem/isolated PDF checks, with only official HTTP transport replaced."""
from copy import deepcopy
import hashlib
from http.client import IncompleteRead
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request

import pytest

from emeris_live import trial_documents as documents


NCT = 'NCT04191499'
REGISTRY = 'https://clinicaltrials.gov/api/v2/studies/' + NCT
PREFIX = 'https://cdn.clinicaltrials.gov/large-docs/99/' + NCT + '/'
SECRET = 'FIXTURE_EXCEPTION_MUST_NOT_ENTER_MANIFEST'
ROW_FIELDS = {'filename', 'type_abbrev', 'label', 'document_date', 'upload_date',
              'has_protocol', 'has_sap', 'has_icf', 'size_bytes', 'download_url'}


def study(nct=NCT):
    return {'protocolSection': {'identificationModule': {'nctId': nct},
                'ipdSharingStatementModule': {'ipdSharing': 'YES',
                    'ipdSharingInfoTypes': ['STUDY_PROTOCOL', 'SAP']}},
            'documentSection': {'largeDocumentModule': {'largeDocs': [
                {'typeAbbrev': 'Prot', 'hasProtocol': True, 'hasSap': False, 'hasIcf': False,
                 'label': 'Study Protocol', 'date': '2023-03-08', 'uploadDate': '2024-09-16T16:21',
                 'filename': 'Prot_000.pdf', 'size': 8767559},
                {'typeAbbrev': 'SAP', 'hasProtocol': False, 'hasSap': True, 'hasIcf': False,
                 'label': 'Statistical Analysis Plan', 'date': '2023-09-18', 'uploadDate': '2024-09-16T16:21',
                 'filename': 'SAP_001.pdf', 'size': 919413}]}}}


def listing(value):
    return value['documentSection']['largeDocumentModule']


@pytest.fixture(autouse=True)
def no_real_transport(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('No real registry/CDN requests are authorized in these tests')
    monkeypatch.setattr(documents, 'build_opener', forbidden)


def transport(monkeypatch, responses, *, final_url=None, status=200, read_error=None):
    calls = []

    class Response:
        def __init__(self, url, raw):
            self.url, self.raw, self.status = url, raw, status
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return None
        def geturl(self):
            return final_url or self.url
        def read(self, maximum):
            calls[-1]['read_limit'] = maximum
            if read_error is not None:
                raise read_error
            return self.raw[:maximum]

    class Opener:
        def open(self, request, timeout):
            url = request.full_url
            calls.append({'url': url, 'timeout': timeout})
            if url not in responses:
                raise AssertionError('Unexpected fixture URL')
            value = responses[url]
            if isinstance(value, Exception):
                raise value
            return Response(url, value)

    def opener(*handlers):
        assert any(isinstance(handler, documents._NoRedirect) for handler in handlers)
        assert any(isinstance(handler, documents.HTTPSHandler) for handler in handlers)
        assert next(handler for handler in handlers if isinstance(handler, documents.ProxyHandler)).proxies == {}
        return Opener()
    monkeypatch.setattr(documents, 'build_opener', opener)
    return calls


def test_observed_listing_shape_preserves_dates_sizes_identity_and_unknown_no_sap():
    original = study()
    before = deepcopy(original)
    result = documents.document_inventory(original)
    assert result['schema_version'] == 'predome.registry-documents-1'
    assert result['nct_id'] == NCT and result['listing_status'] == 'listed'
    assert result['no_sap'] is None and result['warnings'] == []
    assert len(result['documents']) == 2
    assert all(set(row) == ROW_FIELDS for row in result['documents'])
    assert result['documents'][0] == {
        'filename': 'Prot_000.pdf', 'type_abbrev': 'Prot', 'label': 'Study Protocol',
        'document_date': '2023-03-08', 'upload_date': '2024-09-16T16:21',
        'has_protocol': True, 'has_sap': False, 'has_icf': False,
        'size_bytes': 8767559, 'download_url': PREFIX + 'Prot_000.pdf'}
    assert result['documents'][1]['size_bytes'] == 919413
    assert result['documents'][1]['has_sap'] is True
    assert original == before
    assert 'available_at' not in json.dumps(result)


def test_ipd_sharing_promise_without_attachment_module_is_not_a_listing():
    value = study()
    del value['documentSection']
    result = documents.document_inventory(value)
    assert result['listing_status'] == 'not_listed_in_snapshot'
    assert result['no_sap'] is None and result['documents'] == []
    assert result['warnings'] == ['listing_not_present_in_snapshot']


@pytest.mark.parametrize('section', [{}, {'largeDocumentModule': {}}, {'largeDocumentModule': {'noSap': True}}])
def test_missing_list_is_not_a_claim_that_a_protocol_or_sap_does_not_exist(section):
    value = study()
    value['documentSection'] = section
    result = documents.document_inventory(value)
    assert result['listing_status'] == 'not_listed_in_snapshot'
    assert result['documents'] == []
    assert result['no_sap'] is (True if section.get('largeDocumentModule', {}).get('noSap') is True else None)


def test_explicit_empty_list_and_no_sap_are_preserved_without_inventing_documents():
    value = study()
    value['documentSection'] = {'largeDocumentModule': {'noSap': True, 'largeDocs': []}}
    result = documents.document_inventory(value)
    assert result['listing_status'] == 'listed' and result['no_sap'] is True
    assert result['documents'] == [] and result['warnings'] == []


@pytest.mark.parametrize('section', [None, [], {'largeDocumentModule': None},
    {'largeDocumentModule': []}, {'largeDocumentModule': {'largeDocs': None}},
    {'largeDocumentModule': {'largeDocs': {}}}])
def test_bad_listing_structure_remains_unreadable(section):
    value = study()
    value['documentSection'] = section
    result = documents.document_inventory(value)
    assert result['listing_status'] == 'unreadable_listing'
    assert 'listing_structure_invalid' in result['warnings']


@pytest.mark.parametrize('value', [None, [], 'bad'])
def test_unreadable_study_does_not_imply_an_absent_attachment(value):
    result = documents.document_inventory(value)
    assert result['listing_status'] == 'unreadable_listing' and result['nct_id'] is None
    assert result['documents'] == [] and result['warnings'] == ['study_structure_invalid']


@pytest.mark.parametrize('bad', [None, [], 'not a row', 7])
def test_bad_rows_keep_original_row_count_and_order(bad):
    value = study()
    listing(value)['largeDocs'].insert(1, bad)
    result = documents.document_inventory(value)
    assert len(result['documents']) == 3
    assert result['documents'][0]['filename'] == 'Prot_000.pdf'
    assert all(v is None for v in result['documents'][1].values())
    assert result['documents'][2]['filename'] == 'SAP_001.pdf'
    assert result['listing_status'] == 'unreadable_listing'
    assert result['warnings'] == ['document_entry_invalid']


@pytest.mark.parametrize('filename', ['../Prot_000.pdf', '/Prot_000.pdf', 'a\\Prot_000.pdf',
    'Prot%2F000.pdf', 'Prot_000.pdf?token=x', 'https://example.test/a.pdf',
    'Prot..pdf', '.Prot.pdf', '协议.pdf', 'Prot_000.pdf#x', None])
def test_only_safe_literal_listed_pdf_basename_can_generate_cdn_url(filename):
    value = study()
    listing(value)['largeDocs'][0]['filename'] = filename
    result = documents.document_inventory(value)
    assert len(result['documents']) == 2 and result['listing_status'] == 'unreadable_listing'
    assert result['documents'][0]['filename'] is None
    assert result['documents'][0]['download_url'] is None
    assert 'document_filename_unsafe' in result['warnings']


@pytest.mark.parametrize('nct', [None, 'NCT0419149', 'nct04191499', ' NCT04191499',
    'NCT04191499/../', 4191499])
def test_missing_or_invalid_identity_never_constructs_attachment_url(nct):
    result = documents.document_inventory(study(nct))
    assert result['nct_id'] is None and result['listing_status'] == 'unreadable_listing'
    assert all(row['download_url'] is None for row in result['documents'])


def test_unknown_booleans_are_not_coerced_and_duplicate_names_are_ambiguous():
    value = study()
    first = listing(value)['largeDocs'][0]
    del first['hasSap']
    first.update(hasProtocol='true', hasIcf=0, size='8767559')
    listing(value)['largeDocs'][1]['filename'] = first['filename']
    listing(value)['noSap'] = 'false'
    result = documents.document_inventory(value)
    assert result['listing_status'] == 'unreadable_listing' and result['no_sap'] is None
    assert all(result['documents'][0][key] is None for key in ('has_protocol', 'has_sap', 'has_icf', 'size_bytes'))
    assert set(result['warnings']) == {'no_sap_invalid', 'document_metadata_invalid', 'document_filename_duplicate'}


def test_no_sap_contradiction_and_bad_metadata_are_visible():
    value = study()
    listing(value)['noSap'] = True
    listing(value)['largeDocs'][0]['uploadDate'] = {'not': 'a timestamp'}
    result = documents.document_inventory(value)
    assert result['no_sap'] is True and result['listing_status'] == 'unreadable_listing'
    assert result['documents'][0]['upload_date'] is None
    assert set(result['warnings']) == {'document_metadata_invalid', 'no_sap_conflicts_with_listed_document'}


def test_inventory_only_acquires_exact_original_registry_with_current_availability(tmp_path, monkeypatch):
    raw = json.dumps(study(), indent=1).encode() + b'\n'
    calls = transport(monkeypatch, {REGISTRY: raw})
    output = tmp_path / 'new-run'
    result = documents.acquire_documents(NCT, output)
    assert len(calls) == 1 and calls[0]['url'] == REGISTRY
    assert calls[0]['read_limit'] == documents.MAX_REGISTRY_BYTES + 1
    assert calls[0]['timeout'] == documents.NETWORK_TIMEOUT_SECONDS
    assert (output / 'registry.json').read_bytes() == raw
    assert result['registry']['source']['sha256'] == hashlib.sha256(raw).hexdigest()
    assert result['registry']['source']['bytes'] == len(raw)
    source = result['registry']['source']
    assert source['available_at'] == source['retrieved_at_utc']
    assert source['available_at'] >= result['started_at_utc']
    assert source['available_at'] <= result['completed_at_utc']
    assert source['available_at'] != result['inventory']['documents'][0]['upload_date']
    assert result['status'] == 'complete' and result['documents'] == []
    assert result['registry']['error_http_status'] is None
    assert result['model_calls'] == result['ledger_writes'] == 0 and not result['extraction_performed']
    assert json.loads((output / 'manifest.json').read_bytes()) == result
    assert json.loads((output / 'inventory.json').read_bytes()) == result['inventory']
    assert {p.name for p in output.iterdir()} == {'registry.json', 'inventory.json', 'manifest.json'}
    assert (output.stat().st_mode & 0o777) == 0o700
    assert all((p.stat().st_mode & 0o777) == 0o600 for p in output.iterdir())


def test_explicit_protocol_over_old6mib_limit_and_sap_preserve_source_bytes(tmp_path, monkeypatch):
    value = study()
    listing(value)['largeDocs'][0]['url'] = 'https://attacker.test/replaced.pdf'
    protocol = b'%PDF-1.7\n' + b'x' * (7 * 1024 * 1024)
    sap = b'%PDF-1.4\nfixture protocol bytes; extraction deliberately not automatic'
    calls = transport(monkeypatch, {REGISTRY: json.dumps(value).encode(),
        PREFIX + 'Prot_000.pdf': protocol, PREFIX + 'SAP_001.pdf': sap})
    output = tmp_path / 'selected'
    result = documents.acquire_documents(NCT, output, ['Prot_000.pdf', 'SAP_001.pdf'])
    assert result['status'] == 'complete'
    assert [c['url'] for c in calls] == [REGISTRY, PREFIX + 'Prot_000.pdf', PREFIX + 'SAP_001.pdf']
    assert all(c['read_limit'] == documents.MAX_DOCUMENT_BYTES + 1 for c in calls[1:])
    for entry, raw in zip(result['documents'], (protocol, sap)):
        assert entry['status'] == 'acquired' and entry['error_category'] is None
        assert (output / entry['path']).read_bytes() == raw
        assert entry['source']['sha256'] == hashlib.sha256(raw).hexdigest()
        assert entry['source']['bytes'] == len(raw)
        assert entry['source']['available_at'] == entry['source']['retrieved_at_utc']


def test_unselected_sap_is_not_fetched_and_icf_or_unknown_name_cannot_expand_scope(tmp_path, monkeypatch):
    value = study()
    listing(value)['largeDocs'].append({'filename': 'ICF_002.pdf', 'typeAbbrev': 'ICF',
        'hasProtocol': False, 'hasSap': False, 'hasIcf': True})
    calls = transport(monkeypatch, {REGISTRY: json.dumps(value).encode()})
    result = documents.acquire_documents(NCT, tmp_path / 'run', ['ICF_002.pdf', 'SAP_999.pdf'])
    assert len(calls) == 1 and result['status'] == 'partial'
    assert [row['error_category'] for row in result['documents']] == ['document_not_protocol_or_sap', 'document_not_listed']
    assert all(row['path'] is None for row in result['documents'])


def test_duplicate_listed_filename_never_selects_an_arbitrary_entry(tmp_path, monkeypatch):
    value = study()
    listing(value)['largeDocs'].append(deepcopy(listing(value)['largeDocs'][0]))
    calls = transport(monkeypatch, {REGISTRY: json.dumps(value).encode()})
    result = documents.acquire_documents(NCT, tmp_path / 'run', ['Prot_000.pdf'])
    assert len(calls) == 1
    assert result['documents'][0]['error_category'] == 'document_listing_ambiguous'


@pytest.mark.parametrize('filenames', ['Prot_000.pdf', ['../Prot.pdf'], ['Prot_000.pdf'] * 2, ['x.pdf'] * 33])
def test_invalid_selection_has_no_network_or_output_side_effect(tmp_path, filenames):
    output = tmp_path / 'run'
    with pytest.raises(ValueError, match='invalid_selected_filenames'):
        documents.acquire_documents(NCT, output, filenames)
    assert not output.exists()


def test_existing_output_and_symlink_are_never_overwritten(tmp_path, monkeypatch):
    output = tmp_path / 'existing'
    output.mkdir()
    (output / 'original').write_bytes(b'keep')
    with pytest.raises(FileExistsError):
        documents.acquire_documents(NCT, output)
    alias = tmp_path / 'alias'
    alias.symlink_to(output, target_is_directory=True)
    with pytest.raises(FileExistsError):
        documents.acquire_documents(NCT, alias)
    assert (output / 'original').read_bytes() == b'keep'
    assert list(output.iterdir()) == [output / 'original']
    with pytest.raises(ValueError, match='invalid_nct_id'):
        documents.acquire_documents('NCTbad', tmp_path / 'bad')
    assert not (tmp_path / 'bad').exists()


def test_mismatched_registry_identity_preserves_raw_but_never_downloads_selected_pdf(tmp_path, monkeypatch):
    raw = json.dumps(study('NCT00000001')).encode()
    calls = transport(monkeypatch, {REGISTRY: raw})
    output = tmp_path / 'mismatched'
    result = documents.acquire_documents(NCT, output, ['Prot_000.pdf'])
    assert len(calls) == 1 and result['status'] == 'failed'
    assert result['registry']['error_category'] == 'registry_identity_mismatch'
    assert result['registry']['error_http_status'] == 200
    assert result['inventory']['nct_id'] == 'NCT00000001'
    assert result['documents'][0]['error_category'] == 'registry_unavailable_or_mismatched'
    assert (output / 'registry.json').read_bytes() == raw


@pytest.mark.parametrize('raw', [b'{"protocolSection":', b'{"x":1,"x":2}', b'{"x":NaN}', b'\xff\xfeinvalid'])
def test_unparseable_registry_is_archived_without_exception_text_or_download(tmp_path, monkeypatch, raw):
    calls = transport(monkeypatch, {REGISTRY: raw})
    output = tmp_path / 'bad-json'
    result = documents.acquire_documents(NCT, output, ['Prot_000.pdf'])
    assert len(calls) == 1 and result['registry']['error_category'] == 'registry_json_invalid'
    assert result['status'] == 'failed' and result['inventory']['listing_status'] == 'unreadable_listing'
    assert (output / 'registry.json').read_bytes() == raw


@pytest.mark.parametrize('error,category', [
    (TimeoutError(SECRET), 'timeout'), (URLError(SECRET), 'transport_error'),
    (HTTPError(REGISTRY, 403, SECRET, {}, None), 'http_error'),
    (HTTPError(REGISTRY, 302, SECRET, {}, None), 'redirect_rejected')])
def test_fetch_failures_persist_only_fixed_categories_without_retry(tmp_path, monkeypatch, error, category):
    calls = transport(monkeypatch, {REGISTRY: error})
    output = tmp_path / 'failed'
    result = documents.acquire_documents(NCT, output, ['Prot_000.pdf'])
    assert len(calls) == 1 and result['registry']['error_category'] == category
    assert result['registry']['error_http_status'] == (error.code if isinstance(error, HTTPError) else None)
    assert result['registry']['source']['sha256'] is None
    assert result['registry']['source']['available_at'] is None
    assert result['status'] == 'failed' and not (output / 'registry.json').exists()
    assert SECRET not in (output / 'manifest.json').read_text()


@pytest.mark.parametrize('status', [403, 429, 503])
def test_observed_explicit_http_status_is_preserved_without_reading_failure_body(tmp_path, monkeypatch, status):
    calls = transport(monkeypatch, {REGISTRY: SECRET.encode()}, status=status)
    output = tmp_path / 'status'
    result = documents.acquire_documents(NCT, output)
    assert len(calls) == 1 and 'read_limit' not in calls[0]
    assert result['registry']['error_category'] == 'http_error'
    assert result['registry']['error_http_status'] == status
    assert SECRET not in (output / 'manifest.json').read_text()


def test_selected_document_retains_observed_error_status_without_reading_http_error_body(tmp_path, monkeypatch):
    class ForbiddenErrorBody:
        def __init__(self):
            self.closed = False
        def read(self, *args):
            raise AssertionError('Never inspect an error body')
        def close(self):
            self.closed = True
    body = ForbiddenErrorBody()
    calls = transport(monkeypatch, {REGISTRY: json.dumps(study()).encode(),
        PREFIX + 'SAP_001.pdf': HTTPError(PREFIX + 'SAP_001.pdf', 429, SECRET, {}, body)})
    output = tmp_path / 'doc-http'
    result = documents.acquire_documents(NCT, output, ['SAP_001.pdf'])
    assert len(calls) == 2 and body.closed
    assert result['documents'][0]['error_http_status'] == 429
    assert result['documents'][0]['error_category'] == 'http_error'
    assert result['documents'][0]['source']['available_at'] is None
    assert SECRET not in (output / 'manifest.json').read_text()


@pytest.mark.parametrize('error', [IncompleteRead(SECRET.encode(), 123), TimeoutError(SECRET)])
def test_body_interruption_retains_observed200_and_no_partial_original_or_raw_error(tmp_path, monkeypatch, error):
    calls = transport(monkeypatch, {REGISTRY: b'{}'}, read_error=error)
    output = tmp_path / 'interrupted'
    result = documents.acquire_documents(NCT, output)
    assert len(calls) == 1
    assert result['registry']['error_category'] == ('timeout' if isinstance(error, TimeoutError) else 'transport_error')
    assert result['registry']['error_http_status'] == 200
    assert result['registry']['path'] is None
    assert SECRET not in (output / 'manifest.json').read_text()


def test_selected_download_failure_keeps_other_original_and_fixed_error(tmp_path, monkeypatch):
    calls = transport(monkeypatch, {REGISTRY: json.dumps(study()).encode(),
        PREFIX + 'Prot_000.pdf': b'%PDF-1.4\nkept',
        PREFIX + 'SAP_001.pdf': TimeoutError(SECRET)})
    output = tmp_path / 'partial'
    result = documents.acquire_documents(NCT, output, ['Prot_000.pdf', 'SAP_001.pdf'])
    assert len(calls) == 3 and result['status'] == 'partial'
    assert result['documents'][0]['status'] == 'acquired'
    assert result['documents'][1]['error_category'] == 'timeout'
    assert (output / result['documents'][0]['path']).read_bytes() == b'%PDF-1.4\nkept'
    assert SECRET not in (output / 'manifest.json').read_text()


def test_non_pdf_response_keeps_raw_without_claiming_an_acquired_protocol(tmp_path, monkeypatch):
    raw = b'<html>blocked</html>'
    transport(monkeypatch, {REGISTRY: json.dumps(study()).encode(), PREFIX + 'Prot_000.pdf': raw})
    output = tmp_path / 'html'
    result = documents.acquire_documents(NCT, output, ['Prot_000.pdf'])
    row = result['documents'][0]
    assert result['status'] == 'partial' and row['status'] == 'failed'
    assert row['error_category'] == 'document_not_pdf'
    assert row['error_http_status'] == 200
    assert (output / row['path']).read_bytes() == raw
    assert row['source']['sha256'] == hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize('registry', [True, False])
def test_registry_and_document_caps_are_enforced_at_actual_http_read(tmp_path, monkeypatch, registry):
    maximum = documents.MAX_REGISTRY_BYTES if registry else documents.MAX_DOCUMENT_BYTES
    responses = {REGISTRY: b'x' * (maximum + 1) if registry else json.dumps(study()).encode()}
    if not registry:
        responses[PREFIX + 'Prot_000.pdf'] = b'%PDF-' + b'x' * maximum
    calls = transport(monkeypatch, responses)
    output = tmp_path / 'cap'
    result = documents.acquire_documents(NCT, output, ['Prot_000.pdf'])
    row = result['registry'] if registry else result['documents'][0]
    assert row['error_category'] == 'response_size_limit' and row['path'] is None
    assert row['error_http_status'] == 200
    assert calls[-1]['read_limit'] == maximum + 1
    assert not (output / 'documents').exists()


@pytest.mark.parametrize('url', ['http://clinicaltrials.gov/api/v2/studies/' + NCT,
    'https://clinicaltrials.gov.attacker.test/api/v2/studies/' + NCT,
    'https://user:pass@clinicaltrials.gov/api/v2/studies/' + NCT,
    REGISTRY + '?token=x', 'https://127.0.0.1/a',
    'https://cdn.clinicaltrials.gov/large-docs/00/' + NCT + '/Prot_000.pdf',
    'https://cdn.clinicaltrials.gov/large-docs/99/' + NCT + '/../../a.pdf',
    'https://[bad'])
def test_unapproved_urls_are_rejected_before_transport(url):
    with pytest.raises(documents._FetchError, match='source_url_rejected'):
        documents._fetch(url, 10)


@pytest.mark.parametrize('destination', [PREFIX + 'Prot_000.pdf', 'https://attacker.test/secret'])
def test_every_redirect_is_rejected_even_on_same_official_host(destination):
    with pytest.raises(documents._FetchError, match='redirect_rejected') as caught:
        documents._NoRedirect().redirect_request(Request(REGISTRY), None, 302, SECRET, {}, destination)
    assert caught.value.http_status == 302


def test_unexpected_final_url_is_rejected_without_storing_it(tmp_path, monkeypatch):
    calls = transport(monkeypatch, {REGISTRY: b'{}'}, final_url='https://attacker.test/' + SECRET)
    output = tmp_path / 'redirect'
    result = documents.acquire_documents(NCT, output)
    assert len(calls) == 1 and result['registry']['error_category'] == 'redirect_rejected'
    assert SECRET not in (output / 'manifest.json').read_text()


def pdf_bytes(texts):
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    writer = PdfWriter()
    for text in texts:
        page = writer.add_blank_page(width=612, height=792)
        if text is not None:
            font = DictionaryObject({NameObject('/Type'): NameObject('/Font'),
                NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
            page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'):
                DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
            content = DecodedStreamObject()
            content.set_data(('BT /F1 12 Tf 10 10 Td (' + text + ') Tj ET').encode())
            page[NameObject('/Contents')] = writer._add_object(content)
    stream = io.BytesIO()
    writer.write(stream)
    return stream.getvalue()


def test_explicit_pdf_subprocess_extracts_text_with_complete_page_indices():
    raw = pdf_bytes(['Protocol population', 'SAP estimand'])
    result = documents.extract_pdf_pages(raw)
    assert result['source_sha256'] == hashlib.sha256(raw).hexdigest()
    assert result['status'] == 'complete' and result['page_count'] == 2
    assert [page['page_index'] for page in result['pages']] == [0, 1]
    assert [page['page_number'] for page in result['pages']] == [1, 2]
    assert result['pages'][1]['text'] == 'SAP estimand'
    assert result['warnings'] == [] and result['error_category'] is None


def test_208_page_pdf_passes_new_limit_but_blank_pages_are_never_complete_text():
    raw = pdf_bytes([None] * 208)
    result = documents.extract_pdf_pages(raw)
    assert result['status'] == 'partial' and result['page_count'] == 208
    assert [page['page_index'] for page in result['pages']] == list(range(208))
    assert all(page['status'] == 'empty' for page in result['pages'])
    assert result['warnings'] == ['empty_pages_may_be_blank_or_scanned']


def test_more_than400_pages_fails_without_claiming_partial_index_is_complete():
    result = documents.extract_pdf_pages(pdf_bytes([None] * 401))
    assert result['status'] == 'failed' and result['page_count'] == 401
    assert result['pages'] == [] and result['error_category'] == 'pdf_page_limit'


def test_actual_pdf_text_cap_keeps_all_page_indices_and_does_not_invent_remaining_text():
    raw = pdf_bytes(['A' * (documents.MAX_TEXT_BYTES + 1), 'Must remain unextracted'])
    result = documents.extract_pdf_pages(raw)
    assert result['status'] == 'partial' and result['page_count'] == 2
    assert [p['status'] for p in result['pages']] == ['text_limit_exceeded', 'not_extracted_text_limit']
    assert all(p['text'] is None for p in result['pages'])
    assert result['warnings'] == ['pdf_text_limit']


def test_blank_whitespace_is_included_in_total_text_byte_budget():
    raw = pdf_bytes([' ' * documents.MAX_TEXT_BYTES, 'Later readable page'])
    result = documents.extract_pdf_pages(raw)
    assert result['status'] == 'partial' and result['page_count'] == 2
    assert result['pages'][0]['status'] == 'empty'
    assert len(result['pages'][0]['text'].encode()) == documents.MAX_TEXT_BYTES
    assert result['pages'][1]['status'] == 'text_limit_exceeded'
    assert result['pages'][1]['text'] is None
    assert set(result['warnings']) == {'empty_pages_may_be_blank_or_scanned', 'pdf_text_limit'}


def test_corrupt_pdf_and_non_pdf_never_become_complete_text():
    raw = b'%PDF-1.7\nnot a parseable PDF'
    result = documents.extract_pdf_pages(raw)
    assert result['status'] == 'failed' and result['error_category'] == 'pdf_parse_failed'
    assert result['source_sha256'] == hashlib.sha256(raw).hexdigest()
    assert result['pages'] == []
    assert documents.extract_pdf_pages(b'html')['error_category'] == 'invalid_pdf_header'
    assert documents.extract_pdf_pages(b'%PDF-' + b'x' * documents.MAX_DOCUMENT_BYTES)['error_category'] == 'pdf_size_limit'


def test_pdf_timeout_has_no_raw_exception_or_credentials(monkeypatch):
    monkeypatch.setenv('SEM_API_KEY', SECRET)
    def timeout(argv, **kwargs):
        assert argv[1:3] == ['-B', '-I']
        assert kwargs['timeout'] == documents.PDF_TIMEOUT_SECONDS
        assert SECRET not in json.dumps(kwargs['env'])
        assert kwargs['stderr'] is subprocess.DEVNULL
        raise subprocess.TimeoutExpired(argv, documents.PDF_TIMEOUT_SECONDS, output=SECRET, stderr=SECRET)
    monkeypatch.setattr(documents.subprocess, 'run', timeout)
    result = documents.extract_pdf_pages(b'%PDF-1.7\nfixture')
    assert result['error_category'] == 'pdf_parse_timeout'
    assert SECRET not in json.dumps(result)


@pytest.mark.parametrize('payload', [
    {'status': 'complete', 'page_count': 2, 'pages': [], 'warnings': [], 'error_category': None},
    {'status': 'complete', 'page_count': 1, 'pages': [{'page_index': 0, 'page_number': 1,
        'status': 'empty', 'text': ''}], 'warnings': [], 'error_category': None},
    {'status': 'partial', 'page_count': 1, 'pages': [{'page_index': 1, 'page_number': 2,
        'status': 'text', 'text': 'bad index'}], 'warnings': [], 'error_category': None},
])
def test_parser_output_validation_rejects_missing_pages_or_fabricated_completeness(monkeypatch, payload):
    monkeypatch.setattr(documents.subprocess, 'run', lambda *args, **kwargs:
        subprocess.CompletedProcess(args, 0, json.dumps(payload).encode()))
    result = documents.extract_pdf_pages(b'%PDF-1.7\nfixture')
    assert result['error_category'] == 'pdf_parser_output_invalid' and result['pages'] == []


def test_module_import_has_no_network_database_write_or_subprocess_effect(tmp_path):
    app = Path(__file__).resolve().parents[1]
    program = '''
import os,sys
def guard(event,args):
 if event.startswith('socket.') or event in {'sqlite3.connect','subprocess.Popen','os.system','os.mkdir','os.remove','os.rename'}:
  raise RuntimeError('import activity forbidden')
 if event=='open':
  mode,flags=args[1],args[2]
  if (isinstance(mode,str) and any(c in mode for c in 'wax+')) or (isinstance(flags,int) and flags&(os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND)):
   raise RuntimeError('import write forbidden')
sys.addaudithook(guard)
sys.path.insert(0,sys.argv[1])
from emeris_live.trial_documents import document_inventory
assert document_inventory({})['listing_status']=='not_listed_in_snapshot'
'''
    process = subprocess.run([sys.executable, '-B', '-I', '-c', program, str(app)],
        cwd=tmp_path, capture_output=True, text=True, timeout=20,
        env={'PATH': os.defpath, 'PYTHONDONTWRITEBYTECODE': '1'})
    assert process.returncode == 0, process.stderr
    assert list(tmp_path.iterdir()) == []
