"""Isolated fixed-baseline registration and strict pre-readout alignment tests."""
import json
import sqlite3
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from emeris_live.store import Store
from emeris_live import platform_store,prospective_baselines as baseline
from emeris_live.platform_store import PlatformStore,import_legacy_archive,digest
from emeris_live.platform_api import router

TARGET={'endpoint':'PFS','population':'ITT adults','comparison':'Treatment versus placebo','success_definition':'Prespecified adjusted primary PFS analysis favors treatment'}
CONFIRM={key:True for key in ('endpoint_match','population_match','comparison_match','source_checked','prespecified_analysis','earliest_result_checked','evidence_cutoff_checked')}

@pytest.fixture
def ledger(tmp_path,monkeypatch):
    monkeypatch.setattr(platform_store,'now',lambda:'2025-01-10T12:00:00+00:00')
    return PlatformStore(Store(tmp_path/'isolated'))


def create(ledger,publish=True,target=None):
    run=ledger.create_run('owner',{'nct_id':'NCT01234567','private_profile':'PRIVATE NEVER BASELINE FEATURE'},
        {'forecast_kind':'uncalibrated_llm_research','forecasts':{'test-model':{'status':'predicted','probability':.8}},'statistical_assurance':.999},
        model_version='test-model',pipeline_version='test',data_cutoff='2025-01-09T00:00:00+00:00',target=target or TARGET,
        evidence=[{'url':'https://clinicaltrials.gov/study/NCT01234567','availability_date':'2025-01-08'}])
    return ledger.lock('owner',run['id'],publish=publish)


def approved(ledger,snapshot,monkeypatch,label=1):
    monkeypatch.setattr(platform_store,'now',lambda:'2025-03-01T12:00:00+00:00')
    target=snapshot['record']['target']
    outcome=ledger.submit_outcome('owner',snapshot['record']['id'],{
        'trial_id':'NCT01234567',**{key:target[key] for key in ('endpoint','population','comparison')},
        'label':label,'readout_date':'2025-02-10','source':{'url':'https://example.invalid/synthetic-result','published_at':'2025-02-10'},
        'extraction_status':'complete','conflicts':[]})
    ledger.verify_outcome('independent',outcome['id'],'approved','Independent synthetic result adjudication in isolated test.',CONFIRM)


def client(ledger):
    app=FastAPI();app.state.store=ledger.store;app.include_router(router)
    return TestClient(app)


def test_new_public_lock_freezes_feature_free_policy_then_verified_result_aligns(ledger,monkeypatch):
    snapshot=create(ledger)
    records=baseline.list_baselines(ledger.store)
    assert len(records)==1
    record=records[0]
    assert record['probability']==.5 and record['inputs_used']==[] and record['label_fitting'] is False
    assert record['recorded_at']=='2025-01-10T12:00:00+00:00'
    assert record['source_forecast_locked_at']==snapshot['record']['locked_at']
    assert record['effective_evidence_cutoff']==record['source_forecast_cutoff']==snapshot['record']['data_cutoff']
    assert record['snapshot_sha256']==snapshot['sha256']
    assert record['target_sha256']==digest(TARGET)
    assert record['target']==TARGET and record['policy_sha256']==digest(baseline.POLICY)
    assert record['state']=='awaiting_verified_readout' and not record['eligible']
    assert 'PRIVATE' not in json.dumps(record) and 'statistical_assurance' not in json.dumps(record)
    assert not ledger.evaluation_rows()
    approved(ledger,snapshot,monkeypatch)
    rows=ledger.evaluation_rows();assert len(rows)==1
    assert rows[0]['eligible_for_prospective'] and rows[0]['baseline_probabilities']=={'fixed_0_5':.5}
    audit=rows[0]['baseline_audits'][0]
    assert audit['eligible'] and audit['integrity_verified']
    assert audit['snapshot_sha256']==rows[0]['snapshot_sha256']
    assert audit['target_sha256']==digest(rows[0]['target'])
    summary=baseline.summary(ledger.store)
    assert summary['pre_readout_registered_verified_count']==1 and summary['registered_snapshot_count']==1
    assert summary['awaiting_verified_readout_count']==0


def test_private_lock_never_registers_and_public_get_does_not_create_records(ledger):
    private=create(ledger,publish=False)
    c=client(ledger)
    result=c.get('/api/platform/baselines').json()
    assert result['records']==[] and result['summary']['public_locked_snapshot_count']==0
    assert baseline.register_existing_public_baselines(ledger.store)['registered']==0
    with ledger.store.connect() as db:
        with pytest.raises(ValueError,match='Private'):
            baseline.register_snapshot(ledger.store,db,private['record'],private['sha256'],'new_public_snapshot_lock')
        assert db.execute('SELECT COUNT(*) FROM platform_prospective_baselines').fetchone()[0]==0
    assert c.get('/api/platform/baselines/not-existing').status_code==404


def test_explicit_old_archive_migration_keeps_all_snapshot_bytes_and_real_current_times(ledger,monkeypatch):
    import_legacy_archive(ledger.store)
    with ledger.store.connect() as db:
        before=[tuple(row) for row in db.execute('SELECT * FROM platform_prediction_snapshots ORDER BY id')]
    assert client(ledger).get('/api/platform/baselines').json()['summary']['missing_registration_count']==10
    assert not baseline.list_baselines(ledger.store)
    monkeypatch.setattr(platform_store,'now',lambda:'2026-11-01T12:00:00+00:00')
    first=baseline.register_existing_public_baselines(ledger.store)
    assert first['registered']==10 and first['existing']==0
    assert all(row['recorded_at']=='2026-11-01T12:00:00+00:00' for row in first['records'])
    assert all(row['recorded_at']>row['source_forecast_locked_at'] for row in first['records'])
    assert all(row['effective_evidence_cutoff']==row['source_forecast_cutoff'] for row in first['records'])
    monkeypatch.setattr(platform_store,'now',lambda:'2026-11-02T12:00:00+00:00')
    repeat=baseline.register_existing_public_baselines(ledger.store)
    assert repeat['registered']==0 and repeat['existing']==10
    assert [row['record_sha256'] for row in first['records']]==[row['record_sha256'] for row in repeat['records']]
    assert [row['recorded_at'] for row in first['records']]==[row['recorded_at'] for row in repeat['records']]
    with ledger.store.connect() as db:
        assert before==[tuple(row) for row in db.execute('SELECT * FROM platform_prediction_snapshots ORDER BY id')]


def test_postreadout_migration_is_excluded_without_discarding_agent_score(ledger,monkeypatch):
    register=baseline.register_snapshot
    monkeypatch.setattr(baseline,'register_snapshot',lambda *args:None)
    snapshot=create(ledger)
    monkeypatch.setattr(baseline,'register_snapshot',register)
    approved(ledger,snapshot,monkeypatch)
    assert ledger.evaluation_rows()[0]['baseline_audits'][0]['exclusion_reasons']==['baseline_not_registered']
    before=ledger.get_snapshot(snapshot['record']['id'],owner='owner')
    # Registration must not inspect outcome labels or fitted assurances.
    with ledger.store.connect() as db:
        forbidden={row[0] for row in db.execute('SELECT payload FROM platform_actual_outcomes')}|{row[0] for row in db.execute('SELECT payload FROM platform_evaluation_results')}
    unpack=ledger.store.unpack
    def guarded(blob):
        assert blob not in forbidden,'Migration read actual outcome or evaluation label'
        return unpack(blob)
    with monkeypatch.context() as context:
        context.setattr(ledger.store,'unpack',guarded)
        migration=baseline.register_existing_public_baselines(ledger.store)
    assert migration['registered']==1
    row=ledger.evaluation_rows()[0]
    assert row['eligible_for_prospective'] and row['probability']==.8
    assert row['baseline_probabilities']=={}
    assert 'baseline_not_registered_before_readout' in row['baseline_audits'][0]['exclusion_reasons']
    assert ledger.get_snapshot(snapshot['record']['id'],owner='owner')==before
    assert baseline.summary(ledger.store)['late_registered_verified_count']==1


def test_append_only_idempotent_and_whole_target_binding(ledger):
    first=create(ledger)
    second=create(ledger,target=TARGET|{'population':'Different subgroup'})
    items=baseline.list_baselines(ledger.store)
    assert len(items)==2 and len({row['target_sha256'] for row in items})==2
    repeated=baseline.register_existing_public_baselines(ledger.store)
    assert repeated['registered']==0 and repeated['existing']==2
    with ledger.store.connect() as db:
        for action in ('UPDATE platform_prospective_baselines SET created=created','DELETE FROM platform_prospective_baselines'):
            with pytest.raises(sqlite3.IntegrityError,match='append-only'):db.execute(action)
        with pytest.raises(ValueError,match='integrity'):
            baseline.register_snapshot(ledger.store,db,first['record']|{'target':TARGET|{'comparison':'Wrong comparator'}},first['sha256'],'explicit_public_snapshot_migration')


@pytest.mark.parametrize('change',[
    {'algorithm':'fitted_labels'},
    {'target_sha256':'a'*64},
    {'snapshot_sha256':'b'*64},
    {'actual_label':1},
    {'source_forecast_cutoff':'2025-02-01'},
])
def test_corrupt_sidecar_fails_closed_but_keeps_valid_agent_evaluation(ledger,monkeypatch,change):
    snapshot=create(ledger);approved(ledger,snapshot,monkeypatch)
    with ledger.store.connect() as db:
        row=db.execute('SELECT * FROM platform_prospective_baselines').fetchone()
        record=ledger.store.unpack(row['payload'])|change
        db.execute('DROP TRIGGER prospective_baselines_no_update')
        db.execute('UPDATE platform_prospective_baselines SET payload=?,sha256=? WHERE id=?',(ledger.store.pack(record),digest(record),row['id']))
    with pytest.raises(ValueError):baseline.list_baselines(ledger.store)
    item=ledger.evaluation_rows()[0]
    assert item['eligible_for_prospective'] and item['baseline_probabilities']=={}
    assert item['baseline_audits'][0]['exclusion_reasons']==['baseline_integrity_or_binding_failed']
    assert client(ledger).get('/api/platform/baselines').status_code==422


def test_atomic_new_lock_rolls_back_when_baseline_registration_fails(ledger,monkeypatch):
    def fail(*args):raise ValueError('Synthetic baseline insert failure')
    monkeypatch.setattr(baseline,'register_snapshot',fail)
    with pytest.raises(ValueError,match='insert failure'):create(ledger)
    with ledger.store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM platform_prediction_snapshots').fetchone()[0]==0
        assert db.execute('SELECT COUNT(*) FROM platform_prediction_runs').fetchone()[0]==1
        assert db.execute('SELECT COUNT(*) FROM platform_prospective_baselines').fetchone()[0]==0


def test_public_api_paging_summary_is_complete_and_no_model_count_inflation(ledger):
    for number in range(22):create(ledger,target=TARGET|{'endpoint':'PFS '+str(number)})
    c=client(ledger)
    first=c.get('/api/platform/baselines').json()
    last=c.get('/api/platform/baselines?offset=20').json()
    assert len(first['records'])==20 and len(last['records'])==2
    assert first['summary']==last['summary']
    assert first['summary']['registered_snapshot_count']==22
    assert first['summary']['awaiting_verified_readout_count']==22
    assert first['pagination']['next_offset']==20 and last['pagination']['next_offset'] is None
    assert len({r['baseline_id'] for r in first['records']+last['records']})==22
    detail=c.get('/api/platform/baselines/'+first['records'][0]['baseline_id']).json()
    assert detail==first['records'][0]
    assert 'PRIVATE' not in json.dumps(first)
    assert all(len(record['record']['forecasts'])==1 for record in ledger.list_public_snapshots())
