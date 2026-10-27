"""Prepare/verify an offline sealed cohort bundle, or invoke installed host import.

Preparation reads a stable existing DB/WAL byte copy in a temporary query-only
SQLite snapshot. It never executes a prediction, initializes a source/host
database or requests a timestamp.
Import requires the separately installed ArenaLedger host integration.
"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from emeris_live import arena_imports


def summary(payload):
    events = payload['source_events']
    return {'schema_version': payload['schema_version'], 'bundle_sha256': payload['bundle_sha256'],
        'source_cohort_id': payload['source_cohort_id'], 'source_ledger_sha256': payload['source_ledger_sha256'],
        'source_events': len(events), 'assigned_runs': sum(e['kind'] == 'slot_registered' for e in events),
        'completed_runs': sum(e['kind'] == 'run_completed' for e in events),
        'ai_citation_reviews': sum(e['kind'] == 'citation_review' for e in events),
        'human_reviews_imported': 0, 'results_imported': 0, 'provider_calls': 0,
        'clinical_prediction_validated': False,
        'proof_times_utc': {k: v['generated_at_utc'] for k, v in payload['proofs'].items()},
        'time_scope': 'Original source execution and attestation times; preparation/import is not a new prediction.'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    prepare = commands.add_parser('prepare', help='Read an isolated existing source ledger and public originals')
    prepare.add_argument('--source-data-dir', required=True, type=Path)
    prepare.add_argument('--registration-proof', required=True, type=Path)
    prepare.add_argument('--predictions-proof', required=True, type=Path)
    prepare.add_argument('--source-manifest', required=True, type=Path,
        help='predome-arena-source-assets-1 JSON: assets [{path, sha256, content_type, source_url}]')
    prepare.add_argument('--source-root', required=True, type=Path)
    prepare.add_argument('--output', required=True, type=Path)
    verify = commands.add_parser('verify', help='Offline signature, nonce, source-chain and contract verification')
    verify.add_argument('--bundle', required=True, type=Path)
    verify.add_argument('--sha256', required=True)
    apply = commands.add_parser('import', help='Invoke the separately installed host append-only integration')
    apply.add_argument('--data-dir', required=True, type=Path)
    apply.add_argument('--bundle', required=True, type=Path)
    apply.add_argument('--sha256', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'prepare':
            payload = arena_imports.prepare_bundle(args.source_data_dir,
                {'registration': args.registration_proof, 'predictions': args.predictions_proof},
                args.source_manifest, args.source_root, args.output)
            result = {**summary(payload), 'output': str(args.output.resolve()), 'host_ledger_writes': 0,
                'source_ledger_writes': 0, 'host_import_performed': False}
        elif args.command == 'verify':
            payload = arena_imports.verify_bundle(args.bundle, args.sha256)
            result = {**summary(payload), 'host_ledger_writes': 0, 'source_ledger_writes': 0,
                'host_import_performed': False}
        else:
            from emeris_live.arena_store import ArenaLedger
            if not callable(getattr(ArenaLedger, 'import_sealed_cohort', None)):
                raise ValueError('Host integration is not installed; no ledger was constructed or changed')
            path = args.data_dir / 'arena.sqlite3'
            if path.is_symlink() or not path.is_file():
                raise ValueError('An existing host ledger is required; import never initializes a host DB')
            result = ArenaLedger(args.data_dir).import_sealed_cohort(args.bundle, args.sha256)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (ValueError, OSError) as exc:
        parser.exit(2, str(exc) + '\n')


if __name__ == '__main__':
    raise SystemExit(main())
