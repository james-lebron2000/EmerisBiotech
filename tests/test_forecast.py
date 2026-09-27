import pytest
from vbt.forecast import TrialForecast,score_forecasts

def row():
 return dict(trial_id='NCT00000001',asset_group='TEST_ASSET',phase=2,endpoint_definition='Predefined primary endpoint',cutoff='2024-01-01T00:00:00Z',recorded_at='2023-12-31T00:00:00Z',source_latest_public_at='2023-12-30T00:00:00Z',training_latest_label_at='2023-01-01T00:00:00Z',model_version='TEST',source_manifest_sha256='0'*64,probability=.8,baseline_probability=.5,commercial_rights='verified',outcome='success',outcome_public_at='2024-02-01T00:00:00Z',outcome_source='TEST ONLY',outcome_adjudicator='TEST ONLY',synthetic=True)
@pytest.mark.parametrize('key,value',[('recorded_at','2024-02-01T00:00:00Z'),('source_latest_public_at','2024-02-01T00:00:00Z'),('training_latest_label_at','2024-01-01T00:00:00Z'),('outcome_public_at','2023-01-01T00:00:00Z'),('probability',float('nan')),('cutoff','2024-01-01')])
def test_reject(key,value):
 r=row();r[key]=value
 with pytest.raises(ValueError):TrialForecast.model_validate(r)
def test_score():
 s=score_forecasts([row()]);assert s['phases']['2']['brier']==pytest.approx(.04);assert s['phases']['3']['brier'] is None;assert not s['investment_edge_established']
def test_unresolved_not_failure():
 r=row();r['outcome']='administrative_stop';assert score_forecasts([r])['phases']['2']['n']==0
@pytest.mark.parametrize('kind',['rights','duplicate','overlap'])
def test_admission(kind):
 r=row()
 if kind=='rights':r['commercial_rights']='noncommercial'
 with pytest.raises(ValueError):score_forecasts([r,r] if kind=='duplicate' else [r],['TEST_ASSET'] if kind=='overlap' else [])
def test_empty():assert score_forecasts([])['phases']['2']['n']==0
