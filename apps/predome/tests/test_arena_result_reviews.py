"""Synthetic result/source reviews only; these are not real expert annotations."""
from copy import deepcopy
from datetime import datetime,timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest
from emeris_live.arena_store import ArenaLedger, result_target_binding
from emeris_live.arena_metrics import build_metrics
from emeris_live.arena_public import result_view
from test_arena_core import registered,finish

sys.path.insert(0,str(Path(__file__).parents[1]/'scripts'))
import arena_result_review as cli


def result(target,label='success'):
    return {'label':label,'source_url':'https://synthetic-test.example/official-result',
        'source_sha256':'b'*64,'first_public_at':datetime.now(timezone.utc).isoformat(),
        'endpoint_match_note':'Isolated source mapping to the exact fixture target',
        'target_binding':result_target_binding(target)}


def vote(ledger,target,body,who,kind='ai',**kwargs):
    return ledger.record_result(target['id'],body,reviewer_id=who,reviewer_kind=kind,
        review_note='Independent isolated test note by '+who,**kwargs)


@pytest.mark.parametrize('kinds,basis,humans',[(('ai','ai'),'ai_only',0),(('human','human'),'human_only',2),(('ai','human'),'mixed',1)])
def test_explicit_review_kinds_remain_distinct_without_claiming_authenticated_people(tmp_path,kinds,basis,humans):
    ledger,t,ids=registered(tmp_path)
    for ident,model in zip(ids,('A','B')):finish(ledger,ident,returned_model=model+'-fixed')
    body=result(t)
    assert vote(ledger,t,body,'fixture-one',kinds[0])=='announced_pending_adjudication'
    assert vote(ledger,t,body,'fixture-two',kinds[1])=='adjudicated'
    snap=ledger.snapshot();target=snap['targets'][0];p=target['result']['review_provenance']
    assert p['basis']==basis and p['review_counts']['human']==humans
    assert p['identity_assurance']=='operator_declared_not_independently_authenticated'
    assert p['independent_human_review_verified'] is False
    public=result_view(target)
    assert 'fixture-one' not in json.dumps(public) and 'fixture-two' not in json.dumps(public)
    assert public['verification_note'] and build_metrics(snap)['by_model'][0]['outcome_review_basis_counts']=={basis:1}
    assert build_metrics(snap)['clinical_prediction_validated'] is False


def test_legacy_unknown_reviewers_are_not_automatically_humans(tmp_path):
    ledger,t,_=registered(tmp_path);body=result(t);body.pop('target_binding')
    ledger.record_result(t['id'],body,reviewer_id='Dr.One');ledger.record_result(t['id'],body,reviewer_id='Dr.Two')
    target=ledger.snapshot()['targets'][0]
    assert target['result']['review_provenance']['review_counts']=={'ai':0,'human':0,'unspecified':2}
    assert 'unverified' in result_view(target)['verification_note']


@pytest.mark.parametrize('field',['id','nct_id','endpoint','population','comparison','success_definition','analysis_timepoint'])
def test_typed_reviews_cannot_bind_a_different_trial_hypothesis(tmp_path,field):
    ledger,t,_=registered(tmp_path);body=result(t);body['target_binding'][field]='wrong'
    before=ledger.events()
    with pytest.raises(ValueError,match='exact registered'):vote(ledger,t,body,'fixture-one')
    assert ledger.events()==before


def test_typed_and_unknown_reviews_do_not_silently_form_a_matching_pair(tmp_path):
    ledger,t,_=registered(tmp_path);body=result(t)
    ledger.record_result(t['id'],body,reviewer_id='unspecified')
    assert vote(ledger,t,body,'fixture-ai')=='announced_pending_adjudication'
    assert ledger.snapshot()['targets'][0]['status']=='announced'


def test_case_variants_are_one_reviewer_not_two(tmp_path):
    ledger,t,_=registered(tmp_path);body=result(t)
    vote(ledger,t,body,'FixtureDoctor','human')
    with pytest.raises(ValueError,match='already voted'):vote(ledger,t,body,'fixturedoctor','human')


def test_an_ai_review_cannot_be_relabelled_as_human_by_a_correction(tmp_path):
    ledger,t,_=registered(tmp_path);body=result(t)
    vote(ledger,t,body,'fixture-agent','ai')
    first=next(e for e in ledger.events() if e['kind']=='result_review')
    with pytest.raises(ValueError,match='kind cannot change'):
        vote(ledger,t,body,'fixture-agent','human',review_supersedes=first['payload']['vote_sha256'])
    assert ledger.snapshot()['targets'][0]['result']['review_provenance']['review_counts']['human']==0


@pytest.mark.parametrize('label',['mixed','inconclusive','not_adjudicable'])
def test_nonbinary_outcomes_survive_registration_and_public_view_without_binary_scores(tmp_path,label):
    ledger,t,ids=registered(tmp_path)
    for ident,model in zip(ids,('A','B')):finish(ledger,ident,returned_model=model+'-fixed')
    body=result(t,label);vote(ledger,t,body,'fixture-one');vote(ledger,t,body,'fixture-two')
    target=ledger.snapshot()['targets'][0]
    assert target['status']=='adjudicated' and target['result']['label']==label
    assert result_view(target)['label']==('inconclusive' if label=='not_adjudicable' else label)
    metrics=build_metrics(ledger.snapshot())
    assert metrics['common_tasks']==0 and all(row['brier'] is None for row in metrics['by_model'])


def test_conflicting_typed_reviews_stay_pending_until_explicit_own_correction(tmp_path):
    ledger,t,_=registered(tmp_path);yes=result(t);no={**yes,'label':'failure'}
    vote(ledger,t,yes,'fixture-one');vote(ledger,t,no,'fixture-two');vote(ledger,t,yes,'fixture-three')
    snap=ledger.snapshot();assert snap['targets'][0]['status']=='announced'
    assert snap['targets'][0]['result']['verification_status']=='conflicting_reviews'
    assert result_view(snap['targets'][0])['label'] is None
    original=next(e for e in ledger.events() if e['kind']=='result_review' and e['payload']['reviewer_id']=='fixture-two')
    with pytest.raises(ValueError,match='latest own'):vote(ledger,t,yes,'fixture-two',review_supersedes='0'*64)
    assert vote(ledger,t,yes,'fixture-two',review_supersedes=original['payload']['vote_sha256'])=='adjudicated'
    assert original in ledger.events()
    assert ledger.snapshot()['targets'][0]['result']['review_provenance']['review_counts']['ai']==3


def test_result_correction_suspends_old_score_and_retains_previous_result(tmp_path):
    ledger,t,ids=registered(tmp_path)
    for ident,model in zip(ids,('A','B')):finish(ledger,ident,returned_model=model+'-fixed')
    body=result(t);vote(ledger,t,body,'fixture-one');vote(ledger,t,body,'fixture-two')
    previous=ledger.snapshot()['targets'][0]['result']['result_sha256']
    assert build_metrics(ledger.snapshot())['common_tasks']==1
    changed={**body,'label':'mixed'}
    vote(ledger,t,changed,'fixture-one',supersedes=previous)
    pending=ledger.snapshot()['targets'][0]
    assert pending['status']=='announced' and pending['previous_adjudicated_result']['label']=='success'
    assert build_metrics(ledger.snapshot())['common_tasks']==0
    assert cli.outcome_version(pending)==previous
    vote(ledger,t,changed,'fixture-two',supersedes=previous)
    assert ledger.snapshot()['targets'][0]['result']['label']=='mixed'


def test_prepare_is_query_only_and_does_not_declare_a_reviewer(tmp_path,monkeypatch):
    ledger,t,_=registered(tmp_path/'ledger');before=ledger.path.read_bytes();output=tmp_path/'worksheet.json'
    monkeypatch.setattr(ArenaLedger,'__init__',lambda *a,**k:pytest.fail('Prepare must not initialize the ledger'))
    status=cli.prepare(ledger.path.parent,t['id'],output)
    r=cli.read(output)
    assert r['reviewer_id'] is r['reviewer_kind'] is r['review_note'] is None
    assert r['result']['label'] is r['result']['source_sha256'] is None
    assert r['result']['target_binding']==result_target_binding(t)
    assert status['ledger_writes']==status['human_reviews_created']==0 and ledger.path.read_bytes()==before
    with pytest.raises(FileExistsError):cli.prepare(ledger.path.parent,t['id'],output)


@pytest.fixture
def submitted_files(tmp_path):
    ledger,t,_=registered(tmp_path/'ledger');review=tmp_path/'review.json';source=tmp_path/'source.txt';acq=tmp_path/'source-manifest.json'
    cli.prepare(ledger.path.parent,t['id'],review)
    value=cli.read(review);value.update(reviewer_id='fixture-ai-one',reviewer_kind='ai',review_note='Isolated original source check')
    source.write_bytes(b'Synthetic source bytes; not an actual clinical result')
    value['result'].update(label='success',source_url='https://synthetic-test.example/result',
        source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),first_public_at=datetime.now(timezone.utc).isoformat(),
        endpoint_match_note='Exact synthetic fixture target checked')
    review.write_text(json.dumps(value))
    manifest={'schema_version':'predome-outcome-acquisition-v1','assets':[{'source_url':value['result']['source_url'],
        'sha256':value['result']['source_sha256'],'bytes':source.stat().st_size,'retrieved_at':datetime.now(timezone.utc).isoformat()}]}
    acq.write_text(json.dumps(manifest))
    return ledger,t,review,source,acq,value,manifest


def test_submit_requires_real_bytes_and_bound_acquisition_record(submitted_files):
    ledger,t,review,source,acq,value,_=submitted_files
    first=cli.submit(ledger.path.parent,review,source,acq)
    assert first['status']=='announced_pending_adjudication' and first['reviewer_kind']=='ai'
    value['reviewer_id']='fixture-ai-two';value['review_note']='Second independent fixture source check'
    review.write_text(json.dumps(value));second=cli.submit(ledger.path.parent,review,source,acq)
    assert second['status']=='adjudicated' and second['review_provenance']['review_counts']['human']==0


@pytest.mark.parametrize('fault',['missing_kind','missing_note','target','source_bytes','source_url','source_hash','source_size','future_retrieval','stale_result'])
def test_invalid_submissions_fail_before_a_writable_ledger_is_initialized(submitted_files,monkeypatch,fault):
    ledger,_,review,source,acq,value,manifest=submitted_files
    if fault=='missing_kind':value['reviewer_kind']=None
    elif fault=='missing_note':value['review_note']=None
    elif fault=='target':value['result']['target_binding']['population']='wrong'
    elif fault=='source_bytes':source.write_bytes(b'changed')
    elif fault=='source_url':manifest['assets'][0]['source_url']='https://other.example/'
    elif fault=='source_hash':manifest['assets'][0]['sha256']='a'*64
    elif fault=='source_size':manifest['assets'][0]['bytes']+=1
    elif fault=='future_retrieval':manifest['assets'][0]['retrieved_at']='2099-01-01T00:00:00+00:00'
    elif fault=='stale_result':value['supersedes_result_sha256']='a'*64
    review.write_text(json.dumps(value));acq.write_text(json.dumps(manifest));before=ledger.events()
    monkeypatch.setattr(ArenaLedger,'__init__',lambda *a,**k:pytest.fail('Invalid submission must not initialize the store'))
    with pytest.raises(ValueError):cli.submit(ledger.path.parent,review,source,acq)
    assert ledger.events()==before


def test_real_ledger_review_projection_through_public_api_is_pending_then_mixed(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from emeris_live import arena_public as api
    ledger,t,ids=registered(tmp_path)
    for ident,model in zip(ids,('A','B')):finish(ledger,ident,returned_model=model+'-fixed')
    monkeypatch.setattr(api,'read_frozen',lambda store:ledger.snapshot())
    monkeypatch.setattr(api,'read_archived',lambda store:([],[],{}))
    app=FastAPI();app.state.store=SimpleNamespace(path=tmp_path/'workbench.sqlite3');app.include_router(api.router)
    client=TestClient(app);body=result(t,'mixed')
    vote(ledger,t,body,'fixture-reviewer-one')
    first=client.get('/api/arena/public/targets/'+t['id']);assert first.status_code==200
    assert first.json()['target']['result']['label'] is None
    assert first.json()['target']['result']['status']=='announced'
    vote(ledger,t,body,'fixture-reviewer-two')
    response=client.get('/api/arena/public/targets/'+t['id']);assert response.status_code==200
    data=response.json();assert data['target']['result']['label']=='mixed'
    assert data['target']['result']['status']=='adjudicated'
    assert all(r['match'] is None for r in data['runs'])
    assert 'fixture-reviewer' not in response.text and 'review_provenance' not in response.text
    assert 'AI' in data['target']['result']['verification_note']
