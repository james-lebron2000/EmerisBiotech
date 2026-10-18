"""Attributed sponsor decisions with archived evidence for automated reviews."""
import csv,datetime,hashlib,io,json,re,uuid
from pathlib import Path
from .io import now,within
from .sponsor_resolution import init_schema,match_sponsor,query_name_from_listing,proposal_query_issue

_NASDAQ_URL='https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt'
_OTHER_URL='https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt'
_DIRECTORY_SYMBOLS={_NASDAQ_URL:'Symbol',_OTHER_URL:'ACT Symbol'}

def _stamp(value):
 try:
  stamp=datetime.datetime.fromisoformat(value)
  if stamp.tzinfo is None:raise ValueError()
  return stamp.astimezone(datetime.timezone.utc)
 except (TypeError,ValueError):raise ValueError('核对与抓取时间必须为带时区的 ISO 时间') from None

def _archive(market,value):
 if not isinstance(value,str):raise ValueError('缺少本地来源证据路径')
 path=Path(value)
 if path.is_absolute() or path.parts[:2]!=('artifacts','market'):raise ValueError('证据必须位于本工作区的市场来源归档中')
 return within(market.root.joinpath(*path.parts[2:]),market.root/'identity_review')

def _document(market,record,expected_hash,max_bytes=8*1024*1024):
 if not isinstance(expected_hash,str) or len(expected_hash)!=64 or any(c not in '0123456789abcdef' for c in expected_hash):raise ValueError('来源校验值不正确')
 path=_archive(market,record.get('path'))
 if path.stat().st_size>max_bytes:raise ValueError('身份来源文件超过允许大小')
 raw=path.read_bytes()
 if record.get('status')!=200 or record.get('bytes')!=len(raw) or record.get('sha256')!=expected_hash or hashlib.sha256(raw).hexdigest()!=expected_hash:raise ValueError('来源归档校验失败')
 return raw

def exchange_identity(raw,url,ticker,alias):
 """Independently parse either official Nasdaq Trader directory, never a flag.

 Nasdaq's otherlisted file uses ACT Symbol; its NASDAQ Symbol column may encode
 a different share class. Only the exact canonical ACT ticker proves this row.
 """
 if url not in _DIRECTORY_SYMBOLS:raise ValueError('证券来源格式尚无自动核对适配器')
 if not isinstance(raw,bytes) or not raw or len(raw)>8*1024*1024:raise ValueError('证券来源大小不正确')
 reader=csv.DictReader(io.StringIO(raw.decode('utf-8-sig')),delimiter='|')
 symbol=_DIRECTORY_SYMBOLS[url]
 required={symbol,'Security Name','Test Issue','ETF'}
 if not reader.fieldnames or len(set(reader.fieldnames))!=len(reader.fieldnames) or not required.issubset(reader.fieldnames):raise ValueError('官方证券目录字段发生变化')
 rows=[r for r in reader if r.get(symbol)==ticker]
 if not rows:return None
 if len(rows)!=1 or rows[0].get('Test Issue')!='N' or rows[0].get('ETF')!='N' or match_sponsor(query_name_from_listing(rows[0].get('Security Name'),'US'),alias)!='normalized_exact':raise ValueError('官方证券目录无法证明当前证券主体与申办方名称相符')
 return rows[0]

def _validate_automated(market,row,proposal_id,evidence):
 if not isinstance(evidence,dict):raise ValueError('自动关联需要与本建议一致的双源核对证据')
 required={'status':'verified','candidate_id':row['candidate_id'],'proposal_id':proposal_id,'candidate_hash':row['current_hash'],'alias':row['alias'],'market':row['market'],'ticker':row['ticker'],'reviewer_type':'automated_source_review','scope':'listed_entity_to_exact_registry_sponsor_name_only','human_sign_off':False,'drug_ownership_verified':False,'registry_source_url':row['source_url']}
 if any(k not in evidence or type(evidence[k]) is not type(v) or evidence[k]!=v for k,v in required.items()):raise ValueError('自动核对证据未绑定当前候选、建议与限定审查范围')
 if not isinstance(evidence.get('supporting_fact'),str) or not 10<=len(evidence['supporting_fact'])<=5000:raise ValueError('缺少主体核对事实')
 name=evidence.get('verified_company_name')
 if not isinstance(name,str) or len(name)>200 or match_sponsor(name,row['alias'])!='normalized_exact' or match_sponsor(query_name_from_listing(row['name'],row['market']),row['alias'])!='normalized_exact':raise ValueError('已核对主体名、当前候选名与申办方不符')
 checked=_stamp(evidence.get('checked_at'));current=datetime.datetime.now(datetime.timezone.utc)
 if checked>current+datetime.timedelta(minutes=5) or checked<current-datetime.timedelta(days=30):raise ValueError('主体核对时间已过期或在未来')
 # Both official US directory formats are independently parsed. Other markets
 # require an explicit adapter, never a caller-provided verified flag.
 if row['market']!='US' or evidence.get('official_source_url') not in _DIRECTORY_SYMBOLS:raise ValueError('该官方证券来源尚无可验证的自动核对适配器')
 if row['classification'] not in ('candidate','drug_developer') or row['match_type']!='normalized_exact':raise ValueError('不确定分类或非精确申办方建议需要人工核对')
 exchange_path=_archive(market,evidence.get('exchange_capture_manifest'))
 if exchange_path.stat().st_size>1024*1024:raise ValueError('来源清单过大')
 exchange=json.loads(exchange_path.read_bytes())
 if not isinstance(exchange,dict):raise ValueError('证券来源清单格式不正确')
 if exchange.get('url')!=evidence.get('official_source_url') or exchange.get('fetched_at')!=evidence.get('exchange_fetched_at'):raise ValueError('证券来源与归档不一致')
 raw=_document(market,exchange,evidence.get('exchange_sha256'))
 if exchange_identity(raw,exchange['url'],row['ticker'],row['alias']) is None:raise ValueError('官方证券目录缺少当前证券主体')
 captures_path=_archive(market,evidence.get('evidence_capture_manifest'))
 if captures_path.stat().st_size>1024*1024:raise ValueError('来源清单过大')
 captures=json.loads(captures_path.read_bytes())
 if not isinstance(captures,list):raise ValueError('登记来源归档不正确')
 records=[r for r in captures if isinstance(r,dict) and r.get('url')==evidence.get('registry_api_url') and r.get('ticker')==row['ticker'] and r.get('kind')=='registry']
 if len(records)!=1:raise ValueError('缺少唯一登记来源归档')
 record=records[0]
 if record.get('fetched_at')!=evidence.get('registry_fetched_at'):raise ValueError('登记抓取时间与归档不符')
 registry=json.loads(_document(market,record,evidence.get('registry_sha256')))
 if not isinstance(registry,dict) or not isinstance(registry.get('protocolSection'),dict):raise ValueError('登记原始文件格式不正确')
 section=registry['protocolSection'];identification=section.get('identificationModule',{});sponsors=section.get('sponsorCollaboratorsModule',{})
 if not isinstance(identification,dict) or not isinstance(sponsors,dict) or not isinstance(sponsors.get('leadSponsor'),dict):raise ValueError('登记原始文件缺少身份与申办方')
 nct=identification.get('nctId')
 if not isinstance(nct,str) or not re.fullmatch(r'NCT[0-9]{8}',nct) or nct not in json.loads(row['evidence_nct']) or evidence.get('registry_api_url')!='https://clinicaltrials.gov/api/v2/studies/'+str(nct) or row['source_url']!='https://clinicaltrials.gov/study/'+str(nct) or sponsors['leadSponsor'].get('name')!=row['alias']:raise ValueError('登记原始文件未绑定本建议的 NCT 与 exact 申办方')
 for field in ('exchange_fetched_at','registry_fetched_at'):
  stamp=_stamp(evidence.get(field))
  if stamp>checked+datetime.timedelta(minutes=5) or stamp<checked-datetime.timedelta(days=7):raise ValueError('来源抓取时间超出核对窗口')

def init_review_schema(market):
 """Create additive review ledgers before beginning a caller-owned transaction."""
 init_schema(market)
 with market.db() as db:
  db.execute('CREATE TABLE IF NOT EXISTS sponsor_proposal_reviews (id TEXT PRIMARY KEY,proposal_id TEXT,candidate_hash TEXT,decision TEXT,reviewer TEXT,reason TEXT,created TEXT)')
  db.execute('CREATE TABLE IF NOT EXISTS sponsor_review_evidence (review_id TEXT PRIMARY KEY,reviewer_type TEXT NOT NULL,evidence_json TEXT,evidence_sha256 TEXT)')

def review_in_transaction(market,db,proposal_id,decision,reviewer,reason,*,reviewer_type='human_declared',evidence=None,guard=lambda db:None):
 """Append a review inside an existing write transaction; never commit it here.

 A collection caller can atomically append its completion and review. The
 optional guard fences owner/cancellation immediately around identity writes.
 Normal UI callers should use review(), which owns the transaction itself.
 """
 if not db.in_transaction:raise ValueError('Review publication requires a transaction')
 if reviewer_type not in ('human_declared','automated_source_review'):raise ValueError('Unknown reviewer type')
 if decision not in ('accepted','rejected'):raise ValueError('Unknown proposal decision')
 reviewer,reason=reviewer.strip(),reason.strip()
 if not 1<=len(reviewer)<=100 or not 10<=len(reason)<=5000:raise ValueError('填写记录人和至少十个字符的主体核对依据')
 row=db.execute('SELECT p.*,c.market,c.ticker,c.name,c.classification,c.content_hash AS current_hash,c.review_status FROM sponsor_proposals p JOIN issuer_candidates c ON c.id=p.candidate_id WHERE p.id=?',(proposal_id,)).fetchone()
 if not row:raise ValueError('Unknown sponsor proposal')
 if row['status']!='pending':raise ValueError('建议已处理；请查看历史核对记录')
 if row['candidate_hash']!=row['current_hash']:raise ValueError('公司识别依据已变化，需要重新检索和核对')
 if reviewer_type=='automated_source_review':_validate_automated(market,row,proposal_id,evidence)
 guard(db)
 if decision=='accepted':
  issue=proposal_query_issue(market,db,row)
  if issue:raise ValueError(issue)
  if row['review_status']=='rejected':raise ValueError('公司分类已排除，先重新核对分类')
  if not row['alias'] or len(row['alias'])>200 or not row['evidence_nct']:raise ValueError('缺少可核对的申办方证据')
  old=db.execute('SELECT aliases FROM companies WHERE id=?',(row['candidate_id'],)).fetchone()
  if old and row['alias'] not in json.loads(old['aliases']):raise ValueError('已有不同申办方映射，需要单独核对变更')
  if not old:
   provenance=('Automated source-name reconciliation (not human sign-off) by ' if reviewer_type=='automated_source_review' else 'Declared mapping review by ')+reviewer+' at '+now()+'; '+reason[:1000]+'; exact registry source '+row['source_url']+'; proposal '+proposal_id+'. Drug ownership and full pipeline completeness remain unverified.'
   db.execute('INSERT INTO companies VALUES (?,?,?,?,?,?,?)',(row['candidate_id'],row['market'],row['ticker'],row['name'],json.dumps([row['alias']],ensure_ascii=False),provenance,now()))
 rid=uuid.uuid4().hex
 db.execute('INSERT INTO sponsor_proposal_reviews VALUES (?,?,?,?,?,?,?)',(rid,proposal_id,row['candidate_hash'],decision,reviewer,reason,now()))
 payload=json.dumps(evidence or {},ensure_ascii=False,sort_keys=True)
 db.execute('INSERT INTO sponsor_review_evidence VALUES (?,?,?,?)',(rid,reviewer_type,payload,hashlib.sha256(payload.encode()).hexdigest()))
 db.execute('UPDATE sponsor_proposals SET status=? WHERE id=?',(decision,proposal_id))
 guard(db)
 return {'review_id':rid,'company':row['candidate_id'],'decision':decision}

def review(market,proposal_id,decision,reviewer,reason,*,reviewer_type='human_declared',evidence=None):
 init_review_schema(market)
 with market.db() as db:
  db.execute('BEGIN IMMEDIATE')
  return review_in_transaction(market,db,proposal_id,decision,reviewer,reason,reviewer_type=reviewer_type,evidence=evidence)
