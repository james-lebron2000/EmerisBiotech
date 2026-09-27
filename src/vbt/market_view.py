import html,json
from urllib.parse import urlencode
from .market import Market
from .market_sources import URLS

def e(v):return html.escape(str(v if v is not None else '未知'),quote=True)
def render(app,q):
 m=Market(app.workspace,app.state);companies=m.rows('companies');listings=m.rows('listings');runs=sorted(m.rows('runs'),key=lambda r:r['started'],reverse=True)
 def form(action,fields,label):return '<form method="post" action="'+action+'"><input type="hidden" name="token" value="'+e(app.token)+'">'+''.join('<input type="hidden" name="'+e(k)+'" value="'+e(v)+'">' for k,v in fields.items())+'<button>'+e(label)+'</button></form>'
 body='<div class="eyebrow">LIVE SOURCE OBSERVATIONS</div><h1>市场与管线雷达</h1><p class="lead">美股 · A股 · 港股，从证券名录追踪到临床登记。</p><div class="notice">当前为公开来源监测。证券条数不是药企数；登记试验不是公司完整管线。尚未覆盖全部临床前项目、中国境内登记、授权关系或北交所名录。覆盖率分母未知，不报告全市场覆盖率。</div>'
 body+='<div class="grid">'+''.join('<div class="card"><span>'+k+'</span><strong class="metric">'+str(v)+'</strong></div>' for k,v in [('证券名录条目',len(listings)),('已配置公司映射',len(companies)),('试验归属观察',m.count('trials')),('记录变更',m.count('changes'))])+'</div>'
 enabled=m.setting('enabled')=='1'
 body+='<div class="card"><h2>自动更新 · '+('已开启' if enabled else '未开启')+'</h2><p>服务运行时，公司名录每 24 小时、已配置申办方的临床登记每 6 小时检查一次；可手动立即刷新。上游披露延迟仍然存在，不代表秒级实时。关闭工作台后自动更新暂停。</p>'+form('/market/schedule',{'enabled':'0' if enabled else '1'},'暂停自动更新' if enabled else '开启自动更新')+'</div>'
 body+='<h2>名录数据源</h2><div class="scroll"><table><tr><th>市场来源</th><th>最近任务</th><th>条目</th><th>操作</th></tr>'
 for source in URLS:
  last=next((r for r in runs if r['source']==source),None)
  body+='<tr><td>'+e({'US':'SEC 美股名录','HK':'HKEX 港股证券','SH':'上交所主板 A 股','STAR':'上交所科创板','SZ':'深交所 A 股','USNASDAQ':'Nasdaq 上市证券','USOTHER':'Nasdaq Trader 其他交易所证券'}[source])+'</td><td>'+e((last['status']+' · '+last['started']) if last else '尚未抓取')+'</td><td>'+str(sum(x['source']==source for x in listings))+'</td><td>'+form('/market/refresh',{'kind':'universe','reference':source},'立即抓取')+'</td></tr>'
 body+='</table></div><details class="card"><summary>添加公司与申办方映射</summary><p>可从下方证券名录确定代码。逐行填写 ClinicalTrials.gov 中的准确英文申办方名称；映射需人工核对，申办方身份不证明药物所有权。已有映射不自动覆盖。</p><form method="post" action="/market/company"><input type="hidden" name="token" value="'+e(app.token)+'"><label for="market">市场</label><select id="market" name="market"><option>US</option><option>A</option><option>HK</option></select><label for="ticker">证券代码</label><input id="ticker" name="ticker" required maxlength="16"><label for="company-name">公司名称</label><input id="company-name" name="name" required maxlength="200"><label for="aliases">申办方别名（每行一个）</label><textarea id="aliases" name="aliases" required></textarea><label for="provenance">映射依据／来源链接</label><input id="provenance" name="provenance" required maxlength="2000"><p><button>登记映射</button></p></form></details><h2>公司观察池</h2>'
 for c in companies:
  last=next((r for r in runs if r['company']==c['id']),None)
  body+='<div class="card"><h3>'+e(c['name'])+' · '+e(c['id'])+'</h3><p>准确匹配的申办方：'+e(' / '.join(json.loads(c['aliases'])))+'</p><p class="ref">映射依据：'+e(c['provenance'])+'</p><p>'+e(last['status']+' · '+last['started'] if last else '尚未抓取')+'</p>'+form('/market/refresh',{'kind':'company','reference':c['id']},'抓取临床登记')+'<a href="/market?'+urlencode({'company':c['id']})+'">查看该公司试验 →</a></div>'
 if not companies:body+='<div class="empty">先登记公司与申办方映射，再抓取临床登记。没有记录不表示没有管线。</div>'
 market=q.get('market','');query=q.get('q','').casefold();cid=q.get('company','')
 body+='<h2>查询证券与试验</h2><form><label for="market-query">名称、代码、药物或适应症</label><input id="market-query" name="q" value="'+e(q.get('q',''))+'"><select name="market"><option value="">全部市场</option>'+''.join('<option '+('selected ' if market==v else '')+'value="'+v+'">'+v+'</option>' for v in ['US','A','HK'])+'</select><select name="phase" aria-label="临床阶段"><option value="">全部阶段</option>'+''.join('<option '+('selected ' if q.get('phase')==v else '')+'value="'+v+'">'+v+'</option>' for v in ['PHASE1','PHASE2','PHASE3','PHASE4'])+'</select><select name="status" aria-label="试验状态"><option value="">全部状态</option>'+''.join('<option '+('selected ' if q.get('status')==v else '')+'value="'+v+'">'+v+'</option>' for v in ['RECRUITING','ACTIVE_NOT_RECRUITING','COMPLETED','TERMINATED'])+'</select><button>查询</button></form>'
 selected=[r for r in listings if (not market or r['market']==market) and query in (r['name']+' '+r['ticker']+' '+r['industry']).casefold()]
 body+='<details class="card"><summary>证券名录 · 匹配 '+str(len(selected))+' 条，展示前 100 条</summary><table><tr><th>证券</th><th>名称</th><th>行业原文</th><th>观察时间</th></tr>'+''.join('<tr><td>'+e(r['exchange']+':'+r['ticker'])+'</td><td>'+e(r['name'])+'</td><td>'+e(r['industry'] or '未分类')+'</td><td>'+e(r['observed'])+'</td></tr>' for r in selected[:100])+'</table></details>'
 try:offset=max(0,int(q.get('offset','0')))
 except ValueError:offset=0
 rows,total=m.search_trials(market,cid,query,q.get('phase',''),q.get('status',''),offset=offset)
 body+='<h2>药物／生物制品试验线索 · '+str(total)+' 条</h2><p>包含对照治疗和联合用药。试验阶段、完成日期不能直接当作资产阶段、读出时间或成功结局；同一试验跨公司可能重复。</p><div class="scroll"><table><tr><th>公司 / 试验</th><th>干预与适应症</th><th>阶段 / 状态</th><th>来源更新 / 抓取时间</th></tr>'
 for r in rows:
  d=json.loads(r['data']);body+='<tr><td>'+e(r['company'])+'<br><a target="_blank" rel="noopener" href="'+e(d['source_url'])+'">'+e(r['nct'])+'</a><p>'+e(d['title'])+'</p></td><td>'+e(' / '.join(x['name'] for x in d['interventions']))+'<p>'+e(' / '.join(d['conditions']))+'</p></td><td>'+e(' / '.join(d['phases']) or '未分期')+'<br>'+e(d['status'])+'</td><td>'+e(d['source_updated'])+'<br>'+e(r['observed'])+'</td></tr>'
 body+='</table></div><p class="muted">按来源更新日期排序，每页 200 条；可按公司、市场或关键词缩小范围。完整观察保存在本机注册表。</p><h2>最近变更</h2>'
 if offset:body+='<a href="/market?'+e(urlencode({**q,'offset':max(0,offset-200)}))+'">← 上一页</a> '
 if offset+200<total:body+='<a href="/market?'+e(urlencode({**q,'offset':offset+200}))+'">下一页试验 →</a>'
 for r in m.recent_changes():
  before=json.loads(r['before_json']) if r['before_json'] else {};after=json.loads(r['after_json']);keys=[k for k in after if before.get(k)!=after[k]]
  body+='<details class="card"><summary>'+e(r['company']+' · '+r['nct']+' · '+('首次观察' if not before else ', '.join(keys)))+'</summary><p>'+e(r['observed'])+'</p><pre>'+e(json.dumps({'before':before,'after':after},ensure_ascii=False,indent=2))+'</pre></details>'
 body+='<h2>抓取任务与失败记录</h2><p>失败或达到分页上限会保留旧记录，旧记录不表示仍为当前状态。succeeded 仅表示该查询完成。</p>'
 for r in runs[:30]:body+='<div class="card"><b>'+e(r['source']+' '+r['company']+' · '+r['status'])+'</b><p>'+e(r['started'])+' · '+str(r['count'])+' 条</p><p class="body">'+e(r['error'])+'</p><span class="ref">'+e(r['id'])+'</span></div>'
 return body
