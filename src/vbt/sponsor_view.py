"""Source-backed sponsor suggestions with explicit mapping review."""
import html
from urllib.parse import urlencode
from .market import Market
from .market_coverage import candidate_rows
from .sponsor_resolution import proposals,resolution_runs,query_name_from_listing,init_schema
from .market_jobs import enqueue

def e(v):return html.escape(str(v if v is not None else '尚未记录'),quote=True)
LABEL={'suggested':'规范名称相符，待核对','ambiguous':'主体有歧义，待核对','zero':'未检索到记录','blocked':'需要补充准确英文主体','partial':'分页未完成','failed':'来源请求失败','busy':'同一主体正在检索','cancelled':'已取消','interrupted':'检索中断','running':'检索中'}

def enqueue_batch(market,limit=20):
 if not 1<=limit<=20:raise ValueError('Batch size must be 1-20')
 init_schema(market)
 with market.db() as db:
  rows=db.execute("SELECT c.id,c.name,c.market FROM issuer_candidates c LEFT JOIN companies m ON m.id=c.id LEFT JOIN (SELECT candidate_id,max(started) t FROM sponsor_resolution_runs GROUP BY candidate_id) r ON r.candidate_id=c.id WHERE m.id IS NULL AND c.review_status!='rejected' AND c.classification IN ('drug_developer','candidate','uncertain','drug_developer_candidate') ORDER BY r.t IS NOT NULL,r.t,c.id").fetchall()
 result=[]
 for row in rows:
  if not query_name_from_listing(row['name'],row['market']):continue
  result.append(enqueue(market,'resolve',row['id']))
  if len(result)>=limit:break
 return result

def render(app,q):
 m=Market(app.workspace,app.state);init_schema(m)
 def form(action,fields,label):return '<form method="post" action="'+action+'"><input type="hidden" name="token" value="'+e(app.token)+'">'+''.join('<input type="hidden" name="'+e(k)+'" value="'+e(v)+'">' for k,v in fields.items())+'<button>'+label+'</button></form>'
 body='<div class="eyebrow">ISSUER → REGISTRY SPONSOR</div><h1>申办方关联</h1><p class="lead">为尚未关联的药企查找登记主体，保留依据后再决定是否接受。</p><p><a href="/coverage">覆盖进度 →</a> · <a href="/market">临床项目 →</a></p><div class="notice">名称相符只是一条关联线索。请核对证券主体、母子公司和历史名称；接受后才登记该申办方并排队抓取。此记录不确认药物权益，也不构成科学审批。</div>'
 body+='<div class="card"><h2>推进下一批</h2><p>每批最多20家，优先未检索且有可用英文名称的候选。仅产生待核对建议，不自动接受。</p>'+form('/market/resolve-batch',{},'检索下一批申办方')+'</div>'
 rows,total=candidate_rows(m,q=q.get('q',''),market_filter=q.get('market',''),status='unmapped',limit=20,classification='active')
 body+='<details class="card"><summary>选择未关联公司 · 匹配 '+str(total)+' 条，展示前20条</summary><form><label for="sponsor-q">公司名称或代码</label><input id="sponsor-q" name="q" value="'+e(q.get('q',''))+'"><select name="market" aria-label="市场"><option value="">全部市场</option>'+''.join('<option '+('selected ' if q.get('market')==code else '')+'value="'+code+'">'+label+'</option>' for code,label in [('US','美股'),('A','A股'),('HK','港股')])+'</select><button>筛选候选</button></form>'
 for row in rows:
  query=query_name_from_listing(row['name'],row['market'])
  body+='<div class="card"><b>'+e(row['name']+' · '+row['id'])+'</b><p>'+('检索名称：'+e(query) if query else '需要先取得准确英文主体；平台不会自动翻译后归属。')+'</p>'
  if query:body+=form('/market/resolve',{'candidate_id':row['id']},'检索此公司申办方')
  body+='</div>'
 body+='</details><h2>待核对的关联建议</h2>'
 pending=proposals(m,status='pending',limit=50)
 if not pending:body+='<div class="empty">暂时没有待核对建议。可先检索候选公司，再核对返回的申办方。</div>'
 for row in pending:
  body+='<article class="card"><span class="badge">待主体核对</span><h3>'+e(row['candidate_name'])+' → '+e(row['alias'])+'</h3><p>'+e(row['candidate_id'])+' · '+e(row['match_type'])+' · '+e(LABEL.get(row['resolution_status'],row['resolution_status']))+'</p><p><a href="'+e(row['source_url'])+'" target="_blank" rel="noopener">登记来源 '+e(', '.join(row['evidence_nct'][:5]))+' ↗</a></p><p class="ref">来源查询完成 '+e(row['resolved_at'])+' · 证据批次 '+e(row['run_id'])+'</p>'
  if row['candidate_changed']:body+='<p class="notice">候选识别依据已变化，需要重新检索。</p>'
  else:body+='<form method="post" action="/market/proposal-review"><input type="hidden" name="token" value="'+e(app.token)+'"><input type="hidden" name="proposal_id" value="'+e(row['id'])+'"><label>记录人</label><input name="reviewer" required maxlength="100"><label>主体核对依据</label><textarea name="reason" required minlength="10" maxlength="5000" placeholder="公司来源如何证明该登记申办方属于这一证券主体？"></textarea><label>判断</label><select name="decision"><option value="accepted">接受关联并排队抓取</option><option value="rejected">排除此关联建议</option></select><p><button>保存主体核对</button></p></form>'
  body+='</article>'
 body+='<h2>近期检索与缺口</h2>'
 for row in resolution_runs(m,limit=30):body+='<div class="card"><b>'+e(row['candidate_id'])+' · '+e(LABEL.get(row['status'],row['status']))+'</b><p>查询名称 '+e(row['query'])+' · 建议 '+str(row['proposal_count'])+' 条</p><p>'+e(row['error'])+'</p><span class="ref">'+e(row['started'])+' · '+e(row['id'])+'</span></div>'
 with m.db() as db:
  if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sponsor_proposal_reviews'").fetchone():
   body+='<h2>最近主体核对记录</h2>'
   for r in db.execute('SELECT * FROM sponsor_proposal_reviews ORDER BY created DESC LIMIT 20'):body+='<div class="card"><b>'+e(r['reviewer'])+' · '+('接受' if r['decision']=='accepted' else '排除')+'</b><p>'+e(r['reason'])+'</p><span class="ref">'+e(r['created'])+' · '+e(r['proposal_id'])+'</span></div>'
 return body
