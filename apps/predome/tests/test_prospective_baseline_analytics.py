"""Prospective baselines compare only authenticated, pre-readout full-target pairs."""
from copy import deepcopy
import pytest
from emeris_live.analytics import build_analytics, _digest, BASELINE_POLICY
from emeris_live.prospective_baselines import POLICY_SHA256

TARGET={'endpoint':'PFS','population':'ITT','comparison':'drug versus placebo','success_definition':'prespecified two-sided alpha 0.05'}
DASHBOARD={'historical':[],'provenance':{}}


def record(id='p1',target=None,model='deepseek-v4-pro',label=1,baseline=True):
    target=deepcopy(target or TARGET)
    raw={'prediction_id':id,'trial_id':'NCT01234567','trial_name':'Fixture trial','asset':'asset-A',
         'snapshot_id':'s-'+id,'snapshot_sha256':_digest(['snapshot',id,target]),'target':target,'endpoint':target['endpoint'],
         'model':model,'model_version':model,'probability':.7,'label':label,'scope':'prospective','eligible_for_prospective':True,
         'generated_at':'2026-01-02T00:00:00Z','locked_at':'2026-01-03T00:00:00Z','evidence_cutoff':'2026-01-01T00:00:00Z',
         'readout_date':'2026-02-10T00:00:00Z','verified_at':'2026-02-11T00:00:00Z','verification_status':'approved'}
    if baseline:
        raw['baseline_probabilities']={'fixed_0_5':.5}
        raw['baseline_audits']=[{'baseline_key':'fixed_0_5','baseline_id':'b-'+id,'probability':.5,
            'policy_version':BASELINE_POLICY,'policy_sha256':POLICY_SHA256,'record_sha256':'b'*64,
            'snapshot_id':raw['snapshot_id'],'snapshot_sha256':raw['snapshot_sha256'],'prediction_id':id,
            'trial_id':raw['trial_id'],'target_sha256':_digest(target),'recorded_at':'2026-01-04T00:00:00Z',
            'source_forecast_locked_at':raw['locked_at'],'effective_evidence_cutoff':raw['evidence_cutoff'],'source_forecast_cutoff':raw['evidence_cutoff'],
            'eligible':True,'exclusion_reasons':[],'integrity_verified':True,'inputs_used':[]}]
    return raw


def result(rows,model='deepseek-v4-pro'):
    return build_analytics(prospective_records=rows,model=model,dashboard=DASHBOARD)


def test_authenticated_same_target_pairs_and_provenance():
    r=result([record()])
    assert r['baseline_common_n']==1 and len(r['baselines'])==2
    a,b=r['baselines']
    assert a['case_ids']==b['case_ids']==r['baseline_coverage']['case_ids']
    assert a['brier']==pytest.approx(.09) and b['brier']==.25
    assert b['name']=='fixed_0_5' and b['kind']=='prospective_fixed_baseline'
    assert r['baseline_coverage']['status']=='complete'
    provenance=r['baseline_provenance']
    assert provenance['feature_free'] and not provenance['uses_outcome_fitting']
    assert not provenance['estimated_historical_success_rate']
    assert provenance['records'][0]['recorded_at']=='2026-01-04T00:00:00Z'
    assert provenance['records'][0]['source_forecast_locked_at']=='2026-01-03T00:00:00Z'


def test_numeric_mapping_alone_is_not_prospective_baseline():
    raw=record(baseline=False)
    raw['baseline_probabilities']={'fixed_0_5':.5,'constant_0_5':.5,'beta_pooled':.8,'design_logistic':.9}
    r=result([raw])
    assert r['summary']['answered']==1 and r['baselines']==[]
    assert r['baseline_coverage']['missing_cases']==1
    assert r['baseline_coverage']['reason_counts']=={'baseline_not_registered':1}


@pytest.mark.parametrize('field,value,reason',[
    ('recorded_at','2026-02-10T00:00:00Z','baseline_not_before_readout'),
    ('recorded_at','2026-02-11T00:00:00Z','baseline_not_before_readout'),
    ('recorded_at',None,'baseline_missing_audit_timestamps'),
    ('target_sha256','c'*64,'baseline_target_mismatch'),
    ('snapshot_sha256','c'*64,'baseline_snapshot_hash_mismatch'),
    ('snapshot_id','other','baseline_snapshot_id_mismatch'),
    ('trial_id','NCT11111111','baseline_trial_id_mismatch'),
    ('prediction_id','other','baseline_prediction_id_mismatch'),
    ('integrity_verified',False,'baseline_integrity_unverified'),
    ('eligible',False,'baseline_ledger_ineligible'),
    ('probability',.6,'baseline_not_fixed_half'),
    ('policy_version','fitted-2026','baseline_policy_mismatch'),
    ('policy_sha256',None,'baseline_missing_policy_sha256'),
    ('policy_sha256','c'*64,'baseline_policy_mismatch'),
    ('source_forecast_locked_at','2026-01-04T00:00:00Z','baseline_source_lock_mismatch'),
    ('effective_evidence_cutoff','2026-01-05T00:00:00Z','baseline_evidence_cutoff_mismatch'),
    ('source_forecast_cutoff','2026-01-05T00:00:00Z','baseline_evidence_cutoff_mismatch'),
    ('inputs_used',['readout_label'],'baseline_feature_inputs_not_empty'),
])
def test_unverified_late_or_mismatched_baseline_never_removes_agent_score(field,value,reason):
    raw=record();raw['baseline_audits'][0][field]=value
    r=result([raw])
    assert r['summary']['answered']==1 and r['baseline_common_n']==0 and r['baselines']==[]
    assert reason in r['baseline_coverage']['exclusions'][0]['reasons']


def test_different_full_targets_same_endpoint_are_distinct_and_revisions_dedup():
    first=record();second=record('p2',TARGET|{'population':'biomarker-positive'})
    third=record('p3',TARGET|{'comparison':'drug versus active control'})
    fourth=record('p4',TARGET|{'success_definition':'prespecified alpha 0.01'})
    revision=record('p5');revision['locked_at']='2026-01-05T00:00:00Z'
    r=result([revision,fourth,third,second,first])
    assert r['summary']['answered']==4 and r['summary']['distinct_trials']==1
    assert len(set(r['baseline_coverage']['case_ids']))==4
    assert {x['id'] for x in r['records']}=={'p1','p2','p3','p4'}
    assert r['excluded'][0]['reasons']==['later_revision_same_trial_full_target_model']


def test_case_identity_separates_models_even_same_snapshot_prediction_and_target():
    rows=[record(),record(model='deepseek-flash')]
    a=result(rows);b=result(rows,'deepseek-flash')
    assert a['baseline_common_n']==b['baseline_common_n']==1
    assert a['baseline_coverage']['case_ids']!=b['baseline_coverage']['case_ids']


def test_complete_intersection_coverage_and_no_posthoc_fitting():
    a=record();b=record('p2',TARGET|{'population':'B'},baseline=False)
    c=record('p3',TARGET|{'population':'C'});c['baseline_audits'][0]['recorded_at']='2026-02-12T00:00:00Z'
    r=result([a,b,c]);coverage=r['baseline_coverage']
    assert r['summary']['answered']==3
    assert coverage['total_cases']==3 and coverage['eligible_cases']==1
    assert coverage['missing_cases']==1 and coverage['excluded_cases']==1 and coverage['coverage']==pytest.approx(1/3)
    assert all(x['n']==1 for x in r['baselines'])
    assert r['baselines'][0]['case_ids']==r['baselines'][1]['case_ids']
    flipped=deepcopy([a,b,c])
    for x in flipped:x['label']=0
    after=result(flipped)
    assert after['baseline_provenance']==r['baseline_provenance']
    assert after['baseline_coverage']==r['baseline_coverage']
    assert after['baselines'][1]['brier']==.25  # Fixed value regardless observed outcomes.


@pytest.mark.parametrize('snapshot_present',[True,False])
@pytest.mark.parametrize('target_change',[None,{}, {'endpoint':'PFS'}, TARGET|{'population':''}])
def test_any_missing_full_target_is_excluded(snapshot_present,target_change):
    raw=record()
    if target_change is None:raw.pop('target')
    else:raw['target']=target_change
    if not snapshot_present:
        for key in ('snapshot_id','snapshot_sha256','verification_status'):raw.pop(key)
    r=result([raw])
    assert r['summary']['answered']==0 and r['baselines']==[]
    assert 'missing_full_target_identity' in r['excluded'][0]['reasons']


def test_empty_forward_scope_does_not_imply_no_pending_locked_predictions():
    r=result([])
    assert r['baseline_coverage']['coverage'] is None
    assert r['baseline_coverage']['status']=='no_scored_cases'
    assert r['baseline_coverage']['pending_status']=='not_observable_from_verified_evaluation_rows'


@pytest.mark.parametrize('registration',['on_lock','missing_legacy','after_readout'])
def test_real_ledger_verified_entrypoint_controls_baseline_inclusion(tmp_path,monkeypatch,registration):
    from emeris_live import platform_store, prospective_baselines
    from emeris_live.platform_store import PlatformStore
    from emeris_live.store import Store
    monkeypatch.setattr(platform_store,'now',lambda:'2026-01-03T00:00:00+00:00')
    ledger=PlatformStore(Store(tmp_path/registration))
    run=ledger.create_run('owner',input_snapshot={'nct_id':'NCT01234567','drug':'asset-A'},
        outputs={'forecast_kind':'uncalibrated_llm_research','forecasts':{'deepseek-v4-pro':{'status':'predicted','probability':.7}}},
        model_version='deepseek-v4-pro',pipeline_version='fixture-v1',data_cutoff='2026-01-01T00:00:00Z',
        target=TARGET,evidence=[{'url':'https://clinicaltrials.gov/study/NCT01234567','availability_date':'2025-12-31'}])
    with monkeypatch.context() as temporary:
        if registration!='on_lock':temporary.setattr(prospective_baselines,'register_snapshot',lambda *a:None)
        lock=ledger.lock('owner',run['id'],publish=True)
    monkeypatch.setattr(platform_store,'now',lambda:'2026-02-11T00:00:00+00:00')
    if registration=='after_readout':prospective_baselines.register_existing_public_baselines(ledger.store)
    outcome=ledger.submit_outcome('owner',lock['record']['id'],{
        'trial_id':'NCT01234567','endpoint':TARGET['endpoint'],'population':TARGET['population'],'comparison':TARGET['comparison'],
        'label':1,'readout_date':'2026-02-10','source':{'url':'https://sponsor.example/primary-result','published_at':'2026-02-10','sha256':'a'*64},
        'effect_size':None,'confidence_interval':None,'p_value':None,'extraction_status':'complete','conflicts':[]})
    assert ledger.evaluation_rows()==[]
    confirm={k:True for k in ('endpoint_match','population_match','comparison_match','source_checked','prespecified_analysis','earliest_result_checked','evidence_cutoff_checked')}
    ledger.verify_outcome('independent-reviewer',outcome['id'],'approved','Reviewed exact trial target, source and earliest chronology.',confirm)
    rows=ledger.evaluation_rows(public_only=True)
    assert len(rows)==1
    evaluated=result(rows)
    assert evaluated['summary']['answered']==1
    if registration=='on_lock':
        assert evaluated['baseline_common_n']==1
        assert evaluated['baseline_provenance']['records'][0]['record_sha256']==rows[0]['baseline_audits'][0]['record_sha256']
    else:
        assert evaluated['baseline_common_n']==0 and evaluated['baselines']==[]
        expected='baseline_not_registered' if registration=='missing_legacy' else 'baseline_not_registered_before_readout'
        assert expected in evaluated['baseline_coverage']['exclusions'][0]['reasons']
