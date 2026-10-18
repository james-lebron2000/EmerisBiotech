"""Auditable, idempotent installation of observed sponsor collection targets."""
import hashlib,json,uuid
from .issuer_discovery import discover,initial_mappings,seed_matches_listing,unsupported_listings
from .market_coverage import refresh_candidates
from .issuer_directory_screening import supplement_candidates
from .china_issuer_sources import source_mappings, matches_listing
from .china_issuer_sources_v2 import source_mappings as source_mappings_v2
from .china_expansion_review import review_mapping, curated_proposal_plan, VERSION as REVIEW_VERSION
from .io import now

def expand(market):
 listings=market.rows('listings');candidates=supplement_candidates(market,discover(listings))
 result={'classification':refresh_candidates(market,candidates),'unsupported_identity_count':len(unsupported_listings(listings)),'added':[],'unchanged':[],'conflicts':[],'missing_listing':[],
         'source_name_verified':[],'blocked_evidence':[],'pending_proposal_conflicts':[],'curated_reconciliations':[],'source_name_review_version':REVIEW_VERSION}
 existing={r['id']:r for r in market.rows('companies')}
 with market.db() as db:
  db.execute('CREATE TABLE IF NOT EXISTS mapping_evidence (company TEXT PRIMARY KEY,created TEXT,payload TEXT)')
  db.execute('CREATE TABLE IF NOT EXISTS mapping_source_reviews (id TEXT PRIMARY KEY,company TEXT,batch TEXT,status TEXT,created TEXT,payload TEXT)')
  db.execute('CREATE TABLE IF NOT EXISTS curated_source_reconciliations (id TEXT PRIMARY KEY,proposal_id TEXT UNIQUE,review_id TEXT UNIQUE,company TEXT,source_review_id TEXT,evidence_sha256 TEXT,created TEXT,evidence_json TEXT)')
  db.execute("CREATE TRIGGER IF NOT EXISTS curated_reconciliations_no_update BEFORE UPDATE ON curated_source_reconciliations BEGIN SELECT RAISE(ABORT,'Curated reconciliation records are append only'); END")
  db.execute("CREATE TRIGGER IF NOT EXISTS curated_reconciliations_no_delete BEFORE DELETE ON curated_source_reconciliations BEGIN SELECT RAISE(ABORT,'Curated reconciliation records are append only'); END")
 # Shared review schemas are initialized before any caller-owned transaction.
 # The public reviewer_type allowlist remains unchanged in sponsor_review.py.
 from .sponsor_review import init_review_schema
 init_review_schema(market)
 for row,matcher in [(r,seed_matches_listing) for r in initial_mappings()]+[(r,matches_listing) for r in source_mappings()]:
  if not any(matcher(row,l) for l in listings if l['market']==row['market'] and l['ticker']==row['ticker']):
   result['missing_listing'].append(row['id']);continue
  old=existing.get(row['id'])
  if old:
   if set(json.loads(old['aliases']))!=set(row['aliases']):result['conflicts'].append(row['id']);continue
   result['unchanged'].append(row['id'])
  else:
   market.add_company(row['market'],row['ticker'],row['name'],row['aliases'],row['provenance']);result['added'].append(row['id'])
  with market.db() as db:db.execute('INSERT OR IGNORE INTO mapping_evidence VALUES (?,?,?)',(row['id'],now(),json.dumps(row,ensure_ascii=False)))
 # This second batch has a separate independent, capture-bound gate.  A cloud
 # deployment that has only the public config must restore the original raw
 # evidence; it cannot silently treat a reference link as verified evidence.
 cache={}
 for row in source_mappings_v2():
  if not any(matches_listing(row,l) for l in listings):
   result['missing_listing'].append(row['id']);continue
  with market.db() as db:
   candidate=db.execute('SELECT * FROM issuer_candidates WHERE id=?',(row['id'],)).fetchone()
  review=review_mapping(row,market.root.parent.parent,listings,cache=cache,candidate=dict(candidate) if candidate else None)
  if review['status']!='verified_source_name':
   result['blocked_evidence'].append(review);continue
  result['source_name_verified'].append(row['id'])
  _install_reviewed_v2(market,row,review,result)
 with market.db() as db:
  db.execute('CREATE TABLE IF NOT EXISTS expansion_events (created TEXT,payload TEXT)')
  db.execute('INSERT INTO expansion_events VALUES (?,?)',(now(),json.dumps(result,ensure_ascii=False)))
 return result

def _install_reviewed_v2(market,row,review,result):
 """Atomically retain review receipts and install only a nonconflicting target.

 Eligible pending proposals receive a distinct curated-official-source review
 in the same transaction as the company mapping.  Other pending proposals stay
 pending and remain explicit conflicts; no human signature is manufactured.
 """
 with market.db() as db:
  db.execute('BEGIN IMMEDIATE')
  current=[dict(r) for r in db.execute('SELECT * FROM listings WHERE market=? AND ticker=?',(row['market'],row['ticker']))]
  if not any(matches_listing(row,l) for l in current):
   result['source_name_verified'].remove(row['id'])
   result['blocked_evidence'].append({'id':row['id'],'status':'blocked','errors':[{'code':'current_listing_changed','message':'Exchange identity changed after evidence validation; retry with a new review','reference':''}]});return
  old=db.execute('SELECT * FROM companies WHERE id=?',(row['id'],)).fetchone()
  if old and set(json.loads(old['aliases']))!=set(row['aliases']):
   result['conflicts'].append(row['id']);return
  candidate=db.execute('SELECT * FROM issuer_candidates WHERE id=?',(row['id'],)).fetchone()
  if not candidate or review.get('candidate_hash')!=candidate['content_hash']:
   result['source_name_verified'].remove(row['id'])
   result['blocked_evidence'].append({'id':row['id'],'status':'blocked','errors':[{'code':'current_candidate_changed','message':'Candidate identity/hash changed after source validation; retry with a new review','reference':''}]});return
  pending=[r['id'] for r in db.execute("SELECT id FROM sponsor_proposals WHERE candidate_id=? AND status='pending'",(row['id'],))]
  receipt_id=hashlib.sha256((REVIEW_VERSION+':'+row['id']+':'+review['row_sha256']+':'+candidate['content_hash']).encode()).hexdigest()
  db.execute('INSERT OR IGNORE INTO mapping_source_reviews VALUES (?,?,?,?,?,?)',(receipt_id,row['id'],REVIEW_VERSION,review['status'],now(),json.dumps(review,ensure_ascii=False)))
  stored_receipt=db.execute('SELECT payload FROM mapping_source_reviews WHERE id=?',(receipt_id,)).fetchone()['payload']
  try:
   stored=json.loads(stored_receipt)
   same=isinstance(stored,dict) and {k:v for k,v in stored.items() if k!='checked_at'}=={k:v for k,v in review.items() if k!='checked_at'}
  except (TypeError,ValueError):same=False
  if not same:
   result['source_name_verified'].remove(row['id'])
   result['blocked_evidence'].append({'id':row['id'],'status':'blocked','errors':[{'code':'source_review_ledger_mismatch','message':'Stored source-review evidence differs from the current independent gate; existing ledger was retained','reference':receipt_id}]});return
  receipt_digest=hashlib.sha256(stored_receipt.encode()).hexdigest()
  plan={'eligible':[],'conflicts':[],'review':None}
  if pending:
   plan=curated_proposal_plan(market,db,row,candidate['content_hash'])
   if plan['review'] and plan['review']['status']!='verified_source_name':
    result['blocked_evidence'].append(plan['review'])
    result['source_name_verified'].remove(row['id']);return
   if plan['conflicts']:
    result['pending_proposal_conflicts'].append({'id':row['id'],'proposal_ids':[p['proposal_id'] for p in plan['conflicts']],'source_name_verified':True,'existing_mapping':bool(old),
                                              'reason':'Only independently source-backed, unchanged, complete exact-alias proposals can be reconciled. Other proposals remain pending.', 'conflicts':plan['conflicts']})
   if not plan['eligible'] and not old:return
  payload={**row,'source_name_review':review}
  if old:
   result['unchanged'].append(row['id'])
  else:
   provenance=row['provenance']+' Independent source-name audit '+REVIEW_VERSION+'; human sign-off, scientific review and drug ownership remain pending.'
   db.execute('INSERT INTO companies VALUES (?,?,?,?,?,?,?)',(row['id'],row['market'],row['ticker'],row['name'],json.dumps(row['aliases'],ensure_ascii=False),provenance,now()))
   result['added'].append(row['id'])
  db.execute('INSERT OR IGNORE INTO mapping_evidence VALUES (?,?,?)',(row['id'],now(),json.dumps(payload,ensure_ascii=False)))
  for item in plan['eligible']:
   proposal,resolution=item['proposal'],item['resolution']
   evidence={'status':'verified_source_name','reviewer_type':'curated_official_source_review','scope':'listed_security_to_exact_registry_source_name_only',
             'source_review_id':receipt_id,'source_review_sha256':receipt_digest,'source_review_version':REVIEW_VERSION,'curated_row_sha256':review['row_sha256'],
             'candidate_id':row['id'],'candidate_hash':candidate['content_hash'],'current_listing':[dict(r) for r in current],
             'proposal_id':proposal['id'],'proposal_candidate_hash':proposal['candidate_hash'],'proposal_alias':proposal['alias'],'approved_alias':item['approved_alias'],
             'proposal_status_before':'pending','proposal_source_url':proposal['source_url'],'proposal_evidence_nct':json.loads(proposal['evidence_nct']),
             'original_resolution':{k:resolution[k] for k in ('id','candidate_id','candidate_hash','query','status','truncated','page_count','finished')},
             'proof_captures':plan['review']['captures'],'raw_verification_at':plan['review']['checked_at'],'relationship_to_issuer':review['relationship_to_issuer'],
             'review_note':review['review_note'],'human_sign_off':False,'drug_ownership_verified':False,'scientific_review':'pending','full_pipeline_verified':False,'checked_at':now()}
   encoded=json.dumps(evidence,ensure_ascii=False,sort_keys=True);digest=hashlib.sha256(encoded.encode()).hexdigest();review_id=uuid.uuid4().hex;reconciliation_id=uuid.uuid4().hex
   reason='Curated official-source reconciliation of the independently frozen issuer/registry association. '+review['review_note']+' Human and scientific sign-off and drug ownership remain unverified.'
   db.execute('INSERT INTO sponsor_proposal_reviews VALUES (?,?,?,?,?,?,?)',(review_id,proposal['id'],proposal['candidate_hash'],'accepted',REVIEW_VERSION,reason,now()))
   db.execute('INSERT INTO sponsor_review_evidence VALUES (?,?,?,?)',(review_id,'curated_official_source_review',encoded,digest))
   if db.execute("UPDATE sponsor_proposals SET status='accepted' WHERE id=? AND status='pending' AND candidate_hash=?",(proposal['id'],candidate['content_hash'])).rowcount!=1:raise ValueError('Curated proposal changed before publication')
   db.execute('INSERT INTO curated_source_reconciliations VALUES (?,?,?,?,?,?,?,?)',(reconciliation_id,proposal['id'],review_id,row['id'],receipt_id,digest,now(),encoded))
   result['curated_reconciliations'].append({'id':reconciliation_id,'proposal_id':proposal['id'],'review_id':review_id,'company':row['id'],'reviewer_type':'curated_official_source_review','source_review_id':receipt_id,'evidence_sha256':digest,'human_sign_off':False})
