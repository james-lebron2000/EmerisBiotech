"""Integration checks: synthetic calendar sources only in isolated temporary stores."""
from copy import deepcopy
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import pytest
from fastapi import FastAPI,HTTPException
from fastapi.testclient import TestClient
from emeris_live.store import Store
from emeris_live import product,intake,readout_updates
from emeris_live.platform_store import PlatformStore,import_legacy_archive
from emeris_live.readout_updates import ReadoutUpdateStore,CONFIRMATIONS
from emeris_live.intake_api import router as intake_router

AS_OF=datetime(2026,10,21,12,tzinfo=timezone.utc)


@pytest.fixture
def isolated_store(tmp_path,monkeypatch):
    monkeypatch.setattr(intake,'now',lambda:'2026-10-21T10:00:00+00:00')
    monkeypatch.setattr(readout_updates,'now',lambda:'2026-10-21T12:00:00+00:00')
    store=Store(tmp_path/'synthetic-test-only')
    intake.initialize(store)
    return store


def test_real_product_calendar_overlay_changes_only_reviewed_display_metadata(isolated_store):
    store=isolated_store
    import_legacy_archive(store)
    frozen_before=PlatformStore(store).list_public_snapshots()
    before=product.build_product(store,now=AS_OF)
    assert len(before['upcoming'])==7
    original=deepcopy(before['upcoming'][0]);nct=original['nct_id']
    quote=f'SYNTHETIC TEST ONLY: {nct} primary readout will be presented on April 12, 2027.'
    url='https://example.invalid/synthetic-readout-integration'
    cid,_=intake.save_candidate(store,nct,'official_announcement_candidate',url,quote.encode(),
        {'title':'SYNTHETIC TEST DATA — NOT REAL GUIDANCE','text':quote,'published_at':'2026-10-20',
         'content_type':'text/plain','trial_identity_match':['nct'],'synthetic_test_only':True},
        hash_scope='source_response_bytes')
    proposal=ReadoutUpdateStore(store).propose('synthetic-proposer-private',{
        'nct_id':nct,'candidate_id':cid,'date_kind':'official_exact_date','start':'2027-04-12','end':'2027-04-12',
        'precision':'day','label':'SYNTHETIC TEST · 2027-04-12','quoted_evidence':quote,
        'reason':'PRIVATE TEST REASON: synthetic integration fixture, not real clinical guidance.'})
    pending=product.build_product(store,now=AS_OF)
    assert pending==before
    ReadoutUpdateStore(store).review('synthetic-independent-private',proposal['id'],'approved',
        'PRIVATE REVIEW REASON: approval applies solely to this isolated synthetic test fixture.',
        {key:True for key in CONFIRMATIONS})
    after=product.build_product(store,now=AS_OF)
    changed=next(t for t in after['upcoming'] if t['nct_id']==nct)
    assert changed['window']=={'start':'2027-04-12','end':'2027-04-12','precision':'day',
                               'label':'SYNTHETIC TEST · 2027-04-12','source_url':url}
    assert changed['readout_date_kind']=='official_exact_date'
    assert changed['official_announced_readout_date']=='2027-04-12'
    assert changed['previous_display_window']==original['window']
    assert changed['original_window']==original['original_window']
    assert changed['readout_update']['source_sha256']==hashlib.sha256(quote.encode()).hexdigest()
    assert changed['readout_update']['provenance']['original_prediction_unchanged']
    # Full target equality covers probability, frozen timestamp, evidence cutoff,
    # target definition, source archive SHA, refusal state and uncertainty fields.
    assert changed['targets']==original['targets']
    assert changed['inputs']==original['inputs']
    changed_keys={key for key in set(changed)|set(original) if changed.get(key)!=original.get(key)}
    assert changed_keys <= {'window','previous_display_window','readout_date_kind','official_announced_readout_date',
                            'readout_update','calendar_status','checked_at','status_note','sources'}
    for trial in after['upcoming']:
        if trial['nct_id']!=nct:
            assert trial==next(t for t in before['upcoming'] if t['nct_id']==trial['nct_id'])
    assert PlatformStore(store).list_public_snapshots()==frozen_before
    assert after['summary']['reviewed_readout_update_count']==1
    public=json.dumps(after,ensure_ascii=False)
    for secret in ('synthetic-proposer-private','synthetic-independent-private','PRIVATE TEST REASON','PRIVATE REVIEW REASON'):
        assert secret not in public


def test_all_seven_drug_annotations_have_source_bound_target_and_modality():
    view=product.build_product(now=AS_OF)
    assert len(view['upcoming'])==7
    assert len(view['provenance']['drug_annotations_sha256'])==64
    for trial in view['upcoming']:
        assert trial['molecular_target'] and trial['molecular_target']!='Unknown'
        assert trial['modality'] and trial['modality']!='Unknown'
        annotation=trial['drug_annotation']
        assert annotation['nct_id']==trial['nct_id']
        assert annotation['asset'].casefold()==trial['asset'].casefold()
        assert annotation['molecular_target']==trial['molecular_target']
        assert annotation['drug_modality']==trial['modality']
        assert annotation['clinical_result_inferred'] is False
        assert annotation['frozen_prediction_input_changed'] is False
        for source in annotation['sources']+[annotation['trial_drug_linkage']]:
            path=(product.ROOT/source['path']).resolve()
            assert path.is_relative_to(product.ROOT.resolve())
            assert hashlib.sha256(path.read_bytes()).hexdigest()==source['sha256']


def test_bad_drug_annotation_source_fails_closed(monkeypatch):
    view=product.build_product(now=AS_OF)
    source=view['upcoming'][0]['drug_annotation']['sources'][0]
    bad_path=(product.ROOT/source['path']).resolve()
    read=Path.read_bytes
    def tampered(path):
        return b'SYNTHETIC CORRUPTED SOURCE FOR TEST ONLY' if path.resolve()==bad_path else read(path)
    monkeypatch.setattr(Path,'read_bytes',tampered)
    with pytest.raises(ValueError,match='Drug annotation source integrity'):
        product.attach_drug_annotations(deepcopy(view))


def test_intake_api_second_page_and_binary_download_auth_and_integrity(isolated_store,monkeypatch):
    store=isolated_store
    monkeypatch.setenv('EMERIS_REVIEWERS','reviewer')
    ids=[]
    for index in range(21):
        raw=f'SYNTHETIC SOURCE {index}'.encode()
        cid,_=intake.save_candidate(store,'NCT01234567','registry_update',f'https://example.invalid/test-source-{index}',raw,{'synthetic_test_only':True})
        ids.append(cid)
    app=FastAPI();app.state.store=store
    def actor(request):
        user=request.headers.get('x-test-user')
        if not user:raise HTTPException(401)
        return {'owner':user,'name':user}
    app.state.platform_user=actor;app.include_router(intake_router)
    client=TestClient(app);headers={'x-test-user':'reviewer'}
    assert client.get('/api/intake/candidates').status_code==401
    assert client.get('/api/intake/candidates',headers={'x-test-user':'ordinary'}).status_code==403
    first=client.get('/api/intake/candidates',headers=headers).json()
    last=client.get('/api/intake/candidates?offset=20',headers=headers).json()
    assert len(first['records'])==20 and len(last['records'])==1
    assert first['pagination']['next_offset']==20
    assert last['pagination']['next_offset'] is None
    assert {r['id'] for r in first['records']+last['records']}==set(ids)
    raw=b'%PDF-1.7\x00\xff\xfe SYNTHETIC BINARY TEST ONLY'
    sha=hashlib.sha256(raw).hexdigest()
    cid,_=intake.save_candidate(store,'NCT01234567','official_announcement_candidate',
        'https://example.invalid/synthetic-source.pdf',raw,{'synthetic_test_only':True,'content_type':'application/pdf'},
        hash_scope='source_response_bytes')
    path='/api/intake/candidates/'+cid+'/source'
    assert client.get(path).status_code==401
    assert client.get(path,headers={'x-test-user':'ordinary'}).status_code==403
    response=client.get(path,headers=headers)
    assert response.status_code==200 and response.content==raw
    assert hashlib.sha256(response.content).hexdigest()==sha
    assert response.headers['content-type']=='application/octet-stream'
    assert response.headers['content-disposition']==f'attachment; filename="source-{sha[:16]}.pdf"'
    assert client.get('/api/intake/candidates/not-existing/source',headers=headers).status_code==404
    with store.connect() as db:
        db.execute('UPDATE intake_source_blobs SET payload=? WHERE sha256=?',(store.pack({'bytes_b64':'Y29ycnVwdGVk'}),sha))
    assert client.get(path,headers=headers).status_code==409
