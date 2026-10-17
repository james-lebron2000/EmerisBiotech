"""Incremental directory screening and bounded sponsor discovery, without approval."""
import hashlib,json,time
from datetime import datetime,timedelta,timezone
from .io import now


def record(market,status,payload):
 with market.db() as db:
  db.execute('CREATE TABLE IF NOT EXISTS market_discovery_events(created TEXT,status TEXT,payload TEXT)')
  db.execute('INSERT INTO market_discovery_events VALUES (?,?,?)',(now(),status,json.dumps(payload,ensure_ascii=False)))


def tick(market):
 """Called only by the enabled scheduler; one batch/hour, never auto-accept names."""
 from .issuer_discovery import VERSION
 from .china_issuer_sources import VERSION as CN_VERSION
 from .market_expansion import expand
 from .market_jobs import enqueue
 from .sponsor_resolution import init_schema,query_name_from_listing
 init_schema(market)
 with market.db() as db:
  snapshot=[tuple(r) for r in db.execute('SELECT source,count(*),max(observed) FROM listings GROUP BY source ORDER BY source')]
 signature=hashlib.sha256(json.dumps([VERSION,CN_VERSION,snapshot]).encode()).hexdigest()
 result={'expanded':False,'company_jobs':[],'resolution_jobs':[]}
 try:
  directory_key='discovery:directory'
  if market.claim(directory_key):
   try:
    if market.setting('discovery.directory_signature')!=signature:
     expansion=expand(market);result['expanded']=True;result['expansion']=expansion
     result['company_jobs']=[enqueue(market,'company',cid) for cid in expansion['added']]
     with market.db() as db:db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',('discovery.directory_signature',signature))
   finally:market.release(directory_key)
  # Scheduling is serialized across the UI and collector via a short fenced lease.
  key='discovery:sponsor-batch'
  if market.claim(key):
   try:
    last=float(market.setting('discovery.last_resolution_batch','0'))
    if time.time()-last>=3600:
     current=datetime.now(timezone.utc)
     failed_cutoff=(current-timedelta(hours=24)).isoformat()
     zero_cutoff=(current-timedelta(days=30)).isoformat()
     with market.db() as db:
      rows=db.execute("SELECT c.id,c.name,c.market FROM issuer_candidates c LEFT JOIN companies m ON m.id=c.id WHERE m.id IS NULL AND c.review_status!='rejected' AND c.classification IN ('drug_developer','candidate','uncertain','drug_developer_candidate') AND NOT EXISTS (SELECT 1 FROM sponsor_resolution_runs r WHERE r.candidate_id=c.id AND r.candidate_hash=c.content_hash AND (r.status NOT IN ('failed','interrupted','cancelled','zero') OR (r.status IN ('failed','interrupted','cancelled') AND r.started>=?) OR (r.status='zero' AND r.started>=?))) ORDER BY EXISTS (SELECT 1 FROM sponsor_resolution_runs r WHERE r.candidate_id=c.id),c.id",(failed_cutoff,zero_cutoff)).fetchall()
     for row in rows:
      if not query_name_from_listing(row['name'],row['market']):continue
      result['resolution_jobs'].append(enqueue(market,'resolve',row['id']))
      if len(result['resolution_jobs'])>=20:break
     with market.db() as db:db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',('discovery.last_resolution_batch',str(time.time())))
   finally:market.release(key)
  if result['expanded'] or result['resolution_jobs']:record(market,'succeeded',result)
 except Exception as exc:
  record(market,'failed',{'error':type(exc).__name__+': '+str(exc)[:1000],**result});raise
 return result
