"""Source-backed sponsor suggestions with explicit mapping review."""
import html,json
from urllib.parse import urlencode
from .market import Market
from .market_coverage import candidate_rows
from .sponsor_resolution import proposals,resolution_runs,candidate_query_contexts,init_schema
from .market_jobs import enqueue
from .market_view import _timestamp, _source_link

def e(v):return html.escape(str(v if v is not None else '尚未记录'),quote=True)
LABEL={'suggested':'规范名称相符，待核对','ambiguous':'主体有歧义，待核对','zero':'未检索到记录','blocked':'需要补充准确英文主体','partial':'分页未完成','failed':'来源请求失败','busy':'同一主体正在检索','cancelled':'已取消','interrupted':'检索中断','running':'检索中'}

def _query_details(context):
 if isinstance(context,str):
  try:context=json.loads(context)
  except (ValueError,TypeError):context={}
 if not isinstance(context,dict) or not context.get('origin'):
  return '<p class="cell-meta">查询种子来源与原文：历史记录未保存</p>'
 origin=context['origin']
 label={'verified_exchange_english_name':'交易所目录英文全称（已核对原始归档）',
        'verified_hk_official_name':'HKEX 官方英文名称（检索依据）',
        'listing_display_name':'证券目录显示名称','unresolved':'准确英文主体尚未取得'}.get(origin,'来源类型未识别')
 body='<p class="cell-meta">查询种子来源：'+e(label)+'<br>名称原文：'+e(context.get('name_original'))+'</p>'
 if origin=='verified_exchange_english_name':
  proof=context.get('directory_evidence')
  if isinstance(proof,dict):
   body+='<details><summary>查看查询名称凭据</summary><dl class="source-meta"><dt>目录</dt><dd>'+e(proof.get('source'))+'</dd><dt>来源批次</dt><dd>'+e(proof.get('source_run_id'))+'</dd><dt>凭据</dt><dd>'+e(proof.get('receipt_id'))+'</dd><dt>原件 SHA-256</dt><dd>'+e(proof.get('raw_sha256'))+'</dd></dl><p class="data-note">英文全称只用于检索，不确认申办方、母子公司关系或药物权益。</p></details>'
 elif origin=='verified_hk_official_name':
  proof=context.get('hk_name_evidence')
  if not isinstance(proof,dict):
   return body+'<p class="notice">HKEX 名称凭据未保存或格式尚未核实；不能据此确认登记申办方别名。</p>'
  from .hk_issuer_names import SOUTHBOUND_URL
  source_url=proof.get('source_url')
  source_link=_source_link(source_url,'HKEX 官方名称来源') if isinstance(source_url,str) and source_url==SOUTHBOUND_URL else '<span class="cell-meta">HKEX 来源链接尚未核实</span>'
  body+='<details><summary>查看 HKEX 查询名称凭据</summary><dl class="source-meta">'
  for field,value in [('名称凭据 UUID',e(proof.get('receipt_id'))),
                      ('来源英文名称原文',e(proof.get('english_name'))),
                      ('来源内容日期',_timestamp(proof.get('content_as_of'))),
                      ('原件取回时间',_timestamp(proof.get('retrieved_at'))),
                      ('官方来源',source_link),('原文定位',e(proof.get('source_locator'))),
                      ('原件 SHA-256',e(proof.get('raw_sha256'))),
                      ('保存的凭据范围',e(proof.get('scope')))]:
   body+='<dt>'+field+'</dt><dd>'+value+'</dd>'
  body+='</dl><p class="data-note">query_seed_only：仅用作检索种子。登记申办方别名尚未证明；该来源只覆盖已观察到的互联互通持股子集，不能作为全市场药企目录、独立法人认证、母子公司关系或药物权益验证。</p>'
  if proof.get('directory_date_status')=='future_dated':
   body+='<p class="notice">证券目录原文日期存在未来日期异常，需另行核对；英文名称来源日期不能解除该异常或证明申办方关联。</p>'
  body+='<p class="ref">以上转录本次检索保存的来源字段；本视图未重新验真，当前查询依据是否失效另列记录。</p></details>'
 return body

def enqueue_batch(market,limit=20):
 if not 1<=limit<=20:raise ValueError('Batch size must be 1-20')
 from .market_traversal import enqueue_traversal
 return enqueue_traversal(market,limit=limit)['new_jobs']

def render(app, q):
 m = Market(app.workspace, app.state)
 init_schema(m)

 def form(action, fields, label):
  return ('<form method="post" action="' + action + '"><input type="hidden" name="token" value="'
          + e(app.token) + '">' + ''.join('<input type="hidden" name="' + e(k) + '" value="' + e(v)
                                         + '">' for k, v in fields.items())
          + '<button>' + e(label) + '</button></form>')

 body = ('<div class="eyebrow">ISSUER → REGISTRY SPONSOR</div><h1>申办方关联</h1>'
         '<p class="lead">为尚未关联的药企查找登记主体，保留依据后再决定是否接受。</p>'
         '<p><a href="/coverage">覆盖进度 →</a> · <a href="/market">临床项目 →</a></p>'
         '<div class="notice">名称相符只是一条关联线索。请核对证券主体、母子公司和历史名称；'
         '接受后才登记该申办方并排队抓取。此记录不确认药物权益，也不构成科学审批。</div>')
 body += ('<div class="card"><h2>推进下一批</h2><p>每批最多20家，优先未检索且有可用英文名称的候选。'
          '仅产生待核对建议，不自动接受。</p>' + form('/market/resolve-batch', {}, '检索下一批申办方') + '</div>')
 from .market_traversal import readiness,LABELS as TRAVERSAL_LABELS
 traversal=readiness(m,q.get('market',''))
 body+='<section class="card"><h2>遍历当前候选范围</h2><p>尚未关联的候选 '+str(traversal['total_unmapped'])+' 家；可排队 '+str(traversal['counts']['ready'])+' 家，已有任务 '+str(traversal['counts']['active'])+' 家，需要准确英文主体 '+str(traversal['counts']['needs_name'])+' 家。</p>'+form('/market/resolve-all',{'market':q.get('market','')},'遍历全部可检索候选')+'<p class="ref">每次最多1000家，跳过活跃任务及尚在冷却期的记录；只生成待核对建议，不能将查询零条当作没有管线。</p><details><summary>查看遍历范围与缺口</summary>'+''.join('<p>'+e(label)+'：'+str(traversal['counts'][key])+'</p>' for key,label in TRAVERSAL_LABELS.items())+'</details></section>'
 from .identity_view import render as identity_panel
 body+=identity_panel(app,m,q)
 rows, total = candidate_rows(m, q=q.get('q', ''), market_filter=q.get('market', ''),
                             status='unmapped', limit=20, classification='active')
 body += ('<details class="card"><summary>选择未关联公司 · 匹配 ' + str(total) + ' 条，展示前20条</summary>'
          '<form><label for="sponsor-q">公司名称或代码</label><input id="sponsor-q" name="q" value="'
          + e(q.get('q', '')) + '"><select name="market" aria-label="市场"><option value="">全部市场</option>'
          + ''.join('<option ' + ('selected ' if q.get('market') == code else '') + 'value="' + code + '">'
                    + label + '</option>' for code, label in [('US', '美股'), ('A', 'A股'), ('HK', '港股')])
          + '</select><button>筛选候选</button></form>')
 contexts=candidate_query_contexts(m,rows)
 for row in rows:
  context=contexts[row['id']];query=context['query']
  body += ('<div class="card"><b>' + e(row['name'] + ' · ' + row['id']) + '</b><p>'
           + ('检索名称：' + e(query) if query else '需要先取得准确英文主体；平台不会自动翻译后归属。') + '</p>')
  body += _query_details(context)
  if query:
   body += form('/market/resolve', {'candidate_id': row['id']}, '检索此公司申办方')
  body += '</div>'
 body += '</details><h2>待核对的关联建议</h2>'
 pending = proposals(m, status='pending', limit=50)
 if not pending:
  body += '<div class="empty">暂时没有待核对建议。可先检索候选公司，再核对返回的申办方。</div>'
 for index, row in enumerate(pending):
  body += ('<article class="card" id="proposal-' + e(row['id']) + '"><span class="badge">建议 · 待主体核对</span><h3>'
           + e(row['candidate_name']) + ' → ' + e(row['alias']) + '</h3><dl class="source-meta">'
           '<dt>证券主体</dt><dd>' + e(row['candidate_id']) + '</dd><dt>匹配方式</dt><dd>'
           + e(row['match_type']) + '</dd><dt>检索状态</dt><dd>'
           + e(LABEL.get(row['resolution_status'], row['resolution_status'])) + '</dd>'
           '<dt>登记来源</dt><dd>' + _source_link(row['source_url'], '登记来源 ' + ', '.join(row['evidence_nct'][:5]))
           + '</dd></dl>')
  if row['candidate_changed']:
   body += '<p class="notice">候选识别依据已变化，需要重新检索。</p>'
  elif row.get('query_input_stale'):
   body += '<p class="notice">查询名称或其来源依据已变化或暂不可验证，需要重新检索。'+e(row.get('stale_reason'))+'</p>'
  body += ('<details><summary>查看证据并记录决定</summary><dl class="source-meta">'
           '<dt>来源查询完成</dt><dd>' + _timestamp(row['resolved_at']) + '</dd><dt>证据批次</dt><dd><code>'
           + e(row['run_id']) + '</code></dd></dl>'
           '<p class="data-note">当前为关联建议，尚待主体核对。来源核对、记录人声明与人工签署分别记录；'
           '名称相符不能替代药物权益核验或科学审批。</p>')
  body += '<p>本次查询：'+e(row.get('query_used'))+'</p>'+_query_details(row.get('query_evidence_json'))
  if not row['candidate_changed'] and not row.get('query_input_stale'):
   field_id = 'mapping-decision-' + str(index)
   body += ('<p class="notice">选择“接受关联并排队抓取”并保存后，将登记该申办方关联并排队抓取临床登记。'
            '选择“排除此关联建议”只记录排除决定。请先核对上述来源和证券主体。</p>'
            '<form method="post" action="/market/proposal-review"><input type="hidden" name="token" value="'
            + e(app.token) + '"><input type="hidden" name="proposal_id" value="' + e(row['id']) + '">'
            '<label for="' + field_id + '-reviewer">记录人</label><input id="' + field_id
            + '-reviewer" name="reviewer" required maxlength="100"><label for="' + field_id
            + '-reason">主体核对依据</label><textarea id="' + field_id
            + '-reason" name="reason" required minlength="10" maxlength="5000" '
            'placeholder="公司来源如何证明该登记申办方属于这一证券主体？"></textarea><label for="'
            + field_id + '-choice">判断</label><select id="' + field_id + '-choice" name="decision">'
            '<option value="accepted">接受关联并排队抓取</option><option value="rejected">排除此关联建议</option>'
            '</select><p><button>保存主体核对</button></p></form>')
  body += '</details></article>'
 recent = resolution_runs(m, limit=30)
 body += '<details class="card"><summary>近期检索与缺口 · ' + str(len(recent)) + ' 条</summary>'
 if not recent:
  body += '<p class="data-note">尚未记录申办方检索。</p>'
 for row in recent:
  body += ('<div class="card"><b>' + e(row['candidate_id']) + ' · '
           + e(LABEL.get(row['status'], row['status'])) + '</b><p>查询名称 ' + e(row['query'])
           + ' · 建议 ' + str(row['proposal_count']) + ' 条</p><p>' + e(row['error'])
           + '</p><p class="ref">' + _timestamp(row['started']) + ' · ' + e(row['id']) + '</p></div>')
  body += _query_details(row.get('query_evidence_json'))
 body += '</details>'
 with m.db() as db:
  if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sponsor_proposal_reviews'").fetchone():
   typed = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sponsor_review_evidence'").fetchone()
   query = ('SELECT r.*,e.reviewer_type,e.evidence_json,e.evidence_sha256 FROM sponsor_proposal_reviews r LEFT JOIN sponsor_review_evidence e '
            'ON e.review_id=r.id ORDER BY r.created DESC LIMIT 20') if typed else (
            "SELECT r.*,'human_declared' AS reviewer_type,NULL AS evidence_json,NULL AS evidence_sha256 FROM sponsor_proposal_reviews r ORDER BY r.created DESC LIMIT 20")
   reviewed = db.execute(query).fetchall()
   body += '<details class="card"><summary>最近主体核对记录 · ' + str(len(reviewed)) + ' 条</summary>'
   if not reviewed:
    body += '<p class="data-note">尚未记录主体核对决定。</p>'
   for row in reviewed:
    curated = row['reviewer_type'] == 'curated_official_source_review'
    a_share = row['reviewer_type'] == 'a_share_directory_source_review'
    automated = row['reviewer_type'] == 'automated_source_review' or curated or a_share
    human_declared = row['reviewer_type'] == 'human_declared'
    label = ('A股官方法人名与登记核对，非人工签署' if a_share else '官方资料与登记名称核对，非人工签署' if curated else '自动双源名称核对，非人工签署' if automated else
             '记录人声明的主体核对' if human_declared else '核对类型未记录')
    signing = '人工签署：无（自动来源核对）' if automated else '人工签署：未验证'
    body += ('<div class="card"><span class="badge">' + label + '</span><b>' + e(row['reviewer'])
             + ' · ' + ('接受' if row['decision'] == 'accepted' else '排除') + '</b><p>' + e(row['reason'])
             + '</p><p class="cell-meta">' + signing + ' · 药物权益与科学审批分别核对</p><p class="ref">'
             + _timestamp(row['created']) + ' · ' + e(row['proposal_id']) + '</p>')
    if a_share:
     body += '<p class="data-note">仅核对官方法人名与精确登记申办方；药物研发业务类型尚未独立确认。</p>'
    if row['evidence_json']:
     body+='<details><summary>查看已记录的来源依据与校验值</summary><p class="ref">'+e(row['evidence_sha256'])+'</p><pre>'+e(row['evidence_json'])+'</pre></details>'
    body+='</div>'
   body += '</details>'
 return body
