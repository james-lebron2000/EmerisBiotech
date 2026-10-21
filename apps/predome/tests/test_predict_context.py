"""Isolated request ordering, no external service or production data."""
import pytest
from emeris_live import predict_api
from test_predict import client, login, request_payload


@pytest.mark.parametrize('kind', ['invalid', 'wrong_schema', 'malformed', 'other_trial', 'missing_parent'])
def test_invalid_context_rejected_before_report_or_paid_reservation(client, monkeypatch, kind):
    headers=login(client)
    body=request_payload('llm')
    body.update(models=['deepseek-flash'],allow_external_processing=True)
    related={'schema_version':'predome.comparables.v1','records':[],'selection':'Current registry context'}
    if kind=='invalid':body['comparable_receipt']='invalid'
    elif kind=='missing_parent':body['parent_prediction_id']='pr_not_owned'
    else:
        if kind=='wrong_schema':related['schema_version']='other'
        if kind=='malformed':related['records']='wrong'
        if kind=='other_trial':related['query_context']={'exclude_nct':'NCT99999999'}
        body['comparable_receipt']=client.app.state.store.pack(related).decode()
    monkeypatch.setattr(predict_api,'capabilities',lambda:{'llm_available':True})
    monkeypatch.setattr(predict_api,'create_report',lambda *a:pytest.fail('Invalid context invoked engine'))
    real_limit=predict_api.limit
    def guard(request,bucket,*args):
        assert not bucket.startswith('predict-paid'), 'Invalid context reserved paid quota'
        return real_limit(request,bucket,*args)
    monkeypatch.setattr(predict_api,'limit',guard)
    response=client.post('/api/predict/run',json=body,headers=headers)
    assert response.status_code==(404 if kind=='missing_parent' else 422)
    assert client.get('/api/platform/predictions').json()['records']==[]


def test_current_trial_binding_preserves_context_without_new_expiry_policy(client,monkeypatch):
    # There is no existing expiry policy: old retrieval time is displayed as old,
    # never upgraded to new evidence or sent to the model.
    value={'schema_version':'predome.comparables.v1','records':[], 'selection':'Historical query context', 'retrieved_at':'2025-01-01T00:00:00Z'}
    monkeypatch.setattr(predict_api,'retrieve_comparables',lambda *args:value.copy())
    receipt=client.get('/api/predict/comparables',params={'condition':'Test condition','exclude_nct':'NCT12345678'}).json()['receipt']
    headers=login(client);body=request_payload();body['comparable_receipt']=receipt
    response=client.post('/api/predict/run',json=body,headers=headers)
    assert response.status_code==200,response.text
    report=response.json()['report']
    assert report['comparable_source_receipt']['retrieved_at']=='2025-01-01T00:00:00Z'
    assert report['comparable_source_receipt']['query_context']['exclude_nct']=='NCT12345678'
    assert report['evidence']==[]
