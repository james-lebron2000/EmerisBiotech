"""Authenticated structured intake and execution routes; no anonymous paid runs."""
from pathlib import Path
from datetime import datetime,timezone
import json
import secrets
import os
import httpx
from fastapi import APIRouter, HTTPException, Request, UploadFile, File, Query
from fastapi.responses import FileResponse
from .prediction_service import (PredictionRequest, TrialProfile, retrieve_registry,
    parse_document, capabilities, create_report, PIPELINE_VERSION, FIELD_NAMES, retrieve_comparables)
from .platform_store import PlatformStore

ROOT=Path(__file__).resolve().parents[1]
router=APIRouter()

def actor(request):return request.app.state.platform_user(request)
def limit(request,bucket,count,seconds):request.app.state.platform_rate_limit(bucket,count,seconds)

def receipt_for(request,result):
    value={'kind':'predome.intake.receipt.v1','fields':result['fields'],'source':result.get('source'),'document':result.get('document'),'has_results':result.get('has_results'),'created_at':datetime.now(timezone.utc).isoformat()}
    return request.app.state.store.pack(value).decode()


def normalize_profile(request,profile):
    """Public/inferred provenance must match a server-issued import receipt.

Any user change becomes a user declaration; it cannot impersonate the registry.
Imported document suggestions remain inferred until user confirmation.
"""
    original={}
    if profile.import_receipt:
        try:
            receipt=request.app.state.store.unpack(profile.import_receipt.encode())
            if receipt.get('kind')!='predome.intake.receipt.v1':raise ValueError()
            original=receipt['fields']
        except Exception:raise HTTPException(422,'导入凭据无效，请重新导入或将字段标记为用户填写。') from None
    fields={}
    for name,field in profile.fields.items():
        value=field.model_dump()
        if not (value.get('value') or '').strip():
            value.update(value=None,origin='missing',source_url=None,source_id=None,available_at=None,confirmed=False)
        elif value['origin'] in ('public','inferred'):
            known=original.get(name)
            keys=('value','origin','source_url','source_id','available_at')
            if not known or any(value[k]!=known.get(k) for k in keys):
                value.update(origin='user',source_url=None,source_id=None,available_at=None)
        else:
            value['origin']='user'
        fields[name]=value
    return TrialProfile(fields=fields)


@router.get('/predict')
def predict_page():return FileResponse(ROOT/'static/predict.html')

@router.get('/api/predict/capabilities')
def predict_capabilities():return capabilities()

@router.get('/api/predict/registry/{nct}')
def registry(nct:str,request:Request):
    limit(request,'registry-intake:'+request.client.host,30,600)
    try:result=retrieve_registry(nct.strip().upper())
    except ValueError as exc:raise HTTPException(422,str(exc)) from None
    except httpx.HTTPStatusError as exc:
        raise HTTPException(404 if exc.response.status_code==404 else 502,'登记数据库未返回可用记录，请核对编号或稍后重试。') from None
    except (httpx.HTTPError,KeyError,TypeError):raise HTTPException(502,'登记数据库暂时无法访问，没有填入替代数据。') from None
    result['import_receipt']=receipt_for(request,result)
    return result

@router.get('/api/predict/comparables')
def comparables(request:Request,condition:str=Query(min_length=3,max_length=180),exclude_nct:str|None=None):
    limit(request,'comparable-intake:'+request.client.host,20,600)
    try:result=retrieve_comparables(condition,exclude_nct)
    except ValueError as exc:raise HTTPException(422,str(exc)) from None
    except (httpx.HTTPError,KeyError,TypeError):raise HTTPException(502,'相似登记检索未完成，请稍后重试。') from None
    # Bind new receipts to the explicitly excluded current trial, when supplied.
    result['query_context']={'exclude_nct':exclude_nct}
    result['receipt']=request.app.state.store.pack(result).decode()
    return result

@router.post('/api/predict/parse')
def parse(request:Request,file:UploadFile=File(...)):
    user=actor(request);limit(request,'protocol-parse:'+user['owner'],12,600)
    content=file.file.read(5*1024*1024+1)
    try:result=parse_document(content,file.filename or '')
    except ImportError:raise HTTPException(503,'当前服务缺少PDF解析依赖，请使用DOCX或手动填写。') from None
    except (ValueError,KeyError) as exc:raise HTTPException(422,str(exc)) from None
    except Exception:raise HTTPException(422,'文档无法安全解析。请使用有效的文本型PDF/DOCX或手动填写。') from None
    result['import_receipt']=receipt_for(request,result)
    return result

@router.post('/api/predict/run')
def run(body:PredictionRequest,request:Request):
    user=actor(request);limit(request,'predict-run:'+user['owner'],12,600)
    body.profile=normalize_profile(request,body.profile)
    related=None
    if body.comparable_receipt:
        try:
            related=request.app.state.store.unpack(body.comparable_receipt.encode())
            if related.get('schema_version')!='predome.comparables.v1':raise ValueError()
            if not isinstance(related.get('records'),list) or not isinstance(related.get('selection'),str):raise ValueError()
            bound=related.get('query_context',{}).get('exclude_nct')
            current=body.profile.fields.get('nct_id')
            if bound and bound!=(current.value if current else None):raise ValueError()
        except Exception:raise HTTPException(422,'相似登记检索凭据无效，请重新查询。') from None
    if body.parent_prediction_id:
        try:PlatformStore(request.app.state.store).get_run(user['owner'],body.parent_prediction_id)
        except KeyError:raise HTTPException(404,'无法访问父版本，请从当前账号的报告创建新版本。') from None
    if body.method=='llm' and capabilities()['llm_available']:
        # Persistent quotas reserve provider calls before invocation, even on failure.
        daily_max=max(1,min(int(os.environ.get('EMERIS_USER_LLM_DAILY_CALL_LIMIT','20')),100))
        for _ in body.models:
            limit(request,'predict-paid-global',daily_max,86400)
            limit(request,'predict-paid-user:'+user['owner'],4,86400)
    try:report=create_report(body)
    except ValueError as exc:raise HTTPException(422,str(exc)) from None
    if related is not None:
        report['comparable_trials']=related['records']
        report['comparable_trials_status']=related['selection']
        report['comparable_source_receipt']={k:v for k,v in related.items() if k!='records'}
    evidence=[e.model_dump(mode='json') for e in body.evidence]
    # Field-level public evidence is included in the immutable cutoff audit.
    seen=set()
    for field in body.profile.fields.values():
        if not field.value or not field.source_id or field.source_id in seen:continue
        seen.add(field.source_id)
        evidence.append({'id':field.source_id,'url':field.source_url,'availability_date':field.available_at,'origin':field.origin})
    models=','.join(body.models) if body.method=='llm' else ('triallab-0.1.0' if body.method=='scenario' else 'evidence-review-no-prediction')
    try:
        stored=PlatformStore(request.app.state.store).create_run(owner=user['owner'],input_snapshot={'nct_id':body.profile.fields.get('nct_id').value if body.profile.fields.get('nct_id') else None,'trial_name':body.profile.fields.get('trial_name').value if body.profile.fields.get('trial_name') else None,'profile':report['profile'],'target':body.target.model_dump(),'method':body.method,'scenario':body.scenario.model_dump() if body.scenario else None},outputs=report,model_version=models,pipeline_version=PIPELINE_VERSION,data_cutoff=str(body.data_cutoff),evidence=evidence,target=body.target.model_dump(),parent_prediction_id=body.parent_prediction_id,training_data_cutoff=None)
    except ValueError as exc:raise HTTPException(422,str(exc)) from None
    except KeyError:raise HTTPException(404,'无法访问父版本，请从当前账号的报告创建新版本。') from None
    return {'report':report,'prediction_id':stored['id'],'status':'draft','private':True,'notice':'已保存在当前账号。锁定前仍为草稿；修改后重新运行会创建新版本。'}
