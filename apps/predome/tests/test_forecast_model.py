"""Prospective workflow and numerical tests; not clinical performance evidence."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
from statistics import NormalDist

import pytest
from pydantic import ValidationError

from emeris_live.forecast_model import (
    AdjudicationObservation, adjudicate, binary_score, build_forecast, canonical_sha256,
)


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def inputs():
    return (json.loads((ROOT/'trial_data/candidates.json').read_text()),
            json.loads((ROOT/'trial_data/forecast_model_spec_v1.json').read_text()))


@pytest.fixture
def forecast(inputs):
    return build_forecast(*inputs)


def test_candidate_numbers_match_direct_conjugate_and_mixture_calculation(inputs, forecast):
    spec = inputs[1]
    norm = NormalDist()
    for arm in forecast['comparisons']:
        data = arm['historical_evidence']
        se = math.log(data['ci95'][1]/data['ci95'][0])/(2*norm.inv_cdf(.975))
        variance = 1/(1/.35**2 + 1/se**2)
        mean = math.log(data['estimate'])*variance/se**2
        expected_direction, expected_stat = 0, 0
        for d in [200,400,600]:
            sigma = math.sqrt(variance+.2**2+4/d)
            expected_direction += norm.cdf(-mean/sigma)/3
            expected_stat += norm.cdf((-norm.inv_cdf(.975)*math.sqrt(4/d)-mean)/sigma)/3
        assert arm['direction_probability'] == pytest.approx(expected_direction)
        assert arm['statistical_success_probability'] == pytest.approx(expected_stat)
        assert arm['direction_probability'] > arm['statistical_success_probability']
        assert arm['direction_call'] == 'HR_below_1'
        for quantile, bound in zip([.025,.975], arm['hr_estimate_interval_95']):
            actual = sum(norm.cdf((math.log(bound)-mean)/math.sqrt(variance+.2**2+4/d))/3 for d in [200,400,600])
            assert actual == pytest.approx(quantile, abs=1e-12)
        assert len(arm['sensitivity']) == 18
    for field in ['direction', 'statistical_success']:
        ps = [a[f'{field}_probability'] for a in forecast['comparisons']]
        assert forecast['trial_level'][f'{field}_probability_bounds'] == pytest.approx([max(ps), min(1,sum(ps))])
        assert forecast['trial_level'][f'{field}_probability'] is None
    assert not forecast['clinical_prediction_validated']


def test_frozen_recipe_hash_and_evidence_rejections(inputs, forecast):
    assert forecast == build_forecast(*inputs)
    assert forecast['payload_sha256'] == canonical_sha256({k:v for k,v in forecast.items() if k!='payload_sha256'})
    catalog,spec = inputs
    for mutation in ['changed_prior','known_result','future_source','invalid_ci','missing_arm','duplicate_arm']:
        c,s=deepcopy(catalog),deepcopy(spec)
        target=next(x for x in c['candidates'] if x['id']=='pacific-9')
        if mutation=='changed_prior':s['prior_log_hr_sd']=.5
        if mutation=='known_result':target['status_as_of']['registry_has_results']=True
        if mutation=='future_source':target['prior_evidence'][0]['published_at']='2099-01-01'
        if mutation=='invalid_ci':target['prior_evidence'][0]['ci95']=[.9,.3]
        if mutation=='missing_arm':target['prior_evidence'].pop()
        if mutation=='duplicate_arm':target['prior_evidence'].append(deepcopy(target['prior_evidence'][0]))
        with pytest.raises(ValueError):build_forecast(c,s)
    assert all(x['probability'] is None for x in forecast['abstentions'])


def observation(**updates):
    value = dict(trial_id='NCT05221840',arm_id='oleclumab',endpoint='BICR_PFS',population='ITT',
                 analysis='first_official_prespecified_primary_pfs_analysis',
                 is_first_eligible_official_analysis=True,independently_reviewed=True,
                 source_authority='sponsor',source_url='https://example.org/fake-test-result',
                 source_locator='Synthetic test fixture only, not an actual result',
                 published_at_utc='2026-10-10T00:01:00Z')
    value.update(updates)
    return value


def test_direction_and_official_success_are_scored_separately_missing_is_pending(forecast):
    frozen='2026-10-10T00:00:00Z'
    empty=adjudicate(forecast,observation(),frozen_at_utc=frozen)
    assert empty['status']=='pending'
    assert empty['target_count_scored']==0
    assert empty['direction']['score'] is None and empty['statistical_success']['score'] is None
    # Qualitative official failure contains no numerical HR: direction is unknown.
    stat=adjudicate(forecast,observation(statistical_result='did_not_meet_prespecified_primary_criterion',
                                      statistical_source_quote='Synthetic statement: this primary comparison did not meet its prespecified criterion.'),frozen_at_utc=frozen)
    assert stat['status']=='partially_adjudicated'
    assert stat['statistical_success']['outcome'] is False
    assert stat['direction']['outcome'] is None
    # Numerically favorable HR can coexist with failure to meet statistical criterion.
    both=adjudicate(forecast,observation(observed_hr=.95,hr_source_quote='Synthetic HR: 0.95',
                                      statistical_result='did_not_meet_prespecified_primary_criterion',
                                      statistical_source_quote='Synthetic primary criterion not met.'),frozen_at_utc=frozen)
    assert both['direction']['outcome'] is True and both['statistical_success']['outcome'] is False
    assert both['target_count_scored']==2 and both['trial_ids_for_dedup']==['NCT05221840']
    p=forecast['comparisons'][0]['direction_probability']
    assert both['direction']['score']['brier']==pytest.approx((1-p)**2)
    assert both['direction']['score']['log_loss']==pytest.approx(-math.log(p))
    boundary=adjudicate(forecast,observation(observed_hr=1,hr_source_quote='Synthetic rounded HR: 1.00'),frozen_at_utc=frozen)
    assert boundary['direction']['outcome'] is False and boundary['rounding_boundary']


def test_wrong_observation_identity_timing_and_tamper_do_not_create_scores(forecast):
    frozen='2026-10-10T00:00:00Z'
    mutations=[{'trial_id':'NCT00000000'},{'arm_id':'wrong'},{'endpoint':'OS'},
               {'population':'PDL1_positive_only'},{'analysis':'exploratory'},
               {'source_authority':'unverified'},{'independently_reviewed':False},
               {'is_first_eligible_official_analysis':False},
               {'published_at_utc':'2026-10-09T23:59:59Z'},
               {'published_at_utc':datetime.now(timezone.utc)+timedelta(days=1)}]
    for update in mutations:
        result=adjudicate(forecast,observation(observed_hr=.8,hr_source_quote='Synthetic HR: .8',**update),frozen_at_utc=frozen)
        assert result['status']=='ineligible' and result['target_count_scored']==0
    altered=deepcopy(forecast);altered['comparisons'][0]['direction_probability']=.99
    with pytest.raises(ValueError,match='hash mismatch'):
        adjudicate(altered,observation(),frozen_at_utc=frozen)
    with pytest.raises(ValueError,match='predate'):
        adjudicate(forecast,observation(),frozen_at_utc='2020-01-01T00:00:00Z')
    with pytest.raises(ValidationError):
        AdjudicationObservation(**observation(observed_hr=.9))
    with pytest.raises(ValidationError):
        AdjudicationObservation(**observation(statistical_result='met_prespecified_primary_criterion'))
    with pytest.raises(ValueError):binary_score(.7,None)
    conflicting=adjudicate(forecast,observation(observed_hr=1.1,hr_source_quote='Synthetic HR: 1.1',
                         statistical_result='met_prespecified_primary_criterion',
                         statistical_source_quote='Conflicting synthetic assertion of favorable success.'),frozen_at_utc=frozen)
    assert conflicting['status']=='ineligible'
    assert 'conflicting_hr_and_favorable_statistical_statement' in conflicting['reasons']
    assert binary_score(0,False)['log_loss']==0
    assert binary_score(0,True)['log_loss_infinite'] and binary_score(0,True)['log_loss'] is None


def test_source_effect_change_recomputes_hash_not_hardcoded_probabilities(inputs, forecast):
    catalog,spec=deepcopy(inputs)
    target=next(x for x in catalog['candidates'] if x['id']=='pacific-9')
    target['prior_evidence'][0]['estimate']=1.05
    target['prior_evidence'][0]['ci95']=[.8,1.4]
    changed=build_forecast(catalog,spec)
    assert changed['comparisons'][0]['direction_probability']<.5
    assert changed['comparisons'][0]['direction_call']=='HR_not_below_1'
    assert changed['payload_sha256']!=forecast['payload_sha256']
    assert changed['comparisons'][1]['direction_probability']==forecast['comparisons'][1]['direction_probability']
