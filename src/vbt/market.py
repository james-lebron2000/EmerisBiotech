"""Observed market intelligence, separate from frozen research and forecasts."""
import hashlib,json,re,sqlite3,threading,time,uuid
from pathlib import Path
from .io import now
from . import market_sources as sources

class Market:
 def __init__(self,workspace,state):
  self.root=Path(workspace)/'artifacts/market';self.root.mkdir(parents=True,exist_ok=True)
  self.state=Path(state);self.state.mkdir(parents=True,exist_ok=True)
  if self.state.resolve().is_relative_to('/Volumes'):raise ValueError('Market state must be local')
  self.path=self.state/'market.sqlite3'
  with self.db() as d:
   d.executescript('''
   CREATE TABLE IF NOT EXISTS listings (id TEXT PRIMARY KEY,market TEXT,exchange TEXT,ticker TEXT,name TEXT,industry TEXT,source TEXT,observed TEXT);
   CREATE TABLE IF NOT EXISTS companies (id TEXT PRIMARY KEY,market TEXT,ticker TEXT,name TEXT,aliases TEXT,provenance TEXT,created TEXT);
   CREATE TABLE IF NOT EXISTS trials (company TEXT,nct TEXT,data TEXT,hash TEXT,observed TEXT,PRIMARY KEY(company,nct));
   CREATE TABLE IF NOT EXISTS changes (id TEXT PRIMARY KEY,company TEXT,nct TEXT,observed TEXT,before_json TEXT,after_json TEXT,run_id TEXT);
   CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY,source TEXT,company TEXT,started TEXT,finished TEXT,status TEXT,count INTEGER,error TEXT);
   CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY,value TEXT);
   CREATE TABLE IF NOT EXISTS leases (key TEXT PRIMARY KEY,until REAL);
   ''')
   d.execute("UPDATE runs SET status='interrupted',finished=?,error='Collection lease expired; retry required' WHERE status='running' AND NOT EXISTS (SELECT 1 FROM leases WHERE leases.key=CASE WHEN runs.company='' THEN 'universe:' || runs.source ELSE 'trials:' || runs.company END AND leases.until>?)",(now(),time.time()))
 def db(self):
  d=sqlite3.connect(self.path,timeout=30);d.row_factory=sqlite3.Row;return d
 def rows(self,table):
  if table not in ('listings','companies','trials','changes','runs'):raise ValueError('Unknown table')
  with self.db() as d:return [dict(x) for x in d.execute('SELECT * FROM '+table)]
 def count(self,table):
  if table not in ('listings','companies','trials','changes'):raise ValueError('Unknown table')
  with self.db() as d:return d.execute('SELECT count(*) FROM '+table).fetchone()[0]
 def recent_changes(self,limit=20):
  with self.db() as d:return [dict(r) for r in d.execute('SELECT * FROM changes ORDER BY observed DESC LIMIT ?', (limit,))]
 def search_trials(self,market='',company='',query='',phase='',status='',offset=0):
  conditions=[];params=[]
  if market:conditions.append('company LIKE ?');params.append(market+':%')
  if company:conditions.append('company=?');params.append(company)
  if query:conditions.append('(company LIKE ? OR data LIKE ?)');params.extend(['%'+query+'%']*2)
  if phase:conditions.append('data LIKE ?');params.append('%"'+phase+'"%')
  if status:conditions.append("json_extract(data,'$.status')=?");params.append(status)
  where=' WHERE '+' AND '.join(conditions) if conditions else ''
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
 def claim(self,key):
  with self.db() as d:
   d.execute('BEGIN IMMEDIATE');r=d.execute('SELECT until FROM leases WHERE key=?',(key,)).fetchone()
   if r and r[0]>time.time():return False
   d.execute('INSERT OR REPLACE INTO leases VALUES (?,?)',(key,time.time()+7200))
  return True
 def touch(self,key):
  with self.db() as d:d.execute('UPDATE leases SET until=? WHERE key=?',(time.time()+7200,key))
 def release(self,key):
  with self.db() as d:d.execute('DELETE FROM leases WHERE key=?',(key,))
 def begin(self,source,company=''):
  rid=uuid.uuid4().hex
  with self.db() as d:d.execute('INSERT INTO runs VALUES (?,?,?,?,?,?,?,?)',(rid,source,company,now(),None,'running',0,''))
  return rid
 def finish(self,rid,status,count,error=''):
  with self.db() as d:d.execute('UPDATE runs SET finished=?,status=?,count=?,error=? WHERE id=?',(now(),status,count,error[:1000],rid))
 def archive(self,rid,index,url,raw):
  folder=self.root/'captures'/rid;folder.mkdir(parents=True,exist_ok=True)
  path=folder/f'{index}.bin';path.write_bytes(raw)
  (folder/f'{index}.json').write_text(json.dumps({'url':url,'retrieved_at':now(),'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw)},ensure_ascii=False))
 def refresh_universe(self,source,fetch=sources.fetch):
  if source not in sources.URLS:raise ValueError('Unknown universe source')
  key='universe:'+source
  if not self.claim(key):return None
  rid=self.begin(source);count=0
  try:
   raw=fetch(sources.URLS[source]);self.archive(rid,0,sources.URLS[source],raw);rows,total=sources.parse_universe(source,raw);count=len(rows)
   # Entire replacement only after complete, nonempty parse. Incomplete response never deletes old listings.
   if total is not None and count<total:raise ValueError(f'Incomplete universe: {count}/{total}; prior snapshot retained')
   with self.db() as d:
    d.execute('DELETE FROM listings WHERE source=?',(source,))
    for r in rows:
     ident=source+':'+r['exchange']+':'+r['ticker']
     d.execute('INSERT OR REPLACE INTO listings VALUES (?,?,?,?,?,?,?,?)',(ident,r['market'],r['exchange'],r['ticker'],r['name'],r['industry'],source,now()))
   self.finish(rid,'succeeded',count)
  except Exception as ex:self.finish(rid,'failed',0,f'{type(ex).__name__}: {ex}')
  finally:self.release(key)
  return rid
 def sync_company(self,cid,fetch=sources.fetch,max_pages=20):
  company=next((c for c in self.rows('companies') if c['id']==cid),None)
  if not company:raise ValueError('Unknown company')
  if not 1<=max_pages<=100:raise ValueError('Invalid page limit')
  key='trials:'+cid
  if not self.claim(key):return None
  rid=self.begin('ClinicalTrials.gov',cid);count=0;seen=set();partial=False;idx=0
  try:
   aliases=json.loads(company['aliases'])
   for alias in aliases:
    token=None;tokens=set()
    for page in range(max_pages):
     self.touch(key)
     url=sources.trial_url(alias,token);raw=fetch(url);self.archive(rid,idx,url,raw);idx+=1
     rows,token,total=sources.parse_trials(raw,aliases)
     with self.db() as d:
      for r in rows:
       if not re.fullmatch(r'NCT\d{8}',r['nct_id']):raise ValueError('Invalid NCT identifier')
       serial=json.dumps(r,ensure_ascii=False,sort_keys=True);digest=hashlib.sha256(serial.encode()).hexdigest()
       old=d.execute('SELECT data,hash FROM trials WHERE company=? AND nct=?',(cid,r['nct_id'])).fetchone()
       if not old or old['hash']!=digest:d.execute('INSERT INTO changes VALUES (?,?,?,?,?,?,?)',(uuid.uuid4().hex,cid,r['nct_id'],now(),old['data'] if old else None,serial,rid))
       d.execute('INSERT OR REPLACE INTO trials VALUES (?,?,?,?,?)',(cid,r['nct_id'],serial,digest,now()))
       seen.add(r['nct_id'])
     count=len(seen)
     if not token:break
     if token in tokens:raise ValueError('Repeated pagination token')
     tokens.add(token);time.sleep(.4)
    if token:partial=True
   self.finish(rid,'partial' if partial else 'succeeded',count,'Page cap reached; registry coverage incomplete' if partial else '')
  except Exception as ex:self.finish(rid,'failed',len(seen),f'{type(ex).__name__}: {ex}; previous observations retained')
  finally:self.release(key)
  return rid
 def enqueue(self,kind,reference):
  if kind=='universe' and reference in sources.URLS:target=self.refresh_universe
  elif kind=='company' and any(c['id']==reference for c in self.rows('companies')):target=self.sync_company
  else:raise ValueError('Unknown collection target')
  threading.Thread(target=target,args=(reference,),daemon=True).start()
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
   if kind=='universe':self.refresh_universe(key)
   else:self.sync_company(key)
 def scheduler(self,stop):
  while not stop.is_set():
   try:self.tick()
   except Exception:pass # source-specific failures are recorded in runs
   stop.wait(60)
