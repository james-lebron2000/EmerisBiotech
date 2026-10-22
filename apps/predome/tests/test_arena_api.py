"""Public Arena contracts use isolated fixtures, never forecast-provider calls."""
import hashlib
import json
from pathlib import Path
from fastapi import FastAPI
from fastapi.testclient import TestClient
from emeris_live import arena_api as api
from emeris_live.store import Store
from emeris_live.arena_store import ArenaLedger
from test_arena_core import spec


def client(tmp_path,monkeypatch):
    monkeypatch.setattr(api,'archived_views',lambda store:([],[],{}))
    monkeypatch.setattr(api,'read_catalog',lambda:{'items':[],'connectors':[]})
    monkeypatch.setattr(api,'list_models',lambda:[])
    app=FastAPI();app.state.store=Store(tmp_path/'runtime');app.include_router(api.router)
    return TestClient(app),ArenaLedger(app.state.store.path)


def test_no_public_billable_or_result_mutation_routes(tmp_path,monkeypatch):
    c,_=client(tmp_path,monkeypatch)
    for url in ('/api/arena','/api/arena/run','/api/arena/results','/api/arena/register'):
        assert c.post(url,json={}).status_code in (404,405)
    view=c.get('/api/arena').json()
    assert view['capabilities']['public_run_enabled'] is False
    assert view['metrics']['assigned']==0
    assert view['metrics']['ranking_allowed'] is False


def test_download_exact_frozen_packet_and_null_pending(tmp_path,monkeypatch):
    c,ledger=client(tmp_path,monkeypatch)
    t,slots=spec();t["scope"]="prospective_challenge";s=slots[0]
    ids=ledger.register('test',[t],[s],{})
    view=c.get('/api/arena').json()
    assert view['runs'][0]['probability'] is None
    assert 'packet' not in view['runs'][0]
    assert c.get('/api/arena/runs/'+ids[0]+'/packet').json()['packet']==s['packet']
    detail=c.get('/api/arena/targets/'+t['id']).json()
    assert detail['evidence']==s['packet']['evidence']
    assert c.get('/api/arena/targets/unknown').status_code==404


def test_attestation_exports_only_allowlisted_integrity_checked_files(tmp_path,monkeypatch):
    c,_=client(tmp_path,monkeypatch)
    monkeypatch.setattr(api,'ROOT',tmp_path)
    p=tmp_path/'arena_data/attestations/test-proof';p.mkdir(parents=True)
    files={}
    for name in ('manifest.json','manifest.tsq','manifest.tsr','tsa.crt','cacert.pem','verification.txt'):
        (p/name).write_bytes(b'isolated-test-only');files[name]=hashlib.sha256(b'isolated-test-only').hexdigest()
    (p/'receipt.json').write_text(json.dumps({'files':files}))
    assert c.get('/api/arena/attestations/test-proof').status_code==200
    download=c.get('/api/arena/attestations/test-proof/download')
    assert download.status_code==200 and download.headers['content-type']=='application/zip'
    (p/'manifest.json').write_bytes(b'tampered')
    assert c.get('/api/arena/attestations/test-proof').status_code==404


def test_empty_support_array_never_turns_all_citations_into_support():
    row={'evidence_ids':['e1'],'supporting_evidence_ids':[],'opposing_evidence_ids':['e1'],
        'packet':{'evidence':[{'id':'e1','source_url':'https://example.org','text':'limitation'}]}}
    public=api._public_run(row)
    assert public['supporting_evidence']==[]
    assert len(public['counter_evidence'])==1


def test_report_download_uses_target_scoped_data(tmp_path,monkeypatch):
    c,ledger=client(tmp_path,monkeypatch)
    t,slots=spec();t['scope']='prospective_challenge'
    ledger.register('test',[t],[slots[0]],{})
    response=c.get('/api/arena/targets/'+t['id']+'/report')
    assert response.status_code==200
    assert response.headers['content-type'].startswith('text/markdown')
    assert 'attachment;' in response.headers['content-disposition']
    assert t['endpoint'] in response.text
    assert c.get('/api/arena/targets/missing/report').status_code==404


def test_source_download_is_manifest_bound_hash_verified_attachment(tmp_path,monkeypatch):
    c,_=client(tmp_path,monkeypatch)
    monkeypatch.setattr(api,'ROOT',tmp_path)
    folder=tmp_path/'arena_data/source_snapshots';folder.mkdir(parents=True)
    raw=b'<article><p>Actual archived source bytes</p></article>'
    source=folder/'paper.xml';source.write_bytes(raw)
    h=hashlib.sha256(raw).hexdigest()
    manifest=tmp_path/'arena_data/season_acquisition_manifest.json'
    manifest.write_text(json.dumps({'schema_version':'arena-public-acquisition-v1','assets':[
        {'path':'arena_data/source_snapshots/paper.xml','sha256':h,'bytes':len(raw)}]}))
    r=c.get('/api/arena/sources/'+h)
    assert r.status_code==200 and r.content==raw
    assert r.headers['content-type']=='application/octet-stream'
    assert r.headers['x-content-type-options']=='nosniff'
    assert 'attachment;' in r.headers['content-disposition']
    source.write_bytes(b'changed')
    assert c.get('/api/arena/sources/'+h).status_code==404
    assert c.get('/api/arena/sources/'+'a'*64).status_code==404


def test_source_download_rejects_manifest_escape_or_symlink(tmp_path,monkeypatch):
    c,_=client(tmp_path,monkeypatch);monkeypatch.setattr(api,'ROOT',tmp_path)
    folder=tmp_path/'arena_data/source_snapshots';folder.mkdir(parents=True)
    private=tmp_path/'private.txt';private.write_bytes(b'private')
    h=hashlib.sha256(b'private').hexdigest();manifest=tmp_path/'arena_data/season_acquisition_manifest.json'
    for name in ('../../private.txt','arena_data/source_snapshots/public.txt'):
        if 'public.txt' in name:(folder/'public.txt').symlink_to(private)
        manifest.write_text(json.dumps({'schema_version':'arena-public-acquisition-v1','assets':[
            {'path':name,'sha256':h,'bytes':7}]}))
        assert c.get('/api/arena/sources/'+h).status_code==404


def test_withdrawn_formal_cohort_is_not_active_season(tmp_path,monkeypatch):
    c,ledger=client(tmp_path,monkeypatch);t,slots=spec();t['scope']='prospective_challenge'
    ledger.register('test',[t],[slots[0]],{'season_kind':'registered_12_project_season'})
    ledger.withdraw_unstarted_cohort('test','Incorrect source excerpt; replacement must be newly registered.')
    view=c.get('/api/arena').json()
    assert view['challenge_summary']['complete_season_started'] is False
    assert view['runs'][0]['status']=='withdrawn' and view['runs'][0]['probability'] is None
    report=c.get('/api/arena/targets/'+t['id']+'/report').text
    assert '任何模型调用前撤回' in report and '模型失败或临床阴性' in report


def test_public_llm_name_is_exact_id_without_adapter_label(tmp_path,monkeypatch):
    c,_=client(tmp_path,monkeypatch)
    monkeypatch.setattr(api,'list_models',lambda:[{'id':'kimi-k3','name':'Kimi K3 · Semantix 网关',
        'provider':'openai_compatible','available':True}])
    m=c.get('/api/arena').json()['models'][0]
    assert m['name']=='kimi-k3'
    # Do not change the audited connection identity to rename a UI label.
    assert m['provider']=='openai_compatible'
