import json,os,threading,time
import pytest
from vbt.market import Market
from vbt.market_jobs import enqueue,jobs,claim,execute,cancel,recover,run_worker

@pytest.fixture
def market(tmp_path):
 m=Market(tmp_path/'workspace',tmp_path/'state');m.add_company('US','TEST','Synthetic',['Synthetic sponsor'],'Synthetic fixture');return m

def response(nct='NCT00000001',token=None):
 d={'studies':[{'protocolSection':{'identificationModule':{'nctId':nct},'sponsorCollaboratorsModule':{'leadSponsor':{'name':'Synthetic sponsor'}},'armsInterventionsModule':{'interventions':[{'type':'DRUG','name':'Synthetic drug'}]}}}]}
 if token:d['nextPageToken']=token
 return json.dumps(d).encode()

def test_durable_deduplicated_job(market):
 jid=enqueue(market,'company','US:TEST')
 assert enqueue(market,'company','US:TEST')==jid
 restored=Market(market.root.parent.parent,market.state)
 assert jobs(restored)[0]['status']=='queued'
 job=claim(restored);assert job['id']==jid and claim(restored) is None
 cancel(restored,jid);execute(restored,job)
 assert jobs(restored)[0]['status']=='cancelled'
 assert enqueue(restored,'company','US:TEST')!=jid

def test_worker_records_target_run(market,monkeypatch):
 def collect(cid,cancelled):return Market.sync_company(market,cid,fetch=lambda u:response(),cancelled=cancelled)
 monkeypatch.setattr(market,'sync_company',collect)
 enqueue(market,'company','US:TEST');execute(market,claim(market))
 assert jobs(market)[0]['status']=='succeeded' and jobs(market)[0]['run_id']
 with market.db() as db:assert db.execute('SELECT last_run FROM trial_lineage').fetchone()[0]==jobs(market)[0]['run_id']

def test_failed_or_partial_snapshot_never_promoted(market):
 market.sync_company('US:TEST',fetch=lambda u:response())
 market.sync_company('US:TEST',fetch=lambda u:response('NCT00000002','next'),max_pages=1)
 assert [r['nct'] for r in market.rows('trials')]==['NCT00000001']
 calls=[]
 def broken(u):
  calls.append(u)
  if len(calls)>1:raise OSError('timed out')
  return response('NCT00000003','next')
 market.sync_company('US:TEST',fetch=broken)
 assert [r['nct'] for r in market.rows('trials')]==['NCT00000001']
 assert market.rows('runs')[-1]['status']=='failed'

def test_recovery_respects_live_owner(market):
 enqueue(market,'company','US:TEST');job=claim(market)
 recover(market);assert jobs(market)[0]['status']=='running'
 with market.db() as db:db.execute('UPDATE collection_jobs SET owner_pid=99999999,heartbeat=0 WHERE id=?',(job['id'],))
 recover(market);assert jobs(market)[0]['status']=='queued'

def test_cancel_pending_stays_terminal(market):
 jid=enqueue(market,'company','US:TEST');cancel(market,jid)
 assert claim(market) is None and jobs(market)[0]['status']=='cancelled'

def test_transient_retry_is_bounded(market,monkeypatch):
 def failed(cid,cancelled):
  rid=market.begin('ClinicalTrials.gov',cid);market.finish(rid,'failed',0,'Connection timed out');return rid
 monkeypatch.setattr(market,'sync_company',failed)
 enqueue(market,'company','US:TEST')
 for i in range(3):
  execute(market,claim(market))
  with market.db() as db:db.execute('UPDATE collection_jobs SET next_at=0')
 assert jobs(market)[0]['status']=='failed' and jobs(market)[0]['attempts']==3

def test_fixed_worker_capacity(market):
 stop=threading.Event();threads=run_worker(market,stop,workers=2)
 assert len(threads)==2
 stop.set()
 for t in threads:t.join(timeout=3)
 assert all(not t.is_alive() for t in threads)
 with pytest.raises(ValueError):run_worker(market,stop,workers=100)

def test_cancelled_dead_worker_does_not_permanently_block_target(market):
 jid=enqueue(market,'company','US:TEST');claim(market);cancel(market,jid)
 with market.db() as db:db.execute('UPDATE collection_jobs SET owner_pid=99999999,heartbeat=0 WHERE id=?',(jid,))
 recover(market)
 assert jobs(market)[0]['status']=='cancelled'
 assert enqueue(market,'company','US:TEST')!=jid
 assert claim(market) is not None

def test_recovery_sees_old_jobs_beyond_recent_history(market):
 jid=enqueue(market,'company','US:TEST');claim(market)
 with market.db() as db:
  db.execute('UPDATE collection_jobs SET owner_pid=99999999,heartbeat=0 WHERE id=?',(jid,))
  for n in range(501):db.execute("INSERT INTO collection_jobs(id,kind,reference,status,created,updated) VALUES (?,?,?,'succeeded','2099','2099')",(str(n),'company',str(n)))
 recover(market)
 with market.db() as db:assert db.execute('SELECT status FROM collection_jobs WHERE id=?',(jid,)).fetchone()[0]=='queued'

def test_alive_reused_pid_with_expired_heartbeat_is_recovered(market):
 enqueue(market,'company','US:TEST');claim(market)
 with market.db() as db:db.execute('UPDATE collection_jobs SET owner_pid=?,heartbeat=0',(os.getpid(),))
 recover(market)
 assert jobs(market)[0]['status']=='queued'

def test_cancellation_during_final_fetch_does_not_publish(market):
 cancelled=[False]
 def fetch(url):
  cancelled[0]=True
  return response()
 market.sync_company('US:TEST',fetch=fetch,cancelled=lambda:cancelled[0])
 assert market.rows('trials')==[] and market.rows('runs')[-1]['status']=='cancelled'

def test_publication_and_success_marker_roll_back_together(market):
 with market.db() as db:db.executescript("CREATE TRIGGER deny_success BEFORE UPDATE OF status ON runs WHEN NEW.status='succeeded' BEGIN SELECT RAISE(ABORT,'synthetic storage failure'); END;")
 market.sync_company('US:TEST',fetch=lambda u:response())
 assert market.rows('trials')==[] and market.rows('changes')==[]
 with market.db() as db:assert db.execute('SELECT count(*) FROM trial_lineage').fetchone()[0]==0
 assert market.rows('runs')[-1]['status']=='failed'

def test_recovery_does_not_delete_new_collector_lease(market):
 jid=enqueue(market,'company','US:TEST');claim(market)
 assert market.claim('trials:US:TEST');old=market.begin('ClinicalTrials.gov','US:TEST')
 with market.db() as db:
  db.execute('UPDATE collection_jobs SET owner_pid=99999999,heartbeat=0 WHERE id=?',(jid,))
  db.execute('UPDATE leases SET until=0')
 newer=Market(market.root.parent.parent,market.state)
 assert newer.claim('trials:US:TEST')
 with newer.db() as db:new_owner=db.execute('SELECT owner FROM leases').fetchone()[0]
 recover(market)
 with market.db() as db:assert db.execute('SELECT owner FROM leases').fetchone()[0]==new_owner
 assert not market.claim('trials:US:TEST')
 with pytest.raises(ValueError):market.promote_snapshot(old,'US:TEST')


def test_old_owner_cannot_release_or_touch_new_lease(market):
 assert market.claim('trials:US:TEST');old_token=market.lease_tokens()['trials:US:TEST']
 with market.db() as db:db.execute('UPDATE leases SET until=0')
 newer=Market(market.root.parent.parent,market.state);assert newer.claim('trials:US:TEST')
 market.release('trials:US:TEST',old_token)
 with pytest.raises(ValueError):market.touch('trials:US:TEST',old_token)
 assert not market.claim('trials:US:TEST')

def test_stale_attempt_cannot_overwrite_recovered_attempt(market,monkeypatch):
 enqueue(market,'company','US:TEST');old_job=claim(market);new_run=[]
 def stale(cid,cancelled):
  old_run=market.begin('ClinicalTrials.gov',cid)
  with market.db() as db:db.execute('UPDATE collection_jobs SET heartbeat=0 WHERE id=?',(old_job['id'],))
  recover(market);new_job=claim(market)
  assert new_job['attempts']==2
  market._job_context.job=new_job
  new_run.append(market.begin('ClinicalTrials.gov',cid))
  market._job_context.job=old_job
  return old_run
 monkeypatch.setattr(market,'sync_company',stale)
 execute(market,old_job)
 row=jobs(market)[0]
 assert row['status']=='running' and row['attempts']==2 and row['run_id']==new_run[0]
