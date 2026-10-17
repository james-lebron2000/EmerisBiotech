"""Evidence-preserving sponsor suggestions; never an issuer/asset approval.

Only the fixed ClinicalTrials.gov API is queried. Exact lead-sponsor observations
are archived with the response URL, SHA-256 and retrieval time. Suggestions stay
pending and bind the issuer-candidate snapshot; this module never writes the
configured-company, trial or scientific-review tables.
"""
from __future__ import annotations

import json
import re
import unicodedata
import uuid
import time
from datetime import datetime,timedelta,timezone
from urllib.parse import urlencode

from . import market_sources
from .io import now
from .market_coverage import init_schema as init_candidates

_ELIGIBLE=('drug_developer','candidate','uncertain','drug_developer_candidate')
_GENERIC=frozenset('pharma pharmaceutical pharmaceuticals biopharma biopharmaceutical biopharmaceuticals therapeutic therapeutics medicine medicines biotech biotechnology biologic biologics bio science sciences bioscience biosciences laboratory laboratories lab labs research development medical healthcare health holding holdings group international global and the'.split())
_LEGAL=frozenset('inc incorporated corp corporation ltd limited plc llc co company ag nv sa se ab as lp'.split())
_SECURITY=re.compile(r'\s+[-–—]\s+(?:class\s+[a-z]\b|common\b|ordinary\b|american\b|ads\b|depositary\b|preferred\b|warrant\b|units?\b).*$',re.I)
_TRAILING_SECURITY=re.compile(r'\b(?:class\s+[a-z]\s+)?(?:common\s+(?:stock|shares)|ordinary\s+shares|american\s+depositary\s+(?:shares|receipts)|depositary\s+shares|preferred\s+(?:stock|shares)|warrants?)\b.*$',re.I)
_NON_LATIN=re.compile(r'[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]')


class _Cancelled(Exception):
 """Internal signal that rolls back a proposal publication transaction."""


def _tokens(name):
 if not isinstance(name,str) or not name.strip() or len(name)>400 or _NON_LATIN.search(name):return []
 value=unicodedata.normalize('NFKD',name).encode('ascii','ignore').decode().casefold()
 value=value.replace('&',' and ')
 value=re.sub(r'\b(s\.a\.|n\.v\.|a\.g\.|s\.e\.|l\.l\.c\.)\s*$',lambda m:m.group(1).replace('.',''),value)
 tokens=re.findall(r'[a-z0-9]+',value)
 while tokens and tokens[-1] in _LEGAL:tokens.pop()
 while tokens and tokens[-1]=='and':tokens.pop()
 return tokens


def query_name_from_listing(name,market):
 """Return a conservative English registry query, or None for identity gaps.

 No translation, ticker expansion, fuzzy similarity or short-acronym guessing
 occurs. A short brand is permitted only in a longer company phrase, while a
 single token requires six characters. Legal suffix removal never removes
 substantive Pharma/Bio/Therapeutics words.
 """
 if market not in ('US','A','HK'):raise ValueError('Unsupported market')
 if not isinstance(name,str) or len(name)>400:return None
 value=_SECURITY.sub('',name.strip()) if market=='US' else name.strip()
 value=_TRAILING_SECURITY.sub('',value)
 if market=='HK':value=re.sub(r'[-－](?:B|U|W|SW|S|R)$','',value,flags=re.I)
 tokens=_tokens(value)
 distinctive=[t for t in tokens if t not in _GENERIC and t not in _LEGAL]
 if not distinctive or not any(re.search('[a-z]',t) for t in distinctive):return None
 if len(tokens)==1 and (len(distinctive[0])<6 or not distinctive[0].isalpha()):return None
 if len(tokens)>1 and not any(len(t)>=4 and re.search('[a-z]',t) for t in distinctive):return None
 result=' '.join(tokens)
 return result if 4<=len(result)<=200 else None


def match_sponsor(query,alias):
 """Return normalized_exact, distinctive_token, or unrelated.

 Token matching requires the complete distinctive-token sets to agree and two
 distinctive tokens, or an eight-character brand plus a shared scientific
 company descriptor. Shared generic words alone can never create a proposal.
 Even normalized_exact is a pending suggestion, never ownership evidence.
 """
 left=_tokens(query);right=_tokens(alias)
 ld={t for t in left if t not in _GENERIC and t not in _LEGAL}
 rd={t for t in right if t not in _GENERIC and t not in _LEGAL}
 if not left or not right or not ld or not rd:return 'unrelated'
 if left==right:return 'normalized_exact'
 if ld!=rd:return 'unrelated'
 long_brand=len(ld)==1 and len(next(iter(ld)))>=8 and bool(set(left)&set(right)&_GENERIC)
 if len(ld)>=2 or long_brand:return 'distinctive_token'
 return 'unrelated'


def query_url(query,token=None):
 if not query or len(query)>200 or not re.fullmatch(r'[a-z0-9 ]+',query):raise ValueError('Unsafe sponsor query')
 params={'query.spons':'"'+query+'"','format':'json','pageSize':100,'fields':'NCTId,LeadSponsorName','countTotal':'true'}
 if token:
  if not isinstance(token,str) or len(token)>4000:raise ValueError('Invalid page token')
  params['pageToken']=token
 return 'https://clinicaltrials.gov/api/v2/studies?'+urlencode(params)


def init_schema(market):
 init_candidates(market)
 with market.db() as db:
  db.executescript('''
  CREATE TABLE IF NOT EXISTS sponsor_resolution_runs (
    id TEXT PRIMARY KEY,candidate_id TEXT NOT NULL,candidate_hash TEXT NOT NULL,
    query TEXT,started TEXT NOT NULL,finished TEXT,status TEXT NOT NULL,
    page_count INTEGER NOT NULL DEFAULT 0,proposal_count INTEGER NOT NULL DEFAULT 0,
    observation_count INTEGER NOT NULL DEFAULT 0,truncated INTEGER NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT '',lease_owner TEXT NOT NULL DEFAULT ''
  );
  CREATE TABLE IF NOT EXISTS sponsor_proposals (
    id TEXT PRIMARY KEY,candidate_id TEXT NOT NULL,candidate_hash TEXT NOT NULL,
    alias TEXT NOT NULL,normalized_alias TEXT NOT NULL,match_type TEXT NOT NULL,
    evidence_nct TEXT NOT NULL,source_url TEXT NOT NULL,run_id TEXT NOT NULL,
    created TEXT NOT NULL,last_seen TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending'
      CHECK(status IN ('pending','accepted','rejected')),
    UNIQUE(candidate_id,alias,candidate_hash)
  );
  CREATE TABLE IF NOT EXISTS sponsor_resolution_observations (
    run_id TEXT NOT NULL,alias TEXT NOT NULL,nct_id TEXT NOT NULL,
    match_type TEXT NOT NULL,source_url TEXT NOT NULL,capture_index INTEGER NOT NULL,
    PRIMARY KEY(run_id,alias,nct_id)
  );
  CREATE INDEX IF NOT EXISTS idx_sponsor_resolution_candidate
    ON sponsor_resolution_runs(candidate_id,started);
  CREATE INDEX IF NOT EXISTS idx_sponsor_proposals_candidate
    ON sponsor_proposals(candidate_id,status);
  ''')
  if 'lease_owner' not in {r[1] for r in db.execute('PRAGMA table_info(sponsor_resolution_runs)')}:
   db.execute("ALTER TABLE sponsor_resolution_runs ADD COLUMN lease_owner TEXT NOT NULL DEFAULT ''")
  # Active owners keep their running state. Only old observations whose actual
  # persistent lease expired/lost its owner become interrupted, never completed.
  cutoff=(datetime.now(timezone.utc)-timedelta(minutes=5)).isoformat()
  db.execute('''UPDATE sponsor_resolution_runs SET status='interrupted',finished=?,
    error='Sponsor-resolution lease expired; retry required'
    WHERE status='running' AND started<? AND NOT EXISTS
      (SELECT 1 FROM leases l WHERE l.key='sponsor-resolution:' || sponsor_resolution_runs.candidate_id
       AND l.owner=sponsor_resolution_runs.lease_owner AND l.until>?)''',(now(),cutoff,time.time()))


def _finish(market,run_id,status,pages=0,count=0,observations=0,truncated=False,error=''):
 with market.db() as db:
  db.execute('''UPDATE sponsor_resolution_runs SET finished=?,status=?,page_count=?,
    proposal_count=?,observation_count=?,truncated=?,error=? WHERE id=?''',
    (now(),status,pages,count,observations,int(truncated),str(error)[:1500],run_id))
  row=dict(db.execute('SELECT * FROM sponsor_resolution_runs WHERE id=?',(run_id,)).fetchone())
 row['run_id']=row['id'];row['truncated']=bool(row['truncated'])
 return row


def _parse(raw,query):
 if not isinstance(raw,bytes) or len(raw)>2_000_000:raise ValueError('Sponsor response must be bytes within 2 MB')
 data=json.loads(raw)
 if not isinstance(data,dict) or not isinstance(data.get('studies'),list) or len(data['studies'])>100:raise ValueError('Sponsor registry schema changed')
 result=[]
 for study in data['studies']:
  p=study.get('protocolSection',{});nct=p.get('identificationModule',{}).get('nctId','')
  alias=p.get('sponsorCollaboratorsModule',{}).get('leadSponsor',{}).get('name','')
  if not isinstance(nct,str) or not re.fullmatch(r'NCT\d{8}',nct):raise ValueError('Invalid NCT evidence identifier')
  if not isinstance(alias,str) or not 1<=len(alias.strip())<=300:raise ValueError('Missing or invalid lead-sponsor name')
  alias=alias.strip()
  result.append({'alias':alias,'nct_id':nct,'match_type':match_sponsor(query,alias),'source_url':'https://clinicaltrials.gov/study/'+nct})
 token=data.get('nextPageToken')
 if token is not None and (not isinstance(token,str) or not token or len(token)>4000):raise ValueError('Invalid next-page token')
 return result,token


def resolve(market,candidate_id,fetch=market_sources.fetch,*,max_pages=3,
            cancelled=lambda:False,on_start=lambda rid:None):
 """Observe sponsor candidates for one unmapped issuer; never configure it.

 Failure preserves earlier proposals and all captures. Successful suggestions
 bind the candidate content hash. A caller must separately inspect identity,
 review stale hashes, and explicitly accept before configuring collection.
 """
 if not 1<=max_pages<=3:raise ValueError('Sponsor resolution allows 1-3 pages')
 init_schema(market)
 with market.db() as db:
  candidate=db.execute('SELECT * FROM issuer_candidates WHERE id=?',(candidate_id,)).fetchone()
  mapped=db.execute('SELECT 1 FROM companies WHERE id=?',(candidate_id,)).fetchone()
 if not candidate:raise ValueError('Unknown issuer candidate')
 candidate=dict(candidate);query=query_name_from_listing(candidate['name'],candidate['market'])
 rid=uuid.uuid4().hex
 with market.db() as db:
  db.execute('''INSERT INTO sponsor_resolution_runs
    (id,candidate_id,candidate_hash,query,started,status) VALUES (?,?,?,?,?,'running')''',
    (rid,candidate_id,candidate['content_hash'],query,now()))
 try:on_start(rid)
 except Exception as ex:return _finish(market,rid,'failed',error='Start callback failed: '+type(ex).__name__+': '+str(ex))
 if cancelled():return _finish(market,rid,'cancelled',error='Cancelled before sponsor lookup')
 if mapped:return _finish(market,rid,'blocked',error='Issuer already has a configured sponsor mapping')
 if candidate['classification'] not in _ELIGIBLE or candidate['review_status']=='rejected':return _finish(market,rid,'blocked',error='Candidate classification is ineligible or rejected')
 if not query:return _finish(market,rid,'blocked',error='Verified English legal/company name required; no translation or short-name inference performed')
 key='sponsor-resolution:'+candidate_id
 if not market.claim(key):return _finish(market,rid,'busy',error='A sponsor-resolution lease is active')
 lease_token=market.lease_tokens()[key];pages=0;observed=[];proposed={};token=None;tokens=set()
 with market.db() as db:db.execute('UPDATE sponsor_resolution_runs SET lease_owner=? WHERE id=?',(lease_token,rid))
 try:
  for index in range(max_pages):
   if cancelled():return _finish(market,rid,'cancelled',pages,0,len(observed),error='Cancelled before sponsor capture; no proposals published')
   market.touch(key,lease_token)
   url=query_url(query,token);raw=fetch(url)
   # Archive even schema failures so the failed observation is recoverable.
   if not isinstance(raw,bytes) or len(raw)>2_000_000:raise ValueError('Sponsor response must be bytes within 2 MB')
   market.archive(rid,index,url,raw);pages+=1
   if cancelled():return _finish(market,rid,'cancelled',pages,0,len(observed),error='Cancelled after sponsor capture; no proposals published')
   rows,token=_parse(raw,query)
   with market.db() as db:
    for r in rows:
     db.execute('INSERT OR IGNORE INTO sponsor_resolution_observations VALUES (?,?,?,?,?,?)',
       (rid,r['alias'],r['nct_id'],r['match_type'],r['source_url'],index))
     observed.append(r)
     if r['match_type']!='unrelated':
      entry=proposed.setdefault(r['alias'],{'match_type':r['match_type'],'ncts':set()})
      entry['ncts'].add(r['nct_id'])
   if not token:break
   if token in tokens:raise ValueError('Repeated sponsor-pagination token')
   tokens.add(token)
  # Guard against stale workers publishing after a later process claims a lease.
  if cancelled():return _finish(market,rid,'cancelled',pages,0,len(observed),error='Cancelled before proposal publication')
  market.touch(key,lease_token)
  with market.db() as db:
   db.execute('BEGIN IMMEDIATE')
   if cancelled():raise _Cancelled('Cancelled before proposal publication')
   lease=db.execute('SELECT 1 FROM leases WHERE key=? AND owner=? AND until>CAST(strftime(\'%s\',\'now\') AS REAL)',(key,lease_token)).fetchone()
   if not lease:raise ValueError('Sponsor-resolution lease no longer owned')
   for alias,p in proposed.items():
    ncts=sorted(p['ncts']);timestamp=now()
    db.execute('''INSERT INTO sponsor_proposals
      (id,candidate_id,candidate_hash,alias,normalized_alias,match_type,evidence_nct,source_url,run_id,created,last_seen,status)
      VALUES (?,?,?,?,?,?,?,?,?,?,?,'pending')
      ON CONFLICT(candidate_id,alias,candidate_hash) DO UPDATE SET
       match_type=excluded.match_type,evidence_nct=excluded.evidence_nct,
       source_url=excluded.source_url,run_id=excluded.run_id,last_seen=excluded.last_seen
       WHERE sponsor_proposals.status='pending' ''',
      (uuid.uuid4().hex,candidate_id,candidate['content_hash'],alias,' '.join(_tokens(alias)),p['match_type'],json.dumps(ncts),'https://clinicaltrials.gov/study/'+ncts[0],rid,timestamp,timestamp))
   if cancelled():raise _Cancelled('Cancelled before proposal transaction committed')
  status='partial' if token else 'suggested' if len(proposed)==1 and next(iter(proposed.values()))['match_type']=='normalized_exact' else 'ambiguous' if observed else 'zero'
  observation_count=len({(r['alias'],r['nct_id']) for r in observed})
  return _finish(market,rid,status,pages,len(proposed),observation_count,bool(token),
    'Page cap reached; sponsor search incomplete' if token else 'Registry matches lacked a qualifying exact/high-constraint lead sponsor' if observed and not proposed else '')
 except _Cancelled as ex:
  return _finish(market,rid,'cancelled',pages,0,len({(r['alias'],r['nct_id']) for r in observed}),error=str(ex))
 except Exception as ex:
  return _finish(market,rid,'failed',pages,0,len({(r['alias'],r['nct_id']) for r in observed}),False,type(ex).__name__+': '+str(ex))
 finally:market.release(key,lease_token)


def proposals(market,candidate_id=None,*,status=None,limit=100,offset=0):
 """Return pending/reviewed suggestions with source dates and staleness flag."""
 if status not in (None,'pending','accepted','rejected') or not 1<=limit<=200 or offset<0:raise ValueError('Invalid proposal filter')
 init_schema(market);where=[];params=[]
 if candidate_id:where.append('p.candidate_id=?');params.append(candidate_id)
 if status:where.append('p.status=?');params.append(status)
 suffix=' WHERE '+' AND '.join(where) if where else ''
 with market.db() as db:
  rows=[dict(r) for r in db.execute('''SELECT p.*,c.name AS candidate_name,
    c.content_hash AS current_candidate_hash,r.status AS resolution_status,
    r.finished AS resolved_at,r.truncated
    FROM sponsor_proposals p JOIN issuer_candidates c ON c.id=p.candidate_id
    JOIN sponsor_resolution_runs r ON r.id=p.run_id'''+suffix+' ORDER BY p.last_seen DESC,p.id LIMIT ? OFFSET ?',params+[limit,offset])]
 for row in rows:
  row['evidence_nct']=json.loads(row['evidence_nct']);row['candidate_changed']=row['candidate_hash']!=row['current_candidate_hash']
  row['capture_metadata_root']=str(market.root/'captures'/row['run_id'])
 return rows


def resolution_runs(market,candidate_id=None,*,limit=50):
 if not 1<=limit<=200:raise ValueError('Invalid run limit')
 init_schema(market)
 with market.db() as db:
  return [dict(r) for r in db.execute('SELECT * FROM sponsor_resolution_runs'+(' WHERE candidate_id=?' if candidate_id else '')+' ORDER BY started DESC LIMIT ?',([candidate_id] if candidate_id else [])+[limit])]


def batch(market,*,limit=10,market_filter='',fetch=market_sources.fetch):
 """Resolve at most limit unmapped candidates, least recently attempted first.

 This is an explicitly invoked sequential operation, not an auto scheduler.
 Mapped/rejected/excluded/unclassified rows are never selected. Blocked and
 unsuccessful identities remain inspectable and are not configured implicitly.
 """
 if not 1<=limit<=50 or market_filter not in ('','US','A','HK'):raise ValueError('Invalid resolution batch')
 init_schema(market)
 with market.db() as db:
  rows=db.execute('''SELECT c.id FROM issuer_candidates c
    LEFT JOIN companies m ON m.id=c.id
    LEFT JOIN (SELECT candidate_id,MAX(started) AS latest FROM sponsor_resolution_runs GROUP BY candidate_id) r ON r.candidate_id=c.id
    WHERE m.id IS NULL AND c.review_status!='rejected'
      AND c.classification IN ('drug_developer','candidate','uncertain','drug_developer_candidate')'''+
    (' AND c.market=?' if market_filter else '')+' ORDER BY r.latest IS NOT NULL,r.latest,c.id LIMIT ?',([market_filter] if market_filter else [])+[limit]).fetchall()
 return [resolve(market,r['id'],fetch=fetch) for r in rows]
