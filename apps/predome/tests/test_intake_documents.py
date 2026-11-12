"""Registry document views derive from originals, never rewrite old candidates."""
import base64
import hashlib
import json

import pytest

from emeris_live import intake, trial_documents
from emeris_live.store import Store
from test_intake_api import setup


NCT='NCT01234567'
URL='https://clinicaltrials.gov/api/v2/studies/'+NCT


def study(*,results=False):
    return {'hasResults':results,'protocolSection':{
        'identificationModule':{'nctId':NCT,'briefTitle':'SYNTHETIC document intake trial'},
        'outcomesModule':{'primaryOutcomes':[{'measure':'Synthetic primary endpoint'}]},
        'ipdSharingStatementModule':{'ipdSharing':'YES','description':'A plan, not an acquired SAP.'}},
        'documentSection':{'largeDocumentModule':{'largeDocs':[{
            'filename':'Prot_SAP_000.pdf','typeAbbrev':'Prot_SAP',
            'label':'SYNTHETIC protocol and SAP','date':'2024-01-02','uploadDate':'2025-02-03',
            'hasProtocol':True,'hasSap':True,'hasIcf':False,'size':1234}]}}}


def save_legacy(store,value=None,*,blob=False,kind=None):
    value=study() if value is None else value
    raw=json.dumps(value,ensure_ascii=False,sort_keys=True).encode()
    structured={'label':None,'readout_date':None,'legacy_note':'Preserve this extraction exactly.',
                'nested_legacy':{'unchanged':[1,None,'old metadata']}}
    kind=kind or ('reported_results_candidate' if value.get('hasResults') else 'registry_update')
    cid,created=intake.save_candidate(store,NCT,kind,URL,raw,structured,
        hash_scope='source_response_bytes' if blob else None)
    assert created
    return cid,raw,structured


def stored_state(store):
    with store.connect() as db:
        return {table:[tuple(row) for row in db.execute('SELECT * FROM '+table+' ORDER BY 1')]
                for table in ('intake_candidates','intake_source_blobs','intake_jobs','intake_links','events','research_runs')}


@pytest.fixture
def source_store(tmp_path,monkeypatch):
    store=Store(tmp_path/'isolated');intake.initialize(store)
    def forbidden(*args,**kwargs):raise AssertionError('Projection must not acquire a source')
    monkeypatch.setattr(intake,'fetch',forbidden)
    monkeypatch.setattr(intake,'urlopen',forbidden)
    monkeypatch.setattr(trial_documents,'_fetch',forbidden)
    return store


def assert_projection(candidate,raw,expected):
    structured=candidate['structured']
    assert {k:v for k,v in structured.items() if k not in ('study_documents','study_documents_projection')}==expected
    assert structured['study_documents_projection']=={
        'schema_version':'predome.registry-document-projection-1',
        'status':'verified_source_bytes','source_sha256':hashlib.sha256(raw).hexdigest()}
    inventory=structured['study_documents']
    assert inventory['schema_version']=='predome.registry-documents-1'
    assert inventory['nct_id']==NCT and inventory['listing_status']=='listed'
    assert inventory['documents'][0]['filename']=='Prot_SAP_000.pdf'
    assert inventory['documents'][0]['has_protocol'] is True
    assert inventory['documents'][0]['has_sap'] is True
    assert inventory['documents'][0]['document_date']=='2024-01-02'
    assert inventory['documents'][0]['upload_date']=='2025-02-03'


@pytest.mark.parametrize('results',[False,True])
@pytest.mark.parametrize('blob',[False,True])
def test_old_raw_identical_candidate_gets_readonly_inventory_without_payload_rewrite(source_store,results,blob):
    cid,raw,old=save_legacy(source_store,study(results=results),blob=blob)
    before=stored_state(source_store)
    fresh=intake.registry_summary(study(results=results))
    assert 'study_documents' in fresh and 'study_documents_projection' not in fresh
    repeated,created=intake.save_candidate(source_store,NCT,
        'reported_results_candidate' if results else 'registry_update',URL,raw,fresh,
        hash_scope='source_response_bytes' if blob else None)
    assert repeated==cid and created is False
    detail=intake.get_candidate(source_store,cid);listed=intake.list_candidates(source_store)
    assert len(listed)==1 and 'raw_text' not in listed[0]
    assert_projection(detail,raw,old);assert_projection(listed[0],raw,old)
    downloaded,source=intake.get_source_bytes(source_store,cid)
    assert downloaded==raw and source['source']==detail['source']
    assert_projection(source,raw,old)
    # Returned nested values are fresh projections, not mutable stored records.
    detail['structured']['study_documents']['documents'][0]['filename']='changed-only-in-caller.pdf'
    assert_projection(intake.get_candidate(source_store,cid),raw,old)
    assert stored_state(source_store)==before
    payload=source_store.unpack(before['intake_candidates'][0][-1])
    assert payload['structured']==old and 'study_documents' not in payload['structured']
    assert payload['source']==source['source']


def test_real_intake_saves_inventory_but_never_stores_projection_or_acquires_pdf(source_store):
    raw=json.dumps(study()).encode();calls=[]
    def fetcher(url):
        calls.append(url)
        if url==URL:return raw
        assert url.startswith('https://www.ebi.ac.uk/')
        return b'{"resultList":{"result":[]}}'
    report=intake.run_intake(source_store,[NCT],fetcher,discover=False,official=False)
    assert report['new_candidates']==1 and len(calls)==2
    assert all('cdn.clinicaltrials.gov' not in url for url in calls)
    candidate=intake.list_candidates(source_store)[0]
    assert candidate['structured']['study_documents']['documents'][0]['has_sap'] is True
    assert candidate['structured']['label'] is candidate['structured']['readout_date'] is None
    assert candidate['structured']['study_documents_projection']['status']=='verified_source_bytes'
    with source_store.connect() as db:
        payload=source_store.unpack(db.execute('SELECT payload FROM intake_candidates').fetchone()['payload'])
    assert 'study_documents' in payload['structured']
    assert 'study_documents_projection' not in payload['structured']
    source_before=stored_state(source_store)['intake_candidates']
    repeated=intake.run_intake(source_store,[NCT],fetcher,discover=False,official=False)
    assert repeated['new_candidates']==0 and stored_state(source_store)['intake_candidates']==source_before


@pytest.mark.parametrize('fault',['raw_sha','row_sha','nct','missing_original','corrupt_blob','source_url'])
def test_unavailable_or_wrong_original_is_unknown_not_an_empty_document_listing(source_store,fault):
    value=study()
    if fault=='nct':value['protocolSection']['identificationModule']['nctId']='NCT87654321'
    cid,_,old=save_legacy(source_store,value,blob=True)
    with source_store.connect() as db:
        row=db.execute('SELECT * FROM intake_candidates WHERE id=?',(cid,)).fetchone()
        payload=source_store.unpack(row['payload'])
        if fault=='raw_sha':
            replacement=b'{"changed":"not the original response"}'
            db.execute('UPDATE intake_source_blobs SET payload=? WHERE sha256=?',
                (source_store.pack({'bytes_b64':base64.b64encode(replacement).decode()}),row['sha256']))
        elif fault=='row_sha':
            db.execute('UPDATE intake_candidates SET sha256=? WHERE id=?',('e'*64,cid))
        elif fault=='missing_original':
            db.execute('DELETE FROM intake_source_blobs WHERE sha256=?',(row['sha256'],))
            payload.pop('raw_text');db.execute('UPDATE intake_candidates SET payload=? WHERE id=?',(source_store.pack(payload),cid))
        elif fault=='corrupt_blob':
            db.execute('UPDATE intake_source_blobs SET payload=? WHERE sha256=?',
                (source_store.pack({'bytes_b64':'not base64'}),row['sha256']))
        elif fault=='source_url':
            payload['source']['url']='https://unrelated.example/'+NCT
            db.execute('UPDATE intake_candidates SET payload=? WHERE id=?',(source_store.pack(payload),cid))
    before=stored_state(source_store)
    for candidate in (intake.get_candidate(source_store,cid),intake.list_candidates(source_store)[0]):
        assert candidate['structured']['study_documents'] is None
        assert candidate['structured']['study_documents_projection']['status']=='source_unavailable'
        assert {k:v for k,v in candidate['structured'].items() if k not in ('study_documents','study_documents_projection')}==old
    assert stored_state(source_store)==before


def test_verified_registry_without_listing_is_distinct_from_missing_source(source_store):
    value=study();value.pop('documentSection')
    cid,_,_=save_legacy(source_store,value)
    candidate=intake.get_candidate(source_store,cid)
    assert candidate['structured']['study_documents_projection']['status']=='verified_source_bytes'
    inventory=candidate['structured']['study_documents']
    assert inventory['listing_status']=='not_listed_in_snapshot' and inventory['documents']==[]
    assert inventory['no_sap'] is None


def test_existing_trial_fragment_source_key_retains_verified_projection(source_store):
    raw=json.dumps(study()).encode();old={'legacy_note':'Existing source-key binding'}
    cid,_=intake.save_candidate(source_store,NCT,'registry_update',URL,raw,old,
        source_key=URL+'#predome-trial='+NCT,hash_scope='source_response_bytes')
    before=stored_state(source_store)
    assert_projection(intake.get_candidate(source_store,cid),raw,old)
    assert intake.get_source_bytes(source_store,cid)[0]==raw
    assert stored_state(source_store)==before


def test_invalid_listing_is_exposed_as_unreadable_with_verified_original(source_store):
    value=study();value['documentSection']['largeDocumentModule']['largeDocs']=[None]
    cid,_,_=save_legacy(source_store,value)
    candidate=intake.get_candidate(source_store,cid)
    assert candidate['structured']['study_documents_projection']['status']=='verified_source_bytes'
    assert candidate['structured']['study_documents']['listing_status']=='unreadable_listing'
    assert len(candidate['structured']['study_documents']['documents'])==1


@pytest.mark.parametrize('valid_source',[True,False])
def test_stored_inventory_and_projection_never_replace_original_byte_verification(source_store,valid_source):
    cid,_,_=save_legacy(source_store)
    with source_store.connect() as db:
        row=db.execute('SELECT payload FROM intake_candidates WHERE id=?',(cid,)).fetchone()
        payload=source_store.unpack(row['payload'])
        payload['structured']['study_documents']={'documents':[{'filename':'FORGED-STORED-SAP.pdf'}]}
        payload['structured']['study_documents_projection']={'status':'verified_source_bytes'}
        if not valid_source:payload.pop('raw_text')
        db.execute('UPDATE intake_candidates SET payload=? WHERE id=?',(source_store.pack(payload),cid))
    before=stored_state(source_store);candidate=intake.get_candidate(source_store,cid)
    assert 'FORGED-STORED-SAP' not in json.dumps(candidate)
    assert candidate['structured']['study_documents_projection']['status']==(
        'verified_source_bytes' if valid_source else 'source_unavailable')
    assert (candidate['structured']['study_documents'] is None) is (not valid_source)
    assert stored_state(source_store)==before


@pytest.mark.parametrize('kind',['official_announcement_candidate','pubmed_literature_candidate','discovery_requires_readout_source'])
def test_nonregistry_candidates_retain_their_previous_shape(source_store,kind):
    cid,raw,old=save_legacy(source_store,kind=kind,blob=True)
    before=stored_state(source_store)
    detail=intake.get_candidate(source_store,cid);listed=intake.list_candidates(source_store)[0]
    assert detail['structured']==listed['structured']==old
    assert 'study_documents_projection' not in detail and 'study_documents_projection' not in detail['structured']
    downloaded,source=intake.get_source_bytes(source_store,cid)
    assert downloaded==raw and source==detail
    assert {k:v for k,v in detail.items() if k!='raw_text'}==listed
    assert stored_state(source_store)==before


def test_existing_authenticated_intake_api_shows_derived_fields_and_downloads_original(setup):
    app,clients,_,ledger,make_snapshot,_=setup;store=app.state.store
    snapshot_id=make_snapshot()
    with store.connect() as db:owner=db.execute('SELECT id FROM users WHERE name=?',('intake_owner',)).fetchone()['id']
    frozen=ledger.get_snapshot(snapshot_id,owner=owner)
    cid,raw,old=save_legacy(store)
    before=stored_state(store)
    response=clients['intake_submitter'].get('/api/intake/candidates')
    assert response.status_code==200
    candidate=next(item for item in response.json()['records'] if item['id']==cid)
    assert_projection(candidate,raw,old)
    downloaded=clients['intake_submitter'].get('/api/intake/candidates/'+cid+'/source')
    assert downloaded.status_code==200 and downloaded.content==raw
    assert stored_state(store)==before
    assert ledger.get_snapshot(snapshot_id,owner=owner)==frozen
