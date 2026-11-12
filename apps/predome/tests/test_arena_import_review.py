"""Synthetic imported cohorts retain the actual host authorization/review gates."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib

import pytest

from emeris_live import arena_result_assurance as assurance
from emeris_live.arena_review_auth import enroll_reviewer, resolve_enrollment
from emeris_live.arena_store import ArenaLedger
from emeris_live.store import Store
from test_arena_core import registered
from test_arena_imports import bundle_factory, signed
from test_arena_result_reviews import result


@pytest.fixture
def imported(tmp_path, bundle_factory):
    sample = bundle_factory(cohort_id='synthetic-review-import')
    ledger, native, _ = registered(tmp_path / 'runtime')
    store = Store(ledger.path.parent)
    with store.connect() as db:
        db.executemany('INSERT INTO users VALUES(?,?,?)', [
            ('account-one', 'reviewer-one', 'synthetic-unusable-password-hash'),
            ('account-two', 'reviewer-two', 'synthetic-unusable-password-hash')])
    before = ledger.events()
    receipt = ledger.import_sealed_cohort(sample['path'], sample['payload']['bundle_sha256'])
    target = next(t for t in ledger.snapshot()['targets'] if t['id'] == sample['target_id'])
    assert ledger.events()[:-1] == before
    return ledger, target, sample, receipt, native


def projected(ledger, target):
    return next(t for t in ledger.snapshot()['targets'] if t['id'] == target['id'])


def grant(ledger, target, account='account-one', principal='synthetic-person-one'):
    return enroll_reviewer(ledger, account, principal, [target['id']], 'a' * 64,
        'Synthetic operator account linkage; no real qualifications or identity proof')


def outcome(target):
    source = b'SYNTHETIC ORIGINAL RESULT DOCUMENT FOR ISOLATED IMPORT REVIEW TESTS\n'
    body = result(target)
    body['source_sha256'] = hashlib.sha256(source).hexdigest()
    context = {'candidate_id': 'synthetic-import-result', 'source_sha256': body['source_sha256'],
        'excerpt': 'Synthetic original source inspection for the exact registered trial ' + target['nct_id'],
        'source_publication_date': datetime.now(timezone.utc).date().isoformat(),
        'publication_date_basis': 'reviewer_provided_not_independently_verified',
        'confirmations': {key: True for key in ('exact_trial', 'primary_endpoint', 'prespecified_analysis',
            'first_public_release', 'source_inspected')}}
    return body, context, source


def review(ledger, target, body, context, account, *, supersedes=None):
    enrollment = resolve_enrollment(ledger.events(), account, target['id'])
    return ledger.record_authenticated_result(target['id'], deepcopy(body), account_id=account,
        enrollment_sha256=enrollment['event_hash'],
        review_note='Synthetic individual source review; no clinical validation or real scientific qualification',
        review_context=deepcopy(context), supersedes=supersedes)


def agree(ledger, target):
    body, context, source = outcome(target)
    grant(ledger, target)
    grant(ledger, target, 'account-two', 'synthetic-person-two')
    assert review(ledger, target, body, context, 'account-one') == 'announced_pending_adjudication'
    assert review(ledger, target, body, context, 'account-two') == 'adjudicated'
    return body, context, source


def manual_document(ledger, target, tmp_path, source):
    document = assurance.prepare_acceptance(ledger, target['id'])
    directory = tmp_path / ('synthetic-documents-' + str(len(ledger.events())))
    directory.mkdir()
    original = directory / 'original.txt'
    original.write_bytes(source)
    document.update(source_evidence_file=str(original), acceptor='Synthetic responsible test operator',
        note='Synthetic isolated test acceptance only; no real independent qualifications or clinical validity')
    for index, entry in enumerate(document['reviews']):
        qualification = directory / ('qualification-' + str(index) + '.txt')
        independence = directory / ('independence-' + str(index) + '.txt')
        qualification.write_text('SYNTHETIC TEST QUALIFICATION DOCUMENT ' + str(index))
        independence.write_text('SYNTHETIC TEST INDEPENDENCE STATEMENT ' + str(index))
        entry.update(scientific_role='Synthetic scientific reviewer for isolated imported target',
            qualification_check_method='manual_original_documents',
            qualification_check_note='Synthetic manual qualification document check for an isolated engineering test',
            qualification_evidence_file=str(qualification), independence_conclusion='independent_for_registered_target',
            independence_check_note='Synthetic target-specific independence documentation checked in this test',
            independence_statement_file=str(independence),
            confirmations={key: True for key in assurance.CONFIRMATIONS})
    return document


def test_imported_ai_citation_reviews_do_not_authorize_people_or_accept_results(imported):
    ledger, target, sample, _, _ = imported
    source = sample['payload']['source_events']
    assert any(e['kind'] == 'citation_review' and e['payload']['reviewer_kind'] == 'ai' for e in source)
    assert target['status'] == 'pending' and target['result'] is None
    assert assurance.result_assurance(target)['scoring_eligible'] is False
    for account in ('ai-source-a', 'account-one'):
        with pytest.raises(PermissionError, match='no active enrollment'):
            resolve_enrollment(ledger.events(), account, target['id'])
    with pytest.raises(ValueError, match='current agreed source result'):
        assurance.prepare_acceptance(ledger, target['id'])
    assert not any(e['kind'] in ('reviewer_enrollment', 'result_review', 'result_adjudicated',
        assurance.ACCEPT_KIND, assurance.REVOKE_KIND) for e in ledger.events())


def test_imported_target_uses_actual_host_reviews_and_separate_documentary_acceptance(imported, tmp_path):
    ledger, target, sample, receipt, _ = imported
    sealed_prefix = deepcopy(ledger.events())
    _, _, source = agree(ledger, target)
    pending = projected(ledger, target)
    assert pending['status'] == 'adjudicated'
    assert assurance.result_assurance(pending)['scoring_eligible'] is False
    document = manual_document(ledger, target, tmp_path, source)
    accepted = assurance.accept_scientific_adjudication(ledger, document)
    actual = projected(ledger, target)
    assert assurance.result_assurance(actual)['scoring_eligible'] is True
    assert actual['scientific_adjudication_acceptance']['scientific_credentials_verified'] is False
    assert actual['scientific_adjudication_acceptance']['independent_human_review_verified'] is False
    assert ledger.events()[:len(sealed_prefix)] == sealed_prefix
    assert sealed_prefix[-1]['payload']['source_events'] == sample['events']
    assert sealed_prefix[-1]['created_at'] == receipt['synced_at_utc']
    assert next(e for e in sample['events'] if e['kind'] == 'target_registered')['payload']['result'] is None
    host_votes = [e for e in ledger.events() if e['kind'] == 'result_review']
    assert len(host_votes) == 2 and accepted['payload']['reviews'][0]['review_event_sha256'] in {
        e['event_hash'] for e in host_votes}
    assert all(e['seq'] > len(sealed_prefix) for e in host_votes)
    before = ledger.events()
    duplicate = ledger.import_sealed_cohort(sample['path'], sample['payload']['bundle_sha256'])
    assert duplicate['appended'] is False and ledger.events() == before
    assert assurance.result_assurance(projected(ledger, target))['scoring_eligible'] is True


def test_future_import_cannot_retroactively_authorize_an_earlier_host_enrollment(imported, tmp_path):
    ledger, target, sample, _, native = imported
    native_grant = grant(ledger, native)
    payload = deepcopy(native_grant['payload'])
    payload['target_ids'] = [target['id']]
    # A hash-consistent but semantically invalid early grant is deliberate:
    # a future import must not repair its historical authorization scope.
    earlier = ArenaLedger(tmp_path / 'earlier-host')
    with earlier.connect() as db:
        earlier._append(db, 'reviewer_enrollment', 'account-one', payload)
    earlier.import_sealed_cohort(sample['path'], sample['payload']['bundle_sha256'])
    with pytest.raises(PermissionError, match='Invalid reviewer enrollment history'):
        resolve_enrollment(earlier.events(), 'account-one', target['id'])


def test_imported_target_enrollment_remains_exact_and_cannot_claim_unknown_scope(imported):
    ledger, target, _, _, native = imported
    event = grant(ledger, target)
    before = ledger.events()
    with pytest.raises(PermissionError, match='does not cover'):
        resolve_enrollment(before, 'account-one', native['id'])
    with pytest.raises(ValueError, match='unregistered target'):
        enroll_reviewer(ledger, 'account-two', 'synthetic-person-two', ['not-yet-imported'], 'b' * 64,
            'Synthetic unregistered scope must not be granted')
    assert ledger.events() == before
    assert resolve_enrollment(before, 'account-one', target['id'])['event_hash'] == event['event_hash']


def test_imported_target_alias_accounts_cannot_supply_two_independent_votes(imported):
    ledger, target, _, _, _ = imported
    body, context, _ = outcome(target)
    grant(ledger, target)
    grant(ledger, target, 'account-two', 'synthetic-person-one')
    review(ledger, target, body, context, 'account-one')
    before = ledger.events()
    with pytest.raises(ValueError, match='already voted'):
        review(ledger, target, body, context, 'account-two')
    assert ledger.events() == before
    assert projected(ledger, target)['status'] == 'announced'
    with pytest.raises(ValueError, match='current agreed source result'):
        assurance.prepare_acceptance(ledger, target['id'])


def test_later_account_revocation_preserves_valid_historical_review_but_blocks_new_vote(imported, tmp_path):
    ledger, target, _, _, _ = imported
    body, context, source = agree(ledger, target)
    document = manual_document(ledger, target, tmp_path, source)
    accepted = assurance.accept_scientific_adjudication(ledger, document)
    old = resolve_enrollment(ledger.events(), 'account-one', target['id'])
    enroll_reviewer(ledger, 'account-one', old['principal_id'], [], 'a' * 64,
        'Synthetic revoke future account access without rewriting prior review facts',
        active=False, supersedes=old['event_hash'])
    before = ledger.events()
    with pytest.raises(PermissionError, match='revoked'):
        ledger.record_authenticated_result(target['id'], body, account_id='account-one',
            enrollment_sha256=ledger.events()[-1]['event_hash'],
            review_note='Synthetic forbidden post-revocation review attempt', review_context=context,
            supersedes=document['result_sha256'])
    assert ledger.events() == before
    assert assurance.result_assurance(projected(ledger, target))['scoring_eligible'] is True
    revoked = assurance.revoke_scientific_adjudication(ledger, target['id'], 'Synthetic operator',
        'Synthetic explicit scientific acceptance withdrawal for isolated testing', supersedes=accepted['event_hash'])
    assert assurance.result_assurance(projected(ledger, target))['private_reason'] == 'scientific_acceptance_revoked'
    restored = assurance.accept_scientific_adjudication(ledger, document, supersedes=revoked['event_hash'])
    assert restored['payload']['supersedes'] == revoked['event_hash']
    assert assurance.result_assurance(projected(ledger, target))['scoring_eligible'] is True


def test_imported_result_correction_requires_new_agreement_and_new_acceptance(imported, tmp_path):
    ledger, target, _, _, _ = imported
    body, context, source = agree(ledger, target)
    document = manual_document(ledger, target, tmp_path, source)
    original = assurance.accept_scientific_adjudication(ledger, document)
    revised = {**body, 'label': 'failure'}
    assert review(ledger, target, revised, context, 'account-one',
        supersedes=document['result_sha256']) == 'announced_pending_adjudication'
    assert assurance.result_assurance(projected(ledger, target))['scoring_eligible'] is False
    with pytest.raises(ValueError, match='correction is awaiting'):
        assurance.prepare_acceptance(ledger, target['id'])
    assert review(ledger, target, revised, context, 'account-two', supersedes=document['result_sha256']) == 'adjudicated'
    assert assurance.result_assurance(projected(ledger, target))['scoring_eligible'] is False
    correction = manual_document(ledger, target, tmp_path, source)
    with pytest.raises(ValueError, match='supersede'):
        assurance.accept_scientific_adjudication(ledger, correction)
    second = assurance.accept_scientific_adjudication(ledger, correction, supersedes=original['event_hash'])
    assert second['payload']['result_sha256'] != original['payload']['result_sha256']
    current = projected(ledger, target)
    assert current['result']['label'] == 'failure'
    assert assurance.result_assurance(current)['scoring_eligible'] is True
