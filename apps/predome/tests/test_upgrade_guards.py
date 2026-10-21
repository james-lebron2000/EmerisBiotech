import time
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from emeris_live.main import create_app,digest
from emeris_live.platform_store import PlatformStore
from emeris_live.intake import tracked_trial_ids

def client(app):
    c=TestClient(app,base_url='http://testserver');c.headers['Origin']='http://testserver'
    r=c.post('/api/auth/register',json={'name':'guardtester','password':'test-only-strong-password'});assert r.status_code==200
    c.headers['x-csrf-token']=r.json()['csrf'];return c

def test_daily_budget_not_reset_by_hourly_cleanup(tmp_path):
    app=create_app(tmp_path/'db');limit=app.state.platform_rate_limit
    with app.state.store.connect() as db:db.execute('INSERT INTO auth_limits VALUES(?,?,?)',(digest('paid-daily'),time.time()-3601,20))
    limit('other-hourly',1,3600)
    with pytest.raises(HTTPException) as err:limit('paid-daily',20,86400)
    assert err.value.status_code==429

def test_ledger_account_delete_fails_without_false_success(tmp_path):
    app=create_app(tmp_path/'db');c=client(app);ledger=PlatformStore(app.state.store)
    with app.state.store.connect() as db:owner=db.execute('SELECT id FROM users').fetchone()[0]
    run=ledger.create_run(owner,{'nct_id':'NCT12345678','protocol':'private'}, {'forecasts':{}},'v1','v1','2025-01-01')
    r=c.delete('/api/account');assert r.status_code==409 and '均未删除' in r.json()['detail']
    assert c.get('/api/state').status_code==200
    assert ledger.get_run(owner,run['id'])['input_snapshot']['protocol']=='private'

def test_private_trial_id_never_sent_to_external_intake(tmp_path):
    app=create_app(tmp_path/'db');ledger=PlatformStore(app.state.store)
    for nct,publish in [('NCT12345678',True),('NCT99999999',False)]:
        run=ledger.create_run('owner',{'nct_id':nct}, {'forecast_kind':'uncalibrated_llm_research','forecasts':{'m':{'status':'predicted','probability':.5}}},'v1','v1','2025-01-01',target={'endpoint':'Primary','population':'ITT','comparison':'A versus B','success_definition':'Prespecified significance'},evidence=[{'url':'https://clinicaltrials.gov/study/'+nct,'availability_date':'2024-12-31'}])
        ledger.lock('owner',run['id'],publish=publish)
    ids=tracked_trial_ids(app.state.store)
    assert 'NCT12345678' in ids and 'NCT99999999' not in ids
