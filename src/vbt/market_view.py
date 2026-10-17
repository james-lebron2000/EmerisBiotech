"""Searchable clinical observations with visible provenance and collection health."""
import html,json
from urllib.parse import urlencode
from .market import Market
from .market_sources import URLS

SOURCES={'US':'SEC 美股名录','HK':'HKEX 港股证券','SH':'上交所主板 A 股','STAR':'上交所科创板','SZ':'深交所 A 股','USNASDAQ':'Nasdaq 上市证券','USOTHER':'Nasdaq Trader 其他交易所证券'}
STATES={'queued':'已排队','running':'采集中','succeeded':'查询完成','failed':'失败','partial':'分页未完成','interrupted':'中断','cancelled':'已取消'}
PHASES={'PHASE1':'一期','PHASE2':'二期','PHASE3':'三期','PHASE4':'四期','EARLY_PHASE1':'早期一期'}
TRIAL_STATES={'RECRUITING':'招募中','ACTIVE_NOT_RECRUITING':'进行中，停止招募','COMPLETED':'已完成','TERMINATED':'已终止','NOT_YET_RECRUITING':'尚未招募','WITHDRAWN':'已撤回','SUSPENDED':'暂停','UNKNOWN':'未知'}

def e(v):return html.escape(str(v if v is not None else '未知'),quote=True)
def render(app,q):
 m=Market(app.workspace,app.state);companies=m.rows('companies');runs=sorted(m.rows('runs'),key=lambda r:r['started'],reverse=True)
 company_names={c['id']:c['name'] for c in companies}
 def form(action,fields,label):return '<form method="post" action="'+action+'"><input type="hidden" name="token" value="'+e(app.token)+'">'+''.join('<input type="hidden" name="'+e(k)+'" value="'+e(v)+'">' for k,v in fields.items())+'<button>'+e(label)+'</button></form>'
 with m.db() as db:
  unique_trials=db.execute('SELECT count(DISTINCT nct) FROM trials').fetchone()[0]
  source_counts={r['source']:r['n'] for r in db.execute('SELECT source,count(*) n FROM listings GROUP BY source')}
 body='<div class="eyebrow">CLINICAL INTELLIGENCE</div><h1>市场与管线雷达</h1><p class="lead">查公司、药物和适应症，追溯每一条临床登记。</p><p><a class="button" href="/coverage">查看覆盖进度与缺口</a> <a href="/assets">候选药物与试验关联 →</a></p>'
 body+='<div class="grid">'+''.join('<div class="card"><span>'+k+'</span><strong class="metric">'+str(v)+'</strong></div>' for k,v in [('已配置证券',len(companies)),('唯一 NCT 试验',unique_trials),('证券—试验关联',m.count('trials')),('历史变更',m.count('changes'))])+'</div><p class="notice">这些是公开登记观察，包含历史试验、联合用药及对照治疗。公司归属、药物权益和完整管线仍需逐项核对；完成状态不表示临床成功。</p>'
 market=q.get('market','');query=q.get('q','').strip();cid=q.get('company','')
 body+='<form class="card"><h2>查找临床项目</h2><label for="market-query">公司、代码、药物或适应症</label><input id="market-query" name="q" value="'+e(query)+'" placeholder="例如 恒瑞、PFE、PD-1、breast cancer"><label for="market-filter">市场</label><select id="market-filter" name="market"><option value="">全部市场</option>'+''.join('<option '+('selected ' if market==v else '')+'value="'+v+'">'+label+'</option>' for v,label in [('US','美股'),('A','A股'),('HK','港股')])+'</select> <select name="company" aria-label="公司"><option value="">全部已配置公司</option>'+''.join('<option '+('selected ' if cid==c['id'] else '')+'value="'+e(c['id'])+'">'+e(c['name']+' · '+c['id'])+'</option>' for c in sorted(companies,key=lambda c:c['id']))+'</select> <select name="phase" aria-label="临床阶段"><option value="">全部阶段</option>'+''.join('<option '+('selected ' if q.get('phase')==v else '')+'value="'+v+'">'+label+'</option>' for v,label in PHASES.items())+'</select> <select name="status" aria-label="试验状态"><option value="">全部状态</option>'+''.join('<option '+('selected ' if q.get('status')==v else '')+'value="'+v+'">'+label+'</option>' for v,label in TRIAL_STATES.items())+'</select><p><button>查询</button> <a href="/market">清除筛选</a></p></form>'
 try:offset=max(0,int(q.get('offset','0')))
 except ValueError:offset=0
 rows,total=m.search_trials(market,cid,query,q.get('phase',''),q.get('status',''),offset=offset)
 body+='<h2>药物／生物制品试验线索 · '+str(total)+' 条</h2>'+form('/market/export',{k:q[k] for k in ('market','company','q','phase','status') if q.get(k)},'导出筛选结果 CSV')+'<p>每页 200 条，按来源更新日期排序。多地上市会产生重复证券关联；顶部另列去重后的 NCT 数量。</p>'
 if rows:
  body+='<div class="scroll"><table class="clinical-table"><tr><th>公司 / 试验</th><th>干预与适应症</th><th>阶段 / 状态</th><th>来源更新 / 抓取时间</th></tr>'
  for r in rows:
   d=json.loads(r['data']);body+='<tr><td><a href="/assets?'+e(urlencode({'company':r['company']}))+'">'+e(company_names.get(r['company'],r['company']))+'</a><div class="ref">'+e(r['company'])+'</div><a target="_blank" rel="noopener" href="'+e(d['source_url'])+'">'+e(r['nct'])+' ↗</a><p>'+e(d['title'])+'</p></td><td>'+e(' / '.join(x['name'] for x in d['interventions']))+'<p>'+e(' / '.join(d['conditions']))+'</p></td><td>'+e(' / '.join(PHASES.get(p,p) for p in d['phases']) or '未分期')+'<br>'+e(TRIAL_STATES.get(d['status'],d['status']))+'</td><td>'+e(d['source_updated'])+'<br><span class="ref">'+e(r['observed'])+'</span></td></tr>'
  body+='</table></div>'
 else:body+='<div class="empty">没有匹配的已采集记录。可调整条件，或到覆盖页面查看该公司是否尚未关联、采集失败或未返回记录。</div>'
 if offset:body+='<a href="/market?'+e(urlencode({**q,'offset':max(0,offset-200)}))+'">← 上一页</a> '
 if offset+200<total:body+='<a href="/market?'+e(urlencode({**q,'offset':offset+200}))+'">下一页试验 →</a>'
 enabled=m.setting('enabled')=='1'
 body+='<details class="card"><summary>数据更新与来源 · '+('自动更新已开启' if enabled else '自动更新未开启')+'</summary><p>工作台或独立采集服务运行时，名录每 24 小时、已配置公司的登记每 6 小时检查一次。任务持久排队，最多同时采集两项；源站披露延迟仍然存在。</p>'+form('/market/schedule',{'enabled':'0' if enabled else '1'},'暂停自动更新' if enabled else '开启自动更新')+'<table><tr><th>来源</th><th>最近任务</th><th>条目</th><th>操作</th></tr>'
 for source in URLS:
  last=next((r for r in runs if r['source']==source),None)
  body+='<tr><td>'+e(SOURCES.get(source,source))+'</td><td>'+e(STATES.get(last['status'],last['status'])+' · '+last['started'] if last else '尚未抓取')+'</td><td>'+str(source_counts.get(source,0))+'</td><td>'+form('/market/refresh',{'kind':'universe','reference':source},'立即抓取')+'</td></tr>'
 body+='</table></details><details class="card"><summary>添加公司与申办方映射</summary><p>填写准确的登记申办方名称及主体映射来源；该操作不会确认药物所有权。现有映射不被静默覆盖。</p><form method="post" action="/market/company"><input type="hidden" name="token" value="'+e(app.token)+'"><label for="company-market">市场</label><select id="company-market" name="market"><option>US</option><option>A</option><option>HK</option></select><label for="ticker">证券代码</label><input id="ticker" name="ticker" required maxlength="16"><label for="company-name">公司名称</label><input id="company-name" name="name" required maxlength="200"><label for="aliases">申办方别名（每行一个）</label><textarea id="aliases" name="aliases" required></textarea><label for="provenance">映射依据／来源链接</label><input id="provenance" name="provenance" required maxlength="2000"><p><button>登记映射</button></p></form></details>'
 body+='<h2>最近登记变化</h2>'
 for r in m.recent_changes(limit=10):
  before=json.loads(r['before_json']) if r['before_json'] else {};after=json.loads(r['after_json']);keys=[k for k in after if before.get(k)!=after[k]]
  body+='<details class="card"><summary>'+e(company_names.get(r['company'],r['company'])+' · '+r['nct']+' · '+('首次观察' if not before else ' / '.join(keys)))+'</summary><p>'+e(r['observed'])+'</p><pre>'+e(json.dumps({'before':before,'after':after},ensure_ascii=False,indent=2))+'</pre></details>'
 return body
