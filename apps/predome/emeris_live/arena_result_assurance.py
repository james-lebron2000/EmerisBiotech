"""Operator acceptance of documented independent review for one exact result round.

This is an auditable manual acceptance, not automated verification of a license,
the truth of a document, scientific independence, or clinical prediction validity.
Only the trusted operator CLI may append these events. Account authentication or
client-supplied review flags never establish this separate result qualification.
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
import stat

from .arena_store import sha, result_target_binding, instant, RESULT_LABELS
from .arena_records import registered_targets

POLICY_VERSION = 'operator-documented-independent-review-v1'
ACCEPT_KIND = 'scientific_adjudication_acceptance'
REVOKE_KIND = 'scientific_adjudication_revocation'
MAX_EVIDENCE_BYTES = 8 * 1024 * 1024
ARCHIVE_DIRECTORY = 'scientific_review_evidence'
CONFIRMATIONS = ('qualification_and_identity_checked', 'target_scope_checked', 'no_material_conflict',
                'independent_of_model_execution', 'original_source_and_target_checked', 'individual_review_confirmed')
METHODS = ('manual_original_documents', 'manual_institutional_confirmation', 'manual_professional_registry_check')
DOCUMENT_FIELDS = {'schema_version', 'target_id', 'project_id', 'adjudication_event_sha256', 'target_binding_sha256',
                   'result_sha256', 'source_evidence_file', 'reviews', 'acceptor', 'note'}
REVIEW_FIELDS = {'review_event_sha256', 'vote_sha256', 'scientific_role', 'qualification_check_method',
                'qualification_check_note', 'qualification_evidence_file', 'independence_conclusion',
                'independence_check_note', 'independence_statement_file', 'confirmations'}
STORED_REVIEW_FIELDS = (REVIEW_FIELDS - {'qualification_evidence_file', 'independence_statement_file'}) | {
    'qualification_evidence', 'independence_statement', 'account_id', 'principal_id', 'enrollment_event_sha256'}
STORED_FIELDS = (DOCUMENT_FIELDS - {'source_evidence_file'}) | {
    'policy_version', 'source_evidence', 'operator_uid', 'accepted_at_utc', 'supersedes'}
REVOKE_FIELDS = {'schema_version', 'policy_version', 'target_id', 'adjudication_event_sha256', 'result_sha256',
                 'acceptor', 'note', 'operator_uid', 'revoked_at_utc', 'supersedes'}


def _hash(value):
    return isinstance(value, str) and re.fullmatch('[a-f0-9]{64}', value) is not None


def _text(value, minimum=3, maximum=2000):
    if (not isinstance(value, str) or not minimum <= len(value.strip()) <= maximum or '\x00' in value
            or re.search(r'(?<![A-Za-z])sk-[A-Za-z0-9_-]{24,}|-----BEGIN .*PRIVATE KEY-----', value)):
        raise ValueError('Bounded manual acceptance details without credentials are required')
    return value.strip()


def _read_file(path):
    path = Path(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not 0 < info.st_size <= MAX_EVIDENCE_BYTES:
            raise ValueError('Bounded single regular evidence files are required')
        raw = stream.read(MAX_EVIDENCE_BYTES + 1)
    if not 0 < len(raw) <= MAX_EVIDENCE_BYTES or len(raw) != info.st_size:
        raise ValueError('Evidence file changed or exceeded its bound')
    return raw, info


def _data_root(ledger):
    path = Path(ledger.path)
    info = path.lstat()
    if path.name != 'arena.sqlite3' or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or path.parent.is_symlink():
        raise ValueError('An existing private Arena ledger is required')
    return path.parent, info


def _archive(ledger, path):
    raw, _ = _read_file(path)
    root, owner = _data_root(ledger)
    directory = root / ARCHIVE_DIRECTORY
    try:
        directory.mkdir(mode=0o700)
        os.chown(directory, owner.st_uid, owner.st_gid)
    except FileExistsError:
        pass
    info = directory.lstat()
    if (not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o700
            or (info.st_uid, info.st_gid) != (owner.st_uid, owner.st_gid)):
        raise ValueError('Private evidence archive permissions are invalid')
    metadata = {'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}
    destination = directory / (metadata['sha256'] + '.bin')
    try:
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        descriptor = None
    if descriptor is not None:
        with os.fdopen(descriptor, 'wb') as stream:
            os.fchown(stream.fileno(), owner.st_uid, owner.st_gid)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    _verify_archived(root, metadata, owner)
    return metadata


def _verify_archived(root, metadata, owner=None):
    if (not isinstance(metadata, dict) or set(metadata) != {'sha256', 'bytes'} or not _hash(metadata['sha256'])
            or type(metadata['bytes']) is not int or not 0 < metadata['bytes'] <= MAX_EVIDENCE_BYTES):
        raise ValueError('Invalid archived evidence reference')
    root = Path(root)
    if owner is None:
        owner = (root / 'arena.sqlite3').lstat()
    directory = root / ARCHIVE_DIRECTORY
    info = directory.lstat()
    if (not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o700
            or (info.st_uid, info.st_gid) != (owner.st_uid, owner.st_gid)):
        raise ValueError('Private evidence archive permissions are invalid')
    raw, info = _read_file(directory / (metadata['sha256'] + '.bin'))
    if (stat.S_IMODE(info.st_mode) != 0o600 or (info.st_uid, info.st_gid) != (owner.st_uid, owner.st_gid)
            or len(raw) != metadata['bytes'] or hashlib.sha256(raw).hexdigest() != metadata['sha256']):
        raise ValueError('Archived evidence integrity is unverified')


def _latest(events, target_id):
    target = registered_targets(events).get(target_id)
    rounds = [event for event in events if event['kind'] == 'result_adjudicated' and event['entity_id'] == target_id]
    if target is None or not rounds:
        raise ValueError('A current agreed source result is required')
    current = rounds[-1]
    # A correction under consideration must not inherit the previous approval.
    if any(event['kind'] == 'result_review' and event['entity_id'] == target_id and event['seq'] > current['seq']
           and event['payload'].get('supersedes') for event in events):
        raise ValueError('A result correction is awaiting source agreement')
    return target, current, rounds[-2]['seq'] if len(rounds) > 1 else 0


def _bindings(events, document):
    from .arena_review_auth import resolve_enrollment
    target, current, prior_seq = _latest(events, document['target_id'])
    result = current['payload']['result']
    if (document.get('project_id') != target['project_id'] or result.get('label') not in RESULT_LABELS
            or document['adjudication_event_sha256'] != current['event_hash']
            or document['target_binding_sha256'] != sha(result_target_binding(target))
            or document['result_sha256'] != current['payload']['result_sha256']
            or sha(result) != document['result_sha256'] or result.get('target_binding') != result_target_binding(target)):
        raise ValueError('Acceptance must bind the exact current adjudication round and result')
    refs = document.get('reviews')
    if not isinstance(refs, list) or len(refs) != 2:
        raise ValueError('Exactly two actual authenticated scientific reviews are required')
    selected = []
    principals = set()
    for entry in refs:
        event = next((event for event in events if event['event_hash'] == entry.get('review_event_sha256')), None)
        if (event is None or event['kind'] != 'result_review' or event['entity_id'] != target['id']
                or not prior_seq < event['seq'] < current['seq']):
            raise ValueError('Scientific review reference is not in the current result round')
        vote = event['payload']
        authentication = vote.get('reviewer_authentication') or {}
        context = vote.get('review_context') or {}
        if (vote.get('vote_sha256') != entry.get('vote_sha256')
                or not _hash(vote.get('vote_sha256'))
                or sha({key: value for key, value in vote.items() if key != 'vote_sha256'}) != vote['vote_sha256']
                or vote.get('result_sha256') != document['result_sha256'] or vote.get('result') != result
                or vote.get('reviewer_kind') != 'human'
                or authentication.get('identity_assurance') != 'authenticated_account_operator_enrolled'
                or vote.get('reviewer_id') not in current['payload'].get('reviewer_ids', [])
                or context.get('source_sha256') != result.get('source_sha256')
                or not context.get('excerpt') or not context.get('source_publication_date')
                or not context.get('confirmations') or not all(value is True for value in context['confirmations'].values())):
            raise ValueError('Only source-bound actual authenticated review votes may be accepted')
        # Reconstruct authorization at signing time. Ordinary later revocation
        # does not rewrite a valid historical factual review.
        grant = resolve_enrollment(events[:event['seq'] - 1], authentication.get('account_id'), target['id'],
                                   authentication.get('enrollment_event_sha256'))
        if (grant['principal_id'] != authentication.get('principal_id')
                or grant['identity_evidence_sha256'] != authentication.get('identity_evidence_sha256')):
            raise ValueError('Authenticated reviewer principal is not bound to its enrollment')
        principal = grant['principal_id']
        if any(later['kind'] == 'result_review' and later['entity_id'] == target['id']
               and event['seq'] < later['seq'] < current['seq']
               and (later['payload'].get('reviewer_authentication') or {}).get('principal_id') == principal
               for later in events):
            raise ValueError('Acceptance must reference each principal latest actual review vote')
        if principal in principals:
            raise ValueError('Two distinct documented reviewer principals are required')
        principals.add(principal)
        selected.append(authentication)
    return target, current, selected


def _review_details(entry):
    for key in ('review_event_sha256', 'vote_sha256'):
        if not _hash(entry.get(key)):
            raise ValueError('Exact review event and vote hashes are required')
    if entry.get('qualification_check_method') not in METHODS:
        raise ValueError('An explicit manual qualification check method is required')
    if entry.get('independence_conclusion') != 'independent_for_registered_target':
        raise ValueError('A documented independent review without material target conflict is required')
    if (not isinstance(entry.get('confirmations'), dict) or set(entry['confirmations']) != set(CONFIRMATIONS)
            or any(entry['confirmations'][key] is not True for key in CONFIRMATIONS)):
        raise ValueError('Every individual manual review acceptance must be explicitly confirmed')
    return {'review_event_sha256': entry['review_event_sha256'], 'vote_sha256': entry['vote_sha256'],
            'scientific_role': _text(entry.get('scientific_role'), 8, 300),
            'qualification_check_method': entry['qualification_check_method'],
            'qualification_check_note': _text(entry.get('qualification_check_note'), 20),
            'independence_conclusion': entry['independence_conclusion'],
            'independence_check_note': _text(entry.get('independence_check_note'), 20),
            'confirmations': deepcopy(entry['confirmations'])}


def _last_acceptance_event(events, target_id):
    records = [event for event in events if event['kind'] in (ACCEPT_KIND, REVOKE_KIND) and event['entity_id'] == target_id]
    return records[-1] if records else None


def _acceptance_history(events, target_id):
    last = None
    for event in events:
        if event['kind'] not in (ACCEPT_KIND, REVOKE_KIND) or event['entity_id'] != target_id:
            continue
        payload = event['payload']
        if (set(payload) != (STORED_FIELDS if event['kind'] == ACCEPT_KIND else REVOKE_FIELDS)
                or payload['schema_version'] != POLICY_VERSION or payload['policy_version'] != POLICY_VERSION
                or payload['target_id'] != target_id or payload['supersedes'] != (last['event_hash'] if last else None)):
            raise ValueError('Scientific acceptance revision history is invalid')
        if event['kind'] == REVOKE_KIND and (last is None or last['kind'] != ACCEPT_KIND
                or any(payload[key] != last['payload'][key] for key in ('adjudication_event_sha256', 'result_sha256'))):
            raise ValueError('Scientific revocation does not bind the latest acceptance')
        last = event
    return last


def prepare_acceptance(ledger, target_id):
    from .arena_review_auth import read_enrollment_events
    _data_root(ledger)
    events = read_enrollment_events(Path(ledger.path).parent)
    target, current, prior_seq = _latest(events, target_id)
    votes = [event for event in events if event['kind'] == 'result_review' and event['entity_id'] == target_id
             and prior_seq < event['seq'] < current['seq']
             and event['payload'].get('result_sha256') == current['payload']['result_sha256']
             and event['payload'].get('reviewer_authentication')
             and event['payload'].get('reviewer_id') in current['payload'].get('reviewer_ids', [])]
    latest = {}
    for event in votes:
        latest[event['payload']['reviewer_authentication']['principal_id']] = event
    if len(latest) != 2:
        raise ValueError('Current result requires exactly two distinct authenticated source reviews')
    reviews = []
    for event in latest.values():
        entry = {key: None for key in REVIEW_FIELDS}
        entry.update(review_event_sha256=event['event_hash'], vote_sha256=event['payload']['vote_sha256'],
                     confirmations={key: False for key in CONFIRMATIONS})
        reviews.append(entry)
    document = {'schema_version': POLICY_VERSION, 'target_id': target_id, 'project_id': target['project_id'],
                'adjudication_event_sha256': current['event_hash'], 'target_binding_sha256': sha(result_target_binding(target)),
                'result_sha256': current['payload']['result_sha256'], 'source_evidence_file': None,
                'reviews': reviews, 'acceptor': None, 'note': None}
    _bindings(events, document)
    return document


def accept_scientific_adjudication(ledger, document, *, supersedes=None):
    if (not isinstance(document, dict) or set(document) != DOCUMENT_FIELDS
            or document.get('schema_version') != POLICY_VERSION):
        raise ValueError('An exact versioned manual scientific acceptance document is required')
    acceptor, note = _text(document.get('acceptor'), 3, 300), _text(document.get('note'), 20, 4000)
    if not isinstance(document.get('reviews'), list) or len(document['reviews']) != 2:
        raise ValueError('Exactly two documented reviews are required')
    for entry in document['reviews']:
        if not isinstance(entry, dict) or set(entry) != REVIEW_FIELDS:
            raise ValueError('Unknown or missing individual scientific review fields')
        _review_details(entry)
    _data_root(ledger)
    with ledger.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        events = ledger._verified_events(db)
        target, current, identities = _bindings(events, document)
        last = _acceptance_history(events, target['id'])
        if (last and supersedes != last['event_hash']) or (not last and supersedes is not None):
            raise ValueError('An update must explicitly supersede the latest acceptance or revocation event')
        source_bytes, _ = _read_file(document['source_evidence_file'])
        if hashlib.sha256(source_bytes).hexdigest() != current['payload']['result']['source_sha256']:
            raise ValueError('The preserved original source differs from the adjudicated source')
        source = _archive(ledger, document['source_evidence_file'])
        if source['sha256'] != current['payload']['result']['source_sha256']:
            raise ValueError('The preserved original source differs from the adjudicated source')
        reviews = []
        for entry, identity in zip(document['reviews'], identities):
            reviews.append({**_review_details(entry),
                'account_id': identity['account_id'], 'principal_id': identity['principal_id'],
                'enrollment_event_sha256': identity['enrollment_event_sha256'],
                'qualification_evidence': _archive(ledger, entry['qualification_evidence_file']),
                'independence_statement': _archive(ledger, entry['independence_statement_file'])})
        payload = {key: deepcopy(document[key]) for key in DOCUMENT_FIELDS - {'reviews', 'source_evidence_file'}}
        payload.update(policy_version=POLICY_VERSION, acceptor=acceptor, note=note, reviews=reviews,
                       source_evidence=source, supersedes=supersedes, operator_uid=os.geteuid(),
                       accepted_at_utc=datetime.now(timezone.utc).isoformat())
        semantic = STORED_FIELDS - {'operator_uid', 'accepted_at_utc', 'supersedes'}
        if last and last['kind'] == ACCEPT_KIND and all(payload[key] == last['payload'][key] for key in semantic):
            raise ValueError('An identical scientific acceptance cannot be recorded twice')
        return ledger._append(db, ACCEPT_KIND, target['id'], payload)


def revoke_scientific_adjudication(ledger, target_id, acceptor, note, *, supersedes):
    acceptor, note = _text(acceptor, 3, 300), _text(note, 20, 4000)
    _data_root(ledger)
    with ledger.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        events = ledger._verified_events(db)
        previous = _acceptance_history(events, target_id)
        if not previous or previous['kind'] != ACCEPT_KIND or previous['event_hash'] != supersedes:
            raise ValueError('Revocation must identify the latest active acceptance event')
        payload = {'schema_version': POLICY_VERSION, 'policy_version': POLICY_VERSION, 'target_id': target_id,
            'adjudication_event_sha256': previous['payload']['adjudication_event_sha256'],
            'result_sha256': previous['payload']['result_sha256'], 'acceptor': acceptor, 'note': note,
            'operator_uid': os.geteuid(), 'revoked_at_utc': datetime.now(timezone.utc).isoformat(), 'supersedes': supersedes}
        return ledger._append(db, REVOKE_KIND, target_id, payload)


class _VerifiedAcceptance(dict):
    """A plain API/JSON dictionary cannot issue itself this projection seal."""
    def __init__(self, value, core_result):
        super().__init__(value)
        self._seal = sha(value)
        self._core_fields = tuple(sorted(core_result))
        self._core_sha256 = sha(core_result)


def project_result_assurance(events, targets, data_root):
    """Called only after ArenaLedger verifies its append-only event stream."""
    for target in targets.values():
        target.pop('scientific_adjudication_acceptance', None)
        target.pop('scientific_assurance_error', None)
        try:
            record = _acceptance_history(events, target['id'])
            if record is None:
                continue
            if record['kind'] == REVOKE_KIND:
                target['scientific_assurance_error'] = 'scientific_acceptance_revoked'
                continue
            payload = record['payload']
            if set(payload) != STORED_FIELDS or payload['schema_version'] != POLICY_VERSION or payload['policy_version'] != POLICY_VERSION:
                raise ValueError('Scientific acceptance policy or fields are invalid')
            if payload['target_id'] != target['id'] or record['entity_id'] != target['id']:
                raise ValueError('Scientific acceptance target is invalid')
            if type(payload['operator_uid']) is not int or payload['operator_uid'] < 0:
                raise ValueError('Scientific acceptance operator is unrecorded')
            if not instant(payload['accepted_at_utc']) <= instant(record['created_at']) <= datetime.now(timezone.utc):
                raise ValueError('Scientific acceptance time is invalid')
            _text(payload['acceptor'], 3, 300)
            _text(payload['note'], 20, 4000)
            # Bind against the current full stream; old approvals cannot return
            # when an equal result hash recurs in a later A -> B -> A round.
            _, current, identities = _bindings(events, payload)
            if record['seq'] <= current['seq'] or data_root is None:
                raise ValueError('Scientific acceptance archive context is unavailable')
            if payload['source_evidence']['sha256'] != current['payload']['result']['source_sha256']:
                raise ValueError('Scientific acceptance source differs')
            _verify_archived(data_root, payload['source_evidence'])
            for entry, identity in zip(payload['reviews'], identities):
                if set(entry) != STORED_REVIEW_FIELDS:
                    raise ValueError('Scientific review acceptance fields are invalid')
                _review_details(entry)
                if any(entry[key] != identity[key] for key in ('account_id', 'principal_id', 'enrollment_event_sha256')):
                    raise ValueError('Scientific acceptance reviewer identity differs')
                _verify_archived(data_root, entry['qualification_evidence'])
                _verify_archived(data_root, entry['independence_statement'])
            target['scientific_adjudication_acceptance'] = _VerifiedAcceptance({
                'policy_version': POLICY_VERSION, 'acceptance_event_sha256': record['event_hash'],
                'project_id': payload['project_id'],
                'adjudication_event_sha256': payload['adjudication_event_sha256'],
                'target_binding_sha256': payload['target_binding_sha256'], 'result_sha256': payload['result_sha256'],
                'assurance': 'operator_accepted_documented_independent_review',
                'manual_qualification_acceptance': True, 'manual_independence_acceptance': True,
                'scientific_credentials_verified': False, 'independent_human_review_verified': False},
                current['payload']['result'])
        except (OSError, ValueError, TypeError, KeyError, PermissionError) as error:
            target['scientific_assurance_error'] = 'scientific_acceptance_unverified: ' + str(error)


def result_assurance(target):
    status = 'awaiting_source_agreement'
    reason = 'source_agreement_not_recorded'
    if target.get('record_kind') == 'historical_replay':
        status, reason = 'historical_replay', 'historical_replay_not_prospective_reference'
    elif target.get('status') == 'adjudicated':
        status, reason = 'agreement_pending_independent_review', 'documented_independent_review_not_accepted'
        accepted = target.get('scientific_adjudication_acceptance')
        result = target.get('result') or {}
        try:
            valid = (isinstance(accepted, _VerifiedAcceptance) and accepted._seal == sha(dict(accepted))
                and accepted.get('policy_version') == POLICY_VERSION
                and accepted.get('adjudication_event_sha256') == target.get('result_adjudication_event_sha256')
                and accepted.get('result_sha256') == result.get('result_sha256')
                and accepted.get('project_id') == target.get('project_id')
                and sha({key: result[key] for key in accepted._core_fields}) == accepted._core_sha256
                and accepted.get('target_binding_sha256') == sha(result_target_binding(target)))
        except (TypeError, ValueError, KeyError):
            valid = False
        if valid:
            status = 'accepted'
            reason = 'operator_accepted_documented_independent_review'
    eligible = status == 'accepted' and (target.get('result') or {}).get('label') in ('success', 'failure')
    if status == 'accepted' and not eligible:
        reason = 'accepted_outcome_not_binary'
    return {'status': status, 'scoring_eligible': eligible, 'reason': reason, 'policy_version': POLICY_VERSION,
            'private_reason': target.get('scientific_assurance_error') or reason}
