"""Register a workspace-local PDF and unverified browser-UI observation offline."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from emeris_live.local_trial_documents import register_local_document


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pdf', required=True, type=Path)
    parser.add_argument('--observation', required=True, type=Path)
    parser.add_argument('--output-dir', required=True, type=Path, help='New workspace directory; existing files are never overwritten')
    parser.add_argument('--extract', action='store_true', help='Additionally create independent page-indexed text; partial/failed extraction exits nonzero')
    args = parser.parse_args(argv)
    try:
        result = register_local_document(args.pdf, args.observation, args.output_dir, extract=args.extract)
    except (OSError, ValueError, TypeError, UnicodeError, RecursionError):
        parser.exit(1, 'Local registration input, observation or new workspace output failed. No HTTP response or source verification was inferred.\n')
    registered, extraction = result['registration'], result['extraction']
    print(json.dumps({'registration_status': registered['registration_status'],
        'registration_manifest_sha256': result['registration_manifest_sha256'],
        'review_status': registered['review_status'], 'identity_status': registered['identity_status'],
        'source_sha256': registered['original']['sha256'], 'source_bytes': registered['original']['bytes'],
        'extraction_status': extraction['status'] if extraction else 'not_requested',
        'page_count': extraction['page_count'] if extraction else None,
        'eligible_for_forecast': False, 'model_calls': 0, 'ledger_writes': 0,
        'human_scientific_reviews': 0}, ensure_ascii=False))
    return 0 if extraction is None or extraction['status'] == 'complete' else 1


if __name__ == '__main__':
    raise SystemExit(main())
