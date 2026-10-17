"""Explicit, attributed acceptance of a source-backed sponsor suggestion."""
import json,uuid
from .io import now
from .sponsor_resolution import init_schema

def review(market,proposal_id,decision,reviewer,reason):
 if decision not in ('accepted','rejected'):raise ValueError('Unknown proposal decision')
 reviewer,reason=reviewer.strip(),reason.strip()
 if not 1<=len(reviewer)<=100 or not 10<=len(reason)<=5000:raise ValueError('填写记录人和至少十个字符的主体核对依据')
 init_schema(market)
 with market.db() as db:
  db.execute('CREATE TABLE IF NOT EXISTS sponsor_proposal_reviews (id TEXT PRIMARY KEY,proposal_id TEXT,candidate_hash TEXT,decision TEXT,reviewer TEXT,reason TEXT,created TEXT)')
  db.execute('BEGIN IMMEDIATE')
  row=db.execute('SELECT p.*,c.market,c.ticker,c.name,c.content_hash AS current_hash,c.review_status FROM sponsor_proposals p JOIN issuer_candidates c ON c.id=p.candidate_id WHERE p.id=?',(proposal_id,)).fetchone()
  if not row:raise ValueError('Unknown sponsor proposal')
  if row['status']!='pending':raise ValueError('建议已处理；请查看历史核对记录')
  if row['candidate_hash']!=row['current_hash']:raise ValueError('公司识别依据已变化，需要重新检索和核对')
  if decision=='accepted':
   if row['review_status']=='rejected':raise ValueError('公司分类已排除，先重新核对分类')
   if not row['alias'] or len(row['alias'])>200 or not row['evidence_nct']:raise ValueError('缺少可核对的申办方证据')
   old=db.execute('SELECT aliases FROM companies WHERE id=?',(row['candidate_id'],)).fetchone()
   if old and row['alias'] not in json.loads(old['aliases']):raise ValueError('已有不同申办方映射，需要单独核对变更')
   if not old:
    provenance='Declared mapping review by '+reviewer+' at '+now()+'; '+reason[:1000]+'; exact registry source '+row['source_url']+'; proposal '+proposal_id+'. Drug ownership and full pipeline completeness remain unverified.'
    db.execute('INSERT INTO companies VALUES (?,?,?,?,?,?,?)',(row['candidate_id'],row['market'],row['ticker'],row['name'],json.dumps([row['alias']],ensure_ascii=False),provenance,now()))
  rid=uuid.uuid4().hex
  db.execute('INSERT INTO sponsor_proposal_reviews VALUES (?,?,?,?,?,?,?)',(rid,proposal_id,row['candidate_hash'],decision,reviewer,reason,now()))
  db.execute('UPDATE sponsor_proposals SET status=? WHERE id=?',(decision,proposal_id))
 return {'review_id':rid,'company':row['candidate_id'],'decision':decision}
