"""Numerical and evidence-gate validation, NOT clinical performance validation."""
import hashlib
import json
import math
from statistics import NormalDist

import pytest
from pydantic import ValidationError

from emeris_live.triallab import TrialRequest, execute, favorable_power


def test_direction_and_known_schoenfeld_calculations():
    # 400 events, 1:1 allocation, two-sided .05, favorable direction only.
    assert favorable_power(0, 400, .5, .05) == pytest.approx(.025)
    assert favorable_power(math.log(.75), 400, .5, .05) == pytest.approx(.8203911423)
    assert favorable_power(math.log(.85), 400, .5, .05) == pytest.approx(.3688975274)
    assert favorable_power(math.log(1.25), 400, .5, .05) < .025
    assert favorable_power(math.log(.75), 400, .2, .05) < favorable_power(math.log(.75), 400, .5, .05)


def test_assurance_matches_independent_numerical_integration_and_zero_sd_limit():
    req = TrialRequest(prior_log_hr_mean=math.log(.8), prior_log_hr_sd=.2)
    result = execute(req)
    # Independent Simpson integration over standard normal effect z, +/-9 SD.
    n = 12000
    step = 18 / n
    integral = 0
    for i in range(n + 1):
        z = -9 + i * step
        density = math.exp(-z*z/2) / math.sqrt(2*math.pi)
        power = favorable_power(req.prior_log_hr_mean + req.prior_log_hr_sd*z,
                                req.event_count, req.allocation, req.alpha)
        integral += (1 if i in (0, n) else (4 if i % 2 else 2)) * density * power
    integral *= step / 3
    assert result['summary']['assurance'] == pytest.approx(integral, abs=1e-10)
    point = execute(TrialRequest(prior_log_hr_sd=0))
    assert point['summary']['assurance'] == pytest.approx(point['summary']['conditional_power_at_median'])
    assert point['effect_distribution']['hr_interval_95'] == pytest.approx([.75, .75])
    assert point['future_readout_distribution']['hr_interval_95'][0] < .75


def historical(**changes):
    value = dict(trial_id='HIST-001', endpoint='PFS', log_hr=math.log(.7), log_hr_se=.1,
                 source_url='https://example.org/study', source_note='Test fixture only, not clinical evidence.',
                 published_date='2025-01-01', matching_rationale='Synthetic matching declaration for numerical validation only.',
                 reviewed=True)
    value.update(changes)
    return value


def test_normal_normal_update_matches_conjugate_posterior_and_retains_target_uncertainty():
    req = TrialRequest(mode='evidence_conditioned', target_trial_id='TARGET-001', cutoff_date='2026-01-01',
                       prior_log_hr_mean=0, prior_log_hr_sd=.2, between_study_sd=.1, transport_sd=.05,
                       historical_effects=[historical()])
    result = execute(req)
    # Prior precision 25; historical observation precision 1/(.1^2+.1^2)=50.
    expected_mean = math.log(.7) * 50 / 75
    assert result['fit']['common_mean_posterior_log_hr'] == pytest.approx(expected_mean)
    assert result['fit']['common_mean_posterior_sd'] == pytest.approx(math.sqrt(1/75))
    assert result['effect_distribution']['log_hr_sd'] == pytest.approx(math.sqrt(1/75+.1**2+.05**2))
    assert not result['clinical_prediction_validated']
    assert result['candidate_forecast'] is None


def test_evidence_leakage_unsupported_endpoints_and_invalid_values_rejected():
    base = dict(mode='evidence_conditioned', target_trial_id='TARGET-001', cutoff_date='2026-01-01',
                historical_effects=[historical()])
    invalid = [
        {'prior_log_hr_sd': float('nan')}, {'prior_log_hr_mean': float('inf')},
        {'event_count': 0}, {'event_count': 400.5}, {'alpha': 0}, {'allocation': 1},
        {'endpoint': 'SRI-4'}, {'analysis': 'co_primary_endpoints'}, {'candidate_id': 'fake-drug'},
        {**base, 'historical_effects': []}, {**base, 'prior_log_hr_sd': 0},
        {**base, 'cutoff_date': '9999-12-31'},
        {**base, 'historical_effects': [historical(trial_id='target-001')]},
        {**base, 'historical_effects': [historical(published_date='2026-01-02')]},
        {**base, 'historical_effects': [historical(endpoint='OS')]},
        {**base, 'historical_effects': [historical(), historical(trial_id='hist-001')]},
        {**base, 'historical_effects': [historical(log_hr_se=0)]},
        {**base, 'historical_effects': [historical(reviewed=False)]},
    ]
    for kwargs in invalid:
        with pytest.raises(ValidationError):
            TrialRequest(**kwargs)


def test_hash_reproducibility_and_extreme_bounded_requests_remain_finite():
    result = execute(TrialRequest())
    assert result == execute(TrialRequest())
    digest = result.pop('result_sha256')
    assert hashlib.sha256(json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).hexdigest() == digest
    for mean in (-3, 3):
        out = execute(TrialRequest(prior_log_hr_mean=mean, prior_log_hr_sd=1.5,
                                  transport_sd=1.5, event_count=100000, allocation=.05, alpha=.0001))
        json.dumps(out, allow_nan=False)
        assert 0 <= out['summary']['assurance'] <= 1
        assert out['future_readout_distribution']['log_hr_sd'] > out['effect_distribution']['log_hr_sd']
