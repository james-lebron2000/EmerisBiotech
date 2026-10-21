"""Isolated real encrypted ledger -> independent review -> analytics regressions."""
import json
import pytest
from emeris_live import platform_store
from emeris_live.analytics import build_analytics
from test_platform_store import ledger, create, outcome, CONFIRM, TARGET

MODEL='deepseek-v4-pro'


def freeze(ledger, monkeypatch, status='predicted', *, nct='NCT01234567', publish=True,
           when='2025-01-10T12:00:00+00:00', version=MODEL, target=None, share_metadata=False):
    monkeypatch.setattr(platform_store,'now',lambda:when)
    run=create(ledger,input_snapshot={'nct_id':nct,'drug':'private-asset'},
        target=target or TARGET,
        outputs={'forecast_kind':'uncalibrated_llm_research','forecasts':{
            MODEL:{'status':status,'probability':.7 if status=='predicted' else None,'model_version':version},
            'other-model':{'status':'predicted','probability':.6}}})
    return ledger.lock('owner',run['id'],publish=publish,publish_evaluation_metadata=share_metadata,
                       confirm_evaluation_metadata=share_metadata,confirm_public=share_metadata)


def approve(ledger,monkeypatch,snapshot):
    monkeypatch.setattr(platform_store,'now',lambda:'2025-03-01T12:00:00+00:00')
    record=snapshot['record']
    submitted=ledger.submit_outcome('owner',record['id'],outcome(trial_id=record['trial_id'],
        **{k:record['target'][k] for k in ('endpoint','population','comparison')}))
    ledger.verify_outcome('independent',submitted['id'],'approved','Independent target and earliest source verification',CONFIRM)


def analytics(ledger,model=MODEL):
    return build_analytics(model=model,prospective_records=ledger.evaluation_rows(),dashboard={'provenance':{}})


def test_real_ledger_preserves_all_requested_statuses_without_scoring_nonanswers(ledger,monkeypatch):
    locks=[freeze(ledger,monkeypatch,s,nct=f'NCT{i:08d}') for i,s in enumerate(('predicted','abstain','failed','unavailable'),1)]
    assert ledger.evaluation_rows()==[] # pending reviews do not count
    before=[ledger.get_snapshot(x['record']['id'],owner='owner') for x in locks]
    for lock in locks:approve(ledger,monkeypatch,lock)
    result=analytics(ledger);s=result['summary']
    assert (s['total_records'],s['answered'],s['abstained'],s['failed'],s['unavailable'])==(4,1,1,1,1)
    assert s['coverage']==.25 and s['unanswered']==3
    assert s['brier']==pytest.approx(.09) and s['n']==1
    assert s['asset_groups']==0 # unconsented private asset must not become a public group
    assert result['subgroups']['phase'][0]['total']==4
    assert sum(x['n'] for x in result['calibration_bins'])==1
    assert sum(x['success']+x['failure'] for x in result['distribution'])==1
    assert result['cumulative'][-1]['cumulative']['n']==1
    assert result['baseline_coverage']['total_cases']==1
    assert all(b['n']==1 for b in result['baselines'])
    assessments=[a for o in ledger.public_outcome_records() for a in o['assessments'] if a['model']==MODEL]
    assert {a['prediction_status'] for a in assessments}=={'predicted','abstain','failed','unavailable'}
    assert all(a['coverage_eligible'] for a in assessments)
    assert sum(a['score_eligible'] for a in assessments)==1
    assert 'private-asset' not in json.dumps(ledger.public_outcome_records())
    assert before==[ledger.get_snapshot(x['record']['id'],owner='owner') for x in locks]


@pytest.mark.parametrize('first_status',['abstain','failed','unavailable'])
def test_first_public_nonanswer_blocks_later_answer_even_new_version(ledger,monkeypatch,first_status):
    first=freeze(ledger,monkeypatch,first_status)
    later=freeze(ledger,monkeypatch,when='2025-01-11T12:00:00+00:00',version='release-2')
    # First is pending: scoring only the favorable later result must still fail.
    approve(ledger,monkeypatch,later)
    assert analytics(ledger,MODEL+' :: release-2')['summary']['total_records']==0
    approve(ledger,monkeypatch,first)
    result=analytics(ledger)
    assert result['summary']['total_records']==1 and result['summary']['coverage']==0
    assert result['summary']['brier'] is None and result['calibration_bins'][0]['n']==0
    assert result['baselines']==[] and result['cumulative']==[]
    rows=[r for r in ledger.evaluation_rows() if r['model']==MODEL]
    assert all(r['primary_prediction_id']==first['record']['prediction_id'] for r in rows)
    assert next(r for r in rows if r['model_version']=='release-2')['score_eligible'] is False
    assert 'across_versions' in result['provenance']['version_policy']


def test_private_and_retrospective_requests_do_not_enter_coverage(ledger,monkeypatch):
    private=freeze(ledger,monkeypatch,'abstain',publish=False)
    public=freeze(ledger,monkeypatch,when='2025-01-11T12:00:00+00:00')
    retro=freeze(ledger,monkeypatch,'failed',nct='NCT00000009',when='2025-02-11T12:00:00+00:00')
    for lock in (private,public,retro):approve(ledger,monkeypatch,lock)
    result=analytics(ledger)
    assert result['summary']['total_records']==1 and result['summary']['coverage']==1
    assert result['records'][0]['prediction_id']==public['record']['prediction_id']
    assert len(result['excluded'])==1 # retrospective failed request excluded, not scored or zero-filled
    assert len(ledger.public_outcome_records())==2


def test_full_target_identity_keeps_distinct_populations_in_denominator(ledger,monkeypatch):
    first=freeze(ledger,monkeypatch,'abstain')
    second=freeze(ledger,monkeypatch,target=TARGET|{'population':'Biomarker selected population'})
    for lock in (first,second):approve(ledger,monkeypatch,lock)
    result=analytics(ledger)
    assert result['summary']['total_records']==2 and result['summary']['coverage']==.5
    assert len({r['case_id'] for r in result['records']})==2


def test_multiple_trials_of_one_disclosed_asset_remain_one_independent_asset_group(ledger,monkeypatch):
    first=freeze(ledger,monkeypatch,share_metadata=True)
    second=freeze(ledger,monkeypatch,nct='NCT00000002',share_metadata=True)
    refused=freeze(ledger,monkeypatch,'abstain',nct='NCT00000003',share_metadata=True)
    for lock in (first,second,refused):approve(ledger,monkeypatch,lock)
    result=analytics(ledger)
    assert result['summary']['answered']==2 and result['summary']['total_records']==3
    assert result['summary']['asset_groups']==result['summary']['coverage_asset_groups']==1
    assert all(group['metrics'] is None for groups in result['subgroups'].values() for group in groups)
