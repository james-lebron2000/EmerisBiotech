"""Local single-user research workbench. Historical artifacts remain immutable."""
import html,json,secrets,sqlite3,threading,subprocess,sys,os,hashlib,uuid
from pathlib import Path
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from urllib.parse import urlsplit,parse_qs,quote
from .io import ensure_storage,now,write,within
from .mission import MISSION,MILESTONES,STATES,validate_update
from .product_views import CASES,ACTIONS,home_intro,case_page,guide,record_summary

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
        self.state.mkdir(parents=True,exist_ok=True);self.dbpath=self.state/'workbench.sqlite3';self.token=secrets.token_urlsafe(32);self.lock=threading.Lock()
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS decisions (id TEXT PRIMARY KEY, created TEXT, case_id TEXT, action TEXT, author TEXT, reason TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS entries (id TEXT PRIMARY KEY, created TEXT, author TEXT, kind TEXT, body TEXT, reference TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS milestones (id TEXT PRIMARY KEY, created TEXT, milestone TEXT, status TEXT, author TEXT, note TEXT, reference TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, created TEXT, kind TEXT, reference TEXT, status TEXT, output TEXT)')
            db.execute("UPDATE jobs SET status='interrupted', output=output || '\n服务重启，任务中断；请重新发起校验。' WHERE status='running'")
    def db(self):
        db=sqlite3.connect(self.dbpath,timeout=30);db.row_factory=sqlite3.Row;return db
    def records(self):
        result=[]
        for kind,folder,name in [('run','runs','status.json'),('portfolio','portfolio/snapshots','evaluation.json'),('cycle','cycles','cycle.json')]:
            for path in (self.workspace/'artifacts'/folder).glob('*'):
                if not path.is_dir():continue
                data=read(path/name)
                status=data.get('execution') if kind=='run' else ('completed' if data.get('complete_research_cycle') else 'recorded') if kind=='cycle' else 'snapshot'
                title=read(path/'inputs/plan.json').get('question',path.name) if kind=='run' else read(path/'plan.json').get('title',path.name) if kind=='portfolio' else data.get('hypothesis',path.name)
                result.append(dict(id=path.name,kind=kind,title=title,status=status or 'unknown',review=data.get('scientific_review','pending'),path=path,data=data))
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

STYLE='''*{box-sizing:border-box}body{margin:0;background:#f4f6f8;color:#182b36;font:15px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}aside{position:fixed;width:224px;height:100vh;background:#102d35;color:#c7dcdf;padding:30px 22px}aside b{color:#fff;font-size:22px}aside small{display:block;margin:8px 0 35px;color:#80a8af}aside a{display:block;color:#c7dcdf;text-decoration:none;padding:12px;border-radius:8px;margin:5px 0}aside a:hover{background:#24464e;color:white}main{margin-left:224px;max-width:1450px;padding:35px 45px}h1{font-size:30px;line-height:1.3;margin:8px 0 12px}h2{font-size:20px}p.sub{color:#60747c}.kicker{color:#087970;letter-spacing:2px;font-size:12px;font-weight:700}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:16px}.card{background:white;border:1px solid #dee7e9;border-radius:12px;padding:22px;margin:18px 0}.metric{font-size:34px;font-weight:700;display:block}.muted{color:#617780}.badge{font-size:12px;background:#e8efef;padding:5px 9px;border-radius:6px;display:inline-block}.failed,.blocked,.interrupted{background:#fff0df;color:#985210}.succeeded,.completed{background:#e2f4ed;color:#196b54}table{border-collapse:collapse;width:100%;font-size:14px}td,th{text-align:left;padding:15px 10px;border-bottom:1px solid #edf0f1;vertical-align:top}td a{font-weight:600}a{color:#08776f;text-decoration:none}a:hover{text-decoration:underline}input,textarea,select{font:inherit;border:1px solid #c8d7dc;border-radius:7px;padding:10px;background:white;max-width:100%}input[type=search]{width:55%}textarea{width:100%;min-height:130px}label{display:block;margin:10px 0 5px}button,.button{font:inherit;display:inline-block;border:0;border-radius:7px;background:#08796f;color:white;padding:10px 16px;cursor:pointer}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f2f5f7;padding:16px;font-size:12px}details{margin:14px 0}summary{cursor:pointer;font-weight:600}.notice{border-left:4px solid #d39d45;padding:12px 18px;background:#fff7e9}.split{display:grid;grid-template-columns:2fr 1fr;gap:22px}.body{white-space:pre-wrap;overflow-wrap:anywhere}.ref{font-size:11px;overflow-wrap:anywhere;color:#67808a}.scroll{overflow:auto}@media(max-width:850px){aside{position:static;width:auto;height:auto;padding:15px}aside small{margin:0}aside a{display:inline-block}main{margin:0;padding:20px}.grid{grid-template-columns:repeat(2,1fr)}.split{display:block}}'''
STYLE += """
.hero{padding:36px;border-radius:18px;background:#102f3d;color:#fff;margin:4px 0 30px}.hero h1{font-size:40px;letter-spacing:-1px;line-height:1.25;margin:20px 0}.hero p{color:#b8d0d8;font-size:18px}.hero .button{background:#b9f0d7;color:#113c36;font-weight:650}.secondary{color:#deeeee;margin-left:16px}.hero-note{margin-top:22px;color:#a6c0c9;font-size:12px}.eyebrow{font-size:11px;letter-spacing:1.5px;font-weight:700;color:#52777b}.hero .eyebrow{color:#95c2c9}.case-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}.case-card{display:block;background:#fff;border:1px solid #dce5e7;border-radius:12px;padding:22px;color:#203940;transition:transform .15s}.case-card:hover{transform:translateY(-3px);text-decoration:none;border-color:#73a49c}.case-card h3{font-size:18px;margin:16px 0}.case-card p{color:#60767f;font-size:14px}.text-link{font-size:13px;color:#08796f;font-weight:650}.section-head{display:flex;justify-content:space-between;align-items:center;gap:20px;margin-top:25px}.section-head>span{font-size:12px;color:#677e85}.lead{font-size:20px;color:#536c76}.counter{border-top:3px solid #d59456}.conclusion{border-left:4px solid #08796f}.empty{padding:35px;text-align:center;background:#fff;border:1px dashed #c3d3d8;border-radius:12px;color:#637b84;margin:18px 0}.success{background:#e1f4e9;border:1px solid #a7d7bf;border-radius:8px;padding:15px;margin-bottom:18px;color:#17603f}.notice{font-size:13px;margin-bottom:18px}aside b{font-size:21px}.split{grid-template-columns:1fr 1fr}a:focus-visible,button:focus-visible,input:focus-visible,textarea:focus-visible,select:focus-visible{outline:3px solid #dea03a;outline-offset:3px}@media(max-width:950px){.case-grid{grid-template-columns:1fr}.hero h1{font-size:30px}.hero{padding:24px}.section-head{display:block}}
"""

LABEL={'run':'分析运行','portfolio':'研究快照','cycle':'研究循环','succeeded':'执行成功','failed':'执行失败','blocked':'执行受阻','completed':'循环已完成','snapshot':'已冻结','running':'正在校验','interrupted':'任务中断','pending':'待科学审查','revise':'需修订','insufficient_evidence':'证据不足','unknown':'未知'}
def badge(s):return f'<span class="badge {esc(s)}">{esc(LABEL.get(s,s))}</span>'
def link(r):return '/record?kind='+r['kind']+'&id='+quote(r['id'])
def table(records):
    if not records:return '<div class="empty"><h3>这里还没有匹配的研究记录</h3><p>可以先体验内置回顾案例，或调整筛选条件查看已有历史。</p><a href="/case?id=osmr-uc">体验研究流程 →</a></div>'
    return '<div class="scroll"><table><tr><th>研究与记录</th><th>类型</th><th>执行 / 记录状态</th><th>科学审查</th></tr>'+''.join(f'<tr><td><a href="{link(r)}">{esc(r["title"])}</a><div class="ref">{esc(r["id"])}</div></td><td>{LABEL[r["kind"]]}</td><td>{badge(r["status"])}</td><td>{badge(r["review"])}</td></tr>' for r in records)+'</table></div>'
def page(app,path,q):
    records=app.records();body='';refresh=''
    if path=='/':
        body=home_intro()+'<h2>研究控制台 · 当前工作区</h2><div class="grid">'
        for label,value in [('分析运行',sum(r['kind']=='run' for r in records)),('研究循环',sum(r['kind']=='cycle' for r in records)),('冻结快照',sum(r['kind']=='portfolio' for r in records)),('运行中任务',sum(j['status']=='running' for j in app.rows('jobs')))]:body+=f'<div class="card"><span class="muted">{label}</span><strong class="metric">{value}</strong></div>'
        body+='</div><div class="card"><h2>我们的目标</h2><p>'+esc(MISSION)+'</p><a href="/mission">查看验收条件与下一步 →</a></div><div class="notice">执行成功不等于科学结论通过。当前尚未建立经过验证的二期／三期预测能力或投资收益优势。</div><div class="card"><h2>最近研究记录</h2>'+table(records[:8])+'</div><a class="button" href="/notes">提出研究问题</a> <a href="/records">查看全部历史 →</a>'
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
        search=q.get('q','').lower();kind=q.get('kind','');filtered=[r for r in records if (not kind or r['kind']==kind) and search in (r['title']+' '+r['id']).lower()]
        body='<h1>历史记录</h1><p class="sub">包含成功、失败和受阻记录；旧版本不会被新版本覆盖。</p><form><input type="search" name="q" placeholder="搜索研究问题或运行编号" value="'+esc(q.get('q',''))+'"> <select name="kind"><option value="">所有类型</option>'+''.join(f'<option value="{k}" '+('selected' if k==kind else '')+f'>{LABEL[k]}</option>' for k in ['run','cycle','portfolio'])+'</select> <button>筛选</button></form><div class="card">'+f'<p>{len(filtered)} 条记录</p>'+table(filtered)+'</div>'
    elif path=='/record':
        r=app.record(q.get('kind'),q.get('id'))
        if not r:raise ValueError('记录不存在')
        body=f'<a href="/records">← 返回历史</a><h1>{esc(r["title"])}</h1><p class="ref">{esc(r["id"])}</p>'+badge(r['status'])+' '+badge(r['review'])
        if r['kind'] in ['run','portfolio'] and not (r['kind']=='run' and (app.workspace/'cache-info.json').exists()):body+=f'<form method="post" action="/verify"><input type="hidden" name="token" value="{app.token}"><input type="hidden" name="kind" value="{r["kind"]}"><input type="hidden" name="id" value="{esc(r["id"])}"><p><button>重新校验完整性</button> <span class="muted">校验在后台执行，可在任务页观测。</span></p></form>'
        for name in (['audit.html','claims.json','review.json','work/results.json','work/sample_flow.json','stdout.log','stderr.log'] if r['kind']=='run' else ['index.html','plan.json','evaluation.json','provenance.json'] if r['kind']=='portfolio' else ['index.html','cycle.json','counterevidence.json','results.json']):
            if (r['path']/name).is_file():body+=f'<a class="button" style="margin:4px" href="/artifact?kind={r["kind"]}&id={quote(r["id"])}&file={quote(name)}" target="_blank" rel="noopener">{esc(name)}</a>'
        body+=record_summary(r,read)+'<details class="card"><summary>查看完整技术记录</summary><pre>'+esc(json.dumps(r['data'],ensure_ascii=False,indent=2))+'</pre></details><h2>关联研究笔记</h2>'+entries([e for e in app.rows('entries') if e['reference']==r['id']])+noteform(app,r['id'])
    elif path=='/jobs':
        jobs=app.rows('jobs');refresh='<meta http-equiv="refresh" content="5">' if any(j['status']=='running' for j in jobs) else ''
        body='<h1>任务观测</h1><p class="sub">运行中每 5 秒刷新。校验检查文件与证据记录，不证明科学正确性。</p>'
        for j in jobs:body+=f'<div class="card">{badge(j["status"])} <a href="/record?kind={j["kind"]}&id={quote(j["reference"])}">{esc(j["reference"])}</a><p class="ref">{esc(j["created"])} · {esc(j["id"])}</p><pre>{esc(j["output"])}</pre></div>'
        if not jobs:body+='<div class="card">暂无校验任务。打开一条分析运行或研究快照，点击“重新校验完整性”。</div>'
    elif path=='/notes':body='<h1>研究问题与笔记</h1><p class="sub">保存问题、判断和待办，形成可追溯的人工记录。保存不会自动启动分析或构成人工审批。</p>'+noteform(app)+entries(app.rows('entries'))
    else:raise ValueError('页面不存在')
    if q.get('saved')=='1':body='<div class="success" role="status">'+('请求已受理。请查看数据源状态或抓取任务；后台抓取期间可刷新页面。' if path=='/market' else '已保存。记录已追加，历史内容保持不变。')+'</div>'+body
    if path!='/market' and (app.workspace/'cache-info.json').exists():body='<div class="notice">'+esc(read(app.workspace/'cache-info.json').get('scope'))+'</div>'+body
    return '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'+refresh+'<title>EmerisBiotech · 临床资产研究</title><style>'+STYLE+'</style></head><body><aside><b>EmerisBiotech</b><small>临床资产研究工作台</small><a href="/">研究首页</a><a href="/market">市场与管线</a><a href="/guide">演示路线</a><a href="/mission">目标与里程碑</a><a href="/records">历史记录</a><a href="/jobs">任务观测</a><a href="/notes">问题与笔记</a><p style="margin-top:50px;font-size:12px">本地单用户 · 研究用途<br>无自动交易</p></aside><main>'+body+'</main></body></html>'
def entries(rows):
    return ''.join('<div class="card"><b>'+('研究问题' if e['kind']=='question' else '研究笔记')+' · '+esc(e['author'])+'</b><p class="ref">'+esc(e['created'])+' · '+esc(e['reference'])+'</p><div class="body">'+esc(e['body'])+'</div></div>' for e in rows) or '<p class="muted">暂无记录。</p>'
def noteform(app,reference=''):
    return f'<form class="card" method="post" action="/note"><input type="hidden" name="token" value="{app.token}"><input type="hidden" name="reference" value="{esc(reference)}"><label>记录类型</label><select name="kind"><option value="question">研究问题</option><option value="note">研究笔记</option></select><label>记录人</label><input name="author" maxlength="100" required><label>内容</label><textarea name="body" minlength="3" maxlength="10000" required placeholder="希望验证什么？已有证据是什么？还缺少什么？"></textarea><p><button>保存记录</button></p></form>'

def handler(app, public_origin=None):
    class Handler(BaseHTTPRequestHandler):
        def send(self,status,body,ctype='text/html; charset=utf-8',sandbox=False):
            self.send_response(status);self.send_header('Content-Type',ctype);self.send_header('Content-Length',str(len(body.encode())));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Content-Security-Policy',("sandbox; " if sandbox else '')+"default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'");self.end_headers();self.wfile.write(body.encode())
        def safehost(self):return self.headers.get('Host')==(urlsplit(public_origin).netloc if public_origin else f'127.0.0.1:{self.server.server_port}')
        def do_GET(self):
            if not self.safehost():self.send(403,'Host rejected');return
            u=urlsplit(self.path);q={k:v[0] for k,v in parse_qs(u.query).items()}
            try:
                if u.path=='/artifact':
                    r=app.record(q.get('kind'),q.get('id'));name=q.get('file','')
                    allowed={'audit.html','index.html','claims.json','review.json','work/results.json','work/sample_flow.json','stdout.log','stderr.log','plan.json','evaluation.json','provenance.json','cycle.json','counterevidence.json','results.json'}
                    if not r or name not in allowed:raise ValueError('记录附件不存在')
                    p=within(r['path']/name,r['path']);text=p.read_text();self.send(200,text,'text/html; charset=utf-8' if name.endswith('.html') else 'text/plain; charset=utf-8',True);return
                self.send(200,page(app,u.path,q))
            except (ValueError,OSError) as e:self.send(404,'<h1>暂时无法读取</h1><p>'+esc(e)+'</p><a href="/">返回首页</a>')
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
                    if self.path=='/market/company':market.add_company(q.get('market',''),q.get('ticker',''),q.get('name',''),q.get('aliases','').splitlines(),q.get('provenance',''))
                    elif self.path=='/market/refresh':market.enqueue(q.get('kind'),q.get('reference'))
                    elif self.path=='/market/schedule':market.schedule(q.get('enabled')=='1')
                    else:raise ValueError('Unknown market operation')
                    dest='/market?saved=1'
                elif self.path=='/decision':app.decide(q.get('case_id',''),q.get('action',''),q.get('author',''),q.get('reason',''));dest='/case?id='+quote(q['case_id'])+'&saved=1'
                elif self.path=='/note':app.add(q.get('author',''),q.get('kind',''),q.get('body',''),q.get('reference',''));dest='/notes?saved=1'
                elif self.path=='/milestone':app.update_milestone(q.get('milestone',''),q.get('status',''),q.get('author',''),q.get('note',''),q.get('reference',''));dest='/mission?saved=1'
                elif self.path=='/verify':app.start(q.get('kind'),q.get('id'));dest='/jobs'
                else:raise ValueError('Unknown action')
                self.send_response(303);self.send_header('Location',dest);self.send_header('Content-Length','0');self.end_headers()
            except (ValueError,OSError,sqlite3.IntegrityError) as e:self.send(400,'<h1>未能完成操作</h1><p>'+esc(e)+'</p><a href="/">返回首页</a>')
    return Handler

def serve(workspace,state=None,port=18764):
    with ThreadingHTTPServer(('127.0.0.1',port),BaseHTTPRequestHandler) as server:
        app=Workbench(workspace,state)
        server.RequestHandlerClass=handler(app)
        print(f'Workbench ready: http://127.0.0.1:{port}',flush=True)
        from .market import Market
        stop=threading.Event();market=Market(app.workspace,app.state)
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
