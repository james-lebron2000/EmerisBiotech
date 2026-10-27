"""Temporary account/ledger fixtures; no real identities, outcomes, or models."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from emeris_live.arena_review_auth import (
    account_id_for_username, enroll_reviewer, list_enrollments, read_enrollment_events, resolve_enrollment,
)
from emeris_live.store import Store
from test_arena_core import registered, spec


@pytest.fixture
def workspace(tmp_path):
    store = Store(tmp_path)
    with store.connect() as db:
        db.executemany('INSERT INTO users VALUES(?,?,?)', [
            ('account-one', 'reviewer-one', 'isolated-password-hash'),
            ('account-alias', 'reviewer-alias', 'isolated-password-hash'),
            ('account-two', 'reviewer-two', 'isolated-password-hash')])
    ledger, target, _ = registered(tmp_path)
    other, slots = spec()
    other.update(id='target-other', nct_id='NCT00000002')
    for slot in slots:
        slot['target_id'] = other['id']
    ledger.register('season-other', [other], slots, {'status': 'isolated enrollment fixture'})
    return store, ledger, target, other


def enroll(ledger, account='account-one', principal='person-one', targets=None, **changes):
    values = dict(account_id=account, principal_id=principal,
                  target_ids=['target-test'] if targets is None else targets,
                  identity_evidence_sha256='a' * 64, note='Isolated operator enrollment reference')
    values.update(changes)
    return enroll_reviewer(ledger, **values)


def test_existing_account_enrollment_is_append_only_scoped_and_not_credentials(workspace, monkeypatch):
    store, ledger, target, other = workspace
    before = ledger.events()
    accounts_before = (store.path / 'workbench.sqlite3').read_bytes()
    monkeypatch.setattr(os, 'geteuid', lambda: 4321)
    event = enroll(ledger, principal='  Dr.Person-One  ', targets=[other['id'], target['id']])
    current = resolve_enrollment(ledger.events(), 'account-one', target['id'], event['event_hash'])
    assert current['principal_id'] == 'dr.person-one'
    assert current['target_ids'] == sorted([target['id'], other['id']])
    assert current['operator_uid'] == 4321
    assert current['identity_evidence_sha256'] == 'a' * 64
    assert current['identity_assurance'] == 'authenticated_account_operator_enrolled'
    assert current['role'] == 'arena_result_reviewer'
    assert current['clinical_credentials_verified'] is False
    assert current['scientific_independence_verified'] is False
    assert 'reviewer_kind' not in current
    assert ledger.events()[:-1] == before
    assert event['kind'] == 'reviewer_enrollment' and event['entity_id'] == 'account-one'
    assert (store.path / 'workbench.sqlite3').read_bytes() == accounts_before
    assert all(t['status'] == 'pending' for t in ledger.snapshot()['targets'])
    current['target_ids'].append('client-injected-target')
    assert 'client-injected-target' not in resolve_enrollment(ledger.events(), 'account-one')['target_ids']


def test_same_person_alias_accounts_resolve_one_principal(workspace):
    _, ledger, target, _ = workspace
    enroll(ledger, principal='Person.One')
    enroll(ledger, account='account-alias', principal='person.one')
    principals = {resolve_enrollment(ledger.events(), account, target['id'])['principal_id']
                  for account in ('account-one', 'account-alias')}
    assert principals == {'person.one'}


def test_deleted_and_reregistered_username_does_not_inherit_authority(workspace):
    store, ledger, target, _ = workspace
    event = enroll(ledger)
    with store.connect() as db:
        db.execute('DELETE FROM users WHERE id=?', ('account-one',))
        db.execute('INSERT INTO users VALUES(?,?,?)', ('new-account', 'reviewer-one', 'new-isolated-hash'))
    with pytest.raises(PermissionError):
        resolve_enrollment(ledger.events(), 'new-account', target['id'])
    with pytest.raises(ValueError, match='does not exist'):
        enroll(ledger, supersedes=event['event_hash'], note='Cannot update a deleted account')
    assert ledger.events()[-1]['event_hash'] == event['event_hash']


def test_account_principal_cannot_change_even_after_revocation(workspace):
    _, ledger, _, _ = workspace
    event = enroll(ledger)
    with pytest.raises(ValueError, match='permanent'):
        enroll(ledger, principal='different-person', supersedes=event['event_hash'])
    revoked = enroll(ledger, targets=[], active=False, supersedes=event['event_hash'], note='Revoke isolated authorization')
    before = ledger.events()
    with pytest.raises(ValueError, match='permanent'):
        enroll(ledger, principal='different-person', supersedes=revoked['event_hash'])
    assert ledger.events() == before


def test_scope_revision_revocation_reactivation_and_stale_pins(workspace):
    _, ledger, target, other = workspace
    first = enroll(ledger)
    with pytest.raises(PermissionError, match='cover'):
        resolve_enrollment(ledger.events(), 'account-one', other['id'])
    with pytest.raises(ValueError, match='latest'):
        enroll(ledger, targets=[other['id']], note='Isolated changed scope')
    second = enroll(ledger, targets=[other['id']], supersedes=first['event_hash'], note='Isolated changed scope')
    with pytest.raises(PermissionError, match='changed'):
        resolve_enrollment(ledger.events(), 'account-one', other['id'], first['event_hash'])
    with pytest.raises(PermissionError, match='cover'):
        resolve_enrollment(ledger.events(), 'account-one', target['id'])
    assert resolve_enrollment(ledger.events(), 'account-one', other['id'])['event_hash'] == second['event_hash']
    revoked = enroll(ledger, targets=[], active=False, supersedes=second['event_hash'], note='Revoke isolated authorization')
    with pytest.raises(PermissionError, match='revoked'):
        resolve_enrollment(ledger.events(), 'account-one')
    restored = enroll(ledger, targets=[target['id']], supersedes=revoked['event_hash'], note='Restore isolated authorization')
    assert resolve_enrollment(ledger.events(), 'account-one', target['id'])['event_hash'] == restored['event_hash']


def test_duplicate_enrollment_and_stale_or_foreign_supersedes_do_not_append(workspace):
    _, ledger, _, _ = workspace
    first = enroll(ledger)
    with pytest.raises(ValueError, match='identical'):
        enroll(ledger, supersedes=first['event_hash'])
    second = enroll(ledger, account='account-two', principal='person-two')
    before = ledger.events()
    with pytest.raises(ValueError, match='latest'):
        enroll(ledger, supersedes=second['event_hash'], note='Wrong account correction pointer')
    with pytest.raises(ValueError, match='latest'):
        enroll(ledger, account='account-alias', supersedes=first['event_hash'])
    assert ledger.events() == before


@pytest.mark.parametrize('changes', [
    {'account_id': 'unknown-account'}, {'principal_id': 'person with spaces'},
    {'principal_id': '\u0430dmin'}, {'target_ids': []}, {'target_ids': ['unknown-target']},
    {'target_ids': ['target-test', 'target-test']}, {'target_ids': 'target-test'},
    {'identity_evidence_sha256': 'not-a-hash'}, {'note': '   '}, {'active': 'true'},
    {'supersedes': 'not-a-hash'}, {'active': False},
])
def test_invalid_enrollments_do_not_append(workspace, changes):
    _, ledger, _, _ = workspace
    before = ledger.events()
    with pytest.raises(ValueError):
        enroll(ledger, **changes)
    assert ledger.events() == before


def test_account_lookup_is_query_only_and_never_constructs_store(workspace, monkeypatch):
    store, ledger, _, _ = workspace
    def forbidden(*args, **kwargs):
        raise AssertionError('Enrollment must not initialize a Store')
    monkeypatch.setattr(Store, '__init__', forbidden)
    calls = []
    connect = sqlite3.connect
    def observed(database, *args, **kwargs):
        calls.append((str(database), kwargs.get('uri')))
        return connect(database, *args, **kwargs)
    monkeypatch.setattr(sqlite3, 'connect', observed)
    enroll(ledger)
    assert any(path.endswith('workbench.sqlite3?mode=ro') and uri for path, uri in calls)
    with store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM users').fetchone()[0] == 3


def test_missing_account_database_is_not_created(tmp_path):
    ledger, _, _ = registered(tmp_path)
    before = ledger.events()
    with pytest.raises(ValueError, match='existing workbench'):
        enroll(ledger)
    assert not (tmp_path / 'workbench.sqlite3').exists()
    assert ledger.events() == before


def test_client_event_tampering_or_unknown_account_cannot_resolve(workspace):
    _, ledger, target, _ = workspace
    enroll(ledger)
    before = ledger.events()
    tampered = deepcopy(before)
    tampered[-1]['payload']['principal_id'] = 'forged-principal'
    for events, account, pin in [(tampered, 'account-one', None),
                                 (before, 'unknown-account', None),
                                 (before, 'account-one', 'b' * 64)]:
        with pytest.raises(PermissionError):
            resolve_enrollment(events, account, target['id'], pin)
    assert ledger.events() == before


def test_legacy_reviews_are_not_enrollments_or_retroactively_authenticated(workspace):
    _, ledger, target, _ = workspace
    from test_arena_result_reviews import result
    ledger.record_result(target['id'], result(target), reviewer_id='account-one',
                         reviewer_kind='human', review_note='Legacy isolated operator-declared review')
    legacy = ledger.events()
    with pytest.raises(PermissionError):
        resolve_enrollment(legacy, 'account-one', target['id'])
    enroll(ledger)
    assert ledger.events()[:-1] == legacy
    review = next(e for e in ledger.events() if e['kind'] == 'result_review')
    assert 'principal_id' not in review['payload'] and 'identity_assurance' not in review['payload']
    assert ledger.snapshot()['targets'][0]['result']['review_provenance']['identity_assurance'] == 'operator_declared_not_independently_authenticated'


REVIEWERS_CLI = Path(__file__).resolve().parents[1] / 'scripts/arena_reviewers.py'


def reviewers_cli(data_dir, action, *args):
    env = {key: os.environ[key] for key in ('PATH', 'HOME', 'TMPDIR', 'LANG') if key in os.environ}
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    return subprocess.run([sys.executable, str(REVIEWERS_CLI), '--data-dir', str(data_dir), action, *args],
                          cwd=Path(data_dir).parent, env=env, capture_output=True, text=True, timeout=15)


def cli_enrollment_args(evidence, *, username='reviewer-one', principal='Person.One', note='Synthetic operator account linkage'):
    return ['--username', username, '--principal', principal, '--target', 'target-test',
            '--identity-evidence-file', str(evidence), '--note', note]


def test_operator_cli_enroll_list_revoke_real_subprocess_without_uploads(workspace, tmp_path):
    store, ledger, target, _ = workspace
    evidence = tmp_path / 'identity-evidence.txt'
    evidence.write_bytes(b'PRIVATE SYNTHETIC IDENTITY DOCUMENT; NOT A REAL PERSON')
    original = evidence.read_bytes()
    accounts = (store.path / 'workbench.sqlite3').read_bytes()
    before = ledger.events()
    created = reviewers_cli(store.path, 'enroll', *cli_enrollment_args(evidence))
    assert created.returncode == 0, created.stderr
    receipt = json.loads(created.stdout)
    assert receipt['principal_id'] == 'person.one' and receipt['account_id'] == 'account-one'
    assert receipt['identity_evidence_sha256'] == hashlib.sha256(original).hexdigest()
    assert receipt['ledger_writes'] == 1 and receipt['scientific_approval_created'] is False
    assert original.decode() not in created.stdout and str(evidence) not in created.stdout
    event = ledger.events()[-1]
    assert ledger.events()[:-1] == before and event['payload']['operator_uid'] == os.geteuid()
    assert 'identity_evidence_file' not in event['payload']
    assert evidence.read_bytes() == original and (store.path / 'workbench.sqlite3').read_bytes() == accounts
    before_list = ledger.path.read_bytes()
    listed = reviewers_cli(store.path, 'list', '--username', 'reviewer-one')
    assert listed.returncode == 0, listed.stderr
    body = json.loads(listed.stdout)
    assert body['ledger_writes'] == 0 and len(body['enrollments']) == 1
    assert body['enrollments'][0]['active'] is True and 'note' not in body['enrollments'][0]
    assert ledger.path.read_bytes() == before_list
    revoked = reviewers_cli(store.path, 'revoke', '--username', 'reviewer-one', '--principal', 'person.one',
        '--identity-evidence-file', str(evidence), '--note', 'Revoke future synthetic submissions',
        '--supersedes', receipt['enrollment_event_sha256'])
    assert revoked.returncode == 0, revoked.stderr
    assert json.loads(revoked.stdout)['status'] == 'revoked'
    with pytest.raises(PermissionError):
        resolve_enrollment(ledger.events(), 'account-one', target['id'])
    listing = json.loads(reviewers_cli(store.path, 'list').stdout)
    assert listing['enrollments'][0]['active'] is False
    assert all(t['status'] == 'pending' for t in ledger.snapshot()['targets'])


def test_cli_exact_username_stable_id_and_stale_update_rejected(workspace, tmp_path):
    store, ledger, target, _ = workspace
    evidence = tmp_path / 'identity-evidence.txt'; evidence.write_bytes(b'Synthetic account linkage proof')
    args = cli_enrollment_args(evidence)
    upper = reviewers_cli(store.path, 'enroll', *cli_enrollment_args(evidence, username='Reviewer-One'))
    assert upper.returncode != 0 and ledger.events()[-1]['kind'] != 'reviewer_enrollment'
    first = json.loads(reviewers_cli(store.path, 'enroll', *args).stdout)
    missing_pin = reviewers_cli(store.path, 'enroll', *cli_enrollment_args(evidence, note='Different synthetic grant note'))
    assert missing_pin.returncode != 0
    updated = reviewers_cli(store.path, 'enroll', *cli_enrollment_args(evidence, note='Different synthetic grant note'),
                            '--supersedes', first['enrollment_event_sha256'])
    assert updated.returncode == 0, updated.stderr
    before = ledger.events()
    stale = reviewers_cli(store.path, 'revoke', '--username', 'reviewer-one', '--principal', 'person.one',
        '--identity-evidence-file', str(evidence), '--note', 'Synthetic stale revocation attempt',
        '--supersedes', first['enrollment_event_sha256'])
    assert stale.returncode != 0 and ledger.events() == before
    with store.connect() as db:
        db.execute('DELETE FROM users WHERE id=?', ('account-one',))
        db.execute('INSERT INTO users VALUES(?,?,?)', ('new-owner', 'reviewer-one', 'isolated-password-hash'))
    assert account_id_for_username(store.path, 'reviewer-one') == 'new-owner'
    assert json.loads(reviewers_cli(store.path, 'list', '--username', 'reviewer-one').stdout)['enrollments'] == []
    with pytest.raises(PermissionError):
        resolve_enrollment(ledger.events(), 'new-owner', target['id'])


@pytest.mark.parametrize('bad_input', ['missing', 'empty', 'symlink', 'oversized'])
def test_cli_identity_evidence_must_be_bounded_regular_file(workspace, tmp_path, bad_input):
    store, ledger, _, _ = workspace
    evidence = tmp_path / 'identity-evidence.txt'
    if bad_input == 'empty': evidence.touch()
    elif bad_input == 'symlink':
        original = tmp_path / 'original.txt'; original.write_bytes(b'Synthetic identity evidence')
        evidence.symlink_to(original)
    elif bad_input == 'oversized':
        with evidence.open('wb') as stream: stream.truncate(8 * 1024 * 1024 + 1)
    before = ledger.events()
    result = reviewers_cli(store.path, 'enroll', *cli_enrollment_args(evidence))
    assert result.returncode == 2 and result.stdout == ''
    assert str(evidence) not in result.stderr and ledger.events() == before


def test_cli_missing_runtime_and_sensitive_argument_errors_do_not_create_or_echo(tmp_path):
    missing = tmp_path / 'uncreated-runtime'
    result = reviewers_cli(missing, 'list')
    assert result.returncode == 2 and not missing.exists()
    assert str(missing) not in result.stderr and result.stdout == ''
    parse_error = reviewers_cli(missing, 'list', '--unrecognized-private-input', 'PRIVATE-ARGUMENT')
    assert parse_error.returncode == 2 and 'PRIVATE-ARGUMENT' not in parse_error.stderr


def test_read_only_account_and_enrollment_helpers_do_not_construct_databases(workspace, monkeypatch):
    store, ledger, _, _ = workspace
    enroll(ledger)
    original = ledger.events()
    def forbidden(*args, **kwargs):
        raise AssertionError('Read helpers must not initialize stores or ledgers')
    monkeypatch.setattr(Store, '__init__', forbidden)
    monkeypatch.setattr(type(ledger), '__init__', forbidden)
    assert account_id_for_username(store.path, 'reviewer-one') == 'account-one'
    assert read_enrollment_events(store.path) == original
    assert list_enrollments(original)[0]['active'] is True
    assert ledger.events() == original
