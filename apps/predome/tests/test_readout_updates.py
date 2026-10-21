import hashlib
import json
import sqlite3
import pytest
from fastapi import FastAPI,HTTPException
from fastapi.testclient import TestClient
from emeris_live.store import Store
from emeris_live import intake,readout_updates
from emeris_live.readout_updates import ReadoutUpdateStore,approved_public_updates,CONFIRMATIONS
from emeris_live.readout_updates_api import router

QUOTE='The NCT01234567 trial primary readout is expected in the first quarter of 2027.'
CONFIRM={key:True for key in CONFIRMATIONS}

def real_pdf(text=QUOTE):
    from io import BytesIO
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject,NameObject,DecodedStreamObject
    writer=PdfWriter();page=writer.add_blank_page(width=700,height=700)
    font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
    page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
    stream=DecodedStreamObject();stream.set_data(('BT /F1 10 Tf 10 600 Td ('+text+') Tj ET').encode())
    page[NameObject('/Contents')]=writer._add_object(stream)
    raw=BytesIO();writer.write(raw);return raw.getvalue()

@pytest.fixture
def records(tmp_path,monkeypatch):
    monkeypatch.setattr(intake,'now',lambda:'2026-10-20T12:00:00+00:00')
    monkeypatch.setattr(readout_updates,'now',lambda:'2026-10-21T12:00:00+00:00')
    store=Store(tmp_path/'data');intake.initialize(store)
    cid,_=intake.save_candidate(store,'NCT01234567','official_announcement_candidate','https://sponsor.example/announcement',QUOTE.encode(),
        {'text':QUOTE,'published_at':'2026-10-19','content_type':'text/html','trial_identity_match':['nct']})
    data={'nct_id':'NCT01234567','candidate_id':cid,'date_kind':'estimated_window','start':'2027-01-01','end':'2027-03-31',
          'precision':'quarter','label':'2027 Q1 · estimated','quoted_evidence':QUOTE,'reason':'Explicit sponsor readout guidance, independently check.'}
    return store,ReadoutUpdateStore(store),data


def test_pending_rejected_do_not_affect_calendar_and_approved_is_redacted(records):
    store,ledger,data=records
    pending=ledger.propose('reviewer-a',data)
    assert not approved_public_updates(store)
    assert not pending['can_review']
    assert ledger.get(pending['id'],'reviewer-b')['can_review']
    with pytest.raises(ValueError,match='own update'):
        ledger.review('reviewer-a',pending['id'],'approved','I reviewed the source myself.',CONFIRM)
    ledger.review('reviewer-b',pending['id'],'rejected','Source requires more precise date confirmation.')
    assert not approved_public_updates(store)
    proposal=ledger.propose('reviewer-a',data)
    ledger.review('reviewer-b',proposal['id'],'approved','Source identity and readout wording were checked.',CONFIRM)
    result=approved_public_updates(store)['NCT01234567']
    assert result['start']=='2027-01-01' and result['precision']=='quarter'
    assert result['source_sha256']==hashlib.sha256(QUOTE.encode()).hexdigest()
    assert result['provenance']['original_prediction_unchanged'] is True
    assert 'reviewer-a' not in json.dumps(result) and 'reviewer-b' not in json.dumps(result)
    assert 'reason' not in result and 'reason' not in result['provenance']
    with store.connect() as db:
        assert b'Explicit sponsor' not in db.execute('SELECT payload FROM readout_update_proposals LIMIT 1').fetchone()[0]
        for table in ('readout_update_proposals','readout_update_decisions'):
            with pytest.raises(sqlite3.IntegrityError,match='append-only'):db.execute(f'DELETE FROM {table}')
    with pytest.raises(ValueError,match='immutable'):
        ledger.review('reviewer-c',proposal['id'],'rejected','Cannot override a previous final decision.')


@pytest.mark.parametrize('changes',[
    {'source_sha256':'a'*64}, {'published_at':'2026-10-01'},
    {'date_kind':'completion_date'}, {'start':'2027-01-02'}, {'end':'2027-04-01'},
    {'date_kind':'official_exact_date'}, {'quoted_evidence':'This invented statement is not in the captured source.'},
    {'nct_id':'NCT99999999'}, {'start':'2027-02-31'}, {'precision':'day'},
])
def test_invalid_or_fabricated_proposals_rejected(records,changes):
    store,ledger,data=records
    with pytest.raises((ValueError,KeyError)):ledger.propose('reviewer-a',data|changes)
    assert approved_public_updates(store)=={}


def test_all_independent_confirmations_required(records):
    store,ledger,data=records;proposal=ledger.propose('reviewer-a',data)
    for key in CONFIRM:
        with pytest.raises(ValueError,match='confirmations'):
            ledger.review('reviewer-b',proposal['id'],'approved','Independent review of source is complete.',CONFIRM|{key:False})
    assert not approved_public_updates(store)


def test_newer_approved_update_supersedes_without_rewriting_previous(records,monkeypatch):
    store,ledger,data=records;first=ledger.propose('reviewer-a',data)
    ledger.review('reviewer-b',first['id'],'approved','Independent readout confirmation complete.',CONFIRM)
    original=ledger.get(first['id'])
    newquote='The NCT01234567 primary results will be disclosed on April 12, 2027.'
    cid,_=intake.save_candidate(store,data['nct_id'],'official_announcement_candidate','https://sponsor.example/new',newquote.encode(),
        {'text':newquote,'published_at':'2026-10-20','trial_identity_match':['trial_alias'],'content_type':'text/plain'})
    monkeypatch.setattr(readout_updates,'now',lambda:'2026-10-22T12:00:00+00:00')
    second=ledger.propose('reviewer-a',data|{'candidate_id':cid,'quoted_evidence':newquote,'date_kind':'official_exact_date','start':'2027-04-12','end':'2027-04-12','precision':'day','label':'April 12 · official'})
    assert approved_public_updates(store)[data['nct_id']]['start']=='2027-01-01'
    ledger.review('reviewer-b',second['id'],'approved','Independent exact readout date confirmation.',CONFIRM)
    assert approved_public_updates(store)[data['nct_id']]['start']=='2027-04-12'
    assert ledger.get(first['id'])==original


def test_source_changed_after_proposal_cannot_be_approved(records):
    store,ledger,data=records;proposal=ledger.propose('reviewer-a',data)
    with store.connect() as db:
        row=db.execute('SELECT payload FROM intake_candidates WHERE id=?',(data['candidate_id'],)).fetchone()
        payload=store.unpack(row['payload']);payload['structured']['text']+=' Extra source text.'
        db.execute('UPDATE intake_candidates SET payload=? WHERE id=?',(store.pack(payload),data['candidate_id']))
    with pytest.raises(ValueError,match='changed after proposal'):
        ledger.review('reviewer-b',proposal['id'],'approved','Independent review of source complete.',CONFIRM)


def test_pdf_source_hash_is_original_response_and_text_hash_is_separate(records):
    store,ledger,data=records
    raw=real_pdf()
    cid,_=intake.save_candidate(store,data['nct_id'],'official_announcement_candidate','https://sponsor.example/slides.pdf',raw,
        {'text':QUOTE,'published_at':'2026-10-19','content_type':'application/pdf','trial_identity_match':['nct']},hash_scope='source_response_bytes')
    proposal=ledger.propose('reviewer-a',data|{'candidate_id':cid})
    src=proposal['proposal']['source']
    assert src['sha256']==hashlib.sha256(raw).hexdigest()
    from emeris_live.official_intake import extract
    assert src['quote_text_sha256']==hashlib.sha256(extract(raw)['text'].encode()).hexdigest()
    assert src['sha256']!=src['quote_text_sha256']
    assert src['hash_scope']=='source_response_bytes'
    ledger.review('reviewer-b',proposal['id'],'approved','PDF text and original source reviewed.',CONFIRM)


def test_drug_name_only_candidate_and_missing_publication_date_rejected(records):
    store,ledger,data=records
    for structured in ({'text':'Only testdrug is expected to have results in first quarter 2027.','trial_identity_match':['drug_name'],'published_at':'2026-10-19'}, {'text':QUOTE,'trial_identity_match':['nct']}):
        cid,_=intake.save_candidate(store,data['nct_id'],'official_announcement_candidate','https://sponsor.example/'+str(len(structured))+str(structured.get('published_at')),structured['text'].encode(),structured)
        with pytest.raises(ValueError):ledger.propose('reviewer-a',data|{'candidate_id':cid,'quoted_evidence':structured['text']})


def test_registry_completion_does_not_automatically_become_readout(records):
    store,ledger,data=records
    raw={'protocolSection':{'statusModule':{'lastUpdatePostDateStruct':{'date':'2026-10-19'},'primaryCompletionDateStruct':{'date':'2027-03-31'}}}}
    cid,_=intake.save_candidate(store,data['nct_id'],'registry_update','https://clinicaltrials.gov/api/v2/studies/'+data['nct_id'],json.dumps(raw).encode(),intake.registry_summary(raw))
    with pytest.raises(ValueError,match='Quoted'):
        ledger.propose('reviewer-a',data|{'candidate_id':cid})
    assert approved_public_updates(store)=={}


def test_api_roles_csrf_delegation_paging_and_forbidden_source_fields(records,monkeypatch):
    store,ledger,data=records
    monkeypatch.setenv('EMERIS_REVIEWERS','a,b')
    app=FastAPI();app.state.store=store
    def actor(request):
        name=request.headers.get('x-user')
        if not name:raise HTTPException(401)
        if request.method!='GET' and request.headers.get('x-csrf')!='valid':raise HTTPException(403)
        return {'owner':name,'name':name}
    app.state.platform_user=actor;app.include_router(router);client=TestClient(app)
    assert client.get('/api/readout-updates').status_code==401
    assert client.get('/api/readout-updates',headers={'x-user':'ordinary'}).status_code==403
    assert client.post('/api/readout-updates',json=data,headers={'x-user':'a'}).status_code==403
    headers={'x-user':'a','x-csrf':'valid'}
    assert client.post('/api/readout-updates',json=data|{'source_sha256':'a'*64},headers=headers).status_code==422
    first=client.post('/api/readout-updates',json=data,headers=headers).json()
    client.post('/api/readout-updates',json=data,headers=headers)
    page=client.get('/api/readout-updates?limit=1',headers={'x-user':'b'}).json()
    assert len(page['records'])==1 and page['pagination']['next_offset']==1
    body={'decision':'approved','reason':'Independent readout statement checked.','confirmations':CONFIRM}
    assert client.post('/api/readout-updates/'+first['id']+'/review',json=body,headers=headers).status_code==422
    assert client.post('/api/readout-updates/'+first['id']+'/review',json=body,headers={'x-user':'b','x-csrf':'valid'}).status_code==200


def test_pubmed_quote_uses_matched_source_publication_date_not_readout_date(records):
    store,ledger,data=records
    article={'id':'12345','title':'Trial design report','firstPublicationDate':'2026-10-18','abstractText':QUOTE}
    raw=json.dumps({'resultList':{'result':[article]}}).encode()
    cid,_=intake.save_candidate(store,data['nct_id'],'pubmed_literature_candidate','https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=NCT01234567',raw,{'articles':[article]})
    proposal=ledger.propose('reviewer-a',data|{'candidate_id':cid})
    assert proposal['proposal']['source']['published_at']=='2026-10-18'
    assert proposal['proposal']['start']=='2027-01-01'
    assert proposal['proposal']['source']['publication_date_basis']=='matched_article_publication_date_not_readout_date'
    assert not approved_public_updates(store)


def test_forged_candidate_hash_or_reviewed_record_tamper_fails_closed(records):
    store,ledger,data=records
    with store.connect() as db:
        row=db.execute('SELECT payload FROM intake_candidates WHERE id=?',(data['candidate_id'],)).fetchone()
        original=store.unpack(row['payload']);forged=json.loads(json.dumps(original))
        forged['source']['sha256']='f'*64
        db.execute('UPDATE intake_candidates SET payload=? WHERE id=?',(store.pack(forged),data['candidate_id']))
    with pytest.raises(ValueError,match='server intake'):
        ledger.propose('reviewer-a',data)
    with store.connect() as db:
        db.execute('UPDATE intake_candidates SET payload=? WHERE id=?',(store.pack(original),data['candidate_id']))
    proposal=ledger.propose('reviewer-a',data)
    ledger.review('reviewer-b',proposal['id'],'approved','Independent verification against captured official source.',CONFIRM)
    with store.connect() as db:
        db.execute('DROP TRIGGER readout_update_proposals_no_update')
        altered=dict(proposal['proposal']);altered['start']='2028-01-01'
        db.execute('UPDATE readout_update_proposals SET payload=? WHERE id=?',(store.pack(altered),proposal['id']))
    with pytest.raises(ValueError,match='integrity'):
        approved_public_updates(store)


def test_official_per_trial_source_key_uses_real_url_and_rechecks_binary_blob(records):
    store,ledger,data=records
    raw=real_pdf()
    url='https://sponsor.example/shared-guidance.pdf'
    cid,_=intake.save_candidate(store,data['nct_id'],'official_announcement_candidate',url,raw,
        {'text':QUOTE,'published_at':'2026-10-19','content_type':'application/pdf','trial_identity_match':['trial_alias']},
        source_key=url+'#predome-trial='+data['nct_id'],hash_scope='source_response_bytes')
    proposal=ledger.propose('reviewer-a',data|{'candidate_id':cid})
    assert proposal['proposal']['source']['url']==url
    with store.connect() as db:
        db.execute('UPDATE intake_source_blobs SET payload=? WHERE sha256=?',(store.pack({'bytes_b64':'dGFtcGVyZWQ='}),hashlib.sha256(raw).hexdigest()))
    with pytest.raises(ValueError,match='integrity'):
        ledger.review('reviewer-b',proposal['id'],'approved','Original PDF and extracted guidance reviewed.',CONFIRM)


def test_invented_structured_quote_cannot_pass_original_byte_verification(records):
    store,ledger,data=records
    fake='NCT01234567 primary results will be published on December 1, 2027.'
    with store.connect() as db:
        row=db.execute('SELECT payload FROM intake_candidates WHERE id=?',(data['candidate_id'],)).fetchone()
        source=store.unpack(row['payload']);source['structured']['text']=fake
        db.execute('UPDATE intake_candidates SET payload=? WHERE id=?',(store.pack(source),data['candidate_id']))
    with pytest.raises(ValueError,match='original source bytes'):
        ledger.propose('reviewer-a',data|{'quoted_evidence':fake})
    assert approved_public_updates(store)=={}


@pytest.mark.parametrize('text',['NCT012345678 primary readout is expected in the first quarter of 2027.',
                                'FAKE-ALIAS with fakedrug will have results in the first quarter of 2027.'])
def test_mutable_identity_flags_and_partial_nct_cannot_authorize_quote(records,text):
    store,ledger,data=records
    cid,_=intake.save_candidate(store,data['nct_id'],'official_announcement_candidate','https://sponsor.example/fake-alias',text.encode(),
       {'text':text,'published_at':'2026-10-19','trial_identity_match':['nct','trial_alias'],'trial_name':'FAKE-ALIAS','drug':'fakedrug'})
    with pytest.raises(ValueError,match='exact NCT'):
        ledger.propose('reviewer-a',data|{'candidate_id':cid,'quoted_evidence':text})


def test_valid_configured_alias_and_drug_are_verified_in_actual_quote(records,monkeypatch):
    from emeris_live import official_intake
    store,ledger,data=records
    text='STUDY-1 with testdrug primary readout is expected in the first quarter of 2027.'
    monkeypatch.setattr(official_intake,'tracked_config',lambda:[{'nct_id':data['nct_id'],'trial':'STUDY-1','drug':'testdrug'}])
    cid,_=intake.save_candidate(store,data['nct_id'],'official_announcement_candidate','https://sponsor.example/alias',text.encode(),
       {'text':text,'published_at':'2026-10-19','trial_identity_match':[],'trial_name':'untrusted-alias','drug':'untrusted-drug'})
    proposal=ledger.propose('reviewer-a',data|{'candidate_id':cid,'quoted_evidence':text})
    ledger.review('reviewer-b',proposal['id'],'approved','Actual configured trial and drug quote checked.',CONFIRM)
    assert approved_public_updates(store)[data['nct_id']]['quoted_evidence']==text


def test_html_metadata_publication_date_verified_from_raw(records):
    from emeris_live.official_intake import extract
    store,ledger,data=records
    raw=('<html><head><meta name="date" content="2026-10-19"></head><body>'+QUOTE+'</body></html>').encode()
    doc=extract(raw)
    cid,_=intake.save_candidate(store,data['nct_id'],'official_announcement_candidate','https://sponsor.example/html',raw,doc)
    proposal=ledger.propose('reviewer-a',data|{'candidate_id':cid})
    assert proposal['proposal']['source']['publication_date_basis']=='document_metadata'
    with store.connect() as db:
        row=db.execute('SELECT payload FROM intake_candidates WHERE id=?',(cid,)).fetchone()
        source=store.unpack(row['payload']);source['structured']['published_at']='2026-10-18'
        db.execute('UPDATE intake_candidates SET payload=? WHERE id=?',(store.pack(source),cid))
    with pytest.raises(ValueError,match='publication date'):
        ledger.review('reviewer-b',proposal['id'],'approved','Check captured HTML original metadata.',CONFIRM)


def test_candidate_kind_cannot_be_changed_to_skip_official_raw_validation(records):
    store,ledger,data=records
    with store.connect() as db:
        row=db.execute('SELECT payload FROM intake_candidates WHERE id=?',(data['candidate_id'],)).fetchone()
        source=store.unpack(row['payload']);source['kind']='pubmed_literature_candidate'
        db.execute('UPDATE intake_candidates SET payload=? WHERE id=?',(store.pack(source),data['candidate_id']))
    with pytest.raises(ValueError,match='server intake record'):
        ledger.propose('reviewer-a',data)


def test_previously_approved_record_is_not_rewritten_by_later_intake_mutation(records):
    store,ledger,data=records
    proposal=ledger.propose('reviewer-a',data)
    ledger.review('reviewer-b',proposal['id'],'approved','Verified original source readout date.',CONFIRM)
    before=approved_public_updates(store)
    with store.connect() as db:
        row=db.execute('SELECT payload FROM intake_candidates WHERE id=?',(data['candidate_id'],)).fetchone()
        source=store.unpack(row['payload']);source['structured']['text']='An unrelated later mutated source paragraph.'
        db.execute('UPDATE intake_candidates SET payload=? WHERE id=?',(store.pack(source),data['candidate_id']))
    assert approved_public_updates(store)==before
    with pytest.raises(ValueError,match='changed after proposal'):
        ledger.propose('reviewer-c',data)
