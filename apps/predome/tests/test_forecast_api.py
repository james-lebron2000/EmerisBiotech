from pathlib import Path
import hashlib
import json
import math
from fastapi.testclient import TestClient
from emeris_live.main import create_app
from emeris_live.forecast_registry import sha
from emeris_live.forecast_model import build_forecast

def numerically_equal(a,b):
    # Raw frozen bytes retain their own hash; libm/Python can differ by one ULP
    # when independently recomputing mixture quantiles on another runtime.
    if isinstance(a,dict):
        assert a.keys()==b.keys()
        for k in a:
            if k!='payload_sha256':numerically_equal(a[k],b[k])
    elif isinstance(a,list):
        assert len(a)==len(b)
        for x,y in zip(a,b):numerically_equal(x,y)
    elif isinstance(a,float):assert math.isclose(a,b,rel_tol=1e-12,abs_tol=1e-12)
    else:assert a==b

def test_frozen_forecast_is_reproducible_and_read_only(tmp_path):
    client=TestClient(create_app(tmp_path/'store'));client.headers['Origin']='http://testserver'
    response=client.get('/api/forecasts');assert response.status_code==200
    data=response.json();assert data['integrity_verified'] and not data['clinical_prediction_validated']
    entry=data['records'][0];record=entry['record']
    assert sha(record)==entry['record_sha256']
    numerically_equal(build_forecast(record['catalog_snapshot'],record['model_spec']),record['forecast'])
    assert record['model_code_sha256']==hashlib.sha256((Path(__file__).resolve().parents[1]/'emeris_live/forecast_model.py').read_bytes()).hexdigest()
    assert client.get('/api/forecasts/'+record['id']).json()==entry
    assert client.get('/api/forecasts/missing').status_code==404
    assert client.post('/api/forecasts',json={}).status_code==405
    assert client.delete('/api/forecasts/'+record['id']).status_code==405
    assert all(c['adjudication_status']=='pending' for c in record['forecast']['comparisons'])
    assert record['forecast']['trial_level']['statistical_success_probability'] is None

def test_source_refresh_is_preserved_separately_from_old_catalog(tmp_path):
    client=TestClient(create_app(tmp_path/'store'))
    record=client.get('/api/forecasts').json()['records'][0]['record']
    assert record['catalog_snapshot']['as_of']=='2026-10-09'
    assert record['evidence_refresh']['checked_at_utc'].startswith('2026-10-10')
    assert record['frozen_at_utc']>=record['evidence_refresh']['checked_at_utc'].replace('Z','+00:00')
    assert not record['independent_timestamp_verified']
