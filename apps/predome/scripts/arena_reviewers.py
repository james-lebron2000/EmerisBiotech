"""Operator-only enrollment, revocation and listing for existing Arena accounts.

No HTTP enrollment endpoint, account creation, model call or scientific approval.
The identity evidence file stays local: only its SHA-256 enters the event ledger.
The server's operating-system operator enrolls an account/principal association;
this does not establish clinical credentials or independent scientific review.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from emeris_live.arena_review_auth import (
    account_id_for_username, enroll_reviewer, list_enrollments, read_enrollment_events,
)
from emeris_live.arena_store import ArenaLedger


MAX_IDENTITY_EVIDENCE_BYTES = 8 * 1024 * 1024
PUBLIC_ERROR = 'Reviewer operation rejected; check arguments, existing runtime and current enrollment.\n'
OUTPUT_FIELDS = ('account_id', 'principal_id', 'target_ids', 'active', 'event_hash',
                 'identity_evidence_sha256', 'operator_uid', 'enrolled_at', 'identity_assurance', 'role',
                 'clinical_credentials_verified', 'scientific_independence_verified')


class PrivateParser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, PUBLIC_ERROR)


def identity_evidence_hash(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= MAX_IDENTITY_EVIDENCE_BYTES:
        raise ValueError('A bounded regular identity evidence file is required')
    digest, size = hashlib.sha256(), 0
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            size += len(block)
            if size > MAX_IDENTITY_EVIDENCE_BYTES:
                raise ValueError('Identity evidence exceeded its bound')
            digest.update(block)
    if not size:
        raise ValueError('Identity evidence is empty')
    return digest.hexdigest()


def operate(args):
    root = Path(args.data_dir)
    # Verify the existing databases before any write connection. Deliberately
    # avoid constructors that initialize schemas, directories or account state.
    events = read_enrollment_events(root)
    workbench = root / 'workbench.sqlite3'
    if workbench.is_symlink() or not workbench.is_file():
        raise ValueError('An existing workbench database is required')
    current = list_enrollments(events)
    if args.action == 'list':
        if args.username is not None:
            account = account_id_for_username(root, args.username)
            current = [row for row in current if row['account_id'] == account]
        return {'status': 'operator_enrollments', 'enrollments': [
            {key: row[key] for key in OUTPUT_FIELDS} for row in current],
            'ledger_writes': 0, 'provider_calls': 0, 'scientific_approval_created': False}
    account = account_id_for_username(root, args.username)
    evidence = identity_evidence_hash(args.identity_evidence_file)
    ledger = object.__new__(ArenaLedger)
    ledger.path = root / 'arena.sqlite3'
    event = enroll_reviewer(ledger, account, args.principal, args.target or [], evidence, args.note,
                            active=args.action == 'enroll', supersedes=args.supersedes)
    return {'status': 'enrolled' if args.action == 'enroll' else 'revoked',
            'account_id': event['payload']['account_id'], 'principal_id': event['payload']['principal_id'],
            'target_ids': event['payload']['target_ids'], 'enrollment_event_sha256': event['event_hash'],
            'identity_evidence_sha256': evidence, 'identity_assurance': event['payload']['identity_assurance'],
            'ledger_writes': 1, 'provider_calls': 0, 'scientific_approval_created': False,
            'clinical_credentials_verified': False, 'scientific_independence_verified': False}


def main(argv=None):
    parser = PrivateParser(description=__doc__)
    parser.add_argument('--data-dir', required=True, type=Path)
    actions = parser.add_subparsers(dest='action', required=True, parser_class=PrivateParser)
    listing = actions.add_parser('list')
    listing.add_argument('--username')
    for action in ('enroll', 'revoke'):
        command = actions.add_parser(action)
        command.add_argument('--username', required=True)
        command.add_argument('--principal', required=True)
        command.add_argument('--target', action='append', required=action == 'enroll')
        command.add_argument('--identity-evidence-file', required=True, type=Path)
        command.add_argument('--note', required=True)
        command.add_argument('--supersedes', required=action == 'revoke')
    try:
        result = operate(parser.parse_args(argv))
    except (OSError, ValueError, TypeError, sqlite3.Error):
        parser.exit(2, PUBLIC_ERROR)
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
