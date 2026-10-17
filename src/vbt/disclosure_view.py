"""Read-only official chart observations and conservative registry reconciliation.

A chart row is a drug–indication–phase observation. It is not an independently
verified asset, a portfolio census, or a claim about territorial rights. Linkage
uses only exact NFKC, whitespace and case normalization within configured source
companies; synonyms, inferred code aliases, targets and title mentions are not
used to invent a match.
"""
from __future__ import annotations

import html
import json
import re
from datetime import datetime
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from . import disclosures
from .market import Market
from .market_assets import normalize_name

PAGE_SIZE = 50
STATES = {
    'running': '抓取中', 'succeeded': '结构化提取完成',
    'parse_blocked': '已留存来源，结构化提取待完成',
    'fetch_failed': '抓取失败', 'interrupted': '任务已中断，需重试',
    'superseded': '旧任务已失去执行权', 'cancelled': '已取消',
}
ROLES = {
    'EXPERIMENTAL': '实验组', 'ACTIVE_COMPARATOR': '活性对照组',
    'PLACEBO_COMPARATOR': '安慰剂对照组', 'SHAM_COMPARATOR': '模拟对照组',
    'NO_INTERVENTION': '无干预组', 'OTHER': '其他分组',
}


def e(value):
    return html.escape(str(value if value is not None else '尚未提供'), quote=True)


def _json_dict(value):
    try:
        result = json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError):
        return {}
    return result if isinstance(result, dict) else {}


def _offset(value):
    try:
        return max(0, int(value))
    except (TypeError, ValueError, OverflowError):
        return 0


def _url(params):
    return '/disclosures?' + html.escape(urlencode(params), quote=True)


def _date(value):
    """Readable dates without losing precision or inventing a source timezone."""
    if not value:
        return '<span class="muted">尚未提供</span>'
    raw = str(value)
    if re.fullmatch(r'\d{4}-\d{2}', raw):
        label = raw[:4] + ' 年 ' + raw[5:] + ' 月'
    elif re.fullmatch(r'\d{4}-\d{2}-\d{2}', raw):
        label = raw.replace('-', '/')
    else:
        try:
            parsed = datetime.fromisoformat(raw.replace('Z', '+00:00'))
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone(ZoneInfo('Asia/Taipei'))
                label = parsed.strftime('%Y/%m/%d %H:%M') + ' 台北'
            else:
                label = parsed.strftime('%Y/%m/%d %H:%M') + '（时区未提供）'
        except ValueError:
            return e(raw)
    return '<time datetime="' + e(raw) + '" title="原始时间：' + e(raw) + '">' + e(label) + '</time>'


def _source_options(sources, company='', source_id=''):
    if source_id and source_id not in {source['id'] for source in sources}:
        raise ValueError('未知官方披露来源')
    return [source for source in sources if (not company or company in source['companies'])
            and (not source_id or source['id'] == source_id)]


def select_observations(market, sources, *, company='', source_id='', query='', phase='', offset=0):
    """Read one consistent latest-success page and explicit latest-attempt state."""
    selected = _source_options(sources, company, source_id)
    if len(query) > 1000:
        raise ValueError('查询内容过长')
    if not selected:
        return [], 0, {}
    captures = {}
    latest = {}
    with market.db() as db:
        db.execute('BEGIN')
        for source in selected:
            attempt = db.execute('SELECT * FROM disclosure_captures WHERE source_id=? ORDER BY started DESC,id DESC LIMIT 1', (source['id'],)).fetchone()
            success = db.execute("SELECT * FROM disclosure_captures WHERE source_id=? AND status='succeeded' ORDER BY started DESC,id DESC LIMIT 1", (source['id'],)).fetchone()
            if attempt:
                latest[source['id']] = dict(attempt)
            if success:
                captures[success['id']] = dict(success)
        if not captures:
            return [], 0, {'latest': latest, 'successful': {}}
        clause = 'capture_id IN (' + ','.join('?' for _ in captures) + ')'
        params = list(captures)
        if query:
            # Treat user input as literal text, including percent/underscore.
            escaped = query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
            clause += " AND (drug_code LIKE ? ESCAPE '\\' OR drug_name LIKE ? ESCAPE '\\' OR indication LIKE ? ESCAPE '\\' OR target LIKE ? ESCAPE '\\')"
            params += ['%' + escaped + '%'] * 4
        if phase:
            clause += ' AND phase=?'
            params.append(phase)
        total = db.execute('SELECT count(*) FROM disclosure_observations WHERE ' + clause, params).fetchone()[0]
        rows = [dict(row) for row in db.execute('SELECT * FROM disclosure_observations WHERE ' + clause + ' ORDER BY source_id,source_locator,id LIMIT ? OFFSET ?', params + [PAGE_SIZE, offset])]
    configs = {source['id']: source for source in selected}
    for row in rows:
        capture = captures[row['capture_id']]
        row.update({
            'data': _json_dict(row['data']), 'url': capture['url'],
            'sha256': capture['sha256'], 'retrieved_at': capture['finished'],
            'published_at': capture['published_at'], 'as_of': capture['as_of'],
            'companies': [company] if company else configs[row['source_id']]['companies'],
            'prior_capture_retained': latest[row['source_id']]['id'] != capture['id'],
            'latest_attempt_status': latest[row['source_id']]['status'],
        })
    return rows, total, {'latest': latest, 'successful': {row['source_id']: row for row in captures.values()}}


def link_registry(market, observations):
    """Exact intervention-name/code matches within the same source company only.

    A trial is linked at most once per security per observation. A title, target,
    synonym field, another company or an indication alone cannot create a link.
    Returned roles remain per intervention and never imply asset ownership.
    """
    company_ids = sorted({company for row in observations for company in row['companies']})
    index = {}
    malformed = 0
    if company_ids:
        with market.db() as db:
            rows = db.execute('SELECT company,nct,data,observed FROM trials WHERE company IN (' + ','.join('?' for _ in company_ids) + ') ORDER BY company,nct', company_ids).fetchall()
        for trial in rows:
            data = _json_dict(trial['data'])
            interventions = data.get('interventions')
            if not isinstance(interventions, list) or not re.fullmatch(r'NCT[0-9]{8}', trial['nct'] or ''):
                malformed += 1
                continue
            for intervention in interventions:
                if (not isinstance(intervention, dict) or intervention.get('type') not in ('DRUG', 'BIOLOGICAL')
                        or not isinstance(intervention.get('name'), str) or not intervention['name'].strip()):
                    continue
                name = normalize_name(intervention['name'])
                key = trial['company'], name
                roles = intervention.get('arm_roles', [])
                roles = sorted({role for role in roles if isinstance(role, str)}) if isinstance(roles, list) else []
                index.setdefault(key, []).append({
                    'company': trial['company'], 'nct': trial['nct'],
                    'url': 'https://clinicaltrials.gov/study/' + trial['nct'],
                    'intervention_name': intervention['name'], 'arm_roles': roles,
                    'phases': data.get('phases') if isinstance(data.get('phases'), list) else [],
                    'conditions': data.get('conditions') if isinstance(data.get('conditions'), list) else [],
                    'source_updated': data.get('source_updated'), 'observed': trial['observed'],
                })
    result = []
    for row in observations:
        needles = {normalize_name(row[key]) for key in ('drug_code', 'drug_name')
                   if isinstance(row.get(key), str) and row[key].strip()}
        matches = {}
        for company in row['companies']:
            for needle in needles:
                for found in index.get((company, needle), []):
                    key = found['company'], found['nct']
                    existing = matches.get(key)
                    if existing:
                        existing['arm_roles'] = sorted(set(existing['arm_roles']) | set(found['arm_roles']))
                        existing['intervention_names'] = sorted(set(existing['intervention_names']) | {found['intervention_name']})
                    else:
                        matches[key] = dict(found, intervention_names=[found['intervention_name']])
        result.append(dict(row, registry_links=sorted(matches.values(), key=lambda found: (found['company'], found['nct'])),
                           linkage='exact_intervention_name' if matches else 'unresolved'))
    return result, malformed


def _source_card(source, token, names, success=None):
    attempt = source.get('latest')
    status = STATES.get(attempt['status'], attempt['status']) if attempt else '尚未抓取'
    attempt = attempt or {}
    success = success or (attempt if attempt.get('status') == 'succeeded' else {})
    status_class = 'succeeded' if attempt.get('status') == 'succeeded' else 'blocked' if attempt.get('status') in ('fetch_failed', 'parse_blocked', 'interrupted') else ''
    body = '<article class="card source-card"><div class="source-card-head"><span class="badge ' + status_class + '">' + e(status) + '</span>'
    body += '<a class="text-link" href="' + e(source['url']) + '" target="_blank" rel="noopener">官方来源 ↗</a></div><h3>' + e(source['name']) + '</h3>'
    body += '<p class="muted">' + ' / '.join(e(names.get(company, company)) for company in source['companies']) + '</p>'
    if success:
        body += '<p class="source-count"><strong>' + str(success.get('count', 0)) + '</strong> 条结构化观察</p>'
    else:
        body += '<p class="source-count muted">尚无结构化观察</p>'
    body += '<dl class="source-meta"><dt>来源内容截止时间</dt><dd>' + _date(success.get('as_of')) + '</dd>'
    body += '<dt>来源发布日期</dt><dd>' + _date(success.get('published_at') or attempt.get('published_at')) + '</dd>'
    body += '<dt>最近抓取尝试</dt><dd>' + _date(attempt.get('finished') or attempt.get('started')) + '</dd></dl>'
    if success and success.get('id') != attempt.get('id'):
        body += '<p class="notice">最新尝试未发布新的观察，当前仍展示最近成功批次；请核对内容截止日期。</p>'
    if attempt.get('error'):
        body += '<p class="source-error">存在来源缺口，请查看下方抓取详情。</p>'
    body += '<details class="source-details"><summary>覆盖范围与抓取详情</summary><p>覆盖范围：' + e(source['scope']) + '</p>'
    body += '<p>关联证券：' + ' / '.join(e(company) for company in source['companies']) + '</p>'
    body += '<p>最近成功结构化批次：' + _date(success.get('finished')) + '</p>'
    if success:
        body += '<p class="ref">来源批次：' + e(success['id']) + '</p>'
    if attempt.get('error'):
        body += '<p class="notice">来源缺口：' + e(attempt['error']) + '</p>'
    body += '</details><form method="post" action="/market/disclosure/enqueue"><input type="hidden" name="token" value="' + e(token) + '"><input type="hidden" name="source_id" value="' + e(source['id']) + '"><button class="button-secondary">排队刷新官方来源</button></form></article>'
    return body


def render(app, q):
    market = getattr(app, 'market', None) or Market(app.workspace, app.state)
    sources = disclosures.sources(market)
    companies = sorted(market.rows('companies'), key=lambda row: row['id'])
    names = {company['id']: company['name'] for company in companies}
    known_ids = set(names) | {company for source in sources for company in source['companies']}
    company = q.get('company', '')
    if company and company not in known_ids:
        raise ValueError('公司尚未配置')
    source_id = q.get('source_id', '')
    selected = _source_options(sources, company, source_id)
    query = q.get('q', '').strip()
    phase = q.get('phase', '')
    offset = _offset(q.get('offset', 0))
    observations, total, state = select_observations(market, sources, company=company, source_id=source_id,
                                                     query=query, phase=phase, offset=offset)
    observations, malformed = link_registry(market, observations)
    linked = sum(bool(row['registry_links']) for row in observations)
    body = '<header class="page-header"><div><div class="eyebrow">OFFICIAL DISCLOSURES</div><h1>官方管线披露与登记核对</h1><p class="lead">从公司披露出发，逐项核对药物、适应症、研发阶段与临床登记。</p></div>'
    body += '<nav class="page-actions" aria-label="相关研究视图"><a class="button button-secondary" href="/coverage">覆盖与缺口</a><a class="button button-secondary" href="/market">临床登记检索</a><a class="button button-secondary" href="/assets">药物与试验关联</a></nav></header>'
    body += '<p class="notice">观察行按药物—适应症—阶段计数，同一药物可出现多行。公司披露和登记关联均未独立确认完整管线、药物所有权或授权地域。登记阶段与适应症原文另列，尚未判定与公司披露是否一致。</p>'
    body += '<form class="card filter-toolbar" action="/disclosures" method="get" aria-label="筛选官方披露观察"><div class="form-grid"><div class="field"><label for="disclosure-company">公司</label><select id="disclosure-company" name="company"><option value="">全部已配置披露来源</option>'
    body += ''.join('<option ' + ('selected ' if company == ident else '') + 'value="' + e(ident) + '">' + e(names.get(ident, ident) + ' · ' + ident) + '</option>' for ident in sorted(known_ids))
    body += '</select></div><div class="field"><label for="disclosure-source">官方披露来源</label><select id="disclosure-source" name="source_id"><option value="">全部来源</option>'
    body += ''.join('<option ' + ('selected ' if source_id == source['id'] else '') + 'value="' + e(source['id']) + '">' + e(source['name']) + '</option>' for source in sources)
    body += '</select></div><div class="field"><label for="disclosure-query">药物、代号、靶点或适应症</label><input type="search" id="disclosure-query" name="q" value="' + e(query or '') + '" placeholder="搜索来源原文"></div><div class="field"><label for="disclosure-phase">公司披露阶段</label><select id="disclosure-phase" name="phase"><option value="">全部阶段</option>'
    body += ''.join('<option ' + ('selected ' if phase == label else '') + 'value="' + e(label) + '">' + e(label) + '</option>' for label in ('Phase I', 'Phase II', 'Phase III', 'NDA/BLA'))
    body += '</select></div></div><div class="filter-actions"><button>查看披露观察</button><a href="/disclosures">清除筛选</a></div></form>'
    body += '<section aria-labelledby="disclosure-sources-heading"><div class="section-head"><h2 id="disclosure-sources-heading">来源状态与覆盖缺口</h2><span>' + str(len(selected)) + ' 个配置来源</span></div><p class="muted">每个来源仅覆盖其明确标注的范围。抓取时间、内容截止时间和发布日期分别保留。</p>'
    if not selected:
        no_source = not any(company in source['companies'] for source in sources) if company else False
        message = '该证券尚未配置官方管线披露来源。' if no_source else '当前公司／来源筛选没有匹配的官方披露来源，可清除来源筛选。'
        body += '<div class="empty">' + message + '请在覆盖页查看申办方关联与登记采集情况；此处为空不表示公司没有研发管线。</div>'
    else:
        body += '<div class="source-grid">'
        for source in selected:
            body += _source_card(source, app.token, names, state.get('successful', {}).get(source['id']))
        body += '</div>'
    body += '</section><section aria-labelledby="disclosure-observations-heading"><div class="section-head"><h2 id="disclosure-observations-heading">药物—适应症—阶段观察 · ' + str(total) + ' 条</h2><span>每页 ' + str(PAGE_SIZE) + ' 条</span></div>'
    body += '<div class="metric-row"><div class="metric-card"><span class="muted">筛选后观察</span><strong class="metric">' + str(total) + '</strong><small>最近成功结构化批次</small></div><div class="metric-card"><span class="muted">本页已建立名称关联</span><strong class="metric">' + str(linked) + '</strong><small>不代表阶段或适应症一致</small></div><div class="metric-card"><span class="muted">本页待关联</span><strong class="metric">' + str(len(observations) - linked) + '</strong><small>不代表缺少临床试验</small></div></div>'
    body += '<p class="muted">当前页 ' + str(linked) + ' 条具有同公司登记名称关联，' + str(len(observations) - linked) + ' 条尚未建立精确关联。关联仅采用名称／代号的 Unicode、空白和大小写规范化，不猜测同义词或研发代号转换。</p>'
    if malformed:
        body += '<p class="notice">相关公司有 ' + str(malformed) + ' 条登记记录格式不完整，本页未用其建立关联；需检查采集记录。</p>'
    if observations:
        body += '<p class="scroll-cue muted" id="disclosure-scroll-help">表格包含五列；较窄窗口可横向滚动。键盘用户可聚焦表格区域并使用方向键。</p><div class="table-scroll scroll" role="region" tabindex="0" aria-label="官方披露与同公司登记关联表格" aria-describedby="disclosure-scroll-help"><table class="disclosure-table"><caption class="sr-only">公司披露的药物、适应症、阶段、来源日期及同公司登记名称关联</caption><thead><tr><th scope="col">公司披露的药物／代号</th><th scope="col">靶点／形式</th><th scope="col">适应症／阶段</th><th scope="col">来源与日期</th><th scope="col">同公司登记关联</th></tr></thead><tbody>'
        for row in observations:
            body += '<tr><td><strong>' + e(row['drug_name']) + '</strong><p class="ref">代号：' + e(row['drug_code']) + '</p><p class="muted">' + ' / '.join(e(names.get(ident, ident)) for ident in row['companies']) + '</p></td><td>' + e(row['target']) + '<p class="muted">' + e(row['modality']) + '</p></td><td>' + e(row['indication']) + '<p><span class="badge">' + e(row['phase']) + '</span></p></td><td><a href="' + e(row['url']) + '" target="_blank" rel="noopener">官方原文 ↗</a><dl class="source-meta"><dt>内容截止</dt><dd>' + _date(row['as_of']) + '</dd><dt>抓取</dt><dd>' + _date(row['retrieved_at']) + '</dd><dt>发布</dt><dd>' + _date(row['published_at']) + '</dd></dl><details><summary>原文定位与完整性依据</summary><p class="ref">原文定位：' + e(row['source_locator']) + '</p><p class="ref">来源批次：' + e(row['capture_id']) + '</p><p class="ref">SHA-256：' + e(row['sha256']) + '</p><p>未提供字段：' + e(' / '.join(row['data'].get('missing_fields', []))) + '</p><p class="ref">关联证券：' + ' / '.join(e(ident) for ident in row['companies']) + '</p></details>'
            if row['prior_capture_retained']:
                body += '<p class="notice">保留此前成功批次；最近尝试：' + e(STATES.get(row['latest_attempt_status'], row['latest_attempt_status'])) + '</p>'
            body += '</td><td>'
            if not row['registry_links']:
                body += '<span class="badge">精确名称关联待解决</span><p class="muted">可能尚未采集、使用其他名称或未在该来源登记；不能据此判定缺少试验。</p>'
            else:
                body += '<details><summary>' + str(len(row['registry_links'])) + ' 项证券—试验关联</summary>'
                for link in row['registry_links']:
                    role = ' / '.join(ROLES.get(value, value) for value in link['arm_roles']) or '用药角色未提供'
                    body += '<div class="registry-link"><p><a href="' + e(link['url']) + '" target="_blank" rel="noopener">' + e(link['nct']) + ' ↗</a> · ' + e(link['company']) + '<br>干预名称：' + e(' / '.join(link['intervention_names'])) + '<br>用药角色：' + e(role) + '<br>登记阶段：' + e(' / '.join(str(value) for value in link['phases'])) + '<br>登记适应症：' + e(' / '.join(str(value) for value in link['conditions'])) + '<br>登记更新：' + _date(link['source_updated']) + '</p></div>'
                body += '<p class="ref">比较组与实验组均可出现名称关联；分组角色不确认资产所有权。</p></details>'
            body += '</td></tr>'
        body += '</tbody></table></div>'
    else:
        body += '<div class="empty">当前筛选没有可展示的已结构化观察。可调整筛选，或检查上方来源是否尚未抓取、解析受阻或最新抓取失败。</div>'
    params = {key: q[key] for key in ('company', 'source_id', 'q', 'phase') if q.get(key)}
    if offset or offset + PAGE_SIZE < total:
        body += '<nav class="pagination" aria-label="披露观察分页">'
        if offset:
            body += '<a class="button button-secondary" href="' + _url({**params, 'offset': max(0, offset - PAGE_SIZE)}) + '">← 上一页</a>'
        body += '<span class="muted">第 ' + str(offset // PAGE_SIZE + 1) + ' 页</span>'
        if offset + PAGE_SIZE < total:
            body += '<a class="button button-secondary" href="' + _url({**params, 'offset': offset + PAGE_SIZE}) + '">下一页观察 →</a>'
        body += '</nav>'
    return body + '</section>'
