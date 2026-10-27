"""Session-bound source review. No model calls, acquisition or web enrollment.

Each reviewer sees the frozen question, acquired originals and their own review
pointer. Peer labels and model forecasts are deliberately absent from this view.
Account enrollment is not verification of clinical credentials or independence.
"""
from contextlib import closing
from datetime import date, datetime, timezone
from html import escape
import json
from pathlib import Path
import sqlite3
import subprocess
import time
from types import SimpleNamespace
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from . import platform_api
from .arena_acquired import read_acquired
from .arena_public import read_frozen
from .arena_review_auth import resolve_enrollment
from .arena_store import ArenaLedger, instant, result_target_binding, sha
from .intake_api import official_outcome_source
from .official_intake import extract, normalize

ROOT = Path(__file__).resolve().parents[1]
router = APIRouter(tags=['enrolled internal source review'])
CONFIRMATIONS = ('exact_trial', 'primary_endpoint', 'prespecified_analysis', 'first_public_release', 'source_inspected')
HASH = r'^[a-f0-9]{64}$'


class Confirmations(BaseModel):
    model_config = ConfigDict(extra='forbid')
    exact_trial: StrictBool
    primary_endpoint: StrictBool
    prespecified_analysis: StrictBool
    first_public_release: StrictBool
    source_inspected: StrictBool


class SourceReview(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enrollment_sha256: str = Field(pattern=HASH)
    target_binding_sha256: str = Field(pattern=HASH)
    candidate_id: str = Field(min_length=1, max_length=160, pattern=r'^[A-Za-z0-9_-]+$')
    candidate_sha256: str = Field(pattern=HASH)
    label: Literal['success', 'failure', 'mixed', 'inconclusive', 'not_adjudicable']
    first_public_at: str = Field(min_length=20, max_length=80)
    source_publication_date: date
    source_quote: str = Field(min_length=20, max_length=6000)
    review_note: str = Field(min_length=1, max_length=4000)
    confirmations: Confirmations
    supersedes_result_sha256: str | None = Field(default=None, pattern=HASH)
    supersedes_review_sha256: str | None = Field(default=None, pattern=HASH)


def authorized(request, target_id=None, expected=None):
    user = platform_api.actor(request)
    try:
        snapshot = read_frozen(request.app.state.store)
        grant = resolve_enrollment(snapshot.get('events', []), user['owner'], target_id, expected)
    except PermissionError:
        raise HTTPException(403, '当前账号未获该试验的有效结果核验授权，或授权已更新。') from None
    except (ValueError, OSError, sqlite3.Error):
        raise HTTPException(409, '核验账本未通过完整性检查；未确认任何操作成功。') from None
    return user, grant, snapshot


def target_from(snapshot, target_id):
    target = next((t for t in snapshot['targets'] if t['id'] == target_id), None)
    if target is None:
        raise HTTPException(404, '未找到登记的预测目标。')
    return target


def sources(store, target):
    """A never-collected inbox is empty; a partial/corrupt schema is an error."""
    path = Path(store.path) / 'workbench.sqlite3'
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        db.execute('PRAGMA query_only=ON')
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    required = {'intake_candidates', 'intake_source_blobs'}
    if not tables & required:
        return []
    if not required <= tables:
        raise ValueError('Incomplete acquired source archive')
    return read_acquired(store.path, [target['nct_id']])


def selected_source(request, target, candidate_id, digest=None):
    try:
        rows = sources(request.app.state.store, target)
        source = next((r for r in rows if r['id'] == candidate_id), None)
        if source is None:
            raise HTTPException(404, '该试验没有这份已保存来源。')
        if digest is not None and digest != source['sha256']:
            raise HTTPException(409, '来源版本已改变；请重新核对原件。')
        return source
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error):
        raise HTTPException(409, '原始来源或采集记录完整性未通过；不生成替代结果。') from None


def reviewable(source):
    if source['kind'] == 'official_announcement_candidate':
        return True, '候选公告仍须人工核对确切试验、主要终点和首次公开时间。'
    if (source['kind'] == 'reported_results_candidate'
            and source['structured'].get('reported_outcome_measures')):
        return True, '已发布登记结果；仍须人工核对预设主要分析，不能仅凭hasResults判定达标。'
    return False, '登记状态、无结果更新和文献索引不能作为本流程的结果公告。'


def inspection(source):
    if not reviewable(source)[0]:
        raise ValueError('Source is not eligible for result review')
    if source['kind'] == 'official_announcement_candidate':
        doc = extract(source['raw'])
        return {'text': doc['text'], 'source_publication_date': doc.get('published_at'),
                'source_publication_timestamp_utc': doc.get('published_at_utc'),
                'publication_time_precision': doc.get('publication_time_precision'),
                'publication_date_basis': 'document_metadata' if doc.get('published_at') else 'not_recorded',
                'source_sha256': source['sha256']}
    value = json.loads(source['raw'])
    text = json.dumps(value.get('resultsSection', {}), ensure_ascii=False, sort_keys=True)
    if not text.strip() or len(text) > 500000:
        raise ValueError('Results text cannot be inspected within the request bound')
    publication = (source['structured'].get('results_first_post_date') or {}).get('date')
    if publication is not None:
        publication = date.fromisoformat(publication).isoformat()
    return {'text': text, 'source_publication_date': publication,
            'publication_date_basis': 'registry_results_post_date' if publication else 'not_recorded',
            'source_sha256': source['sha256']}


def own_pointers(snapshot, target, principal):
    adjudications = [e for e in snapshot['events'] if e['kind'] == 'result_adjudicated' and e['entity_id'] == target['id']]
    version = adjudications[-1]['payload']['result_sha256'] if adjudications else None
    round_start = adjudications[-1]['seq'] if adjudications else 0
    own = [e for e in snapshot['events'] if e['kind'] == 'result_review' and e['entity_id'] == target['id']
           and e['seq'] > round_start
           and (e['payload'].get('reviewer_authentication') or {}).get('principal_id') == principal
           and e['payload'].get('supersedes') == version]
    return version, own[-1]['payload']['vote_sha256'] if own else None


@router.get('/arena/lab/review')
@router.get('/static/arena-review.html', include_in_schema=False)
def review_page(request: Request):
    try:
        authorized(request)
    except HTTPException as exc:
        if exc.status_code not in (401,403):
            raise
        host=request.url.hostname
        login='https://'+('['+host+']' if ':' in host else host)+'/data'
        message='请先在数据工作台登录，再返回此页。' if exc.status_code==401 else '当前账号尚未获该试验的结果核验授权。工作台调试权限不自动授予结果核验权限。'
        return HTMLResponse('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Predome · 结果核验访问</title><main><h1>结果核验工作区</h1><p>'+message+'</p><p><a href="'+escape(login,quote=True)+'">打开数据工作台登录</a></p><p>登录和账号登记不构成临床资质或科学独立性证明。</p></main></html>',status_code=exc.status_code)
    return FileResponse(ROOT / 'static' / 'arena-review.html')


@router.get('/api/arena/review/access')
def access(request: Request):
    user, grant, _ = authorized(request)
    return {'name': user['name'], 'csrf': user['csrf'], 'enrollment_sha256': grant['event_hash'],
            'target_ids': grant['target_ids'], 'identity_assurance': grant['identity_assurance'],
            'scientific_credentials_verified': False, 'independent_human_review_verified': False}


@router.get('/api/arena/review/targets')
def targets(request: Request):
    _, grant, snapshot = authorized(request)
    fields = ('id', 'trial_name', 'nct_id', 'drug', 'endpoint')
    return {'targets': [{k: t.get(k) for k in fields} for t in snapshot['targets'] if t['id'] in grant['target_ids']]}


@router.get('/api/arena/review/targets/{target_id}')
def worksheet(target_id: str, request: Request):
    _, grant, snapshot = authorized(request, target_id)
    target = target_from(snapshot, target_id)
    version, own = own_pointers(snapshot, target, grant['principal_id'])
    try:
        candidates = sources(request.app.state.store, target)
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error):
        raise HTTPException(409, '来源档案未通过核对；没有把读取失败显示为没有结果。') from None
    fields = ('id', 'nct_id', 'trial_name', 'drug', 'endpoint', 'population', 'comparison', 'success_definition', 'analysis_timepoint')
    return {'target': {k: target.get(k) for k in fields}, 'target_binding_sha256': sha(result_target_binding(target)),
            'enrollment_sha256': grant['event_hash'], 'supersedes_result_sha256': version, 'own_review_sha256': own,
            'candidates': [{**{k: r[k] for k in ('id', 'kind', 'url', 'sha256', 'retrieved_at')},
                            'reviewable': reviewable(r)[0], 'reason': reviewable(r)[1]} for r in candidates]}


@router.get('/api/arena/review/targets/{target_id}/sources/{candidate_id}')
def original(target_id: str, candidate_id: str, request: Request):
    _, _, snapshot = authorized(request, target_id)
    source = selected_source(request, target_from(snapshot, target_id), candidate_id)
    extension = '.pdf' if source['raw'].startswith(b'%PDF-') else '.txt'
    return Response(source['raw'], media_type='application/octet-stream',
                    headers={'Content-Disposition': f'attachment; filename="source-{source["sha256"][:16]}{extension}"'})


@router.get('/api/arena/review/targets/{target_id}/sources/{candidate_id}/inspect')
def inspect_source(target_id: str, candidate_id: str, request: Request):
    user, _, snapshot = authorized(request, target_id)
    request.app.state.platform_rate_limit('arena-source-inspect:' + user['owner'], 30, 600)
    source = selected_source(request, target_from(snapshot, target_id), candidate_id)
    try:
        return inspection(source)
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
        raise HTTPException(422, '该原件无法作为本流程的可核对结果正文；请下载原件，不生成替代摘录。') from None


@router.post('/api/arena/review/targets/{target_id}/reviews')
def submit_review(target_id: str, body: SourceReview, request: Request):
    user, _, snapshot = authorized(request, target_id, body.enrollment_sha256)
    request.app.state.platform_rate_limit('arena-source-review:' + user['owner'], 20, 600)
    target = target_from(snapshot, target_id)
    if body.target_binding_sha256 != sha(result_target_binding(target)):
        raise HTTPException(409, '登记问题版本不一致；未写入审核。')
    if not all(body.confirmations.model_dump().values()):
        raise HTTPException(422, '请逐项确认试验、主要终点、分析时间、首次公开及原件检查。')
    source = selected_source(request, target, body.candidate_id, body.candidate_sha256)
    try:
        first_public = instant(body.first_public_at)
        retrieved = instant(source['retrieved_at'])
        today = datetime.now(timezone.utc)
        if not first_public <= retrieved <= today or first_public.date() > body.source_publication_date:
            raise ValueError('Inconsistent source and result times')
        if source['kind'] == 'official_announcement_candidate':
            checked = official_outcome_source(request.app.state.store, source['id'], SimpleNamespace(
                source_quote=body.source_quote, source_publication_date=body.source_publication_date,
                readout_date=body.first_public_at))
        else:
            document = inspection(source)
            quote = normalize(body.source_quote)
            if (source['kind'] != 'reported_results_candidate' or not document['source_publication_date']
                    or document['source_publication_date'] != body.source_publication_date.isoformat()
                    or not 20 <= len(quote) <= 6000 or quote not in normalize(document['text'])
                    or body.source_publication_date > retrieved.date()):
                raise ValueError('Exact posted results and original quote required')
            checked = {'excerpt': quote, 'publication_date_basis': 'registry_results_post_date'}
        context = {'candidate_id': source['id'], 'source_sha256': source['sha256'],
                   'excerpt': checked['excerpt'], 'publication_date_basis': checked['publication_date_basis'],
                   'source_publication_date': body.source_publication_date.isoformat(),
                   'confirmations': body.confirmations.model_dump()}
        result = {'target_binding': result_target_binding(target), 'label': body.label,
                  'source_url': source['url'], 'source_sha256': source['sha256'],
                  'first_public_at': first_public.isoformat(),
                  'endpoint_match_note': 'Exact registered target correspondence attested; individual source-check notes remain in the review record.'}
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
        raise HTTPException(422, '原件、连续摘录、确切试验或公开时间未通过核对；未写入审核。') from None
    # Serialize against logout/account deletion, then reauthenticate after parsing.
    # Lock order is workbench -> Arena; enrollment is checked again by the ledger.
    with request.app.state.store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        live = db.execute('SELECT s.owner,s.csrf FROM sessions s JOIN users u ON u.id=s.owner WHERE s.token=? AND s.expires>?',
                          (user['token'], time.time())).fetchone()
        if not live or live['owner'] != user['owner'] or live['csrf'] != user['csrf']:
            raise HTTPException(401, '会话在原件核对期间已失效；未写入审核。')
        try:
            ledger = ArenaLedger(request.app.state.store.path)
            status = ledger.record_authenticated_result(target_id, result, account_id=live['owner'],
                enrollment_sha256=body.enrollment_sha256, review_note=body.review_note,
                review_context=context, supersedes=body.supersedes_result_sha256,
                review_supersedes=body.supersedes_review_sha256)
        except PermissionError:
            raise HTTPException(403, '授权已撤销、变更或不覆盖此试验；未写入审核。') from None
        except (ValueError, OSError, sqlite3.Error):
            raise HTTPException(409, '审核版本或账本状态已改变；请重新核对，未确认提交成功。') from None
    return {'status': status, 'target_id': target_id, 'scientific_credentials_verified': False,
            'independent_human_review_verified': False,
            'note': '已记录账号认证的来源审核。账号登记和一致票不构成临床资质或科学独立性证明。'}
