"""Official announcements stay pending until independent adjudication."""
import json,hashlib
from copy import deepcopy
import pytest
from fastapi import FastAPI,HTTPException
from fastapi.testclient import TestClient
from emeris_live import intake,official_intake,platform_store
from emeris_live.store import Store
from emeris_live.product import trial_detail
from emeris_live.platform_store import PlatformStore,public_result_source
from emeris_live.intake_api import router
from emeris_live.platform_api import router as platform_router

NCT='NCT12345678'
TARGET={'endpoint':'Synthetic PFS','population':'Synthetic ITT','comparison':'Synthetic drug versus placebo','success_definition':'Prespecified PFS superiority'}
QUOTE=NCT+' synthetic trial reported the prespecified primary PFS analysis in its ITT population.'
CONFIRM={k:True for k in ('endpoint_match','population_match','comparison_match','source_checked','prespecified_analysis','earliest_result_checked','evidence_cutoff_checked')}


def headers(name='submitter'):return {'x-user':name,'x-csrf':'valid'}


@pytest.fixture
def setup(tmp_path,monkeypatch):
    monkeypatch.setenv('EMERIS_REVIEWERS','owner,submitter,independent')
    monkeypatch.setattr(platform_store,'now',lambda:'2025-01-01T00:00:00+00:00')
    store=Store(tmp_path/'isolated');ledger=PlatformStore(store);intake.initialize(store)
    run=ledger.create_run('owner',{'nct_id':NCT},{'forecast_kind':'uncalibrated_llm_research','forecasts':{'flash':{'status':'predicted','probability':.6},'pro':{'status':'predicted','probability':.7}}},model_version='test',pipeline_version='test',data_cutoff='2024-12-31',target=TARGET,evidence=[{'url':'https://example.invalid/prior','availability_date':'2024-12-30'}])
    snapshot=ledger.lock('owner',run['id'],publish=True)
    monkeypatch.setattr(platform_store,'now',lambda:'2025-03-01T00:00:00+00:00')
    app=FastAPI();app.state.store=store
    def actor(request):
        name=request.headers.get('x-user')
        if not name:raise HTTPException(401)
        if request.method!='GET' and request.headers.get('x-csrf')!='valid':raise HTTPException(403)
        return {'owner':name,'name':name}
    app.state.platform_user=actor;app.include_router(router);app.include_router(platform_router)
    @app.get('/api/product/trials/{nct}')
    def detail(nct:str):return trial_detail(nct,store)
    return TestClient(app),ledger,snapshot


def candidate(ledger,quote=QUOTE,metadata=True,kind='official_announcement_candidate'):
    raw=('<html><head>'+('<meta name="date" content="2025-02-02">' if metadata else '')+'</head><body><p>'+quote+'</p><p>PRIVATE UNSELECTED SOURCE PARAGRAPH</p></body></html>').encode()
    doc=official_intake.extract(raw)
    structured=doc|{'trial_name':'SYNTHETIC-TRIAL','drug':'synthetic-agent','published_at':'2025-02-02' if metadata else '2024-07-27','publication_date_basis':'document_metadata' if metadata else 'previously_source_checked_guidance_date'}
    if kind=='reported_results_candidate':structured['results_first_post_date']={'date':'2025-02-02'}
    if kind=='pubmed_literature_candidate':structured['articles']=[{'id':'a','publication_url':'https://pubmed.ncbi.nlm.nih.gov/123/','firstPublicationDate':'2025-02-02'}]
    cid,_=intake.save_candidate(ledger.store,NCT,kind,'https://www.astrazeneca.com/media-centre/press-releases/2025/synthetic.html',raw,structured,source_key='https://www.astrazeneca.com/media-centre/press-releases/2025/synthetic.html#predome-trial='+NCT,hash_scope='source_response_bytes')
    return cid


def payload(snapshot,**changes):
    return {'snapshot_id':snapshot['record']['id'],**{k:TARGET[k] for k in ('endpoint','population','comparison')},'label':1,'readout_date':'2025-02-01','source_quote':QUOTE}|changes


def post(client,cid,body,name='submitter'):
    return client.post('/api/intake/candidates/'+cid+'/link',json=body,headers=headers(name))


def approve(client,oid,name='independent'):
    return client.post('/api/platform/reviews/'+oid,json={'decision':'approved','reason':'Synthetic official source and exact endpoint independently checked.','confirmations':CONFIRM},headers=headers(name))


def test_official_link_is_pending_then_independent_review_scores_original_two_models(setup):
    client,ledger,snapshot=setup;cid=candidate(ledger)
    response=post(client,cid,payload(snapshot));assert response.status_code==200,response.text
    result=response.json();assert result['status']=='needs_independent_review' and not result['auto_approved']
    oid=result['outcome']['id'];source=result['outcome']['outcome']['source']
    assert source['excerpt']==QUOTE and 'PRIVATE' not in json.dumps(source)
    assert source['candidate_id']==cid and source['publication_date_basis']=='document_metadata'
    assert '#' not in source['url'] and source['sha256']==intake.get_candidate(ledger.store,cid)['source']['sha256']
    assert ledger.evaluation_rows()==[]
    assert ledger.public_outcome_records()==[]
    assert client.get('/api/product/trials/'+NCT).json()['public_verified_outcomes']==[]
    assert approve(client,oid,'owner').status_code==422
    assert approve(client,oid,'submitter').status_code==422
    assert approve(client,oid).status_code==200
    rows=ledger.evaluation_rows();assert len(rows)==2
    assert {r['probability'] for r in rows}=={.6,.7}
    assert all(r['eligible_for_prospective'] for r in rows)
    assert ledger.get_snapshot(snapshot['record']['id'],owner='owner')==snapshot
    safe_source={key:source[key] for key in ('url','published_at','sha256','excerpt','excerpt_sha256','publication_date_basis')}
    assert safe_source['excerpt_sha256']==hashlib.sha256(QUOTE.encode()).hexdigest()
    assert public_result_source(source|{'private_notes':'PRIVATE NOTES','text':'PRIVATE FULL DOCUMENT','reviewer_id':'PRIVATE REVIEWER'})==safe_source
    outcomes=ledger.public_outcome_records()
    assert len(outcomes)==1 and outcomes[0]['source']==safe_source
    public_detail=client.get('/api/product/trials/'+NCT).json()
    assert len(public_detail['public_verified_outcomes'])==1
    assert public_detail['public_verified_outcomes'][0]['source']==safe_source
    assert public_detail['public_verified_outcomes'][0]['outcome_id']==oid
    assert 'PRIVATE' not in json.dumps(outcomes)
    assert 'PRIVATE' not in json.dumps(public_detail)
    assert all(key not in safe_source for key in ('candidate_id','candidate_kind','quote_validation','trial_identity_match','retrieved_at'))


def test_null_label_remains_needs_review_and_cannot_be_approved(setup):
    client,ledger,snapshot=setup;cid=candidate(ledger)
    response=post(client,cid,payload(snapshot,label=None));assert response.status_code==200
    result=response.json();assert result['outcome']['outcome']['extraction_status']=='needs_review'
    assert approve(client,result['outcome']['id']).status_code==422
    assert ledger.evaluation_rows()==[]


@pytest.mark.parametrize('changes',[{'source_quote':None},{'source_quote':'Invented unrelated excerpt with enough characters.'},{'source_publication_date':'2025-02-03'},{'source_publication_date':'2099-01-01'},{'readout_date':'2025-02-03'},{'readout_date':'not-a-date'},{'source_publication_date':'not-a-date'}])
def test_invalid_quote_or_dates_rejected(setup,changes):
    client,ledger,snapshot=setup;cid=candidate(ledger)
    assert post(client,cid,payload(snapshot,**changes)).status_code==422
    assert ledger.list_outcomes(reviewer=True)==[]


def test_guidance_fallback_is_not_a_result_publication_date(setup):
    client,ledger,snapshot=setup;cid=candidate(ledger,metadata=False)
    assert post(client,cid,payload(snapshot)).status_code==422
    response=post(client,cid,payload(snapshot,source_publication_date='2025-02-02'))
    assert response.status_code==200,response.text
    source=response.json()['outcome']['outcome']['source']
    assert source['published_at']=='2025-02-02'
    assert source['publication_date_basis']=='reviewer_provided_not_independently_verified'
    assert ledger.evaluation_rows()==[]


@pytest.mark.parametrize('quote',['Only synthetic-agent had a statistically significant result in its study.','NCT123456789 is a different identifier in a synthetic statement.'])
def test_drug_only_or_partial_nct_not_identity(setup,quote):
    client,ledger,snapshot=setup;cid=candidate(ledger,quote)
    assert post(client,cid,payload(snapshot,source_quote=quote)).status_code==422


def test_alias_and_drug_requires_server_configured_trial_identity(setup,monkeypatch):
    client,ledger,snapshot=setup;quote='SYNTHETIC-TRIAL reported synthetic-agent met its prespecified primary endpoint.'
    cid=candidate(ledger,quote)
    assert post(client,cid,payload(snapshot,source_quote=quote)).status_code==422
    monkeypatch.setattr(official_intake,'tracked_config',lambda:[{'nct_id':NCT,'trial':'SYNTHETIC-TRIAL','drug':'synthetic-agent'}])
    response=post(client,cid,payload(snapshot,source_quote=quote));assert response.status_code==200,response.text


@pytest.mark.parametrize('mutation',['structured_text','raw_blob','metadata_date','source_url','nct'])
def test_tampered_source_cannot_create_outcome(setup,mutation):
    client,ledger,snapshot=setup;cid=candidate(ledger);source=intake.get_candidate(ledger.store,cid)
    quote=QUOTE
    with ledger.store.connect() as db:
        row=db.execute('SELECT payload FROM intake_candidates WHERE id=?',(cid,)).fetchone();doc=ledger.store.unpack(row['payload'])
        if mutation=='structured_text':
            quote=NCT+' invented result paragraph not contained in the raw source bytes.';doc['structured']['text']=quote
        elif mutation=='metadata_date':doc['structured']['published_at']='2025-02-01'
        elif mutation=='source_url':doc['source']['url']='https://www.astrazeneca.com/other-source.html'
        elif mutation=='nct':doc['nct_id']='NCT99999999'
        else:db.execute('UPDATE intake_source_blobs SET payload=? WHERE sha256=?',(ledger.store.pack({'bytes_b64':'YWJj'}),source['source']['sha256']))
        db.execute('UPDATE intake_candidates SET payload=? WHERE id=?',(ledger.store.pack(doc),cid))
    assert post(client,cid,payload(snapshot,source_quote=quote)).status_code==422
    assert ledger.list_outcomes(reviewer=True)==[]


@pytest.mark.parametrize('kind',['reported_results_candidate','pubmed_literature_candidate','registry_update_candidate'])
def test_nonofficial_extra_fields_rejected_and_existing_result_routes_compatible(setup,kind):
    client,ledger,snapshot=setup;cid=candidate(ledger,kind=kind)
    assert post(client,cid,payload(snapshot)).status_code==422
    value=payload(snapshot);value.pop('source_quote')
    if kind=='pubmed_literature_candidate':value['article_id']='a'
    response=post(client,cid,value)
    assert response.status_code==(422 if kind=='registry_update_candidate' else 200),response.text


def test_auth_reviewer_and_csrf_gates(setup):
    client,ledger,snapshot=setup;cid=candidate(ledger);url='/api/intake/candidates/'+cid+'/link'
    assert client.post(url,json=payload(snapshot)).status_code==401
    assert client.post(url,json=payload(snapshot),headers=headers('ordinary')).status_code==403
    assert client.post(url,json=payload(snapshot),headers={'x-user':'submitter'}).status_code==403


def test_binary_pdf_original_source_is_reparsed_before_link(setup):
    from io import BytesIO
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject,NameObject,DecodedStreamObject
    client,ledger,snapshot=setup
    writer=PdfWriter();page=writer.add_blank_page(width=700,height=700)
    font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
    page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
    stream=DecodedStreamObject();stream.set_data(('BT /F1 10 Tf 10 600 Td ('+QUOTE+') Tj ET').encode())
    page[NameObject('/Contents')]=writer._add_object(stream)
    output=BytesIO();writer.write(output);raw=output.getvalue()
    doc=official_intake.extract(raw)
    cid,_=intake.save_candidate(ledger.store,NCT,'official_announcement_candidate','https://www.astrazeneca.com/synthetic-source.pdf',raw,doc|{'trial_name':'SYNTHETIC','drug':'agent','publication_date_basis':'unknown'},hash_scope='source_response_bytes')
    response=post(client,cid,payload(snapshot,source_publication_date='2025-02-02'))
    assert response.status_code==200,response.text
    source=response.json()['outcome']['outcome']['source']
    assert source['excerpt']==QUOTE
    assert source['sha256']==intake.get_candidate(ledger.store,cid)['source']['sha256']
    assert source['publication_date_basis']=='reviewer_provided_not_independently_verified'


def test_changing_metadata_basis_cannot_override_date_in_original_bytes(setup):
    client,ledger,snapshot=setup;cid=candidate(ledger)
    with ledger.store.connect() as db:
        row=db.execute('SELECT payload FROM intake_candidates WHERE id=?',(cid,)).fetchone()
        doc=ledger.store.unpack(row['payload']);doc['structured']['publication_date_basis']='unknown'
        db.execute('UPDATE intake_candidates SET payload=? WHERE id=?',(ledger.store.pack(doc),cid))
    assert post(client,cid,payload(snapshot,source_publication_date='2025-02-01')).status_code==422


@pytest.mark.parametrize('changes',[
    {'excerpt_sha256':'0'*64},
    {'candidate_kind':'pubmed_literature_candidate'},
    {'candidate_kind':'reported_results_candidate'},
    {'quote_validation':'unchecked'},
    {'publication_date_basis':'previously_source_checked_guidance_date'},
])
def test_public_result_source_omits_unverified_or_nonofficial_excerpt(changes):
    source={'url':'https://www.astrazeneca.com/synthetic.html','published_at':'2025-02-02','sha256':'a'*64,
        'excerpt':QUOTE,'excerpt_sha256':hashlib.sha256(QUOTE.encode()).hexdigest(),
        'candidate_kind':'official_announcement_candidate','publication_date_basis':'document_metadata',
        'quote_validation':'continuous_normalized_excerpt_reextracted_from_verified_source_bytes',
        'text':'PRIVATE full document','private_notes':'PRIVATE notes'}|changes
    assert public_result_source(source)=={key:source[key] for key in ('url','published_at','sha256')}


def test_public_result_source_keeps_explicit_unverified_date_basis_visible():
    source={'url':'https://www.astrazeneca.com/synthetic.pdf','published_at':'2025-02-02','sha256':'a'*64,
        'excerpt':QUOTE,'excerpt_sha256':hashlib.sha256(QUOTE.encode()).hexdigest(),
        'candidate_kind':'official_announcement_candidate','publication_date_basis':'reviewer_provided_not_independently_verified',
        'quote_validation':'continuous_normalized_excerpt_reextracted_from_verified_source_bytes'}
    public=public_result_source(source)
    assert public['excerpt']==QUOTE
    assert public['publication_date_basis']=='reviewer_provided_not_independently_verified'
