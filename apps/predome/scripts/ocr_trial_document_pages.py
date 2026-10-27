"""Explicit workspace-only offline OCR sidecars for selected original PDF pages."""
import argparse
import json
from pathlib import Path
import stat
import sys

WORKSPACE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKSPACE))
from emeris_live.trial_document_ocr import MAX_PDF_BYTES, ocr_missing_pages


def _input(path, maximum):
    resolved = path.resolve()
    if path.is_symlink() or not resolved.is_relative_to(WORKSPACE.resolve()) or not stat.S_ISREG(path.stat().st_mode):
        raise ValueError('workspace_regular_input_required')
    with resolved.open('rb') as source:
        raw = source.read(maximum + 1)
    if len(raw) > maximum:
        raise ValueError('input_size_limit')
    return raw


def _extraction_json(raw):
    def unique(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate_json_key')
            result[key] = value
        return result
    def invalid_constant(value):
        raise ValueError('nonfinite_json_value')
    return json.loads(raw, object_pairs_hook=unique, parse_constant=invalid_constant)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pdf', required=True, type=Path)
    parser.add_argument('--extraction', required=True, type=Path)
    parser.add_argument('--page', required=True, action='append', type=int,
                        help='Original PDF page number; repeat explicitly, maximum20')
    parser.add_argument('--output-dir', required=True, type=Path, help='New directory inside this workspace')
    args = parser.parse_args(argv)
    try:
        output = args.output_dir.resolve()
        if args.output_dir.is_symlink() or not output.is_relative_to(WORKSPACE.resolve()):
            raise ValueError('workspace_output_required')
        raw = _input(args.pdf, MAX_PDF_BYTES)
        extraction = _extraction_json(_input(args.extraction, 16 * 1024 * 1024))
        result = ocr_missing_pages(raw, extraction, output, tuple(args.page))
    except (OSError, ValueError, TypeError, RecursionError):
        parser.exit(1, 'Offline OCR input binding, page selection or new workspace output failed. Original files were not rewritten.\n')
    print(json.dumps({'status': result['status'], 'review_status': result['review_status'],
        'selected_pages': len(result['pages']),
        'ocr_completed_pages': sum(page['status'] == 'ocr_complete' for page in result['pages']),
        'original_extraction_modified': False, 'ocr_text_merged': False, 'network_calls': 0,
        'model_calls': 0, 'ledger_writes': 0}, ensure_ascii=False))
    return 0 if result['status'] == 'ocr_complete' else 1


if __name__ == '__main__':
    raise SystemExit(main())
