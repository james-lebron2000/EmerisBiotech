"""Source coverage regression with synthetic HTML; no clinical adjudications."""
from copy import deepcopy
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest
from emeris_live import arena_public,official_intake as official,intake
from emeris_live.store import Store


def formal_snapshot(monkeypatch):
    hosts=['www.roche.com','assets.roche.com','cdn.pfizer.com','investor.lilly.com',
           'www.bms.com','investors.biogen.com','www.astrazeneca.com','www.sec.gov']
    targets=[{'id':f'case-{n:02}','cohort_id':'formal','nct_id':f'NCT{n:08}',
        'trial_name':f'TRIAL-{n}','drug':f'drug{n}',
        'readout':{'source_url':f'https://{hosts[n%len(hosts)]}/public/guidance-{n}',
                   'source_sha256':'a'*64,'guidance_as_of':'2026-01-01'}} for n in range(12)]
    snapshot={'cohorts':[{'id':'formal','registered_at':'2026-01-01T00:00:00Z',
        'protocol':{'season_kind':'registered_12_project_season'}}],'targets':targets}
    monkeypatch.setattr(arena_public,'read_frozen',lambda _:deepcopy(snapshot))
    return snapshot


def test_all_registered_targets_have_approved_watch_seeds_without_runtime_creation(tmp_path,monkeypatch):
    snap=formal_snapshot(monkeypatch)
    records=official.watch_config(SimpleNamespace(path=tmp_path/'absent'))
    selected=[r for r in records if r.get('configuration_basis')=='verified_formal_registration']
    assert {r['nct_id'] for r in selected}=={t['nct_id'] for t in snap['targets']}
    assert len(selected)==12 and not (tmp_path/'absent').exists()
    assert all(r['sources'] and all(official.allowed(s['url']) for s in r['sources']) for r in selected)


@pytest.mark.parametrize('field,value',[
    ('source_url','https://www.roche.com.attacker.example/result'),
    ('source_url','https://127.0.0.1/'),('source_sha256','invalid'),
    ('guidance_as_of','2999-01-01'),('guidance_as_of','2026-99-99')])
def test_invalid_formal_source_fails_before_network(tmp_path,monkeypatch,field,value):
    snap=formal_snapshot(monkeypatch);snap['targets'][0]['readout'][field]=value
    store=Store(tmp_path/'runtime')
    r=official.collect_official(store,[snap['targets'][0]['nct_id']],listings=[],
        fetcher=lambda _:pytest.fail('Invalid registration cannot trigger a fetch'))
    assert r['failures'][0]['category']=='integrity_error'
    assert r['new_candidates']==0 and not (store.path/'arena.sqlite3').exists()


def test_formal_identity_wins_without_inheriting_conflicting_legacy_drug(monkeypatch,tmp_path):
    snap=formal_snapshot(monkeypatch);t=snap['targets'][0]
    monkeypatch.setattr(official,'tracked_config',lambda:[{'nct_id':t['nct_id'],
        'trial':'different-trial','drug':'wrong-drug','sources':[{'url':'https://www.gsk.com/wrong'}]}])
    r=next(r for r in official.watch_config(SimpleNamespace(path=tmp_path)) if r['nct_id']==t['nct_id'])
    assert r['trial']==t['trial_name'] and r['drug']==t['drug']
    assert [s['url'] for s in r['sources']]==[t['readout']['source_url']]


def test_requested_order_and_document_limit_are_reported_without_false_retrieval(tmp_path):
    store=Store(tmp_path/'runtime');records=[]
    for n in range(17):
        records.append({'nct_id':f'NCT{n:08}','trial':f'TRIAL-{n}','drug':f'drug{n}',
            'sources':[{'url':f'https://www.roche.com/media/releases/trial-{n}'}]})
    ids=[r['nct_id'] for r in reversed(records)]
    r=official.collect_official(store,ids,config=records,listings=[],fetcher=lambda _:b'<html>Guidance only</html>')
    assert r['configured_trial_ids']==ids
    assert r['scheduled_document_urls']==[s['sources'][0]['url'] for s in reversed(records[1:])]
    assert r['deferred_document_urls']==[records[0]['sources'][0]['url']]
    assert not r['all_discovered_documents_scheduled']
    assert r['trials_without_retrieved_configured_source']==[records[0]['nct_id']]
    assert len(r['trials_with_retrieved_configured_source'])==16 and r['new_candidates']==0


def test_access_denied_is_neither_coverage_nor_negative_outcome(tmp_path):
    store=Store(tmp_path/'runtime')
    record={'nct_id':'NCT01234567','trial':'TARGET','drug':'drug',
        'sources':[{'url':'https://investors.biogen.com/node/example/pdf'}]}
    def denied(url):raise HTTPError(url,403,'Forbidden',{},None)
    r=official.collect_official(store,[record['nct_id']],config=[record],listings=[],fetcher=denied)
    assert r['configured_trial_ids']==[record['nct_id']] and r['checked_document_urls']==[]
    assert r['trials_without_retrieved_configured_source']==[record['nct_id']]
    assert r['failures'][0]['category']=='access_denied' and r['new_candidates']==0
    assert intake.list_candidates(store)==[]


@pytest.mark.parametrize('host',['www.roche.com','cdn.pfizer.com','investors.biogen.com','www.sanofi.com','www.sec.gov'])
def test_new_hosts_are_exact_tls_destinations_only(host):
    assert official.allowed('https://'+host+'/public')
    for url in ('http://'+host+'/public','https://'+host+'.attacker.example/public',
                'https://user@'+host+'/public','https://'+host+':8443/public'):
        assert not official.allowed(url)
