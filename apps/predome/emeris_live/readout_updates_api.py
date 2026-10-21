"""Reviewer-only readout calendar proposals and independent approvals."""
from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal
from .platform_api import reviewer, invoke, page_response
from .readout_updates import ReadoutUpdateStore, CONFIRMATIONS

router=APIRouter(prefix='/api/readout-updates',tags=['readout calendar review'])


class Proposal(BaseModel):
    model_config=ConfigDict(extra='forbid')
    nct_id:str=Field(pattern=r'^NCT\d{8}$')
    candidate_id:str=Field(min_length=1,max_length=100)
    date_kind:Literal['official_exact_date','estimated_window']
    start:str=Field(max_length=10)
    end:str=Field(max_length=10)
    precision:Literal['day','month','quarter','half_year','year','range']
    label:str=Field(min_length=1,max_length=160)
    quoted_evidence:str=Field(min_length=20,max_length=6000)
    reason:str=Field(min_length=10,max_length=3000)


class Decision(BaseModel):
    model_config=ConfigDict(extra='forbid')
    decision:Literal['approved','rejected']
    reason:str=Field(min_length=10,max_length=3000)
    confirmations:dict[str,bool]=Field(default_factory=dict)


@router.get('')
def proposals(request:Request,limit:int=Query(20,ge=1,le=100),offset:int=Query(0,ge=0)):
    user=reviewer(request)
    rows=invoke(ReadoutUpdateStore(request.app.state.store).list,user['owner'],limit=limit+1,offset=offset)
    return page_response(rows,limit,offset) | {'approval_requirements':list(CONFIRMATIONS),
        'calendar_effect':'only_independently_approved_updates_are_public'}


@router.post('')
def propose(body:Proposal,request:Request):
    user=reviewer(request)
    if hasattr(request.app.state,'platform_rate_limit'):
        request.app.state.platform_rate_limit('readout-proposal:'+user['owner'],20,600)
    return invoke(ReadoutUpdateStore(request.app.state.store).propose,user['owner'],body.model_dump())


@router.get('/{proposal_id}')
def detail(proposal_id:str,request:Request):
    user=reviewer(request)
    return invoke(ReadoutUpdateStore(request.app.state.store).get,proposal_id,user['owner'])


@router.post('/{proposal_id}/review')
def review(proposal_id:str,body:Decision,request:Request):
    user=reviewer(request)
    if hasattr(request.app.state,'platform_rate_limit'):
        request.app.state.platform_rate_limit('readout-review:'+user['owner'],12,600)
    return invoke(ReadoutUpdateStore(request.app.state.store).review,user['owner'],proposal_id,
                  body.decision,body.reason,body.confirmations)
