"""Source corrections use isolated ledgers: no transport, credentials or live data."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Barrier

import pytest

from emeris_live.arena_metrics import build_metrics
from emeris_live.arena_store import ArenaLedger
from test_arena_core import finish, outcome, registered, spec


REASON = 'Source verification identified an error before any model execution.'
NOTE = 'The frozen source extraction omitted a material qualification.'
REFERENCE = 'https://example.org/source-correction'


def issue(ledger, **extra):
    return ledger.record_evidence_issue('e1', 'a' * 64, note=NOTE,
        reference_url=REFERENCE, **extra)


def source_variants(ledger):
    """Same ID/different hash and same hash/different ID are separate versions."""
    targets, slots = [], []
    base_target, base_slots = spec()
    for index, (evidence_id, source_hash) in enumerate([
        ('e1', 'a' * 64), ('e1', 'b' * 64), ('e2', 'a' * 64)
    ]):
        target = deepcopy(base_target)
        target.update(id=f'target-{index}', project_id=f'asset-{index}')
        slot = deepcopy(base_slots[0])
        slot['target_id'] = target['id']
        slot['packet']['evidence'][0].update(id=evidence_id, source_sha256=source_hash)
        targets.append(target)
        slots.append(slot)
    return targets, ledger.register('variants', targets, slots, {})


def test_withdraw_all_fifty_slots_once_without_start_or_hash_changes(tmp_path):
    ledger = ArenaLedger(tmp_path)
    target, original = spec()
    slots = []
    for index in range(50):
        slot = deepcopy(original[0])
        slot.update(model_id=f'fixture-{index}', requested_model=f'fixture-{index}-fixed')
        slots.append(slot)
    ids = ledger.register('fifty', [target], slots, {})
    before = ledger.snapshot()
    event = ledger.withdraw_unstarted_cohort('fifty', REASON)
    after = ledger.snapshot()
    assert after['events'][:-1] == before['events']
    assert event['payload']['assigned_slots'] == 50
    assert event['payload']['model_calls'] == 0
    assert not any(e['kind'] in ('run_started', 'run_completed') for e in after['events'])
    for old, new in zip(before['runs'], after['runs']):
        assert new['status'] == new['execution_state'] == 'withdrawn'
        assert new['probability'] is None and new['withdrawn_before_execution'] is True
        for field in ('id', 'packet', 'input_sha256', 'requested_model', 'workflow_version', 'registered_at'):
            assert old[field] == new[field]
    assert after['targets'][0]['execution_status'] == 'withdrawn_before_execution'
    with pytest.raises(ValueError, match='already withdrawn'):
        ledger.withdraw_unstarted_cohort('fifty', REASON)
    for ident in ids:
        with pytest.raises(ValueError, match='withdrawn'):
            ledger.start(ident)
    assert ledger.events() == after['events']
    metrics = build_metrics(after)
    assert metrics['assigned'] == metrics['withdrawn'] == 50
    assert metrics['answered'] == metrics['failed'] == metrics['pending'] == 0
    assert all(row['brier'] is None for row in metrics['by_model'])


@pytest.mark.parametrize('completed', [False, True])
def test_started_or_completed_cohort_cannot_withdraw(tmp_path, completed):
    ledger, _, ids = registered(tmp_path)
    ledger.start(ids[0])
    if completed:
        ledger.finish(ids[0], {'status': 'failed', 'probability': None})
    before = ledger.events()
    with pytest.raises(ValueError, match='executed'):
        ledger.withdraw_unstarted_cohort('season-test', REASON)
    assert ledger.events() == before
    assert ledger.snapshot()['runs'][1]['status'] == 'pending'


def test_withdrawal_append_failure_rolls_back_atomically(tmp_path, monkeypatch):
    ledger, _, _ = registered(tmp_path)
    before = ledger.events()
    original = ledger._append
    def fail_after_insert(db, kind, entity_id, payload):
        result = original(db, kind, entity_id, payload)
        if kind == 'cohort_withdrawn':
            raise RuntimeError('isolated simulated failure after insertion')
        return result
    monkeypatch.setattr(ledger, '_append', fail_after_insert)
    with pytest.raises(RuntimeError):
        ledger.withdraw_unstarted_cohort('season-test', REASON)
    assert ledger.events() == before
    assert all(r['status'] == 'pending' for r in ledger.snapshot()['runs'])
    monkeypatch.setattr(ledger, '_append', original)
    ledger.withdraw_unstarted_cohort('season-test', REASON)


@pytest.mark.parametrize('iteration', range(4))
def test_start_racing_withdrawal_has_exactly_one_winner(tmp_path, iteration):
    ledger, _, ids = registered(tmp_path)
    barrier = Barrier(2)
    def perform(action):
        barrier.wait(timeout=5)
        try:
            action()
            return True
        except ValueError:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        start = pool.submit(perform, lambda: ledger.start(ids[0]))
        withdraw = pool.submit(perform, lambda: ledger.withdraw_unstarted_cohort('season-test', REASON))
        assert sum([start.result(timeout=20), withdraw.result(timeout=20)]) == 1
    kinds = [e['kind'] for e in ledger.events()]
    assert kinds.count('run_started') + kinds.count('cohort_withdrawn') == 1


def test_withdrawal_rejects_unknown_cohort_and_empty_reason(tmp_path):
    ledger, _, _ = registered(tmp_path)
    before = ledger.events()
    for ident, reason in [('unknown', REASON), ('season-test', ' ' * 20)]:
        with pytest.raises(ValueError):
            ledger.withdraw_unstarted_cohort(ident, reason)
    assert ledger.events() == before


def test_issue_matches_exact_source_identity_and_version_and_preserves_hashes(tmp_path):
    ledger = ArenaLedger(tmp_path)
    _, ids = source_variants(ledger)
    finish(ledger, ids[0], .8, returned_model='A-fixed')
    before = ledger.snapshot()
    issue(ledger)
    after = ledger.snapshot()
    assert after['events'][:-1] == before['events']
    assert [bool(r.get('input_evidence_issues')) for r in after['runs']] == [True, False, False]
    for old, new in zip(before['runs'], after['runs']):
        for field in ('packet', 'input_sha256', 'output_sha256', 'locked_at_utc', 'probability', 'status'):
            assert old.get(field) == new.get(field)
    assert after['runs'][0]['input_evidence_issues'][0]['event_hash'] == after['events'][-1]['event_hash']


def test_issue_is_append_only_and_warning_can_escalate_to_blocking(tmp_path):
    ledger, _, ids = registered(tmp_path)
    issue(ledger, severity='warning')
    before = ledger.events()
    with pytest.raises(ValueError, match='already recorded'):
        issue(ledger, severity='warning')
    assert ledger.events() == before
    issue(ledger, severity='blocking')
    assert ledger.events()[:-1] == before
    assert [i['severity'] for i in ledger.snapshot()['runs'][0]['input_evidence_issues']] == ['warning', 'blocking']
    with pytest.raises(ValueError, match='blocking evidence issue'):
        ledger.start(ids[0])


def test_known_blocking_issue_prevents_start_only_for_exact_source(tmp_path):
    ledger = ArenaLedger(tmp_path)
    _, ids = source_variants(ledger)
    issue(ledger)
    before = ledger.events()
    with pytest.raises(ValueError, match='blocking evidence issue'):
        ledger.start(ids[0])
    assert ledger.events() == before
    ledger.start(ids[1])
    ledger.start(ids[2])


@pytest.mark.parametrize('evidence_id,source_hash,note,url,severity', [
    ('missing', 'a' * 64, NOTE, REFERENCE, 'blocking'),
    ('e1', 'b' * 64, NOTE, REFERENCE, 'blocking'),
    ('e1', 'a' * 64, ' ' * 20, REFERENCE, 'blocking'),
    ('e1', 'a' * 64, NOTE, 'https://user:secret@example.org/file', 'blocking'),
    ('e1', 'a' * 64, NOTE, REFERENCE, 'unsupported'),
])
def test_invalid_evidence_issue_does_not_append(tmp_path, evidence_id, source_hash, note, url, severity):
    ledger, _, _ = registered(tmp_path)
    before = ledger.events()
    with pytest.raises(ValueError):
        ledger.record_evidence_issue(evidence_id, source_hash, note=note, reference_url=url, severity=severity)
    assert ledger.events() == before


@pytest.mark.parametrize('field,value', [
    ('input_evidence_issues', [{'severity': 'blocking'}]),
    ('withdrawn_at', 'fabricated'), ('withdrawn_before_execution', True)
])
def test_provider_cannot_inject_derived_correction_fields(tmp_path, field, value):
    ledger, _, ids = registered(tmp_path)
    ledger.start(ids[0])
    before = ledger.events()
    with pytest.raises(ValueError, match='frozen'):
        ledger.finish(ids[0], {'status': 'failed', 'probability': None, field: value})
    assert ledger.events() == before


@pytest.mark.parametrize('severity,scored,blocked', [('warning', 3, 0), ('blocking', 2, 1)])
def test_blocking_issue_excludes_loss_but_retains_execution_denominator(tmp_path, severity, scored, blocked):
    ledger = ArenaLedger(tmp_path)
    targets, ids = source_variants(ledger)
    for ident, target in zip(ids, targets):
        ledger.start(ident)
        evidence_id = 'e2' if target['id'] == 'target-2' else 'e1'
        ledger.finish(ident, {'status': 'predicted', 'probability': .8,
                             'evidence_ids': [evidence_id], 'returned_model': 'A-fixed'})
        result = outcome()
        ledger.record_result(target['id'], result, reviewer_id='reviewer-one')
        ledger.record_result(target['id'], result, reviewer_id='reviewer-two')
    issue(ledger, severity=severity)
    metrics = build_metrics(ledger.snapshot())
    assert metrics['assigned'] == metrics['answered'] == 3
    assert metrics['input_blocked'] == blocked
    row = metrics['by_model'][0]
    assert row['scored_tasks'] == scored and row['input_blocked'] == blocked
    assert row['coverage'] == 1 and row['eligible_coverage'] == scored / 3
    assert row['brier'] == pytest.approx(.04)


def test_all_blocked_inputs_remove_scores_and_paired_comparisons(tmp_path):
    ledger, target, ids = registered(tmp_path)
    for ident, model in zip(ids, ('A', 'B')):
        finish(ledger, ident, returned_model=model + '-fixed')
    result = outcome()
    for reviewer in ('one', 'two'):
        ledger.record_result(target['id'], result, reviewer_id=reviewer)
    assert build_metrics(ledger.snapshot())['common_tasks'] == 1
    issue(ledger)
    metrics = build_metrics(ledger.snapshot())
    assert metrics['assigned'] == metrics['answered'] == metrics['input_blocked'] == 2
    assert metrics['common_tasks'] == 0
    assert metrics['baseline']['common_brier'] is None
    for row in metrics['by_model']:
        assert row['scored_tasks'] == 0
        assert row['brier'] is row['log_loss'] is row['accuracy'] is None
    assert metrics['paired_differences'][0]['delta_brier_a_minus_b'] is None
