"""Human-facing research pages. Bundled cases are retrospective demonstrations."""
import html

def e(value):return html.escape(str(value if value is not None else '未记录'),quote=True)
CASES={
 'osmr-uc':{'title':'OSMR × 溃疡性结肠炎','tag':'回顾性反证案例','question':'表达关联，足以支持一个临床成功判断吗？','summary':'观察性关联提供线索；特定干预的临床反证要求收窄结论。','decision':'停止把表达关联单独作为临床成功或投资信号。保留探索性标志物研究。','support':'既有 GSE92415 分析包含 59 名患者。OSM 中位数差为 0.314，95% CI 0.011–0.644；OSMR 为 0.287，95% CI −0.103–0.641（无响应减响应）。','opposition':'Roche 的 vixarelimab UC 二期摘要报告缓解人数为 3/27、1/26，安慰剂为 3/26；报告称未观察到获益并提前结束。','limits':['观察性队列的治疗是 golimumab，不能直接外推 OSMR 阻断疗效。','GEO 第六周响应定义尚待协议级核对；未完成因果调整。','此处结局已知，不能计入前瞻预测成绩或投资业绩。','结果来自既有分析的摘要；没有在本页重新执行分析。原运行编号：2026-09-25T085153-545299+0000-515a3584；原始文件不随公开仓库发布。'], 'sources':[('GSE92415 原始队列','https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE92415','观察性来源'),('Roche GA44839 结果摘要','https://forpatients.roche.com/content/dam/patient-platform/lps/global/ga44839/LPS_GA44839_final-results_November2025_English.pdf','2025-11-05，页 1、5')], 'next':'先核对队列终点和混杂因素，再做独立队列验证。'},
 'bcma-autoimmune':{'title':'BCMA × 自身免疫','tag':'未验证研究草案','question':'致病抗体是否依赖 BCMA 阳性细胞？','summary':'把细胞依赖、功能性抗体和材料可得性拆成可检验问题。','decision':'证据不足，暂不形成成功概率或投资判断。','support':'已有研究问题与拟议实验比较；本演示未提供经独立审查的科学支持来源。','opposition':'尚未完成反证检索。未知不能视为没有反证。','limits':['没有已落实的样本材料、预算或负责人。','没有候选分子、临床结果或经校准概率。'], 'sources':[], 'next':'明确患者范围、材料来源，检索支持及相反证据。'},
 'napi2b-adc':{'title':'NaPi2b × 卵巢癌 ADC','tag':'未验证研究草案','question':'表达、内化和载荷敏感性如何影响治疗窗？','summary':'把靶点表达与药物形式、安全窗分别评价。','decision':'证据不足，先确认差异化假设与可用模型。','support':'已有问题定义及对照思路；本演示未提供经独立审查的科学支持来源。','opposition':'正常组织表达、载荷非特异毒性和耐药是需要核查的问题，尚无本项目实验结果。','limits':['不是自有药物资产；未落实药物、模型或权利。','未完成反证检索，不输出成功概率。'], 'sources':[], 'next':'核对肿瘤与正常模型、内化指标、载荷对照和材料授权。'}
}
ACTIONS={'follow_up':'继续补证据','revise':'修订研究假设','stop':'停止该推断'}

def cards():
 return ''.join('<a class="case-card" href="/case?id='+key+'"><span class="eyebrow">'+e(v['tag'])+'</span><h3>'+e(v['title'])+'</h3><p>'+e(v['question'])+'</p><span class="text-link">进入研究 →</span></a>' for key,v in CASES.items())

def home_intro():
 return '<section class="hero"><div class="eyebrow">EMERIS BIOTECH · RESEARCH WORKSPACE</div><h1>让每一个临床判断<br>都有可复核的依据。</h1><p>查看证据、面对反证，留下每一次判断与修订。</p><a class="button" href="/case?id=osmr-uc">体验一轮研究</a> <a class="secondary" href="/guide">查看演示路线</a><div class="hero-note">研究原型 · 尚未验证临床预测或投资收益</div></section><section class="section-head"><h2>从一个具体问题开始</h2><span>1 个回顾案例 · 2 个待验证草案</span></section><div class="case-grid">'+cards()+'</div>'

def case_page(app,key):
 if key not in CASES:raise ValueError('研究案例不存在')
 c=CASES[key];events=[r for r in app.rows('decisions') if r['case_id']==key]
 body='<a href="/">← 返回工作台</a><div class="section-head"><div><span class="eyebrow">'+e(c['tag'])+'</span><h1>'+e(c['title'])+'</h1></div><span class="badge">演示资料 · 截至 2026-09-25</span></div><p class="lead">'+e(c['question'])+'</p><div class="notice">本页使用固定的回顾案例／草案，不是实时预测。这里保存的判断单独标为演示记录，不计入正式研究审批。</div><div class="card conclusion"><span class="eyebrow">当前研究结论</span><h2>'+e(c['decision'])+'</h2><p>'+e(c['summary'])+'</p></div><div class="split"><div class="card"><span class="eyebrow">支持与线索</span><h2>已知的证据</h2><p>'+e(c['support'])+'</p></div><div class="card counter"><span class="eyebrow">反证与未知</span><h2>需要面对的限制</h2><p>'+e(c['opposition'])+'</p></div></div><div class="card"><h2>来源与边界</h2>'
 for title,url,loc in c['sources']:body+='<p><a target="_blank" rel="noopener noreferrer" href="'+e(url)+'">'+e(title)+' ↗</a><br><span class="muted">'+e(loc)+'</span></p>'
 if not c['sources']:body+='<p class="muted">尚无可供独立复核的来源；需要补充证据。</p>'
 body+='<ul>'+''.join('<li>'+e(x)+'</li>' for x in c['limits'])+'</ul><p><b>下一步：</b>'+e(c['next'])+'</p></div><form class="card" method="post" action="/decision"><h2>记录你的判断</h2><p class="muted">演示记录只追加，不覆盖此前判断。</p><input type="hidden" name="token" value="'+app.token+'"><input type="hidden" name="case_id" value="'+e(key)+'"><label for="decision-author">记录人</label><input id="decision-author" name="author" required maxlength="100" placeholder="填写姓名或演示昵称"><label for="decision-action">研究动作</label><select id="decision-action" name="action">'+''.join('<option value="'+k+'">'+v+'</option>' for k,v in ACTIONS.items())+'</select><label for="decision-reason">判断依据</label><textarea id="decision-reason" name="reason" required minlength="10" maxlength="5000" placeholder="哪条证据改变了你的判断？还需要验证什么？"></textarea><button>保存演示判断</button></form><h2>判断时间线</h2>'
 for x in events:body+='<div class="card"><span class="badge">演示记录</span> <b>'+e(ACTIONS[x['action']])+'</b><p class="body">'+e(x['reason'])+'</p><p class="ref">'+e(x['author'])+' · '+e(x['created'])+'</p></div>'
 if not events:body+='<div class="empty">还没有判断记录。先查看证据，再留下你的第一条判断。</div>'
 return body

def guide():
 return '<span class="eyebrow">PRODUCT WALKTHROUGH</span><h1>六分钟，走完一轮研究。</h1><p class="lead">适合首次使用和现场产品演示。无需模型密钥。</p><div class="card"><h2>01 · 明确问题</h2><p>从 OSMR 回顾案例开始，先说明这是已知结局的流程演示。</p><a href="/case?id=osmr-uc">打开案例 →</a></div><div class="card"><h2>02 · 对照证据与反证</h2><p>同时查看观察性关联、临床反证、来源定位与尚未解决的限制。</p></div><div class="card"><h2>03 · 记录判断</h2><p>填写演示昵称与至少十个字符的依据，选择继续、修订或停止；保存后查看时间线。</p></div><div class="card"><h2>04 · 查看历史和任务</h2><p>有历史快照时，可进入详情并发起完整性校验；新安装的空环境会明确提示没有记录。</p><a href="/records">历史记录 →</a> · <a href="/jobs">任务观测 →</a></div><div class="card"><h2>05 · 说明下一阶段</h2><p>展示临床预测、投资验证与云端交付的验收条件。不要把工程测试或演示案例当成预测业绩。</p><a href="/mission">目标与里程碑 →</a></div>'

def record_summary(record,read):
 data=record['data'];path=record['path'];kind=record['kind'];body='<div class="card"><h2>研究摘要</h2>'
 if kind=='run':
  errors=data.get('errors',[])
  body+='<p>'+('本次分析有需要处理的问题。' if errors else '查看下方结果与科学审查；执行完成不代表结论通过。')+'</p>'
  for error in errors:body+='<p class="notice">'+e(error)+'</p>'
  results=read(path/'work/results.json')
  if isinstance(results,list) and results:
   body+='<div class="scroll"><table><tr><th>基因</th><th>队列</th><th>无响应 / 响应</th><th>中位数差</th><th>95% CI</th></tr>'
   for r in results:
    def fmt(k):
     value=r.get(k);return f'{value:.3f}' if isinstance(value,(int,float)) else '未估计'
    body+='<tr><td>'+e(r.get('gene'))+'</td><td>'+e(r.get('cohort'))+'</td><td>'+e(r.get('n_nonresponder'))+' / '+e(r.get('n_responder'))+'</td><td>'+fmt('median_difference')+'</td><td>'+fmt('ci_low')+' ～ '+fmt('ci_high')+'</td></tr>'
   body+='</table></div>'
 elif kind=='cycle':
  d=data.get('decision',{});body+='<h3>'+e(d.get('scope','查看完整决策记录'))+'</h3><p>'+e(d.get('retained_work',''))+'</p><p class="muted">回顾性研究；不计为前瞻预测业绩。</p>'
 else:
  plan=read(path/'plan.json')
  for h in plan.get('hypotheses',[]):body+='<h3>'+e(h.get('title'))+'</h3><p>'+e(h.get('question'))+'</p>'
 return body+'</div>'
