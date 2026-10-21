"""Synthetic providers, real report/lock/product flow; no clinical-result claims."""
from copy import deepcopy
import hashlib
import json
import sqlite3
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from emeris_live import platform_store, prediction_service as svc
from emeris_live.platform_store import PlatformStore, digest, import_legacy_archive, canonical
from emeris_live.store import Store
from emeris_live.platform_api import router as platform_router
from emeris_live.predict_api import router as predict_router
from emeris_live.product import trial_detail
from emeris_live.analytics import build_analytics
from emeris_live.prediction_ensemble import MEMBERS

TARGET={'endpoint':'Synthetic PFS','population':'Synthetic ITT adults','comparison':'Synthetic drug versus placebo','success_definition':'Prespecified adjusted primary PFS superiority'}
HEADERS={'x-user':'owner','x-csrf':'valid'}
CONFIRM={k:True for k in ('endpoint_match','population_match','comparison_match','source_checked','prespecified_analysis','earliest_result_checked','evidence_cutoff_checked')}


def body():
    values={'nct_id':'NCT12345678','drug':'PRIVATE synthetic drug','indication':'PRIVATE indication','population':'Synthetic ITT adults','primary_endpoint':'Synthetic PFS','comparator':'placebo'}
    return {'profile':{'fields':{k:{'value':v,'origin':'user','confirmed':True} for k,v in values.items()}},
            'target':TARGET,'method':'llm','data_cutoff':'2025-01-09','models':list(MEMBERS),
            'allow_external_processing':True,'confirm_fields_reviewed':True,
            'evidence':[{'id':'source1','text':'PRIVATE synthetic evidence text','source_url':'https://example.invalid/evidence','availability_date':'2025-01-08','origin':'user'}]}


def provider(model,req):
    packet=svc.canonical_packet(req)
    return {'status':'predicted','probability':.2 if model==MEMBERS[0] else .8,'model_version':model+'-test','returned_model':model+'-provider-alias',
            'target_definition':req.target.success_definition,'reasoning':'PRIVATE synthetic provider reasoning',
            'evidence':{'text':'PRIVATE nested component text'},'input_sha256':svc.digest(packet),
            'request_sha256':svc.digest(svc.model_request(model,packet)),
            'prompt_sha256':hashlib.sha256(svc.SYSTEM_PROMPT.encode()).hexdigest()}


@pytest.fixture
def system(tmp_path,monkeypatch):
    monkeypatch.setattr(platform_store,'now',lambda:'2025-01-10T12:00:00+00:00')
    monkeypatch.setattr(svc,'utcnow',lambda:'2025-01-10T12:00:00+00:00')
    monkeypatch.setattr(svc,'call_model',provider)
    store=Store(tmp_path/'isolated');app=FastAPI();app.state.store=store
    def actor(request):
        name=request.headers.get('x-user')
        if not name:raise HTTPException(401)
        if request.method!='GET' and request.headers.get('x-csrf')!='valid':raise HTTPException(403)
        return {'owner':name,'name':name}
    app.state.platform_user=actor;app.state.platform_rate_limit=lambda *args:None
    app.include_router(predict_router);app.include_router(platform_router)
    @app.get('/api/product/trials/{nct}')
    def detail(nct:str):return trial_detail(nct,store)
    return TestClient(app),PlatformStore(store)


def api_run(client,changes=None):
    response=client.post('/api/predict/run',json=body()|(changes or {}),headers=HEADERS)
    assert response.status_code==200,response.text
    return response.json()


def save(ledger,report):
    return ledger.create_run('owner',{'nct_id':'NCT12345678','profile':report['profile']},report,
        model_version='test',pipeline_version='synthetic',data_cutoff=report['data_cutoff'],target=report['target'])


def test_real_report_api_public_lock_snapshot_and_trial_detail(system):
    client,ledger=system;result=api_run(client);pid=result['prediction_id']
    original=result['report']['ensemble'];assert original['status']=='available' and original['probability']==.5
    response=client.post('/api/platform/predictions/'+pid+'/lock',json={'publish':True,'confirm_public':True},headers=HEADERS)
    assert response.status_code==200,response.text
    snapshot=response.json();sid=snapshot['record']['id']
    public=client.get('/api/platform/snapshots/'+sid).json()
    assert 'PRIVATE' not in json.dumps(public)
    assert public['record']['ensemble']['probability']==.5
    assert public['record']['ensemble']['generated_at_utc']<=public['record']['locked_at']
    assert set(public['record']['forecasts'])==set(MEMBERS)
    detail=client.get('/api/product/trials/NCT12345678')
    assert detail.status_code==200
    assert detail.json()['public_predictions']==[public]
    assert 'PRIVATE' not in json.dumps(detail.json())
    assert ledger.get_snapshot(sid,owner='owner')['record']['outputs']['ensemble']==original
    assert client.post('/api/platform/predictions/'+pid+'/lock',json={'publish':True,'confirm_public':True},headers=HEADERS).json()==snapshot
    # Future prompt changes cannot rewrite or invalidate the frozen public view.
    old_prompt=svc.SYSTEM_PROMPT
    try:
        svc.SYSTEM_PROMPT='A later prompt version'
        assert ledger.get_snapshot(sid)==public
    finally:svc.SYSTEM_PROMPT=old_prompt


def test_nested_private_fields_stripped_and_snapshot_immutable(system):
    _,ledger=system;report=svc.create_report(svc.PredictionRequest.model_validate(body()))
    report['ensemble'].update(reason='PRIVATE arbitrary text',reasoning={'text':'PRIVATE extra'},evidence=['PRIVATE'])
    for member in report['ensemble']['members']:member.update(reasoning='PRIVATE member',nested={'text':'PRIVATE'})
    run=save(ledger,report);snapshot=ledger.lock('owner',run['id'],publish=True)
    public=ledger.get_snapshot(snapshot['record']['id'])
    assert 'PRIVATE' not in json.dumps(public)
    assert snapshot['record']['outputs']['ensemble']==report['ensemble']
    assert public['record']['ensemble']['reason']!=report['ensemble']['reason']
    report['ensemble']['probability']=.99
    assert ledger.get_snapshot(snapshot['record']['id'],owner='owner')==snapshot
    with ledger.store.connect() as db:
        with pytest.raises(sqlite3.IntegrityError,match='append-only'):
            db.execute('UPDATE platform_prediction_snapshots SET payload=?',(b'changed',))


def test_private_ensemble_stays_private(system):
    client,ledger=system;pid=api_run(client)['prediction_id'];snap=ledger.lock('owner',pid)
    assert ledger.get_snapshot(snap['record']['id'],owner='owner')==snap
    with pytest.raises(KeyError):ledger.get_snapshot(snap['record']['id'])
    assert ledger.list_public_snapshots()==[]
    assert ledger.evaluation_rows()==[]


@pytest.mark.parametrize('field,value',[('probability',.9),('target_sha256','0'*64),('input_sha256','0'*64),('policy_sha256','0'*64),('generated_at_utc','2025-01-11T00:00:00Z')])
def test_inconsistent_ensemble_rejected_before_persistence(system,field,value):
    _,ledger=system;report=svc.create_report(svc.PredictionRequest.model_validate(body()))
    report['ensemble'][field]=value
    with pytest.raises(ValueError,match='Ensemble'):save(ledger,report)
    assert ledger.list_runs('owner')==[]


def test_component_mutation_and_saved_profile_mismatch_rejected(system):
    _,ledger=system;report=svc.create_report(svc.PredictionRequest.model_validate(body()))
    report['forecasts'][MEMBERS[0]]['probability']=.7
    with pytest.raises(ValueError,match='Ensemble'):save(ledger,report)
    report=svc.create_report(svc.PredictionRequest.model_validate(body()))
    with pytest.raises(ValueError,match='profile'):
        ledger.create_run('owner',{'nct_id':'NCT12345678','profile':{}},report,model_version='test',pipeline_version='test',data_cutoff=report['data_cutoff'],target=TARGET)


def test_ensemble_never_adds_performance_record_or_changes_probabilities(system,monkeypatch):
    client,ledger=system;pid=api_run(client)['prediction_id'];snap=ledger.lock('owner',pid,publish=True)
    monkeypatch.setattr(platform_store,'now',lambda:'2025-03-01T00:00:00+00:00')
    submitted=ledger.submit_outcome('owner',snap['record']['id'],{'trial_id':'NCT12345678',**{k:TARGET[k] for k in ('endpoint','population','comparison')},'label':1,'readout_date':'2025-02-10','source':{'url':'https://example.invalid/result','published_at':'2025-02-10','sha256':'a'*64},'extraction_status':'complete','conflicts':[]})
    ledger.verify_outcome('independent',submitted['id'],'approved','Synthetic source independently checked',CONFIRM)
    rows=ledger.evaluation_rows();assert len(rows)==2
    assert {row['model']:row['probability'] for row in rows}=={MEMBERS[0]:.2,MEMBERS[1]:.8}
    for row in rows:
        result=build_analytics(scope='prospective',model=row['model']+' :: '+row['model_version'],prospective_records=rows,dashboard={'historical':[],'provenance':{}})
        assert len(result['records'])==1
        assert result['records'][0]['probability']==row['probability']
    assert all('ensemble' not in row['model'] for row in rows)


def test_all_ten_archived_public_snapshots_exact_pre_ensemble_projection(system):
    _,ledger=system;import_legacy_archive(ledger.store)
    # Freeze the prior public field contract and compare every original
    # envelope before/after a new ensemble lock; no ensemble:null backfill.
    legacy_keys={'schema_version','id','snapshot_id','prediction_id','trial_id','status','created_at','prediction_timestamp','model_version','pipeline_version','data_cutoff','target','parent_prediction_id','model_training_data_cutoff','locked_at','timestamp_attestation','clinical_prediction_validated','forecasts','evidence_sources','provenance','archive_provenance','imported_at','input_snapshot_sha256','public_view'}
    before=ledger.list_public_snapshots();assert len(before)==10
    for envelope in before:
        assert set(envelope['record'])==legacy_keys
        full=ledger.get_snapshot(envelope['record']['id'],owner='archived-public-release')
        assert 'ensemble' not in full['record']['outputs']
        assert full['sha256']==digest(full['record'])
    frozen=canonical(before)
    run=save(ledger,svc.create_report(svc.PredictionRequest.model_validate(body())))
    ledger.lock('owner',run['id'],publish=True)
    after=[x for x in ledger.list_public_snapshots() if x['record']['provenance']=='archived_record_import']
    assert canonical(after)==frozen


def test_missing_second_model_retains_null_descriptive_average(system):
    client,ledger=system;result=api_run(client,{'models':[MEMBERS[0]]})
    assert result['report']['ensemble']['status']=='unavailable'
    snap=ledger.lock('owner',result['prediction_id'],publish=True)
    public=ledger.get_snapshot(snap['record']['id'])['record']
    assert public['ensemble']['probability'] is None
    assert public['ensemble']['model_range'] is None
    assert public['forecasts'][MEMBERS[0]]['probability']==.2
    assert len(public['forecasts'])==1


def test_later_prompt_does_not_prevent_existing_draft_lock(system,monkeypatch):
    client,ledger=system;pid=api_run(client)['prediction_id']
    monkeypatch.setattr(svc,'SYSTEM_PROMPT','Future changed prompt')
    snap=ledger.lock('owner',pid,publish=True)
    assert snap['record']['outputs']['ensemble']['probability']==.5


def test_boolean_probability_never_accepted_as_zero(system,monkeypatch):
    _,ledger=system
    def zero_provider(model,req):return provider(model,req)|{'probability':0.0}
    monkeypatch.setattr(svc,'call_model',zero_provider)
    report=svc.create_report(svc.PredictionRequest.model_validate(body()))
    report['ensemble']['probability']=False
    with pytest.raises(ValueError,match='numeric'):save(ledger,report)


def test_api_normalizes_target_whitespace_before_packet_hash_and_lock(system):
    client,ledger=system
    result=api_run(client,{'target':{key:'  '+value+'  ' for key,value in TARGET.items()}})
    assert result['report']['target']==TARGET
    assert result['report']['ensemble']['target_sha256']==digest(TARGET)
    snap=ledger.lock('owner',result['prediction_id'],publish=True)
    assert ledger.get_snapshot(snap['record']['id'])['record']['ensemble']['probability']==.5
