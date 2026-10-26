"""Operator-enrolled Arena review authority, separate from scientific review.

Only a trusted server-side administrator command may call ``enroll_reviewer``.
It records the operating-system UID, not a remotely authenticated administrator.
HTTP callers must obtain account_id from the existing authenticated session and
resolve a complete ledger.events() stream; request JSON is not an identity source.
An evidence hash is an operator's reference, not verified identity documentation,
clinical credentials, a human qualification, or proof of scientific independence.
"""
from contextlib import closing
from copy import deepcopy
import os
from pathlib import Path
import re
import sqlite3

from .arena_store import ArenaLedger, sha
from .arena_records import registered_targets, target_registrations


VERSION = 'arena-reviewer-enrollment-1'
ASSURANCE = 'authenticated_account_operator_enrolled'
ROLE = 'arena_result_reviewer'
HASH = re.compile(r'[a-f0-9]{64}')
FIELDS = {'schema_version', 'account_id', 'principal_id', 'target_ids',
          'identity_evidence_sha256', 'note', 'active', 'supersedes', 'operator_uid',
          'identity_assurance', 'role'}


def _account(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', value):
        raise ValueError('An exact existing account ID is required')
    return value


def _principal(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', value.strip()):
        raise ValueError('A bounded stable principal ID is required')
    return value.strip().lower()


def _scope(value, active):
    if (not isinstance(value, list) or len(value) > 1000 or (active and not value)
            or any(not isinstance(t, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', t) for t in value)
            or len(set(value)) != len(value)):
        raise ValueError('Explicit unique registered target IDs are required')
    return sorted(value)


def _note(value):
    if (not isinstance(value, str) or not value.strip() or len(value) > 4000
            or re.search(r'(?<![A-Za-z])sk-[A-Za-z0-9_-]{24,}|-----BEGIN .*PRIVATE KEY-----', value)):
        raise ValueError('A bounded operator enrollment note without credentials is required')
    return value.strip()


def _hash(value):
    return isinstance(value, str) and HASH.fullmatch(value) is not None


def _same_registration(a, b):
    return all(a.get(k) == b.get(k) for k in FIELDS - {'supersedes', 'operator_uid'})


def _enrollments(events):
    """Validate the full ordered stream and return the latest grant per account."""
    if not isinstance(events, (list, tuple)):
        raise PermissionError('Invalid reviewer enrollment history')
    previous, targets, latest = '0' * 64, set(), {}
    try:
        for event in events:
            if (not isinstance(event, dict) or not _hash(event.get('event_hash'))
                    or event.get('previous_hash') != previous
                    or sha({k: v for k, v in event.items() if k not in ('seq', 'event_hash')}) != event['event_hash']):
                raise ValueError('Invalid event chain')
            previous = event['event_hash']
            # A sealed cohort becomes available to this host only at its import
            # event. Do not flatten source sequences or let a later import
            # authorize an enrollment that preceded it in the host chain.
            targets.update(target_registrations(event))
            if event.get('kind') != 'reviewer_enrollment':
                continue
            p = event['payload']
            if (not isinstance(p, dict) or set(p) != FIELDS or p['schema_version'] != VERSION
                    or p['identity_assurance'] != ASSURANCE or p['role'] != ROLE
                    or type(p['active']) is not bool or type(p['operator_uid']) is not int or p['operator_uid'] < 0
                    or _account(p['account_id']) != event['entity_id']
                    or _principal(p['principal_id']) != p['principal_id']
                    or _scope(p['target_ids'], p['active']) != p['target_ids']
                    or not set(p['target_ids']) <= targets
                    or not _hash(p['identity_evidence_sha256']) or _note(p['note']) != p['note']):
                raise ValueError('Invalid enrollment event')
            prior = latest.get(p['account_id'])
            if (p['supersedes'] != (prior['event_hash'] if prior else None)
                    or (prior is None and not p['active'])
                    or (prior and (p['principal_id'] != prior['payload']['principal_id']
                                   or _same_registration(p, prior['payload'])))):
                raise ValueError('Invalid enrollment sequence')
            latest[p['account_id']] = event
    except (KeyError, TypeError, ValueError, OverflowError):
        raise PermissionError('Invalid reviewer enrollment history') from None
    return latest


def _existing_account(ledger_path, account_id):
    path = Path(ledger_path).parent / 'workbench.sqlite3'
    if path.is_symlink() or not path.is_file():
        raise ValueError('An existing workbench account database is required')
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=10)) as db:
            db.execute('PRAGMA query_only=ON')
            db.execute('BEGIN')
            found = db.execute('SELECT 1 FROM users WHERE id=?', (account_id,)).fetchone()
    except sqlite3.Error:
        raise ValueError('The existing workbench account database could not be read') from None
    if not found:
        raise ValueError('The account ID does not exist')


def account_id_for_username(data_dir, username):
    """Resolve one exact current username without creating a Store or an account."""
    if not isinstance(username, str) or not re.fullmatch(r'[A-Za-z0-9_-]{3,32}', username):
        raise ValueError('An exact existing username is required')
    path = Path(data_dir) / 'workbench.sqlite3'
    if path.is_symlink() or not path.is_file():
        raise ValueError('An existing workbench account database is required')
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=10)) as db:
            db.execute('PRAGMA query_only=ON')
            db.execute('BEGIN')
            rows = db.execute('SELECT id FROM users WHERE name=?', (username,)).fetchall()
    except sqlite3.Error:
        raise ValueError('The existing workbench account database could not be read') from None
    if len(rows) != 1:
        raise ValueError('The username does not identify one current account')
    return _account(rows[0][0])


def read_enrollment_events(data_dir):
    """Read the existing Arena event stream in one query-only snapshot."""
    root = Path(data_dir)
    path = root / 'arena.sqlite3'
    if root.is_symlink() or not root.is_dir() or path.is_symlink() or not path.is_file():
        raise ValueError('An existing Arena runtime is required')
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=10)) as db:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA query_only=ON')
            db.execute('BEGIN')
            return ArenaLedger._verified_events(None, db)
    except sqlite3.Error:
        raise ValueError('The existing Arena ledger could not be read') from None


def _projection(event):
    return {**deepcopy(event['payload']), 'event_hash': event['event_hash'], 'enrolled_at': event['created_at'],
            'clinical_credentials_verified': False, 'scientific_independence_verified': False}


def list_enrollments(events):
    """List latest operator enrollment decisions, including explicit revocations."""
    latest = _enrollments(events)
    return [_projection(latest[account]) for account in sorted(latest)]


def enroll_reviewer(ledger, account_id, principal_id, target_ids, identity_evidence_sha256,
                    note, *, active=True, supersedes=None):
    """Append an operator enrollment/revocation and return its Arena event.

    The same account can never be reassigned to another principal. Multiple
    accounts may belong to one principal; review quorum must count principals,
    not accounts or display names. Revocation may retain its scope or use [].
    This helper neither submits a review nor changes any prediction or outcome.
    Revocation controls future submissions; it does not retract previously
    signed fact reviews. Those require an explicit own-review correction.
    """
    account_id, principal_id, note = _account(account_id), _principal(principal_id), _note(note)
    if type(active) is not bool or not _hash(identity_evidence_sha256):
        raise ValueError('Explicit active state and identity evidence hash are required')
    target_ids = _scope(target_ids, active)
    if supersedes is not None and not _hash(supersedes):
        raise ValueError('The preceding enrollment event hash is required')
    path = Path(ledger.path)
    if path.is_symlink() or not path.is_file():
        raise ValueError('An existing Arena ledger is required')
    _existing_account(path, account_id)
    with ledger.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        events = ledger._verified_events(db)
        prior = _enrollments(events).get(account_id)
        if supersedes != (prior['event_hash'] if prior else None):
            raise ValueError('The latest own enrollment must be superseded explicitly')
        if prior and prior['payload']['principal_id'] != principal_id:
            raise ValueError('An account principal binding is permanent')
        if prior is None and not active:
            raise ValueError('An account must be enrolled before revocation')
        registered = set(registered_targets(events))
        if not set(target_ids) <= registered:
            raise ValueError('Enrollment scope contains an unregistered target')
        payload = {'schema_version': VERSION, 'account_id': account_id, 'principal_id': principal_id,
                   'target_ids': target_ids, 'identity_evidence_sha256': identity_evidence_sha256,
                   'note': note, 'active': active, 'supersedes': supersedes, 'operator_uid': os.geteuid(),
                   'identity_assurance': ASSURANCE, 'role': ROLE}
        if prior and _same_registration(payload, prior['payload']):
            raise ValueError('An identical enrollment must not be appended twice')
        return ledger._append(db, 'reviewer_enrollment', account_id, payload)


def resolve_enrollment(events, account_id, target_id=None, expected_event_hash=None):
    """Resolve current server-side authorization for an authenticated account.

    Callers must use the session's stable owner ID and the complete ordered
    server ledger event stream. This function does not authenticate a session,
    accept a client identity declaration, or prove a real person's credentials.
    expected_event_hash pins a worksheet/action to the latest enrollment state.
    """
    try:
        account_id = _account(account_id)
    except ValueError:
        raise PermissionError('Reviewer account is not authorized') from None
    event = _enrollments(events).get(account_id)
    if event is None:
        raise PermissionError('Reviewer account has no active enrollment')
    if expected_event_hash is not None and (not _hash(expected_event_hash) or expected_event_hash != event['event_hash']):
        raise PermissionError('Reviewer enrollment has changed')
    p = event['payload']
    if not p['active']:
        raise PermissionError('Reviewer enrollment is revoked')
    if target_id is not None and (not isinstance(target_id, str) or target_id not in p['target_ids']):
        raise PermissionError('Reviewer enrollment does not cover this target')
    return _projection(event)
