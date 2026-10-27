"""Script-free patient showcase using the workbench shell and domain results."""
import html
import math
from urllib.parse import urlencode
from .patient_lab import PLANS, snapshot

COLORS = {'a': '#16858a', 'b': '#8961b0', 'ab': '#bd7728'}
DASH = {'a': '', 'b': '7 4', 'ab': '2 4'}


def esc(value):
    return html.escape(str(value), quote=True)


STYLE = '''<style>
.patientlab{--pl-a:#16858a;--pl-b:#8961b0;--pl-ab:#bd7728}.patientlab .pl-head{display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap;align-items:start}.patientlab .pl-kicker{font-size:11px;letter-spacing:1.8px;color:var(--muted);margin:0 0 6px}.patientlab h1{margin:0 0 9px}.patientlab .pl-sub{color:var(--muted);margin:0 0 15px}.patientlab .pl-pill{display:inline-block;background:var(--teal-light);color:#12685d;border-radius:6px;padding:6px 10px;font-size:12px}.patientlab .pl-banner{background:#fff3da;padding:12px 16px;border-radius:8px;line-height:1.8;margin:16px 0;color:#5c4820}.patientlab .pl-flow{display:flex;flex-wrap:wrap;gap:9px 20px;font-size:12px;color:var(--muted);margin:18px 0}.patientlab .pl-flow b{font-weight:600;color:#166b66}.patientlab .pl-layout{display:grid;grid-template-columns:255px minmax(0,1fr);gap:20px;align-items:start}.patientlab .pl-profile,.patientlab .pl-stage{background:white;border:1px solid var(--line);border-radius:10px;padding:18px;min-width:0}.patientlab h2{font-size:16px;margin:0 0 12px}.patientlab h3{font-size:13px;margin:15px 0 8px}.patientlab .pl-id{font-size:23px;letter-spacing:.5px;font-weight:600}.patientlab .pl-muted{font-size:12px;color:var(--muted);line-height:1.8}.patientlab .pl-fields{margin:8px 0 0}.patientlab .pl-fields div{padding:9px 0;border-bottom:1px solid var(--line)}.patientlab .pl-fields dt{font-size:11px;color:var(--muted)}.patientlab .pl-fields dd{margin:2px 0 0;font-size:12px;overflow-wrap:anywhere}.patientlab details{margin-top:12px}.patientlab summary{cursor:pointer;font-size:12px;color:#166b66}.patientlab .pl-form{display:flex;gap:12px;flex-wrap:wrap;align-items:end;margin:16px 0}.patientlab label{font-size:12px;display:block}.patientlab select,.patientlab input[type=range]{max-width:100%}.patientlab select{font-size:13px;min-height:36px;margin-top:5px}.patientlab button,.patientlab .button{font-size:12px;min-height:36px}.patientlab .pl-plans{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin:0 0 14px}.patientlab .pl-plan{padding:12px;border:1px solid var(--line);border-radius:8px;background:#fff;text-decoration:none;color:var(--ink);min-width:0}.patientlab .pl-plan[aria-current=true]{border:2px solid #166b66;padding:11px;background:#f4faf9}.patientlab .pl-plan small{display:block;color:var(--muted);font-size:11px;margin-top:5px}.patientlab .pl-dot{width:8px;height:8px;border-radius:50%;display:inline-block;margin-right:6px}.patientlab .pl-tabs{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:15px}.patientlab .pl-tabs a{font-size:12px;padding:7px 10px;text-decoration:none;color:var(--muted);border-radius:6px}.patientlab .pl-tabs a[aria-current=page]{background:var(--teal-light);color:#166b66}.patientlab .pl-chart{display:block;width:100%;height:auto;max-height:310px}.patientlab .pl-chart text{font:13px sans-serif;fill:#5e747e}.patientlab .pl-chart .pl-grid{stroke:#dce5e9;stroke-width:1}.patientlab .pl-metrics{display:grid;grid-template-columns:1fr 1fr;gap:15px;border-top:1px solid var(--line);padding-top:13px}.patientlab .pl-value{font-size:27px;font-weight:600;display:block;margin:6px 0}.patientlab .pl-cellbar{height:10px;display:flex;border-radius:4px;overflow:hidden;margin:12px 0 8px}.patientlab .pl-row{display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap}.patientlab .pl-row small{font-size:11px;color:var(--muted)}.patientlab .pl-run{padding:13px 0;border-top:1px solid var(--line);font-size:12px}.patientlab code{overflow-wrap:anywhere;word-break:break-all}.patientlab .pl-history{display:flex;gap:8px;flex-wrap:wrap}.patientlab .pl-history a{font-size:12px;padding:6px 9px;border:1px solid var(--line);border-radius:5px}.patientlab .pl-table{width:100%;border-collapse:collapse;font-size:12px}.patientlab .pl-table th,.patientlab .pl-table td{padding:11px 7px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top;overflow-wrap:anywhere;max-width:none}.patientlab .pl-footer{margin-top:15px;font-size:11px;color:var(--muted);line-height:1.8}.patientlab .pl-scroll{overflow-x:auto}.patientlab .pl-compare{display:grid;grid-template-columns:1fr 1fr;gap:16px}.patientlab .pl-evidence{font-size:12px;line-height:1.9}.patientlab .pl-evidence dd{margin:3px 0 12px;color:var(--muted)}
.patientlab .pl-chart-small{display:none}@media(max-width:1100px){.patientlab .pl-chart-wide{display:none}.patientlab .pl-chart-small{display:block}.patientlab .pl-layout{grid-template-columns:220px minmax(0,1fr)}}
@media(max-width:800px){.patientlab .pl-layout{grid-template-columns:1fr}.patientlab .pl-profile .pl-fields{display:grid;grid-template-columns:1fr 1fr;gap:0 15px}.patientlab .pl-compare{grid-template-columns:1fr}}
@media(max-width:440px){.patientlab .pl-profile .pl-fields,.patientlab .pl-metrics{grid-template-columns:1fr}.patientlab .pl-profile,.patientlab .pl-stage{padding:13px}.patientlab .pl-plan{padding:10px 7px;font-size:12px}.patientlab .pl-plan[aria-current=true]{padding:9px 6px}.patientlab .pl-tabs{gap:2px}.patientlab .pl-tabs a{padding:8px 6px}}
</style>'''


def chart(curves, selected, week=12, survival=False):
    return _chart(curves, selected, week, survival, 680) + _chart(curves, selected, week, survival, 280)


def _chart(curves, selected, week, survival, width):
    height = 225 if width == 280 else 265
    left, right, top, bottom = 48, 14, 30, 40
    metric = 'illustrative_percent' if survival else 'burden_index'
    xs = 'month' if survival else 'week_after_cutoff'
    ymax = 100 if survival else math.ceil(max(row['scenario_high'] for rows in curves.values() for row in rows) / 20) * 20
    x = lambda t: left + t / 24 * (width - left - right)
    y = lambda v: height - bottom - v / ymax * (height - top - bottom)
    size_class = 'pl-chart-small' if width == 280 else 'pl-chart-wide'
    parts = [f'<svg class="pl-chart {size_class}" viewBox="0 0 {width} {height}" role="img" aria-label="'+('纯数学生存曲线示意，非患者预后' if survival else '三种虚构治疗的负荷情景，非临床预测')+'"><title>'+('独立数学示意' if survival else '合成病灶负荷')+'</title>']
    parts.append(f'<text x="{left}" y="17">'+('示意存活比例（%）' if survival else '负荷指数（基线＝100）')+'</text>')
    for v in (0, ymax / 2, ymax):
        parts.append(f'<line class="pl-grid" x1="{left}" x2="{width-right}" y1="{y(v):.1f}" y2="{y(v):.1f}"/><text x="{left-7}" y="{y(v)+4:.1f}" text-anchor="end">{v:g}</text>')
    for t in (0, 8, 16, 24):
        parts.append(f'<text x="{x(t):.1f}" y="{height-20}" text-anchor="'+('start' if t == 0 else 'end' if t == 24 else 'middle')+f'">{t}</text>')
    if not survival:
        rows = curves[selected]
        upper = [(x(r[xs]), y(r['scenario_high'])) for r in rows]
        lower = [(x(r[xs]), y(r['scenario_low'])) for r in reversed(rows)]
        points = ' '.join(f'{a:.1f},{b:.1f}' for a, b in upper + lower)
        parts.append(f'<polygon points="{points}" fill="{COLORS[selected]}" opacity=".12"/>')
    for key in PLANS:
        rows = curves[key]
        path = ' '.join(('M' if i == 0 else 'L') + f'{x(r[xs]):.1f},{y(r[metric]):.1f}' for i, r in enumerate(rows))
        parts.append(f'<path d="{path}" fill="none" stroke="{COLORS[key]}" stroke-width="{3 if key == selected else 1.8}" stroke-dasharray="{DASH[key]}"/>')
    if not survival:
        v = curves[selected][week][metric]
        parts.append(f'<line x1="{x(week):.1f}" x2="{x(week):.1f}" y1="{top}" y2="{height-bottom}" stroke="#7c909a" stroke-dasharray="3 4"/><circle cx="{x(week):.1f}" cy="{y(v):.1f}" r="4" fill="{COLORS[selected]}"/>')
    parts.append(f'<text x="{width-right}" y="{height-2}" text-anchor="end">'+('情景时间（月）' if survival else '当前决策时点后（周）')+'</text></svg>')
    return ''.join(parts)


def render(app, query):
    if set(query) - {'id', 'plan', 'week', 'view', 'saved'}:
        raise ValueError('Unknown patient scenario query')
    selected, view = query.get('plan', 'a'), query.get('view', 'response')
    try:
        week = int(query.get('week', '12'))
    except (ValueError, TypeError):
        raise ValueError('Invalid scenario week') from None
    if selected not in PLANS or view not in ('response', 'prognosis', 'evidence') or not 0 <= week <= 24:
        raise ValueError('Invalid patient scenario view')
    stored = app.patient_lab.get(query['id']) if query.get('id') else None
    data = stored['result'] if stored else None
    patient = data['patient'] if data else snapshot()
    def url(**changes):
        params = {'plan': selected, 'week': week, 'view': view}
        if stored:
            params['id'] = stored['run_id']
        params.update(changes)
        return '/patient-lab?' + esc(urlencode(params))
    def fields(values):
        return '<dl class="pl-fields">' + ''.join('<div><dt>'+esc(k)+'</dt><dd>'+esc(v)+'</dd></div>' for k, v in values.items()) + '</dl>'
    token = '<input type="hidden" name="token" value="'+esc(app.token)+'">'
    body = STYLE + '<section class="patientlab"><div class="pl-head"><div><p class="pl-kicker">PATIENT SCENARIO LAB</p><h1>同一个患者，不同治疗路径</h1><p class="pl-sub">宏观病程 × 微观状态 → 方案推演 → 随访更新</p></div><span class="pl-pill">合成病例 · 研究演示</span></div>'
    body += '<div class="pl-banner"><strong>当前为合成病例与虚构治疗。</strong>数值来自明确的演练假设，不是对真实患者的疗效、毒性或寿命预测。</div><div class="pl-flow"><span>① 查看已有资料</span><span>② 选择状态假设</span><b>③ 运行并保存推演</b><span>④ 加载随访并保留旧版本</span></div>'
    body += '<div class="pl-layout"><aside class="pl-profile"><span class="pl-id">'+esc(patient['patient_id'])+'</span><p class="pl-muted">当前资料截止：第 '+str(patient['cutoff_week'])+' 周<br>匿名患者编号 · 合成资料</p><h2>宏观临床</h2>'+fields(patient['clinical'])+'<details><summary>微观生物与缺失资料</summary>'+fields(patient['molecular'])+'</details><h3>当前可见的合成观测</h3>'
    for obs in patient['observations']:
        body += '<p class="pl-muted">第 '+str(obs['week'])+' 周 · 指数 '+str(obs['burden_index'])+'<br>'+esc(obs['source_id'])+'</p>'
    body += '<p class="pl-muted">部位与组学字段仅为背景，未用于计算；未提供不等于阴性。</p></aside><div>'
    body += '<form class="pl-form" method="post" action="/patient-lab/run">'+token+'<label for="resistance">新建基线推演的耐药假设<select id="resistance" name="resistance"><option value="low"'+(' selected' if not data or data['resistance_assumption'] == 'low' else '')+'>低耐药：20%</option><option value="high"'+(' selected' if data and data['resistance_assumption'] == 'high' else '')+'>高耐药：45%</option></select></label><button type="submit">运行并保存新推演</button></form>'
    if not data:
        body += '<div class="pl-stage"><h2>从 P001 的同一起点展开未来分支</h2><p class="pl-muted">点击“运行并保存新推演”，计算虚构 A、B、A＋B 的 24 周负荷情景，生成可重开的独立记录。随后可查看预后展示、导出 JSON，逐次加载第 6、12 周的预设合成随访。</p><p class="pl-muted">页面查看不会自动写入记录或运行模型；当前未执行推演。</p></div>'
    else:
        body += '<div class="pl-plans">'
        for key, plan in PLANS.items():
            body += '<a class="pl-plan" aria-current="'+str(key == selected).lower()+'" href="'+url(plan=key)+'"><span class="pl-dot" style="background:'+COLORS[key]+'"></span>'+esc(plan['label'])+'<small>'+esc(plan['description'])+'</small></a>'
        body += '</div><form method="get" action="/patient-lab" class="pl-form"><input type="hidden" name="id" value="'+stored['run_id']+'"><input type="hidden" name="plan" value="'+selected+'"><input type="hidden" name="view" value="'+view+'"><label for="scenario-week">查看决策时点后第 '+str(week)+' 周<input id="scenario-week" name="week" type="range" min="0" max="24" value="'+str(week)+'"></label><button type="submit" class="secondary">更新视图</button><span class="pl-muted">本次假设：'+('20%' if data['resistance_assumption'] == 'low' else '45%')+' 初始耐药</span></form><div class="pl-stage"><nav class="pl-tabs" aria-label="模拟结果视图">'
        for key, label in [('response', '病灶与细胞状态'), ('prognosis', '疗效与预后'), ('evidence', '证据与缺口')]:
            body += '<a href="'+url(view=key)+'"'+(' aria-current="page"' if view == key else '')+'>'+label+'</a>'
        body += '</nav>'
        if view == 'response':
            rows = {k: v['trajectory'] for k, v in data['forecasts'].items()}
            point = rows[selected][week]
            resistant = point['resistant_fraction'] * 100
            body += '<h2>病灶负荷的未来分支</h2><p class="pl-muted">同一当前状态；色带为参数情景跨度，非置信区间。实线 A／虚线 B／点线 A＋B。</p>'+chart(rows, selected, week)
            body += '<div class="pl-metrics" aria-live="polite"><div><span class="pl-muted">'+esc(PLANS[selected]['label'])+' · 第 '+str(week)+' 周负荷指数</span><strong class="pl-value">'+f'{point["burden_index"]:.1f}'+'</strong><span class="pl-muted">假设跨度 '+f'{point["scenario_low"]:.1f}–{point["scenario_high"]:.1f}'+'</span></div><div><span class="pl-muted">模拟细胞构成，非检测结果</span><div class="pl-cellbar"><span style="width:'+f'{100-resistant:.2f}'+'%;background:var(--pl-a)"></span><span style="width:'+f'{resistant:.2f}'+'%;background:var(--pl-ab)"></span></div><span class="pl-muted">敏感 '+f'{100-resistant:.0f}'+'% · 耐药 '+f'{resistant:.0f}'+'%</span></div></div>'
            body += '<h3>三方案同时间对照</h3><div class="pl-scroll"><table class="pl-table"><thead><tr><th>虚构方案</th><th>负荷指数</th><th>耐药构成</th></tr></thead><tbody>'
            for key in PLANS:
                row = rows[key][week]
                body += '<tr><td>'+esc(PLANS[key]['label'])+'</td><td>'+f'{row["burden_index"]:.1f}'+'</td><td>'+f'{row["resistant_fraction"]*100:.0f}'+'%</td></tr>'
            body += '</tbody></table></div><p class="pl-muted">假设模型只计算两个细胞群体的指数变化；不识别真实分子机制，不产生最优治疗推荐。</p>'
        elif view == 'prognosis':
            body += '<h2>预后情景展示位</h2><p class="pl-muted">下面是固定的数学示意曲线，与患者资料、病灶推演及随访无推导关系；非 PFS／OS 预测。</p>'+chart(data['survival_illustration']['curves'], selected, survival=True)+'<table class="pl-table"><tr><th>终点</th><th>当前状态</th></tr><tr><td>影像疗效 PR／PD</td><td>不可估计；负荷指数不是 RECIST 测量</td></tr><tr><td>PFS／OS</td><td>不可估计；没有适用的个体模型</td></tr><tr><td>毒性／生活质量</td><td>不可估计；需独立数据与模型</td></tr></table>'
        else:
            body += '<h2>来源、假设与适用范围</h2><dl class="pl-evidence"><dt>资料</dt><dd>内置合成病例 P001。每个版本只包含截止时点已可见的观测；未接收真实患者、原始影像或外部模型资料。</dd><dt>实际参与计算</dt><dd>当前负荷指数、选择的初始耐药比例和虚构药物的敏感群体抑制/耐药群体增长参数。年龄、癌种、部位与分子字段未参与计算，不可声称为多模态临床模型。</dd><dt>更新机制</dt><dd>加载固定的第6周82、第12周69的合成负荷观测；演练实际治疗记为A。以新观测重置当前负荷，保留耐药假设；不重新训练，也不证明预测准确。未接受方案的结局仍不可观测。</dd><dt>生存展示</dt><dd>独立数学曲线，只演示布局。病灶缩小不能直接换算为存活时间、PFS或OS。</dd><dt>审核</dt><dd>执行成功与科学审核分别记录；科学审核未开展，clinical_use=false。SHA-256验证记录内容一致性，不认证作者或临床有效性。</dd></dl>'
        body += '</div><section class="pl-run"><div class="pl-row"><h3>保存的运行与随访版本</h3><a download="patient-scenario.json" href="/patient-lab/export?id='+stored['run_id']+'">导出 JSON</a></div><p class="pl-muted">运行 '+esc(stored['run_id'])+'<br>生成时间 '+esc(stored['created_at'])+'<br>模型 '+esc(data['model_version'])+' · 科学审阅未开展</p><details><summary>输入与结果指纹</summary><p class="pl-muted">输入 <code>'+esc(data['input_sha256'])+'</code><br>结果 <code>'+esc(stored['sha256'])+'</code></p></details>'
        if data['parent_id']:
            body += '<p><a href="/patient-lab?id='+data['parent_id']+'">查看上一版冻结推演</a></p>'
            parent = app.patient_lab.get(data['parent_id'])['result']
            observed = patient['observations'][-1]
            past = parent['forecasts']['a']['trajectory'][6]['burden_index']
            body += '<p class="pl-muted">本次合成观测 '+str(observed['burden_index'])+'；上一版 A 的6周情景 '+f'{past:.1f}'+'。仅核对演练数据流，不计预测性能；B/联合方案没有对应观测。</p>'
        if patient['cutoff_week'] < 12:
            body += '<form method="post" action="/patient-lab/followup" class="pl-form">'+token+'<input type="hidden" name="parent_id" value="'+stored['run_id']+'"><button type="submit">加载第 '+str(patient['cutoff_week'] + 6)+' 周合成随访并创建新版本</button></form>'
        else:
            body += '<p class="pl-muted">两次预设随访均已加载，完整演练流程已走通。可回看旧版本或从基线创建新假设。</p>'
        body += '</section>'
    body += '</div></div><h3>最近 30 次已保存推演</h3><div class="pl-history">'
    for run in app.patient_lab.history():
        body += '<a href="/patient-lab?id='+run['run_id']+'">P001 · 截止'+str(run['result']['patient']['cutoff_week'])+'周 · '+run['run_id'][:8]+'</a>'
    body += '</div><p class="pl-footer">只运行内置合成情景，无真实患者导入、无真实药物剂量、无外部模型调用。新运行追加保存，旧预测保持不变。</p></section>'
    return body
