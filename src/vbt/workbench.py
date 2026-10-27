"""Local single-user research workbench. Historical artifacts remain immutable."""
import html,json,secrets,sqlite3,threading,subprocess,sys,os,hashlib,uuid,re
from pathlib import Path
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from urllib.parse import urlsplit,parse_qs,quote
from .io import ensure_storage,now,write,within
from .mission import MISSION,MILESTONES,STATES,validate_update
from .product_views import CASES,ACTIONS,home_intro,case_page,guide,record_summary,record_gates
from .sqlite_connections import ClosingConnection
from .patient_lab import PatientLab

def esc(x):return html.escape(str(x if x is not None else '未记录'),quote=True)
def read(path):
    try:return json.loads(path.read_text())
    except (OSError,ValueError):return {'record_error':'无法读取记录'}

class Workbench:
    def __init__(self,workspace,state=None):
        self.workspace=ensure_storage(workspace)
        tag=hashlib.sha256(str(self.workspace).encode()).hexdigest()[:12]
        self.state=Path(state or Path.home()/'Library/Application Support/VirtualBiotech'/tag).resolve()
        if self.state.is_relative_to('/Volumes'):raise ValueError('State must be local')
        self.state.mkdir(parents=True,exist_ok=True);self.dbpath=self.state/'workbench.sqlite3';self.token=secrets.token_urlsafe(32);self.lock=threading.Lock();self.patient_lab=PatientLab(self.state)
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS decisions (id TEXT PRIMARY KEY, created TEXT, case_id TEXT, action TEXT, author TEXT, reason TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS entries (id TEXT PRIMARY KEY, created TEXT, author TEXT, kind TEXT, body TEXT, reference TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS milestones (id TEXT PRIMARY KEY, created TEXT, milestone TEXT, status TEXT, author TEXT, note TEXT, reference TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, created TEXT, kind TEXT, reference TEXT, status TEXT, output TEXT)')
            db.execute("UPDATE jobs SET status='interrupted', output=output || '\n服务重启，任务中断；请重新发起校验。' WHERE status='running'")
    def db(self):
        db=sqlite3.connect(self.dbpath,timeout=30,factory=ClosingConnection);db.row_factory=sqlite3.Row;return db
    def records(self):
        result=[]
        for kind,folder,name in [('run','runs','status.json'),('portfolio','portfolio/snapshots','evaluation.json'),('cycle','cycles','cycle.json')]:
            for path in (self.workspace/'artifacts'/folder).glob('*'):
                if not path.is_dir():continue
                data=read(path/name)
                malformed=not isinstance(data,dict) or 'record_error' in data
                if not isinstance(data,dict):data={'record_error':'记录格式待核实：预期 JSON 对象','saved_value':data}
                status=data.get('execution') if kind=='run' else ('completed' if data.get('complete_research_cycle') is True else 'recorded') if kind=='cycle' else 'snapshot'
                plan=read(path/'inputs/plan.json') if kind=='run' else read(path/'plan.json') if kind=='portfolio' else data
                if not isinstance(plan,dict):
                    malformed=True;plan={}
                title=plan.get('question' if kind=='run' else 'title' if kind=='portfolio' else 'hypothesis',path.name)
                if not isinstance(title,str):malformed=True
                if malformed:title='记录格式待核实 · '+path.name
                review=data.get('scientific_review')
                result.append(dict(id=path.name,kind=kind,title=title,status=status if not malformed and isinstance(status,str) and status else 'unknown',review=review if not malformed and isinstance(review,str) and review else 'unknown',path=path,data=data))
        return sorted(result,key=lambda r:r['id'],reverse=True)
    def record(self,kind,rid):
        return next((r for r in self.records() if r['kind']==kind and r['id']==rid),None)
    def rows(self,table):
        if table not in ['entries','jobs','milestones','decisions']:raise ValueError('Unknown table')
        with self.db() as db:return [dict(x) for x in db.execute(f'SELECT * FROM {table} ORDER BY created DESC')]
    def export(self):
        write(self.workspace/'artifacts/workbench/history.json',{'exported_at':now(),'entries':self.rows('entries'),'jobs':self.rows('jobs'),'milestones':self.rows('milestones'),'demo_decisions':self.rows('decisions')})
    def add(self,author,kind,body,reference=''):
        if kind not in ['question','note'] or not 1<=len(author.strip())<=100 or not 3<=len(body.strip())<=10000 or len(reference)>300:raise ValueError('请填写作者与至少三个字符的内容，内容最多10000字符')
        with self.lock:
            with self.db() as db:db.execute('INSERT INTO entries VALUES (?,?,?,?,?,?)',(uuid.uuid4().hex,now(),author.strip(),kind,body.strip(),reference))
            self.export()
    def decide(self,case_id,action,author,reason):
        if case_id not in CASES or action not in ACTIONS:raise ValueError('未知案例或操作')
        if not 1<=len(author.strip())<=100 or not 10<=len(reason.strip())<=5000:raise ValueError('请填写记录人和10至5000字符的判断依据')
        with self.lock:
            with self.db() as db:db.execute('INSERT INTO decisions VALUES (?,?,?,?,?,?)',(uuid.uuid4().hex,now(),case_id,action,author.strip(),reason.strip()))
            self.export()
    def update_milestone(self,milestone,state,author,note,reference=''):
        validate_update(self,milestone,state,author,note,reference)
        with self.lock:
            with self.db() as db:db.execute('INSERT INTO milestones VALUES (?,?,?,?,?,?,?)',(uuid.uuid4().hex,now(),milestone,state,author.strip(),note.strip(),reference))
            self.export()
    def start(self,kind,rid):
        if kind=='run' and (self.workspace/'cache-info.json').exists():raise ValueError('备份视图不能核验外置盘原始输入；请在源工作区执行')
        if kind not in ['run','portfolio'] or not self.record(kind,rid):raise ValueError('没有可校验的记录')
        with self.lock:
            with self.db() as db:
                old=db.execute("SELECT id FROM jobs WHERE kind=? AND reference=? AND status='running'",(kind,rid)).fetchone()
                if old:return old['id']
                jid=uuid.uuid4().hex;db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?)',(jid,now(),kind,rid,'running','校验已开始'))
            self.export()
        threading.Thread(target=self.execute,args=(jid,kind,rid),daemon=True).start();return jid
    def execute(self,jid,kind,rid):
        bootstrap='import sys; sys.path[:0]='+repr([str(Path(__file__).resolve().parents[1])]+[p for p in sys.path if 'site-packages' in p])+'; from vbt.cli import main; raise SystemExit(main())'
        cmd=[sys.executable,'-S','-c',bootstrap,'--workspace',str(self.workspace),'--state-dir',str(self.state)]+(['portfolio'] if kind=='portfolio' else [])+['verify',rid]
        try:
            env={k:v for k,v in os.environ.items() if k in ['PATH','HOME','LANG','TMPDIR','SYSTEMROOT']};env['PYTHONPATH']=str(Path(__file__).resolve().parents[1])
            r=subprocess.run(cmd,capture_output=True,text=True,timeout=180,env=env)
            status='succeeded' if r.returncode==0 else 'failed';output=(r.stdout+'\n'+r.stderr)[-100000:]
        except Exception as e:status='failed';output=str(e)
        with self.lock:
            with self.db() as db:db.execute('UPDATE jobs SET status=?,output=? WHERE id=?',(status,output,jid))
            self.export()

from .ui_shell import STYLE,frame

LABEL={'run':'分析运行','portfolio':'研究快照','cycle':'研究循环','succeeded':'执行成功','failed':'执行失败','blocked':'执行受阻','completed':'循环已完成','snapshot':'已冻结','running':'正在校验','interrupted':'任务中断','pending':'待科学审查','revise':'需修订','insufficient_evidence':'证据不足','unknown':'未知'}
def badge(s):return f'<span class="badge {esc(s)}">{esc(LABEL.get(s,s))}</span>'
def link(r):return '/record?kind='+r['kind']+'&id='+quote(r['id'])
def table(records):
    from .history_view import record_table
    return record_table(records)
def page(app,path,q):
    records=app.records();body='';refresh=''
    if path=='/':
        from .dashboard_view import render
        body=render(app,records,table)
    elif path=='/cohort-lab':
        from .cohort_lab_view import render
        body=render(app,q)
    elif path=='/molecule-lab':
        from .molecule_lab_view import render
        body=render(app,q)
    elif path=='/patient-lab':
        from .patient_lab_view import render
        body=render(app,q)
    elif path=='/market/export':
        from .market_export import export_page
        body=export_page(app,q)
    elif path=='/disclosures':
        from .disclosure_view import render
        body=render(app,q)
    elif path=='/mapping':
        from .sponsor_view import render
        body=render(app,q)
    elif path=='/assets':
        from .market_asset_view import render
        body=render(app,q)
    elif path in ('/coverage','/market/progress'):
        from .market_progress_view import render
        body=render(app,q)
    elif path=='/market':
        from .market_view import render
        body=render(app,q)
    elif path=='/case':
        body=case_page(app,q.get('id',''))
    elif path=='/guide':
        body=guide()
    elif path=='/mission':
        body=mission_page(app,records)
    elif path=='/records':
        from .history_view import render
        body=render(records,q)
    elif path=='/record':
        from .history_view import safe_return
        r=app.record(q.get('kind'),q.get('id'))
        if not r:raise ValueError('记录不存在')
        heading='研究循环 · 原始研究假设' if r['kind']=='cycle' else '保存的研究问题' if r['kind']=='run' else '保存的研究计划'
        title_note='<p class="data-note">起始假设保留原文；本轮保存的决策见下方研究摘要。</p>' if r['kind']=='cycle' else ''
        body=f'<a href="{esc(safe_return(q.get("return")))}">← 返回历史</a><header class="page-header"><div><span class="eyebrow">{heading}</span><h1>{esc(r["title"])}</h1><p class="ref">{esc(r["id"])}</p>{title_note}</div></header>'+record_gates(r,read)+record_summary(r,read)
        if r['kind'] in ['run','portfolio'] and not (r['kind']=='run' and (app.workspace/'cache-info.json').exists()):body+=f'<form method="post" action="/verify"><input type="hidden" name="token" value="{app.token}"><input type="hidden" name="kind" value="{r["kind"]}"><input type="hidden" name="id" value="{esc(r["id"])}"><p><button>重新校验完整性</button> <span class="muted">校验在后台执行，可在任务页观测。</span></p></form>'
        labels={'audit.html':'审计报告','claims.json':'证据主张','review.json':'科学审查记录','work/results.json':'统计结果','work/sample_flow.json':'样本流转','stdout.log':'执行日志','stderr.log':'错误日志','index.html':'研究报告','plan.json':'研究计划','evaluation.json':'评估记录','provenance.json':'来源与版本','cycle.json':'研究循环记录','counterevidence.json':'反证记录','results.json':'分析结果'}
        body+='<section class="card record-attachments"><h2>证据与研究附件</h2><p class="data-note">附件保留原始版本，在新标签页打开。查看附件不会重新执行分析或完成审批。</p><div class="primary-actions">'
        names=(['audit.html','claims.json','review.json','work/results.json','work/sample_flow.json','stdout.log','stderr.log'] if r['kind']=='run' else ['index.html','plan.json','evaluation.json','provenance.json'] if r['kind']=='portfolio' else ['index.html','cycle.json','counterevidence.json','results.json'])
        available=[name for name in names if (r['path']/name).is_file()]
        for name in available:
            if name.endswith('.log'):continue
            body+=f'<a class="button secondary" href="/artifact?kind={r["kind"]}&id={quote(r["id"])}&file={quote(name)}" target="_blank" rel="noopener">{esc(labels[name])}</a>'
        if not available:body+='<p class="muted">尚无可查看的研究附件。</p>'
        body+='</div><details><summary>技术文件与执行日志</summary><ul>'
        for name in available:body+=f'<li><a href="/artifact?kind={r["kind"]}&id={quote(r["id"])}&file={quote(name)}" target="_blank" rel="noopener">{esc(labels[name])} · <code>{esc(name)}</code></a></li>'
        body+='</ul></details></section><details class="card"><summary>查看完整技术记录</summary><pre>'+esc(json.dumps(r['data'],ensure_ascii=False,indent=2))+'</pre></details><h2>关联研究笔记</h2>'+entries([e for e in app.rows('entries') if e['reference']==r['id']])+noteform(app,r['id'])
    elif path=='/jobs':
        jobs=app.rows('jobs');refresh='<meta http-equiv="refresh" content="5">' if any(j['status']=='running' for j in jobs) else ''
        body=('<header class="page-header"><div><span class="eyebrow">RECORD INTEGRITY</span><h1>完整性校验</h1>'
              '<p class="lead">查看研究记录的文件与证据校验。运行中每 5 秒刷新；校验结果不证明科学正确性。</p></div>'
              '<div class="page-actions"><a class="button secondary" href="/coverage#collection-queue">查看市场采集任务 →</a>'
              '<a class="button secondary" href="/records">研究历史</a></div></header>')
        for j in jobs:body+=f'<div class="card">{badge(j["status"])} <a href="/record?kind={j["kind"]}&id={quote(j["reference"])}">{esc(j["reference"])}</a><p class="ref">{esc(j["created"])} · {esc(j["id"])}</p><pre>{esc(j["output"])}</pre></div>'
        if not jobs:body+='<div class="card">暂无校验任务。打开一条分析运行或研究快照，点击“重新校验完整性”。</div>'
    elif path=='/notes':body='<h1>研究问题与笔记</h1><p class="sub">保存问题、判断和待办，形成可追溯的人工记录。保存不会自动启动分析或构成人工审批。</p>'+noteform(app)+entries(app.rows('entries'))
    else:raise ValueError('页面不存在')
    if q.get('saved')=='1':body='<div class="success" role="status">'+('请求已受理。请查看数据源状态或抓取任务；后台抓取期间可刷新页面。' if path in ('/market','/coverage','/mapping','/disclosures') else '已保存。记录已追加，历史内容保持不变。')+'</div>'+body
    if path not in ('/','/market','/coverage','/market/progress','/assets','/mapping','/market/export','/disclosures') and (app.workspace/'cache-info.json').exists():body='<div class="notice"><strong>历史研究记录的备份范围</strong><p>'+esc(read(app.workspace/'cache-info.json').get('scope'))+'</p></div>'+body
    return frame(body,path,refresh)

def entries(rows):
    return ''.join('<div class="card"><b>'+('研究问题' if e['kind']=='question' else '研究笔记')+' · '+esc(e['author'])+'</b><p class="ref">'+esc(e['created'])+' · '+esc(e['reference'])+'</p><div class="body">'+esc(e['body'])+'</div></div>' for e in rows) or '<p class="muted">暂无记录。</p>'
def noteform(app,reference=''):
    return f'<form class="card" method="post" action="/note" aria-label="保存研究问题与笔记"><input type="hidden" name="token" value="{app.token}"><input type="hidden" name="reference" value="{esc(reference)}"><label for="note-kind">记录类型</label><select id="note-kind" name="kind"><option value="question">研究问题</option><option value="note">研究笔记</option></select><label for="note-author">记录人</label><input id="note-author" name="author" maxlength="100" required><label for="note-body">内容</label><textarea id="note-body" name="body" minlength="3" maxlength="10000" required placeholder="希望验证什么？已有证据是什么？还缺少什么？"></textarea><p><button>保存记录</button></p></form>'

def handler(app, public_origin=None):
    class Handler(BaseHTTPRequestHandler):
        def send(self,status,body,ctype='text/html; charset=utf-8',sandbox=False):
            self.send_bytes(status,body.encode(),ctype,sandbox)
        def send_bytes(self,status,body,ctype,sandbox=False,download_name=None):
            if download_name is not None and not re.fullmatch(r'disclosure-[a-f0-9]{32}\.pdf',download_name):raise ValueError('Invalid evidence attachment name')
            self.send_response(status);self.send_header('Content-Type',ctype);self.send_header('Content-Length',str(len(body)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Content-Security-Policy',("sandbox; " if sandbox else '')+"default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'")
            if download_name is not None:self.send_header('Content-Disposition','attachment; filename="'+download_name+'"')
            self.end_headers();self.wfile.write(body)
        def safehost(self):return self.headers.get('Host')==(urlsplit(public_origin).netloc if public_origin else f'127.0.0.1:{self.server.server_port}')
        def do_GET(self):
            if not self.safehost():self.send(403,'Host rejected');return
            u=urlsplit(self.path);q={k:v[0] for k,v in parse_qs(u.query).items()}
            try:
                if u.path=='/patient-lab/export':
                    if set(q)!={'id'}:raise ValueError('Expected scenario ID')
                    record=app.patient_lab.get(q['id'])
                    self.send(200,json.dumps(record,ensure_ascii=False,indent=2),'application/json; charset=utf-8');return
                if u.path=='/market/disclosure-evidence':
                    from .market import Market
                    from .disclosures import evidence_file
                    raw,mime=evidence_file(Market(app.workspace,app.state),q.get('id',''),q.get('file',''))
                    name='disclosure-'+q['id']+'.pdf' if mime=='application/pdf' else None
                    self.send_bytes(200,raw,mime,True,download_name=name);return
                if u.path=='/market/identity-evidence':
                    from .market import Market
                    from .identity_view import evidence_file
                    raw,mime=evidence_file(Market(app.workspace,app.state),q.get('id',''),q.get('file',''))
                    self.send_bytes(200,raw,mime,True);return
                if u.path=='/market/export-file':
                    from .market import Market
                    from .market_export import export_file
                    snapshot,manifest=export_file(Market(app.workspace,app.state),q.get('id',''),q.get('file',''))
                    try:
                        with snapshot as file:
                            file.seek(0,2);size=file.tell();file.seek(0)
                            suffix='.csv' if q['file']=='observations.csv' else '.json'
                            self.send_response(200);self.send_header('Content-Type','text/csv; charset=utf-8' if suffix=='.csv' else 'application/json; charset=utf-8');self.send_header('Content-Disposition','attachment; filename=emeris-'+manifest['export_id']+suffix);self.send_header('Content-Length',str(size));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.end_headers()
                            for chunk in iter(lambda:file.read(1024*1024),b''):self.wfile.write(chunk)
                    except (BrokenPipeError,ConnectionResetError):pass
                    return
                if u.path=='/artifact':
                    r=app.record(q.get('kind'),q.get('id'));name=q.get('file','')
                    allowed={'audit.html','index.html','claims.json','review.json','work/results.json','work/sample_flow.json','stdout.log','stderr.log','plan.json','evaluation.json','provenance.json','cycle.json','counterevidence.json','results.json'}
                    if not r or name not in allowed:raise ValueError('记录附件不存在')
                    p=within(r['path']/name,r['path']);text=p.read_text();self.send(200,text,'text/html; charset=utf-8' if name.endswith('.html') else 'text/plain; charset=utf-8',True);return
                self.send(200,page(app,u.path,q))
            except (ValueError,OSError) as e:
                self.send(404,('<h1>Unable to open scenario</h1><p>'+esc(e)+'</p><a href="/patient-lab">Back to Patient Lab</a>') if u.path.startswith(('/patient-lab', '/molecule-lab', '/cohort-lab')) else '<h1>暂时无法读取</h1><p>'+esc(e)+'</p><a href="/">返回首页</a>')
        def do_POST(self):
            origin=public_origin or f'http://127.0.0.1:{self.server.server_port}'
            if not self.safehost() or self.headers.get('Origin')!=origin:self.send(403,'Origin rejected');return
            try:
                n=int(self.headers.get('Content-Length','0'))
                if not 0<n<=65536:raise ValueError('Request too large or empty')
                q={k:v[0] for k,v in parse_qs(self.rfile.read(n).decode()).items()}
                if not secrets.compare_digest(q.get('token',''),app.token):self.send(403,'Token rejected');return
                if self.path.startswith('/market/'):
                    from .market import Market
                    market=Market(app.workspace,app.state)
                    if self.path=='/market/export':
                        from .market_export import create_export
                        manifest=create_export(market,q)
                        self.send_response(303);self.send_header('Location','/market/export?id='+manifest['export_id']);self.send_header('Content-Length','0');self.end_headers();return
                    elif self.path=='/market/disclosure/enqueue':
                        market.enqueue('disclosure',q.get('source_id',''))
                    elif self.path=='/market/resolve':
                        from .market_jobs import enqueue
                        enqueue(market,'resolve',q.get('candidate_id',''))
                    elif self.path=='/market/resolve-batch':
                        from .sponsor_view import enqueue_batch
                        enqueue_batch(market)
                    elif self.path=='/market/resolve-all':
                        from .market_traversal import enqueue_traversal
                        enqueue_traversal(market,limit=1000,market_filter=q.get('market',''))
                    elif self.path=='/market/identity-batch':
                        from .market_jobs import enqueue_identity,enqueue_identity_repairs
                        enqueue_identity_repairs(market,limit=1000)
                        enqueue_identity(market,limit=1000)
                    elif self.path=='/market/identity-schedule':
                        if q.get('enabled') not in ('0','1'):raise ValueError('Invalid source identity schedule setting')
                        with market.db() as db:db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',('identity.enabled',q['enabled']))
                    elif self.path=='/market/proposal-review':
                        from .sponsor_review import review
                        result=review(market,q.get('proposal_id',''),q.get('decision',''),q.get('reviewer',''),q.get('reason',''))
                        if result['decision']=='accepted':market.enqueue('company',result['company'])
                    elif self.path=='/market/discover':
                        from .issuer_discovery import discover
                        from .market_coverage import refresh_candidates
                        refresh_candidates(market,discover(market.rows('listings')))
                    elif self.path=='/market/expand':
                        from .market_expansion import expand
                        expand(market)
                    elif self.path=='/market/batch':
                        from .market_jobs import enqueue_all
                        enqueue_all(market)
                    elif self.path=='/market/cancel':
                        from .market_jobs import cancel
                        cancel(market,q.get('jid',''))
                    elif self.path=='/market/review':
                        from .market_coverage import review_candidate
                        review_candidate(market,q.get('candidate_id',''),q.get('decision',''),q.get('reviewer',''),q.get('reason',''))
                    elif self.path=='/market/company':market.add_company(q.get('market',''),q.get('ticker',''),q.get('name',''),q.get('aliases','').splitlines(),q.get('provenance',''))
                    elif self.path=='/market/refresh':market.enqueue(q.get('kind'),q.get('reference'))
                    elif self.path=='/market/schedule':market.schedule(q.get('enabled')=='1')
                    else:raise ValueError('Unknown market operation')
                    dest=('/disclosures' if self.path=='/market/disclosure/enqueue' else '/mapping' if self.path in ('/market/resolve','/market/resolve-batch','/market/resolve-all','/market/identity-batch','/market/identity-schedule','/market/proposal-review') else '/coverage' if self.path in ('/market/discover','/market/expand','/market/batch','/market/cancel','/market/review') else '/market')+'?saved=1'
                elif self.path=='/patient-lab/run':
                    record=app.patient_lab.run({k:v for k,v in q.items() if k!='token'})
                    dest='/patient-lab?id='+record['run_id']
                elif self.path=='/patient-lab/followup':
                    if set(q)!={'token','parent_id'}:raise ValueError('Expected parent scenario ID')
                    record=app.patient_lab.followup(q['parent_id'])
                    dest='/patient-lab?id='+record['run_id']
                elif self.path=='/decision':app.decide(q.get('case_id',''),q.get('action',''),q.get('author',''),q.get('reason',''));dest='/case?id='+quote(q['case_id'])+'&saved=1'
                elif self.path=='/note':app.add(q.get('author',''),q.get('kind',''),q.get('body',''),q.get('reference',''));dest='/notes?saved=1'
                elif self.path=='/milestone':app.update_milestone(q.get('milestone',''),q.get('status',''),q.get('author',''),q.get('note',''),q.get('reference',''));dest='/mission?saved=1'
                elif self.path=='/verify':app.start(q.get('kind'),q.get('id'));dest='/jobs'
                else:raise ValueError('Unknown action')
                self.send_response(303);self.send_header('Location',dest);self.send_header('Content-Length','0');self.end_headers()
            except (ValueError,OSError,sqlite3.IntegrityError) as e:
                self.send(400,('<h1>Unable to complete request</h1><p>'+esc(e)+'</p><a href="/patient-lab">Back to Patient Lab</a>') if self.path.startswith('/patient-lab') else '<h1>未能完成操作</h1><p>'+esc(e)+'</p><a href="/">返回首页</a>')
    return Handler

def serve(workspace,state=None,port=18764):
    with ThreadingHTTPServer(('127.0.0.1',port),BaseHTTPRequestHandler) as server:
        app=Workbench(workspace,state)
        server.RequestHandlerClass=handler(app)
        print(f'Workbench ready: http://127.0.0.1:{port}',flush=True)
        from .market import Market
        stop=threading.Event();market=Market(app.workspace,app.state)
        from .market_jobs import run_worker
        run_worker(market,stop,workers=2)
        threading.Thread(target=market.scheduler,args=(stop,),daemon=True).start()
        try:server.serve_forever()
        except KeyboardInterrupt:pass
        finally:stop.set()

def mission_page(app,records):
    updates=app.rows('milestones');latest={}
    for event in updates:latest.setdefault(event['milestone'],event)
    body='<div class="kicker">MISSION & EVIDENCE</div><h1>目标与里程碑</h1><div class="card"><h2>'+esc(MISSION)+'</h2><p>产品定位：AI 驱动的临床资产研究与投资决策支持平台。当前优先验证预测能力；自研药物管线不是本阶段交付目标。</p></div><div class="notice">以下是人工录入的进展，不是科学审批。没有记录表示尚未记录，不能解释为已完成或没有问题。已提交证据也不等于临床预测或盈利能力成立。</div>'
    for m in MILESTONES:
        event=latest.get(m['id']);status=STATES[event['status']] if event else '尚无进展记录'
        body+='<div class="card"><h2>'+esc(m['title'])+'</h2><span class="badge">'+esc(status)+'</span><p><b>验收要求：</b>'+esc(m['acceptance'])+'</p><p><b>下一步：</b>'+esc(m['next'])+'</p>'
        if event:body+='<p class="body">'+esc(event['note'])+'</p><p class="ref">'+esc(event['author'])+' · '+esc(event['created'])+'</p>'
        body+='</div>'
    body+='<form class="card" method="post" action="/milestone"><h2>记录进展</h2><input type="hidden" name="token" value="'+app.token+'"><label>里程碑</label><select name="milestone">'+''.join('<option value="'+m['id']+'">'+esc(m['title'])+'</option>' for m in MILESTONES)+'</select><label>状态</label><select name="status">'+''.join('<option value="'+k+'">'+esc(v)+'</option>' for k,v in STATES.items())+'</select><label>记录人</label><input name="author" required maxlength="100"><label>说明／阻塞原因</label><textarea name="note" required minlength="10" maxlength="10000"></textarea><label>关联证据（提交证据时必选）</label><select name="reference"><option value="">暂不关联</option>'+''.join('<option value="'+esc(r['id'])+'">'+esc(r['kind']+' · '+r['id'])+'</option>' for r in records)+'</select><p><button>追加进展记录</button></p></form><h2>变更历史</h2>'
    for event in updates:
        body+='<div class="card"><b>'+esc(event['milestone'])+' · '+esc(STATES[event['status']])+'</b><p class="body">'+esc(event['note'])+'</p><p class="ref">'+esc(event['author'])+' · '+esc(event['created'])+'</p>'
        for r in records:
            if r['id']==event['reference']:body+='<a href="'+link(r)+'">查看关联证据</a>'
        body+='</div>'
    return body
