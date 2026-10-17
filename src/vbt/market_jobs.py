"""Durable, bounded collection jobs for local and private workbenches."""
import json,os,sqlite3,threading,time,uuid
from .io import now

ACTIVE=('queued','running','retry_wait')

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
   CREATE INDEX IF NOT EXISTS collection_due ON collection_jobs(status,next_at,created);''')

def enqueue(market,kind,reference):
 from .market_sources import URLS
 with market.db() as db:
  table=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='issuer_candidates'").fetchone()
  candidate=bool(table and db.execute('SELECT 1 FROM issuer_candidates WHERE id=?',(reference,)).fetchone())
 if not ((kind=='resolve' and candidate) or (kind=='universe' and reference in URLS) or (kind=='company' and any(c['id']==reference for c in market.rows('companies')))):
  raise ValueError('Unknown collection target')
 init_schema(market)
 with market.db() as db:
  db.execute('BEGIN IMMEDIATE')
  old=db.execute("SELECT id FROM collection_jobs WHERE kind=? AND reference=? AND status IN ('queued','running','retry_wait')",(kind,reference)).fetchone()
  if old:return old[0]
  jid=uuid.uuid4().hex;stamp=now()
  db.execute('INSERT INTO collection_jobs(id,kind,reference,status,created,updated) VALUES (?,?,?,?,?,?)',(jid,kind,reference,'queued',stamp,stamp))
 return jid

def enqueue_all(market,only_due=False):
 from datetime import datetime,timezone
 runs=market.rows('runs');result=[]
 for c in market.rows('companies'):
  latest=max((r['started'] for r in runs if r['company']==c['id']),default=None)
  if only_due and latest and (datetime.now(timezone.utc)-datetime.fromisoformat(latest)).total_seconds()<21600:continue
  result.append(enqueue(market,'company',c['id']))
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
   changed=db.execute("UPDATE collection_jobs SET status=CASE WHEN cancel_requested=1 THEN 'cancelled' ELSE 'queued' END,owner_pid=NULL,heartbeat=NULL,updated=?,error='Interrupted worker recovered' WHERE id=? AND status='running' AND COALESCE(heartbeat,0)=?",(now(),row['id'],row['heartbeat'] or 0)).rowcount
   if changed:
    if row.get('run_id') and row['kind']=='resolve':
     db.execute("UPDATE sponsor_resolution_runs SET status='interrupted',finished=?,error='Worker interrupted before completion' WHERE id=? AND status='running'",(now(),row['run_id']))
     db.execute('DELETE FROM leases WHERE key=? AND owner=(SELECT lease_owner FROM sponsor_resolution_runs WHERE id=?)',('sponsor-resolution:'+row['reference'],row['run_id']))
    elif row.get('run_id'):db.execute("UPDATE runs SET status='interrupted',finished=?,error='Worker stopped before complete publication' WHERE id=? AND status='running'",(now(),row['run_id']))
    key=('universe:' if row['kind']=='universe' else 'trials:')+row['reference']
    db.execute('DELETE FROM leases WHERE key=? AND (owner=(SELECT owner FROM run_leases WHERE run_id=?) OR (owner IS NULL AND until<=?))',(key,row.get('run_id'),time.time()))

def claim(market):
 with market.db() as db:
  db.execute('BEGIN IMMEDIATE')
  row=db.execute("SELECT * FROM collection_jobs WHERE status IN ('queued','retry_wait') AND next_at<=? AND cancel_requested=0 ORDER BY created,id LIMIT 1",(time.time(),)).fetchone()
  if not row:return None
  db.execute("UPDATE collection_jobs SET status='running',attempts=attempts+1,owner_pid=?,heartbeat=?,updated=? WHERE id=?",(os.getpid(),time.time(),now(),row['id']))
  return {**dict(row),'attempts':row['attempts']+1}

def execute(market,job):
 done=threading.Event()
 market._job_context.job=job
 owner=os.getpid()
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
   if job['kind']=='resolve':
    from .sponsor_resolution import resolve
    def on_start(value):
     with market.db() as db:db.execute("UPDATE collection_jobs SET run_id=? WHERE id=? AND attempts=? AND owner_pid=? AND status='running'",(value,job['id'],job['attempts'],owner))
    result=resolve(market,job['reference'],cancelled=cancelled,on_start=on_start);rid=result['run_id'];status=result['status'];error=result['error']
    if status in ('suggested','ambiguous','zero'):status='succeeded'
    elif status=='busy':status='retry_wait'
   else:
    rid=market.refresh_universe(job['reference'],cancelled=cancelled) if job['kind']=='universe' else market.sync_company(job['reference'],cancelled=cancelled)
   if job['kind']=='resolve':pass
   elif rid is None:status='retry_wait';error='Collection already leased; waiting'
   else:
    with market.db() as db:r=db.execute('SELECT * FROM runs WHERE id=?',(rid,)).fetchone()
    status=r['status'];error=r['error']
  transient=any(x in error.lower() for x in ('timed out','timeout','429','503','502','connection','already leased','lease is active'))
  if status in ('failed','retry_wait') and transient and job['attempts']<3:status='retry_wait'
  elif status=='retry_wait':status='failed'
  with market.db() as db:db.execute("UPDATE collection_jobs SET status=?,updated=?,heartbeat=NULL,run_id=?,error=?,next_at=? WHERE id=? AND attempts=? AND owner_pid=? AND status='running'",(status,now(),rid,error[:1000],time.time()+60*2**job['attempts'] if status=='retry_wait' else 0,job['id'],job['attempts'],owner))
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
