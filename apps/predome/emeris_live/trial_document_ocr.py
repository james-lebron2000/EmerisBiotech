"""Explicit offline OCR sidecars for selected, unextracted original PDF pages.

OCR completion never completes the original text extraction or verifies source
content. Every result remains needs_human_review, including apparently clear text.
"""
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import struct
import subprocess
import sys


VERSION = 'predome.trial-document-ocr-1'
MAX_PDF_BYTES = 16 * 1024 * 1024
MAX_PAGES = 400
MAX_SELECTED_PAGES = 20
MAX_PNG_BYTES = 32 * 1024 * 1024
MAX_TEXT_BYTES = 1024 * 1024
MAX_TSV_BYTES = 4 * 1024 * 1024
MAX_IMAGE_PIXELS = 50_000_000
STEP_TIMEOUT_SECONDS = 30
TOOL_PATH = '/opt/homebrew/bin:/usr/bin:/bin'
LANGUAGE = 'eng'
PSM = 6
DPI = 200
LOW_CONFIDENCE_THRESHOLD = 50.0
_TSV_HEADER = ['level', 'page_num', 'block_num', 'par_num', 'line_num',
               'word_num', 'left', 'top', 'width', 'height', 'conf', 'text']
_LIMIT_PROGRAM = '''import os,resource,sys
limit=int(sys.argv[1])
resource.setrlimit(resource.RLIMIT_FSIZE,(limit,limit))
resource.setrlimit(resource.RLIMIT_CPU,(28,28))
if sys.platform.startswith('linux'):
 resource.setrlimit(resource.RLIMIT_AS,(1073741824,1073741824))
os.execv(sys.argv[2],sys.argv[2:])
'''


class _ToolError(Exception):
    def __init__(self, category):
        self.category = category
        super().__init__(category)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + '\n').encode()


def _write_new(path, raw):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'wb') as output:
        output.write(raw)


def _validate_inputs(raw, extraction, page_numbers):
    if type(raw) is not bytes or len(raw) > MAX_PDF_BYTES or not raw.startswith(b'%PDF-'):
        raise ValueError('invalid_pdf_input')
    if (type(extraction) is not dict or extraction.get('schema_version') != 'predome.pdf-pages-1'
            or extraction.get('source_sha256') != _sha(raw)
            or type(extraction.get('source_bytes')) is not int or extraction['source_bytes'] != len(raw)):
        raise ValueError('extraction_source_binding_invalid')
    count, pages = extraction.get('page_count'), extraction.get('pages')
    if (type(count) is not int or not 1 <= count <= MAX_PAGES
            or type(pages) is not list or len(pages) != count):
        raise ValueError('extraction_page_index_invalid')
    known = {'text', 'empty', 'page_extraction_failed', 'text_limit_exceeded', 'not_extracted_text_limit'}
    for index, page in enumerate(pages):
        if (type(page) is not dict or type(page.get('page_index')) is not int or page['page_index'] != index
                or type(page.get('page_number')) is not int or page['page_number'] != index + 1
                or type(page.get('status')) is not str or page['status'] not in known):
            raise ValueError('extraction_page_index_invalid')
        text = page.get('text')
        if page['status'] in ('text', 'empty'):
            if type(text) is not str or (page['status'] == 'text') != bool(text.strip()):
                raise ValueError('extraction_page_status_invalid')
        elif text is not None:
            raise ValueError('extraction_page_status_invalid')
    if (type(page_numbers) not in (tuple, list) or not 1 <= len(page_numbers) <= MAX_SELECTED_PAGES
            or any(type(page) is not int or not 1 <= page <= count for page in page_numbers)
            or len(set(page_numbers)) != len(page_numbers)):
        raise ValueError('explicit_page_selection_invalid')
    if any(pages[number - 1]['status'] == 'text' for number in page_numbers):
        raise ValueError('selected_page_already_has_text')
    if extraction.get('status') != 'partial':
        raise ValueError('extraction_status_inconsistent_with_missing_pages')


def _tool_path(name):
    return shutil.which(name, path=TOOL_PATH)


def _run_tool(argv, *, input_bytes, directory, timeout, file_limit, version=False):
    """Single bounded process; a limit wrapper execs the fixed local executable.

    The wrapper retains stdin for pdftoppm, constrains regular output-file sizes,
    and execs in-place so timeout terminates the tool itself. No shell or ambient
    credentials/configuration are inherited. Temporary files stay in this run.
    """
    environment = {'PATH': TOOL_PATH, 'LANG': 'C.UTF-8', 'TMPDIR': str(directory / 'tool-tmp')}
    command = [sys.executable, '-B', '-I', '-c', _LIMIT_PROGRAM, str(file_limit), *argv]
    try:
        process = subprocess.run(command, input=input_bytes,
            cwd=directory, env=environment, timeout=timeout, check=False,
            stdout=subprocess.PIPE if version else subprocess.DEVNULL,
            stderr=subprocess.STDOUT if version else subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        raise _ToolError('tool_timeout') from None
    except OSError:
        raise _ToolError('tool_unavailable') from None
    return process


def _version(name, path, output):
    result = {'path': path, 'version': None, 'status': 'unavailable'}
    if path is None:
        return result
    args = [path, '-v' if name == 'pdftoppm' else '--version']
    try:
        process = _run_tool(args, input_bytes=None, directory=output,
                            timeout=5, file_limit=8192, version=True)
    except _ToolError as error:
        result['status'] = error.category
        return result
    raw = process.stdout or b''
    if process.returncode != 0 or not raw or len(raw) > 8192:
        result['status'] = 'version_unavailable'
        return result
    try:
        line = raw.decode('utf-8').splitlines()[0]
    except (UnicodeError, IndexError):
        result['status'] = 'version_unavailable'
        return result
    if not re.fullmatch(re.escape(name) + r'[ A-Za-z0-9._+()/,:-]{1,180}', line, re.ASCII):
        result['status'] = 'version_unavailable'
        return result
    result.update(version=line, status='available')
    return result


def _artifact(path, output, maximum):
    if not path.exists() and not path.is_symlink():
        return None, None, 'output_missing'
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not path.resolve().is_relative_to(output):
        return None, None, 'output_not_regular'
    if info.st_size > maximum:
        return None, None, 'output_size_limit'
    with path.open('rb') as source:
        raw = source.read(maximum + 1)
    if len(raw) > maximum:
        return None, None, 'output_size_limit'
    path.chmod(0o600)
    record = {'path': path.relative_to(output).as_posix(), 'sha256': _sha(raw), 'bytes': len(raw)}
    return record, raw, 'output_empty' if not raw else None


def _png_dimensions(raw):
    if (len(raw) < 45 or raw[:8] != b'\x89PNG\r\n\x1a\n' or raw[12:16] != b'IHDR'
            or raw[-12:] != b'\x00\x00\x00\x00IEND\xaeB`\x82'):
        raise ValueError('rendered_png_invalid')
    width, height = struct.unpack('>II', raw[16:24])
    if width == 0 or height == 0 or width * height > MAX_IMAGE_PIXELS:
        raise ValueError('rendered_image_dimensions_invalid')
    return width, height


def _tsv_counts(raw):
    try:
        reader = csv.reader(io.StringIO(raw.decode('utf-8')), delimiter='\t', quoting=csv.QUOTE_NONE)
        if next(reader) != _TSV_HEADER:
            raise ValueError('ocr_tsv_invalid')
        words = low = unscored = 0
        for row in reader:
            if len(row) != 12:
                raise ValueError('ocr_tsv_invalid')
            numbers = []
            for value in row[:10]:
                if not re.fullmatch(r'[0-9]{1,9}', value, re.ASCII):
                    raise ValueError('ocr_tsv_invalid')
                numbers.append(int(value))
            if not 1 <= numbers[0] <= 5 or numbers[1] != 1:
                raise ValueError('ocr_tsv_invalid')
            confidence = float(row[10])
            if not math.isfinite(confidence) or confidence < -1 or confidence > 100:
                raise ValueError('ocr_tsv_invalid')
            if numbers[0] == 5 and row[11].strip():
                words += 1
                unscored += confidence < 0
                low += 0 <= confidence < LOW_CONFIDENCE_THRESHOLD
    except (UnicodeError, StopIteration, csv.Error, OverflowError) as error:
        raise ValueError('ocr_tsv_invalid') from None
    return {'engine_word_count': words, 'engine_low_confidence_word_count': low,
            'engine_unscored_word_count': unscored}


def ocr_missing_pages(pdf_bytes, extraction, output_dir, page_numbers):
    """Create an unreviewed sidecar; never merge or rewrite original extraction."""
    _validate_inputs(pdf_bytes, extraction, page_numbers)
    extraction_sha = _sha(_json_bytes(extraction))
    output = Path(output_dir)
    output.mkdir(mode=0o700, parents=False, exist_ok=False)
    output = output.resolve()
    (output / 'tool-tmp').mkdir(mode=0o700)
    source_sha = _sha(pdf_bytes)
    manifest = {'schema_version': VERSION, 'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'source_sha256': source_sha, 'source_bytes': len(pdf_bytes),
        'extraction_sha256': extraction_sha,
        'extraction_hash_scope': 'canonical_json_of_supplied_extraction',
        'original_extraction_status': extraction.get('status'), 'original_page_count': extraction['page_count'],
        'selected_page_numbers': list(page_numbers), 'status': 'failed',
        'review_status': 'needs_human_review', 'language': LANGUAGE, 'psm': PSM, 'dpi': DPI,
        'engine_confidence_note': 'Tesseract engine scores are not transcription accuracy or scientific validation.',
        'low_confidence_threshold': LOW_CONFIDENCE_THRESHOLD,
        'tsv_page_number_scope': 'page_num is1 for each separately rendered image; page_number is the original PDF page.',
        'tools': {}, 'pages': [], 'network_calls': 0, 'model_calls': 0, 'ledger_writes': 0,
        'human_review_performed': False, 'original_extraction_modified': False,
        'ocr_text_merged': False, 'clinical_fields_extracted': False,
        'limits': {'max_selected_pages': MAX_SELECTED_PAGES, 'step_timeout_seconds': STEP_TIMEOUT_SECONDS,
                   'max_png_bytes': MAX_PNG_BYTES, 'max_text_bytes': MAX_TEXT_BYTES, 'max_tsv_bytes': MAX_TSV_BYTES}}
    for tool in ('pdftoppm', 'tesseract'):
        manifest['tools'][tool] = _version(tool, _tool_path(tool), output)
    tools_ready = all(tool['status'] == 'available' for tool in manifest['tools'].values())
    for number in page_numbers:
        directory = output / ('page-' + str(number).zfill(3))
        directory.mkdir(mode=0o700)
        row = {'page_number': number, 'page_index': number - 1, 'source_sha256': source_sha,
               'original_extraction_status': extraction['pages'][number - 1]['status'],
               'status': 'failed', 'review_status': 'needs_human_review', 'error_category': None,
               'artifacts': {'png': None, 'text': None, 'tsv': None},
               'image_width': None, 'image_height': None,
               'engine_word_count': None, 'engine_low_confidence_word_count': None,
               'engine_unscored_word_count': None}
        manifest['pages'].append(row)
        if not tools_ready:
            row['error_category'] = 'required_tool_unavailable'
            continue
        prefix = directory / 'rendered'
        render = [manifest['tools']['pdftoppm']['path'], '-f', str(number), '-l', str(number),
                  '-r', str(DPI), '-singlefile', '-png', '-', str(prefix)]
        try:
            process = _run_tool(render, input_bytes=pdf_bytes, directory=output,
                timeout=STEP_TIMEOUT_SECONDS, file_limit=MAX_PNG_BYTES)
        except _ToolError as error:
            row['artifacts']['png'] = _artifact(prefix.with_suffix('.png'), output, MAX_PNG_BYTES)[0]
            row['error_category'] = 'render_' + error.category
            continue
        artifact, png, error = _artifact(prefix.with_suffix('.png'), output, MAX_PNG_BYTES)
        row['artifacts']['png'] = artifact
        if process.returncode != 0 or error:
            row['error_category'] = 'render_failed' if process.returncode != 0 else 'render_' + error
            continue
        try:
            row['image_width'], row['image_height'] = _png_dimensions(png)
        except ValueError:
            row['error_category'] = 'rendered_png_invalid'
            continue
        prefix = directory / 'ocr'
        command = [manifest['tools']['tesseract']['path'], str(directory / 'rendered.png'), str(prefix),
                   '-l', LANGUAGE, '--psm', str(PSM), 'txt', 'tsv']
        try:
            process = _run_tool(command, input_bytes=None, directory=output,
                timeout=STEP_TIMEOUT_SECONDS, file_limit=MAX_TSV_BYTES)
        except _ToolError as error:
            row['artifacts']['text'] = _artifact(prefix.with_suffix('.txt'), output, MAX_TEXT_BYTES)[0]
            row['artifacts']['tsv'] = _artifact(prefix.with_suffix('.tsv'), output, MAX_TSV_BYTES)[0]
            row['error_category'] = 'ocr_' + error.category
            continue
        text_record, text, text_error = _artifact(prefix.with_suffix('.txt'), output, MAX_TEXT_BYTES)
        tsv_record, tsv, tsv_error = _artifact(prefix.with_suffix('.tsv'), output, MAX_TSV_BYTES)
        row['artifacts'].update(text=text_record, tsv=tsv_record)
        if process.returncode != 0 or text_error or tsv_error:
            row['error_category'] = 'ocr_failed' if process.returncode != 0 else 'ocr_' + (text_error or tsv_error)
            continue
        try:
            if not text.decode('utf-8').strip():
                row['error_category'] = 'ocr_no_text'
                continue
            counts = _tsv_counts(tsv)
        except (UnicodeError, ValueError):
            row['error_category'] = 'ocr_tsv_or_text_invalid'
            continue
        row.update(counts)
        if counts['engine_word_count'] == 0:
            row['error_category'] = 'ocr_no_words'
            continue
        row['status'] = 'ocr_complete'
    completed = sum(row['status'] == 'ocr_complete' for row in manifest['pages'])
    manifest['status'] = 'ocr_complete' if completed == len(page_numbers) else 'partial' if completed else 'failed'
    manifest['completed_at_utc'] = datetime.now(timezone.utc).isoformat()
    _write_new(output / 'manifest.json', _json_bytes(manifest))
    return manifest
