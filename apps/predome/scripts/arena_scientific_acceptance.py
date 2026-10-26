"""Document operator acceptance of independent scientific reviews on an existing Arena.

This local administrator command never creates a runtime, enrolls a reviewer,
calls a model or establishes global clinical predictive validity. Private source
documents are handled by the runtime acceptance helper and never printed here.
"""
import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import stat
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from emeris_live.arena_result_assurance import (
    POLICY_VERSION, accept_scientific_adjudication, prepare_acceptance,
    revoke_scientific_adjudication,
)
from emeris_live.arena_review_auth import read_enrollment_events
from emeris_live.arena_store import ArenaLedger


MAX_DOCUMENT_BYTES = 512 * 1024
PUBLIC_ERROR = 'Scientific acceptance operation rejected; check the existing runtime, document and current event.\n'
EVENT_KINDS = frozenset(('scientific_adjudication_acceptance', 'scientific_adjudication_revocation'))
SUMMARY_FIELDS = ('schema_version', 'policy_version', 'target_id', 'adjudication_event_sha256',
                  'target_binding_sha256', 'result_sha256', 'operator_uid',
                  'accepted_at_utc', 'revoked_at_utc', 'supersedes')


class PrivateParser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, PUBLIC_ERROR)


def read_document(path):
    """Bound the JSON input and preserve evidence paths for the helper's checks."""
    path = Path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not 0 < info.st_size <= MAX_DOCUMENT_BYTES:
            raise ValueError('A bounded regular acceptance document is required')
        raw = stream.read(MAX_DOCUMENT_BYTES + 1)
    if not raw or len(raw) > MAX_DOCUMENT_BYTES:
        raise ValueError('Acceptance document exceeds its bound')

    def unique(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError('Duplicate acceptance document field')
            value[key] = item
        return value

    def invalid_constant(value):
        raise ValueError('Non-finite acceptance document value')

    document = json.loads(raw, object_pairs_hook=unique, parse_constant=invalid_constant)
    document = deepcopy(document)
    if isinstance(document, dict):
        source = document.get('source_evidence_file')
        if isinstance(source, str) and source and not Path(source).is_absolute():
            document['source_evidence_file'] = str(path.absolute().parent / source)
    if isinstance(document, dict) and isinstance(document.get('reviews'), list):
        for review in document['reviews']:
            if not isinstance(review, dict):
                continue
            for key in ('qualification_evidence_file', 'independence_statement_file'):
                value = review.get(key)
                if isinstance(value, str) and value and not Path(value).is_absolute():
                    # Do not resolve the evidence itself: the helper must still
                    # see and reject a symbolic link or an unsafe original.
                    review[key] = str(path.absolute().parent / value)
    return document


def write_document(path, document):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        os.fchmod(stream.fileno(), 0o600)
        json.dump(document, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())


def event_summary(event):
    payload = event['payload']
    return {'event_kind': event['kind'], 'event_sha256': event['event_hash'],
            **{key: payload[key] for key in SUMMARY_FIELDS if key in payload}}


def operate(args):
    root = Path(args.data_dir)
    events = read_enrollment_events(root)
    # The verified existing event stream is read before obtaining any mutating
    # handle. ArenaLedger.__init__ would create a runtime and is never called.
    ledger = object.__new__(ArenaLedger)
    ledger.path = root / 'arena.sqlite3'
    if args.action == 'list':
        summaries = [event_summary(event) for event in events
                     if event['kind'] in EVENT_KINDS
                     and (args.target is None or event['entity_id'] == args.target)]
        return {'status': 'scientific_acceptance_history', 'policy_version': POLICY_VERSION,
                'events': summaries, 'ledger_writes': 0, 'provider_calls': 0,
                'clinical_prediction_validated': False}
    if args.action == 'prepare':
        document = prepare_acceptance(ledger, args.target)
        if args.output is None:
            return document
        write_document(args.output, document)
        return {'status': 'worksheet_prepared_not_accepted', 'target_id': args.target,
                'policy_version': POLICY_VERSION, 'ledger_writes': 0, 'provider_calls': 0,
                'clinical_prediction_validated': False}
    if args.action == 'accept':
        event = accept_scientific_adjudication(ledger, read_document(args.document), supersedes=args.supersedes)
        status = 'scientific_acceptance_recorded'
    else:
        event = revoke_scientific_adjudication(ledger, args.target, args.acceptor, args.note,
                                               supersedes=args.supersedes)
        status = 'scientific_acceptance_revoked'
    return {'status': status, **event_summary(event), 'ledger_writes': 1,
            'provider_calls': 0, 'clinical_prediction_validated': False}


def main(argv=None):
    parser = PrivateParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path)
    actions = parser.add_subparsers(dest='action', required=True, parser_class=PrivateParser)
    for name in ('prepare', 'accept', 'revoke', 'list'):
        command = actions.add_parser(name)
        command.add_argument('--data-dir', type=Path, default=argparse.SUPPRESS)
        if name in ('prepare', 'revoke'):
            command.add_argument('--target', required=True)
        if name == 'prepare':
            command.add_argument('--output', type=Path)
        elif name == 'accept':
            command.add_argument('--document', type=Path, required=True)
            command.add_argument('--supersedes')
        elif name == 'revoke':
            command.add_argument('--acceptor', required=True)
            command.add_argument('--note', required=True)
            command.add_argument('--supersedes', required=True)
        else:
            command.add_argument('--target')
    try:
        args = parser.parse_args(argv)
        if args.data_dir is None:
            raise ValueError('An existing runtime is required')
        result = operate(args)
    except (Exception, KeyboardInterrupt):
        parser.exit(2, PUBLIC_ERROR)
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
