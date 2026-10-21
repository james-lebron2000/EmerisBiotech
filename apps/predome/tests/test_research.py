import hashlib
import json
import statistics
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from emeris_live.main import create_app
from emeris_live.research import ResearchRequest, execute, auc, fit_line


def test_observed_auc_known_curves():
    assert auc([100]*6)==pytest.approx(1)
    assert auc([0]*6)==0
    assert auc([50]*6)==pytest.approx(.5)


def test_line_fits_known_relationship():
    assert fit_line([(0,1),(1,3),(2,5)])=={'intercept':1,'slope':2}


def test_deterministic_output_and_hash():
    a=execute(ResearchRequest());b=execute(ResearchRequest())
    assert a==b
    digest=a.pop('result_sha256')
    assert hashlib.sha256(json.dumps(a,ensure_ascii=False,sort_keys=True,allow_nan=False).encode()).hexdigest()==digest
    assert execute(ResearchRequest(seed=43))['wells']!=b['wells']


def test_simulation_units_controls_and_grouped_evaluation():
    r=execute(ResearchRequest(donors=12));e=r['model_evaluation']
    assert len(r['wells'])==12*(4*6*3+3)
    assert len(r['next_experiments'])==6
    assert not set(e['train_donors'])&set(e['test_donors'])
    for fit in e['parameters']:
        train=[x for x in r['responses'] if x['compound']==fit['compound'] and x['split']=='train']
        assert fit['drug_mean_baseline']==pytest.approx(statistics.mean(x['auc'] for x in train))
        expected=fit_line([(x['baseline_marker'],x['auc']) for x in train])
        assert fit['slope']==pytest.approx(expected['slope'])
    assert len(e['predictions'])==3*4
    assert e['model_mae']==pytest.approx(statistics.mean(abs(p['predicted_auc']-p['observed_auc']) for p in e['predictions']))
    assert r['evidence_kind']=='synthetic' and not r['clinical_use']


@pytest.mark.parametrize('disease,total',[('crc',594),('gastric',440)])
def test_public_sources_and_filter_conservation(disease,total):
    r=execute(ResearchRequest(kind='public_cohort',disease=disease))
    assert r['summary']['included_patients']==total
    assert sum(r['stage_counts'].values())==total
    assert sum(r['age_counts'].values())==total
    assert sum(r['sex_counts'].values())==total
    selected=execute(ResearchRequest(kind='public_cohort',disease=disease,age_min=60,age_max=80,stage='II'))
    s=selected['summary'];assert s['included_patients']+sum(selected['exclusions'].values())==total
    assert 0<s['included_patients']<total
    assert selected['source']['sha256']==r['source']['sha256']


def test_empty_public_cohort_is_valid_empty_not_zero_source():
    r=execute(ResearchRequest(kind='public_cohort',age_min=0,age_max=1))
    assert r['summary']['included_patients']==0
    assert r['summary']['source_patients']==594
    assert r['summary']['age_median'] is None


@pytest.mark.parametrize('kwargs',[{'donors':10000},{'noise':float('nan')},{'age_min':80,'age_max':60},{'kind':'arbitrary_code'}])
def test_limits(kwargs):
    with pytest.raises(ValidationError):ResearchRequest(**kwargs)


def test_run_persistence_isolation_delete_and_restart(tmp_path):
    app=create_app(tmp_path/'data')
    def client(name):
        c=TestClient(app);c.headers['Origin']='http://testserver'
        response=c.post('/api/auth/register',json={'name':name,'password':'test-only-strong-password'})
        c.headers['x-csrf-token']=response.json()['csrf'];return c
    a=client('research_a');b=client('research_b')
    run=a.post('/api/research/runs',json={'donors':12});assert run.status_code==200
    id=run.json()['id']
    assert b.get('/api/research/runs/'+id).status_code==404
    assert b.delete('/api/research/runs/'+id).status_code==404
    assert len(a.get('/api/research/runs').json())==1
    restarted=TestClient(create_app(tmp_path/'data'));restarted.cookies.update(a.cookies)
    assert restarted.get('/api/research/runs/'+id).json()['result']==run.json()['result']
    with app.state.store.connect() as db:
        raw=db.execute('SELECT payload FROM research_runs WHERE id=?',(id,)).fetchone()[0]
        assert b'SIM-D001' not in raw
    assert a.delete('/api/account').status_code==200
    with app.state.store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM research_runs').fetchone()[0]==0


def test_guest_run_does_not_persist_and_origin_required(tmp_path):
    app=create_app(tmp_path/'data');c=TestClient(app)
    assert c.get('/research').status_code==200
    assert c.post('/api/research/execute',json={}).status_code==403
    c.headers['Origin']='http://testserver'
    assert c.post('/api/research/execute',json={}).status_code==200
    assert c.post('/api/research/runs',json={}).status_code==401
    with app.state.store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM research_runs').fetchone()[0]==0


def test_run_limit_and_csrf(tmp_path):
    app=create_app(tmp_path/'data');c=TestClient(app);c.headers['Origin']='http://testserver'
    csrf=c.post('/api/auth/register',json={'name':'limited','password':'test-only-strong-password'}).json()['csrf']
    assert c.post('/api/research/runs',json={}).status_code==403
    c.headers['x-csrf-token']=csrf
    with app.state.store.connect() as db:
        owner=db.execute('SELECT id FROM users').fetchone()[0]
        for i in range(30):db.execute('INSERT INTO research_runs VALUES(?,?,?,?)',(str(i),owner,app.state.store.pack({}),'test'))
    assert c.post('/api/research/runs',json={}).status_code==409
