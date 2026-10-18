"""Incremental directory screening and bounded sponsor discovery, without approval."""
import hashlib,json,time
from .io import now


def record(market,status,payload):
 with market.db() as db:
  db.execute('CREATE TABLE IF NOT EXISTS market_discovery_events(created TEXT,status TEXT,payload TEXT)')
  db.execute('INSERT INTO market_discovery_events VALUES (?,?,?)',(now(),status,json.dumps(payload,ensure_ascii=False)))


def tick(market):
 """Enabled scheduler: source suggestions hourly, opt-in strict identity checks per tick."""
 from .issuer_discovery import VERSION
 from .china_issuer_sources import VERSION as CN_VERSION
 from .china_issuer_sources_v2 import VERSION as CN_V2_VERSION
 from .market_expansion import expand
 from .market_jobs import enqueue,enqueue_identity,enqueue_identity_repairs
 from .market_traversal import enqueue_traversal
 from .sponsor_resolution import init_schema,QUERY_CONTEXT_VERSION
 from .issuer_directory_names import VERSION as NAMES_VERSION,SOURCES as NAME_SOURCES,enrich
 from .issuer_directory_screening import VERSION as SCREENING_VERSION
 init_schema(market)
 with market.db() as db:
  snapshot=[tuple(r) for r in db.execute('SELECT source,count(*),max(observed) FROM listings GROUP BY source ORDER BY source')]
 signature=hashlib.sha256(json.dumps([VERSION,CN_VERSION,CN_V2_VERSION,NAMES_VERSION,SCREENING_VERSION,QUERY_CONTEXT_VERSION,snapshot]).encode()).hexdigest()
 result={'expanded':False,'company_jobs':[],'resolution_jobs':[],'identity_jobs':[],'identity_repair_jobs':[]}
 try:
  directory_key='discovery:directory'
  if market.claim(directory_key):
   try:
    if market.setting('discovery.directory_signature')!=signature:
     sources=[source for source,_,_ in snapshot if source in NAME_SOURCES]
     receipts=[]
     def enrich_phase(phase):
      current=[enrich(market,source=source) for source in sources]
      receipts.extend({'phase':phase,**r} for r in current)
      return sum(r['added'] for r in current)
     # Existing candidates receive current source-bound names before screening.
     # A fresh database first needs base candidates, then a second screening pass.
     enrich_phase('before_screening')
     expansion=expand(market);result['expanded']=True;added=list(expansion['added'])
     if enrich_phase('after_screening'):
      expansion=expand(market)
      added=list(dict.fromkeys(added+expansion['added']))
      enrich_phase('final_candidate_hashes')
     expansion['added']=added;result['expansion']=expansion
     complete=all(r['status']=='succeeded' for r in receipts)
     result['directory_name_enrichment']={'status':('succeeded' if complete else 'blocked') if sources else 'not_applicable','sources':receipts}
     result['company_jobs']=[enqueue(market,'company',cid) for cid in expansion['added']]
     # Record the attempted source/version signature even when proof is blocked:
     # do not replay a corrupt directory on every scheduler tick. A fresh source,
     # code-version migration or explicit directory-names command can retry it.
     with market.db() as db:db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',('discovery.directory_signature',signature))
   finally:market.release(directory_key)
  # Scheduling is serialized across the UI and collector via a short fenced lease.
  key='discovery:sponsor-batch'
  if market.claim(key):
   try:
    last=float(market.setting('discovery.last_resolution_batch','0'))
    if time.time()-last>=3600:
     batch=enqueue_traversal(market,limit=20)
     result['resolution_jobs']=batch['new_jobs'];result['traversal_batch_id']=batch['batch_id']
     with market.db() as db:db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',('discovery.last_resolution_batch',str(time.time())))
   finally:market.release(key)
  if market.setting('identity.enabled','0')=='1':
   result['identity_repair_jobs']=enqueue_identity_repairs(market,limit=20)
   result['identity_jobs']=enqueue_identity(market,limit=20)
  if result['expanded'] or result['resolution_jobs'] or result['identity_jobs'] or result['identity_repair_jobs']:
   record(market,'partial' if result.get('directory_name_enrichment',{}).get('status')=='blocked' else 'succeeded',result)
 except Exception as exc:
  record(market,'failed',{'error':type(exc).__name__+': '+str(exc)[:1000],**result});raise
 return result
