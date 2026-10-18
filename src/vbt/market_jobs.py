"""Durable, bounded collection jobs for local and private workbenches."""
import json,math,os,re,sqlite3,threading,time,uuid
from datetime import datetime,timezone
from .io import now

ACTIVE=('queued','running','retry_wait')
# Two bounded sponsor-discovery slots per cycle retain throughput while new
# identities, company follow-ups and source refreshes receive a durable turn.
CLAIM_CYCLE=('universe','identity','resolve','company','resolve','disclosure')

def init_schema(market):
 with market.db() as db:
  db.executescript('''CREATE TABLE IF NOT EXISTS collection_jobs (
   id TEXT PRIMARY KEY, kind TEXT NOT NULL, reference TEXT NOT NULL,
   status TEXT NOT NULL, created TEXT NOT NULL, updated TEXT NOT NULL,
   attempts INTEGER NOT NULL DEFAULT 0, next_at REAL NOT NULL DEFAULT 0,
   owner_pid INTEGER, heartbeat REAL, run_id TEXT, error TEXT NOT NULL DEFAULT '',
   cancel_requested INTEGER NOT NULL DEFAULT 0);
   CREATE UNIQUE INDEX IF NOT EXISTS collection_active ON collection_jobs(kind,reference)
   WHERE status IN ('queued','running','retry_wait');
   CREATE INDEX IF NOT EXISTS collection_due ON collection_jobs(status,next_at,created);
   CREATE INDEX IF NOT EXISTS collection_kind_due
     ON collection_jobs(kind,status,next_at,created,id);
   CREATE TABLE IF NOT EXISTS collection_claim_cursor (
     id INTEGER PRIMARY KEY CHECK(id=1),position INTEGER NOT NULL CHECK(position>=0 AND position<6));
   CREATE TABLE IF NOT EXISTS collection_source_leases (
     job_id TEXT NOT NULL,attempt INTEGER NOT NULL CHECK(attempt>0),owner_pid INTEGER NOT NULL,
     source_id TEXT NOT NULL,run_id TEXT NOT NULL,lease_owner TEXT NOT NULL,created TEXT NOT NULL,
     PRIMARY KEY(job_id,attempt,run_id));
   INSERT OR IGNORE INTO collection_claim_cursor(id,position) VALUES (1,0);''')

def _enqueue_in_transaction(db,kind,reference):
 """Caller validates target and owns the transaction, including follow-up jobs."""
 old=db.execute("SELECT id FROM collection_jobs WHERE kind=? AND reference=? AND status IN ('queued','running','retry_wait')",(kind,reference)).fetchone()
 if old:return old[0]
 jid=uuid.uuid4().hex;stamp=now()
 db.execute('INSERT INTO collection_jobs(id,kind,reference,status,created,updated) VALUES (?,?,?,?,?,?)',(jid,kind,reference,'queued',stamp,stamp))
 return jid

def enqueue(market,kind,reference):
 from .market_sources import URLS
 from .disclosures import SOURCES as DISCLOSURE_SOURCES
 with market.db() as db:
  table=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='issuer_candidates'").fetchone()
  candidate=bool(table and db.execute('SELECT 1 FROM issuer_candidates WHERE id=?',(reference,)).fetchone())
  identity=False
  if kind=='identity' and isinstance(reference,str) and re.fullmatch(r'[0-9a-f]{32}',reference):
   proposal_table=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sponsor_proposals'").fetchone()
   proposal=db.execute('SELECT status FROM sponsor_proposals WHERE id=?',(reference,)).fetchone() if proposal_table else None
   identity=bool(proposal and proposal['status']=='pending')
   if proposal and proposal['status']=='accepted':
    run_table=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='identity_collection_runs'").fetchone()
    identity=bool(run_table and db.execute("SELECT 1 FROM identity_collection_runs WHERE proposal_id=? AND status='succeeded'",(reference,)).fetchone())
  company=bool(kind=='company' and db.execute('SELECT 1 FROM companies WHERE id=?',(reference,)).fetchone())
 if not ((kind=='identity' and identity) or (kind=='disclosure' and reference in DISCLOSURE_SOURCES) or (kind=='resolve' and candidate) or (kind=='universe' and reference in URLS) or company):
  raise ValueError('Unknown collection target')
 init_schema(market)
 with market.db() as db:
  db.execute('BEGIN IMMEDIATE')
  return _enqueue_in_transaction(db,kind,reference)

IDENTITY_RETRY_SECONDS=24*60*60
_IDENTITY_RETRY_STATES=frozenset(('failed','cancelled','interrupted','busy','superseded'))
_IDENTITY_RUN_STATES=_IDENTITY_RETRY_STATES|{'running','succeeded','blocked'}
_IDENTITY_JOB_STATES=_IDENTITY_RETRY_STATES|set(ACTIVE)|{'succeeded','blocked'}

def _identity_proposals(market):
 """Traverse the source helper completely; the first page is not the batch."""
 from .identity_collection import eligible_proposals
 rows=[];seen=set();offset=0;page_size=100
 while True:
  page=eligible_proposals(market,limit=page_size,offset=offset)
  for row in page:
   if row['id'] not in seen:rows.append(row);seen.add(row['id'])
  if len(page)<page_size:break
  offset+=page_size
 return rows

def _identity_time(value,clock):
 """Unattributed/naive/future timestamps never make an attempt due."""
 if not isinstance(value,str) or not value:return None
 try:
  stamp=datetime.fromisoformat(value.replace('Z','+00:00'))
  if stamp.tzinfo is None or stamp.utcoffset() is None:return None
  epoch=stamp.timestamp()
  if not math.isfinite(epoch) or epoch>clock+300:return None
  return epoch
 except (ValueError,OverflowError,OSError):return None

def _identity_schedule_state(db,proposal,clock,force):
 from .identity_collection import _row,_gate
 result={'proposal_id':proposal['id'],'candidate_id':proposal['candidate_id'],
         'candidate_hash':None,'status':'unknown','reason':'核对调度状态尚未核实',
         'retry_at':None,'last_attempt':None,'_priority':(1,clock,proposal['id'])}
 def state(status,reason):
  result.update(status=status,reason=reason)
  return result
 row=_row(db,proposal['id'])
 if not row:return state('ineligible','建议已不存在，需重新检索')
 result['candidate_hash']=row['candidate_hash']
 gap=_gate(row)
 if gap:return state('ineligible',gap)
 if db.execute('SELECT 1 FROM companies WHERE id=?',(row['candidate_id'],)).fetchone():
  return state('ineligible','证券主体已有申办方关联；请检查已记录的核对结果')
 queued=db.execute("SELECT 1 FROM collection_jobs WHERE kind='identity' AND reference=? AND status IN ('queued','running','retry_wait')",(row['id'],)).fetchone()
 if queued:return state('active','同一建议已有排队、执行或等待重试任务')
 history=[]
 for run in db.execute('SELECT * FROM identity_collection_runs WHERE proposal_id=? AND candidate_hash=?',(row['id'],row['candidate_hash'])):
  started=_identity_time(run['started'],clock)
  if run['candidate_id']!=row['candidate_id'] or run['status'] not in _IDENTITY_RUN_STATES or started is None:
   return state('unknown','来源核对运行的状态、主体或开始时间异常；暂停自动重试')
  if run['status']=='running':
   if run['finished'] is not None:return state('unknown','执行中的核对运行含结束时间；暂停自动重试')
   return state('active','同一建议已有正在执行的来源核对运行')
  finished=_identity_time(run['finished'],clock)
  if finished is None or finished<started:
   return state('unknown','来源核对运行的结束时间未知或顺序异常；暂停自动重试')
  history.append((finished,started,run['status'],run['id']))
 for job in db.execute("SELECT * FROM collection_jobs WHERE kind='identity' AND reference=?",(row['id'],)):
  if job['run_id']:
   run=db.execute('SELECT proposal_id,candidate_hash FROM identity_collection_runs WHERE id=?',(job['run_id'],)).fetchone()
   if not run or run['proposal_id']!=row['id']:
    return state('unknown','身份任务缺少可归属的来源核对运行；暂停自动重试')
   # A new candidate hash is a new evidence version; keep older jobs historical.
   if run['candidate_hash']!=row['candidate_hash']:continue
  started=_identity_time(job['created'],clock);finished=_identity_time(job['updated'],clock)
  if job['status'] not in _IDENTITY_JOB_STATES or started is None or finished is None or finished<started:
   return state('unknown','身份任务状态或时间异常；暂停自动重试')
  history.append((finished,started,job['status'],job['id']))
 if not history:
  result['_priority']=(0,0,row['id'])
  return state('ready','尚未尝试来源核对')
 last=max(history,key=lambda item:(item[0],item[1],item[3]))
 result['last_attempt']=datetime.fromtimestamp(last[0],timezone.utc).isoformat()
 result['_priority']=(1,last[0],row['id'])
 if any(item[2]=='blocked' for item in history):
  return state('blocked','此建议与候选版本已有受阻记录；新建议或候选哈希版本才可再次自动调度')
 if any(item[2]=='succeeded' for item in history):
  return state('unknown','已有成功记录但建议仍待处理；请核对处理状态，暂停自动重试')
 retry_at=last[0]+IDENTITY_RETRY_SECONDS
 result['retry_at']=datetime.fromtimestamp(retry_at,timezone.utc).isoformat()
 if not force and clock<retry_at:
  return state('cooldown','失败、取消或中断后冷却24小时，保留原始尝试记录')
 return state('ready','手动重试已跳过已知失败冷却' if force and clock<retry_at else '已知失败冷却已结束')

def identity_schedule(market,*,force=False):
 """Observe every eligible proposal's scheduling state, without enqueuing work.

 ``blocked`` is bound to proposal ID plus candidate hash. Repeating resolution
 or changing last_seen alone does not silently unblock an evidence version.
 ``force`` skips only known retry cooldowns; eligibility, active work, blocked
 versions and abnormal states/timestamps remain enforced.
 """
 if type(force) is not bool:raise ValueError('Identity force must be a boolean')
 init_schema(market);proposals=_identity_proposals(market);clock=time.time()
 with market.db() as db:
  db.execute('BEGIN')
  result=[_identity_schedule_state(db,row,clock,force) for row in proposals]
 return [{k:v for k,v in row.items() if k!='_priority'} for row in result]

def enqueue_identity(market,limit=20,*,force=False):
 """Return only newly queued identity jobs, preferring never-attempted proposals.

 Scan all eligible pages, then recheck source eligibility and durable job/run
 state inside the enqueue transaction. Active work never consumes the new-job
 limit. Failed/cancelled/interrupted work has a 24-hour default cooldown.
 Single-target ``enqueue`` keeps its existing idempotent/manual semantics.
 """
 if type(limit) is not int or not 1<=limit<=1000:raise ValueError('Identity batch limit must be 1-1000')
 if type(force) is not bool:raise ValueError('Identity force must be a boolean')
 init_schema(market);proposals=_identity_proposals(market);clock=time.time()
 with market.db() as db:
  db.execute('BEGIN IMMEDIATE')
  candidates=[_identity_schedule_state(db,row,clock,force) for row in proposals]
  ready=sorted((row for row in candidates if row['status']=='ready'),key=lambda row:row['_priority'])
  return [_enqueue_in_transaction(db,'identity',row['proposal_id']) for row in ready[:limit]]

def enqueue_identity_repairs(market,limit=20):
 """Repair a failed durable handoff only after a 24-hour cooldown.

 Accepted mappings are never assumed to have valid proof: the collector's
 idempotent verifier must independently accept the archived raw evidence and
 current candidate before a retry is queued. The queued identity attempt then
 revalidates the same proof and uses the normal owner/attempt-fenced handoff.
 Any company job/registry run, active identity task, explicit cancellation,
 blocked state or uncertain job chronology keeps the repair closed.
 """
 from .identity_collection import init_schema as identity_schema,_accepted_review
 if type(limit) is not int or not 1<=limit<=1000:raise ValueError('Identity repair limit must be 1-1000')
 init_schema(market);identity_schema(market);clock=time.time()
 with market.db() as db:
  db.execute('BEGIN IMMEDIATE')
  rows=db.execute('''SELECT DISTINCT r.* FROM identity_collection_runs r
    JOIN sponsor_proposals p ON p.id=r.proposal_id AND p.status='accepted'
    JOIN issuer_candidates c ON c.id=p.candidate_id AND c.content_hash=p.candidate_hash
    JOIN companies m ON m.id=c.id
    JOIN collection_jobs j ON j.kind='identity' AND j.reference=p.id AND j.run_id=r.id
    WHERE r.status='succeeded' AND r.candidate_hash=p.candidate_hash
      AND r.company=p.candidate_id AND j.status='failed'
    ORDER BY r.started DESC,r.id DESC''').fetchall()
  ready=[];seen=set()
  for row in rows:
   pid,cid=row['proposal_id'],row['candidate_id']
   if pid in seen:continue
   seen.add(pid)
   # A task or observation of the company already constitutes a handoff.
   if db.execute("SELECT 1 FROM collection_jobs WHERE kind='company' AND reference=?",(cid,)).fetchone():continue
   if db.execute("SELECT 1 FROM runs WHERE source='ClinicalTrials.gov' AND company=?",(cid,)).fetchone():continue
   history=db.execute("SELECT * FROM collection_jobs WHERE kind='identity' AND reference=?",(pid,)).fetchall()
   failed=[];closed=False
   for job in history:
    if job['status'] in ACTIVE:
     closed=True;break
    if job['run_id']:
     bound=db.execute('SELECT proposal_id,candidate_hash FROM identity_collection_runs WHERE id=?',(job['run_id'],)).fetchone()
     if not bound or bound['proposal_id']!=pid:closed=True;break
     if bound['candidate_hash']!=row['candidate_hash']:continue
    if job['status'] in ('cancelled','blocked'):
     closed=True;break
    started=_identity_time(job['created'],clock);finished=_identity_time(job['updated'],clock)
    if job['status']!='failed' or started is None or finished is None or finished<started:
     closed=True;break
    failed.append((finished,job['id']))
   if closed or not failed:continue
   latest=max(failed)
   if clock<latest[0]+IDENTITY_RETRY_SECONDS:continue
   started=_identity_time(row['started'],clock);finished=_identity_time(row['finished'],clock)
   if started is None or finished is None or finished<started or finished>latest[0]:continue
   if not _accepted_review(market,db,row):continue
   ready.append((latest[0],pid))
  ready.sort()
  return [_enqueue_in_transaction(db,'identity',pid) for _,pid in ready[:limit]]

def enqueue_all(market,only_due=False):
 from datetime import datetime,timezone
 runs=market.rows('runs');result=[]
 for c in market.rows('companies'):
  latest=max((r['started'] for r in runs if r['company']==c['id']),default=None)
  if only_due and latest and (datetime.now(timezone.utc)-datetime.fromisoformat(latest)).total_seconds()<21600:continue
  result.append(enqueue(market,'company',c['id']))
 return result

def enqueue_disclosures(market,only_due=False):
 """Queue configured official sources; six-hour freshness is independent of CTG."""
 from datetime import datetime,timezone
 from .disclosures import sources
 result=[]
 for source in sources(market):
  # A fixed historical bundle is rechecked only on an explicit request. It is
  # never a six-hour web collection target and cannot trigger a PDF download.
  if only_due and source.get('intake_mode')=='offline_reviewed_bundle':continue
  latest=source.get('latest');started=latest.get('started') if latest else None
  if only_due and started:
   try:
    if (datetime.now(timezone.utc)-datetime.fromisoformat(started)).total_seconds()<21600:continue
   except (TypeError,ValueError):pass # Unknown timestamps require a new observation.
  result.append(enqueue(market,'disclosure',source['id']))
 return result

def jobs(market,limit=100):
 init_schema(market)
 with market.db() as db:return [dict(r) for r in db.execute('SELECT * FROM collection_jobs ORDER BY created DESC LIMIT ?',(min(max(limit,1),500),))]

def cancel(market,jid):
 init_schema(market)
 with market.db() as db:
  db.execute("UPDATE collection_jobs SET cancel_requested=1,status=CASE WHEN status='running' THEN status ELSE 'cancelled' END,updated=? WHERE id=? AND status IN ('queued','running','retry_wait')",(now(),jid))

def recover(market):
 """Recover only dead owners, or expired heartbeats, never infer death from UI state."""
 init_schema(market)
 with market.db() as db:running=[dict(r) for r in db.execute("SELECT * FROM collection_jobs WHERE status='running'")]
 for row in running:
  alive=True
  try:
   if not row['owner_pid']:alive=False
   else:os.kill(row['owner_pid'],0)
  except (OSError,ValueError):alive=False
  if alive and row['heartbeat'] and time.time()-row['heartbeat']<300:continue
  with market.db() as db:
   changed=db.execute("UPDATE collection_jobs SET status=CASE WHEN cancel_requested=1 THEN 'cancelled' ELSE 'queued' END,owner_pid=NULL,heartbeat=NULL,updated=?,error='Interrupted worker recovered' WHERE id=? AND status='running' AND COALESCE(heartbeat,0)=? AND attempts=? AND owner_pid IS ? AND run_id IS ? AND kind=? AND reference=?",(now(),row['id'],row['heartbeat'] or 0,row['attempts'],row['owner_pid'],row.get('run_id'),row['kind'],row['reference'])).rowcount
   if changed:
    if row['kind']=='disclosure':
     # Captures live in their own namespace; never update generic registry runs.
     # A repeat verification can return a sealed capture from an older attempt.
     # Its immutable lease_owner is not proof of the current worker's lease.
     proof=db.execute('''SELECT lease_owner FROM collection_source_leases
       WHERE job_id=? AND attempt=? AND owner_pid IS ? AND source_id=? AND run_id=?''',
       (row['id'],row['attempts'],row['owner_pid'],row['reference'],row.get('run_id'))).fetchone()
     if proof:
      db.execute('DELETE FROM leases WHERE key=? AND owner=?',('disclosure:'+row['reference'],proof['lease_owner']))
     table=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='disclosure_captures'").fetchone()
     if row.get('run_id') and table:
      columns={r[1] for r in db.execute('PRAGMA table_info(disclosure_captures)')}
      if 'lease_owner' in columns:
       capture=db.execute('SELECT status,lease_owner FROM disclosure_captures WHERE id=? AND source_id=?',(row['run_id'],row['reference'])).fetchone()
       db.execute("UPDATE disclosure_captures SET status='interrupted',finished=?,error='Worker interrupted before completion' WHERE id=? AND source_id=? AND status='running'",(now(),row['run_id'],row['reference']))
       if not proof and capture and capture['status']=='running' and capture['lease_owner']:
        db.execute('DELETE FROM leases WHERE key=? AND owner=?',('disclosure:'+row['reference'],capture['lease_owner']))
      else:
       # Legacy records lack ownership proof: retain the lease until expiry.
       db.execute("UPDATE disclosure_captures SET status='interrupted',finished=?,error='Worker interrupted; legacy lease owner unknown' WHERE id=? AND source_id=? AND status='running'",(now(),row['run_id'],row['reference']))
    elif row['kind']=='identity':
     # The identity namespace contains attributed reviews, not generic runs.
     # Do not initialize every identity table while recovering unrelated jobs.
     table=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='identity_collection_runs'").fetchone()
     if row.get('run_id') and table:
      capture=db.execute('SELECT * FROM identity_collection_runs WHERE id=? AND proposal_id=?',(row['run_id'],row['reference'])).fetchone()
      if capture:
       if capture['status']=='running':
        db.execute("UPDATE identity_collection_runs SET status='interrupted',finished=?,error='Worker interrupted before identity completion' WHERE id=? AND proposal_id=? AND status='running' AND lease_owner=?",(now(),row['run_id'],row['reference'],capture['lease_owner']))
        from .identity_collection import _event
        _event(db,row['run_id'],'worker_interrupted','Owning collection job recovered; fresh exact-source retry required')
       # An old worker may not release a new collector's lease. Completed runs
       # remain succeeded so an idempotent retry can restore a missed follow-up.
       if capture['lease_owner']:
        db.execute('DELETE FROM leases WHERE key=? AND owner=?',('identity:'+row['reference'],capture['lease_owner']))
    elif row.get('run_id') and row['kind']=='resolve':
     db.execute("UPDATE sponsor_resolution_runs SET status='interrupted',finished=?,error='Worker interrupted before completion' WHERE id=? AND status='running'",(now(),row['run_id']))
     db.execute('DELETE FROM leases WHERE key=? AND owner=(SELECT lease_owner FROM sponsor_resolution_runs WHERE id=?)',('sponsor-resolution:'+row['reference'],row['run_id']))
    else:
     if row.get('run_id'):db.execute("UPDATE runs SET status='interrupted',finished=?,error='Worker stopped before complete publication' WHERE id=? AND status='running'",(now(),row['run_id']))
     key=('universe:' if row['kind']=='universe' else 'trials:')+row['reference']
     db.execute('DELETE FROM leases WHERE key=? AND (owner=(SELECT owner FROM run_leases WHERE run_id=?) OR (owner IS NULL AND until<=?))',(key,row.get('run_id'),time.time()))

def claim(market):
 """Claim one due task with a persistent, transactionally fenced kind cycle.

 Empty kinds are skipped; FIFO is preserved inside each kind. The shared
 SQLite cursor advances only with the successful claim, so two workers or a
 restarted process cannot reset priority or claim the same queued attempt.
 """
 with market.db() as db:
  db.execute('BEGIN IMMEDIATE')
  cursor=db.execute('SELECT position FROM collection_claim_cursor WHERE id=1').fetchone()
  if not cursor or type(cursor[0]) is not int or not 0<=cursor[0]<len(CLAIM_CYCLE):
   raise sqlite3.OperationalError('Collection claim cursor is missing or invalid')
  clock=time.time()
  for offset in range(len(CLAIM_CYCLE)):
   slot=(cursor[0]+offset)%len(CLAIM_CYCLE)
   row=db.execute("SELECT * FROM collection_jobs WHERE kind=? AND status IN ('queued','retry_wait') AND next_at<=? AND cancel_requested=0 ORDER BY created,id LIMIT 1",(CLAIM_CYCLE[slot],clock)).fetchone()
   if not row:continue
   changed=db.execute("UPDATE collection_jobs SET status='running',attempts=attempts+1,owner_pid=?,heartbeat=?,updated=?,run_id=NULL WHERE id=? AND attempts=? AND status IN ('queued','retry_wait') AND cancel_requested=0 AND next_at<=?",(os.getpid(),clock,now(),row['id'],row['attempts'],clock)).rowcount
   if changed!=1:raise sqlite3.OperationalError('Collection attempt changed before claim')
   advanced=db.execute('UPDATE collection_claim_cursor SET position=? WHERE id=1 AND position=?',((slot+1)%len(CLAIM_CYCLE),cursor[0])).rowcount
   if advanced!=1:raise sqlite3.OperationalError('Collection claim cursor changed during claim')
   return {**dict(row),'attempts':row['attempts']+1}
  return None

def _bind_started_run(market,job,owner,run_id):
 """Bind a run and its current source lease in the same fenced transaction.

 The capture may already be immutable from a prior publication. Keep its first
 lease owner intact and record this verification attempt separately. A stale
 callback or a thread-local token no longer present in leases cannot create a
 recovery proof for a newer worker.
 """
 with market.db() as db:
  db.execute('BEGIN IMMEDIATE')
  changed=db.execute("UPDATE collection_jobs SET run_id=? WHERE id=? AND attempts=? AND owner_pid=? AND kind=? AND reference=? AND status='running'",
    (run_id,job['id'],job['attempts'],owner,job['kind'],job['reference'])).rowcount
  if not changed or job['kind']!='disclosure':return bool(changed)
  token=market.lease_tokens().get('disclosure:'+job['reference'])
  if not token or not db.execute('SELECT 1 FROM leases WHERE key=? AND owner=? AND until>?',
    ('disclosure:'+job['reference'],token,time.time())).fetchone():return True
  table=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='disclosure_captures'").fetchone()
  if not table or not db.execute('SELECT 1 FROM disclosure_captures WHERE id=? AND source_id=?',
    (run_id,job['reference'])).fetchone():return True
  old=db.execute('SELECT owner_pid,source_id,lease_owner FROM collection_source_leases WHERE job_id=? AND attempt=? AND run_id=?',
    (job['id'],job['attempts'],run_id)).fetchone()
  if old:
   if tuple(old)!=(owner,job['reference'],token):raise ValueError('Source lease proof changed inside the same collection attempt')
  else:
   db.execute('INSERT INTO collection_source_leases VALUES (?,?,?,?,?,?,?)',
     (job['id'],job['attempts'],owner,job['reference'],run_id,token,now()))
  return True

def execute(market,job):
 done=threading.Event()
 market._job_context.job=job
 owner=os.getpid();followup_company=None
 def heartbeat():
  while not done.wait(15):
   with market.db() as db:db.execute("UPDATE collection_jobs SET heartbeat=?,updated=? WHERE id=? AND attempts=? AND owner_pid=? AND status='running'",(time.time(),now(),job['id'],job['attempts'],owner))
 threading.Thread(target=heartbeat,daemon=True).start()
 def cancelled():
  with market.db() as db:r=db.execute('SELECT cancel_requested,attempts,owner_pid,status FROM collection_jobs WHERE id=?',(job['id'],)).fetchone()
  return not r or bool(r['cancel_requested']) or r['attempts']!=job['attempts'] or r['owner_pid']!=owner or r['status']!='running'
 try:
  if cancelled():status='cancelled';rid=None;error='Cancelled before collection'
  else:
   def on_start(value):
    _bind_started_run(market,job,owner,value)
   if job['kind']=='resolve':
    from .sponsor_resolution import resolve
    result=resolve(market,job['reference'],cancelled=cancelled,on_start=on_start);rid=result['run_id'];status=result['status'];error=result['error']
    if status in ('suggested','ambiguous','zero'):status='succeeded'
    elif status=='busy':status='retry_wait'
   elif job['kind']=='identity':
    from .identity_collection import collect
    result=collect(market,job['reference'],cancelled=cancelled,on_start=on_start)
    rid=result['run_id'];status=result['status'];error=result['error']
    if status=='succeeded':followup_company=result['company']
    elif status=='busy':status='retry_wait';error='Identity collection already leased; waiting'
   elif job['kind']=='disclosure':
    from .disclosures import ingest
    result=ingest(market,job['reference'],cancelled=cancelled,on_start=on_start)
    if result is None:rid=None;status='retry_wait';error='Disclosure collection already leased; waiting'
    else:rid=result['run_id'];status=result['status'];error=result['error']
   else:
    rid=market.refresh_universe(job['reference'],cancelled=cancelled) if job['kind']=='universe' else market.sync_company(job['reference'],cancelled=cancelled)
    if rid is None:status='retry_wait';error='Collection already leased; waiting'
    else:
     with market.db() as db:r=db.execute('SELECT * FROM runs WHERE id=?',(rid,)).fetchone()
     status=r['status'];error=r['error']
  transient=any(x in error.lower() for x in ('timed out','timeout','429','503','502','connection','already leased','lease is active'))
  if status in ('failed','fetch_failed','retry_wait') and transient and job['attempts']<3:status='retry_wait'
  elif status=='retry_wait':status='failed'
  with market.db() as db:
   db.execute('BEGIN IMMEDIATE')
   current=db.execute("SELECT cancel_requested FROM collection_jobs WHERE id=? AND attempts=? AND owner_pid=? AND status='running'",(job['id'],job['attempts'],owner)).fetchone()
   if current:
    if current['cancel_requested']:
     status='cancelled';error='Cancelled before job completion; no follow-up scheduled'
    elif followup_company:
     if not db.execute('SELECT 1 FROM companies WHERE id=?',(followup_company,)).fetchone():raise ValueError('Identity success lacks its configured company')
     _enqueue_in_transaction(db,'company',followup_company)
    db.execute("UPDATE collection_jobs SET status=?,updated=?,heartbeat=NULL,run_id=?,error=?,next_at=? WHERE id=? AND attempts=? AND owner_pid=? AND status='running'",(status,now(),rid,error[:1000],time.time()+60*2**job['attempts'] if status=='retry_wait' else 0,job['id'],job['attempts'],owner))
 except Exception as ex:
  with market.db() as db:db.execute("UPDATE collection_jobs SET status='failed',updated=?,heartbeat=NULL,error=? WHERE id=? AND attempts=? AND owner_pid=? AND status='running'",(now(),str(ex)[:1000],job['id'],job['attempts'],owner))
 finally:
  done.set();market._job_context.job=None

def run_worker(market,stop,workers=2):
 if not 1<=workers<=4:raise ValueError('Collection workers must be between one and four')
 init_schema(market);recover(market)
 def recovery_loop():
  while not stop.wait(30):
   try:recover(market)
   except sqlite3.OperationalError:pass
 threading.Thread(target=recovery_loop,name='collection-recovery',daemon=True).start()
 def loop():
  while not stop.is_set():
   try:
    job=claim(market)
    if job:execute(market,job);continue
   except sqlite3.OperationalError:pass
   stop.wait(1)
 threads=[threading.Thread(target=loop,daemon=True) for _ in range(workers)]
 for thread in threads:thread.start()
 return threads
