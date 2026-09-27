import json,threading,urllib.request,urllib.error
from http.server import ThreadingHTTPServer
from urllib.parse import urlencode
import pytest
from vbt.market import Market
from vbt import market_sources as sources
from vbt.workbench import Workbench,page,handler

@pytest.fixture
def market(tmp_path):
 m=Market(tmp_path/'work',tmp_path/'state')
 m.add_company('US','TEST','Test pharma',['Test Sponsor'],'Synthetic test fixture, not a real issuer')
 return m

def payload(status='RECRUITING',sponsor='Test Sponsor',token=None,nct='NCT00000001'):
 d={'studies':[{'protocolSection':{'identificationModule':{'nctId':nct,'briefTitle':'Synthetic <trial>'},'sponsorCollaboratorsModule':{'leadSponsor':{'name':sponsor}},'statusModule':{'overallStatus':status,'lastUpdatePostDateStruct':{'date':'2026-01-01'}},'armsInterventionsModule':{'interventions':[{'type':'DRUG','name':'Test drug'}]},'designModule':{'phases':['PHASE2']}}}]}
 if token:d['nextPageToken']=token
 return json.dumps(d).encode()

def test_observations_dedup_changes_and_source_archive(market):
 market.sync_company('US:TEST',lambda u:payload())
 market.sync_company('US:TEST',lambda u:payload())
 assert len(market.rows('trials'))==1 and len(market.rows('changes'))==1
 market.sync_company('US:TEST',lambda u:payload('COMPLETED'))
 changes=market.rows('changes');assert len(changes)==2
 assert json.loads(changes[-1]['before_json'])['status']=='RECRUITING'
 assert list((market.root/'captures').glob('*/*.json'))
 assert 'success_probability' not in json.loads(changes[-1]['after_json'])

def test_failure_retains_records(market):
 market.sync_company('US:TEST',lambda u:payload())
 def fail(u):raise OSError('source unavailable')
 market.sync_company('US:TEST',fail)
 assert len(market.rows('trials'))==1
 assert market.rows('runs')[-1]['status']=='failed'
 assert Market(market.root.parent.parent,market.state).rows('trials')

def test_pagination_and_partial(market):
 calls=[]
 def fetch(u):
  calls.append(u)
  return payload(token='next') if len(calls)==1 else payload(nct='NCT00000002')
 market.sync_company('US:TEST',fetch)
 assert len(market.rows('trials'))==2 and 'pageToken=next' in calls[-1]
 market.sync_company('US:TEST',lambda u:payload(token='another'),max_pages=1)
 assert market.rows('runs')[-1]['status']=='partial'

def test_no_false_sponsor_attribution(market):
 market.sync_company('US:TEST',lambda u:payload(sponsor='Unrelated Sponsor'))
 assert market.rows('trials')==[]

def test_duplicate_mapping_rejected(market):
 import sqlite3
 with pytest.raises(sqlite3.IntegrityError):market.add_company('US','TEST','Other',['Different'],'not approved')
 assert json.loads(market.rows('companies')[0]['aliases'])==['Test Sponsor']

def test_persistent_lease(market):
 assert market.claim('trials:US:TEST')
 assert market.sync_company('US:TEST',lambda u:pytest.fail('must not fetch')) is None
 market.release('trials:US:TEST')
 assert market.claim('trials:US:TEST')

@pytest.mark.parametrize('url',['http://clinicaltrials.gov/','https://127.0.0.1/','https://clinicaltrials.gov.evil.test/','https://user:pass@clinicaltrials.gov/','https://clinicaltrials.gov:8443/'])
def test_network_scope(url):
 with pytest.raises(ValueError):sources.validate_url(url)

def test_universe_atomic_replacement(market):
 good=json.dumps({'fields':['name','ticker','exchange'],'data':[['Test','TEST','NYSE']]}).encode()
 market.refresh_universe('US',lambda u:good)
 market.refresh_universe('US',lambda u:b'{"error":"changed"}')
 assert len(market.rows('listings'))==1 and market.rows('runs')[-1]['status']=='failed'

def test_incomplete_universe_rejected(market):
 incomplete=json.dumps({'result':[{'A_STOCK_CODE':'600000','COMPANY_ABBR':'Test'}],'pageHelp':{'total':2}}).encode()
 market.refresh_universe('SH',lambda u:incomplete)
 assert not market.rows('listings') and market.rows('runs')[-1]['status']=='failed'

def test_nasdaq_excludes_fund_test_and_footer():
 raw=b'Symbol|Security Name|Test Issue|ETF\nTEST|Test|N|N\nFUND|Fund|N|Y\nFAKE|Fake|Y|N\nFile Creation Time: 1|||\n'
 rows,total=sources.parse_universe('USNASDAQ',raw)
 assert [r['ticker'] for r in rows]==['TEST']

def test_scheduler_disabled_and_due(market,monkeypatch):
 calls=[]
 monkeypatch.setattr(market,'refresh_universe',lambda s:calls.append(s))
 monkeypatch.setattr(market,'sync_company',lambda s:calls.append(s))
 market.tick();assert calls==[]
 market.schedule(True);market.tick();assert 'US:TEST' in calls and 'HK' in calls

def test_web_read_and_authenticated_write(tmp_path):
 app=Workbench(tmp_path/'w',tmp_path/'state')
 assert '市场与管线雷达' in page(app,'/market',{})
 server=ThreadingHTTPServer(('127.0.0.1',0),handler(app));threading.Thread(target=server.serve_forever,daemon=True).start();base=f'http://127.0.0.1:{server.server_port}'
 try:
  data=urlencode(dict(token=app.token,market='US',ticker='TEST',name='<script>',aliases='Test Sponsor',provenance='Synthetic test')).encode()
  with pytest.raises(urllib.error.HTTPError):urllib.request.urlopen(urllib.request.Request(base+'/market/company',data=data))
  result=urllib.request.urlopen(urllib.request.Request(base+'/market/company',data=data,headers={'Origin':base}))
  text=result.read().decode();assert '&lt;script&gt;' in text and '<script>' not in text
  with pytest.raises(urllib.error.HTTPError) as ex:urllib.request.urlopen(urllib.request.Request(base+'/market/company',data=data,headers={'Origin':base}))
  assert ex.value.code==400
 finally:server.shutdown();server.server_close()

def test_query_filters_and_invalid_phase_no_hits(market):
 market.sync_company('US:TEST',lambda u:payload())
 assert market.search_trials(market='US',query='Test drug',phase='PHASE2',status='RECRUITING')[1]==1
 assert market.search_trials(market='HK')[1]==0
 assert market.search_trials(phase='PHASE3')[1]==0
 assert market.search_trials(offset=200)[0]==[]

def test_expired_lease_recovers_interrupted_run(market):
 market.claim('trials:US:TEST');rid=market.begin('ClinicalTrials.gov','US:TEST')
 with market.db() as db:db.execute('UPDATE leases SET until=0')
 restored=Market(market.root.parent.parent,market.state)
 assert next(r for r in restored.rows('runs') if r['id']==rid)['status']=='interrupted'
