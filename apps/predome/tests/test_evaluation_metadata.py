"""Isolated API-run -> consented snapshot -> adjudication -> subgroup integration."""
from copy import deepcopy
import json
import pytest
from fastapi import FastAPI,HTTPException
from fastapi.testclient import TestClient
from emeris_live import platform_store,prediction_service as svc
from emeris_live.store import Store
from emeris_live.platform_store import PlatformStore,import_legacy_archive
from emeris_live.platform_api import router as platform_router
from emeris_live.predict_api import router as predict_router
from emeris_live.analytics import build_analytics
from emeris_live.evaluation_metadata import preview_metadata,evaluation_fields

TARGET={'endpoint':'PFS','population':'Declared trial adults','comparison':'Treatment versus placebo','success_definition':'Prespecified primary PFS analysis reaches adjusted alpha favoring treatment'}
CLASSIFY={'therapeutic_area':'Oncology','sponsor_category':'biotech'}
CONFIRM={key:True for key in ('endpoint_match','population_match','comparison_match','source_checked','prespecified_analysis','earliest_result_checked','evidence_cutoff_checked')}
HEADERS={'x-user':'owner','x-csrf':'valid'}


def request_body(asset='SYNTHETIC asset A'):
    values={'nct_id':'NCT12345678','trial_name':'Synthetic metadata integration','drug':asset,'phase':'PHASE3',
            'modality':'SMALL_MOLECULE','design':'RANDOMIZED; PARALLEL; TREATMENT','indication':'PRIVATE INDICATION',
            'population':'Declared trial adults','primary_endpoint':'PFS','comparator':'Placebo',
            'sponsor':'PRIVATE SPONSOR NAME','mechanism':'PRIVATE MECHANISM PARAGRAPH'}
    return {'profile':{'fields':{k:{'value':v,'origin':'user','confirmed':True,'source_id':'PRIVATE SOURCE ID','source_url':'https://private.example/secret'} for k,v in values.items()}},
            'target':TARGET,'data_cutoff':'2025-01-09','method':'llm','models':['deepseek-flash'],
            'allow_external_processing':True,'confirm_fields_reviewed':True,
            'evidence':[{'id':'public1','text':'Synthetic public evidence for an isolated test only.',
                         'source_url':'https://clinicaltrials.gov/study/NCT12345678','availability_date':'2025-01-08','origin':'user'}]}


@pytest.fixture
def app_state(tmp_path,monkeypatch):
    monkeypatch.setattr(platform_store,'now',lambda:'2025-01-10T12:00:00+00:00')
    monkeypatch.setattr(svc,'utcnow',lambda:'2025-01-10T12:00:00+00:00')
    monkeypatch.setattr(svc,'call_model',lambda *args:{'status':'predicted','probability':.7,'model_version':'test-model-v1','reasoning':'SYNTHETIC TEST OUTPUT'})
    store=Store(tmp_path/'isolated');app=FastAPI();app.state.store=store
    def actor(request):
        name=request.headers.get('x-user')
        if not name:raise HTTPException(401)
        if request.method!='GET' and request.headers.get('x-csrf')!='valid':raise HTTPException(403)
        return {'owner':name,'name':name}
    app.state.platform_user=actor;app.state.platform_rate_limit=lambda *args:None
    app.include_router(predict_router);app.include_router(platform_router)
    client=TestClient(app)
    return client,PlatformStore(store)


def run(client,asset='SYNTHETIC asset A'):
    body=request_body(asset)
    # Strip private field provenance dates/IDs from the actual API normalization;
    # the private field values still remain in the encrypted saved profile.
    for field in body['profile']['fields'].values():field.pop('source_id');field.pop('source_url')
    response=client.post('/api/predict/run',json=body,headers=HEADERS)
    assert response.status_code==200,response.text
    return response.json()['prediction_id']


def approve(ledger,snapshot,monkeypatch):
    monkeypatch.setattr(platform_store,'now',lambda:'2025-03-01T12:00:00+00:00')
    submitted=ledger.submit_outcome('owner',snapshot['record']['id'],{
        'trial_id':'NCT12345678',**{k:TARGET[k] for k in ('endpoint','population','comparison')},'label':1,
        'readout_date':'2025-02-10','source':{'url':'https://example.invalid/synthetic-result','published_at':'2025-02-10','sha256':'a'*64},
        'extraction_status':'complete','conflicts':[]})
    ledger.verify_outcome('independent',submitted['id'],'approved','Synthetic independent verification in isolated test.',CONFIRM)


def lock_body(**changes):
    return {'publish':True,'confirm_public':True,'publish_evaluation_metadata':True,'confirm_evaluation_metadata':True,
            'evaluation_classification':CLASSIFY}|changes


def test_structured_api_run_preview_lock_approval_and_analytics_subgroups(app_state,monkeypatch):
    client,ledger=app_state;pid=run(client)
    preview=client.get('/api/platform/predictions/'+pid+'/evaluation-metadata?therapeutic_area=Oncology&sponsor_category=biotech',headers=HEADERS)
    assert preview.status_code==200
    expected=preview.json()['metadata']
    assert expected['asset']=='SYNTHETIC asset A' and expected['phase']=='PHASE3'
    assert expected['modality']=='SMALL_MOLECULE' and expected['randomized'] is True
    assert expected['field_sources']['asset']['input_path']=='input_snapshot.profile.fields.drug.value'
    assert expected['classification_source']=='user_selected_not_medically_validated'
    assert 'PRIVATE' not in json.dumps(preview.json())
    response=client.post('/api/platform/predictions/'+pid+'/lock',json=lock_body(),headers=HEADERS)
    assert response.status_code==200,response.text
    snapshot=response.json()
    assert snapshot['record']['evaluation_metadata']==expected
    public=client.get('/api/platform/snapshots/'+snapshot['record']['id']).json()
    assert public['record']['evaluation_metadata']==expected
    assert 'PRIVATE' not in json.dumps(public)
    approve(ledger,snapshot,monkeypatch)
    rows=ledger.evaluation_rows()
    assert len(rows)==1 and rows[0]['eligible_for_prospective']
    assert rows[0]['asset']=='SYNTHETIC asset A' and rows[0]['features']['sponsor_category']=='biotech'
    assert rows[0]['evaluation_metadata_provenance']=='explicit_owner_consent_frozen_metadata'
    analytics=build_analytics(scope='prospective',model='deepseek-flash :: test-model-v1',prospective_records=rows,dashboard={'historical':[],'provenance':{}})
    assert len(analytics['records'])==1
    for field,value in (('phase','PHASE3'),('therapeutic_area','Oncology'),('modality','SMALL_MOLECULE'),('sponsor_category','biotech'),('design','RANDOMIZED; PARALLEL; TREATMENT')):
        assert analytics['records'][0]['features'][field]==value
        assert any(group['value']==value for group in analytics['subgroups'][field])
    assert analytics['summary']['asset_groups']==1
    assert 'PRIVATE' not in json.dumps(analytics)


@pytest.mark.parametrize('published',[False,True])
def test_no_extra_consent_never_exposes_saved_classification_or_asset(app_state,monkeypatch,published):
    client,ledger=app_state;pid=run(client,asset='PRIVATE NONCONSENT ASSET')
    response=client.post('/api/platform/predictions/'+pid+'/lock',json={'publish':published,'confirm_public':published},headers=HEADERS)
    assert response.status_code==200
    snapshot=response.json();original_sha=snapshot['sha256']
    assert 'evaluation_metadata' not in snapshot['record']
    if published:
        public=client.get('/api/platform/snapshots/'+snapshot['record']['id']).json()
        assert 'evaluation_metadata' not in public['record'] and 'PRIVATE' not in json.dumps(public)
    approve(ledger,snapshot,monkeypatch)
    rows=ledger.evaluation_rows(public_only=False)
    assert rows[0]['asset']=='unknown'
    assert all(rows[0][field] is None for field in ('phase','modality','design','therapeutic_area','sponsor_category'))
    assert all(value is None for value in rows[0]['features'].values())
    assert 'PRIVATE' not in json.dumps(rows)
    assert ledger.get_snapshot(snapshot['record']['id'],owner='owner')['sha256']==original_sha
    later=client.post('/api/platform/predictions/'+pid+'/lock',json=lock_body(),headers=HEADERS)
    assert later.status_code==422


def test_owner_preview_consent_gates_classification_whitelist_and_idempotence(app_state):
    client,ledger=app_state;pid=run(client);path='/api/platform/predictions/'+pid
    assert client.get(path+'/evaluation-metadata').status_code==401
    assert client.get(path+'/evaluation-metadata',headers={'x-user':'other'}).status_code==404
    assert client.get(path+'/evaluation-metadata?therapeutic_area=secret',headers=HEADERS).status_code==422
    for changes in ({'publish':False},{'confirm_public':False},{'confirm_evaluation_metadata':False},
                    {'publish_evaluation_metadata':False}, {'evaluation_classification':{'sponsor':'PRIVATE EXTRA FIELD'}},
                    {'evaluation_classification':{'therapeutic_area':'Made Up Medical Class'}}):
        assert client.post(path+'/lock',json=lock_body(**changes),headers=HEADERS).status_code==422
    first=client.post(path+'/lock',json=lock_body(),headers=HEADERS).json()
    assert client.post(path+'/lock',json=lock_body(),headers=HEADERS).json()==first
    assert client.post(path+'/lock',json=lock_body(evaluation_classification={'therapeutic_area':'Neurology','sponsor_category':'biotech'}),headers=HEADERS).status_code==422
    frozen=client.get(path+'/evaluation-metadata',headers=HEADERS).json()
    assert frozen['locked'] and frozen['metadata_already_published']
    assert frozen['frozen_metadata']==first['record']['evaluation_metadata']


def test_unknown_assets_never_become_independent_trial_assets(app_state,monkeypatch):
    client,ledger=app_state;pid=run(client,asset='Unknown')
    preview=client.get('/api/platform/predictions/'+pid+'/evaluation-metadata',headers=HEADERS).json()
    assert preview['metadata']['asset'] is None
    snapshot=client.post('/api/platform/predictions/'+pid+'/lock',json=lock_body(),headers=HEADERS).json()
    approve(ledger,snapshot,monkeypatch)
    analytics=build_analytics(scope='prospective',model='deepseek-flash :: test-model-v1',prospective_records=ledger.evaluation_rows(),dashboard={'historical':[],'provenance':{}})
    assert analytics['records'][0]['asset']=='unknown'
    assert analytics['summary']['asset_groups']==0
    assert analytics['summary']['unknown_asset_records']==1


def test_projection_is_bounded_whitelisted_and_does_not_promote_saved_classifications():
    raw={'drug':'asset','phase':['PHASE2','PHASE3'],'modality':'ANTIBODY','trial_design':{'designInfo':{'allocation':'RANDOMIZED','interventionModel':'PARALLEL','private_patient':'SECRET'}},
         'sponsor':'PRIVATE','therapeutic_area':'Oncology','sponsor_category':'large_pharma','protocol_text':'SECRET'}
    metadata=preview_metadata(raw)
    assert metadata['asset']=='asset' and metadata['phase']=='PHASE2; PHASE3'
    assert metadata['design']=='RANDOMIZED; PARALLEL'
    assert metadata['therapeutic_area']=='Unknown' and metadata['sponsor_category']=='unknown'
    assert 'SECRET' not in json.dumps(metadata) and 'PRIVATE' not in json.dumps(metadata)
    oversized=preview_metadata({'drug':'x'*201})
    assert oversized['asset'] is None and oversized['field_sources']['asset']['transformation']=='oversized_value_withheld'
    assert evaluation_fields({'published':True,'input_snapshot':raw})['asset']=='unknown'
    archived=evaluation_fields({'published':True,'provenance':'archived_record_import','input_snapshot':raw})
    assert archived['asset']=='asset' and archived['evaluation_metadata_provenance']=='archived_public_input_fields'
    assert archived['therapeutic_area']=='Oncology' and archived['sponsor_category']=='large_pharma'
    # Existing public archive labels retain their exact spelling, while new user
    # classifications are constrained to the published enum menu.
    exact=evaluation_fields({'published':True,'provenance':'archived_record_import','input_snapshot':raw|{'therapeutic_area':'oncology','sponsor_category':'industry','randomized':False,'trial_design':None}})
    assert exact['therapeutic_area']=='oncology' and exact['sponsor_category']=='industry'
    assert exact['randomized'] is False


def test_existing_public_archive_hashes_and_times_are_not_rewritten(app_state):
    client,ledger=app_state
    import_legacy_archive(ledger.store)
    before=ledger.list_public_snapshots()
    for envelope in before:
        assert 'evaluation_metadata' not in envelope['record']
        snap=ledger.get_snapshot(envelope['record']['snapshot_id'],reviewer=True)['record']
        fields=evaluation_fields(snap)
        assert fields['asset']!='unknown' and fields['phase']
    assert ledger.list_public_snapshots()==before
