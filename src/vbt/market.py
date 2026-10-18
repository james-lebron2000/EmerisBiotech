"""Observed market intelligence, separate from frozen research and forecasts."""
import hashlib,json,re,sqlite3,threading,time,uuid,os
from pathlib import Path
from .io import now
from .sqlite_connections import ClosingConnection
from . import market_sources as sources

def universe_digest(rows):
 fields=('market','exchange','ticker','name','industry')
 payload=sorted(({k:r[k] for k in fields} for r in rows),key=lambda r:(r['market'],r['exchange'],r['ticker']))
 return hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()

class Market:
 def __init__(self,workspace,state):
  self.root=Path(workspace)/'artifacts/market';self.root.mkdir(parents=True,exist_ok=True)
  self.state=Path(state)
  if self.state.resolve().is_relative_to('/Volumes'):raise ValueError('Market state must be local')
  self.state.mkdir(parents=True,exist_ok=True)
  self.path=self.state/'market.sqlite3';self._lease_context=threading.local();self._job_context=threading.local()
  with self.db() as d:
   d.executescript('''
   CREATE TABLE IF NOT EXISTS listings (id TEXT PRIMARY KEY,market TEXT,exchange TEXT,ticker TEXT,name TEXT,industry TEXT,source TEXT,observed TEXT);
   CREATE TABLE IF NOT EXISTS companies (id TEXT PRIMARY KEY,market TEXT,ticker TEXT,name TEXT,aliases TEXT,provenance TEXT,created TEXT);
   CREATE TABLE IF NOT EXISTS trials (company TEXT,nct TEXT,data TEXT,hash TEXT,observed TEXT,PRIMARY KEY(company,nct));
   CREATE TABLE IF NOT EXISTS changes (id TEXT PRIMARY KEY,company TEXT,nct TEXT,observed TEXT,before_json TEXT,after_json TEXT,run_id TEXT);
   CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY,source TEXT,company TEXT,started TEXT,finished TEXT,status TEXT,count INTEGER,error TEXT);
   CREATE TABLE IF NOT EXISTS trial_staging (run_id TEXT,nct TEXT,data TEXT,hash TEXT,observed TEXT,PRIMARY KEY(run_id,nct));
   CREATE TABLE IF NOT EXISTS trial_lineage (company TEXT,nct TEXT,first_seen TEXT,last_seen TEXT,last_run TEXT,PRIMARY KEY(company,nct));
   CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY,value TEXT);
   CREATE TABLE IF NOT EXISTS leases (key TEXT PRIMARY KEY,until REAL,owner TEXT);
   CREATE TABLE IF NOT EXISTS run_leases (run_id TEXT PRIMARY KEY,key TEXT,owner TEXT);
   CREATE TABLE IF NOT EXISTS universe_snapshots (source TEXT PRIMARY KEY,run_id TEXT NOT NULL,count INTEGER NOT NULL,rows_sha256 TEXT NOT NULL,published TEXT NOT NULL);
   ''')
   if 'owner' not in {r[1] for r in d.execute('PRAGMA table_info(leases)')}:d.execute('ALTER TABLE leases ADD COLUMN owner TEXT')
   # Page reads normally have nothing to recover; even a zero-row UPDATE
   # takes a writer lock. Recheck the original lease predicate when writing.
   expired="status='running' AND NOT EXISTS (SELECT 1 FROM leases WHERE leases.key=CASE WHEN runs.company='' THEN 'universe:' || runs.source ELSE 'trials:' || runs.company END AND leases.until>?)"
   clock=time.time()
   if d.execute('SELECT 1 FROM runs WHERE '+expired+' LIMIT 1',(clock,)).fetchone():
    d.execute("UPDATE runs SET status='interrupted',finished=?,error='Collection lease expired; retry required' WHERE "+expired,(now(),clock))
 def db(self):
  d=sqlite3.connect(self.path,timeout=30,factory=ClosingConnection);d.row_factory=sqlite3.Row;return d
 def rows(self,table):
  if table not in ('listings','companies','trials','changes','runs'):raise ValueError('Unknown table')
  with self.db() as d:return [dict(x) for x in d.execute('SELECT * FROM '+table)]
 def count(self,table):
  if table not in ('listings','companies','trials','changes'):raise ValueError('Unknown table')
  with self.db() as d:return d.execute('SELECT count(*) FROM '+table).fetchone()[0]
 def recent_changes(self,limit=20):
  with self.db() as d:return [dict(r) for r in d.execute('SELECT * FROM changes ORDER BY observed DESC LIMIT ?', (limit,))]
 def trial_filters(self,market='',company='',query='',phase='',status=''):
  if market and market not in ('US','A','HK'):raise ValueError('Unknown market')
  if len(query)>1000:raise ValueError('Search query too long')
  conditions=[];params=[]
  if market:conditions.append('company LIKE ?');params.append(market+':%')
  if company:conditions.append('company=?');params.append(company)
  if query:conditions.append('(company LIKE ? OR nct LIKE ? OR data LIKE ? OR EXISTS (SELECT 1 FROM companies c WHERE c.id=trials.company AND c.name LIKE ?))');params.extend(['%'+query+'%']*4)
  if phase:conditions.append("EXISTS (SELECT 1 FROM json_each(trials.data,'$.phases') WHERE value=?)");params.append(phase)
  if status:conditions.append("json_extract(data,'$.status')=?");params.append(status)
  where=' WHERE '+' AND '.join(conditions) if conditions else ''
  return where,params
 def search_trials(self,market='',company='',query='',phase='',status='',offset=0):
  where,params=self.trial_filters(market,company,query,phase,status)
  with self.db() as d:
   count=d.execute('SELECT count(*) FROM trials'+where,params).fetchone()[0]
   rows=[dict(r) for r in d.execute('SELECT * FROM trials'+where+" ORDER BY json_extract(data,'$.source_updated') DESC,observed DESC,company,nct LIMIT 200 OFFSET ?",params+[offset])]
  return rows,count
 def add_company(self,market,ticker,name,aliases,provenance):
  if market not in ('US','A','HK') or not re.fullmatch(r'[A-Za-z0-9.\-]{1,16}',ticker):raise ValueError('市场或证券代码不正确')
  if not name.strip() or not 1<=len(aliases)<=10 or not provenance.strip():raise ValueError('需要公司名、申办方别名及映射依据')
  if any(not isinstance(x,str) or not x.strip() or len(x)>200 for x in aliases):raise ValueError('别名不正确')
  if len(name)>200 or len(provenance)>2000:raise ValueError('内容过长')
  ticker=ticker.upper().zfill(5 if market=='HK' else 6 if market=='A' else len(ticker));cid=market+':'+ticker
  with self.db() as d:
   # Never silently overwrite an existing attribution mapping.
   d.execute('INSERT INTO companies VALUES (?,?,?,?,?,?,?)',(cid,market,ticker,name.strip(),json.dumps(list(dict.fromkeys(x.strip() for x in aliases)),ensure_ascii=False),provenance.strip(),now()))
  return cid
 def setting(self,key,default=None):
  with self.db() as d:r=d.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
  return r[0] if r else default
 def schedule(self,enabled):
  with self.db() as d:d.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',('enabled','1' if enabled else '0'))
 def lease_tokens(self):
  if not hasattr(self._lease_context,'tokens'):self._lease_context.tokens={}
  return self._lease_context.tokens
 def claim(self,key):
  with self.db() as d:
   d.execute('BEGIN IMMEDIATE');r=d.execute('SELECT until FROM leases WHERE key=?',(key,)).fetchone()
   if r and r[0]>time.time():return False
   token=uuid.uuid4().hex;d.execute('INSERT OR REPLACE INTO leases(key,until,owner) VALUES (?,?,?)',(key,time.time()+7200,token))
   self.lease_tokens()[key]=token
  return True
 def touch(self,key,token=None):
  token=token or self.lease_tokens().get(key)
  with self.db() as d:
   if not d.execute('UPDATE leases SET until=? WHERE key=? AND owner=?',(time.time()+7200,key,token)).rowcount:raise ValueError('Collection lease no longer owned')
 def release(self,key,token=None):
  token=token or self.lease_tokens().get(key)
  with self.db() as d:d.execute('DELETE FROM leases WHERE key=? AND owner=?',(key,token))
 def assert_run_lease(self,db,rid):
  r=db.execute('SELECT key,owner FROM run_leases WHERE run_id=?',(rid,)).fetchone()
  if not r or not db.execute('SELECT 1 FROM leases WHERE key=? AND owner=? AND until>?',(r['key'],r['owner'],time.time())).fetchone():raise ValueError('Collection lease lost; snapshot not published')
 def begin(self,source,company=''):
  rid=uuid.uuid4().hex
  with self.db() as d:
   d.execute('INSERT INTO runs VALUES (?,?,?,?,?,?,?,?)',(rid,source,company,now(),None,'running',0,''))
   key=('trials:'+company) if company else ('universe:'+source)
   if self.lease_tokens().get(key):d.execute('INSERT INTO run_leases VALUES (?,?,?)',(rid,key,self.lease_tokens()[key]))
   if d.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='collection_jobs'").fetchone():
    job=getattr(self._job_context,'job',None)
    if job:d.execute("UPDATE collection_jobs SET run_id=? WHERE id=? AND attempts=? AND status='running' AND owner_pid=?",(rid,job['id'],job['attempts'],os.getpid()))
  return rid
 def finish(self,rid,status,count,error=''):
  with self.db() as d:d.execute('UPDATE runs SET finished=?,status=?,count=?,error=? WHERE id=?',(now(),status,count,error[:1000],rid))
 def archive(self,rid,index,url,raw,metadata=None):
  extra=dict(metadata or {})
  if set(extra)-{'method','request_body','status','page'}:raise ValueError('Unexpected source capture metadata')
  if 'method' in extra and extra['method'] not in ('GET','POST'):raise ValueError('Invalid capture method')
  if 'request_body' in extra and (not isinstance(extra['request_body'],str) or len(extra['request_body'])>1000):raise ValueError('Invalid capture request body')
  if 'status' in extra and (type(extra['status']) is not int or not 100<=extra['status']<=599):raise ValueError('Invalid capture HTTP status')
  if 'page' in extra and (type(extra['page']) is not int or not 0<=extra['page']<100):raise ValueError('Invalid capture page')
  folder=self.root/'captures'/rid;folder.mkdir(parents=True,exist_ok=True)
  path=folder/f'{index}.bin';path.write_bytes(raw)
  (folder/f'{index}.json').write_text(json.dumps({'url':url,'retrieved_at':now(),'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw),**extra},ensure_ascii=False))
 def refresh_universe(self,source,fetch=sources.fetch,cancelled=lambda:False,*,bse_fetch=None):
  if source not in sources.URLS:raise ValueError('Unknown universe source')
  key='universe:'+source
  if not self.claim(key):return None
  rid=self.begin(source);count=0;lease_token=self.lease_tokens()[key]
  try:
   if cancelled():
    self.finish(rid,'cancelled',0,'Cancelled before capture');return rid
   if source=='BSE':
    from . import bse_sources
    capture_index=0
    def archive_bse(capture):
     nonlocal capture_index
     self.archive(rid,capture_index,capture['url'],capture['raw'],{k:capture[k] for k in ('method','request_body','status','page')});capture_index+=1
    result=bse_sources.fetch_universe(fetch=bse_fetch or bse_sources.fetch_page,state_dir=self.state,cancelled=cancelled,on_capture=archive_bse)
    rows,total=result['rows'],result['total']
   else:
    raw=sources.read(sources.URLS[source],state_dir=self.state,cancelled=cancelled,fetch=fetch);self.archive(rid,0,sources.URLS[source],raw);rows,total=sources.parse_universe(source,raw)
   count=len(rows)
   # Entire replacement only after complete, nonempty parse. Incomplete response never deletes old listings.
   if total is not None and count<total:raise ValueError(f'Incomplete universe: {count}/{total}; prior snapshot retained')
   if cancelled():
    self.finish(rid,'cancelled',0,'Cancelled before publication; previous snapshot retained');return rid
   with self.db() as d:
    d.execute('BEGIN IMMEDIATE');self.assert_run_lease(d,rid)
    d.execute('DELETE FROM listings WHERE source=?',(source,))
    for r in rows:
     ident=source+':'+r['exchange']+':'+r['ticker']
     d.execute('INSERT OR REPLACE INTO listings VALUES (?,?,?,?,?,?,?,?)',(ident,r['market'],r['exchange'],r['ticker'],r['name'],r['industry'],source,now()))
    if cancelled():raise InterruptedError('Cancelled before snapshot commit; previous snapshot retained')
    d.execute('INSERT OR REPLACE INTO universe_snapshots VALUES (?,?,?,?,?)',(source,rid,count,universe_digest(rows),now()))
    d.execute("UPDATE runs SET status='succeeded',finished=?,count=?,error='' WHERE id=?",(now(),count,rid))
    if cancelled():raise InterruptedError('Cancelled before snapshot commit; previous snapshot retained')
  except InterruptedError as ex:self.finish(rid,'cancelled',0,str(ex))
  except Exception as ex:self.finish(rid,'failed',0,f'{type(ex).__name__}: {ex}')
  finally:self.release(key,lease_token)
  return rid
 def sync_company(self,cid,fetch=sources.fetch,max_pages=20,cancelled=lambda:False):
  company=next((c for c in self.rows('companies') if c['id']==cid),None)
  if not company:raise ValueError('Unknown company')
  if not 1<=max_pages<=100:raise ValueError('Invalid page limit')
  key='trials:'+cid
  if not self.claim(key):return None
  rid=self.begin('ClinicalTrials.gov',cid);lease_token=self.lease_tokens()[key];count=0;seen=set();partial=False;idx=0
  try:
   aliases=json.loads(company['aliases'])
   for alias in aliases:
    token=None;tokens=set()
    for page in range(max_pages):
     if cancelled():
      self.finish(rid,'cancelled',len(seen),'Cancellation requested; incomplete snapshot not promoted');return rid
     self.touch(key,lease_token)
     url=sources.trial_url(alias,token);raw=sources.read(url,state_dir=self.state,cancelled=cancelled,fetch=fetch);self.archive(rid,idx,url,raw);idx+=1
     if cancelled():
      self.finish(rid,'cancelled',len(seen),'Cancelled after capture; incomplete snapshot not promoted');return rid
     rows,token,total=sources.parse_trials(raw,aliases)
     with self.db() as d:
      for r in rows:
       if not re.fullmatch(r'NCT[0-9]{8}',r['nct_id']):raise ValueError('Invalid NCT identifier')
       serial=json.dumps(r,ensure_ascii=False,sort_keys=True);digest=hashlib.sha256(serial.encode()).hexdigest()
       d.execute('INSERT OR REPLACE INTO trial_staging VALUES (?,?,?,?,?)',(rid,r['nct_id'],serial,digest,now()))
       seen.add(r['nct_id'])
     count=len(seen)
     if not token:break
     if token in tokens:raise ValueError('Repeated pagination token')
     tokens.add(token)
    if token:partial=True
   if cancelled():self.finish(rid,'cancelled',count,'Cancelled before publication; previous snapshot retained')
   elif partial:self.finish(rid,'partial',count,'Page cap reached; registry coverage incomplete')
   else:self.promote_snapshot(rid,cid,cancelled=cancelled)
  except InterruptedError as ex:self.finish(rid,'cancelled',len(seen),str(ex)+'; previous observations retained')
  except Exception as ex:self.finish(rid,'failed',len(seen),f'{type(ex).__name__}: {ex}; previous observations retained')
  finally:self.release(key,lease_token)
  return rid
 def promote_snapshot(self,rid,cid,*,cancelled=lambda:False):
  with self.db() as db:
   db.execute('BEGIN IMMEDIATE');self.assert_run_lease(db,rid)
   if cancelled():raise InterruptedError('Cancelled before snapshot publication')
   for r in db.execute('SELECT * FROM trial_staging WHERE run_id=?',(rid,)).fetchall():
    old=db.execute('SELECT data,hash FROM trials WHERE company=? AND nct=?',(cid,r['nct'])).fetchone()
    if not old or old['hash']!=r['hash']:db.execute('INSERT INTO changes VALUES (?,?,?,?,?,?,?)',(uuid.uuid4().hex,cid,r['nct'],r['observed'],old['data'] if old else None,r['data'],rid))
    db.execute('INSERT OR REPLACE INTO trials VALUES (?,?,?,?,?)',(cid,r['nct'],r['data'],r['hash'],r['observed']))
    db.execute('INSERT INTO trial_lineage VALUES (?,?,?,?,?) ON CONFLICT(company,nct) DO UPDATE SET last_seen=excluded.last_seen,last_run=excluded.last_run',(cid,r['nct'],r['observed'],r['observed'],rid))
   count=db.execute('SELECT count(*) FROM trial_staging WHERE run_id=?',(rid,)).fetchone()[0]
   db.execute("UPDATE runs SET status='succeeded',finished=?,count=?,error='' WHERE id=?",(now(),count,rid))
   db.execute('DELETE FROM trial_staging WHERE run_id=?',(rid,))
   if cancelled():raise InterruptedError('Cancelled during snapshot publication; previous observations retained')
 def enqueue(self,kind,reference):
  from .market_jobs import enqueue
  return enqueue(self,kind,reference)
 def tick(self):
  if self.setting('enabled')!='1':return
  from datetime import datetime,timezone
  tasks=[('universe',s,86400) for s in sources.URLS]+[('company',c['id'],21600) for c in self.rows('companies')]
  runs=self.rows('runs')
  for kind,key,interval in tasks:
   relevant=[r for r in runs if (r['source']==key if kind=='universe' else r['company']==key)]
   last=max((r['started'] for r in relevant),default=None)
   if last and (datetime.now(timezone.utc)-datetime.fromisoformat(last)).total_seconds()<interval:continue
   # Sequential scheduler bounds requests. Manual requests share persistent leases.
   self.enqueue(kind,key)
  from .market_jobs import enqueue_disclosures
  enqueue_disclosures(self,only_due=True)
  from .market_discovery_pipeline import tick as discovery_tick
  discovery_tick(self)
 def scheduler(self,stop):
  while not stop.is_set():
   try:self.tick()
   except Exception:pass # source-specific failures are recorded in runs
   stop.wait(60)
