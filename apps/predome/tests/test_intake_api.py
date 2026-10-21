"""Real session/CSRF and independently reviewed public-source intake boundaries."""
import json
import pytest
from fastapi.testclient import TestClient
from emeris_live.main import create_app
from emeris_live import intake, platform_store
from emeris_live.platform_store import PlatformStore

NCT='NCT01234567'
TARGET={'endpoint':'PFS','population':'ITT adults','comparison':'Drug versus placebo','success_definition':'Prespecified PFS superiority analysis'}
CONFIRM={k:True for k in ('endpoint_match','population_match','comparison_match','source_checked','prespecified_analysis','earliest_result_checked','evidence_cutoff_checked')}

@pytest.fixture
def setup(tmp_path,monkeypatch):
    monkeypatch.setenv('EMERIS_REVIEWERS','intake_submitter,intake_independent')
    monkeypatch.setattr(platform_store,'now',lambda:'2025-01-10T12:00:00+00:00')
    app=create_app(tmp_path/'store');clients={};headers={};owners={}
    for name in ('intake_owner','intake_submitter','intake_independent'):
        c=TestClient(app);response=c.post('/api/auth/register',json={'name':name,'password':'IntakeTestPassword123!'},headers={'Origin':'http://testserver'})
        assert response.status_code==200,response.text
        clients[name]=c;headers[name]={'Origin':'http://testserver','x-csrf-token':response.json()['csrf']}
        with app.state.store.connect() as db:owners[name]=db.execute('SELECT id FROM users WHERE name=?',(name,)).fetchone()[0]
    ledger=PlatformStore(app.state.store);intake.initialize(app.state.store)
    def snapshot(publish=True):
        run=ledger.create_run(owners['intake_owner'],input_snapshot={'nct_id':NCT,'protocol_text':'PRIVATE PROTOCOL'},outputs={'forecast_kind':'uncalibrated_llm_research','forecasts':{'qa-model':{'status':'predicted','probability':.6,'reasoning':'PRIVATE MODEL REASONING'}}},model_version='qa-model',pipeline_version='qa-intake',data_cutoff='2025-01-09',evidence=[{'url':'https://clinicaltrials.gov/study/'+NCT,'availability_date':'2025-01-08'}],target=TARGET)
        return ledger.lock(owners['intake_owner'],run['id'],publish=publish)['record']['id']
    yield app,clients,headers,ledger,snapshot,monkeypatch
    for c in clients.values():c.close()


def candidate(app,kind='pubmed_literature_candidate',articles=None,posted='2025-02-15'):
    structured={'articles':articles if articles is not None else [{'id':'12345678','publication_url':'https://pubmed.ncbi.nlm.nih.gov/12345678/','firstPublicationDate':'2025-02-15','title':'Synthetic source fixture'}]}
    if kind!='pubmed_literature_candidate':
        structured=intake.registry_summary({'hasResults':kind=='reported_results_candidate','protocolSection':{'statusModule':{'resultsFirstPostDateStruct':{'date':posted} if posted else None,'lastUpdatePostDateStruct':{'date':'2025-03-01'},'primaryCompletionDateStruct':{'date':'2025-01-01'}}}})
    return intake.save_candidate(app.state.store,NCT,kind,'https://www.ebi.ac.uk/europepmc/webservices/rest/search?query='+NCT if kind=='pubmed_literature_candidate' else 'https://clinicaltrials.gov/api/v2/studies/'+NCT,json.dumps({'kind':kind,'structured':structured}).encode(),structured)[0]


def body(snapshot_id,**changes):
    return {'snapshot_id':snapshot_id,'article_id':'12345678',**{k:TARGET[k] for k in ('endpoint','population','comparison')},'label':1,'readout_date':'2025-02-10',**changes}


def test_intake_requires_real_session_reviewer_and_csrf(setup):
    app,cs,hs,ledger,make,monkeypatch=setup;cid=candidate(app);payload=body(make());url='/api/intake/candidates/'+cid+'/link'
    with TestClient(app) as anon:
        assert anon.get('/api/intake/candidates').status_code==401
        assert anon.post(url,json=payload,headers={'Origin':'http://testserver'}).status_code==401
    owner=cs['intake_owner'];submitter=cs['intake_submitter']
    assert owner.get('/api/intake/candidates').status_code==403
    assert owner.post(url,json=payload,headers=hs['intake_owner']).status_code==403
    assert submitter.get('/api/intake/candidates').status_code==200
    assert submitter.post(url,json=payload,headers={'Origin':'http://testserver'}).status_code==403
    assert submitter.post(url,json=payload,headers=hs['intake_submitter']|{'Origin':'https://untrusted.example'}).status_code==403
    assert submitter.post('/api/intake/candidates/missing/link',json=payload,headers=hs['intake_submitter']).status_code==404


@pytest.mark.parametrize('article_id,articles',[
    (None,None),('wrong',None),
    ('12345678',[{'id':'12345678','publication_url':'https://pubmed.ncbi.nlm.nih.gov/12345678/'}]),
    ('12345678',[{'id':'12345678','firstPublicationDate':'2025-02-15'}]),
])
def test_pubmed_requires_selected_article_with_original_url_and_date(setup,article_id,articles):
    app,cs,hs,ledger,make,monkeypatch=setup;cid=candidate(app,articles=articles)
    r=cs['intake_submitter'].post('/api/intake/candidates/'+cid+'/link',json=body(make(),article_id=article_id),headers=hs['intake_submitter'])
    assert r.status_code==422
    assert ledger.list_outcomes(reviewer=True)==[]


@pytest.mark.parametrize('kind',['pubmed_literature_candidate','reported_results_candidate'])
def test_source_published_date_uses_metadata_not_user_readout(setup,kind):
    app,cs,hs,ledger,make,monkeypatch=setup;cid=candidate(app,kind=kind)
    r=cs['intake_submitter'].post('/api/intake/candidates/'+cid+'/link',json=body(make()),headers=hs['intake_submitter'])
    assert r.status_code==200,r.text
    item=r.json();result=item['outcome']['outcome']
    assert result['readout_date']=='2025-02-10'
    assert result['source']['published_at']=='2025-02-15'
    assert item['auto_approved'] is False and item['status']=='needs_independent_review'
    if kind=='pubmed_literature_candidate':
        assert result['source']['url']=='https://pubmed.ncbi.nlm.nih.gov/12345678/'
        assert result['source']['metadata_sha256']
    else:
        assert result['source']['url']=='https://clinicaltrials.gov/api/v2/studies/'+NCT
    assert ledger.evaluation_rows()==[]


@pytest.mark.parametrize('kind,posted',[('registry_update','2025-02-15'),('discovery_requires_readout_source','2025-02-15'),('reported_results_candidate',None)])
def test_registry_updates_completion_or_missing_results_date_cannot_be_result(setup,kind,posted):
    app,cs,hs,ledger,make,monkeypatch=setup;cid=candidate(app,kind=kind,posted=posted)
    source=intake.get_candidate(app.state.store,cid)
    assert source['structured']['label'] is None
    assert source['structured']['readout_date'] is None
    r=cs['intake_submitter'].post('/api/intake/candidates/'+cid+'/link',json=body(make()),headers=hs['intake_submitter'])
    assert r.status_code==422
    assert ledger.list_outcomes(reviewer=True)==[]


@pytest.mark.parametrize('publish',[True,False])
def test_link_requires_independent_approval_and_public_result_excludes_private(setup,publish):
    app,cs,hs,ledger,make,monkeypatch=setup;sid=make(publish);cid=candidate(app)
    monkeypatch.setattr(platform_store,'now',lambda:'2025-03-01T12:00:00+00:00')
    r=cs['intake_submitter'].post('/api/intake/candidates/'+cid+'/link',json=body(sid),headers=hs['intake_submitter']);assert r.status_code==200,r.text
    oid=r.json()['outcome']['id'];review_url='/api/platform/reviews/'+oid
    review={'decision':'approved','reason':'PRIVATE REVIEW NOTE checked primary source and exact target','confirmations':CONFIRM}
    with TestClient(app) as anon:
        assert anon.get('/api/platform/public-outcomes').json()['records']==[]
        assert anon.post(review_url,json=review,headers={'Origin':'http://testserver'}).status_code==401
        assert cs['intake_submitter'].post(review_url,json=review,headers=hs['intake_submitter']).status_code==422
        assert cs['intake_owner'].post(review_url,json=review,headers=hs['intake_owner']).status_code==403
        approved=cs['intake_independent'].post(review_url,json=review,headers=hs['intake_independent']);assert approved.status_code==200,approved.text
        public=anon.get('/api/platform/public-outcomes').json();assert len(public['records'])==int(publish)
        assert 'PRIVATE' not in json.dumps(public)
        assert 'submitted_by' not in json.dumps(public) and 'reviewer_id' not in json.dumps(public)
        if publish:
            record=public['records'][0];assert record['label']==1 and record['verification_status']=='approved'
            assert record['source']['published_at']=='2025-02-15'
            assert record['source']['url']=='https://pubmed.ncbi.nlm.nih.gov/12345678/'
        else:assert anon.get('/api/platform/snapshots/'+sid).status_code==404


def test_result_link_preserves_explicit_non95_interval(setup):
    app,cs,hs,ledger,make,monkeypatch=setup
    cid=candidate(app,kind='reported_results_candidate')
    response=cs['intake_submitter'].post('/api/intake/candidates/'+cid+'/link',
        json=body(make(),confidence_interval={'level':.99,'lower':.6,'upper':.9}),headers=hs['intake_submitter'])
    assert response.status_code==200,response.text
    assert response.json()['outcome']['outcome']['confidence_interval']=={'level':.99,'lower':.6,'upper':.9}
    assert ledger.evaluation_rows()==[]


@pytest.mark.parametrize('interval',[
    {'lower':.6,'upper':.9}, {'level':99,'lower':.6,'upper':.9},
    {'level':.95,'lower':.9,'upper':.6}, {'level':True,'lower':.6,'upper':.9},
])
def test_result_link_rejects_ambiguous_or_invalid_interval(interval):
    from pydantic import ValidationError
    from emeris_live.intake_api import Link
    with pytest.raises(ValidationError):Link(**body('snapshot',confidence_interval=interval))
