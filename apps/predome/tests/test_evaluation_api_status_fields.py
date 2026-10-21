"""Public evaluation API preserves score and coverage eligibility separately."""
from fastapi import FastAPI
from fastapi.testclient import TestClient
from emeris_live.store import Store
from emeris_live.platform_store import PlatformStore
from emeris_live.platform_api import router


def test_public_evaluation_status_fields_and_private_input_redaction(tmp_path,monkeypatch):
    row={'snapshot_id':'synthetic-snapshot','prediction_id':'synthetic-prediction','trial_id':'NCT12345678',
         'prediction_status':'abstain','coverage_eligible':True,'score_eligible':False,
         'selection_policy':'earliest_locked_trial_target_model','probability':None,
         'input_snapshot':{'protocol_text':'PRIVATE'},'reasoning':'PRIVATE'}
    def evaluation_rows(self,public_only=True):
        assert public_only is True
        yield row
    monkeypatch.setattr(PlatformStore,'iter_evaluation_rows',evaluation_rows)
    app=FastAPI();app.state.store=Store(tmp_path/'isolated');app.include_router(router)
    response=TestClient(app).get('/api/platform/evaluations')
    assert response.status_code==200
    public=response.json()['records'][0]
    for key in ('prediction_status','coverage_eligible','score_eligible','selection_policy'):
        assert public[key]==row[key]
    assert public['probability'] is None
    assert 'input_snapshot' not in public and 'reasoning' not in public
