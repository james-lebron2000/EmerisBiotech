"""Only local render/OCR process boundaries are replaced; no binaries required."""
import base64
from copy import deepcopy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from emeris_live import trial_document_ocr as ocr


PDF = b'%PDF-1.7\nSynthetic local OCR transport fixture, not a clinical document'
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jN1sAAAAASUVORK5CYII=')
TEXT = b'Primary estimand\n'
HEADER = b'level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n'
TSV = HEADER + (b'1\t1\t0\t0\t0\t0\t0\t0\t1\t1\t-1\t\n'
                b'5\t1\t1\t1\t1\t1\t0\t0\t1\t1\t88.5\tPrimary\n'
                b'5\t1\t1\t1\t1\t2\t0\t0\t1\t1\t43.5\testimand\n')
SECRET = 'FIXTURE_RAW_PROCESS_ERROR_MUST_NOT_BE_PERSISTED'


def extraction(count=3):
    return {'schema_version': 'predome.pdf-pages-1',
            'source_sha256': hashlib.sha256(PDF).hexdigest(), 'source_bytes': len(PDF),
            'status': 'partial', 'page_count': count,
            'pages': [{'page_index': i, 'page_number': i + 1, 'status': 'empty', 'text': ''} for i in range(count)],
            'warnings': ['empty_pages_may_be_blank_or_scanned'], 'error_category': None}


@pytest.fixture(autouse=True)
def no_real_tools(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Tests must not require installed poppler/tesseract or call the actual tools')
    monkeypatch.setattr(ocr, '_run_tool', forbidden)


def fixture_tools(monkeypatch, *, png=PNG, text=TEXT, tsv=TSV, render_code=0,
                  ocr_code=0, render_error=None, ocr_error=None, version_error=False):
    calls = []
    monkeypatch.setattr(ocr, '_tool_path', lambda name: '/fixture-tools/' + name)

    def run(argv, *, input_bytes, directory, timeout, file_limit, version=False):
        calls.append({'argv': argv, 'input': input_bytes, 'directory': directory,
                      'timeout': timeout, 'file_limit': file_limit, 'version': version})
        if version:
            name = Path(argv[0]).name
            raw = (name + (' version25.10.0' if name == 'pdftoppm' else '5.5.2') + '\n').encode()
            return subprocess.CompletedProcess(argv, 1 if version_error else 0, raw)
        if Path(argv[0]).name == 'pdftoppm':
            if png is not None:
                Path(argv[-1]).with_suffix('.png').write_bytes(png)
            if render_error:
                raise ocr._ToolError(render_error)
            return subprocess.CompletedProcess(argv, render_code)
        prefix = Path(argv[2])
        if text is not None:
            prefix.with_suffix('.txt').write_bytes(text)
        if tsv is not None:
            prefix.with_suffix('.tsv').write_bytes(tsv)
        if ocr_error:
            raise ocr._ToolError(ocr_error)
        return subprocess.CompletedProcess(argv, ocr_code)

    monkeypatch.setattr(ocr, '_run_tool', run)
    return calls


def test_explicit_missing_pages_produce_hashed_unreviewed_sidecars_without_changing_source(tmp_path, monkeypatch):
    calls = fixture_tools(monkeypatch)
    source = extraction()
    source['pages'][1].update(status='text', text='Already extracted original text')
    original = deepcopy(source)
    output = tmp_path / 'ocr-sidecar'
    result = ocr.ocr_missing_pages(PDF, source, output, [3, 1])
    assert source == original
    assert result['schema_version'] == 'predome.trial-document-ocr-1'
    assert result['status'] == 'ocr_complete' and result['review_status'] == 'needs_human_review'
    assert result['selected_page_numbers'] == [3, 1]
    assert result['original_extraction_status'] == 'partial'
    assert result['original_extraction_modified'] is result['ocr_text_merged'] is False
    assert result['clinical_fields_extracted'] is False
    assert result['network_calls'] == result['model_calls'] == result['ledger_writes'] == 0
    assert result['human_review_performed'] is False
    assert len(calls) == 6
    for number, row in zip([3, 1], result['pages']):
        assert row['page_number'] == number and row['page_index'] == number - 1
        assert row['source_sha256'] == hashlib.sha256(PDF).hexdigest()
        assert row['review_status'] == 'needs_human_review'
        assert row['engine_word_count'] == 2
        assert row['engine_low_confidence_word_count'] == 1
        assert row['engine_unscored_word_count'] == 0
        for role, raw in [('png', PNG), ('text', TEXT), ('tsv', TSV)]:
            artifact = row['artifacts'][role]
            assert (output / artifact['path']).read_bytes() == raw
            assert artifact['sha256'] == hashlib.sha256(raw).hexdigest()
            assert artifact['bytes'] == len(raw)
            assert (output / artifact['path']).stat().st_mode & 0o777 == 0o600
    renders = [call for call in calls if not call['version'] and Path(call['argv'][0]).name == 'pdftoppm']
    assert [call['argv'][2] for call in renders] == ['3', '1']
    assert all(call['argv'][3:10] == ['-l', call['argv'][2], '-r', '200', '-singlefile', '-png', '-'] for call in renders)
    assert all(call['input'] == PDF and call['timeout'] == 30 for call in renders)
    recognizers = [call for call in calls if not call['version'] and Path(call['argv'][0]).name == 'tesseract']
    assert all(call['argv'][3:] == ['-l', 'eng', '--psm', '6', 'txt', 'tsv'] for call in recognizers)
    assert all(call['input'] is None and call['timeout'] == 30 for call in recognizers)
    assert json.loads((output / 'manifest.json').read_bytes()) == result
    assert 'accuracy' in result['engine_confidence_note']
    assert result['tools']['tesseract']['version'] == 'tesseract5.5.2'


@pytest.mark.parametrize('mutate', [
    lambda x: x.update(source_sha256='0' * 64),
    lambda x: x.update(source_bytes=True),
    lambda x: x.update(source_bytes=len(PDF) + 1),
    lambda x: x.update(page_count=401),
    lambda x: x.update(page_count=True),
    lambda x: x['pages'].pop(),
    lambda x: x['pages'][1].update(page_number=1),
    lambda x: x['pages'][0].update(page_index=True),
    lambda x: x['pages'][0].update(status='invented'),
    lambda x: x['pages'][0].update(status='empty', text='Nonempty text hidden behind wrong status'),
    lambda x: x.update(status='complete'),
])
def test_invalid_source_or_original_index_fails_before_creating_any_output(tmp_path, mutate):
    source = extraction()
    mutate(source)
    output = tmp_path / 'invalid'
    with pytest.raises(ValueError):
        ocr.ocr_missing_pages(PDF, source, output, [1])
    assert not output.exists()


@pytest.mark.parametrize('pages', [[], (), [True], [0], [4], [-1], ['1'], [1, 1], list(range(1, 22)), '1'])
def test_page_selection_is_explicit_unique_bounded_and_in_range(tmp_path, pages):
    output = tmp_path / 'invalid-selection'
    with pytest.raises(ValueError):
        source = extraction(30 if type(pages) is list and len(pages) > 20 else 3)
        ocr.ocr_missing_pages(PDF, source, output, pages)
    assert not output.exists()


def test_text_page_is_not_silently_reocrd_and_pdf_size_header_are_checked(tmp_path):
    source = extraction()
    source['pages'][0].update(status='text', text='Already extracted')
    with pytest.raises(ValueError, match='already_has_text'):
        ocr.ocr_missing_pages(PDF, source, tmp_path / 'text', [1])
    for raw in [b'html', b'%PDF-' + b'x' * ocr.MAX_PDF_BYTES]:
        with pytest.raises(ValueError, match='invalid_pdf_input'):
            ocr.ocr_missing_pages(raw, extraction(), tmp_path / 'bad-pdf', [1])
    assert list(tmp_path.iterdir()) == []


def test_existing_directory_or_symlink_is_never_overwritten(tmp_path):
    output = tmp_path / 'old'
    output.mkdir()
    original = output / 'keep'
    original.write_bytes(b'old evidence')
    with pytest.raises(FileExistsError):
        ocr.ocr_missing_pages(PDF, extraction(), output, [1])
    link = tmp_path / 'link'
    link.symlink_to(output, target_is_directory=True)
    with pytest.raises(FileExistsError):
        ocr.ocr_missing_pages(PDF, extraction(), link, [1])
    assert original.read_bytes() == b'old evidence' and list(output.iterdir()) == [original]


def test_missing_tools_preserve_honest_failed_manifest_without_any_attempt(tmp_path, monkeypatch):
    monkeypatch.setattr(ocr, '_tool_path', lambda name: None)
    output = tmp_path / 'missing-tools'
    result = ocr.ocr_missing_pages(PDF, extraction(), output, [1, 3])
    assert result['status'] == 'failed'
    assert all(tool['status'] == 'unavailable' and tool['version'] is None for tool in result['tools'].values())
    assert all(row['error_category'] == 'required_tool_unavailable' for row in result['pages'])
    assert all(row['review_status'] == 'needs_human_review' for row in result['pages'])
    assert json.loads((output / 'manifest.json').read_bytes()) == result


def test_version_failure_is_not_hidden_by_unversioned_ocr(tmp_path, monkeypatch):
    calls = fixture_tools(monkeypatch, version_error=True)
    result = ocr.ocr_missing_pages(PDF, extraction(), tmp_path / 'bad-version', [1])
    assert len(calls) == 2 and all(call['version'] for call in calls)
    assert result['status'] == 'failed'
    assert result['pages'][0]['error_category'] == 'required_tool_unavailable'


@pytest.mark.parametrize('options,error', [
    ({'png': None}, 'render_output_missing'), ({'png': b''}, 'render_output_empty'),
    ({'png': b'not a PNG'}, 'rendered_png_invalid'),
    ({'render_code': 1}, 'render_failed'),
    ({'render_error': 'tool_timeout'}, 'render_tool_timeout'),
    ({'text': None}, 'ocr_output_missing'), ({'text': b''}, 'ocr_output_empty'),
    ({'text': b' \n'}, 'ocr_no_text'), ({'tsv': b''}, 'ocr_output_empty'),
    ({'tsv': HEADER}, 'ocr_no_words'), ({'tsv': b'wrong\theader\n'}, 'ocr_tsv_or_text_invalid'),
    ({'tsv': TSV.replace(b'88.5', b'NaN')}, 'ocr_tsv_or_text_invalid'),
    ({'tsv': TSV.replace(b'5\t1\t1', b'5\t2\t1')}, 'ocr_tsv_or_text_invalid'),
    ({'ocr_code': 1}, 'ocr_failed'), ({'ocr_error': 'tool_timeout'}, 'ocr_tool_timeout'),
])
def test_failure_empty_no_words_and_malformed_tsv_are_never_success(tmp_path, monkeypatch, options, error):
    fixture_tools(monkeypatch, **options)
    output = tmp_path / 'failure'
    result = ocr.ocr_missing_pages(PDF, extraction(), output, [1])
    row = result['pages'][0]
    assert row['status'] == 'failed' and row['error_category'] == error
    assert result['status'] == 'failed' and result['review_status'] == 'needs_human_review'
    assert result['original_extraction_modified'] is False
    for artifact in row['artifacts'].values():
        if artifact is not None:
            assert hashlib.sha256((output / artifact['path']).read_bytes()).hexdigest() == artifact['sha256']


def test_partial_outputs_from_timeouts_are_retained_and_hashed_but_not_accepted(tmp_path, monkeypatch):
    fixture_tools(monkeypatch, ocr_error='tool_timeout')
    output = tmp_path / 'timeout'
    result = ocr.ocr_missing_pages(PDF, extraction(), output, [1])
    row = result['pages'][0]
    assert row['status'] == 'failed' and row['error_category'] == 'ocr_tool_timeout'
    assert all(row['artifacts'][key] is not None for key in ['png', 'text', 'tsv'])
    assert row['engine_word_count'] is None


@pytest.mark.parametrize('role,options,cap', [
    ('png', {'png': PNG * 2}, 'MAX_PNG_BYTES'),
    ('text', {'text': TEXT * 2}, 'MAX_TEXT_BYTES'),
    ('tsv', {'tsv': TSV * 2}, 'MAX_TSV_BYTES')])
def test_practical_artifact_caps_are_checked_before_reading_as_valid(tmp_path, monkeypatch, role, options, cap):
    fixture_tools(monkeypatch, **options)
    monkeypatch.setattr(ocr, cap, 5)
    output = tmp_path / 'cap'
    result = ocr.ocr_missing_pages(PDF, extraction(), output, [1])
    assert result['status'] == 'failed'
    assert result['pages'][0]['error_category'].endswith('output_size_limit')
    assert result['pages'][0]['artifacts'][role] is None


def test_artifact_symlink_is_not_read_even_if_target_is_a_png(tmp_path, monkeypatch):
    calls = fixture_tools(monkeypatch)
    outside = tmp_path / 'outside.png'
    outside.write_bytes(PNG)
    normal = ocr._run_tool
    def linked(argv, **kwargs):
        if not kwargs.get('version', False) and Path(argv[0]).name == 'pdftoppm':
            Path(argv[-1]).with_suffix('.png').symlink_to(outside)
            return subprocess.CompletedProcess(argv, 0)
        return normal(argv, **kwargs)
    monkeypatch.setattr(ocr, '_run_tool', linked)
    result = ocr.ocr_missing_pages(PDF, extraction(), tmp_path / 'linked', [1])
    assert result['status'] == 'failed'
    assert result['pages'][0]['error_category'] == 'render_output_not_regular'
    assert outside.read_bytes() == PNG


def test_minimal_child_environment_exec_limit_wrapper_and_no_raw_timeout(monkeypatch, tmp_path):
    monkeypatch.setenv('SEM_API_KEY', SECRET)
    original = importlib.import_module('emeris_live.trial_document_ocr')
    # The autouse guard replaces this boundary; reload only its original function
    # body from this module's file under a separate name, without executing tools.
    spec = importlib.util.spec_from_file_location('ocr_run_boundary', Path(original.__file__))
    boundary = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(boundary)
    def timeout(argv, **kwargs):
        assert argv[:4] == [sys.executable, '-B', '-I', '-c']
        assert 'RLIMIT_FSIZE' in argv[4] and 'os.execv' in argv[4]
        assert argv[5:] == ['12345', '/fixture/pdftoppm', '-v']
        assert kwargs['env'] == {'PATH': ocr.TOOL_PATH, 'LANG': 'C.UTF-8', 'TMPDIR': str(tmp_path / 'tool-tmp')}
        assert kwargs['timeout'] == 30 and kwargs['check'] is False
        assert kwargs['stderr'] is subprocess.DEVNULL
        assert SECRET not in json.dumps(kwargs['env'])
        raise subprocess.TimeoutExpired(argv, 30, output=SECRET, stderr=SECRET)
    monkeypatch.setattr(boundary.subprocess, 'run', timeout)
    with pytest.raises(boundary._ToolError, match='tool_timeout') as caught:
        boundary._run_tool(['/fixture/pdftoppm', '-v'], input_bytes=PDF,
            directory=tmp_path, timeout=30, file_limit=12345)
    assert SECRET not in str(caught.value)


def load_cli(monkeypatch, workspace):
    app = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location('ocr_cli_under_test', app / 'scripts/ocr_trial_document_pages.py')
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    monkeypatch.setattr(cli, 'WORKSPACE', workspace)
    return cli


def test_actual_cli_main_keeps_pdf_extraction_bytes_unchanged_and_prints_no_text(tmp_path, monkeypatch, capsys):
    fixture_tools(monkeypatch)
    cli = load_cli(monkeypatch, tmp_path)
    pdf = tmp_path / 'source.pdf'
    indexed = tmp_path / 'source.pages.json'
    pdf.write_bytes(PDF)
    indexed.write_text(json.dumps(extraction()))
    before = [path.read_bytes() for path in [pdf, indexed]]
    result = cli.main(['--pdf', str(pdf), '--extraction', str(indexed), '--page', '1', '--page', '3',
                       '--output-dir', str(tmp_path / 'sidecar')])
    assert result == 0
    output = json.loads(capsys.readouterr().out)
    assert output['status'] == 'ocr_complete' and output['review_status'] == 'needs_human_review'
    assert output['ocr_completed_pages'] == 2
    assert 'Primary' not in json.dumps(output)
    assert [path.read_bytes() for path in [pdf, indexed]] == before


def test_cli_requires_explicit_pages_and_rejects_external_paths_and_json_duplicates(tmp_path, monkeypatch):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    cli = load_cli(monkeypatch, workspace)
    external = tmp_path / 'external.pdf'
    external.write_bytes(PDF)
    indexed = workspace / 'source.json'
    indexed.write_text(json.dumps(extraction()))
    with pytest.raises(SystemExit) as missing:
        cli.main(['--pdf', str(external), '--extraction', str(indexed), '--output-dir', str(workspace / 'new')])
    assert missing.value.code == 2
    with pytest.raises(SystemExit) as outside:
        cli.main(['--pdf', str(external), '--extraction', str(indexed), '--page', '1', '--output-dir', str(workspace / 'new')])
    assert outside.value.code == 1
    pdf = workspace / 'source.pdf'
    pdf.write_bytes(PDF)
    indexed.write_bytes(b'{"x":1,"x":2}')
    with pytest.raises(SystemExit) as duplicate:
        cli.main(['--pdf', str(pdf), '--extraction', str(indexed), '--page', '1', '--output-dir', str(workspace / 'new')])
    assert duplicate.value.code == 1 and not (workspace / 'new').exists()


def test_module_import_has_no_filesystem_network_database_or_process_activity(tmp_path):
    app = Path(__file__).resolve().parents[1]
    program = '''import os,sys
def guard(event,args):
 if event.startswith('socket.') or event in {'sqlite3.connect','subprocess.Popen','os.system','os.mkdir','os.remove','os.rename'}:
  raise RuntimeError('import activity forbidden')
 if event=='open':
  mode,flags=args[1],args[2]
  if (isinstance(mode,str) and any(c in mode for c in 'wax+')) or (isinstance(flags,int) and flags&(os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND)):
   raise RuntimeError('import write forbidden')
sys.addaudithook(guard)
sys.path.insert(0,sys.argv[1])
import emeris_live.trial_document_ocr
'''
    process = subprocess.run([sys.executable, '-B', '-I', '-c', program, str(app)],
        cwd=tmp_path, capture_output=True, text=True, timeout=20,
        env={'PATH': os.defpath, 'PYTHONDONTWRITEBYTECODE': '1'})
    assert process.returncode == 0, process.stderr
    assert list(tmp_path.iterdir()) == []
