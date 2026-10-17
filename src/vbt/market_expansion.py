"""Auditable, idempotent installation of observed sponsor collection targets."""
import json
from .issuer_discovery import discover,initial_mappings,seed_matches_listing,unsupported_listings
from .market_coverage import refresh_candidates
from .china_issuer_sources import source_mappings, matches_listing
from .io import now

def expand(market):
 listings=market.rows('listings');candidates=discover(listings)
 result={'classification':refresh_candidates(market,candidates),'unsupported_identity_count':len(unsupported_listings(listings)),'added':[],'unchanged':[],'conflicts':[],'missing_listing':[]}
 existing={r['id']:r for r in market.rows('companies')}
 with market.db() as db:db.execute('CREATE TABLE IF NOT EXISTS mapping_evidence (company TEXT PRIMARY KEY,created TEXT,payload TEXT)')
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
 with market.db() as db:
  db.execute('CREATE TABLE IF NOT EXISTS expansion_events (created TEXT,payload TEXT)')
  db.execute('INSERT INTO expansion_events VALUES (?,?)',(now(),json.dumps(result,ensure_ascii=False)))
 return result
