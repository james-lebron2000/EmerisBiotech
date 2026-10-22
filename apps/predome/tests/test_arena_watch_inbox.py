"""Synthetic watch coverage and acquired-source preparation; no network calls."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from emeris_live.arena_store import ArenaLedger
from emeris_live.arena_watch import current_targets,current_trial_ids
from emeris_live import intake,official_intake
from emeris_live.store import Store
from test_arena_core import spec

sys.path.insert(0,str(Path(__file__).parents[1]/'scripts'))
import arena_outcome_inbox as inbox
import arena_result_review


def formal(root,cohort='formal-current'):
    ledger=ArenaLedger(root);t,slots=spec()
    ledger.register(cohort,[t],slots,{'season_kind':'registered_12_project_season'})
    return ledger,ledger.snapshot()['targets'][0]


@pytest.fixture
def acquired(tmp_path):
    store=Store(tmp_path/'runtime');ledger,t=formal(store.path);intake.initialize(store)
    url='https://clinicaltrials.gov/api/v2/studies/'+t['nct_id']
    value={'hasResults':True,'protocolSection':{'identificationModule':{'nctId':t['nct_id']}}}
    raw=json.dumps(value).encode()
    cid,_=intake.save_candidate(store,t['nct_id'],'reported_results_candidate',url,raw,intake.registry_summary(value))
    return store,ledger,t,cid,raw,url


def test_current_watch_is_readonly_and_never_initializes_missing_arena(tmp_path,monkeypatch):
    store=SimpleNamespace(path=tmp_path)
    monkeypatch.setattr(ArenaLedger,'__init__',lambda *a,**k:pytest.fail('Watch cannot initialize ledger'))
    assert current_trial_ids(store)==[] and not (tmp_path/'arena.sqlite3').exists()


def test_current_watch_uses_latest_active_cohort_not_withdrawn_or_workflow():
    snap={'cohorts':[{'id':'old','registered_at':'2026-01-01T00:00:00Z','protocol':{'season_kind':'registered_12_project_season'}},
        {'id':'current','registered_at':'2026-02-01T00:00:00Z','protocol':{'season_kind':'registered_12_project_season'}},
        {'id':'withdrawn','registered_at':'2026-03-01T00:00:00Z','execution_status':'withdrawn_before_execution','protocol':{'season_kind':'registered_12_project_season'}},
        {'id':'workflow','registered_at':'2026-04-01T00:00:00Z','protocol':{'season_kind':'workflow'}}],
        'targets':[{'id':v,'cohort_id':v,'nct_id':'NCT01234567'} for v in ('old','current','withdrawn','workflow')]}
    assert [t['id'] for t in current_targets(snap)]==['current']
    snap['targets'][1]['nct_id']='https://localhost'
    with pytest.raises(ValueError):current_targets(snap)


def test_registered_formal_ids_precede_legacy_watch_without_losing_published_ids(tmp_path,monkeypatch):
    store=Store(tmp_path/'runtime');_,t=formal(store.path)
    from emeris_live import product,platform_store
    monkeypatch.setattr(product,'build_product',lambda:{'upcoming':[{'nct_id':'NCT11111111'}]})
    monkeypatch.setattr(platform_store.PlatformStore,'list_public_snapshots',lambda self:[{'record':{'trial_id':'NCT22222222'}},{'record':{'trial_id':t['nct_id']}}])
    assert intake.tracked_trial_ids(store)==[t['nct_id'],'NCT11111111','NCT22222222']


def test_intake_bound_reports_every_deferred_id_instead_of_silent_truncation(tmp_path):
    store=Store(tmp_path/'runtime');ids=['NCT'+str(n).zfill(8) for n in range(31)]
    def fetcher(url):
        if 'clinicaltrials.gov' in url:return json.dumps({'protocolSection':{'identificationModule':{'nctId':url.rsplit('/',1)[-1]}}}).encode()
        return b'{"resultList":{"result":[]}}'
    report=intake.run_intake(store,ids,fetcher,discover=False)
    assert report['requested_ncts']==ids[:30] and report['deferred_ncts']==ids[30:]
    assert report['sources_checked']==60 and report['status']=='partial'
    assert report['complete_requested_coverage'] is False and report['auto_approved']==0
    with pytest.raises(ValueError):intake.run_intake(store,ids[:30]+['malformed'],lambda _:pytest.fail('Invalid tail cannot be hidden'))


def test_official_configuration_gap_is_explicit_not_a_false_no_result(tmp_path):
    store=Store(tmp_path/'runtime')
    r=official_intake.collect_official(store,['NCT11111111'],config=[],listings=[],fetcher=lambda _:pytest.fail('No configured source'))
    assert r['configured_trial_ids']==[] and r['unconfigured_trial_ids']==['NCT11111111']
    assert r['matching_is_not_result_verification']


def test_inbox_reads_only_acquired_data_and_preserves_null_outcomes(acquired,monkeypatch):
    store,ledger,t,cid,raw,url=acquired;before=ledger.events();key=(store.path/'master.key').read_bytes()
    monkeypatch.setattr(Store,'__init__',lambda *a,**k:pytest.fail('Cannot initialize runtime'))
    monkeypatch.setattr(ArenaLedger,'__init__',lambda *a,**k:pytest.fail('Cannot initialize Arena'))
    r=inbox.build(store.path)
    assert r['targets_with_acquired_sources']==1 and r['network_calls']==r['ledger_writes']==r['actual_outcomes_created']==0
    c=r['targets'][0]['candidates'][0]
    assert c['id']==cid and c['source_says_results_available'] is True and c['outcome_label'] is None
    assert ledger.events()==before and (store.path/'master.key').read_bytes()==key


def test_absent_acquired_sources_never_mean_no_clinical_result(tmp_path):
    store=Store(tmp_path/'runtime');formal(store.path);intake.initialize(store)
    r=inbox.build(store.path)
    assert r['targets'][0]['coverage']=='no_matching_acquired_source'
    assert r['absence_is_not_evidence_of_no_result'] and r['targets'][0]['candidates']==[]


def test_inbox_checks_source_bytes_not_mutable_result_claims(acquired):
    store,ledger,t,cid,raw,url=acquired
    with store.connect() as db:
        row=db.execute('SELECT payload FROM intake_candidates WHERE id=?',(cid,)).fetchone()
        value=store.unpack(row['payload']);value['structured'].update(label='failure',has_results=False)
        db.execute('UPDATE intake_candidates SET payload=? WHERE id=?',(store.pack(value),cid))
    c=inbox.build(store.path)['targets'][0]['candidates'][0]
    assert c['source_says_results_available'] is True and c['outcome_label'] is None


@pytest.mark.parametrize('field,value',[('nct_id','NCT99999999'),('kind','registry_update'),('raw_text','corrupted source')])
def test_tampered_candidate_fails_closed(acquired,field,value):
    store,ledger,t,cid,raw,url=acquired
    with store.connect() as db:
        row=db.execute('SELECT payload FROM intake_candidates WHERE id=?',(cid,)).fetchone()
        body=store.unpack(row['payload']);body[field]=value
        db.execute('UPDATE intake_candidates SET payload=? WHERE id=?',(store.pack(body),cid))
    with pytest.raises(ValueError):inbox.build(store.path)


def test_candidate_for_different_trial_cannot_be_prepared(acquired,tmp_path):
    store,ledger,t,cid,raw,url=acquired
    with pytest.raises(ValueError,match='Unknown current formal target'):inbox.export(store.path,'wrong-target',cid,tmp_path/'out')
    assert not (tmp_path/'out').exists()


def test_export_reuses_original_bytes_but_cannot_submit_an_invented_label(acquired,tmp_path):
    store,ledger,t,cid,raw,url=acquired;before=ledger.events();out=tmp_path/'review-kit'
    receipt=inbox.export(store.path,t['id'],cid,out)
    assert (out/'source.bin').read_bytes()==raw
    worksheet=json.loads((out/'review.json').read_text());manifest=json.loads((out/'source-manifest.json').read_text())
    assert worksheet['reviewer_id'] is worksheet['reviewer_kind'] is worksheet['result']['label'] is worksheet['result']['first_public_at'] is None
    assert worksheet['result']['source_sha256']==hashlib.sha256(raw).hexdigest()
    assert manifest['assets'][0]['sha256']==worksheet['result']['source_sha256']
    assert receipt['ledger_writes']==0 and receipt['actual_outcomes_created']==0
    with pytest.raises(ValueError):arena_result_review.submit(store.path,out/'review.json',out/'source.bin',out/'source-manifest.json')
    with pytest.raises(FileExistsError):inbox.export(store.path,t['id'],cid,out)
    assert ledger.events()==before and (out.stat().st_mode&0o777)==0o700


def test_original_archive_corruption_cannot_be_exported(acquired):
    store,ledger,t,cid,raw,url=acquired
    with store.connect() as db:
        db.execute('INSERT INTO intake_source_blobs VALUES(?,?)',(hashlib.sha256(raw).hexdigest(),store.pack({'bytes_b64':'Y29ycnVwdA=='})))
    with pytest.raises(ValueError,match='integrity'):inbox.build(store.path)


def test_arena_only_collector_requires_existing_cohort_before_runtime_initialization(tmp_path,monkeypatch):
    monkeypatch.setattr(Store,'__init__',lambda *a,**k:pytest.fail('Cannot create accidental runtime'))
    with pytest.raises(ValueError,match='No existing current'):
        intake.main(['--data-dir',str(tmp_path/'missing'),'--arena-only'])
    assert not (tmp_path/'missing').exists()


def test_bounded_arena_only_collector_preserves_explicit_source_options(acquired,monkeypatch):
    store,ledger,t,*_=acquired;calls=[]
    def collect(given,ids,**options):
        calls.append((ids,options));return {'status':'synthetic-no-network','auto_approved':0}
    monkeypatch.setattr(intake,'run_intake',collect)
    r=intake.main(['--data-dir',str(store.path),'--arena-only','--skip-discovery','--skip-official'])
    assert calls==[([t['nct_id']],{'discover':False,'official':False})]
    assert r['auto_approved']==0


def test_inbox_cannot_silently_report_missing_runtime_as_no_results(tmp_path):
    with pytest.raises(ValueError,match='Existing runtime'):inbox.build(tmp_path/'missing')
