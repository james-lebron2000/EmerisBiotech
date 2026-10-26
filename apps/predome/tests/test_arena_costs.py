"""Synthetic cost records test accounting, never billing or clinical performance."""
from copy import deepcopy
import pytest

from emeris_live.arena_metrics import build_metrics, cost_summary, paired_cost, patent_pairs


def row(**changes):
    return {'id': 'run', 'target_id': 'target', 'cohort_id': 'cohort',
        'scope': 'prospective_challenge', 'track': 'same_evidence', 'condition': 'clinical_only',
        'model_id': 'fixture-model', 'requested_model': 'fixture-v1', 'workflow_version': 'single-v1',
        'status': 'failed', 'probability': None, 'cost_amount': .02, 'cost_currency': 'CNY',
        'cost_usd': None, 'cost_basis': 'configured_rates_estimate', **changes}


def test_model_metrics_keep_native_currency_and_failed_call_spend():
    rows = [row(), row(id='second', cost_amount=.03, status='abstained')]
    metrics = build_metrics({'cohorts': [{'id': 'cohort'}], 'runs': rows})
    actual = metrics['by_model'][0]
    assert actual['cost_complete'] is True
    assert actual['cost_amount'] == pytest.approx(.05)
    assert actual['cost_currency'] == 'CNY' and actual['cost_usd'] is None
    assert actual['cost_totals_by_currency'] == {'CNY': pytest.approx(.05)}
    assert actual['cost_is_estimate'] is True
    assert actual['assigned'] == 2 and actual['failed'] == 1
    assert metrics['ranking_allowed'] is False and actual['common_brier'] is None


def test_mixed_currencies_are_complete_but_never_combined():
    actual = cost_summary([row(), row(cost_currency='USD', cost_amount=.03, cost_usd=.03)])
    assert actual['cost_complete'] is True
    assert actual['cost_totals_by_currency'] == {'CNY': .02, 'USD': .03}
    assert actual['cost_amount'] is None and actual['cost_currency'] is None and actual['cost_usd'] is None


def test_partial_and_unknown_costs_do_not_turn_into_zero_or_complete_totals():
    actual = cost_summary([row(cost_amount=0), row(cost_amount=None, known_cost_amount=.03),
                           row(cost_amount=None), row(cost_amount=None, status='pending')])
    assert actual['cost_complete'] is False and actual['cost_amount'] is None
    assert actual['cost_totals_by_currency'] == {'CNY': 0}
    assert actual['known_cost_totals_by_currency'] == {'CNY': .03}
    assert actual['cost_recorded_runs'] == 1 and actual['cost_partial_runs'] == 1
    assert actual['cost_unknown_runs'] == 2


def test_legacy_usd_alias_is_not_double_counted():
    actual = cost_summary([row(cost_currency=None, cost_amount=None, cost_usd=.02),
                           row(cost_currency='USD', cost_amount=.03, cost_usd=.03)])
    assert actual['cost_complete'] is True and actual['cost_usd'] == pytest.approx(.05)
    assert actual['known_cost_totals_by_currency'] == {'USD': pytest.approx(.05)}
    assert actual['cost_basis'] == 'configured_rates_estimate'


@pytest.mark.parametrize('changes', [
    {'cost_amount': True}, {'cost_amount': -.01}, {'cost_amount': float('nan')},
    {'cost_amount': float('inf')}, {'cost_currency': 'EUR'},
    {'cost_usd': .02}, {'cost_currency': 'USD', 'cost_usd': .03},
    {'cost_amount': None, 'cost_usd': .03},
])
def test_invalid_or_contradictory_cost_records_remain_unknown(changes):
    actual = cost_summary([row(**changes)])
    assert actual['cost_complete'] is False and actual['cost_amount'] is None
    assert actual['known_cost_totals_by_currency'] == {}


def test_paired_cost_requires_complete_matching_currency_and_basis():
    assert paired_cost(row(), row(cost_amount=.05)) == {
        'comparable': True, 'currency': 'CNY', 'delta_amount': pytest.approx(.03),
        'basis': 'configured_rates_estimate', 'reason': None, 'is_estimate': True}
    for changes, reason in [({'cost_amount': None, 'known_cost_amount': .02}, 'incomplete_cost'),
                            ({'cost_currency': 'USD'}, 'currency_mismatch'),
                            ({'cost_basis': None}, 'cost_basis_unconfirmed')]:
        actual = paired_cost(row(), row(**changes))
        assert actual['comparable'] is False and actual['delta_amount'] is None
        assert actual['reason'] == reason


def test_patent_comparison_exposes_original_currency_without_modifying_inputs():
    control = row(id='control', returned_model='fixture-v1', packet={'evidence': []})
    treated = deepcopy(control)
    treated.update(id='treated', condition='with_patents', track='patent_ablation', cost_amount=.05)
    treated['packet']['evidence'].append({'kind': 'patent', 'id': 'patent'})
    snapshot = {'runs': [control, treated]}
    original = deepcopy(snapshot)
    actual = patent_pairs(snapshot)[0]
    assert actual['cost_comparison']['delta_amount'] == pytest.approx(.03)
    assert actual['cost_comparison']['currency'] == 'CNY'
    assert actual['probability_change'] is None and actual['delta_brier_with_minus_without'] is None
    assert snapshot == original


def test_empty_summary_does_not_claim_free_execution():
    assert cost_summary([])['cost_complete'] is False
    assert cost_summary([])['cost_usd'] is None


@pytest.mark.parametrize('changes', [
    {'known_cost_amount': .99}, {'known_cost_usd': float('nan')},
    {'known_cost_amount': -.01}, {'known_cost_usd': .02},
    {'cost_breakdown': [{'currency': 'USD', 'known_amount': .03, 'complete': False}]},
    {'cost_breakdown': [{'currency': 'CNY', 'known_amount': .99, 'complete': False}]},
    {'cost_breakdown': [{'currency': 'CNY', 'known_amount': .01, 'complete': True}]},
    {'known_cost_amount': .01, 'cost_breakdown': [{'currency': 'CNY', 'known_amount': .02, 'complete': False}]},
])
def test_contradictory_full_and_partial_records_cannot_be_totalled_or_paired(changes):
    conflicting = row(**changes)
    assert cost_summary([conflicting])['cost_complete'] is False
    assert cost_summary([conflicting])['known_cost_totals_by_currency'] == {}
    assert paired_cost(row(), conflicting)['comparable'] is False


def test_actual_agent_mixed_currency_breakdown_is_preserved_without_alias_duplication():
    from emeris_live.arena_agents import _account, USAGE_FIELDS
    result = {'agent_calls_dispatched': 2}
    stages = [{'role': role, 'model_id': 'fixture', 'attempts': [],
        'usage': {key: 0 for key in USAGE_FIELDS}, 'cost_currency': currency,
        'cost_amount': amount, 'cost_usd': amount if currency == 'USD' else None}
        for role, currency, amount in [('evidence_review', 'CNY', .02), ('forecast', 'USD', .03)]]
    _account(result, stages)
    before = deepcopy(result)
    actual = cost_summary([result])
    assert actual['known_cost_totals_by_currency'] == {'CNY': .02, 'USD': .03}
    assert actual['cost_complete'] is False and actual['cost_amount'] is None
    assert actual['cost_partial_runs'] == 1 and actual['cost_recorded_runs'] == 0
    assert result == before


@pytest.mark.parametrize('breakdown', [
    [{'currency': 'CNY', 'known_amount': .02, 'complete': False}] * 2,
    [{'currency': 'CNY', 'known_amount': True, 'complete': False}],
    [{'currency': 'CNY', 'known_amount': .02, 'complete': 'yes'}],
    [{'currency': 'CNY', 'known_amount': .02, 'complete': False, 'raw_error': 'untrusted'}],
])
def test_invalid_breakdown_does_not_supply_a_partial_total(breakdown):
    actual = cost_summary([row(cost_amount=None, cost_breakdown=breakdown)])
    assert actual['known_cost_totals_by_currency'] == {} and actual['cost_complete'] is False
