"""Synthetic documentary acceptance exercises actual accounts, events and private files."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from arena_scientific_fixture import scientific_acceptance_document, scientifically_accept
from emeris_live import arena_result_assurance as assurance
from emeris_live.arena_review_auth import resolve_enrollment, enroll_reviewer
from emeris_live.arena_store import sha
from test_arena_core import registered, finish
from test_arena_result_reviews import result


@pytest.fixture
def agreed(tmp_path):
    ledger, target, ids = registered(tmp_path / 'runtime')
    for ident, model in zip(ids, ('A', 'B')):
        finish(ledger, ident, returned_model=model + '-fixed')
    body = result(target)
    for reviewer in ('legacy-one', 'legacy-two'):
        ledger.record_result(target['id'], body, reviewer_id=reviewer)
    return ledger, target


def current(ledger):
    return ledger.snapshot()['targets'][0]


def approval_events(ledger):
    return [event for event in ledger.events() if event['kind'] in (assurance.ACCEPT_KIND, assurance.REVOKE_KIND)]


def revise_result(ledger, label):
    snapshot = ledger.snapshot()
    target = snapshot['targets'][0]
    adjudication = next(event for event in reversed(snapshot['events']) if event['kind'] == 'result_adjudicated')
    body = deepcopy(adjudication['payload']['result'])
    body['label'] = label
    prior = [event for event in snapshot['events'] if event['kind'] == 'result_review'
             and event['payload'].get('result_sha256') == adjudication['payload']['result_sha256']
             and event['payload'].get('reviewer_authentication')]
    by_principal = {}
    for event in prior:
        by_principal[event['payload']['reviewer_authentication']['principal_id']] = event
    statuses = []
    for event in by_principal.values():
        vote = event['payload']
        account = vote['reviewer_authentication']['account_id']
        enrollment = resolve_enrollment(ledger.events(), account, target['id'])['event_hash']
        statuses.append(ledger.record_authenticated_result(target['id'], body, account_id=account,
            enrollment_sha256=enrollment, review_note='Synthetic explicit correction for the actual source result',
            review_context=vote['review_context'], supersedes=adjudication['payload']['result_sha256']))
    return statuses


def test_real_documented_acceptance_preserves_history_and_does_not_certify_credentials(agreed, tmp_path):
    ledger, target = agreed
    before = ledger.events()
    event = scientifically_accept(ledger, target, tmp_path)
    projected = current(ledger)
    value = assurance.result_assurance(projected)
    assert value['status'] == 'accepted' and value['scoring_eligible'] is True
    assert ledger.events()[:len(before)] == before
    assert projected['result']['review_provenance']['scientific_credentials_verified'] is False
    assert projected['result']['review_provenance']['independent_human_review_verified'] is False
    accepted = projected['scientific_adjudication_acceptance']
    assert accepted['assurance'] == 'operator_accepted_documented_independent_review'
    assert accepted['scientific_credentials_verified'] is False
    assert accepted['independent_human_review_verified'] is False
    assert event['payload']['operator_uid'] >= 0 and 'evidence_file' not in json.dumps(event['payload'])
    archive = ledger.path.parent / assurance.ARCHIVE_DIRECTORY
    assert archive.stat().st_mode & 0o777 == 0o700
    assert len(list(archive.iterdir())) == 5
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in archive.iterdir())


def test_private_archive_survives_removal_of_operator_input_paths(agreed, tmp_path):
    ledger, target = agreed
    document = scientific_acceptance_document(ledger, target, tmp_path)
    assurance.accept_scientific_adjudication(ledger, document)
    paths = [document['source_evidence_file']]
    paths.extend(review[key] for review in document['reviews']
                 for key in ('qualification_evidence_file', 'independence_statement_file'))
    for name in paths:
        Path(name).unlink()
    assert assurance.result_assurance(current(ledger))['scoring_eligible'] is True
    assert assurance.result_assurance(deepcopy(current(ledger)))['scoring_eligible'] is True


@pytest.mark.parametrize('kind', ['ai', 'human', 'unspecified'])
def test_declared_review_kind_and_true_flags_cannot_issue_scientific_acceptance(tmp_path, kind):
    ledger, target, _ = registered(tmp_path / 'runtime')
    body = result(target)
    for reviewer in ('one', 'two'):
        ledger.record_result(target['id'], body, reviewer_id=reviewer, reviewer_kind=kind,
                             review_note='Synthetic declared review; not an authenticated reviewer')
    projected = current(ledger)
    projected['result']['review_provenance'].update(scientific_credentials_verified=True,
                                                   independent_human_review_verified=True)
    projected['scientific_adjudication_acceptance'] = {'status': 'accepted', 'scoring_eligible': True}
    assert assurance.result_assurance(projected)['scoring_eligible'] is False
    with pytest.raises(ValueError):
        assurance.prepare_acceptance(ledger, target['id'])
    assert approval_events(ledger) == []


@pytest.mark.parametrize('label', ['mixed', 'inconclusive', 'not_adjudicable'])
def test_accepted_nonbinary_outcome_remains_visible_without_binary_scoring(agreed, tmp_path, label):
    ledger, target = agreed
    scientific_acceptance_document(ledger, target, tmp_path)
    assert revise_result(ledger, label)[-1] == 'adjudicated'
    scientifically_accept(ledger, target, tmp_path)
    value = assurance.result_assurance(current(ledger))
    assert value['status'] == 'accepted' and value['scoring_eligible'] is False
    assert value['reason'] == 'accepted_outcome_not_binary'


@pytest.mark.parametrize('field,value', [('label', 'failure'), ('source_sha256', 'c' * 64),
    ('source_url', 'https://other.example/source'), ('first_public_at', '2020-01-01T00:00:00Z'),
    ('endpoint_match_note', 'Altered endpoint correspondence')])
def test_snapshot_core_result_mutation_invalidates_projection(agreed, tmp_path, field, value):
    ledger, target = agreed
    scientifically_accept(ledger, target, tmp_path)
    projected = current(ledger)
    projected['result'][field] = value
    assert assurance.result_assurance(projected)['scoring_eligible'] is False


def test_json_roundtrip_or_modified_acceptance_cannot_self_certify(agreed, tmp_path):
    ledger, target = agreed
    scientifically_accept(ledger, target, tmp_path)
    projected = current(ledger)
    assert assurance.result_assurance(json.loads(json.dumps(projected)))['scoring_eligible'] is False
    projected['scientific_adjudication_acceptance']['assurance'] = 'forged'
    assert assurance.result_assurance(projected)['scoring_eligible'] is False


@pytest.mark.parametrize('fault', ['same_principal', 'wrong_vote', 'wrong_round', 'wrong_target', 'wrong_project', 'wrong_source',
                                  'false_confirmation', 'conflict', 'symlink', 'oversized'])
def test_invalid_acceptance_never_appends_approval(agreed, tmp_path, monkeypatch, fault):
    ledger, target = agreed
    document = scientific_acceptance_document(ledger, target, tmp_path)
    before = ledger.events()
    if fault == 'same_principal':
        document['reviews'][1] = deepcopy(document['reviews'][0])
    elif fault == 'wrong_vote':
        document['reviews'][0]['vote_sha256'] = 'c' * 64
    elif fault == 'wrong_round':
        document['adjudication_event_sha256'] = before[0]['event_hash']
    elif fault == 'wrong_target':
        document['target_binding_sha256'] = 'c' * 64
    elif fault == 'wrong_project':
        document['project_id'] = 'wrong-project'
    elif fault == 'wrong_source':
        Path(document['source_evidence_file']).write_bytes(b'changed clinical source')
    elif fault == 'false_confirmation':
        document['reviews'][0]['confirmations']['no_material_conflict'] = False
    elif fault == 'conflict':
        document['reviews'][0]['independence_conclusion'] = 'conflict_present'
    elif fault == 'symlink':
        original = Path(document['reviews'][0]['qualification_evidence_file'])
        link = tmp_path / 'qualification-link'
        link.symlink_to(original)
        document['reviews'][0]['qualification_evidence_file'] = str(link)
    else:
        monkeypatch.setattr(assurance, 'MAX_EVIDENCE_BYTES', 16)
    with pytest.raises((ValueError, OSError, PermissionError)):
        assurance.accept_scientific_adjudication(ledger, document)
    assert ledger.events() == before and approval_events(ledger) == []


@pytest.mark.parametrize('material', ['source_evidence', 'qualification_evidence', 'independence_statement'])
def test_private_original_change_or_loss_disqualifies_current_result(agreed, tmp_path, material):
    ledger, target = agreed
    event = scientifically_accept(ledger, target, tmp_path)
    metadata = event['payload'][material] if material == 'source_evidence' else event['payload']['reviews'][0][material]
    original = ledger.path.parent / assurance.ARCHIVE_DIRECTORY / (metadata['sha256'] + '.bin')
    original.write_bytes(b'tampered archived document')
    projected = current(ledger)
    assert assurance.result_assurance(projected)['status'] == 'agreement_pending_independent_review'
    assert 'integrity' in assurance.result_assurance(projected)['private_reason']
    original.unlink()
    assert assurance.result_assurance(current(ledger))['scoring_eligible'] is False


def test_explicit_revisions_revoke_and_reaccept_with_latest_event_only(agreed, tmp_path):
    ledger, target = agreed
    document = scientific_acceptance_document(ledger, target, tmp_path)
    first = assurance.accept_scientific_adjudication(ledger, document)
    with pytest.raises(ValueError, match='identical'):
        assurance.accept_scientific_adjudication(ledger, document, supersedes=first['event_hash'])
    revised = deepcopy(document)
    revised['note'] = 'Synthetic revised responsible acceptance note without changing the original facts'
    with pytest.raises(ValueError, match='supersede'):
        assurance.accept_scientific_adjudication(ledger, revised)
    second = assurance.accept_scientific_adjudication(ledger, revised, supersedes=first['event_hash'])
    with pytest.raises(ValueError):
        assurance.revoke_scientific_adjudication(ledger, target['id'], 'Synthetic acceptor',
            'Synthetic explicit revocation for isolated testing', supersedes=first['event_hash'])
    revoked = assurance.revoke_scientific_adjudication(ledger, target['id'], 'Synthetic acceptor',
        'Synthetic explicit revocation for isolated testing', supersedes=second['event_hash'])
    assert assurance.result_assurance(current(ledger))['scoring_eligible'] is False
    assert assurance.result_assurance(current(ledger))['private_reason'] == 'scientific_acceptance_revoked'
    third = assurance.accept_scientific_adjudication(ledger, revised, supersedes=revoked['event_hash'])
    assert third['payload']['supersedes'] == revoked['event_hash']
    assert assurance.result_assurance(current(ledger))['scoring_eligible'] is True


def test_ordinary_account_authorization_revocation_keeps_previously_valid_facts(agreed, tmp_path):
    ledger, target = agreed
    acceptance = scientifically_accept(ledger, target, tmp_path)
    review = acceptance['payload']['reviews'][0]
    grant = resolve_enrollment(ledger.events(), review['account_id'], target['id'])
    enroll_reviewer(ledger, review['account_id'], review['principal_id'], [], 'a' * 64,
        'Synthetic future access withdrawal; not withdrawal of previous factual review',
        active=False, supersedes=grant['event_hash'])
    assert assurance.result_assurance(current(ledger))['scoring_eligible'] is True


def test_changed_context_own_supersede_requires_latest_actual_vote(agreed, tmp_path):
    ledger, target = agreed
    document = scientific_acceptance_document(ledger, target, tmp_path)
    events = ledger.events()
    adjudication = next(event for event in reversed(events) if event['kind'] == 'result_adjudicated')
    original_votes = [next(event for event in events if event['event_hash'] == review['review_event_sha256'])
                      for review in document['reviews']]
    stale = None
    for index, event in enumerate(original_votes):
        old = event['payload']
        account = old['reviewer_authentication']['account_id']
        grant = resolve_enrollment(ledger.events(), account, target['id'])
        args = dict(account_id=account, enrollment_sha256=grant['event_hash'],
                    review_context=old['review_context'], supersedes=adjudication['payload']['result_sha256'])
        ledger.record_authenticated_result(target['id'], old['result'],
            review_note='Synthetic new round review with original source inspection', **args)
        if index == 0:
            stale = ledger.events()[-1]
            changed = deepcopy(old['review_context'])
            changed['excerpt'] += ' Additional private source correspondence check.'
            args['review_context'] = changed
            ledger.record_authenticated_result(target['id'], old['result'],
                review_note='Synthetic corrected private source context with same core fact',
                review_supersedes=stale['payload']['vote_sha256'], **args)
    template = assurance.prepare_acceptance(ledger, target['id'])
    revised = {**document, **{key: value for key, value in template.items() if key != 'reviews'}}
    revised['source_evidence_file'] = document['source_evidence_file']
    revised['acceptor'], revised['note'] = document['acceptor'], document['note']
    revised['reviews'] = [{**manual, 'review_event_sha256': actual['review_event_sha256'],
                           'vote_sha256': actual['vote_sha256']}
                          for manual, actual in zip(document['reviews'], template['reviews'])]
    invalid = deepcopy(revised)
    invalid['reviews'][0].update(review_event_sha256=stale['event_hash'], vote_sha256=stale['payload']['vote_sha256'])
    before = ledger.events()
    with pytest.raises(ValueError, match='latest actual review'):
        assurance.accept_scientific_adjudication(ledger, invalid)
    assert ledger.events() == before
    assurance.accept_scientific_adjudication(ledger, revised)
    assert assurance.result_assurance(current(ledger))['scoring_eligible'] is True


def test_correction_and_aba_round_never_revive_old_acceptance(agreed, tmp_path):
    ledger, target = agreed
    first = scientifically_accept(ledger, target, tmp_path)
    assert revise_result(ledger, 'failure')[-1] == 'adjudicated'
    assert assurance.result_assurance(current(ledger))['scoring_eligible'] is False
    assert revise_result(ledger, 'success')[-1] == 'adjudicated'
    projected = current(ledger)
    assert projected['result']['result_sha256'] == first['payload']['result_sha256']
    assert projected['result_adjudication_event_sha256'] != first['payload']['adjudication_event_sha256']
    assert assurance.result_assurance(projected)['scoring_eligible'] is False
    new = scientifically_accept(ledger, target, tmp_path)
    assert new['payload']['supersedes'] == first['event_hash']
    assert assurance.result_assurance(current(ledger))['scoring_eligible'] is True


def test_historical_replay_never_becomes_prospective_gold(agreed, tmp_path):
    ledger, target = agreed
    scientifically_accept(ledger, target, tmp_path)
    projected = current(ledger)
    projected['record_kind'] = 'historical_replay'
    assert assurance.result_assurance(projected)['status'] == 'historical_replay'
    assert assurance.result_assurance(projected)['scoring_eligible'] is False
