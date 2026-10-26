"""Script-free R&D program workspace, compatible with the shared strict CSP."""
import html
import itertools
from urllib.parse import quote
from .programs import Programs

LABELS={'title':'项目名称','target':'靶点或机制','population':'目标患者与未满足需求','modality':'拟研究的药物形式','hypothesis':'可证伪的研究假说','comparator':'比较对象','endpoint':'关键终点','differentiation':'目标差异化','material_rights':'材料可得性与权利状态'}
ACTIONS={'advance':'推进研究计划','revise':'修订假设','hold':'暂缓补证据','stop':'停止本轮方向'}
BD_ACTIONS={'collect_evidence':'继续研究尽调','defer':'暂缓资产判断','decline':'本轮不推进该机会'}
DIRECTIONS={'support':'支持与线索','counter':'反证或限制该推断','uncertain':'尚不能判断'}
ASSESSMENTS={'no_frozen_numeric_criterion':'原计划没有数值判据，仅记录结果','meets_recorded_threshold':'所报区间达到记录的成功门槛','reaches_recorded_stop':'所报区间触及记录的停止门槛','inconclusive_between_thresholds':'所报区间尚不足以判定'}
_ids=itertools.count()

def e(v): return html.escape(str(v),quote=True)
def field(key,label,kind='textarea',value='',required=True,maximum=4000):
    attr=' required' if required else ''
    fid=f'p-{key}-{next(_ids)}'
    if kind=='textarea': widget=f'<textarea id="{fid}" name="{key}" maxlength="{maximum}"{attr}>{e(value)}</textarea>'
    else: widget=f'<input id="{fid}" name="{key}" type="{kind}" value="{e(value)}"'+(' step="any"' if key=='cost' else '')+f'{attr}>'
    return f'<label for="{fid}">{e(label)}</label>'+widget

def form(app,p,operation,title):
    return f'<form class="card" method="post" action="/program/action"><h2>{title}</h2><input type="hidden" name="token" value="{app.token}"><input type="hidden" name="operation" value="{operation}"><input type="hidden" name="id" value="{e(p.get("id",""))}"><input type="hidden" name="revision" value="{e(p.get("revision",""))}">'

def render(app,q):
    store=Programs(app.state)
    pid=q.get('id','')
    body='<header class="page-header"><div><span class="eyebrow">R&amp;D PROGRAMS</span><h1>研发项目决策</h1><p class="lead">从目标产品出发，明确下一项验证、投入与停止条件。</p></div><a class="button secondary" href="/programs?new=1">新建项目</a></header>'
    if q.get('saved')=='1':body+='<p class="notice" role="status">记录已保存，历史版本已保留。</p>'
    if not pid:
        projects=store.list()
        if not projects:body+='<section class="card"><h2>先建立一个可证伪的药物项目</h2><p>明确患者、比较对象与关键终点，逐步补齐证据和下一项验证。没有证据时保留未知。</p></section>'
        for p in projects:
            action=ACTIONS[p['decisions'][-1]['action']] if p['decisions'] else '尚未记录研发决定'
            gaps=store.gaps(p)
            body+='<section class="card"><span class="eyebrow">'+e(p['stage'])+'</span><h2><a href="/programs?id='+quote(p['id'])+'">'+e(p['title'])+'</a></h2><p>'+e(p['hypothesis'])+'</p><div class="filter-chips"><span>'+str(len(p['evidence']))+' 条证据</span><span>'+str(len(p['experiments']))+' 项验证计划</span><span>'+e(action)+'</span></div><p class="data-note">'+e('；'.join(gaps) if gaps else '结构化准备项已记录，仍需独立科学审阅和材料权利核实')+'</p></section>'
        if not projects or q.get('new')=='1':
            body+=form(app,{},'create','定义目标产品与研究问题')
            for k,label in LABELS.items():body+=field(k,label,'text' if k in ('title','target','modality') else 'textarea')
            body+=field('author','记录人（本地声明）','text')+'<p><button>建立研究项目</button></p></form>'
        return body
    p=store.get(pid)
    body+='<nav class="asset-page-nav" aria-label="同一项目的研究工作"><a href="#ai-research">AI 研究草案</a><a href="#validation-results">研发验证与结果</a><a href="#asset-diligence">资产与 BD 尽调</a></nav>'
    body+='<a href="/programs">← 所有研发项目</a><h2>'+e(p['title'])+'</h2><div class="page-actions"><a class="button secondary" href="/program/export?id='+quote(pid)+'">导出完整决策包 JSON</a></div>'
    gaps=store.gaps(p)
    body+='<section class="card"><h2>下一步需要解决什么</h2><p>'+e('；'.join(gaps) if gaps else '准备项已记录，可提交研究计划决定；不代表科学审查通过')+'</p><p class="data-note">本地署名与“已审阅”均为记录人的声明。预算是估算，保存不会支付费用、预约实验或启动分析。</p></section>'
    body+='<section class="card"><h2>目标产品与假说</h2><dl class="asset-facts">'+''.join('<dt>'+e(label)+'</dt><dd>'+e(p[k])+'</dd>' for k,label in LABELS.items())+'</dl></section>'
    body+='<details class="card"><summary>修订目标产品与假说（保留旧版本）</summary>'+form(app,p,'profile','修订研究问题')
    for k,label in LABELS.items():body+=field(k,label,value=p[k])
    body+=field('revision_reason','修订依据（至少 10 字符）')+field('author','记录人','text')+'<p><button>保存修订版本</button></p></form></details>'
    body+='<section class="section-head"><h2>证据、反证与未知</h2><span>范围相符才能支持当前主张</span></section>'
    for r in p['evidence']:
        source=r['source']
        if source.startswith('artifact:'):href='/program/attachment?id='+quote(pid)+'&file='+quote(source.removeprefix('artifact:'))
        else:href=source
        body+='<section class="card"><span class="badge">'+e(DIRECTIONS[r['direction']])+'</span> <span class="badge">'+('待审阅' if r['review']=='pending' else '记录人声明已审阅')+'</span><h3>'+e(r['claim'])+'</h3><p><b>适用范围</b> '+e(r['scope'])+'</p><p><b>局限</b> '+e(r['limitations'])+'</p><a href="'+e(href)+'" target="_blank" rel="noopener">查看来源</a><p class="ref">'+e(r['source_date'])+' · 证据编号 '+e(r['event_id'])+'</p></section>'
    if p['evidence']:
        body+='<details class="card"><summary>追加证据审阅记录</summary>'+form(app,p,'review','审阅来源与适用范围')+'<label for="review-evidence">证据</label><select id="review-evidence" name="evidence_id">'+''.join('<option value="'+e(r['event_id'])+'">'+e(r['claim'])+'</option>' for r in p['evidence'])+'</select><label for="review-state">审阅状态（本地声明）</label><select id="review-state" name="review"><option value="pending">仍待核实</option><option value="reviewed">本人已审阅</option></select>'+field('reason','审阅依据与范围（至少 10 字符）')+field('author','审阅记录人','text')+'<p><button>追加审阅记录</button></p></form></details>'
    if not p['evidence']:body+='<p class="data-note">尚无来源。未知不等于没有反证。</p>'
    body+='<details class="card"><summary>添加证据或反证</summary>'+form(app,p,'evidence','记录来源及适用范围')
    body+='<label for="e-direction">证据方向</label><select id="e-direction" name="direction">'+''.join('<option value="'+k+'">'+v+'</option>' for k,v in DIRECTIONS.items())+'</select>'
    for k,l in [('claim','这条证据具体支持或限制什么'),('scope','适用人群、干预、比较与终点'),('limitations','局限与未解决问题'),('source','来源 URL 或已导入附件引用')]:body+=field(k,l)
    body+=field('source_date','来源日期','date')+'<label for="e-review">审阅记录</label><select id="e-review" name="review"><option value="pending">待审阅</option><option value="reviewed">本人已审阅（本地声明）</option></select>'+field('author','记录人','text')+'<p><button>追加证据</button></p></form></details>'
    body+='<section class="section-head"><h2>下一项验证与预算</h2><span>先写成功与停止条件</span></section>'
    for r in p['experiments']:
        body+='<section class="card"><h3>'+e(r['question'])+'</h3><p>'+e(r['design'])+'</p><dl class="asset-facts"><dt>成功标准</dt><dd>'+e(r['success'])+'</dd><dt>停止条件</dt><dd>'+e(r['stop'])+'</dd><dt>预计投入</dt><dd>'+e(r['currency'])+' '+format(r['cost'],',.2f')+' · '+str(r['days'])+' 天</dd><dt>拟议负责人</dt><dd>'+e(r['owner'])+'</dd></dl><p class="data-note">'+('假说已修订，此计划需重新核对范围；请追加更新计划。' if r['status']!='proposed' else '拟议计划 · 估算预算，尚未承诺或执行')+'</p></section>'
        if r.get('criterion'):
            c=r['criterion'];success_sign='≥' if c['direction']=='higher' else '≤';stop_sign='≤' if c['direction']=='higher' else '≥'
            body+='<p class="data-note">冻结判据：'+e(c['metric'])+'（'+e(c['unit'])+'）；成功门槛 '+success_sign+' '+e(c['success_threshold'])+'；停止门槛 '+stop_sign+' '+e(c['stop_threshold'])+'。比对所报 95% 区间。</p>'
    if not p['experiments']:body+='<p class="data-note">尚无验证计划。需要负责人、实验设计与可检查的停止条件。</p>'
    body+='<details class="card"><summary>制定下一项验证</summary>'+form(app,p,'experiment','提交验证计划')
    for k,l in [('question','需要消除的关键不确定性'),('design','设计、独立单位、对照与分析方法'),('success','达到什么结果才能推进'),('stop','出现什么结果应停止或修订'),('owner','拟议负责人')]:body+=field(k,l)
    body+='<label for="currency">预算币种</label><select name="currency" id="currency"><option>CNY</option><option>USD</option></select>'+field('cost','估算预算（大于零）','number')+field('days','预计周期（天）','number')
    body+='<fieldset><legend>可选：冻结一个数值判据</legend><p class="data-note">区间完全越过成功或停止门槛才作对应标记，其余保留不确定。留空则只记录文字计划，不能事后给旧计划补门槛。</p>'+field('metric','精确指标名称',required=False)+field('unit','单位或效应量尺度',required=False)+'<label for="numeric-direction">期望方向</label><select id="numeric-direction" name="direction"><option value="higher">越高越好</option><option value="lower">越低越好</option></select>'+field('success_threshold','成功数值门槛','text',required=False)+field('stop_threshold','停止数值门槛','text',required=False)+'</fieldset>'
    body+=evidence_choices(p,'experiment')+field('author','记录人','text')+'<p><button>保存拟议验证计划</button></p></form></details>'
    body+=result_section(app,p)
    body+='<section class="section-head"><h2>研发决定与修订记录</h2><span>每次决定绑定当时的证据版本</span></section>'
    for r in reversed(p['decisions']):body+='<section class="card"><h3>'+e(ACTIONS[r['action']])+'</h3><p>'+e(r['reason'])+'</p><p><b>剩余风险</b> '+e(r['remaining_risk'])+'</p><p class="ref">'+e(r['created'])+' · '+e(r['author'])+'</p><details><summary>查看当时依据</summary><p class="ref">证据 '+e(', '.join(r['evidence_ids']))+'<br>结果 '+e(', '.join(r.get('result_ids',[])) or '当时未引用验证结果')+'<br>版本 '+e(r['basis_revision'])+'</p></details></section>'
    if not p['decisions']:body+='<p class="data-note">尚未记录决定。导入计算结果不会代替研究者作出决定。</p>'
    body+=form(app,p,'decision','记录研发决定')+'<label for="p-action">本轮动作</label><select name="action" id="p-action">'+''.join('<option value="'+k+'">'+v+'</option>' for k,v in [('hold',ACTIONS['hold']),('revise',ACTIONS['revise']),('stop',ACTIONS['stop']),('advance',ACTIONS['advance'])])+'</select>'+field('reason','决定依据（至少 10 字符）')+field('remaining_risk','仍未解决的风险')+evidence_choices(p,'decision')
    if any(r['current_scope'] for r in p['results']):body+='<fieldset><legend>引用验证结果（推进需引用全部当前结果）</legend>'+''.join('<label><input type="checkbox" name="result_ids" value="'+e(r['event_id'])+'"> '+e(r['metric'])+' · '+e(ASSESSMENTS[r['assessment']])+'</label>' for r in p['results'] if r['current_scope'])+'</fieldset>'
    body+=field('author','记录人（本地声明）','text')+'<p><button>保存本轮决定</button></p></form>'
    body+=diligence_section(app,p)
    from .ai_research_view import render as ai_section
    body+=ai_section(app,store,p)
    body+='<p class="ref">历史链已在读取时校验 · 当前版本 '+e(p['revision'])+'</p>'
    return body

def evidence_choices(p,prefix):
    return '<fieldset><legend>本次引用的项目证据（至少一条）</legend>'+''.join('<label><input type="checkbox" name="evidence_ids" value="'+e(r['event_id'])+'"> '+e(DIRECTIONS[r['direction']])+' · '+e(r['claim'])+'</label>' for r in p['evidence'])+'</fieldset>'

def result_section(app,p):
    body='<section class="section-head" id="validation-results"><h2>验证结果与原计划比对</h2><span>结果记录、门槛比对和科学审阅分别保存</span></section>'
    for r in p['results']:
        body+='<section class="card"><span class="badge">'+e(ASSESSMENTS[r['assessment']])+'</span> <span class="badge">'+('待审阅' if r['review']=='pending' else '记录人声明已审阅')+'</span><h3>'+e(r['metric'])+'</h3><p>'+e(r['estimate'])+'（所报 95% 区间 '+e(r['ci_low'])+'–'+e(r['ci_high'])+'） '+e(r['unit'])+'</p><p>独立单位 '+e(r['n'])+' · '+e(r['independent_unit'])+' · '+e(r['population'])+'</p><p><b>方法</b> '+e(r['method'])+'</p><p><b>局限</b> '+e(r['limitations'])+'</p><p class="data-note">'+('来源早于计划，属于回顾性结果。' if r['timing']=='historical_source' else '来源记录时间晚于计划，不等于已证明独立前瞻验证。')+(' 假说已变化，此结果不用于当前范围。' if not r['current_scope'] else '')+'</p><details><summary>来源与原计划版本</summary><p class="ref">'+e(r['source'])+'<br>可用时间 '+e(r['available_at'])+'<br>原计划 '+e(r['experiment_id'])+'<br>原计划版本 '+e(r['plan_revision'])+'<br>结果编号 '+e(r['event_id'])+'</p></details></section>'
        href=('/program/attachment?id='+quote(p['id'])+'&file='+quote(r['source'].removeprefix('artifact:'))) if r['source'].startswith('artifact:') else r['source']
        body+='<p><a href="'+e(href)+'" target="_blank" rel="noopener">查看验证结果来源</a></p>'
    if not p['results']:body+='<p class="data-note">尚无结果。保存计划不代表验证已经开始或完成。</p>'
    plans=[x for x in p['experiments'] if x['status']=='proposed']
    if plans:
        body+='<details class="card"><summary>回写验证结果</summary>'+form(app,p,'result','关联原计划并记录区间')+'<label for="result-plan">原验证计划</label><select id="result-plan" name="experiment_id">'+''.join('<option value="'+e(x['event_id'])+'">'+e(x['question'])+'</option>' for x in plans)+'</select><p class="data-note">填写来源报告的 95% 区间，系统只作门槛比对，不重新计算或认证区间。指标名和单位须与数值计划完全一致。</p>'
        for k,l in [('metric','结果指标名称'),('unit','结果单位或效应量尺度'),('estimate','效应估计'),('ci_low','95% 区间下限'),('ci_high','95% 区间上限'),('n','独立单位数量'),('independent_unit','独立单位类型（患者、供者等）'),('population','实际分析人群与干预对照'),('method','分析方法与区间计算方法'),('limitations','偏差、缺失与适用限制'),('source','来源 URL 或已导入附件引用'),('available_at','来源实际可用时间（含时区，例如 2026-10-25T10:00:00+08:00）')]:body+=field(k,l)
        body+=field('author','结果记录人','text')+'<p><button>追加结果并比对原计划</button></p></form></details>'
    if p['results']:
        body+='<details class="card"><summary>追加结果审阅记录</summary>'+form(app,p,'result_review','核对方法与来源')+'<label for="result-review-id">验证结果</label><select id="result-review-id" name="result_id">'+''.join('<option value="'+e(r['event_id'])+'">'+e(r['metric'])+' · '+e(r['created'])+'</option>' for r in p['results'])+'</select><label for="result-review-state">审阅状态（本地声明）</label><select id="result-review-state" name="review"><option value="pending">仍需核实</option><option value="reviewed">本人已审阅</option></select>'+field('reason','审阅方法、来源和范围的依据（至少 10 字符）')+field('author','审阅记录人','text')+'<p><button>追加结果审阅</button></p></form></details>'
    return body

def diligence_section(app,p):
    body='<section class="section-head" id="asset-diligence"><h2>资产与 BD 尽调</h2><span>使用同一研究证据，单独记录资产判断</span></section><p class="data-note">研究假说、外部参考药物和可交易资产分别记录。登记申办方不等于资产所有权或可获得许可；研发决定也不等于投资判断。</p>'
    for a in p['assets']:
        body+='<section class="card"><span class="badge">外部参考资产 · 权利未核实</span><h3>'+e(a['asset_name'])+'</h3><p>'+e(a['purpose'])+'</p><dl class="asset-facts"><dt>登记试验</dt><dd>'+e(a['nct'])+'</dd><dt>登记申办方</dt><dd>'+e(a['registered_sponsor'])+'</dd><dt>来源试验阶段/状态</dt><dd>'+e(', '.join(a['phases']))+' / '+e(a['trial_status'])+'</dd><dt>登记终止原因</dt><dd>'+e(a['why_stopped'])+'</dd><dt>版本日期</dt><dd>登记更新 '+e(a['last_update'])+'；本地快照 '+e(a['snapshot_date'])+'</dd></dl><a href="/program/attachment?id='+quote(p['id'])+'&file=NCT06137183_current.json" target="_blank" rel="noopener">查看本地登记源记录</a><p class="data-note">以上为有日期的来源快照，未据此认定当前完整管线、交易可得性或投资回报。</p></section>'
    if not p['assets']:return body+'<p class="data-note">尚未关联有来源的外部参考资产。先补齐实体与权利范围。</p>'
    for r in reversed(p['diligence']):
        body+='<section class="card"><h3>'+e(BD_ACTIONS[r['action']])+'</h3><p>'+e(r['thesis'])+'</p><p><b>未解决的资产与交易问题</b> '+e(r['unresolved'])+'</p><p><b>下一项资料需求</b> '+e(r['next_request'])+'</p><p class="ref">'+e(r['author'])+' · '+e(r['created'])+'<br>依据版本 '+e(r['basis_revision'])+'</p></section>'
    body+=form(app,p,'diligence','记录资产尽调下一步')+'<label for="bd-asset">参考资产</label><select id="bd-asset" name="asset_id">'+''.join('<option value="'+e(a['event_id'])+'">'+e(a['asset_name'])+' · '+e(a['nct'])+'</option>' for a in p['assets'])+'</select><label for="bd-action">尽调动作</label><select id="bd-action" name="action">'+''.join('<option value="'+k+'">'+v+'</option>' for k,v in BD_ACTIONS.items())+'</select>'
    for k,l in [('thesis','研究证据对资产机会意味着什么（至少 10 字符）'),('unresolved','权利、差异化、交易和商业风险中尚缺什么'),('next_request','下一步需要哪些具体材料或验证')]:body+=field(k,l)
    body+=evidence_choices(p,'bd')
    if any(r['current_scope'] for r in p['results']):body+='<fieldset><legend>引用当前验证结果</legend>'+''.join('<label><input type="checkbox" name="result_ids" value="'+e(r['event_id'])+'"> '+e(r['metric'])+' · '+e(ASSESSMENTS[r['assessment']])+'</label>' for r in p['results'] if r['current_scope'])+'</fieldset>'
    return body+field('author','尽调记录人（本地声明）','text')+'<p><button>保存尽调记录</button></p></form>'

def submit(app,q):
    store=Programs(app.state);operation=q.get('operation');pid=q.get('id','');author=q.get('author','');revision=q.get('revision','')
    if operation=='create':pid=store.create(q,author)
    elif operation=='evidence':store.add_evidence(pid,q,author,revision)
    elif operation=='profile':store.revise(pid,q,author,revision)
    elif operation=='review':store.review_evidence(pid,q,author,revision)
    elif operation=='experiment':store.add_experiment(pid,q,author,revision)
    elif operation=='result':store.add_result(pid,q,author,revision)
    elif operation=='result_review':store.review_result(pid,q,author,revision)
    elif operation=='diligence':store.add_diligence(pid,q,author,revision)
    elif operation=='ai_import':
        import json
        from .ai_research import ingest
        ingest(store,pid,json.loads(q.get('proposal_json','')),author,revision,{'kind':q.get('origin_kind'),'model_label':q.get('model_label')})
    elif operation=='ai_run':
        from .ai_research import run_tool
        run_tool(store,pid,q.get('proposal_id'),q.get('task_id'),author,revision)
    elif operation=='decision':store.decide(pid,q,author,revision)
    else:raise ValueError('未知研发操作')
    return '/programs?id='+quote(pid)+'&saved=1'
