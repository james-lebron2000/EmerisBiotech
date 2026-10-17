import json,threading,urllib.request,urllib.error,sqlite3,time
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
 monkeypatch.setattr(market,'enqueue',lambda kind,ref:calls.append(ref))
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

def test_nct_search_uses_registry_key_when_legacy_json_lacks_identifiers(market):
 legacy={'title':'Synthetic legacy import without embedded registry identifiers','phases':['PHASE2'],'status':'RECRUITING'}
 assert 'nct_id' not in legacy and 'source_url' not in legacy
 market.add_company('US','OTHER','Other synthetic issuer',['Other Synthetic Sponsor'],'Isolated scope fixture')
 with market.db() as db:
  for company in ('US:TEST','US:OTHER'):
   db.execute('INSERT INTO trials VALUES (?,?,?,?,?)',(company,'NCT00000001',json.dumps(legacy),'synthetic-hash','2026-10-18'))
 rows,count=market.search_trials(company='US:TEST',query='NCT00000001')
 assert count==1 and len(rows)==1 and rows[0]['nct']=='NCT00000001'
 assert rows[0]['company']=='US:TEST' and json.loads(rows[0]['data'])==legacy
 assert market.search_trials(company='US:TEST',query='NCT99999999')[1]==0
 assert market.search_trials(company='US:EMPTY',query='NCT00000001')[1]==0

def test_expired_lease_recovers_interrupted_run(market):
 market.claim('trials:US:TEST');rid=market.begin('ClinicalTrials.gov','US:TEST')
 with market.db() as db:db.execute('UPDATE leases SET until=0')
 restored=Market(market.root.parent.parent,market.state)
 assert next(r for r in restored.rows('runs') if r['id']==rid)['status']=='interrupted'

@pytest.mark.parametrize('existing',['empty','completed','active_lease'])
def test_constructor_without_expired_runs_does_not_compete_with_a_writer(market,monkeypatch,existing):
 if existing=='completed':
  rid=market.begin('ClinicalTrials.gov','US:TEST');market.finish(rid,'succeeded',0)
 elif existing=='active_lease':
  assert market.claim('trials:US:TEST');market.begin('ClinicalTrials.gov','US:TEST')
 opened=[];statements=[]
 def bounded_db(self):
  db=sqlite3.connect(self.path,timeout=.05);db.row_factory=sqlite3.Row
  db.set_trace_callback(statements.append);opened.append(db);return db
 monkeypatch.setattr(Market,'db',bounded_db)
 writer=sqlite3.connect(market.path)
 writer.execute('BEGIN IMMEDIATE')
 try:
  restored=Market(market.root.parent.parent,market.state)
  assert restored.path==market.path
  assert all(db.total_changes==0 for db in opened)
  assert not any(sql.lstrip().upper().startswith(('UPDATE ','INSERT ','DELETE ','REPLACE ')) for sql in statements)
 finally:
  writer.rollback();writer.close()
  for db in opened:db.close()

@pytest.mark.parametrize('company,source,key',[
 ('US:TEST','ClinicalTrials.gov','trials:US:TEST'),('','US','universe:US')])
def test_constructor_keeps_active_company_and_universe_leases(market,company,source,key):
 assert market.claim(key)
 rid=market.begin(source,company)
 with market.db() as db:before=dict(db.execute('SELECT * FROM leases WHERE key=?',(key,)).fetchone())
 restored=Market(market.root.parent.parent,market.state)
 run=next(row for row in restored.rows('runs') if row['id']==rid)
 assert run['status']=='running' and run['finished'] is None and run['error']==''
 with restored.db() as db:assert dict(db.execute('SELECT * FROM leases WHERE key=?',(key,)).fetchone())==before

@pytest.mark.parametrize('lease',['missing','expired'])
@pytest.mark.parametrize('company,source,key',[
 ('US:TEST','ClinicalTrials.gov','trials:US:TEST'),('','US','universe:US')])
def test_constructor_recovers_only_genuinely_expired_runs(market,lease,company,source,key):
 if lease=='expired':assert market.claim(key)
 rid=market.begin(source,company)
 completed=market.begin('Synthetic completed source','US:COMPLETED');market.finish(completed,'succeeded',3)
 if lease=='expired':
  with market.db() as db:db.execute('UPDATE leases SET until=0 WHERE key=?',(key,))
 restored=Market(market.root.parent.parent,market.state)
 rows={row['id']:row for row in restored.rows('runs')}
 assert rows[rid]['status']=='interrupted' and rows[rid]['finished']
 assert rows[rid]['error']=='Collection lease expired; retry required'
 assert rows[completed]['status']=='succeeded' and rows[completed]['count']==3

def test_recovery_rechecks_lease_renewed_after_the_read_probe(market,monkeypatch):
 assert market.claim('trials:US:TEST')
 rid=market.begin('ClinicalTrials.gov','US:TEST')
 with market.db() as db:db.execute('UPDATE leases SET until=0')
 renewed=[];opened=[]
 class RenewingConnection(sqlite3.Connection):
  def execute(self,sql,parameters=()):
   if sql.lstrip().upper().startswith('UPDATE RUNS') and not renewed:
    with sqlite3.connect(market.path) as other:
     other.execute('UPDATE leases SET until=? WHERE key=?',(time.time()+3600,'trials:US:TEST'))
    renewed.append(True)
   return super().execute(sql,parameters)
 def connection(self):
  db=sqlite3.connect(self.path,timeout=.1,factory=RenewingConnection);db.row_factory=sqlite3.Row;opened.append(db);return db
 monkeypatch.setattr(Market,'db',connection)
 try:
  restored=Market(market.root.parent.parent,market.state)
  assert renewed==[True]
  assert next(row for row in restored.rows('runs') if row['id']==rid)['status']=='running'
 finally:
  for db in opened:db.close()

def test_each_new_constructor_still_checks_expiry_without_schema_cache(market):
 assert market.claim('trials:US:TEST')
 rid=market.begin('ClinicalTrials.gov','US:TEST')
 first=Market(market.root.parent.parent,market.state)
 assert next(row for row in first.rows('runs') if row['id']==rid)['status']=='running'
 with market.db() as db:db.execute('UPDATE leases SET until=0')
 second=Market(market.root.parent.parent,market.state)
 assert next(row for row in second.rows('runs') if row['id']==rid)['status']=='interrupted'
