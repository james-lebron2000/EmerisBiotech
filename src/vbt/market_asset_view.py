"""Read-only intervention leads with explicit registry and ownership boundaries."""
import html
import json
import re
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlencode, urlsplit

from .market import Market
from .market_assets import build_assets


PAGE_SIZE = 50
COMPANY_PAGE_SIZE = 18
ROLES = {'experimental_arm': '在实验组中出现', 'comparator_only': '仅在比较组中出现',
         'unresolved': '用药角色待核对'}
FRESH = {'seen_in_latest': '最新完整抓取中可见', 'not_seen_in_latest': '仅保留于历史观察',
         'unknown': '批次关联尚未建立'}
ATTEMPTS = {'succeeded': '查询完成', 'failed': '失败', 'partial': '分页未完成',
            'interrupted': '中断', 'cancelled': '已取消', 'running': '进行中', 'queued': '已排队',
            'retry_wait': '等待重试'}


def e(value):
    return html.escape(str(value if value is not None else '未记载'), quote=True)


def _joined(values, empty='未记载'):
    return e(' / '.join(str(value) for value in values) if values else empty)


def _timestamp(value):
    if not value:
        return '<span class="cell-meta">未记载</span>'
    raw = str(value)
    try:
        compact = date.fromisoformat(raw).strftime('%Y-%m-%d')
    except ValueError:
        try:
            parsed = datetime.fromisoformat(raw.replace('Z', '+00:00'))
        except ValueError:
            return '<span class="cell-meta">' + e(raw) + '</span>'
        if parsed.tzinfo is not None and parsed.utcoffset() is not None:
            compact = parsed.astimezone(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M') + ' UTC+8'
        else:
            compact = parsed.strftime('%Y-%m-%d %H:%M') + ' · 时区未提供'
    return '<time datetime="' + e(raw) + '" title="' + e(raw) + '">' + e(compact) + '</time>'


def _source_link(source):
    """Only link the expected public registry record; imports remain untrusted."""
    raw, nct = source.get('url'), source.get('nct')
    try:
        parsed = urlsplit(str(raw))
        valid = (parsed.scheme == 'https' and parsed.netloc == 'clinicaltrials.gov'
                 and re.fullmatch(r'NCT[0-9]{8}', str(nct))
                 and parsed.path == '/study/' + str(nct) and not parsed.query and not parsed.fragment)
    except ValueError:
        valid = False
    if valid:
        return ('<a class="source-link" href="' + e(raw)
                + '" target="_blank" rel="noopener noreferrer">' + e(nct) + ' ↗</a>')
    return '<span>' + e(nct) + '</span><span class="cell-meta"> · 来源链接待核对</span>'


def _fact(label, value):
    # Values are already rendered, escaped HTML from this module.
    return '<dt>' + e(label) + '</dt><dd>' + value + '</dd>'


def _conditions(values):
    if len(values) <= 3:
        return _joined(values)
    return (_joined(values[:2]) + '<details class="asset-provenance"><summary>查看全部 '
            + str(len(values)) + ' 个适应症原文</summary><p>' + _joined(values) + '</p></details>')


def _source(source):
    freshness = FRESH.get(source.get('freshness'), FRESH['unknown'])
    role = ROLES.get(source.get('role_status'), ROLES['unresolved'])
    preview = (str(source.get('status') or '状态未记载') + ' · '
               + (' / '.join(str(value) for value in source.get('phases', [])) or '阶段未记载'))
    if len(preview) > 100:
        preview = preview[:100] + '…'
    parts = ['<details class="asset-trial-details"><summary>', e(source.get('nct')), ' · ', e(preview), '</summary>',
             '<section class="asset-source"><h4>', _source_link(source), '</h4>',
             '<p class="trial-title">', e(source.get('title') or '试验标题未记载'), '</p>',
             '<dl class="source-meta">',
             _fact('登记申办方', e(source.get('sponsor'))),
             _fact('用药角色', e(role) + '<div class="cell-meta">原文：' + _joined(source.get('arm_roles')) + '</div>'),
             _fact('试验状态原文', e(source.get('status'))),
             _fact('试验阶段原文', _joined(source.get('phases'), '未分期或未记载')),
             _fact('适应症原文', _joined(source.get('conditions'))),
             _fact('来源更新时间', _timestamp(source.get('source_updated'))),
             _fact('本地观测时间', _timestamp(source.get('observed'))),
             _fact('批次可见性', e(freshness)), '</dl>',
             '<details class="asset-provenance"><summary>查看原文名称、批次与校验值</summary>',
             '<dl class="source-meta">',
             _fact('用药名称原文', _joined(source.get('intervention_names'))),
             _fact('记录来源', e(source.get('url'))),
             _fact('数据 SHA-256', '<code>' + e(source.get('data_sha256')) + '</code>'),
             _fact('本条来源批次', '<code>' + e(source.get('last_run')) + '</code>'),
             _fact('最近完整批次', '<code>' + e(source.get('latest_run')) + '</code>'),
             _fact('最近查询尝试', e(ATTEMPTS.get(source.get('latest_attempt_status'), source.get('latest_attempt_status') or '未记载'))),
             _fact('首次观测时间', _timestamp(source.get('first_seen'))),
             _fact('最近观测时间', _timestamp(source.get('last_seen'))),
             '</dl></details></section></details>']
    return ''.join(parts)


def _card(asset):
    role = ROLES.get(asset.get('role_status'), ROLES['unresolved'])
    freshness = FRESH.get(asset.get('freshness'), FRESH['unknown'])
    retained = len(asset.get('retained_trials', []))
    history = ('<p class="data-note">其中 ' + str(retained)
               + ' 项试验仅保留于历史观察；未在最近完整批次中出现不代表试验终止。</p>') if retained else ''
    return ('<article class="card asset-card"><div class="asset-card-head"><div><div class="eyebrow">登记用药名称</div><h3>'
            + e(asset['drug_name']) + '</h3></div><span class="status-badge caution">权益待核对</span></div>'
            + '<div class="asset-badges"><span class="status-badge">' + e(role)
            + '</span><span class="status-badge">' + str(len(asset['trials'])) + ' 项登记试验</span></div>'
            + '<dl class="asset-facts">'
            + _fact('试验阶段原文', _joined(asset['phases'], '未分期或未记载'))
            + _fact('适应症原文', _conditions(asset['conditions']))
            + _fact('来源更新时间', _timestamp(asset.get('source_updated')))
            + _fact('批次可见性', e(freshness)) + '</dl>' + history
            + '<details class="asset-evidence"><summary>逐项核对 ' + str(len(asset['sources']))
            + ' 条试验依据 · ClinicalTrials.gov</summary>'
            + ''.join(_source(source) for source in asset['sources']) + '</details></article>')


def _company_context(q):
    result = {}
    term = q.get('company_q', '').strip()
    if term:
        result['company_q'] = term
    try:
        offset = max(0, int(q.get('company_offset', 0))) // COMPANY_PAGE_SIZE * COMPANY_PAGE_SIZE
    except (TypeError, ValueError):
        offset = 0
    if offset:
        result['company_offset'] = offset
    return result


def _company_observations(market):
    """One read snapshot; stored observations and latest attempts stay separate."""
    with market.db() as db:
        db.execute('BEGIN')
        counts = {row['company']: row['n'] for row in db.execute(
            'SELECT company,count(DISTINCT nct) AS n FROM trials GROUP BY company')}
        history = [dict(row) for row in db.execute(
            "SELECT * FROM runs WHERE source='ClinicalTrials.gov' ORDER BY started DESC,id DESC")]
        has_jobs = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='collection_jobs'").fetchone()
        active = {row['reference']: row['status'] for row in db.execute(
            "SELECT reference,status FROM collection_jobs WHERE kind='company' AND status IN ('queued','running','retry_wait')")} if has_jobs else {}
    latest, uncertain = {}, set()
    for row in history:
        cid = row['company']
        try:
            started = datetime.fromisoformat(row['started'].replace('Z', '+00:00'))
            if started.tzinfo is None or started.utcoffset() is None:
                raise ValueError()
            if started.timestamp() > datetime.now(timezone.utc).timestamp() + 300:
                raise ValueError()
            key = (started.timestamp(), row['id'])
        except (AttributeError, ValueError, TypeError, OverflowError, OSError):
            uncertain.add(cid)
            continue
        if cid not in latest or key > latest[cid][0]:
            latest[cid] = (key, row)
    return counts, {cid: row[1] for cid, row in latest.items()}, uncertain, active


def _collection_note(count, attempt, uncertain=False):
    if uncertain:
        return '查询记录的时间先后尚未核实，最近结果数量未知；已存观察另列。'
    if not attempt:
        return ('已存历史观察，查询批次尚未核实。' if count else
                '尚未查询或未记录查询批次，结果数量未知；不能据此判断没有管线。')
    status = attempt['status']
    if status == 'succeeded':
        result_count = attempt.get('count')
        if type(result_count) is not int or result_count < 0:
            return '最近查询已记录完成，返回数量尚未核实；已存观察另列。'
        if result_count == 0:
            return ('最近完整查询返回零条，仍保留历史观察；这不表示项目终止或公司没有管线。' if count else
                    '最近完整查询返回零条；此来源范围的零结果不代表公司没有研发管线。')
        return '最近完整查询记录返回 ' + str(result_count) + ' 条；已存数量包含保留的历史观察，不代表完整管线。'
    if status in ('failed', 'partial', 'interrupted', 'cancelled'):
        return '最近查询' + ATTEMPTS[status] + '，本次结果范围未确认；失败或不完整结果不能解释为没有管线。'
    if status == 'running':
        return '本次查询仍在进行，完成前不能将已存观察当作本次完整结果。'
    return '最近查询状态尚未核实，本次结果数量未知。'


def _company_directory(market, companies, q):
    context = _company_context(q)
    term = context.get('company_q', '')
    def searchable(company):
        try:
            aliases = json.loads(company.get('aliases', '[]'))
            aliases = [value for value in aliases if isinstance(value, str)] if isinstance(aliases, list) else []
        except (TypeError, ValueError):
            aliases = []
        return ' '.join([company['name'], company['id']] + aliases).casefold()
    selected = [company for company in companies if term.casefold() in searchable(company)]
    offset = min(context.get('company_offset', 0), max(0, (len(selected) - 1) // COMPANY_PAGE_SIZE * COMPANY_PAGE_SIZE))
    context = {**({'company_q': term} if term else {}), **({'company_offset': offset} if offset else {})}
    counts, latest, uncertain, active = _company_observations(market)
    body = ('<section aria-labelledby="company-directory-title"><div class="section-heading"><div>'
            '<h2 id="company-directory-title">先选择一家证券主体</h2><p class="cell-meta">'
            + str(len(companies)) + ' 家已配置证券主体 · 公司名称、代码及已存申办方均可搜索</p></div>'
            '<a class="text-link" href="/coverage">查看未关联候选与缺口 →</a></div>'
            '<section class="card filter-toolbar"><form method="get" action="/assets" aria-label="搜索公司入口">'
            '<div class="form-grid"><div class="field field-search"><label for="asset-company-search">公司名称、证券代码或申办方</label>'
            '<input id="asset-company-search" type="search" name="company_q" value="' + e(term)
            + '" placeholder="例如 恒瑞、US:PFE、Pfizer"></div></div><div class="filter-actions">'
            '<button type="submit">查找公司</button><a class="text-action" href="/assets">显示全部公司</a></div></form></section>'
            '<p class="data-note">公司入口只读取当前已存记录。已存登记试验数、最新查询尝试与排队状态分别展示，'
            '缺失批次保留未知；此处不代表全市场完整管线。<a href="/coverage#collection-queue">查看采集任务与错误 →</a></p>')
    if not selected:
        return body + ('<div class="empty"><h3>' + ('没有匹配的已配置公司' if companies else '尚未配置可浏览公司')
                       + '</h3><p>可调整搜索词，或到覆盖页面核对尚未建立申办方关联的主体。没有入口不表示公司没有管线。</p></div></section>')
    body += ('<p class="data-note">匹配 ' + str(len(selected)) + ' 家，显示第 ' + str(offset + 1) + '–'
             + str(min(offset + COMPANY_PAGE_SIZE, len(selected))) + ' 家，每页最多 ' + str(COMPANY_PAGE_SIZE)
             + ' 家。</p><div class="source-grid company-directory-grid">')
    for company in selected[offset:offset + COMPANY_PAGE_SIZE]:
        cid = company['id']
        count, attempt = counts.get(cid, 0), latest.get(cid)
        unknown = cid in uncertain
        label = '查询时间待核实' if unknown else ATTEMPTS.get(attempt['status'], '查询状态待核实') if attempt else '尚未记录查询'
        target = '/assets?' + urlencode({'company': cid, **context})
        body += ('<article class="card source-card company-entry-card" data-company-id="' + e(cid)
                 + '"><div class="source-card-head"><span class="cell-meta">' + e(cid)
                 + '</span><span class="status-badge">' + e(label) + '</span></div><h3><a href="' + e(target)
                 + '">' + e(company['name']) + '</a></h3><p class="source-count"><strong>' + f'{count:,}'
                 + '</strong> 项已存登记试验</p><dl class="source-meta">')
        if cid in active:
            body += _fact('当前队列', e(ATTEMPTS.get(active[cid], '队列状态未知')))
        if attempt and not unknown:
            body += _fact('查询完成时间' if attempt.get('finished') else '查询开始时间',
                          _timestamp(attempt.get('finished') or attempt.get('started')))
        else:
            body += _fact('最近查询时间', '未知')
        body += '</dl><p class="data-note">' + e(_collection_note(count, attempt, unknown)) + '</p>'
        if attempt and attempt.get('error') and not unknown:
            body += '<details class="source-details"><summary>查看最近查询原因</summary><p class="body">' + e(attempt['error']) + '</p></details>'
        body += ('<div class="primary-actions"><a class="button small" href="' + e(target)
                 + '">研究登记用药</a><a class="text-link" href="/market?' + e(urlencode({'company': cid}))
                 + '">登记记录 →</a></div></article>')
    body += '</div>'
    if offset or offset + COMPANY_PAGE_SIZE < len(selected):
        body += '<nav class="pagination" aria-label="公司入口分页">'
        for present, step, label, rel in [(offset > 0, max(0, offset - COMPANY_PAGE_SIZE), '← 上一页公司', 'prev'),
                                           (offset + COMPANY_PAGE_SIZE < len(selected), offset + COMPANY_PAGE_SIZE, '下一页公司 →', 'next')]:
            if present:
                params = {**({'company_q': term} if term else {}), 'company_offset': step}
                body += '<a class="button secondary small" rel="' + rel + '" href="/assets?' + e(urlencode(params)) + '">' + label + '</a>'
        body += '</nav>'
    return body + '</section>'


def render(app, q):
    market = Market(app.workspace, app.state)
    companies = sorted(market.rows('companies'), key=lambda company: company['id'])
    cid, query, role = q.get('company', ''), q.get('q', '').strip(), q.get('role', '')
    if role and role not in ROLES:
        raise ValueError('未知的用药角色')
    selected_company = next((company for company in companies if company['id'] == cid), None)
    if cid and selected_company is None:
        raise ValueError('公司尚未配置')
    context = _company_context(q)
    if cid:
        actions = ('<a class="button secondary" href="/market?' + e(urlencode({'company': cid}))
                   + '">同公司登记记录</a><a class="button secondary" href="/disclosures?'
                   + e(urlencode({'company': cid})) + '">同公司官方披露与缺口</a>'
                   '<a class="text-link" href="/assets' + ('?' + e(urlencode(context)) if context else '') + '">返回公司入口</a>')
    else:
        actions = '<a class="button secondary" href="/coverage">覆盖进度与缺口</a><a class="button secondary" href="/market">临床登记检索</a>'
    body = ('<header class="page-header"><div><div class="eyebrow">CLINICAL ASSET RESEARCH</div>'
            '<h1>临床资产研究</h1><p class="lead">从登记用药名称进入试验，逐项核对用药角色、阶段与来源依据。</p>'
            + ('<p class="cell-meta">当前证券：' + e(selected_company['name']) + ' · ' + e(cid) + '</p>' if cid else '')
            + '</div><div class="primary-actions">' + actions + '</div></header>'
            '<p class="notice">来源为 ClinicalTrials.gov 的已存登记观测。名称按原文精确归并，研发代号与通用名可能分开列出。'
            '名称数量不能当作独立资产数量；实验组用药可能包含标准治疗及联合用药，药物权益与授权地域均待核对。试验阶段保留原文，不推断药物最高研发阶段。</p>')
    if not cid:
        body += _company_directory(market, companies, q)
        body += '<details class="card secondary-panel"><summary>按公司代码直接选择并筛选登记名称</summary>'
    body += ('<section class="card filter-toolbar asset-filter" aria-labelledby="asset-filter-title">'
             '<div class="section-heading"><h2 id="asset-filter-title">查找候选药物关联</h2>'
             '<span class="cell-meta">只读查询 · ' + str(len(companies)) + ' 家已配置证券主体</span></div>'
             '<form method="get" action="/assets" aria-labelledby="asset-filter-title">'
             + ''.join('<input type="hidden" name="' + key + '" value="' + e(value) + '">' for key, value in context.items())
             +
             '<div class="form-grid" role="group" aria-label="候选药物筛选条件">'
             '<div class="field field-company"><label for="asset-company">公司 / 证券代码</label>'
             '<select id="asset-company" name="company" required><option value="">请选择公司</option>')
    for company in companies:
        body += ('<option value="' + e(company['id']) + '"' + (' selected' if cid == company['id'] else '')
                 + '>' + e(company['name'] + ' · ' + company['id']) + '</option>')
    body += ('</select></div><div class="field field-search"><label for="asset-query">药物名称、试验编号或适应症</label>'
             '<input id="asset-query" type="search" name="q" value="' + e(query)
             + '" placeholder="研发代号、NCT 编号、indication"></div>'
             '<div class="field"><label for="asset-role">用药角色</label><select id="asset-role" name="role">'
             '<option value="">全部角色</option>'
             + ''.join('<option value="' + key + '"' + (' selected' if role == key else '') + '>' + label + '</option>'
                       for key, label in ROLES.items()) + '</select></div></div><div class="filter-actions">'
             '<button type="submit">查看关联</button><a class="text-action" href="/assets">清除筛选</a>'
             '</div></form></section>')
    if not cid:
        return body + '</details>'

    assets = build_assets(market, cid)
    folded = query.casefold()
    selected = [asset for asset in assets if (not role or asset['role_status'] == role)
                and (not folded or folded in ' '.join([asset['drug_name']] + asset['trials'] + asset['conditions']).casefold())]
    try:
        offset = max(0, int(q.get('offset', 0))) // PAGE_SIZE * PAGE_SIZE
    except (ValueError, TypeError):
        offset = 0
    # A saved URL still produces a useful last page when the result set shrinks.
    offset = min(offset, max(0, (len(selected) - 1) // PAGE_SIZE * PAGE_SIZE))
    trial_count = len({nct for asset in selected for nct in asset['trials']})
    body += ('<section aria-labelledby="asset-results-title"><div class="section-heading">'
             '<div><h2 id="asset-results-title">' + e(selected_company['name']) + '</h2>'
             '<p class="cell-meta">' + e(cid) + ' · ClinicalTrials.gov 登记观测</p></div>'
             '<a class="text-link" href="/market?' + e(urlencode({'company': cid})) + '">查看该公司登记记录 →</a></div>'
             '<div class="metric-row" aria-label="当前筛选范围">'
             '<div class="metric-card"><span class="muted">匹配名称</span><div class="metric">' + f'{len(selected):,}'
             + '</div><small>公司全部 ' + f'{len(assets):,}' + ' 个登记名称中的筛选结果</small></div>'
             '<div class="metric-card"><span class="muted">关联登记试验</span><div class="metric">' + f'{trial_count:,}'
             + '</div><small>当前筛选内按 NCT 编号去重</small></div>'
             '<div class="metric-card"><span class="muted">药物权益</span><div class="metric">待核对</div>'
             '<small>登记角色不构成所有权或授权证据</small></div></div>')
    if query or role:
        body += '<div class="filter-chips" aria-label="当前筛选">'
        if query:
            body += '<span>关键词：' + e(query) + '</span>'
        if role:
            body += '<span>用药角色：' + e(ROLES[role]) + '</span>'
        body += '</div>'
    if selected:
        body += ('<p class="data-note">显示第 ' + str(offset + 1) + '–' + str(min(offset + PAGE_SIZE, len(selected)))
                 + ' 个名称，共 ' + str(len(selected)) + ' 个。展开卡片查看每项试验的原文、日期与校验值。</p>'
                 '<div class="asset-grid">' + ''.join(_card(asset) for asset in selected[offset:offset + PAGE_SIZE]) + '</div>')
    else:
        body += ('<div class="empty"><h3>没有匹配的登记名称关联</h3><p>尚未采集、查询零条或分页未完成都可能导致此处为空，'
                 '不能据此判定公司没有研发管线。<a href="/coverage">查看覆盖状态与缺口 →</a></p></div>')
    filters = {'company': cid, 'q': query, 'role': role, **context}
    if offset or offset + PAGE_SIZE < len(selected):
        body += '<nav class="pagination" aria-label="候选药物结果分页">'
        if offset:
            body += '<a class="button secondary small" rel="prev" href="/assets?' + e(urlencode({**filters, 'offset': max(0, offset - PAGE_SIZE)})) + '">← 上一页</a>'
        body += '<span class="cell-meta">每页最多 ' + str(PAGE_SIZE) + ' 个名称</span>'
        if offset + PAGE_SIZE < len(selected):
            body += '<a class="button secondary small" rel="next" href="/assets?' + e(urlencode({**filters, 'offset': offset + PAGE_SIZE})) + '">下一页 →</a>'
        body += '</nav>'
    return body + '</section>'
