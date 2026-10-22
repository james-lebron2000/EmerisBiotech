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
