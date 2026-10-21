"""Boundary tests for historical evidence and fair comparison, not clinical validation."""
from pathlib import Path
import hashlib, json
import pytest
from predome_pilot import audit_rows, partition, source_ok
from predome_pilot_llm import validate

def test_asset_embargo_preserves_test_and_purges_alias_group():
    rows = [{'nct_id':'older-a','asset_group':'same-canonical-asset','first_result_at':'2021-01-01'}, {'nct_id':'later-a','asset_group':'same-canonical-asset','first_result_at':'2024-01-01'}, {'nct_id':'older-b','asset_group':'different-asset','first_result_at':'2020-01-01'}, {'nct_id':'future','asset_group':'other','first_result_at':'2026-01-01'}]
    train, test, purged = partition(rows)
    assert [r['nct_id'] for r in train] == ['older-b']
    assert [r['nct_id'] for r in test] == ['later-a']
    assert purged == ['older-a']

@pytest.mark.parametrize('change', [{'features_as_of':'2025-01-01','first_result_at':'2024-01-01'}, {'features_as_of':'2022-02-31','first_result_at':'2024-01-01'}, {'label':True}, {'label':None}, {'features':{'phase':3,'randomized':1,'termination_reason':'efficacy failure'}}, {'review_status':'double_reviewed'}])
def test_forged_preoutcome_or_review_flags_do_not_admit_records(change):
    row={'nct_id':'NCT1','label':1,'label_scope':'declared_primary_analysis','earliest_result_status':'verified','features_as_of':'2020-01-01','first_result_at':'2021-01-01','asset_group':'a','features':{'phase':3,'randomized':1},'review_status':'source_curated_agent_checked'}
    row.update(change); accepted, rejected = audit_rows([row]);assert not accepted and rejected

def test_snapshot_hash_required():
    assert not source_ok({'path':'../../not-a-source','sha256':'fake'})
    assert not source_ok({'path':'predome_pilot.py','sha256':'fake'})

def test_llm_replay_never_accepts_duplicated_cases_or_abstention_numbers():
    packet={'cases':[{'case_id':'case-000'},{'case_id':'case-001'}]}
    rows=[{'case_id':'case-000','status':'predicted','probability':.4,'reasoning':'uncertain'},{'case_id':'case-001','status':'abstain','probability':None,'reasoning':'missing evidence'}]
    assert validate({'forecasts':rows},packet)==rows
    rows[1]['probability']=.5
    with pytest.raises(ValueError):validate({'forecasts':rows},packet)
    rows[1].update(case_id='case-000',probability=None)
    with pytest.raises(ValueError):validate({'forecasts':rows},packet)

def test_published_historical_bundle_integrity_and_readonly(tmp_path):
    from emeris_live.historical_api import read_historical, DATA
    if not (DATA/'release.json').exists(): pytest.skip('Historical release not frozen yet')
    from emeris_live.main import create_app
    from fastapi.testclient import TestClient
    record=read_historical();assert record['integrity_verified']
    assert record['record']['clinical_prediction_validated'] is False
    assert record['record']['frozen_forward_predictions_modified'] is False
    c=TestClient(create_app(tmp_path/'s'));c.headers['Origin']='http://testserver'
    assert c.get('/api/predome/historical').json()['sha256']==record['sha256']
    assert c.get('/api/predome/historical/export').json()['sha256']==record['sha256']
    assert c.get('/api/predome/historical/report').status_code==200
    assert c.post('/api/predome/historical',json={}).status_code==405

@pytest.mark.parametrize('tamper', ['label','enrollment','primary_count','alignment','date'])
def test_real_frozen_evidence_rejects_wrong_target_or_post_result_values(tamper):
    import copy
    from predome_pilot import DATA
    if not (DATA/'dataset.json').exists(): pytest.skip('Frozen historical cohort pending')
    accepted,_=audit_rows(json.loads((DATA/'dataset.json').read_text()))
    assert accepted, 'Real cohort must include genuinely admissible rows'
    row=copy.deepcopy(accepted[0]);assert len(audit_rows([row])[0])==1
    if tamper=='label':row['label']=1-row['label']
    elif tamper=='enrollment':row['features']['planned_enrollment']=(row['features'].get('planned_enrollment') or 0)+1
    elif tamper=='primary_count':row['features']['primary_endpoint_count']+=1
    elif tamper=='alignment':row['snapshot_target_match_status']='unknown'
    else:row['features_as_of']=row['first_result_at']
    assert not audit_rows([row])[0]
