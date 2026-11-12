"""Download the actual comparison kit through temporary-ledger public routes.

Synthetic clinical/model/source fixtures test engineering behavior only. There
are no live providers, production servers or original-ledger writes here.
"""
from copy import deepcopy
from datetime import datetime, timezone, timedelta
import hashlib
from io import BytesIO
import json
from types import SimpleNamespace
import zipfile

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from emeris_live import arena_public as public
from emeris_live.arena_store import ArenaLedger, result_target_binding
from arena_scientific_fixture import scientifically_accept
from test_arena_core import spec
from test_arena_public import target as archive_target, row as archive_run
from test_arena_public_comparisons import factory, complete, slot_for, CANARY, keys


FILES={'clinical-record.json','comparisons.json','comparison-report.html',
       'comparison-report.md','manifest.json'}


def download(sample):
    return sample.client.get('/api/arena/public/targets/'+sample.target['id']+'/comparisons/download')


def unpack(response):
    from emeris_live.arena_comparison_kit import verify_kit
    assert response.status_code==200,response.text
    assert response.headers['content-type']=='application/zip'
    assert 'attachment;' in response.headers['content-disposition']
    assert response.headers['cache-control']=='no-store'
    with zipfile.ZipFile(BytesIO(response.content)) as archive:
        assert set(archive.namelist())==FILES
        assert archive.testzip() is None
        files={name:archive.read(name) for name in archive.namelist()}
    manifest=json.loads(files['manifest.json'])
    assert manifest['schema_version']=='predome-public-comparison-research-kit-1'
    assert manifest['record_version']=='arena-clinical-public-1.4'
    assert manifest['comparisons_version']=='predome-public-trial-comparisons-1'
    assert set(manifest['files'])==FILES-{'manifest.json'}
    for name,entry in manifest['files'].items():
        assert entry=={'bytes':len(files[name]),'sha256':hashlib.sha256(files[name]).hexdigest()}
    assert verify_kit(response.content,expected_sha256=hashlib.sha256(response.content).hexdigest())==manifest
    assert manifest['provider_calls']==manifest['ledger_writes']==0
    assert manifest['clinical_prediction_validated'] is False
    return files,manifest


def test_download_keeps_actual_baseline_and_comparison_objects_and_reads_native_snapshot_once(factory,monkeypatch):
    sample=factory(agents=True)
    base='/api/arena/public/targets/'+sample.target['id']
    record=sample.client.get(base+'/record').json()
    comparisons=sample.client.get(base+'/comparisons').json()
    before=sample.ledger.events()
    db_sha=hashlib.sha256(sample.ledger.path.read_bytes()).hexdigest()
    original=public.read_frozen
    calls=[]
    def counted(store):
        calls.append(str(store.path))
        return original(store)
    monkeypatch.setattr(public,'read_frozen',counted)
    response=download(sample)
    assert calls==[str(sample.root)]
    files,manifest=unpack(response)
    assert json.loads(files['clinical-record.json'])==record
    assert json.loads(files['comparisons.json'])==comparisons
    assert record['version']=='arena-clinical-public-1.4'
    assert len(record['runs'])==1
    assert len(comparisons['patent_comparisons'])==1
    assert len(comparisons['agent_comparisons'])==2
    assert comparisons['agent_comparisons'][1]['run']['status']=='failed'
    assert manifest['target_id']==sample.target['id']
    assert manifest['nct_id']==sample.target['nct_id']
    assert manifest['cohort_id']==record['target']['cohort_id']
    assert manifest['baseline_assigned_records']==1
    assert manifest['patent_assigned_rows']==1 and manifest['agent_assigned_records']==2
    assert {row['id'] for row in manifest['original_model_times']}==set(sample.ids)
    assert len(manifest['original_model_times'])==len(sample.ids)
    html=files['comparison-report.html'].decode()
    markdown=files['comparison-report.md'].decode()
    assert 'Synthetic comparison' in html or 'SYNTHETIC comparison' in html
    assert 'A-fixed' in html and 'failed' in html
    assert 'Synthetic Example 2' in html and 'Synthetic v1' in html
    assert 'Synthetic Example 2' in markdown and 'Synthetic v1' in markdown
    for run in record['runs']:
        assert run['generated_at_utc'] in html
        assert run['locked_at_utc'] in html
    added=comparisons['patent_comparisons'][0]['added_sources'][0]
    assert added['page_references']==[7,8]
    assert added['source_url'] in html
    for raw in files.values():
        assert CANARY.encode() not in raw and b': 0.71' not in raw
    assert not (set(keys(comparisons)) & {'packet','usage','cost_usd','agent_contract','prompt',
        'probability_change','accuracy','gain','rank','score'})
    assert sample.ledger.events()==before
    assert hashlib.sha256(sample.ledger.path.read_bytes()).hexdigest()==db_sha
    # These are separate pre-existing endpoints and do not count toward the
    # single-snapshot download operation measured above.
    assert sample.client.get(base+'/record').json()==record
    old_brief=sample.client.get(base+'/brief/download')
    assert old_brief.status_code==200
    with zipfile.ZipFile(BytesIO(old_brief.content)) as archive:
        assert set(archive.namelist())=={'clinical-record.json','clinical-brief.html','clinical-brief.md','manifest.json'}
        assert json.loads(archive.read('clinical-record.json'))==record
        assert json.loads(archive.read('manifest.json'))['schema_version']=='predome-public-clinical-brief-1'


def test_download_does_not_mix_post_snapshot_real_ledger_completion(factory,monkeypatch):
    sample=factory(patent_status='pending')
    original=public.read_frozen
    calls=[]
    def captured_then_completed(store):
        snapshot=original(store)
        calls.append(1)
        if len(calls)==1:
            complete(sample.ledger,*sample.lookup['patent_ablation'],value=.71)
        return snapshot
    monkeypatch.setattr(public,'read_frozen',captured_then_completed)
    files,_=unpack(download(sample))
    assert calls==[1]
    record=json.loads(files['clinical-record.json'])
    comparisons=json.loads(files['comparisons.json'])
    assert comparisons['patent_comparisons'][0]['with_patents']['status']=='pending'
    assert comparisons['patent_comparisons'][0]['judgment_changed'] is None
    assert comparisons['patent_comparisons'][0]['control']==record['runs'][0]
    fresh=original(SimpleNamespace(path=sample.root))
    assert next(r for r in fresh['runs'] if r['track']=='patent_ablation')['status']=='predicted'


@pytest.mark.parametrize('state,label,result_before_prediction',[
    ('pending','success',False),('agreement','success',False),
    ('accepted','success',False),('accepted','failure',False),
    ('accepted','mixed',False),('accepted','inconclusive',False),
    ('accepted','success',True),
])
def test_download_retains_result_review_and_existing_individual_match_gates(
        factory,state,label,result_before_prediction):
    sample=factory(agents=True)
    if state!='pending':
        published=datetime.now(timezone.utc)-(timedelta(minutes=30) if result_before_prediction else timedelta())
        outcome={'target_binding':result_target_binding(sample.target),'label':label,
            'source_url':'https://example.org/synthetic-official-outcome','source_sha256':'6'*64,
            'first_public_at':published.isoformat(),
            'endpoint_match_note':'Synthetic exact registered target for kit integration tests.'}
        for reviewer in ('synthetic-kit-review-one','synthetic-kit-review-two'):
            sample.ledger.record_result(sample.target['id'],outcome,reviewer_id=reviewer,
                reviewer_kind='ai',review_note='Synthetic source review, not a real scientific assessment.')
        if state=='accepted':scientifically_accept(sample.ledger,sample.target)
    base='/api/arena/public/targets/'+sample.target['id']
    expected_record=sample.client.get(base+'/record').json()
    expected_comparisons=sample.client.get(base+'/comparisons').json()
    before=sample.ledger.events()
    files,_=unpack(download(sample))
    record=json.loads(files['clinical-record.json'])
    comparison=json.loads(files['comparisons.json'])
    assert record==expected_record and comparison==expected_comparisons
    assert comparison['patent_comparisons'][0]['control']==record['runs'][0]
    eligible=state=='accepted' and label in ('success','failure') and not result_before_prediction
    pair=comparison['patent_comparisons'][0]
    for run in (pair['control'],pair['with_patents']):
        if eligible:
            matched=run['predicted_outcome'].lower()==label
            assert run['match'] is matched
        else:assert run['match'] is None
    assert comparison['agent_comparisons'][1]['run']['match'] is None
    assert not (set(keys(comparison)) & {'accuracy','score','gain','rank','workflow_benefit'})
    assert sample.ledger.events()==before


def test_other_registered_target_with_same_nct_is_not_borrowed_into_download(factory):
    sample=factory(agents=True)
    other_target,raw_slots=spec()
    other_target.update(id='other-analysis-same-nct',trial_name='Synthetic other version')
    other_slot=slot_for(other_target,raw_slots[0]['packet'])
    ids=sample.ledger.register('other-version-same-nct',[other_target],[other_slot],{})
    complete(sample.ledger,ids[0],other_slot)
    files,_=unpack(download(sample))
    record=json.loads(files['clinical-record.json'])
    comparisons=json.loads(files['comparisons.json'])
    assert record['target']['nct_id']==other_target['nct_id']
    assert record['target']['id']==comparisons['target_id']==sample.target['id']
    assert ids[0] not in json.dumps(comparisons)
    assert other_target['id'] not in json.dumps(record)


def test_missing_download_target_does_not_initialize_runtime(tmp_path,monkeypatch):
    root=tmp_path/'absent-runtime'
    monkeypatch.setattr(public,'ROOT',tmp_path/'absent-source-root')
    monkeypatch.setattr(public,'read_archived',lambda store:([],[],{}))
    app=FastAPI();app.state.store=SimpleNamespace(path=root);app.include_router(public.router)
    with TestClient(app) as client:
        response=client.get('/api/arena/public/targets/missing/comparisons/download')
        existing=client.get('/api/arena/public/targets/missing/comparisons')
    assert response.status_code==existing.status_code==404
    assert not root.exists()


def test_corrupt_existing_ledger_download_fails_closed_without_echo_or_repair(factory):
    sample=factory()
    with sample.ledger.connect() as db:
        db.execute('DROP TRIGGER arena_no_update')
        db.execute("UPDATE arena_events SET event_hash='synthetic-invalid-event-hash' WHERE seq=1")
    before=hashlib.sha256(sample.ledger.path.read_bytes()).hexdigest()
    response=download(sample)
    assert response.status_code==503
    assert CANARY not in response.text and 'synthetic-invalid-event-hash' not in response.text
    assert hashlib.sha256(sample.ledger.path.read_bytes()).hexdigest()==before


def test_known_historical_record_preserves_empty_comparisons_and_old_brief_contract(tmp_path,monkeypatch):
    root=tmp_path/'absent-native-runtime'
    target=archive_target(id='synthetic-history',cohort_id=None,scope='archived_research',
        record_kind='historical_replay')
    run=archive_run(target_id=target['id'],cohort_id=None,scope='archived_research',track='archived')
    monkeypatch.setattr(public,'ROOT',tmp_path/'absent-source-root')
    monkeypatch.setattr(public,'read_archived',lambda store:([deepcopy(target)],[deepcopy(run)],{}))
    app=FastAPI();app.state.store=SimpleNamespace(path=root);app.include_router(public.router)
    sample=SimpleNamespace(client=TestClient(app),target=target)
    base='/api/arena/public/targets/'+target['id']
    comparison=sample.client.get(base+'/comparisons')
    assert comparison.status_code==200
    assert comparison.json()['patent_comparisons']==comparison.json()['agent_comparisons']==[]
    record=sample.client.get(base+'/record').json()
    assert record['version']=='arena-clinical-public-1.4'
    assert record['target']['record_kind']=='historical_replay'
    files,_=unpack(download(sample))
    assert json.loads(files['clinical-record.json'])==record
    assert json.loads(files['comparisons.json'])==comparison.json()
    assert not root.exists()


def test_known_native_target_without_other_tracks_downloads_honest_empty_comparisons(tmp_path,monkeypatch):
    target,raw=spec()
    target.update(trial_name='Synthetic baseline-only trial')
    slots=[slot_for(target,raw[0]['packet'],model=model) for model in ('A','B')]
    ledger=ArenaLedger(tmp_path/'baseline-only')
    ids=ledger.register('baseline-only-cohort',[target],slots,{})
    for ident,slot in zip(ids,slots):complete(ledger,ident,slot)
    monkeypatch.setattr(public,'ROOT',tmp_path/'absent-source-root')
    monkeypatch.setattr(public,'read_archived',lambda store:([],[],{}))
    app=FastAPI();app.state.store=SimpleNamespace(path=ledger.path.parent);app.include_router(public.router)
    sample=SimpleNamespace(client=TestClient(app),target=target)
    base='/api/arena/public/targets/'+target['id']
    result=sample.client.get(base+'/comparisons')
    assert result.status_code==200
    assert result.json()['patent_comparisons']==result.json()['agent_comparisons']==[]
    before=ledger.events()
    files,_=unpack(download(sample))
    assert json.loads(files['comparisons.json'])==result.json()
    assert {r['id'] for r in json.loads(files['clinical-record.json'])['runs']}==set(ids)
    assert ledger.events()==before
