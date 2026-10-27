"""Research landing page composed from observed records, never demo metrics."""
import html,json
from .market import Market
from .product_views import cards
from .market_view import _timestamp
from .history_view import is_synthetic


def e(value):
    return html.escape(str(value),quote=True)


def render(app,records,record_table):
    market=Market(app.workspace,app.state)
    with market.db() as db:
        companies=db.execute('SELECT count(*) FROM companies').fetchone()[0]
        trials=db.execute('SELECT count(DISTINCT nct) FROM trials').fetchone()[0]
        has_proposals=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sponsor_proposals'").fetchone()
        pending=db.execute("SELECT count(*) FROM sponsor_proposals WHERE status='pending'").fetchone()[0] if has_proposals else 0
        last=db.execute("SELECT max(finished) FROM runs WHERE source='ClinicalTrials.gov' AND status='succeeded'").fetchone()[0]
        has_jobs=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='collection_jobs'").fetchone()
        jobs={r['status']:r['n'] for r in db.execute('SELECT status,count(*) n FROM collection_jobs GROUP BY status')} if has_jobs else {}
    body='<header class="page-header"><div><span class="eyebrow">RESEARCH OVERVIEW</span><h1>临床资产研究控制台</h1><p class="lead">从市场线索出发，核对来源，留下可复查的研究判断。</p></div><div class="page-actions"><a class="button" href="/assets">检索临床资产</a><a class="button secondary" href="/notes">记录研究问题</a><a class="button secondary" href="/patient-lab">患者治疗情景</a></div></header>'
    body+='<div class="grid" aria-label="当前工作区指标">'
    synthetic_count=sum(is_synthetic(r) for r in records)
    history_detail='含 '+str(synthetic_count)+' 条明确标记的合成工程测试' if synthetic_count else '包含成功、失败和受阻记录'
    metrics=[('已关联证券',companies,'证券到申办方的已配置关联','/coverage'),('唯一登记试验',trials,'按 NCT 去重，含历史观察','/market'),('待核对关联建议',pending,'需要核对主体与来源','/mapping'),('研究历史记录',len(records),history_detail,'/records')]
    for label,value,detail,url in metrics:
        body+='<a class="card metric-card" href="'+url+'"><span class="metric-label">'+label+'</span><strong class="metric">'+format(value,',')+'</strong><span class="metric-detail">'+detail+'</span></a>'
    body+='</div><div class="data-note"><span class="status-dot"></span>市场与登记 · 当前本机采集：'+('自动检查已开启' if market.setting('enabled')=='1' else '自动检查未开启')+' · 排队 '+str(jobs.get('queued',0))+' · 执行中 '+str(jobs.get('running',0))+' · 待重试 '+str(jobs.get('retry_wait',0))+' · <a href="/coverage">查看任务与缺口 →</a><p class="ref">最近一次成功登记查询：'+(_timestamp(last) if last else '尚无成功查询记录')+'。查询完成不代表公司完整管线已核验。</p></div>'
    body+='<div class="section-head"><h2>开始本次研究</h2><span>市场线索 → 来源核对 → 研究记录</span></div><div class="case-grid workflow-links">'
    flows=[('01','选择公司与登记用药','搜索公司，筛选用药名称、适应症与阶段，逐项展开试验依据。','/assets','进入资产工作台'),('02','核对官方披露','对照公司披露与登记名称，查看日期、缺失信息和反证线索。','/disclosures','查看披露与缺口'),('03','复查过往研究','打开分析、研究循环与冻结快照，核对依据和历史判断。','/records','浏览研究历史')]
    for step,title,description,url,action in flows:
        body+='<a class="case-card" href="'+url+'"><span class="eyebrow">'+step+'</span><h3>'+title+'</h3><p>'+description+'</p><span class="text-link">'+action+' →</span></a>'
    body+='</div><div class="section-head"><h2>工作区研究记录</h2><a href="/records">查看全部历史 →</a></div><p class="data-note">按保存编号展示部分记录；未提供可靠时间的记录保留未知。</p>'
    if (app.workspace/'cache-info.json').exists():
        try:scope=json.loads((app.workspace/'cache-info.json').read_text()).get('scope','备份范围未记录')
        except (OSError,ValueError,AttributeError):scope='备份说明无法读取，范围尚未核实。'
        body+='<div class="notice"><strong>历史分析与研究快照 · 备份记录</strong><p>以下历史研究的备份范围与上方市场登记采集分别记录。</p><details><summary>查看备份时间与范围</summary><p>'+e(scope)+'</p></details></div>'
    body+='<section class="card">'+record_table(records[:5])+'</section>'
    body+='<div class="notice">临床预测和投资收益仍待独立验证。执行成功、来源核对与科学审查分别记录，不能互相替代。<a href="/mission">查看目标与验收条件 →</a></div><details class="card secondary-panel"><summary>回顾性研究演示与待验证草案</summary><p class="muted">下列资料用于体验研究流程；回顾案例的结局已知，草案不代表已验证管线，不计入上方实际研究记录。</p><div class="case-grid">'+cards()+'</div><p><a href="/guide">六分钟演示路线 →</a></p></details>'
    return body
