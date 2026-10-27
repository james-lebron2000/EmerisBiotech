"""Real temporary Ledgers; account assurance is not clinical adjudication."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json

import pytest

from emeris_live import arena_store
from emeris_live.arena_public import result_view
from emeris_live.arena_review_auth import enroll_reviewer, resolve_enrollment
from emeris_live.arena_store import AUTHENTICATED_REVIEW_PREFIX, sha
from test_arena_result_reviews import result
from test_arena_review_auth import workspace


PUBLICATION_NOW = datetime(2026, 10, 26, 23, 59, 59, tzinfo=timezone.utc)


@pytest.fixture
def publication_clock(workspace, monkeypatch):
    # Freeze only the owning validator, after normal workspace registration.
    # Collection and execution must use the same UTC date across real midnight.
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return PUBLICATION_NOW.astimezone(tz) if tz is not None else PUBLICATION_NOW.replace(tzinfo=None)

    monkeypatch.setattr(arena_store, 'datetime', FixedDateTime)
    return PUBLICATION_NOW


def enroll(ledger, account='account-one', principal='person-one', targets=None, **changes):
    return enroll_reviewer(ledger, account, principal, targets or ['target-test'], 'a' * 64,
                          changes.pop('note', 'Synthetic operator identity linkage'), **changes)


def context(body, account='account-one'):
    return {'candidate_id': 'private-synthetic-candidate', 'source_sha256': body['source_sha256'],
            'excerpt': 'Private source inspection by ' + account,
            'source_publication_date': datetime.now(timezone.utc).date().isoformat(),
            'publication_date_basis': 'reviewer_provided_not_independently_verified',
            'confirmations': {key: True for key in ('exact_trial', 'primary_endpoint', 'prespecified_analysis',
                                                   'first_public_release', 'source_inspected')}}


def vote(ledger, target, body, account='account-one', *, enrollment=None, review_context=None, **changes):
    if enrollment is None:
        enrollment = resolve_enrollment(ledger.events(), account)['event_hash']
    return ledger.record_authenticated_result(target['id'], deepcopy(body), account_id=account,
        enrollment_sha256=enrollment, review_note='Private individual review by ' + account,
        review_context=context(body, account) if review_context is None else review_context, **changes)


def reviews(ledger):
    return [e for e in ledger.events() if e['kind'] == 'result_review']


def test_two_principals_same_core_fact_different_private_notes_engineering_quorum(workspace):
    _, ledger, target, _ = workspace
    enroll(ledger); enroll(ledger, 'account-two', 'person-two')
    body = result(target)
    before = ledger.events()
    assert vote(ledger, target, body) == 'announced_pending_adjudication'
    assert vote(ledger, target, body, 'account-two') == 'adjudicated'
    first, second = reviews(ledger)
    assert first['payload']['result_sha256'] == second['payload']['result_sha256'] == sha(body)
    assert first['payload']['vote_sha256'] != second['payload']['vote_sha256']
    assert first['payload']['review_context'] != second['payload']['review_context']
    provenance = ledger.snapshot()['targets'][0]['result']['review_provenance']
    assert provenance['distinct_authenticated_principals'] == 2
    assert provenance['scientific_credentials_verified'] is False
    assert provenance['independent_human_review_verified'] is False
    assert ledger.events()[:len(before)] == before


def test_alias_accounts_share_one_vote_and_one_correction_chain(workspace):
    _, ledger, target, _ = workspace
    enroll(ledger); enroll(ledger, 'account-alias', 'person-one')
    body = result(target)
    vote(ledger, target, body)
    first = reviews(ledger)[-1]['payload']['vote_sha256']
    before = ledger.events()
    with pytest.raises(ValueError, match='already voted'):
        vote(ledger, target, body, 'account-alias')
    assert ledger.events() == before
    assert vote(ledger, target, body, 'account-alias', review_supersedes=first) == 'announced_pending_adjudication'
    current = ledger.snapshot()['targets'][0]
    assert current['status'] == 'announced'
    assert current['result']['review_provenance']['distinct_authenticated_principals'] == 1
    assert len({e['payload']['reviewer_id'] for e in reviews(ledger)}) == 1


def test_authenticated_and_legacy_reviews_never_form_a_quorum(workspace):
    _, ledger, target, _ = workspace
    enroll(ledger); enroll(ledger, 'account-two', 'person-two')
    body = result(target)
    ledger.record_result(target['id'], body, reviewer_id='legacy-person', reviewer_kind='human',
                         review_note='Legacy operator declaration; no account authentication')
    legacy = reviews(ledger)[0]
    assert vote(ledger, target, body) == 'announced_pending_adjudication'
    p = ledger.snapshot()['targets'][0]['result']['review_provenance']
    assert p['distinct_authenticated_principals'] == 1 and p['excluded_other_assurance_reviews'] == 1
    assert vote(ledger, target, body, 'account-two') == 'adjudicated'
    assert reviews(ledger)[0] == legacy and 'reviewer_authentication' not in legacy['payload']


def test_maximum_legal_principal_has_bounded_internal_reviewer_id(workspace):
    _, ledger, target, _ = workspace
    principal = 'p' * 100
    enroll(ledger, principal=principal)
    assert vote(ledger, target, result(target)) == 'announced_pending_adjudication'
    row = reviews(ledger)[0]['payload']
    assert row['reviewer_id'] == AUTHENTICATED_REVIEW_PREFIX + sha(principal)
    assert len(row['reviewer_id']) <= 100 and row['reviewer_authentication']['principal_id'] == principal


@pytest.mark.parametrize('fault', ['not_enrolled', 'stale', 'revoked', 'out_of_scope'])
def test_authentication_grant_failures_do_not_append_reviews(workspace, fault):
    _, ledger, target, other = workspace
    if fault == 'not_enrolled':
        pin = 'b' * 64
    else:
        first = enroll(ledger, targets=[other['id']] if fault == 'out_of_scope' else [target['id']])
        pin = first['event_hash']
        if fault in ('stale', 'revoked'):
            updated = enroll(ledger, active=fault != 'revoked', supersedes=pin,
                             note='Explicit changed synthetic enrollment')
            if fault == 'revoked': pin = updated['event_hash']
    before = ledger.events()
    with pytest.raises(PermissionError):
        vote(ledger, target, result(target), enrollment=pin)
    assert ledger.events() == before


def test_revocation_rejects_new_submissions_but_does_not_retract_signed_fact(workspace):
    _, ledger, target, _ = workspace
    first = enroll(ledger); enroll(ledger, 'account-two', 'person-two')
    body = result(target)
    vote(ledger, target, body)
    original = reviews(ledger)[0]
    revoked = enroll(ledger, active=False, supersedes=first['event_hash'], note='Revoke future submissions only')
    with pytest.raises(PermissionError):
        vote(ledger, target, body, enrollment=revoked['event_hash'])
    assert vote(ledger, target, body, 'account-two') == 'adjudicated'
    assert reviews(ledger)[0] == original


def test_conflicting_review_requires_latest_own_correction(workspace):
    _, ledger, target, _ = workspace
    enroll(ledger); enroll(ledger, 'account-two', 'person-two')
    yes = result(target); no = {**yes, 'label': 'failure'}
    vote(ledger, target, yes); vote(ledger, target, no, 'account-two')
    assert ledger.snapshot()['targets'][0]['result']['verification_status'] == 'conflicting_reviews'
    second = reviews(ledger)[-1]['payload']['vote_sha256']
    before = ledger.events()
    with pytest.raises(ValueError, match='latest own'):
        vote(ledger, target, yes, 'account-two', review_supersedes='b' * 64)
    assert ledger.events() == before
    assert vote(ledger, target, yes, 'account-two', review_supersedes=second) == 'adjudicated'


def test_aba_correction_round_cannot_revive_earlier_same_hash_votes(workspace):
    _, ledger, target, _ = workspace
    enroll(ledger); enroll(ledger, 'account-two', 'person-two')
    yes = result(target); no = {**yes, 'label': 'failure'}
    previous = None
    for body in (yes, no, yes):
        assert vote(ledger, target, body, supersedes=previous) == 'announced_pending_adjudication'
        assert vote(ledger, target, body, 'account-two', supersedes=previous) == 'adjudicated'
        previous = sha(body)
    before = ledger.events()
    assert vote(ledger, target, no, supersedes=previous) == 'announced_pending_adjudication'
    current = ledger.snapshot()['targets'][0]
    assert current['status'] == 'announced' and current['previous_adjudicated_result']['label'] == 'success'
    p = current['result']['review_provenance']
    assert p['distinct_authenticated_principals'] == 1 and p['typed_conflict'] is False
    assert len([e for e in ledger.events() if e['kind'] == 'result_adjudicated']) == 3
    assert ledger.events()[:len(before)] == before
    assert vote(ledger, target, no, 'account-two', supersedes=previous) == 'adjudicated'


@pytest.mark.parametrize('kind', ['ai', 'human', 'unspecified'])
def test_legacy_writer_cannot_impersonate_reserved_authenticated_namespace(workspace, kind):
    _, ledger, target, _ = workspace
    before = ledger.events()
    with pytest.raises(ValueError, match='reserved'):
        ledger.record_result(target['id'], result(target), reviewer_id=(AUTHENTICATED_REVIEW_PREFIX + sha('person-one')).upper(),
                             reviewer_kind=kind, review_note='Synthetic reserved namespace impersonation')
    assert ledger.events() == before


def test_historical_legacy_alias_cannot_block_an_authenticated_principal(workspace):
    _, ledger, target, _ = workspace
    body = result(target)
    legacy = {'reviewer_id': AUTHENTICATED_REVIEW_PREFIX + sha('person-one'), 'reviewer_kind': 'ai',
              'review_note': 'Simulated pre-migration legacy declaration', 'result': body,
              'result_sha256': sha(body), 'supersedes': None, 'review_supersedes': None}
    legacy['vote_sha256'] = sha(legacy)
    # A historical event predating the reserved-namespace rule; never a live bypass.
    with ledger.connect() as db:
        ledger._append(db, 'result_review', target['id'], legacy)
    original = ledger.events()[-1]
    enroll(ledger)
    assert vote(ledger, target, body) == 'announced_pending_adjudication'
    assert reviews(ledger)[0] == original
    assert ledger.snapshot()['targets'][0]['result']['review_provenance']['distinct_authenticated_principals'] == 1


@pytest.mark.parametrize('date_value', [None, '2026-2-01', '2026-02-30', '0000-01-01',
    (PUBLICATION_NOW.date() + timedelta(days=1)).isoformat()])
def test_source_publication_date_is_required_valid_and_not_future(workspace, publication_clock, date_value):
    _, ledger, target, _ = workspace
    enroll(ledger); body = result(target)
    body['first_public_at'] = publication_clock.isoformat()
    details = context(body)
    if date_value is None: details.pop('source_publication_date')
    else: details['source_publication_date'] = date_value
    before = ledger.events()
    with pytest.raises(ValueError):
        vote(ledger, target, body, review_context=details)
    assert ledger.events() == before


def test_source_publication_date_today_is_allowed(workspace, publication_clock):
    _, ledger, target, _ = workspace
    enroll(ledger); body = result(target)
    body['first_public_at'] = publication_clock.isoformat()
    details = context(body)
    details['source_publication_date'] = publication_clock.date().isoformat()
    assert vote(ledger, target, body, review_context=details) == 'announced_pending_adjudication'
    assert reviews(ledger)[-1]['payload']['review_context']['source_publication_date'] == details['source_publication_date']


def test_public_result_projection_excludes_private_authentication_and_context(workspace):
    _, ledger, target, _ = workspace
    enroll(ledger); enroll(ledger, 'account-two', 'person-two')
    body = result(target)
    vote(ledger, target, body); vote(ledger, target, body, 'account-two')
    encoded = json.dumps(result_view(ledger.snapshot()['targets'][0]))
    for private in ('account-one', 'account-two', 'person-one', 'person-two', AUTHENTICATED_REVIEW_PREFIX,
                    'private-synthetic-candidate', 'Private source inspection', 'Private individual review', 'a' * 64):
        assert private not in encoded
