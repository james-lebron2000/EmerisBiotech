import json
import sqlite3
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from emeris_live.store import Store
from emeris_live.platform_store import PlatformStore, digest
from emeris_live import platform_store
from emeris_live.platform_api import router

TARGET={'endpoint':'BICR PFS in ITT','population':'ITT randomized population','comparison':'drug plus SOC versus placebo plus SOC','success_definition':'prespecified multiplicity-adjusted PFS primary analysis reaches statistical significance favoring treatment'}
CONFIRM={'endpoint_match':True,'population_match':True,'comparison_match':True,'source_checked':True,'prespecified_analysis':True,'earliest_result_checked':True,'evidence_cutoff_checked':True}

@pytest.fixture
def ledger(tmp_path,monkeypatch):
    monkeypatch.setattr(platform_store,'now',lambda:'2025-01-10T12:00:00+00:00')
    return PlatformStore(Store(tmp_path/'store'))


def create(ledger,owner='owner',**changes):
    values={'input_snapshot':{'nct_id':'NCT01234567','drug':'asset-a','phase':'PHASE3','protocol_text':'PRIVATE PROTOCOL PARAGRAPH'},
            'outputs':{'forecast_kind':'uncalibrated_llm_research','forecasts':{'test-model':{'status':'predicted','probability':0.7,'reasoning':'PRIVATE REASONING'}}},
            'model_version':'test-model-v1','pipeline_version':'test-pipeline-v1','data_cutoff':'2025-01-09T00:00:00+00:00',
            'target':TARGET,'evidence':[{'url':'https://clinicaltrials.gov/study/NCT01234567','availability_date':'2025-01-08'}]}
    values.update(changes)
    return ledger.create_run(owner,**values)


def outcome(label=1,**changes):
    result={'trial_id':'NCT01234567','endpoint':TARGET['endpoint'],'population':TARGET['population'],'comparison':TARGET['comparison'],
            'label':label,'readout_date':'2025-02-10','source':{'url':'https://sponsor.example/primary-result','published_at':'2025-02-10','sha256':'a'*64},
            'effect_size':None,'confidence_interval':None,'p_value':None,'extraction_status':'complete','conflicts':[]}
    result.update(changes)
    return result


def ready(ledger,monkeypatch,publish=True):
    run=create(ledger)
    lock=ledger.lock('owner',run['id'],publish=publish)
    monkeypatch.setattr(platform_store,'now',lambda:'2025-03-01T12:00:00+00:00')
    submitted=ledger.submit_outcome('owner',lock['record']['id'],outcome())
    return run,lock,submitted


def test_draft_inputs_encrypted_and_owner_isolated(ledger):
    run=create(ledger)
    assert ledger.get_run('owner',run['id'])['status']=='draft'
    assert ledger.list_runs('other')==[]
    with pytest.raises(KeyError):ledger.get_run('other',run['id'])
    with ledger.store.connect() as db:
        raw=db.execute('SELECT payload FROM platform_prediction_runs').fetchone()[0]
        assert b'PRIVATE PROTOCOL' not in raw


def test_lock_is_immutable_idempotent_and_private(ledger):
    run=create(ledger);first=ledger.lock('owner',run['id']);second=ledger.lock('owner',run['id'])
    assert first==second
    assert first['record']['locked_at']=='2025-01-10T12:00:00+00:00'
    assert first['sha256']==digest(first['record'])
    assert first['record']['timestamp_attestation']=='server_clock_not_independent_timestamp'
    assert ledger.list_public_snapshots()==[]
    with pytest.raises(KeyError):ledger.get_snapshot(first['record']['id'])
    with pytest.raises(ValueError,match='visibility'):ledger.lock('owner',run['id'],publish=True)
    with ledger.store.connect() as db:
        for table in ('platform_prediction_runs','platform_prediction_snapshots'):
            with pytest.raises(sqlite3.IntegrityError,match='append-only'):
                db.execute(f'DELETE FROM {table}')


def test_published_snapshot_never_exposes_private_protocol_or_reasoning(ledger):
    run=create(ledger);lock=ledger.lock('owner',run['id'],publish=True)
    public=ledger.get_snapshot(lock['record']['id'])
    assert 'PRIVATE' not in json.dumps(public)
    assert public['record']['input_snapshot_sha256']==digest(run['input_snapshot'])
    assert public['public_view_hash_scope'].startswith('sha256_is_of_full_private_snapshot')
    assert ledger.get_snapshot(lock['record']['id'],owner='owner')['record']['input_snapshot']['protocol_text'].startswith('PRIVATE')


def test_version_lineage_preserves_old_frozen_bytes(ledger):
    old=create(ledger);snap=ledger.lock('owner',old['id'])
    new=create(ledger,parent_prediction_id=old['id'],model_version='test-model-v2')
    assert new['id']!=old['id']
    assert new['parent_prediction_id']==old['id']
    assert ledger.get_snapshot(snap['record']['id'],owner='owner')==snap
    with pytest.raises(KeyError):create(ledger,owner='other',parent_prediction_id=old['id'])


def test_cutoff_and_probability_validation(ledger):
    with pytest.raises(ValueError,match='after prediction cutoff'):
        create(ledger,evidence=[{'availability_date':'2025-01-10'}])
    with pytest.raises(ValueError,match='cutoff'):
        create(ledger,data_cutoff='2025-01-11')
    with pytest.raises(ValueError,match='Training cutoff'):
        create(ledger,training_data_cutoff='2025-01-11')
    for p in (float('nan'),float('inf'),True,-0.1,1.1):
        with pytest.raises(ValueError):
            create(ledger,outputs={'forecasts':{'test':{'status':'predicted','probability':p}}})


def test_conditional_scenario_and_unavailable_models_cannot_lock(ledger):
    for output in ({'forecast_kind':'conditional_scenario','forecasts':{'x':{'status':'predicted','probability':.8}}},
                   {'forecasts':{'x':{'status':'unavailable','probability':None}}}):
        run=create(ledger,outputs=output)
        with pytest.raises(ValueError,match='No actual endpoint'):ledger.lock('owner',run['id'])
    run=create(ledger,target={'endpoint':'PFS'})
    with pytest.raises(ValueError,match='population'):ledger.lock('owner',run['id'])


def test_independent_review_and_approved_evaluation(ledger,monkeypatch):
    run,lock,submitted=ready(ledger,monkeypatch)
    assert ledger.evaluation_rows()==[]
    with pytest.raises(ValueError,match='independent'):
        ledger.verify_outcome('owner',submitted['id'],'approved','Reviewed exact source and target',CONFIRM)
    review=ledger.verify_outcome('reviewer',submitted['id'],'approved','Reviewed exact source and target',CONFIRM)
    rows=ledger.evaluation_rows()
    assert len(rows)==1
    row=rows[0]
    assert row['scope']=='prospective' and row['eligible_for_prospective']
    assert row['probability']==.7 and row['actual_label']==1
    assert row['source_url']==outcome()['source']['url']
    assert row['baseline_probabilities']=={'fixed_0_5':0.5}
    assert row['baseline_audits'][0]['eligible'] is True
    assert row['baseline_audits'][0]['policy_version']=='fixed-0.5-no-features.v1'
    assert row['baseline_audits'][0]['recorded_at'] < row['readout_at']
    assert row['model_training_data_cutoff'] is None
    with pytest.raises(ValueError,match='immutable'):
        ledger.verify_outcome('another',submitted['id'],'rejected','Different result read subsequently',CONFIRM)


@pytest.mark.parametrize('changes',[
    {'label':None},{'endpoint':'OS'}, {'population':'per protocol'}, {'comparison':'other comparator'},
    {'conflicts':['contradictory topline release']},{'extraction_status':'failed'},
    {'readout_date':'2026-01-01'},{'source':{'url':'http://not-https.example','published_at':'2025-02-10'}},
])
def test_incomplete_conflicting_or_mismatched_outcomes_not_forced_to_label(ledger,monkeypatch,changes):
    run=create(ledger);lock=ledger.lock('owner',run['id'],publish=True)
    monkeypatch.setattr(platform_store,'now',lambda:'2025-03-01T00:00:00+00:00')
    item=ledger.submit_outcome('owner',lock['record']['id'],outcome(**changes))
    with pytest.raises(ValueError,match='Approval blocked'):
        ledger.verify_outcome('reviewer',item['id'],'approved','Checked official primary source',CONFIRM)
    assert ledger.evaluation_rows()==[]
    assert ledger.list_outcomes('owner')[0]['status']=='pending_review'


def test_late_lock_and_unknown_evidence_never_count_as_prospective(ledger,monkeypatch):
    for i,ev in enumerate(([{'availability_date':'2025-01-08'}],[{'url':'https://example.org'}])):
        run=create(ledger,owner='owner'+str(i),evidence=ev)
        lock=ledger.lock('owner'+str(i),run['id'],publish=True)
        monkeypatch.setattr(platform_store,'now',lambda:'2025-03-01T00:00:00+00:00')
        item=ledger.submit_outcome('owner'+str(i),lock['record']['id'],outcome(readout_date='2025-01-01') if i==0 else outcome())
        ledger.verify_outcome('reviewer',item['id'],'approved','Checked official result and target',CONFIRM)
    rows=ledger.evaluation_rows()
    assert all(not r['eligible_for_prospective'] for r in rows)
    assert any('lock_not_before_readout' in r['reasons'] for r in rows)
    assert any('evidence_availability_unknown' in r['reasons'] for r in rows)


def test_private_verified_snapshot_not_public_metrics(ledger,monkeypatch):
    run,lock,submitted=ready(ledger,monkeypatch,publish=False)
    ledger.verify_outcome('reviewer',submitted['id'],'approved','Reviewed official source and exact target',CONFIRM)
    assert ledger.evaluation_rows()==[]
    internal=ledger.evaluation_rows(public_only=False)
    assert internal[0]['scope']=='not_evaluable'
    assert 'private_snapshot_not_public_performance' in internal[0]['reasons']


def test_tamper_is_detected_even_if_ciphertext_remains_valid(ledger):
    run=create(ledger)
    with ledger.store.connect() as db:
        db.execute('DROP TRIGGER platform_prediction_runs_no_update')
        altered=dict(run);altered.pop('sha256');altered['outputs']['forecasts']['test-model']['probability']=.99
        db.execute('UPDATE platform_prediction_runs SET payload=? WHERE id=?',(ledger.store.pack(altered),run['id']))
    with pytest.raises(ValueError,match='integrity'):ledger.get_run('owner',run['id'])


def test_review_api_roles_and_public_disclosure_confirmation(ledger,monkeypatch):
    app=FastAPI();app.state.store=ledger.store
    def actor(request):
        if not request.headers.get('x-test-user'):raise HTTPException(401)
        return {'owner':request.headers['x-test-user'],'name':request.headers['x-test-user']}
    app.state.platform_user=actor;app.include_router(router)
    client=TestClient(app);run=create(ledger)
    assert client.get('/api/platform/predictions').status_code==401
    assert client.get('/api/platform/reviews',headers={'x-test-user':'owner'}).status_code==403
    assert client.post('/api/platform/predictions/'+run['id']+'/lock',headers={'x-test-user':'owner'},json={'publish':True}).status_code==422
    assert client.post('/api/platform/predictions/'+run['id']+'/lock',headers={'x-test-user':'owner'},json={'publish':False}).status_code==200
    assert client.get('/api/platform/predictions/'+run['id'],headers={'x-test-user':'other'}).status_code==404
    monkeypatch.setenv('EMERIS_REVIEWERS','reviewer')
    assert client.get('/api/platform/reviews',headers={'x-test-user':'reviewer'}).status_code==200


def test_later_version_cannot_cherry_pick_while_original_unreviewed(ledger,monkeypatch):
    first=create(ledger);ledger.lock('owner',first['id'],publish=True)
    monkeypatch.setattr(platform_store,'now',lambda:'2025-01-11T12:00:00+00:00')
    newer=create(ledger,parent_prediction_id=first['id'],model_version='test-model-v2')
    locked=ledger.lock('owner',newer['id'],publish=True)
    monkeypatch.setattr(platform_store,'now',lambda:'2025-03-01T12:00:00+00:00')
    submitted=ledger.submit_outcome('owner',locked['record']['id'],outcome())
    ledger.verify_outcome('reviewer',submitted['id'],'approved','Checked exact target and primary source',CONFIRM)
    row=ledger.evaluation_rows()[0]
    assert row['primary_prediction_id']==first['id']
    assert not row['eligible_for_prospective']
    assert 'later_version_not_primary' in row['reasons']


def test_zero_probability_is_valid_not_refusal(ledger):
    run=create(ledger,outputs={'forecast_kind':'uncalibrated_llm_research','forecasts':{'model':{'status':'predicted','probability':0}}})
    snapshot=ledger.lock('owner',run['id'])
    assert snapshot['record']['outputs']['forecasts']['model']['probability']==0


def test_legacy_archive_import_preserves_source_bytes_times_and_probabilities(tmp_path,monkeypatch):
    from pathlib import Path
    from emeris_live.platform_store import import_legacy_archive
    from emeris_live.continuous_api import read_continuous
    import hashlib
    root=Path(__file__).resolve().parents[1]
    source=root/'predome_data/continuous_v4/prospective/frozen_forecasts_v1.json'
    before=hashlib.sha256(source.read_bytes()).hexdigest()
    original=read_continuous()['record']['prospective']
    monkeypatch.setattr(platform_store,'now',lambda:'2026-10-21T00:00:00+00:00')
    store=Store(tmp_path/'import-store');result=import_legacy_archive(store);ledger=PlatformStore(store)
    assert result['inserted']==10 and result['existing']==0
    assert result['source_record_sha256']=='4448e33ecb824a6403d9e1d96c868c3c74d68ae2fc5957c19c11ff8cf113eea6'
    published=ledger.list_public_snapshots();assert len(published)==10
    bycase={x['record']['archive_provenance']['case_id']:x for x in published}
    for case in original['cases']:
        record=bycase[case['case_id']]['record']
        assert record['locked_at']==original['frozen_at_utc']
        assert record['data_cutoff']==original['evidence_as_of_utc']
        assert record['prediction_timestamp']<record['locked_at']<record['imported_at']
        assert record['provenance']=='archived_record_import'
        for model,f in case['forecasts'].items():
            assert record['forecasts'][model]['probability']==f['probability']
            assert record['forecasts'][model]['status']==f['status']
    monkeypatch.setattr(platform_store,'now',lambda:'2026-10-22T00:00:00+00:00')
    second=import_legacy_archive(store)
    assert second['inserted']==0 and second['existing']==10
    assert second['records']==result['records']
    assert ledger.list_public_snapshots()==published
    assert hashlib.sha256(source.read_bytes()).hexdigest()==before
    assert ledger.evaluation_rows()==[]


def test_legacy_import_fails_closed_before_any_prediction_write(tmp_path,monkeypatch):
    from emeris_live.platform_store import import_legacy_archive
    from emeris_live import continuous_api
    def invalid():raise ValueError('source archive hash changed')
    monkeypatch.setattr(continuous_api,'read_continuous',invalid)
    store=Store(tmp_path/'bad-import');ledger=PlatformStore(store)
    with pytest.raises(ValueError,match='hash changed'):import_legacy_archive(store)
    assert ledger.list_public_snapshots()==[]


def test_imported_real_snapshot_can_link_independent_result_review(tmp_path,monkeypatch):
    from emeris_live.platform_store import import_legacy_archive
    monkeypatch.setattr(platform_store,'now',lambda:'2026-10-21T00:00:00+00:00')
    store=Store(tmp_path/'review-import');imported=import_legacy_archive(store);ledger=PlatformStore(store)
    selected=next(x for x in imported['records'] if x['case_id']=='forward-000')
    snapshot=ledger.get_snapshot(selected['snapshot_id'],reviewer=True)['record']
    # Synthetic result exists only inside this isolated test DB. The actual
    # source archive is not edited or labeled; this tests the workflow bridge.
    result={'trial_id':snapshot['trial_id'],**{k:snapshot['target'][k] for k in ('endpoint','population','comparison')},
            'label':1,'readout_date':'2026-10-20','source':{'url':'https://example.org/test-only-result','published_at':'2026-10-20'},
            'effect_size':{'measure':'risk difference','estimate':0.2},'confidence_interval':{'lower':0.1,'upper':0.3},'p_value':0.01,
            'extraction_status':'complete','conflicts':[]}
    submitted=ledger.submit_outcome('test-intake',selected['snapshot_id'],result,allow_other_owner=True)
    assert ledger.evaluation_rows()==[]
    ledger.verify_outcome('test-independent-reviewer',submitted['id'],'approved','Test fixture independently checks exact archive target and first-result chronology',CONFIRM)
    rows=ledger.evaluation_rows();assert len(rows)==2
    assert all(x['eligible_for_prospective'] for x in rows)
    assert all(x['locked_at']<x['readout_at']<x['imported_at'] for x in rows)
    assert all(x['archive_provenance']['case_id']=='forward-000' for x in rows)
    public=ledger.public_outcome_records();assert len(public)==1
    assert public[0]['effect_size']['estimate']==.2
    assert public[0]['confidence_interval']=={'lower':.1,'upper':.3}
    assert public[0]['p_value']==.01
    assert len(public[0]['assessments'])==2


def test_public_outcome_assessments_do_not_expose_private_profile(ledger,monkeypatch):
    run,lock,submitted=ready(ledger,monkeypatch)
    ledger.verify_outcome('reviewer',submitted['id'],'approved','Independently reviewed official source chronology',CONFIRM)
    public=ledger.public_outcome_records()[0]
    assert public['assessments']
    for assessment in public['assessments']:
        assert not {'features','asset','sponsor','design','reasons','target'} & assessment.keys()
    assert 'PRIVATE' not in json.dumps(public)


def ledger_client(ledger):
    app=FastAPI();app.state.store=ledger.store
    def actor(request):
        name=request.headers.get('x-test-user')
        if not name:raise HTTPException(401)
        return {'owner':name,'name':name}
    app.state.platform_user=actor;app.include_router(router)
    return TestClient(app)


def test_owner_list_pages_summaries_without_loading_full_collection(ledger,monkeypatch):
    runs=[create(ledger,input_snapshot={'nct_id':'NCT01234567','protocol_text':'PRIVATE '+('x'*100000)}) for _ in range(24)]
    create(ledger,owner='other')
    calls=[];decode=PlatformStore._decode
    def counted(self,row):
        calls.append(row['id'])
        return decode(self,row)
    monkeypatch.setattr(PlatformStore,'_decode',counted)
    client=ledger_client(ledger);headers={'x-test-user':'owner'}
    first=client.get('/api/platform/predictions',headers=headers).json()
    assert len(first['records'])==20 and len(calls)==21
    assert first['pagination']=={'limit':20,'offset':0,'has_more':True,'next_offset':20}
    assert all(row['summary_only'] and 'outputs' not in row and 'input_snapshot' not in row for row in first['records'])
    assert 'PRIVATE' not in json.dumps(first)
    calls.clear()
    second=client.get('/api/platform/predictions?offset=20',headers=headers).json()
    assert len(second['records'])==4 and len(calls)==4
    assert second['pagination']['has_more'] is False
    assert {row['id'] for row in first['records']+second['records']}=={run['id'] for run in runs}
    detail=client.get('/api/platform/predictions/'+runs[0]['id'],headers=headers).json()
    assert detail['input_snapshot']['protocol_text'].startswith('PRIVATE ')
    for query in ('limit=101','limit=0','offset=-1'):
        assert client.get('/api/platform/predictions?'+query,headers=headers).status_code==422


def test_public_list_bounded_decryption_redaction_and_complete_internal_scan(ledger,monkeypatch):
    for _ in range(23):ledger.lock('owner',create(ledger)['id'],publish=True)
    ledger.lock('owner',create(ledger)['id'],publish=False)
    assert len(ledger.list_public_snapshots())==23
    calls=[];decode=PlatformStore._decode
    def counted(self,row):
        calls.append(row['id'])
        return decode(self,row)
    monkeypatch.setattr(PlatformStore,'_decode',counted)
    client=ledger_client(ledger)
    first=client.get('/api/platform/snapshots?limit=5').json()
    assert len(first['records'])==5 and len(calls)==6
    assert first['pagination']['next_offset']==5
    assert 'PRIVATE' not in json.dumps(first)
    assert all(row['record']['public_view']=='explicitly_published_summary_not_full_private_input' for row in first['records'])
    calls.clear()
    last=client.get('/api/platform/snapshots?offset=20').json()
    assert len(last['records'])==3 and len(calls)==3
    assert last['pagination']['next_offset'] is None


def test_ui_pagination_does_not_truncate_evaluation_denominator_or_primary_selection(ledger,monkeypatch):
    locks=[]
    for number in range(22):
        nct=f'NCT{number:08d}'
        run=create(ledger,input_snapshot={'nct_id':nct,'drug':'asset-'+str(number)})
        locks.append(ledger.lock('owner',run['id'],publish=True)['record'])
    # An unreviewed earlier version remains primary even beyond the first UI page.
    later=create(ledger,input_snapshot={'nct_id':locks[0]['trial_id'],'drug':'asset-0'},parent_prediction_id=locks[0]['prediction_id'])
    later=ledger.lock('owner',later['id'],publish=True)['record']
    monkeypatch.setattr(platform_store,'now',lambda:'2025-03-01T12:00:00+00:00')
    for snap in locks[1:]+[later]:
        submitted=ledger.submit_outcome('owner',snap['id'],outcome(trial_id=snap['trial_id']))
        ledger.verify_outcome('reviewer',submitted['id'],'approved','Independent source and exact target checked.',CONFIRM)
    full=ledger.evaluation_rows()
    assert len(full)==22
    assert sum(row['eligible_for_prospective'] for row in full)==21
    excluded=next(row for row in full if row['prediction_id']==later['prediction_id'])
    assert excluded['primary_prediction_id']==locks[0]['prediction_id']
    assert 'later_version_not_primary' in excluded['reasons']
    client=ledger_client(ledger)
    first=client.get('/api/platform/evaluations').json()
    last=client.get('/api/platform/evaluations?offset=20').json()
    assert len(first['records'])==20 and len(last['records'])==2
    assert {row['prediction_id'] for row in first['records']+last['records']}=={row['prediction_id'] for row in full}
    assert len(ledger.public_outcome_records())==22
    assert len(client.get('/api/platform/public-outcomes').json()['records'])==20
    monkeypatch.setenv('EMERIS_REVIEWERS','reviewer')
    reviews=client.get('/api/platform/reviews?limit=3',headers={'x-test-user':'reviewer'}).json()
    assert len(reviews['records'])==3 and reviews['pagination']['has_more']
    assert len(ledger.list_outcomes(reviewer=True))==22
