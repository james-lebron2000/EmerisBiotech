"""Synthetic sponsor-resolution safety and recovery; no live registry requests."""
import hashlib
import json
from urllib.parse import parse_qs,urlsplit

import pytest

from vbt.market import Market
from vbt.market_coverage import refresh_candidates
from vbt.sponsor_resolution import (
 batch,init_schema,match_sponsor,proposals,query_name_from_listing,query_url,resolve,resolution_runs,
)


@pytest.fixture
def market(tmp_path):
 m=Market(tmp_path/'workspace',tmp_path/'state')
 add_candidate(m)
 return m


def add_candidate(m,ticker='SYN',name='Syntheticnova Therapeutics, Inc.',classification='candidate',**extra):
 row={'market':'US','ticker':ticker,'name':name,'classification':classification,
   'reason':'Synthetic company classification, not a real issuer','source':'Synthetic fixture',
   'evidence':[{'kind':'synthetic','text':'No live registry evidence'}],
   'observed':'2026-10-17T00:00:00+00:00'}
 row.update(extra);refresh_candidates(m,[row])


def payload(alias='Syntheticnova Therapeutics, Inc.',nct='NCT00000001',token=None):
 data={'studies':[{'protocolSection':{'identificationModule':{'nctId':nct},'sponsorCollaboratorsModule':{'leadSponsor':{'name':alias}}}}]}
 if token:data['nextPageToken']=token
 return json.dumps(data).encode()


@pytest.mark.parametrize(('name','market','expected'),[
 ('Syntheticnova Therapeutics, Inc. - Common Stock','US','syntheticnova therapeutics'),
 ('Syntheticnova Pharma plc - American Depositary Shares','US','syntheticnova pharma'),
 ('SYNTHETICNOVA BIO-B','HK','syntheticnova bio'),
 ('Syntheticnova Limited Ordinary Shares','US','syntheticnova'),
 ('Médicament Therapeutics S.A.','US','medicament therapeutics'),
 ('Atea Pharmaceuticals, Inc.','US','atea pharmaceuticals'),
 ('上海合成药业','A',None),('UCB SA','US',None),('Bio Pharma Ltd.','HK',None),
 ('ABC','US',None),('123456','A',None),('', 'US',None),
])
def test_conservative_query_extraction(name,market,expected):
 assert query_name_from_listing(name,market)==expected


def test_registry_queries_fixed_and_encoded_not_interpreted_as_url():
 url=query_url('syntheticnova therapeutics','opaque+/=token')
 assert urlsplit(url).hostname=='clinicaltrials.gov'
 q=parse_qs(urlsplit(url).query)
 assert q['pageSize']==['100'] and q['fields']==['NCTId,LeadSponsorName']
 assert q['query.spons']==['"syntheticnova therapeutics"']
 assert q['pageToken']==['opaque+/=token']
 with pytest.raises(ValueError):query_url('Synthetic " OR AREA[SponsorName]evil')
 with pytest.raises(ValueError):query_name_from_listing('Name','EU')


def test_match_never_uses_generic_pharma_words_or_fuzzy_substrings():
 assert match_sponsor('syntheticnova therapeutics','Syntheticnova Therapeutics, Inc.')=='normalized_exact'
 assert match_sponsor('syntheticnova therapeutics','Syntheticnova Therapeutics Global LLC')=='distinctive_token'
 assert match_sponsor('alpha omega pharma','Alpha Omega Biotechnology Ltd.')=='distinctive_token'
 assert match_sponsor('acme pharma','Acme Therapeutics LLC')=='unrelated'
 assert match_sponsor('syntheticnova pharma','Syntheticnovum Pharma')=='unrelated'
 assert match_sponsor('syntheticnova pharma','Syntheticnova Subsidiary Pharma')=='unrelated'
 assert match_sponsor('pharma','Pharma Inc.')=='unrelated'
 assert match_sponsor('syntheticnova pharma','Unrelated Pharma')=='unrelated'


def test_exact_suggestion_stays_pending_with_archived_provenance(market):
 raw=payload();seen=[]
 result=resolve(market,'US:SYN',fetch=lambda u:(seen.append(u) or raw))
 assert result['status']=='suggested' and result['proposal_count']==1
 rows=proposals(market,'US:SYN');assert len(rows)==1
 row=rows[0]
 assert row['status']=='pending' and row['match_type']=='normalized_exact'
 assert row['evidence_nct']==['NCT00000001'] and not row['candidate_changed']
 assert row['source_url']=='https://clinicaltrials.gov/study/NCT00000001'
 assert market.rows('companies')==[] and market.rows('trials')==[]
 info=json.loads((market.root/'captures'/result['run_id']/'0.json').read_text())
 assert info['url']==seen[0] and info['sha256']==hashlib.sha256(raw).hexdigest() and info['retrieved_at']
 assert (market.root/'captures'/result['run_id']/'0.bin').read_bytes()==raw


def test_unrelated_collaborator_hits_are_ambiguous_not_proposals(market):
 result=resolve(market,'US:SYN',fetch=lambda u:payload('Unrelated University'))
 assert result['status']=='ambiguous' and result['observation_count']==1 and result['proposal_count']==0
 assert proposals(market)==[]
 with market.db() as db:
  assert db.execute('SELECT match_type FROM sponsor_resolution_observations').fetchone()[0]=='unrelated'


def test_high_constraint_match_still_ambiguous_and_pending(market):
 result=resolve(market,'US:SYN',fetch=lambda u:payload('Syntheticnova Therapeutics Global, LLC'))
 assert result['status']=='ambiguous'
 assert proposals(market)[0]['status']=='pending'


def test_zero_and_schema_failure_are_distinct_and_old_suggestions_survive(market):
 first=resolve(market,'US:SYN',fetch=lambda u:payload())
 assert first['status']=='suggested'
 empty=resolve(market,'US:SYN',fetch=lambda u:b'{"studies":[]}')
 assert empty['status']=='zero' and len(proposals(market))==1
 failed=resolve(market,'US:SYN',fetch=lambda u:b'{"unexpected":[]}')
 assert failed['status']=='failed' and len(proposals(market))==1
 assert (market.root/'captures'/failed['run_id']/'0.bin').exists()
 assert {r['status'] for r in resolution_runs(market)}=={'suggested','zero','failed'}


def test_failure_on_later_page_does_not_publish_unfinished_proposals(market):
 calls=[]
 def fetch(u):
  calls.append(u)
  if len(calls)==1:return payload(token='second')
  raise OSError('Synthetic outage')
 result=resolve(market,'US:SYN',fetch=fetch)
 assert result['status']=='failed' and result['page_count']==1 and result['observation_count']==1
 assert proposals(market)==[]
 assert result['error'].endswith('Synthetic outage')


def test_pagination_has_hard_three_page_cap_and_explicit_partial(market):
 calls=[]
 def fetch(u):
  calls.append(u);return payload(nct='NCT'+str(len(calls)).zfill(8),token='next'+str(len(calls)))
 result=resolve(market,'US:SYN',fetch=fetch)
 assert len(calls)==3 and result['status']=='partial' and result['truncated']
 row=proposals(market)[0]
 assert row['evidence_nct']==['NCT00000001','NCT00000002','NCT00000003']
 assert row['resolution_status']=='partial' and row['status']=='pending'
 with pytest.raises(ValueError):resolve(market,'US:SYN',fetch=fetch,max_pages=4)


def test_repeated_page_token_fails_and_raw_captures_remain(market):
 result=resolve(market,'US:SYN',fetch=lambda u:payload(token='loop'))
 assert result['status']=='failed' and result['page_count']==2
 assert 'Repeated' in result['error'] and proposals(market)==[]


@pytest.mark.parametrize('raw',[
 b'{bad json',b'{"studies":[{}]}',payload(nct='../bad'),payload(alias=''),
 json.dumps({'studies':[{}]*101}).encode(),b'x'*2_000_001,
])
def test_invalid_evidence_cannot_become_sponsor_mapping(market,raw):
 result=resolve(market,'US:SYN',fetch=lambda u:raw)
 assert result['status']=='failed' and proposals(market)==[] and market.rows('companies')==[]


def test_idempotent_suggestions_preserve_manual_status_and_created_time(market):
 resolve(market,'US:SYN',fetch=lambda u:payload())
 row=proposals(market)[0]
 with market.db() as db:db.execute("UPDATE sponsor_proposals SET status='rejected' WHERE id=?",(row['id'],))
 resolve(market,'US:SYN',fetch=lambda u:payload())
 again=proposals(market)[0]
 assert again['id']==row['id'] and again['created']==row['created'] and again['status']=='rejected'
 assert again['run_id']==row['run_id']  # reviewed evidence is frozen
 assert len(proposals(market))==1


def test_candidate_hash_change_exposes_old_proposal_as_stale(market):
 resolve(market,'US:SYN',fetch=lambda u:payload())
 add_candidate(market,name='Syntheticnova Therapeutics, Inc.',reason='Updated synthetic identity evidence',observed='2026-10-17T02:00:00+00:00')
 assert proposals(market)[0]['candidate_changed']
 resolve(market,'US:SYN',fetch=lambda u:payload())
 rows=proposals(market)
 assert len(rows)==2 and sum(r['candidate_changed'] for r in rows)==1


def test_blocked_names_and_configured_issuers_make_no_requests(market):
 add_candidate(market,ticker='EXCL',name='Syntheticnova Pharma',classification='excluded')
 market.add_company('US','SYN','Syntheticnova',['Exact sponsor'],'Synthetic mapping')
 def fail(u):pytest.fail('Blocked identity must not query')
 assert resolve(market,'US:EXCL',fetch=fail)['status']=='blocked'
 assert resolve(market,'US:SYN',fetch=fail)['status']=='blocked'


def test_non_latin_identity_blocked_without_translation(market):
 add_candidate(market,ticker='600001',name='上海合成药业',market='A')
 assert resolve(market,'A:600001',fetch=lambda u:pytest.fail('No English identity'))['status']=='blocked'


def test_persistent_lease_prevents_competing_resolution_and_stale_publication(market):
 key='sponsor-resolution:US:SYN';assert market.claim(key)
 assert resolve(market,'US:SYN',fetch=lambda u:pytest.fail('Lease active'))['status']=='busy'
 market.release(key)
 def fetch(u):
  with market.db() as db:db.execute('UPDATE leases SET owner=? WHERE key=?',('replacement-owner',key))
  return payload()
 result=resolve(market,'US:SYN',fetch=fetch)
 assert result['status']=='failed' and proposals(market)==[]
 with market.db() as db:assert db.execute('SELECT owner FROM leases WHERE key=?',(key,)).fetchone()[0]=='replacement-owner'


def test_batch_bound_fairness_and_no_automatic_mapping(market):
 add_candidate(market,ticker='SYN2');add_candidate(market,ticker='SYN3')
 add_candidate(market,ticker='OUT',classification='excluded')
 first=batch(market,limit=2,fetch=lambda u:payload())
 assert len(first)==2
 second=batch(market,limit=1,fetch=lambda u:payload())
 assert second[0]['candidate_id']=='US:SYN3'
 assert market.rows('companies')==[]
 with pytest.raises(ValueError):batch(market,limit=51)


def test_unknown_candidate_and_invalid_read_filters_rejected(market):
 with pytest.raises(ValueError):resolve(market,'US:MISSING',fetch=lambda u:pytest.fail('Unknown candidate'))
 with pytest.raises(ValueError):proposals(market,status='mapped')
 with pytest.raises(ValueError):resolution_runs(market,limit=1000)


def test_interrupted_resolution_recovers_only_after_lease_expiry(market):
 init_schema(market);key='sponsor-resolution:US:SYN';market.claim(key)
 owner=market.lease_tokens()[key]
 with market.db() as db:
  db.execute('''INSERT INTO sponsor_resolution_runs
   (id,candidate_id,candidate_hash,query,started,status,lease_owner)
   VALUES ('synthetic-old','US:SYN','fixture','syntheticnova','2026-01-01T00:00:00+00:00','running',?)''',(owner,))
 assert resolution_runs(market)[0]['status']=='running'
 with market.db() as db:db.execute('UPDATE leases SET until=0 WHERE key=?',(key,))
 row=resolution_runs(market)[0]
 assert row['status']=='interrupted' and row['finished'] and 'retry required' in row['error']
 assert proposals(market)==[]


def test_final_fetch_cancellation_preserves_capture_without_publishing_proposals(market):
 state={'cancelled':False}
 def fetch(url):
  state['cancelled']=True
  return payload()
 result=resolve(market,'US:SYN',fetch=fetch,cancelled=lambda:state['cancelled'])
 assert result['status']=='cancelled' and result['page_count']==1
 assert proposals(market)==[] and result['proposal_count']==0
 assert (market.root/'captures'/result['run_id']/'0.bin').exists()
 assert market.claim('sponsor-resolution:US:SYN')  # owner was released


def test_start_callback_receives_durable_run_id_before_network(market):
 order=[]
 def started(rid):
  with market.db() as db:
   assert db.execute('SELECT status FROM sponsor_resolution_runs WHERE id=?',(rid,)).fetchone()[0]=='running'
  order.append(('start',rid))
 def fetch(url):
  assert order and order[0][0]=='start'
  order.append(('fetch',url));return payload()
 result=resolve(market,'US:SYN',fetch=fetch,on_start=started)
 assert result['status']=='suggested' and order[0]==('start',result['run_id'])
 assert [kind for kind,_ in order]==['start','fetch']


def test_cancel_after_final_lease_check_cannot_publish(market,monkeypatch):
 resolve(market,'US:SYN',fetch=lambda u:payload())
 before=proposals(market)[0]
 state={'fetched':False,'cancelled':False};original=market.touch
 def touch(key,token=None):
  original(key,token)
  if state['fetched']:state['cancelled']=True
 def fetch(url):
  state['fetched']=True;return payload(nct='NCT00000002')
 monkeypatch.setattr(market,'touch',touch)
 result=resolve(market,'US:SYN',fetch=fetch,cancelled=lambda:state['cancelled'])
 after=proposals(market)[0]
 assert result['status']=='cancelled'
 assert after['run_id']==before['run_id'] and after['evidence_nct']==['NCT00000001']
