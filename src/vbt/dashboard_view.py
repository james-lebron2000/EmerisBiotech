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
    body='<header class="page-header"><div><span class="eyebrow">RESEARCH OVERVIEW</span><h1>Clinical Asset Research Workbench</h1><p class="lead">Start with market leads, check sources and preserve reviewable research decisions.</p></div><div class="page-actions"><a class="button" href="/assets">Search clinical assets</a><a class="button secondary" href="/notes">Record a research question</a><a class="button secondary" href="/patient-lab">Patient scenarios</a></div></header>'
    body+='<div class="grid" aria-label="Current workspace metrics">'
    synthetic_count=sum(is_synthetic(r) for r in records)
    history_detail='Includes '+str(synthetic_count)+' explicitly marked synthetic engineering tests' if synthetic_count else 'Includes successful, failed and blocked records'
    metrics=[('Mapped securities',companies,'Configured security-to-sponsor mappings','/coverage'),('Unique registered trials',trials,'Distinct NCT IDs, including historical observations','/market'),('Pending mapping suggestions',pending,'Identity and source checks required','/mapping'),('Research history',len(records),history_detail,'/records')]
    for label,value,detail,url in metrics:
        body+='<a class="card metric-card" href="'+url+'"><span class="metric-label">'+label+'</span><strong class="metric">'+format(value,',')+'</strong><span class="metric-detail">'+detail+'</span></a>'
    body+='</div><div class="data-note"><span class="status-dot"></span>Market and registry · Local collection: '+('Automatic checks enabled' if market.setting('enabled')=='1' else 'Automatic checks disabled')+' · Queued '+str(jobs.get('queued',0))+' · Running '+str(jobs.get('running',0))+' · Retry pending '+str(jobs.get('retry_wait',0))+' · <a href="/coverage">View tasks and gaps →</a><p class="ref">Most recent successful registry query: '+(_timestamp(last) if last else 'No successful registry query recorded')+'。A completed query does not verify the complete company pipeline.</p></div>'
    body+='<div class="section-head"><h2>Start a research workflow</h2><span>Market leads → Source review → Research records</span></div><div class="case-grid workflow-links">'
    flows=[('01','Select a company and registered intervention','Search companies, filter interventions, indications and phases, then inspect each trial source.','/assets','Open asset workbench'),('02','Review official disclosures','Compare company disclosures with registry names; inspect dates, missing information and counterevidence.','/disclosures','View disclosures and gaps'),('03','Review previous research','Open analyses, research cycles and frozen snapshots to inspect evidence and earlier decisions.','/records','Browse research history')]
    for step,title,description,url,action in flows:
        body+='<a class="case-card" href="'+url+'"><span class="eyebrow">'+step+'</span><h3>'+title+'</h3><p>'+description+'</p><span class="text-link">'+action+' →</span></a>'
    body+='</div><div class="section-head"><h2>Workspace research records</h2><a href="/records">View all history →</a></div><p class="data-note">A subset is shown by saved ID; timestamps without a reliable saved field remain unknown.</p>'
    if (app.workspace/'cache-info.json').exists():
        try:scope=json.loads((app.workspace/'cache-info.json').read_text()).get('scope','Backup scope not recorded')
        except (OSError,ValueError,AttributeError):scope='Backup metadata cannot be read; scope remains unverified.'
        body+='<div class="notice"><strong>Historical analyses and snapshots · Backup record</strong><p>The backup scope of historical research is recorded separately from market/registry collection above.</p><details><summary>View backup time and scope</summary><p>'+e(scope)+'</p></details></div>'
    body+='<section class="card">'+record_table(records[:5])+'</section>'
    body+='<div class="notice">Clinical forecasting and investment returns require independent validation. Execution, source checks and scientific review are separate states.<a href="/mission">View goals and acceptance criteria →</a></div><details class="card secondary-panel"><summary>Retrospective demonstrations and unvalidated drafts</summary><p class="muted">These materials demonstrate the workflow. Retrospective outcomes are known; drafts are not validated pipelines and are excluded from the actual record counts above.</p><div class="case-grid">'+cards()+'</div><p><a href="/guide">Six-minute walkthrough →</a></p></details>'
    return body
