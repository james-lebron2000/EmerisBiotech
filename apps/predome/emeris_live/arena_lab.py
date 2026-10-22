"""Lab-allowlisted/existing-reviewer workspace; public audit records stay public.

This router adds read-only navigation and views, not a new authentication scheme,
provider runner, or billing surface. All model execution stays in the operator CLI.
"""
import os
from html import escape
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response

from . import arena_api, platform_api


ROOT = Path(__file__).resolve().parents[1]
router = APIRouter(tags=['internal Arena research workspace'])


def lab_user(request):
    """Reuse the existing session without granting global result-review powers."""
    user = platform_api.actor(request)
    allowed = {name.strip().casefold() for name in os.environ.get('EMERIS_ARENA_LAB_USERS', '').split(',')
               if name.strip()}
    if user.get('name', '').casefold() not in allowed and not platform_api.is_reviewer(user):
        raise HTTPException(403, '内部工作台仅对已配置的研究工作台账号开放。')
    return user


def access_shell(status_code):
    authenticated = status_code == 403
    title = '当前账号没有内部工作台权限' if authenticated else '登录内部研究工作台'
    message = ('当前登录账号未获内部工作台访问权限。请联系管理员，或在数据工作台切换账号后返回此页。'
               if authenticated else '请先在数据工作台登录；没有账号可在 /data 注册后登录，再返回此页。内部工作台仅向已获授权的研究账号开放。')
    public_origin = os.environ.get('EMERIS_PUBLIC_ORIGIN', '').rstrip('/')
    try:
        origin = urlsplit(public_origin)
        if (origin.scheme != 'https' or not origin.hostname or origin.username or origin.password
                or origin.path or origin.query or origin.fragment):
            public_origin = ''
        else:
            origin.port  # Reject a malformed port before constructing a link.
    except ValueError:
        public_origin = ''
    login_url = escape(public_origin + '/data', quote=True)
    public_url = escape(public_origin + '/arena', quote=True)
    return HTMLResponse(f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Predome · 内部研究工作台</title><link rel="stylesheet" href="/static/arena.css"></head>
<body><main><section class="panel"><p>Predome · 内部研究工作台</p><h1>{title}</h1>
<p>{message}</p><p><a href="{login_url}">前往登录 / 切换账号</a> · <a href="/arena/lab">登录后返回内部工作台</a></p>
<p><a href="{public_url}">查看公开临床试验比较</a></p>
<p>内部页面只读。模型运行由管理员通过操作命令行执行，网页不会触发付费调用。</p>
<p>公开预测、来源与审计下载仍保持公开；内部访问限制不改变这些资料的公开状态。</p>
</section></main></body></html>''', status_code=status_code)


@router.get('/arena/lab', include_in_schema=False)
@router.get('/static/arena-lab.html', include_in_schema=False)
def lab_page(request: Request):
    try:
        lab_user(request)
    except HTTPException as exc:
        if exc.status_code in (401, 403):
            return access_shell(exc.status_code)
        raise
    return FileResponse(ROOT / 'static/arena-lab.html')


@router.get('/api/arena/lab/overview')
def overview(request: Request, cohort_id: str | None = None,
             track: str = 'same_evidence', condition: str = 'clinical_only'):
    lab_user(request)
    return arena_api.overview(request, cohort_id=cohort_id, track=track, condition=condition)


@router.get('/api/arena/lab/access')
def access(request: Request):
    lab_user(request)
    return Response(status_code=204)


@router.get('/api/arena/lab/targets/{target_id}')
def target_detail(target_id: str, request: Request):
    lab_user(request)
    return arena_api.target_detail(target_id, request)
