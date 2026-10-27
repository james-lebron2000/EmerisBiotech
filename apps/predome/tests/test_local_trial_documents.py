"""Local byte/provenance boundaries, not authentication of browser declarations."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from emeris_live import local_trial_documents as local


PDF = b'%PDF-1.7\nSynthetic local registration fixture; no scientific content'
SECRET = 'UNTRUSTED_EXCEPTION_OR_CREDENTIAL_MUST_NOT_BE_ECHOED'


def observation():
    return {'schema_version': 'predome.browser-trial-document-observation-1',
        'origin_page_url': 'https://euclinicaltrials.eu/ctis-public/view/2024-511450-49-00',
        'nct_id': 'NCT06662383', 'eu_ct_number': '2024-511450-49-00',
        'protocol_code': 'J1I-MC-GZBP', 'listed_document_title': 'D1_Protocol 2024-511450-49-00_Redacted ',
        'document_type': 'protocol', 'observation_at_utc': '2026-01-01T12:00:00+08:00',
        'browser_download_completion_observed': True}


@pytest.fixture(autouse=True)
def workspace(monkeypatch, tmp_path):
    monkeypatch.setattr(local, 'WORKSPACE', tmp_path)
    return tmp_path


def inputs(root, *, pdf=PDF, value=None, raw_observation=None):
    original = root / 'input.pdf'
    record = root / 'input.observation.json'
    original.write_bytes(pdf)
    raw = raw_observation if raw_observation is not None else (json.dumps(observation() if value is None else value, ensure_ascii=False, indent=1) + '\n').encode()
    record.write_bytes(raw)
    return original, record


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def test_exact_local_bytes_and_operator_declaration_are_registered_without_http_or_identity_claims(workspace):
    pdf, declaration = inputs(workspace)
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in (pdf, declaration)}
    output = workspace / 'new-registration'
    result = local.register_local_document(pdf, declaration, output)
    manifest = result['registration']
    assert manifest['schema_version'] == 'predome.local-trial-document-registration-1'
    assert manifest['method'] == 'local_file_registration_from_browser_observation'
    assert manifest['registration_status'] == 'registered'
    assert manifest['review_status'] == 'needs_human_review'
    assert manifest['identity_status'] == 'unverified_operator_observation'
    assert manifest['source_provenance_status'] == 'not_independently_verified'
    assert manifest['download_url'] is manifest['http_status'] is manifest['retrieved_at_utc'] is None
    assert manifest['registered_at_utc'] == manifest['available_at']
    assert datetime.fromisoformat(manifest['registered_at_utc'].replace('Z', '+00:00')) <= datetime.now(timezone.utc)
    assert manifest['observation'] == observation()
    assert manifest['observation']['observation_at_utc'] != manifest['available_at']
    assert manifest['observation']['listed_document_title'].endswith(' ')
    assert 'not a captured download event' in manifest['browser_completion_scope']
    assert 'Archive membership' in manifest['provenance_note']
    assert manifest['hash_scope'] == 'local_file_bytes_only'
    assert manifest['eligible_for_forecast'] is manifest['clinical_prediction_validated'] is False
    assert manifest['model_calls'] == manifest['ledger_writes'] == manifest['human_scientific_reviews'] == 0
    assert result['extraction'] is None
    assert (output / 'original.pdf').read_bytes() == PDF
    assert (output / 'observation.json').read_bytes() == declaration.read_bytes()
    for key in ('original', 'observation_source'):
        record = manifest[key]
        raw = (output / record['path']).read_bytes()
        assert record['sha256'] == sha(raw) and record['bytes'] == len(raw)
    manifest_raw = (output / 'manifest.json').read_bytes()
    assert json.loads(manifest_raw) == manifest
    assert result['registration_manifest_sha256'] == sha(manifest_raw)
    receipt = json.loads((output / 'registration-receipt.json').read_bytes())
    assert receipt['manifest'] == {'path': 'manifest.json', 'sha256': sha(manifest_raw), 'bytes': len(manifest_raw)}
    assert receipt['byte_copy_readback_verified'] is True and receipt['source_authenticity_verified'] is False
    assert set(path.name for path in output.iterdir()) == {'original.pdf', 'observation.json', 'manifest.json', 'registration-receipt.json'}
    assert output.stat().st_mode & 0o777 == 0o700
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in output.iterdir())
    assert all((path.read_bytes(), path.stat().st_mtime_ns) == original for path, original in before.items())


@pytest.mark.parametrize('kind,completed', [('protocol', True), ('synopsis', False), ('sap', True)])
def test_document_type_and_ui_completion_boolean_remain_declarations_not_download_proof(workspace, kind, completed):
    value = observation()
    value.update(document_type=kind, browser_download_completion_observed=completed)
    pdf, record = inputs(workspace, value=value)
    result = local.register_local_document(pdf, record, workspace / 'registered')
    assert result['registration']['observation'] == value
    assert result['registration']['download_url'] is None
    assert result['registration']['http_status'] is None
    assert result['registration']['identity_status'] == 'unverified_operator_observation'


@pytest.mark.parametrize('raw', [b'{', b'{"schema_version":1,"schema_version":2}',
    b'{"x":NaN}', b'{"x":Infinity}', b'{"x":-Infinity}', b'\xff\xfeinvalid'])
def test_bad_duplicate_nonfinite_json_fails_before_directory_creation(workspace, raw):
    pdf, record = inputs(workspace, raw_observation=raw)
    output = workspace / 'not-created'
    with pytest.raises((ValueError, UnicodeError)):
        local.register_local_document(pdf, record, output)
    assert not output.exists() and record.read_bytes() == raw


@pytest.mark.parametrize('field,value', [('http_status', 200), ('download_url', 'https://euclinicaltrials.eu/guessed/file.pdf'),
    ('retrieved_at_utc', '2020-01-01T00:00:00Z'), ('api_key', SECRET),
    ('identity_verified', True), ('eligible_for_forecast', True), ('scientific_qualification', 'verified')])
def test_unknown_transport_and_qualification_claims_are_not_accepted(workspace, field, value):
    record = observation()
    record[field] = value
    pdf, declaration = inputs(workspace, value=record)
    output = workspace / 'not-created'
    with pytest.raises(ValueError, match='invalid_browser_observation_schema'):
        local.register_local_document(pdf, declaration, output)
    assert not output.exists()


@pytest.mark.parametrize('change', [
    {'schema_version': 'other'}, {'nct_id': 'NCTbad'}, {'nct_id': None},
    {'eu_ct_number': '2024-511450'}, {'protocol_code': ''}, {'protocol_code': 'x\nprivate'},
    {'listed_document_title': ' '}, {'listed_document_title': 'x' * 1001},
    {'document_type': 'investigator_brochure'}, {'browser_download_completion_observed': 1},
    {'browser_download_completion_observed': None}, {'observation_at_utc': '2026-01-01'},
    {'observation_at_utc': '2999-01-01T00:00:00Z'}, {'observation_at_utc': True},
])
def test_invalid_closed_metadata_and_unknown_or_future_times_are_rejected(workspace, change):
    value = observation()
    value.update(change)
    pdf, record = inputs(workspace, value=value)
    with pytest.raises(ValueError):
        local.register_local_document(pdf, record, workspace / 'not-created')
    assert not (workspace / 'not-created').exists()


@pytest.mark.parametrize('field', list(local.OBSERVATION_FIELDS))
def test_all_nine_observation_fields_are_required(workspace, field):
    value = observation()
    del value[field]
    pdf, record = inputs(workspace, value=value)
    with pytest.raises(ValueError, match='invalid_browser_observation_schema'):
        local.register_local_document(pdf, record, workspace / 'not-created')
    assert not (workspace / 'not-created').exists()


@pytest.mark.parametrize('url', ['http://euclinicaltrials.eu/ctis-public/view/123',
    'https://user:pass@euclinicaltrials.eu/view/123', 'https://127.0.0.1/view/123',
    'https://[::1]/view/123', 'https://localhost/view/123', 'https://service.local/view/123',
    'https://euclinicaltrials.eu:444/view/123', 'https://euclinicaltrials.eu:bad/view/123',
    'https://euclinicaltrials.eu/view/123?token=private', 'https://euclinicaltrials.eu/view/123#private',
    'https://[malformed', 'https://euclinicaltrials.eu/view/white space'])
def test_origin_must_be_a_credential_free_public_https_page_not_a_download_claim(workspace, url):
    value = observation()
    value['origin_page_url'] = url
    pdf, record = inputs(workspace, value=value)
    with pytest.raises(ValueError, match='invalid_observed_public_page'):
        local.register_local_document(pdf, record, workspace / 'not-created')
    assert not (workspace / 'not-created').exists()


@pytest.mark.parametrize('which', ['pdf', 'observation'])
def test_symlink_and_hardlink_inputs_are_not_read_as_new_originals(workspace, which):
    pdf, record = inputs(workspace)
    original = pdf if which == 'pdf' else record
    alias = workspace / ('alias.pdf' if which == 'pdf' else 'alias.json')
    alias.symlink_to(original)
    args = (alias, record) if which == 'pdf' else (pdf, alias)
    with pytest.raises(ValueError):
        local.register_local_document(*args, workspace / 'not-created')
    alias.unlink()
    os.link(original, alias)
    with pytest.raises(ValueError, match='single_link'):
        local.register_local_document(pdf, record, workspace / 'not-created')
    assert not (workspace / 'not-created').exists()


def test_outside_workspace_source_and_output_are_rejected_without_modifying_files(workspace, monkeypatch):
    inner = workspace / 'inner'
    inner.mkdir()
    pdf, record = inputs(workspace)
    monkeypatch.setattr(local, 'WORKSPACE', inner)
    with pytest.raises(ValueError, match='workspace_path_required'):
        local.register_local_document(pdf, record, inner / 'not-created')
    internal_pdf, internal_record = inputs(inner)
    with pytest.raises(ValueError, match='workspace_path_required'):
        local.register_local_document(internal_pdf, internal_record, workspace / 'outside-output')
    assert pdf.read_bytes() == PDF and not (workspace / 'outside-output').exists()


@pytest.mark.parametrize('existing', ['directory', 'file', 'broken_symlink'])
def test_existing_outputs_are_never_overwritten(workspace, existing):
    pdf, record = inputs(workspace)
    output = workspace / 'old'
    if existing == 'directory':
        output.mkdir()
        (output / 'keep').write_bytes(b'old evidence')
    elif existing == 'file':
        output.write_bytes(b'old evidence')
    else:
        output.symlink_to(workspace / 'missing')
    with pytest.raises((ValueError, FileExistsError)):
        local.register_local_document(pdf, record, output)
    if existing == 'directory':
        assert (output / 'keep').read_bytes() == b'old evidence'
    elif existing == 'file':
        assert output.read_bytes() == b'old evidence'
    else:
        assert output.is_symlink() and not output.exists()


def test_caps_pdf_header_and_invalid_extract_flag_fail_before_new_output(workspace):
    pdf, record = inputs(workspace, pdf=b'not a PDF')
    with pytest.raises(ValueError, match='pdf_header_required'):
        local.register_local_document(pdf, record, workspace / 'bad-header')
    pdf.write_bytes(b'%PDF-' + b'x' * local.MAX_PDF_BYTES)
    with pytest.raises(ValueError, match='bounded_single_link'):
        local.register_local_document(pdf, record, workspace / 'oversized')
    pdf.write_bytes(PDF)
    record.write_bytes(b' ' * (local.MAX_OBSERVATION_BYTES + 1))
    with pytest.raises(ValueError, match='bounded_single_link'):
        local.register_local_document(pdf, record, workspace / 'oversized-observation')
    with pytest.raises(ValueError, match='extract_flag'):
        local.register_local_document(pdf, record, workspace / 'invalid-flag', extract='yes')
    assert not any((workspace / name).exists() for name in ['bad-header', 'oversized', 'oversized-observation', 'invalid-flag'])


def real_pdf(text):
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    if text is not None:
        font = DictionaryObject({NameObject('/Type'): NameObject('/Font'),
            NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'):
            DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
        stream = DecodedStreamObject()
        stream.set_data(('BT /F1 12 Tf 10 10 Td (' + text + ') Tj ET').encode())
        page[NameObject('/Contents')] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


@pytest.mark.parametrize('text,status', [('Synthetic protocol target', 'complete'), (None, 'partial')])
def test_real_pdf_parser_is_reused_in_an_independent_manifest_without_promoting_review(workspace, text, status):
    raw = real_pdf(text)
    pdf, record = inputs(workspace, pdf=raw)
    before = (pdf.read_bytes(), record.read_bytes())
    output = workspace / 'registered'
    result = local.register_local_document(pdf, record, output, extract=True)
    report = result['extraction']
    assert report['status'] == status and report['page_count'] == 1
    assert report['source_sha256'] == sha(raw)
    assert report['review_status'] == 'needs_human_review' and report['eligible_for_forecast'] is False
    assert report['registration_manifest_sha256'] == result['registration_manifest_sha256']
    pages_raw = (output / report['pages_json']['path']).read_bytes()
    assert report['pages_json']['sha256'] == sha(pages_raw)
    parsed = json.loads(pages_raw)
    assert parsed['status'] == status and parsed['source_sha256'] == sha(raw)
    assert parsed['pages'][0]['page_index'] == 0 and parsed['pages'][0]['page_number'] == 1
    assert (parsed['pages'][0]['text'].strip() if text else parsed['pages'][0]['status'] == 'empty')
    assert sha((output / 'manifest.json').read_bytes()) == result['registration_manifest_sha256']
    assert json.loads((output / 'manifest.json').read_bytes()) == result['registration']
    assert result['registration']['registration_status'] == 'registered'
    assert (pdf.read_bytes(), record.read_bytes()) == before


def test_parser_failure_preserves_local_registration_original_and_failed_extraction(workspace):
    pdf, record = inputs(workspace)
    output = workspace / 'bad-pdf-structure'
    result = local.register_local_document(pdf, record, output, extract=True)
    assert result['registration']['registration_status'] == 'registered'
    assert result['extraction']['status'] == 'failed'
    pages = json.loads((output / result['extraction']['pages_json']['path']).read_bytes())
    assert pages['error_category'] == 'pdf_parse_failed' and pages['pages'] == []
    assert (output / 'original.pdf').read_bytes() == PDF
    assert sha((output / 'manifest.json').read_bytes()) == result['registration_manifest_sha256']


def load_cli():
    app = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location('local_registration_cli', app / 'scripts/register_trial_document.py')
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    return cli


@pytest.mark.parametrize('extract,text,expected', [(False, None, 0), (True, 'Synthetic protocol', 0), (True, None, 1)])
def test_actual_cli_main_complete_and_partial_exit_status_keep_all_source_bytes(workspace, capsys, extract, text, expected):
    cli = load_cli()
    pdf, record = inputs(workspace, pdf=real_pdf(text))
    original = (pdf.read_bytes(), record.read_bytes())
    output = workspace / 'cli-registration'
    args = ['--pdf', str(pdf), '--observation', str(record), '--output-dir', str(output)]
    if extract:
        args.append('--extract')
    assert cli.main(args) == expected
    printed = json.loads(capsys.readouterr().out)
    assert printed['registration_status'] == 'registered'
    assert printed['extraction_status'] == ('not_requested' if not extract else 'partial' if text is None else 'complete')
    assert printed['eligible_for_forecast'] is False and printed['human_scientific_reviews'] == 0
    assert printed['registration_manifest_sha256'] == sha((output / 'manifest.json').read_bytes())
    assert (pdf.read_bytes(), record.read_bytes()) == original


def test_cli_bad_observation_reports_fixed_error_without_raw_claim_and_creates_no_directory(workspace, capsys):
    cli = load_cli()
    value = observation()
    value['http_status'] = SECRET
    pdf, record = inputs(workspace, value=value)
    output = workspace / 'not-created'
    with pytest.raises(SystemExit) as error:
        cli.main(['--pdf', str(pdf), '--observation', str(record), '--output-dir', str(output)])
    assert error.value.code == 1
    printed = capsys.readouterr()
    assert SECRET not in printed.out + printed.err and not output.exists()


def test_module_import_has_only_standard_library_and_no_io_process_or_network_effect(workspace):
    app = Path(__file__).resolve().parents[1]
    program = '''import os,sys,importlib.abc
class NoHeavy(importlib.abc.MetaPathFinder):
 def find_spec(self,fullname,path=None,target=None):
  if fullname.split('.')[0] in {'fastapi','httpx','requests','sqlite3','pypdf'} or fullname.startswith('emeris_live.trial_documents'):
   raise RuntimeError('Local registration import must remain standard library and lazy')
sys.meta_path.insert(0,NoHeavy())
def guard(event,args):
 if event.startswith('socket.') or event in {'sqlite3.connect','subprocess.Popen','os.system','os.mkdir','os.remove','os.rename'}:
  raise RuntimeError('Import activity forbidden')
 if event=='open':
  mode,flags=args[1],args[2]
  if (isinstance(mode,str) and any(c in mode for c in 'wax+')) or (isinstance(flags,int) and flags&(os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND)):
   raise RuntimeError('Import write forbidden')
sys.addaudithook(guard)
sys.path.insert(0,sys.argv[1])
import emeris_live.local_trial_documents
'''
    result = subprocess.run([sys.executable, '-B', '-I', '-c', program, str(app)],
        cwd=workspace, capture_output=True, text=True, timeout=20,
        env={'PATH': os.defpath, 'PYTHONDONTWRITEBYTECODE': '1'})
    assert result.returncode == 0, result.stderr
    assert list(workspace.iterdir()) == []
