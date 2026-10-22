import csv
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time
from urllib.parse import urlsplit
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException, Request, Response, Depends, UploadFile, File, Form, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, Field, ConfigDict
from .store import Store
from .data import BASE, FIELDS, parse, suggested, summarize
from .research import ResearchRequest, execute
from .triallab import TrialRequest, execute as execute_trial
from .forecast_registry import read_registry
from .dashboard import build_dashboard
from .product import build_product, trial_detail
from .analytics import build_analytics
from .platform_store import evaluation_rows
from .platform_api import router as platform_router
from .predict_api import router as predict_router
from .intake_api import router as intake_router
from .readout_updates_api import router as readout_updates_router
from .arena_api import router as arena_router
from .arena_lab import router as arena_lab_router
from .arena_lab import lab_user, access_shell
from .arena_public import router as arena_public_router
from .predome_api import read_release
from .historical_api import read_historical
from .expanded_api import read_expanded, expanded_report_path
from .continuous_api import read_continuous, report_path as continuous_report_path, optimization_path

ROOT=Path(__file__).resolve().parents[1]
def now():return datetime.now(timezone.utc).isoformat()
def uid():return secrets.token_hex(16)
def digest(text):return hashlib.sha256(text.encode()).hexdigest()
def password_hash(password,salt=None):
    salt=salt or secrets.token_hex(16)
    return salt+':'+hashlib.pbkdf2_hmac('sha256',password.encode(),salt.encode(),310000).hex()

class Credentials(BaseModel):
    name:str=Field(min_length=3,max_length=32,pattern=r'^[a-zA-Z0-9_-]+$')
    password:str=Field(min_length=12,max_length=128)
class Mapping(BaseModel):
    model_config=ConfigDict(extra='forbid')
    mapping:dict[str,str]
    expected_policy:str
    learn:bool=False
    confirmed_semantics:bool=False
class Rollback(BaseModel):
    expected_policy:str

def create_app(path=None):
    public_mode=os.environ.get('EMERIS_MODE','local')=='public'
    public_origin=os.environ.get('EMERIS_PUBLIC_ORIGIN','').rstrip('/')
    hostlist=[v.strip() for v in os.environ.get('EMERIS_ALLOWED_HOSTS','127.0.0.1,localhost,testserver').split(',') if v.strip()]
    if public_mode and (urlsplit(public_origin).scheme!='https' or not urlsplit(public_origin).hostname or urlsplit(public_origin).hostname not in hostlist or urlsplit(public_origin).path or '*' in hostlist):
        raise RuntimeError('Public mode requires an explicit HTTPS origin and matching allowed host')
    max_users=int(os.environ.get('EMERIS_MAX_USERS','100'))
    max_datasets=int(os.environ.get('EMERIS_MAX_DATASETS','20'))
    store=Store(path or os.environ.get('EMERIS_DATA_DIR', str(Path.home()/'.local/share/emeris-live')))
    app=FastAPI(title='Predome Agent System Research',docs_url=None,redoc_url=None)
    app.state.store=store
    app.add_middleware(TrustedHostMiddleware,allowed_hosts=hostlist)
    @app.middleware('http')
    async def boundaries(request,call_next):
        if request.method not in ('GET','HEAD','OPTIONS'):
            if request.headers.get('origin')!=(public_origin if public_mode else str(request.base_url).rstrip('/')):
                return JSONResponse({'detail':'请从工作台页面提交操作。'},403)
            try:
                if int(request.headers.get('content-length','0'))>5*1024*1024+65536:
                    return JSONResponse({'detail':'文件超过 5 MiB。'},413)
            except ValueError:return JSONResponse({'detail':'无效请求。'},400)
            body=bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body)>5*1024*1024+65536:return JSONResponse({'detail':'请求超过大小限制。'},413)
            request._body=bytes(body)
        result=await call_next(request)
        result.headers.update({'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; object-src 'none'; base-uri 'self'; form-action 'self'",'X-Content-Type-Options':'nosniff','Cache-Control':'no-store','Referrer-Policy':'no-referrer'})
        if public_mode:result.headers['Strict-Transport-Security']='max-age=86400'
        return result
    @app.exception_handler(ValueError)
    async def invalid(request,exc):return JSONResponse({'detail':str(exc)},422)
    @app.exception_handler(RequestValidationError)
    async def format_error(request,exc):return JSONResponse({'detail':'输入格式不正确，请检查字段和长度。'},422)
    @app.exception_handler(Exception)
    async def failed(request,exc):return JSONResponse({'detail':'操作未完成，数据未确认为成功。请重试。'},500)
    def user(request:Request):
        token=request.cookies.get('emeris_session','')
        with store.connect() as db:
            row=db.execute('SELECT s.*,u.name FROM sessions s JOIN users u ON u.id=s.owner WHERE token=? AND expires>?',(digest(token),time.time())).fetchone()
        if not row:raise HTTPException(401,'请先登录。')
        if request.method not in ('GET','HEAD') and not hmac.compare_digest(row['csrf'],request.headers.get('x-csrf-token','')):
            raise HTTPException(403,'会话校验失败，请重新登录。')
        return dict(row)
    def active(db,owner):
        row=db.execute('SELECT p.* FROM policies p JOIN active a ON a.policy=p.id WHERE a.owner=?',(owner,)).fetchone()
        return dict(row)|{'content':store.unpack(row['payload'])}
    def event(db,owner,kind,payload):db.execute('INSERT INTO events(owner,kind,payload,created) VALUES(?,?,?,?)',(owner,kind,store.pack(payload),now()))
    def rate_limit(bucket,limit,seconds):
        blocked=False
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            instant=time.time();key=digest(bucket)
            db.execute('DELETE FROM auth_limits WHERE starts<?',(instant-max(86400,seconds),))
            r=db.execute('SELECT * FROM auth_limits WHERE bucket=?',(key,)).fetchone()
            if not r or r['starts']+seconds<=instant:
                db.execute('INSERT OR REPLACE INTO auth_limits VALUES(?,?,1)',(key,instant))
            elif r['count']>=limit:blocked=True
            else:db.execute('UPDATE auth_limits SET count=count+1 WHERE bucket=?',(key,))
        if blocked:raise HTTPException(429,'操作过于频繁，请稍后重试。')
    def dataset(db,owner,id):
        row=db.execute('SELECT * FROM datasets WHERE id=? AND owner=?',(id,owner)).fetchone()
        if not row:raise HTTPException(404,'未找到有权访问的数据。')
        return dict(row)|{'content':store.unpack(row['payload'])}
    def visible(row,details=False):
        v=row['content']
        value={k:v.get(k) for k in ['name','disease','source','source_url','source_sha256','mapping','summary','learning_enabled','policy_at_import']}
        value.update(id=row['id'],sha256=row['sha'],created=row['created'])
        if details:value.update(headers=v['headers'],preview=v['rows'][:8])
        return value
    def save_dataset(owner,raw,name,disease,source,learn=False,source_url=None,source_sha256=None):
        headers,rows=parse(raw)
        sha=hashlib.sha256(raw).hexdigest()
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT COUNT(*) FROM datasets WHERE owner=?',(owner,)).fetchone()[0]>=max_datasets:
                raise HTTPException(409,'已达到当前研究账号的数据集限额。')
            p=active(db,owner)
            mapping=suggested(headers,p['content']['aliases'])
            summary=summarize(headers,rows,mapping) if 'subject_id' in mapping.values() else None
            value=dict(name=name,disease=disease,source=source,source_url=source_url,source_sha256=source_sha256,headers=headers,rows=rows,mapping=mapping,summary=summary,learning_enabled=learn,policy_at_import=p['id'])
            payload=store.pack(value)
            used=db.execute('SELECT COALESCE(SUM(length(payload)),0) FROM datasets WHERE owner=?',(owner,)).fetchone()[0]
            if used+len(payload)>50*1024*1024:raise HTTPException(409,'已达到当前账号的 50 MiB 存储限额。')
            id=uid()
            try:db.execute('INSERT INTO datasets VALUES(?,?,?,?,?)',(id,owner,sha,payload,now()))
            except sqlite3.IntegrityError:raise HTTPException(409,'这份数据已导入；重复文件不会增加学习证据。')
            event(db,owner,'dataset_imported',{'dataset_id':id,'sha256':sha,'source':source})
            return visible(dataset(db,owner,id),True)
    @app.get('/api/health')
    def health():return {'status':'ok','version':'1.2.5','mode':'server_research_pilot' if public_mode else 'local_research_pilot','clinical_use':False,'learning':'confirmed_schema_aliases_only','weight_training':False,'offline_weight_training':True,'offline_training_scope':'exploratory_historical_pilot_only','formal_training_completed':False,'research_workflows':['public_cohort','pdo_simulation','trial_design_scenario','trial_evidence_catalog','registered_candidate_forecasts','predome_comparison','historical_temporal_benchmark','expanded_historical_development_replay','continuous_window_backtest_and_frozen_forecasts','open_forecast_challenge','patent_evidence'],'synthetic_baseline_fitting':True,'clinical_prediction_validated':False}
    @app.get('/api/config')
    def config():return {'public_mode':public_mode,'max_datasets':max_datasets,'backup_retention_days':14 if public_mode else None}
    @app.post('/api/auth/{action}')
    def auth(action:str,body:Credentials,request:Request,response:Response):
        if action not in ('register','login'):raise HTTPException(404)
        name=body.name.lower()
        rate_limit('ip:'+request.client.host,50,600)
        rate_limit('account:'+name,12,900)
        if action=='register':rate_limit('register:'+request.client.host,5,3600)
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if action=='register':
                if db.execute('SELECT COUNT(*) FROM users').fetchone()[0]>=max_users:raise HTTPException(403,'当前研究试用名额已满。')
                owner=uid();p=uid()
                try:db.execute('INSERT INTO users VALUES(?,?,?)',(owner,name,password_hash(body.password)))
                except sqlite3.IntegrityError:raise HTTPException(409,'用户名已被使用。')
                db.execute('INSERT INTO policies VALUES(?,?,?,?,?)',(p,owner,None,store.pack({'aliases':BASE,'blocked':[],'reason':'预置来源字段映射'}),now()))
                db.execute('INSERT INTO active VALUES(?,?)',(owner,p))
            else:
                row=db.execute('SELECT * FROM users WHERE name=?',(name,)).fetchone()
                salt=row['password'].split(':')[0] if row else 'dummy'
                hashed=password_hash(body.password,salt)
                if not row or not hmac.compare_digest(hashed,row['password']):raise HTTPException(401,'用户名或密码不正确。')
                owner=row['id']
            token=secrets.token_urlsafe(32);csrf=secrets.token_urlsafe(32)
            db.execute('DELETE FROM sessions WHERE expires<?',(time.time(),))
            db.execute('INSERT INTO sessions VALUES(?,?,?,?)',(digest(token),owner,csrf,time.time()+8*3600))
        response.set_cookie('emeris_session',token,httponly=True,samesite='strict',max_age=8*3600,secure=public_mode)
        return {'name':name,'csrf':csrf}
    @app.post('/api/logout')
    def logout(response:Response,u=Depends(user)):
        with store.connect() as db:db.execute('DELETE FROM sessions WHERE token=?',(u['token'],))
        response.delete_cookie('emeris_session',secure=public_mode,httponly=True,samesite='strict');return {'ok':True}
    @app.delete('/api/account')
    def delete_account(response:Response,u=Depends(user)):
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            # Immutable prediction records need an explicit retention/deletion resolution.
            exists=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='platform_prediction_runs'").fetchone()
            if exists and (db.execute('SELECT 1 FROM platform_prediction_runs WHERE owner=? LIMIT 1',(u['owner'],)).fetchone() or db.execute('SELECT 1 FROM platform_actual_outcomes WHERE submitted_by=? LIMIT 1',(u['owner'],)).fetchone() or db.execute('SELECT 1 FROM platform_outcome_verifications WHERE reviewer=? LIMIT 1',(u['owner'],)).fetchone()):
                raise HTTPException(409,'账号包含不可覆盖的预测或核验记录，当前不支持自动删除这些记录。账号和资料均未删除；请先联系管理员处理保留与删除要求。')
            for table in ('sessions','datasets','policies','active','confirmations','events','research_runs'):
                db.execute(f'DELETE FROM {table} WHERE owner=?',(u['owner'],))
            db.execute('DELETE FROM users WHERE id=?',(u['owner'],))
        response.delete_cookie('emeris_session',secure=public_mode,httponly=True,samesite='strict')
        return {'deleted':True,'backup_retention_days':14 if public_mode else None}
    @app.get('/api/state')
    def state(u=Depends(user)):
        with store.connect() as db:
            p=active(db,u['owner'])
            data=[visible(dict(r)|{'content':store.unpack(r['payload'])}) for r in db.execute('SELECT * FROM datasets WHERE owner=? ORDER BY created DESC',(u['owner'],))]
            events=[{'kind':r['kind'],'created':r['created'],'payload':store.unpack(r['payload'])} for r in db.execute('SELECT * FROM events WHERE owner=? ORDER BY id DESC LIMIT 30',(u['owner'],))]
        return {'name':u['name'],'csrf':u['csrf'],'datasets':data,'policy':{'id':p['id'],'parent':p['parent'],**p['content']},'events':events,'fields':FIELDS}
    @app.post('/api/public/{disease}')
    def public(disease:str,u=Depends(user)):
        studies={'crc':'coadread_tcga_pan_can_atlas_2018','gastric':'stad_tcga_pan_can_atlas_2018'}
        if disease not in studies:raise HTTPException(404)
        filename=studies[disease]+'__data_clinical_patient.txt'
        manifest=json.loads((ROOT/'public_data/manifest.json').read_text())
        entry=next(x for x in manifest['files'] if x['path']==filename)
        raw=(ROOT/'public_data'/filename).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=entry['sha256']:raise ValueError('公共来源校验失败。')
        reader=csv.DictReader((l for l in raw.decode().splitlines() if not l.startswith('#')),delimiter='\t')
        output=io.StringIO();fields=[h for h in reader.fieldnames if h in BASE]
        writer=csv.DictWriter(output,fieldnames=fields);writer.writeheader()
        for row in reader:writer.writerow({h:row[h] for h in fields})
        return save_dataset(u['owner'],output.getvalue().encode(), 'TCGA 结直肠癌' if disease=='crc' else 'TCGA 胃癌',disease,'public_real',False,entry['url'],entry['sha256'])
    @app.post('/api/upload')
    async def upload(file:UploadFile=File(...),disease:str=Form(...),authorized:bool=Form(False),learning:bool=Form(False),u=Depends(user)):
        if not authorized:raise HTTPException(422,'需要确认数据已去标识化且有权用于本项目。')
        if disease not in ('crc','gastric'):raise HTTPException(422,'请明确癌种。')
        raw=await file.read(5*1024*1024+1)
        if len(raw)>5*1024*1024:raise HTTPException(413,'文件超过 5 MiB。')
        name=(file.filename or '研究数据')[:120]
        return save_dataset(u['owner'],raw,name,disease,'user_authorized_deidentified',learning)
    @app.get('/api/datasets/{id}')
    def detail(id:str,u=Depends(user)):
        with store.connect() as db:return visible(dataset(db,u['owner'],id),True)
    @app.post('/api/datasets/{id}/mapping')
    def mapping(id:str,body:Mapping,u=Depends(user)):
        if not body.confirmed_semantics:raise ValueError('请核实字段含义及单位后确认。')
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            p=active(db,u['owner']);row=dataset(db,u['owner'],id);v=row['content']
            if p['id']!=body.expected_policy:raise HTTPException(409,'策略已变化，请刷新后再确认。')
            summary=summarize(v['headers'],v['rows'],body.mapping)
            v['mapping']=body.mapping;v['summary']=summary
            db.execute('UPDATE datasets SET payload=? WHERE id=?',(store.pack(v),id))
            promoted=[];pending=[];aliases=dict(p['content']['aliases']);blocked=p['content'].get('blocked',[])
            if body.learn:
                if not v['learning_enabled']:raise ValueError('导入时未授权映射学习；本次未保存更改。')
                if summary['issues']:raise ValueError('请先解决编号或数值异常，再使用此数据验证映射。')
                for source,target in body.mapping.items():
                    alias=source.upper()
                    if alias in aliases and aliases[alias]!=target:raise ValueError('映射与现有规则冲突，须先回滚或重新命名来源字段。')
                    if alias in blocked:raise ValueError('该字段的学习规则已被回滚隔离，请采用手动映射。')
                    if alias in aliases or target=='subject_id':continue
                    if summary['nonmissing'].get(target,0)<10:continue
                    # One file is one confirmation, regardless of repeated clicks.
                    previous=db.execute('SELECT target FROM confirmations WHERE owner=? AND dataset=? AND source=?',(u['owner'],id,alias)).fetchone()
                    if previous and previous[0]!=target:raise ValueError('该文件已有相反确认；不能通过重复修改累积证据。')
                    db.execute('INSERT OR IGNORE INTO confirmations VALUES(?,?,?,?)',(u['owner'],id,alias,target))
                    rows=db.execute('SELECT dataset,target FROM confirmations WHERE owner=? AND source=?',(u['owner'],alias)).fetchall()
                    targets={r['target'] for r in rows}
                    if targets!={target}:pending.append({'source':source,'reason':'跨文件含义冲突'});continue
                    # Check actual identity independence, not just distinct filenames/hashes.
                    populations=[]
                    for r in rows:
                        d=dataset(db,u['owner'],r['dataset'])['content']
                        if r['dataset']==id:d=v
                        actual=next((k for k in d['headers'] if k.upper()==alias),None)
                        if actual is None or d['mapping'].get(actual)!=target:continue
                        audit=summarize(d['headers'],d['rows'],d['mapping'])
                        if audit['issues'] or audit['nonmissing'].get(target,0)<10:continue
                        identity=next(k for k,t in d['mapping'].items() if t=='subject_id')
                        populations.append({x[identity].strip() for x in d['rows']})
                    independent=any(not a.intersection(b) for i,a in enumerate(populations) for b in populations[i+1:])
                    if independent:
                        aliases[alias]=target;promoted.append({'source':source,'target':target,'datasets':[r['dataset'] for r in rows]})
                    else:pending.append({'source':source,'reason':'需要另一份研究编号不重叠的数据再次确认'})
            if promoted:
                next_id=uid()
                policy={'aliases':aliases,'blocked':blocked,'reason':'两份编号不重叠的数据经用户确认；类型检查及旧规则无回退','evidence':promoted,'evaluation_scope':'schema_compatibility_and_user_attestation; not_independent_semantic_validation'}
                db.execute('INSERT INTO policies VALUES(?,?,?,?,?)',(next_id,u['owner'],p['id'],store.pack(policy),now()))
                db.execute('UPDATE active SET policy=? WHERE owner=?',(next_id,u['owner']))
                event(db,u['owner'],'policy_promoted',{'policy_id':next_id,'previous':p['id'],'evidence':promoted,'weight_training':False})
            event(db,u['owner'],'mapping_confirmed',{'dataset_id':id,'learning_requested':body.learn,'pending':pending,'promoted':promoted})
        return {'summary':summary,'promoted':promoted,'pending':pending}
    @app.post('/api/policy/rollback')
    def rollback(body:Rollback,u=Depends(user)):
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE');p=active(db,u['owner'])
            if body.expected_policy!=p['id']:raise HTTPException(409,'策略已变化，请刷新。')
            if not p['parent']:raise ValueError('当前为初始规则。')
            parent=db.execute('SELECT * FROM policies WHERE id=? AND owner=?',(p['parent'],u['owner'])).fetchone()
            prior=store.unpack(parent['payload']);removed=set(p['content']['aliases'])-set(prior['aliases'])
            if not removed:raise ValueError('没有可回退的新规则。')
            prior['blocked']=sorted(set(prior.get('blocked',[]))|set(p['content'].get('blocked',[]))|removed)
            prior['reason']='回滚并隔离已撤回规则';next_id=uid()
            db.execute('INSERT INTO policies VALUES(?,?,?,?,?)',(next_id,u['owner'],parent['parent'],store.pack(prior),now()))
            db.execute('UPDATE active SET policy=? WHERE owner=?',(next_id,u['owner']))
            event(db,u['owner'],'policy_rolled_back',{'previous':p['id'],'policy_id':next_id,'quarantined':sorted(removed)})
        return {'ok':True}
    @app.get('/api/datasets/{id}/report')
    def report(id:str,u=Depends(user)):
        with store.connect() as db:value=visible(dataset(db,u['owner'],id))
        return Response(json.dumps(value,ensure_ascii=False,indent=2),media_type='application/json',headers={'Content-Disposition':'attachment; filename="emeris-research-report.json"'})
    @app.get('/')
    def index():return FileResponse(ROOT/'static/arena-public.html')
    @app.get('/arena/lab/calendar')
    def internal_calendar(request:Request):
        try:lab_user(request)
        except HTTPException as exc:
            if exc.status_code in (401,403):return access_shell(exc.status_code)
            raise
        return FileResponse(ROOT/'static/dashboard.html')
    @app.get('/arena')
    def arena_page():return FileResponse(ROOT/'static/arena-public.html')
    @app.get('/track-record')
    def track_record_page():return FileResponse(ROOT/'static/track-record.html')
    @app.get('/reviews')
    def reviews_page():return FileResponse(ROOT/'static/reviews.html')
    @app.get('/analytics')
    def analytics_page():return FileResponse(ROOT/'static/analytics.html')
    @app.get('/trial/{nct_id}')
    def single_trial_page(nct_id:str):
        if not re.fullmatch(r'NCT[0-9]{8}',nct_id):raise HTTPException(404,'Invalid trial id')
        return FileResponse(ROOT/'static/trial-detail.html')
    @app.get('/api/product/dashboard')
    def product_dashboard():
        try:return build_product(store)
        except (OSError,ValueError,TypeError,KeyError):raise HTTPException(503,'Product evidence unavailable or failed integrity verification') from None
    @app.get('/api/product/export')
    def product_export():
        return JSONResponse(product_dashboard(),headers={'Content-Disposition':'attachment; filename=predome-1.1.8-public-evidence.json'})
    @app.get('/api/product/method')
    def product_method():
        return FileResponse(ROOT/'docs/upgrade/产品交付与持续迭代规范.md',media_type='text/markdown; charset=utf-8',filename='Predome_product_and_prompt_contract_1.1.8.md')
    @app.get('/api/product/trials/{nct_id}')
    def product_trial(nct_id:str):
        if not re.fullmatch(r'NCT[0-9]{8}',nct_id):raise HTTPException(404,'Invalid trial id')
        result=trial_detail(nct_id,store)
        if result is None:raise HTTPException(404,'Trial not yet archived')
        return result
    @app.get('/api/analytics')
    def product_analytics(scope:str='prospective',model:str='deepseek-v4-pro',rolling:int=Query(default=5,ge=2,le=100)):
        try:return build_analytics(scope=scope,model=model,rolling=rolling,prospective_records=evaluation_rows(store,public_only=True))
        except (OSError,KeyError):raise HTTPException(503,'Verified analytics source unavailable') from None
    @app.get('/data')
    def data_page():return FileResponse(ROOT/'static/index.html')
    def verified_dashboard():
        try:return build_dashboard()
        except (OSError,ValueError,TypeError,KeyError):
            raise HTTPException(status_code=503,detail='Dashboard source integrity verification failed') from None
    @app.get('/api/dashboard')
    def dashboard():return verified_dashboard()
    @app.get('/api/dashboard/export')
    def dashboard_export():return JSONResponse(verified_dashboard(),headers={'Content-Disposition':'attachment; filename=predome-v1-evidence-dashboard.json'})
    @app.get('/api/dashboard/method')
    def dashboard_method():return FileResponse(ROOT/'docs/Predome_v1_产品与提示词规范.md',media_type='text/markdown; charset=utf-8',filename='Predome_v1_product_and_prompt_contract.md')
    @app.get('/research')
    def research_page():return FileResponse(ROOT/'static/research.html')
    @app.get('/predome')
    def predome_page():return FileResponse(ROOT/'static/predome.html')
    @app.get('/api/predome/research')
    def predome_research():return read_release()
    @app.get('/api/predome/export')
    def predome_export():return JSONResponse(read_release(),headers={'Content-Disposition':'attachment; filename=predome-research-record.json'})
    @app.get('/api/predome/report')
    def predome_report():return FileResponse(ROOT/'docs/Predome_首批预测结果_20261016.md',media_type='text/markdown; charset=utf-8',filename='Predome_research_report.md')
    def verified_expanded():
        try:
            return read_expanded()
        except (OSError, ValueError, TypeError, KeyError):
            raise HTTPException(status_code=503, detail='Expanded research release unavailable or integrity check failed') from None
    def verified_continuous():
        try:return read_continuous()
        except (OSError,ValueError,TypeError,KeyError):raise HTTPException(status_code=503,detail='Continuous research release unavailable or integrity check failed') from None
    @app.get('/api/predome/continuous')
    def predome_continuous():return verified_continuous()
    @app.get('/api/predome/continuous/export')
    def predome_continuous_export():return JSONResponse(verified_continuous(),headers={'Content-Disposition':'attachment; filename=predome-continuous-cohort-and-forecasts.json'})
    @app.get('/api/predome/continuous/report')
    def predome_continuous_report():
        envelope=verified_continuous()
        try:report=continuous_report_path(envelope)
        except (OSError,ValueError,TypeError,KeyError):raise HTTPException(status_code=503,detail='Verified continuous report unavailable') from None
        return FileResponse(report,media_type='text/markdown; charset=utf-8',filename='Predome_continuous_backtest_and_forecast.md')
    @app.get('/api/predome/continuous/optimization')
    def predome_continuous_optimization():
        envelope=verified_continuous()
        try:report=optimization_path(envelope)
        except (OSError,ValueError,TypeError,KeyError):raise HTTPException(status_code=503,detail='Verified optimization report unavailable') from None
        return FileResponse(report,media_type='text/markdown; charset=utf-8',filename='Predome_70percent_target_research_plan.md')
    @app.get('/api/predome/expanded')
    def predome_expanded():return verified_expanded()
    @app.get('/api/predome/expanded/export')
    def predome_expanded_export():return JSONResponse(verified_expanded(),headers={'Content-Disposition':'attachment; filename=predome-expanded-development-replay.json'})
    @app.get('/api/predome/expanded/report')
    def predome_expanded_report():
        envelope=verified_expanded()
        try:
            report=expanded_report_path(envelope)
        except (OSError, ValueError, TypeError, KeyError):
            raise HTTPException(status_code=503, detail='Verified expanded report unavailable') from None
        return FileResponse(report,media_type='text/markdown; charset=utf-8',filename='Predome_expanded_development_report.md')
    @app.get('/api/predome/historical')
    def predome_historical():return read_historical()
    @app.get('/api/predome/historical/export')
    def predome_historical_export():return JSONResponse(read_historical(),headers={'Content-Disposition':'attachment; filename=predome-historical-benchmark.json'})
    @app.get('/api/predome/historical/report')
    def predome_historical_report():return FileResponse(ROOT/'docs/Predome_历史训练与验证_20261017.md',media_type='text/markdown; charset=utf-8',filename='Predome_historical_benchmark.md')
    @app.get('/guide')
    def guide_page():return FileResponse(ROOT/'static/guide.html')
    @app.get('/trials')
    def trial_page():return FileResponse(ROOT/'static/trials.html')
    @app.get('/api/trials/catalog')
    def trial_catalog():
        raw=(ROOT/'trial_data/candidates.json').read_bytes()
        value=json.loads(raw)
        return {'catalog':value,'source_sha256':hashlib.sha256(raw).hexdigest(),
                'snapshot_only':True,'clinical_prediction_validated':False,
                'timestamp_attestation':'release_snapshot_only_not_independent_timestamp',
                'note':'有日期的公开证据快照；未读出结果仍待核实。情景计算不自动成为该药的预测。'}
    @app.post('/api/trials/execute')
    def trial_execute(body:TrialRequest,request:Request):
        rate_limit('trial:'+request.client.host,60,600)
        return execute_trial(body)
    @app.get('/api/forecasts')
    def forecasts():return read_registry(ROOT/'trial_data/forecasts')
    @app.get('/api/forecasts/{forecast_id}')
    def forecast(forecast_id:str):
        registry=read_registry(ROOT/'trial_data/forecasts')
        record=next((r for r in registry['records'] if r['record']['id']==forecast_id),None)
        if record is None:raise HTTPException(404,'未找到该预测版本。')
        return record
    @app.post('/api/research/execute')
    def research_execute(body:ResearchRequest,request:Request):
        rate_limit('research:'+request.client.host,60,600)
        return execute(body)
    @app.post('/api/research/runs')
    def save_research(body:ResearchRequest,u=Depends(user)):
        rate_limit('research-save:'+u['owner'],20,600)
        result=execute(body);id=uid();created=now()
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT COUNT(*) FROM research_runs WHERE owner=?',(u['owner'],)).fetchone()[0]>=30:
                raise HTTPException(409,'已保存 30 次运行，请删除不需要的记录后再保存。')
            db.execute('INSERT INTO research_runs VALUES(?,?,?,?)',(id,u['owner'],store.pack(result),created))
        return {'id':id,'created':created,'result':result}
    @app.get('/api/research/runs')
    def list_research(u=Depends(user)):
        with store.connect() as db:
            rows=db.execute('SELECT * FROM research_runs WHERE owner=? ORDER BY created DESC',(u['owner'],)).fetchall()
        return [{'id':r['id'],'created':r['created'],'title':store.unpack(r['payload'])['title']} for r in rows]
    @app.get('/api/research/runs/{id}')
    def get_research(id:str,u=Depends(user)):
        with store.connect() as db:
            row=db.execute('SELECT * FROM research_runs WHERE id=? AND owner=?',(id,u['owner'])).fetchone()
        if not row:raise HTTPException(404,'未找到有权访问的运行记录。')
        return {'id':row['id'],'created':row['created'],'result':store.unpack(row['payload'])}
    @app.delete('/api/research/runs/{id}')
    def delete_research(id:str,u=Depends(user)):
        with store.connect() as db:
            changed=db.execute('DELETE FROM research_runs WHERE id=? AND owner=?',(id,u['owner'])).rowcount
        if not changed:raise HTTPException(404,'未找到有权访问的运行记录。')
        return {'deleted':True}
    app.state.platform_user=user
    app.state.platform_rate_limit=rate_limit
    app.include_router(platform_router)
    app.include_router(predict_router)
    app.include_router(intake_router)
    app.include_router(readout_updates_router)
    app.include_router(arena_router)
    app.include_router(arena_lab_router)
    app.include_router(arena_public_router)
    app.mount('/static',StaticFiles(directory=ROOT/'static'),name='static')
    return app
