import hashlib
import json
from pathlib import Path
from fastapi.testclient import TestClient
from emeris_live.main import create_app


def test_trial_catalog_snapshot_and_no_future_claim(tmp_path):
    client=TestClient(create_app(tmp_path/'store'))
    response=client.get('/api/trials/catalog')
    assert response.status_code==200
    value=response.json()
    raw=(Path(__file__).resolve().parents[1]/'trial_data/candidates.json').read_bytes()
    assert value['source_sha256']==hashlib.sha256(raw).hexdigest()
    assert value['snapshot_only'] and not value['clinical_prediction_validated']
    assert len(value['catalog']['candidates'])>0
    for row in value['catalog']['candidates']:
        assert row['assessment']['probability'] is None
        assert row['source_ids']
        assert set(row['source_ids']).issubset({s['id'] for s in value['catalog']['sources']})


def test_trial_api_origin_bounds_and_no_candidate_scenario(tmp_path):
    client=TestClient(create_app(tmp_path/'store'))
    assert client.post('/api/trials/execute',json={}).status_code==403
    client.headers['Origin']='http://testserver'
    result=client.post('/api/trials/execute',json={})
    assert result.status_code==200
    assert result.json()['candidate_forecast'] is None
    for body in [{'event_count':-1},{'candidate_id':'PACIFIC9'},{'endpoint':'ORR'},
                 {'mode':'evidence_conditioned'}, {'event_count':400.5}]:
        assert client.post('/api/trials/execute',json=body).status_code==422
    assert client.get('/trials').status_code==200
    assert "script-src 'self'" in client.get('/trials').headers['content-security-policy']
