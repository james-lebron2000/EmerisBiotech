import pytest
from fastapi.testclient import TestClient
from emeris_live.main import create_app

def setup(tmp_path,monkeypatch):
    monkeypatch.setenv('EMERIS_MODE','public')
    monkeypatch.setenv('EMERIS_PUBLIC_ORIGIN','https://research.example')
    monkeypatch.setenv('EMERIS_ALLOWED_HOSTS','research.example,127.0.0.1')
    app=create_app(tmp_path/'data')
    c=TestClient(app,base_url='https://research.example');c.headers['Origin']='https://research.example'
    return app,c

def register(c,name='researcher'):
    r=c.post('/api/auth/register',json={'name':name,'password':'long-test-password-123'})
    assert r.status_code==200,r.text
    c.headers['x-csrf-token']=r.json()['csrf'];return r

def test_public_requires_explicit_https_origin(tmp_path,monkeypatch):
    monkeypatch.setenv('EMERIS_MODE','public')
    monkeypatch.delenv('EMERIS_PUBLIC_ORIGIN',raising=False)
    with pytest.raises(RuntimeError):create_app(tmp_path/'invalid')

def test_secure_cookie_host_origin_and_public_status(tmp_path,monkeypatch):
    app,c=setup(tmp_path,monkeypatch);r=register(c)
    assert 'Secure' in r.headers['set-cookie'] and 'HttpOnly' in r.headers['set-cookie']
    assert 'max-age' in r.headers['strict-transport-security']
    assert c.get('/api/health').json()['mode']=='server_research_pilot'
    assert c.get('/api/state',headers={'Host':'evil.example'}).status_code==400
    assert c.post('/api/public/crc',headers={'Origin':'http://research.example'}).status_code==403

def test_rate_limit_survives_restart(tmp_path,monkeypatch):
    app,c=setup(tmp_path,monkeypatch)
    for _ in range(12):
        assert c.post('/api/auth/login',json={'name':'missing','password':'bad-but-long-password'}).status_code==401
    restarted=TestClient(create_app(tmp_path/'data'),base_url='https://research.example')
    r=restarted.post('/api/auth/login',headers={'Origin':'https://research.example'},json={'name':'missing','password':'bad-but-long-password'})
    assert r.status_code==429

def test_account_delete_clears_owned_data_without_other_account(tmp_path,monkeypatch):
    app,c=setup(tmp_path,monkeypatch);register(c)
    c.post('/api/public/crc').raise_for_status()
    other=TestClient(app,base_url='https://research.example');other.headers['Origin']='https://research.example';register(other,'other')
    other.post('/api/public/gastric').raise_for_status()
    result=c.delete('/api/account');assert result.status_code==200 and result.json()['backup_retention_days']==14
    assert c.get('/api/state').status_code==401
    assert len(other.get('/api/state').json()['datasets'])==1
    with app.state.store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM users').fetchone()[0]==1
        assert db.execute('SELECT COUNT(*) FROM datasets').fetchone()[0]==1

def test_dataset_and_account_caps(tmp_path,monkeypatch):
    monkeypatch.setenv('EMERIS_MAX_USERS','1');monkeypatch.setenv('EMERIS_MAX_DATASETS','1')
    app,c=setup(tmp_path,monkeypatch);register(c)
    assert c.post('/api/auth/register',json={'name':'second','password':'long-test-password-123'}).status_code==403
    assert c.post('/api/public/crc').status_code==200
    assert c.post('/api/public/gastric').status_code==409
