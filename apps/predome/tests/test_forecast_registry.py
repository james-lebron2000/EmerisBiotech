from datetime import datetime,timezone,timedelta
import json
import pytest
from emeris_live.forecast_registry import freeze,read_registry,sha

NOW=datetime(2026,10,10,2,30,tzinfo=timezone.utc)
def args():
    a=dict(payload={'candidate_id':'trial-a','probability':.6,'model_version':'v1','trial_id':'NCT1','clinical_prediction_validated':False},spec={'model_version':'v1','candidate_id':'trial-a','trial_id':'NCT1'},
        catalog={'candidates':[{'id':'trial-a','nct_id':'NCT1','status_as_of':{'code':'pending_no_public_result_found'}}]},model_code_sha256='a'*64,now=NOW,
        evidence_refresh={'checked_at_utc':(NOW-timedelta(minutes=2)).isoformat(),
                          'candidates':[{'candidate_id':'trial-a','target_results_found':False,'sources':['https://example.org']} ]})
    a['payload']['spec_sha256']=sha(a['spec']);a['payload']['catalog_sha256']=sha(a['catalog'])
    a['payload']['payload_sha256']=sha(a['payload'])
    return a

def test_immutable_chain_and_tamper_detection(tmp_path):
    first=freeze(tmp_path,'record-1',**args());second=freeze(tmp_path,'record-2',**args())
    result=read_registry(tmp_path)
    assert len(result['records'])==2
    assert result['records'][1]['record']['previous_record_sha256']==first['record_sha256']
    assert result['head_sha256']==second['record_sha256']
    with pytest.raises(ValueError):freeze(tmp_path,'record-1',**args())
    p=tmp_path/'record-1.json';r=json.loads(p.read_text());r['forecast']['probability']=.9;p.write_text(json.dumps(r))
    with pytest.raises(ValueError):read_registry(tmp_path)

@pytest.mark.parametrize('flag',[True,None])
def test_reported_or_unresolved_target_rejected(tmp_path,flag):
    a=args();a['evidence_refresh']['candidates'][0]['target_results_found']=flag
    with pytest.raises(ValueError):freeze(tmp_path,'record-1',**a)

@pytest.mark.parametrize('offset',[timedelta(days=-2),timedelta(minutes=1)])
def test_stale_or_future_refresh_rejected(tmp_path,offset):
    a=args();a['evidence_refresh']['checked_at_utc']=(NOW+offset).isoformat()
    with pytest.raises(ValueError):freeze(tmp_path,'record-1',**a)

def test_wrong_inputs_or_payload_rejected(tmp_path):
    for field in ['catalog','spec','payload']:
        a=args();a[field]['unexpected_change']=True
        with pytest.raises(ValueError):freeze(tmp_path,'record-1',**a)
