from pathlib import Path
import copy,json,math
import pytest
from fastapi.testclient import TestClient
from emeris_live.main import create_app
from emeris_live.predome_api import read_release
from emeris_live.predome_statistics import sha,scenario,posterior_grid
from predome_agents import validate_output,TARGETS,Budget
from predome_training import eligible,train
ROOT=Path(__file__).resolve().parents[1];D=ROOT/'predome_data'

def test_readonly_release_hashes_and_legacy(tmp_path):
 c=TestClient(create_app(tmp_path/'s'));c.headers['Origin']='http://testserver';data=c.get('/api/predome/research').json();assert data['integrity_verified']
 assert sha(data['record'])==data['sha256'];assert data['record']['scope']['clinical_results_adjudicated']==0
 assert c.get('/api/forecasts').json()['head_sha256']=='8459b71a823a4a88c1d7d94edbdcb722a6001df826ad765e51bf77b8bbab0910'
 assert c.post('/api/predome/research',json={}).status_code==405
 assert c.delete('/api/predome/research').status_code==405
 assert c.get('/guide').status_code==200;assert 'review.js' in c.get('/trials').text;assert c.get('/predome').status_code==200;assert c.get('/api/predome/report').status_code==200
 assert c.get('/api/predome/export').json()['sha256']==data['sha256']

def test_null_statistical_proxy_and_zero_direction():
 d=[(0.,0.,0.,0.,0.)]*10;r=scenario(d,600,1,0,0,.05,1)
 assert r['direction_probability']==.5
 assert math.isclose(r['statistical_success_proxy_probability'],.025,abs_tol=1e-12)
 assert r['assumptions']['assumed_per_comparison_arm_n']==200

@pytest.mark.parametrize('alpha,fraction',[(0,1),(.5,1),(.05,0),(.05,1.1)])
def test_invalid_design_not_silently_repaired(alpha,fraction):
 with pytest.raises(ValueError):scenario([(0,0,0,0,0)],600,1,.5,.5,alpha,fraction)

def test_joint_posterior_preserves_covariance():
 s=json.loads((D/'stat_spec_v1.json').read_text());points,w,_,edge=posterior_grid(s)
 mb=sum(x*a for (a,b),x in zip(points,w));md=sum(x*b for (a,b),x in zip(points,w));cov=sum(x*(a-mb)*(b-md) for (a,b),x in zip(points,w))
 assert cov<-.03 and edge<.005;assert .7<md<.8

@pytest.mark.parametrize('kind',['duplicate','probability','citation','abstain'])
def test_llm_semantics_validation(kind):
 packet=json.loads((D/'evidence_packet_v1.json').read_text());v={'forecasts':[{'comparison_id':t,'status':'abstain','probability':None,'evidence_ids':[],'reasoning':'insufficient','key_unknowns':['unknown']} for t in TARGETS]}
 if kind=='duplicate':v['forecasts'][0]['comparison_id']=TARGETS[1]
 elif kind=='probability':v['forecasts'][0].update(status='predicted',probability=2)
 elif kind=='citation':v['forecasts'][0]['evidence_ids']=['fabricated_source']
 else:v['forecasts'][0]['probability']=.5
 with pytest.raises(ValueError):validate_output(v,packet)

def test_budget_stops_before_call(tmp_path):
 b=Budget(tmp_path/'budget.json',limit=10);b.reserve('deepseek-flash')
 with pytest.raises(ValueError):b.reserve('deepseek-v4-pro')

def valid_row():return {'record_id':'a','nct_id':'NCT1','asset_group':'assetA','review_status':'double_reviewed','label':1,'features_as_of':'2020-01-01','first_result_at':'2021-01-01','rights_status':'approved_for_this_training','pre_result_snapshot_verified':True,'features':{'phase':3}}

@pytest.mark.parametrize('change',[{'features_as_of':'2022-01-01'},{'review_status':'pending'},{'rights_status':'unknown'},{'features':{'termination_reason':'lack of efficacy'}},{'pre_result_snapshot_verified':False},{'label':True}])
def test_supervised_leakage_and_label_gates(change):
 r=valid_row();r.update(change);a,b=eligible([r]);assert a==[] and len(b)==1

def test_training_refuses_tiny_or_seed_data(tmp_path):
 audit=train([valid_row()],tmp_path/'train');assert not audit['training_completed'];assert not (tmp_path/'train/model.pkl').exists()
 seed=json.loads((D/'training_outcome_seed.json').read_text());a,b=eligible(seed);assert len(a)==0 and len(b)==4

def test_actual_calls_not_claimed_multi_vendor_or_calibrated():
 r=read_release()['record'];assert r['scope']['providers_called']==['deepseek'];assert len(r['llm_records'])==2
 for e in r['llm_records']:
  x=e['record'];assert x['run_status']=='completed' and x['provider_request_id'];assert x['usage']['total_tokens']>0
  assert not x['peer_outputs_visible'] and x['probability_calibration']=='uncalibrated_llm_subjective'
 assert all(x['fused_probability'] is None for x in r['comparisons'])
