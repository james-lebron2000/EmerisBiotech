"""Company-level drug/trial candidate exploration; ownership always unresolved."""
import html
from urllib.parse import urlencode
from .market import Market
from .market_assets import build_assets

def e(value):return html.escape(str(value if value is not None else '未知'),quote=True)
ROLES={'experimental_arm':'在实验组中出现','comparator_only':'仅在比较组中出现','unresolved':'用药角色待核对'}
FRESH={'seen_in_latest':'最新完整抓取中可见','not_seen_in_latest':'仅保留于历史观察','unknown':'批次关联尚未建立'}

def render(app,q):
 m=Market(app.workspace,app.state);companies=sorted(m.rows('companies'),key=lambda c:c['id']);cid=q.get('company','');query=q.get('q','').strip().casefold();role=q.get('role','')
 body='<div class="eyebrow">DRUG · INDICATION · TRIAL</div><h1>候选药物与试验关联</h1><p class="lead">按公司查看药物名称、适应症和阶段，并展开原始试验依据。</p><p><a href="/coverage">覆盖进度与缺口 →</a> · <a href="/market">临床登记检索 →</a></p><div class="notice">药物名称按原文精确归并，尚未合并所有研发代号与通用名。实验组中出现的药物也可能是标准治疗或联合用药；药物所有权及授权地域均待核对。</div><form class="card"><label for="asset-company">公司</label><select id="asset-company" name="company" required><option value="">请选择公司</option>'+''.join('<option '+('selected ' if cid==c['id'] else '')+'value="'+e(c['id'])+'">'+e(c['name']+' · '+c['id'])+'</option>' for c in companies)+'</select><label for="asset-query">药物、试验或适应症</label><input id="asset-query" name="q" value="'+e(q.get('q',''))+'"><label for="asset-role">用药角色</label><select id="asset-role" name="role"><option value="">全部角色</option>'+''.join('<option '+('selected ' if role==key else '')+'value="'+key+'">'+label+'</option>' for key,label in ROLES.items())+'</select><p><button>查看药物关联</button></p></form>'
 if not cid:return body+'<div class="empty">选择一家已配置公司，查看候选药物及其对应的试验。尚未配置的公司可在覆盖页面查看缺口。</div>'
 if not any(c['id']==cid for c in companies):raise ValueError('公司尚未配置')
 assets=build_assets(m,cid);selected=[r for r in assets if (not role or r['role_status']==role) and (not query or query in (' '.join([r['drug_name']]+r['trials']+r['conditions'])).casefold())]
 try:offset=max(0,int(q.get('offset',0)))
 except ValueError:offset=0
 body+='<h2>'+e(next(c['name'] for c in companies if c['id']==cid))+' · '+str(len(selected))+' 个名称关联</h2><p>同一药物的不同剂量、剂型或研发代号可能仍分开列出，不能将名称数量当作独立资产数量。</p>'
 for r in selected[offset:offset+50]:
  body+='<article class="card"><span class="badge">权益待核对</span> <span class="badge">'+e(ROLES[r['role_status']])+'</span><h2>'+e(r['drug_name'])+'</h2><p>'+str(len(r['trials']))+' 项登记试验 · 观察到的试验阶段：'+e(' / '.join(r['phases']) or '未分期')+'</p><p>适应症原文：'+e(' / '.join(r['conditions']))+'</p><p>'+e(FRESH[r['freshness']])+' · 来源最近更新 '+e(r['source_updated'])+'</p><details><summary>查看逐项试验与关联依据</summary><table><tr><th>试验</th><th>用药角色 / 状态</th><th>阶段 / 适应症</th><th>日期与批次</th></tr>'
  for source in r['sources']:
   body+='<tr><td><a href="'+e(source['url'])+'" target="_blank" rel="noopener">'+e(source['nct'])+' ↗</a><p>'+e(source.get('title',''))+'</p></td><td>'+e(' / '.join(source['arm_roles']) or '角色未记载')+'<p>'+e(source.get('status',''))+'</p></td><td>'+e(' / '.join(source.get('phases',[])))+'<p>'+e(' / '.join(source.get('conditions',[])))+'</p></td><td>'+e(source['source_updated'])+'<p>'+e(FRESH[source['freshness']])+'</p><span class="ref">'+e(source['last_run'])+'</span></td></tr>'
  body+='</table></details></article>'
 if not selected:body+='<div class="empty">没有匹配的已验收药物名称关联。尚未采集、查询零条或分页未完成都可能导致此处为空；请查看覆盖页状态。</div>'
 if offset:body+='<a href="/assets?'+e(urlencode({**q,'offset':max(0,offset-50)}))+'">← 上一页</a> '
 if offset+50<len(selected):body+='<a href="/assets?'+e(urlencode({**q,'offset':offset+50}))+'">下一页 →</a>'
 return body
