import csv
import io
import json
import hashlib
import pytest
from fastapi.testclient import TestClient
from emeris_live.main import create_app, ROOT

@pytest.fixture
def clients(tmp_path):
    app=create_app(tmp_path/'data')
    def make(name='researcher'):
        c=TestClient(app,base_url='http://testserver')
        c.headers['Origin']='http://testserver'
        r=c.post('/api/auth/register',json={'name':name,'password':'test-only-strong-password'})
        assert r.status_code==200,r.text
        c.headers['x-csrf-token']=r.json()['csrf']
        return c
    return make,app

def upload(c,prefix='A',age='30',extra='',learning=True):
    raw='SUBJECT_ID,age_years,SEX\n'+''.join(f'{prefix}{i},{age},FEMALE\n' for i in range(12))+extra
    return c.post('/api/upload',data={'disease':'crc','authorized':'true','learning':str(learning).lower()},files={'file':('test.csv',raw.encode(),'text/csv')})

def confirm(c,id,learn=True,**changes):
    mapping={'SUBJECT_ID':'subject_id','age_years':'age','SEX':'sex'}
    mapping.update(changes)
    return c.post(f'/api/datasets/{id}/mapping',json={'mapping':mapping,'expected_policy':c.get('/api/state').json()['policy']['id'],'confirmed_semantics':True,'learn':learn})

def test_learning_promotion_future_import_and_rollback(clients):
    make,app=clients;c=make()
    a=upload(c).json();r=confirm(c,a['id']);assert r.status_code==200 and not r.json()['promoted']
    assert not confirm(c,a['id']).json()['promoted']
    b=upload(c,'B').json();r=confirm(c,b['id']);assert len(r.json()['promoted'])==1
    policy=c.get('/api/state').json()['policy'];assert policy['aliases']['AGE_YEARS']=='age'
    d=upload(c,'C').json();assert d['mapping']['age_years']=='age'
    c.post('/api/policy/rollback',json={'expected_policy':policy['id']}).raise_for_status()
    p=c.get('/api/state').json()['policy'];assert 'AGE_YEARS' not in p['aliases'] and 'AGE_YEARS' in p['blocked']
    assert confirm(c,b['id']).status_code==422
    assert c.get('/api/datasets/'+d['id']).json()['mapping']['age_years']=='age'

def test_patient_overlap_cannot_promote(clients):
    make,_=clients;c=make();a=upload(c).json();confirm(c,a['id'])
    b=upload(c,age='31').json();r=confirm(c,b['id']);assert not r.json()['promoted']
    assert 'AGE_YEARS' not in c.get('/api/state').json()['policy']['aliases']

def test_no_learning_consent_and_repeat_hash(clients):
    make,_=clients;c=make();a=upload(c,learning=False).json()
    assert confirm(c,a['id']).status_code==422
    assert confirm(c,a['id'],learn=False).status_code==200
    assert upload(c,learning=True).status_code==409

def test_invalid_numeric_and_direct_identifiers(clients):
    make,_=clients;c=make();a=upload(c,age='inf').json()
    r=confirm(c,a['id']);assert r.status_code==422
    r=c.post('/api/upload',data={'disease':'crc','authorized':'true'},files={'file':('bad.csv',b'SUBJECT_ID,patient_name\na,Someone\n')})
    assert r.status_code==422

def test_auth_csrf_owner_isolation_and_no_plaintext(clients):
    make,app=clients;c=make();d=upload(c).json();other=make('other')
    assert other.get('/api/datasets/'+d['id']).status_code==404
    assert other.get('/api/datasets/'+d['id']+'/report').status_code==404
    assert TestClient(app).get('/api/state').status_code==401
    csrf=c.headers.pop('x-csrf-token');assert c.post('/api/public/gastric').status_code==403
    c.headers['x-csrf-token']=csrf;c.headers['Origin']='https://evil.example';assert c.post('/api/public/crc').status_code==403
    with app.state.store.connect() as db:
        assert b'age_years' not in bytes(db.execute('SELECT payload FROM datasets').fetchone()[0])

def test_public_real_data_hash_and_counts(clients):
    make,_=clients;c=make()
    manifest=json.loads((ROOT/'public_data/manifest.json').read_text())
    for disease in ('crc','gastric'):
        r=c.post('/api/public/'+disease);assert r.status_code==200,r.text
        d=r.json();assert d['source']=='public_real' and d['summary']['unique_subjects']>400
        assert d['summary']['invalid']=={}
        source=next(x for x in manifest['files'] if x['sha256']==d['source_sha256'])
        raw=(ROOT/'public_data'/source['path']).read_bytes();assert hashlib.sha256(raw).hexdigest()==d['source_sha256']
        reader=list(csv.DictReader((l for l in raw.decode().splitlines() if not l.startswith('#')),delimiter='\t'))
        assert len(reader)==d['summary']['rows']

def test_policy_version_conflict_and_persistence(clients):
    make,app=clients;c=make();a=upload(c).json();old=c.get('/api/state').json()['policy']['id'];confirm(c,a['id']);b=upload(c,'B').json();confirm(c,b['id'])
    assert c.post('/api/policy/rollback',json={'expected_policy':old}).status_code==409
    new=TestClient(create_app(app.state.store.path),base_url='http://testserver');new.cookies.update(c.cookies)
    assert new.get('/api/state').json()['policy']['aliases']['AGE_YEARS']=='age'

def test_malformed_upload_authorization_and_duplicate_id(clients):
    make,_=clients;c=make()
    r=c.post('/api/upload',data={'disease':'crc'},files={'file':('bad.csv',b'SUBJECT_ID,AGE\na,30\n')});assert r.status_code==422
    r=c.post('/api/upload',data={'disease':'crc','authorized':'true'},files={'file':('bad.csv',b'SUBJECT_ID,AGE\na,30,extra\n')});assert r.status_code==422
    a=upload(c,extra='A0,30,FEMALE\n').json();assert confirm(c,a['id']).status_code==422

def test_json_report_no_patient_rows(clients):
    make,_=clients;c=make();a=upload(c).json();r=c.get('/api/datasets/'+a['id']+'/report');assert r.status_code==200
    assert 'rows' not in r.json() and 'preview' not in r.json()
    assert r.json()['summary']['scope']=='descriptive_research_only'
