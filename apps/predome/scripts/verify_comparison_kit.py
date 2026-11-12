"""Verify a public Comparison research kit offline without extracting files."""
import argparse
import hashlib
import json
from pathlib import Path
import stat
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from emeris_live.arena_comparison_kit import MAX_ZIP_BYTES, verify_kit


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kit', type=Path, help='Existing local five-file research-kit ZIP')
    parser.add_argument('--expected-sha256', '--sha256', dest='expected_sha256',
                        help='Optional externally obtained ZIP SHA-256; not an authority by itself')
    args = parser.parse_args(argv)
    try:
        info = args.kit.lstat()
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_ZIP_BYTES:
            raise ValueError('Bounded regular ZIP required')
        with args.kit.open('rb') as source:
            raw = source.read(MAX_ZIP_BYTES + 1)
        manifest = verify_kit(raw, expected_sha256=args.expected_sha256)
    except (OSError, ValueError, TypeError, KeyError):
        print('Comparison kit verification failed; no files were extracted or changed.', file=sys.stderr)
        return 1
    print(json.dumps({'status': 'public_comparison_kit_verified', 'schema_version': manifest['schema_version'],
        'target_id': manifest['target_id'], 'nct_id': manifest['nct_id'],
        'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw),
        'baseline_assigned_records': manifest['baseline_assigned_records'],
        'patent_assigned_rows': manifest['patent_assigned_rows'],
        'agent_assigned_records': manifest['agent_assigned_records'],
        'expected_sha256_supplied': args.expected_sha256 is not None, 'provider_calls': 0, 'ledger_writes': 0,
        'validation_scope': manifest['validation_scope'], 'independent_authentication': False,
        'independent_timestamp_attestation': False, 'clinical_prediction_validated': False},
        ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
