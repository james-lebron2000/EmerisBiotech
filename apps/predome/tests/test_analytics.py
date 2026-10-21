from copy import deepcopy
import math

import pytest

from emeris_live.analytics import build_analytics, wilson
from emeris_live.dashboard import build_dashboard


@pytest.fixture(scope='module')
def dashboard():
    return build_dashboard()


def test_prospective_empty_does_not_borrow_replay_metrics(dashboard):
    result=build_analytics(dashboard=dashboard)
    assert result['summary']['answered']==0
    assert result['summary']['brier'] is None
    assert result['summary']['accuracy'] is None
    assert result['summary']['coverage'] is None
    assert result['cumulative']==[]
    assert result['baselines']==[]
    assert not any(b['n'] for b in result['calibration_bins'])
    assert result['summary']['performance_status']=='insufficient_verified_predictions'


def test_replay_preserves_frozen_probabilities_and_refusals(dashboard):
    result=build_analytics(scope='retrospective',dashboard=dashboard)
    assert result['summary']['total_records']==8
    assert result['summary']['answered']==6
    assert result['summary']['abstained']==2
    assert result['summary']['coverage']==.75
    assert result['summary']['brier']==pytest.approx(.11958333333333333)
    assert result['summary']['accuracy']==pytest.approx(5/6)
    assert result['summary']['asset_groups']==4
    assert all(r['eligible_for_prospective'] is False for r in result['records'])
    assert result['provenance']['historical_memory_contamination_excluded'] is False
    assert result['summary']['clinical_prediction_validated'] is False
    originals={r['nct_id']:r for r in dashboard['historical']}
    for row in result['records']:
        assert row['probability']==originals[row['trial_id']]['forecasts']['deepseek-v4-pro']['probability']


def test_baselines_use_identical_case_ids_and_archived_predictions(dashboard):
    result=build_analytics(scope='retrospective',dashboard=dashboard)
    cases=[b['case_ids'] for b in result['baselines']]
    assert len(cases)==5
    assert all(c==cases[0] for c in cases)
    assert all(b['n']==6 for b in result['baselines'])
    constant=next(b for b in result['baselines'] if b['name']=='constant_0_5')
    assert constant['brier']==.25
    pooled=next(b for b in result['baselines'] if b['name']=='beta_pooled')
    assert pooled['brier']==pytest.approx(.33125577100646353)
    clone=deepcopy(dashboard)
    clone['historical'][0]['statistical_models']['beta_area']=None
    reduced=build_analytics(scope='retrospective',dashboard=clone)
    assert all(b['n']==5 for b in reduced['baselines'])


def test_calibration_boundaries_counts_and_wilson(dashboard):
    clone=deepcopy(dashboard)
    clone['historical']=clone['historical'][:6]
    for row,p in zip(clone['historical'],[0,.2,.4,.6,.8,1]):
        row['forecasts']['deepseek-v4-pro']['probability']=p
    result=build_analytics(scope='retrospective',dashboard=clone)
    assert [b['n'] for b in result['calibration_bins']]==[1,1,1,1,2]
    assert sum(b['success']+b['failure'] for b in result['distribution'])==6
    assert wilson(0,0) is None
    assert wilson(0,10)[0]==0
    assert wilson(10,10)[1]==pytest.approx(1)
    assert wilson(5,10)==pytest.approx([.236593090512564,.763406909487436])


def test_small_subgroups_are_suppressed_and_missing_not_guessed(dashboard):
    result=build_analytics(scope='retrospective',dashboard=dashboard)
    for groups in result['subgroups'].values():
        assert all(g['metrics'] is None for g in groups)
    assert result['subgroups']['modality'][0]['value']=='unknown'
    assert result['subgroups']['modality'][0]['total']==8
    assert result['subgroups']['sponsor_category'][0]['value']=='unknown'


def test_same_day_records_are_not_ordered_into_false_trajectories(dashboard):
    first=build_analytics(scope='retrospective',rolling=2,dashboard=dashboard)
    clone=deepcopy(dashboard);clone['historical'].reverse()
    second=build_analytics(scope='retrospective',rolling=2,dashboard=clone)
    assert first['cumulative']==second['cumulative']
    assert len(first['cumulative'])<first['summary']['answered']
    assert first['cumulative'][-1]['cumulative']['n']==6
    assert all(p['rolling']['n']>=min(2,p['cumulative']['n']) for p in first['cumulative'])


def record(**overrides):
    result={'prediction_id':'p1','trial_id':'NCT00000001','trial_name':'Fixture only','asset':'test-asset',
            'endpoint':'prespecified primary outcome','model':'deepseek-v4-pro','probability':.7,'label':1,
            'generated_at':'2026-01-01T12:00:00Z','locked_at':'2026-01-02T12:00:00Z',
            'readout_date':'2026-02-01T12:00:00Z','verified_at':'2026-02-02T12:00:00Z',
            'scope':'prospective','eligible_for_prospective':True,'source_url':'https://example.org/result',
            'phase':3,'therapeutic_area':'oncology'}
    result.update(overrides)
    if 'target' not in result:
        result['target']={'endpoint':result['endpoint'],'population':'ITT fixture population','comparison':'drug versus placebo','success_definition':'prespecified primary endpoint alpha 0.05'}
    return result


def test_verified_ledger_records_remain_a_separate_scope(dashboard):
    result=build_analytics(prospective_records=[record()],dashboard=dashboard)
    assert result['summary']['answered']==1
    assert result['summary']['brier']==pytest.approx(.09)
    assert result['records'][0]['features']['prediction_horizon']=='0–30 days'
    assert result['baselines']==[]
    assert not any(r['scope']=='retrospective' for r in result['records'])


@pytest.mark.parametrize('change',[
    {'scope':'retrospective'}, {'eligible_for_prospective':False},
    {'locked_at':'2026-03-01T12:00:00Z'}, {'generated_at':'2026-02-03T12:00:00Z'},
    {'verified_at':'2026-01-01T12:00:00Z'}, {'readout_date':None}, {'label':None},
    {'label':True}, {'probability':float('nan')}, {'probability':None}, {'endpoint':''},
])
def test_invalid_forward_records_do_not_score(change,dashboard):
    result=build_analytics(prospective_records=[record(**change)],dashboard=dashboard)
    assert result['summary']['answered']==0
    assert len(result['excluded'])==1


def test_later_revision_cannot_inflate_performance(dashboard):
    raw=[record(),record(prediction_id='p2',probability=1,locked_at='2026-01-10T12:00:00Z')]
    result=build_analytics(prospective_records=raw,dashboard=dashboard)
    assert result['summary']['answered']==1
    assert result['records'][0]['id']=='p1'
    assert result['summary']['brier']==pytest.approx(.09)
    assert 'later_revision_same_trial_full_target_model' in result['excluded'][0]['reasons']


def test_100_percent_probability_never_drops_from_bins(dashboard):
    result=build_analytics(prospective_records=[record(probability=1,label=0)],dashboard=dashboard)
    assert result['calibration_bins'][-1]['n']==1
    assert result['distribution'][-1]['failure']==1
    assert result['summary']['brier']==1
    assert math.isfinite(result['summary']['log_loss'])


@pytest.mark.parametrize('kwargs',[{'scope':'mixed'},{'model':'invented'},{'rolling':1},{'rolling':101},{'rolling':True}])
def test_invalid_controls_rejected(kwargs,dashboard):
    with pytest.raises(ValueError):build_analytics(dashboard=dashboard,**kwargs)


def test_model_versions_never_mix_in_prospective_metrics(dashboard):
    rows=[record(model_version='release-a'), record(prediction_id='p2',trial_id='NCT00000002',model_version='release-b',probability=.1)]
    result=build_analytics(model='deepseek-v4-pro :: release-a',prospective_records=rows,dashboard=dashboard)
    assert result['summary']['answered']==1
    assert result['summary']['brier']==pytest.approx(.09)
    assert 'deepseek-v4-pro :: release-b' in result['models']
    assert result['records'][0]['model_version']=='release-a'
