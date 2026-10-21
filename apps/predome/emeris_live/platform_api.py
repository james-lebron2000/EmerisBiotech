"""Ownership-aware HTTP interface for the immutable clinical research ledger."""
import os
from itertools import islice
from typing import Any
from fastapi import APIRouter, HTTPException, Request, Query
from pydantic import BaseModel, ConfigDict, Field
from .platform_store import PlatformStore

router=APIRouter(prefix='/api/platform',tags=['prediction ledger'])


class Draft(BaseModel):
    model_config=ConfigDict(extra='forbid')
    input_snapshot:dict[str,Any]
    outputs:dict[str,Any]
    model_version:str=Field(min_length=1,max_length=160)
    pipeline_version:str=Field(min_length=1,max_length=160)
    data_cutoff:str=Field(max_length=50)
    evidence:list[dict[str,Any]]=Field(default_factory=list,max_length=100)
    target:dict[str,Any]
    parent_prediction_id:str|None=None
    training_data_cutoff:str|None=None


class EvaluationClassification(BaseModel):
    model_config=ConfigDict(extra='forbid')
    therapeutic_area:str='Unknown'
    sponsor_category:str='unknown'


class Lock(BaseModel):
    model_config=ConfigDict(extra='forbid')
    publish:bool=False
    confirm_public:bool=False
    publish_evaluation_metadata:bool=False
    confirm_evaluation_metadata:bool=False
    evaluation_classification:EvaluationClassification|None=None


class Outcome(BaseModel):
    model_config=ConfigDict(extra='forbid')
    snapshot_id:str=Field(max_length=80)
    outcome:dict[str,Any]


class Review(BaseModel):
    model_config=ConfigDict(extra='forbid')
    decision:str
    reason:str=Field(min_length=10,max_length=5000)
    confirmations:dict[str,bool]=Field(default_factory=dict)


def ledger(request):return PlatformStore(request.app.state.store)


def actor(request):return request.app.state.platform_user(request)


def is_reviewer(user):
    allowed={name.strip() for name in os.environ.get('EMERIS_REVIEWERS','').split(',') if name.strip()}
    return user.get('name') in allowed


def reviewer(request):
    value=actor(request)
    if not is_reviewer(value):raise HTTPException(403,'结果核验仅对已配置的独立审核账号开放。')
    return value


def invoke(function,*args,**kwargs):
    try:return function(*args,**kwargs)
    except KeyError:raise HTTPException(404,'未找到有权访问的记录。') from None
    except ValueError as exc:raise HTTPException(422,str(exc)) from None


def page_response(records, limit, offset):
    has_more=len(records)>limit
    return {'records':records[:limit],
            'pagination':{'limit':limit,'offset':offset,'has_more':has_more,
                          'next_offset':offset+limit if has_more else None}}


@router.get('/capabilities')
def capabilities(request:Request):
    try:user=actor(request)
    except HTTPException:user=None
    return {'authenticated':bool(user),'reviewer':bool(user and is_reviewer(user)),
            'reviewer_configured':bool(os.environ.get('EMERIS_REVIEWERS','').strip()),
            'immutable_snapshots':True,'default_visibility':'private','independent_timestamp':False,
            'publish_scope':'confirmed_target_probability_source_summary including recorded ensemble probability, weights and provenance hashes; optional separately consented evaluation metadata; uploaded protocol stays private'}


@router.get('/predictions')
def predictions(request:Request,limit:int=Query(20,ge=1,le=100),offset:int=Query(0,ge=0)):
    user=actor(request)
    return page_response(ledger(request).list_runs(user['owner'],limit=limit+1,offset=offset),limit,offset)


@router.post('/predictions')
def create_prediction(body:Draft,request:Request):
    user=actor(request)
    if hasattr(request.app.state,'platform_rate_limit'):
        request.app.state.platform_rate_limit('platform-draft:'+user['owner'],20,600)
    data=body.model_dump()
    # This route stores user-entered research estimates. Only the server-side
    # engine adapter can label a run as a provider-generated forecast.
    data['outputs']=dict(data['outputs'])|{'forecast_kind':'user_submitted','clinical_prediction_validated':False}
    return invoke(ledger(request).create_run,user['owner'],**data)


@router.get('/predictions/{prediction_id}')
def prediction(prediction_id:str,request:Request):
    user=actor(request)
    return invoke(ledger(request).get_run,user['owner'],prediction_id)


@router.get('/predictions/{prediction_id}/evaluation-metadata')
def evaluation_metadata_preview(prediction_id:str,request:Request,therapeutic_area:str='Unknown',sponsor_category:str='unknown'):
    user=actor(request)
    return invoke(ledger(request).evaluation_metadata_preview,user['owner'],prediction_id,
                  {'therapeutic_area':therapeutic_area,'sponsor_category':sponsor_category})


@router.post('/predictions/{prediction_id}/lock')
def lock(prediction_id:str,body:Lock,request:Request):
    user=actor(request)
    if body.publish and not body.confirm_public:
        raise HTTPException(422,'请明确确认公开试验标识、目标、单模型及已记录的集成概率、权重和来源摘要；原始上传方案保持私有。')
    return invoke(ledger(request).lock,user['owner'],prediction_id,publish=body.publish,confirm_public=body.confirm_public,
                  publish_evaluation_metadata=body.publish_evaluation_metadata,confirm_evaluation_metadata=body.confirm_evaluation_metadata,
                  evaluation_classification=body.evaluation_classification.model_dump() if body.evaluation_classification else None)


@router.get('/snapshots')
def snapshots(request:Request,limit:int=Query(20,ge=1,le=100),offset:int=Query(0,ge=0)):
    return page_response(ledger(request).list_public_snapshots(limit=limit+1,offset=offset),limit,offset) | {'private_inputs_excluded':True}


@router.get('/snapshots/{snapshot_id}')
def snapshot(snapshot_id:str,request:Request):
    try:user=actor(request)
    except HTTPException:user=None
    return invoke(ledger(request).get_snapshot,snapshot_id,owner=user['owner'] if user else None,
                  reviewer=bool(user and is_reviewer(user)))


@router.post('/outcomes')
def outcome(body:Outcome,request:Request):
    user=actor(request)
    source=body.outcome.get('source')
    allowed={'url','published_at','sha256','title','retrieved_at'}
    if not isinstance(source,dict) or set(source)-allowed:
        raise HTTPException(422,'手工结果来源只接受URL、公开日期、SHA、标题和抓取日期；机器核验摘录必须通过来源候选关联。')
    body.outcome=dict(body.outcome)
    body.outcome['source']=dict(source)|{'provenance':'manual_source_reference_not_machine_verified'}
    return invoke(ledger(request).submit_outcome,user['owner'],body.snapshot_id,body.outcome,allow_other_owner=is_reviewer(user))


@router.get('/outcomes')
def outcomes(request:Request,limit:int=Query(20,ge=1,le=100),offset:int=Query(0,ge=0)):
    user=actor(request)
    return page_response(ledger(request).list_outcomes(user['owner'],limit=limit+1,offset=offset),limit,offset)


@router.get('/reviews')
def reviews(request:Request,limit:int=Query(20,ge=1,le=100),offset:int=Query(0,ge=0)):
    reviewer(request)
    return page_response(ledger(request).list_outcomes(reviewer=True,limit=limit+1,offset=offset),limit,offset) | {
            'approval_requirements':['independent_reviewer','exact_trial_endpoint_population_comparison','primary_source','prespecified_analysis','resolved_conflicts']}


@router.post('/reviews/{outcome_id}')
def review(outcome_id:str,body:Review,request:Request):
    user=reviewer(request)
    return invoke(ledger(request).verify_outcome,user['owner'],outcome_id,body.decision,body.reason,body.confirmations)


@router.get('/evaluations')
def evaluations(request:Request,limit:int=Query(20,ge=1,le=100),offset:int=Query(0,ge=0)):
    allowed=('snapshot_id','prediction_id','trial_id','endpoint','model','model_version','probability','actual_label','scope','eligible_for_prospective','prospective_eligible','locked_at','readout_at','verified_at','verification_status','source_url','source_sha256','primary_prediction_id','snapshot_sha256','baseline_probabilities','baseline_audits','prediction_status','coverage_eligible','score_eligible','selection_policy')
    rows=[{k:row.get(k) for k in allowed} for row in islice(ledger(request).iter_evaluation_rows(public_only=True),offset,offset+limit+1)]
    return page_response(rows,limit,offset) | {'scope':'only_explicitly_published_snapshots',
            'clinical_prediction_validated':False}


@router.get('/public-outcomes')
def public_outcomes(request:Request,limit:int=Query(20,ge=1,le=100),offset:int=Query(0,ge=0)):
    return page_response(ledger(request).public_outcome_records(limit=limit+1,offset=offset),limit,offset) | {'scope':'approved_outcomes_of_explicitly_public_snapshots'}


@router.get('/baselines')
def baselines(request:Request,limit:int=Query(20,ge=1,le=100),offset:int=Query(0,ge=0)):
    from .prospective_baselines import list_baselines,summary
    store=ledger(request).store
    rows=invoke(list_baselines,store,limit=limit+1,offset=offset)
    return page_response(rows,limit,offset) | {'summary':invoke(summary,store),
        'scope':'public_snapshot_targets_not_model_rows','policy_note':'Feature-free 50% reference; not a fitted historical base rate.'}


@router.get('/baselines/{baseline_id}')
def baseline(baseline_id:str,request:Request):
    from .prospective_baselines import get_baseline
    return invoke(get_baseline,ledger(request).store,baseline_id)
