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
MARKETS = {'US': '美股', 'A': 'A 股', 'HK': '港股'}
ROLES = {'experimental_arm': '在实验组中出现', 'comparator_only': '仅在比较组中出现',
         'unresolved': '用药角色待核对'}
FRESH = {'seen_in_latest': '最新完整抓取中可见', 'not_seen_in_latest': '仅保留于历史观察',
         'unknown': '批次关联尚未建立'}
SORTS = {'name': '名称 A–Z', 'trials': '关联登记试验数（多→少）'}
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


def _source(source, company=None):
    freshness = FRESH.get(source.get('freshness'), FRESH['unknown'])
    role = ROLES.get(source.get('role_status'), ROLES['unresolved'])
    preview = (str(source.get('status') or '状态未记载') + ' · '
               + (' / '.join(str(value) for value in source.get('phases', [])) or '阶段未记载'))
    if len(preview) > 100:
        preview = preview[:100] + '…'
    parts = ['<details class="asset-trial-details"><summary>', e(source.get('nct')), ' · ', e(preview), '</summary>',
             '<section class="asset-source"><h4>', _source_link(source), '</h4>',
             '<p class="cell-meta">上方链接为外部当前页面，内容可能已更新；以下保留本地登记观察。</p>',
             ('<p><a class="text-link" href="/market?' + e(urlencode({'company': company, 'q': source.get('nct', '')}))
              + '">查看本地已存观察 →</a> · <a class="text-link" href="/market?'
              + e(urlencode({'company': company, 'q': source.get('nct', '')}))
              + '#market-changes">核对该试验登记变更 →</a></p>') if company else '',
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


def _card(asset, location=''):
    role = ROLES.get(asset.get('role_status'), ROLES['unresolved'])
    freshness = FRESH.get(asset.get('freshness'), FRESH['unknown'])
    retained = len(asset.get('retained_trials', []))
    history = ('<p class="data-note">其中 ' + str(retained)
               + ' 项试验仅保留于历史观察；未在最近完整批次中出现不代表试验终止。</p>') if retained else ''
    return ('<article id="asset-' + e(asset['id']) + '" class="card asset-card" aria-labelledby="asset-name-' + e(asset['id']) + '"><div class="asset-card-head"><div><div class="eyebrow">登记用药名称</div><h3 id="asset-name-' + e(asset['id']) + '">'
            + e(asset['drug_name']) + '</h3></div><span class="status-badge caution">权益待核对</span></div>'
            + '<div class="asset-badges"><span class="status-badge">' + e(role)
            + '</span><span class="status-badge">' + str(len(asset['trials'])) + ' 项登记试验</span></div>'
            + '<dl class="asset-facts">'
            + _fact('试验阶段原文', _joined(asset['phases'], '未分期或未记载'))
            + _fact('适应症原文', _conditions(asset['conditions']))
            + _fact('来源更新时间', _timestamp(asset.get('source_updated')))
            + _fact('批次可见性', e(freshness)) + '</dl>' + history
            + ('<p class="cell-meta"><a href="' + e(location + '#asset-' + asset['id']) + '">定位此名称卡</a></p>' if location else '')
            + '<details class="asset-evidence" id="asset-evidence-' + e(asset['id']) + '"><summary>逐项核对 ' + str(len(asset['sources']))
            + ' 条试验依据 · ClinicalTrials.gov</summary>'
            + ''.join(_source(source, asset['company']) for source in asset['sources']) + '</details></article>')


def _company_context(q):
    result = {}
    market = q.get('company_market', '')
    if market and market not in MARKETS:
        raise ValueError('未知的公司入口市场')
    if market:
        result['company_market'] = market
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


def _company_observations(market, cid=None):
    """One read snapshot; stored observations and latest attempts stay separate."""
    with market.db() as db:
        db.execute('BEGIN')
        parameters = (cid,) if cid is not None else ()
        counts = {row['company']: row['n'] for row in db.execute(
            'SELECT company,count(DISTINCT nct) AS n FROM trials'
            + (' WHERE company=?' if cid is not None else '') + ' GROUP BY company', parameters)}
        history = [dict(row) for row in db.execute(
            "SELECT * FROM runs WHERE source='ClinicalTrials.gov'"
            + (' AND company=?' if cid is not None else '') + ' ORDER BY started DESC,id DESC', parameters)]
        has_jobs = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='collection_jobs'").fetchone()
        active = {row['reference']: row['status'] for row in db.execute(
            "SELECT reference,status FROM collection_jobs WHERE kind='company' AND status IN ('queued','running','retry_wait')"
            + (' AND reference=?' if cid is not None else ''), parameters)} if has_jobs else {}
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
    market_filter = context.get('company_market', '')
    def searchable(company):
        try:
            aliases = json.loads(company.get('aliases', '[]'))
            aliases = [value for value in aliases if isinstance(value, str)] if isinstance(aliases, list) else []
        except (TypeError, ValueError):
            aliases = []
        return ' '.join([company['name'], company['id']] + aliases).casefold()
    selected = [company for company in companies if (not market_filter or company['market'] == market_filter)
                and term.casefold() in searchable(company)]
    offset = min(context.get('company_offset', 0), max(0, (len(selected) - 1) // COMPANY_PAGE_SIZE * COMPANY_PAGE_SIZE))
    context = {**({'company_market': market_filter} if market_filter else {}),
               **({'company_q': term} if term else {}), **({'company_offset': offset} if offset else {})}
    counts, latest, uncertain, active = _company_observations(market)
    body = ('<section aria-labelledby="company-directory-title"><div class="section-heading"><div>'
            '<h2 id="company-directory-title">先选择一家证券主体</h2><p class="cell-meta">'
            + str(len(companies)) + ' 家已配置证券主体 · 公司名称、代码及已存申办方均可搜索</p></div>'
            '<a class="text-link" href="/coverage">查看未关联候选与缺口 →</a></div>'
            '<section class="card filter-toolbar company-directory-filter"><form method="get" action="/assets" aria-label="搜索公司入口">'
            '<div class="form-grid"><div class="field field-search"><label for="asset-company-search">公司名称、证券代码或申办方</label>'
            '<input id="asset-company-search" type="search" name="company_q" value="' + e(term)
            + '" placeholder="例如 恒瑞、US:PFE、Pfizer"></div><div class="field"><label for="asset-company-market">上市市场</label>'
            '<select id="asset-company-market" name="company_market"><option value="">全部市场</option>'
            + ''.join('<option value="' + key + '"' + (' selected' if key == market_filter else '') + '>' + label + '</option>' for key, label in MARKETS.items())
            + '</select></div></div><div class="filter-actions">'
            '<button type="submit">查找公司</button><a class="text-action" href="/assets">显示全部公司</a></div></form></section>'
            '<p class="data-note">公司入口只读取当前已存记录。已存登记试验数、最新查询尝试与排队状态分别展示，'
            '缺失批次保留未知；此处不代表全市场完整管线。<a href="/coverage#collection-queue">查看采集任务与错误 →</a></p>')
    if term or market_filter:
        body += '<nav class="filter-chips" aria-label="公司入口筛选">'
        if term:
            body += '<a class="asset-clear-chip" href="' + e(_filter_url({'company_market': market_filter})) + '">移除公司搜索：' + e(term) + ' ×</a>'
        if market_filter:
            body += '<a class="asset-clear-chip" href="' + e(_filter_url({'company_q': term})) + '">移除市场：' + e(MARKETS[market_filter]) + ' ×</a>'
        body += '</nav>'
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
        body += ('</dl><details class="source-details company-state-details"><summary>'
                 + ('查询失败或不完整 · 查看原因与范围' if attempt and attempt.get('status') in ('failed', 'partial', 'interrupted', 'cancelled') else '核对查询范围与记录边界')
                 + '</summary><p class="data-note">' + e(_collection_note(count, attempt, unknown)) + '</p>')
        if attempt and attempt.get('error') and not unknown:
            body += '<p class="body">最近查询原因：' + e(attempt['error']) + '</p>'
        body += '</details>'
        body += ('<div class="primary-actions"><a class="button small" href="' + e(target)
                 + '">研究登记用药</a><a class="text-link" href="/market?' + e(urlencode({'company': cid}))
                 + '">登记记录 →</a></div></article>')
    body += '</div>'
    if offset or offset + COMPANY_PAGE_SIZE < len(selected):
        body += '<nav class="pagination" aria-label="公司入口分页">'
        for present, step, label, rel in [(offset > 0, max(0, offset - COMPANY_PAGE_SIZE), '← 上一页公司', 'prev'),
                                           (offset + COMPANY_PAGE_SIZE < len(selected), offset + COMPANY_PAGE_SIZE, '下一页公司 →', 'next')]:
            if present:
                params = {**({'company_market': market_filter} if market_filter else {}),
                          **({'company_q': term} if term else {}), 'company_offset': step}
                body += '<a class="button secondary small" rel="' + rel + '" href="/assets?' + e(urlencode(params)) + '">' + label + '</a>'
        body += '</nav>'
    return body + '</section>'


def _company_research_context(market, cid):
    counts, latest, uncertain, active = _company_observations(market, cid)
    count, attempt, unknown = counts.get(cid, 0), latest.get(cid), cid in uncertain
    status = '查询时间待核实' if unknown else ATTEMPTS.get(attempt['status'], '查询状态待核实') if attempt else '尚未记录查询'
    when = (_timestamp(attempt.get('finished') or attempt.get('started')) if attempt and not unknown else '未知')
    body = ('<section id="asset-collection" tabindex="-1" class="card asset-context-panel" aria-labelledby="asset-context-title">'
            '<div class="section-heading"><h2 id="asset-context-title">采集状态与研究边界</h2>'
            '<a class="text-link" href="/coverage#collection-queue">采集队列与错误 →</a></div>'
            '<dl class="asset-context-grid"><div><dt>当前已存登记试验</dt><dd><strong>'
            + f'{count:,}' + '</strong> 项 NCT · 按编号去重</dd></div>'
            '<div><dt>最近查询尝试</dt><dd><span class="status-badge">' + e(status)
            + '</span><p class="cell-meta">' + when + '</p></dd></div>'
            '<div><dt>当前公司采集队列</dt><dd>'
            + e(ATTEMPTS.get(active[cid], '队列状态未知') if cid in active else '未记录活跃任务')
            + '<p><a class="text-link" href="#asset-results">进入名称与试验依据 ↓</a></p></dd></div></dl>'
            '<p class="data-note">' + e(_collection_note(count, attempt, unknown)) + '</p>'
            '<details class="source-details"><summary>查看完整性、科学审查与权益边界</summary>'
            '<dl class="source-meta">' + _fact('记录完整性', '本页未重新校验来源原件与校验值；展开试验可追溯已记录的批次和 SHA-256。')
            + _fact('科学审查', '尚未在此页判定；登记观察不能直接作为疗效结论。')
            + _fact('人工签署与权益', '本页不确认人工签署、所有权或授权地域，均需另行核对。') + '</dl></details>')
    if attempt and attempt.get('error') and not unknown:
        body += ('<details class="source-details"><summary>查看最近查询原因</summary><p class="body">'
                 + e(attempt['error']) + '</p></details>')
    return body + '</section>'


def _filter_url(filters, **changes):
    """All links keep the current company/context; changed filters start at page one."""
    params = {**filters, **changes}
    encoded = urlencode({key: value for key, value in params.items() if value != '' and value is not None})
    return '/assets' + ('?' + encoded if encoded else '')


def _pagination(filters, offset, total, position):
    """Render identical filtered destinations at either end of the name list."""
    if not total:
        return ''
    page, pages = offset // PAGE_SIZE + 1, (total + PAGE_SIZE - 1) // PAGE_SIZE
    body = ('<nav class="pagination asset-pagination" id="asset-pagination-' + position
            + '" aria-label="登记名称分页（' + ('顶部' if position == 'top' else '底部') + '）">')
    if offset:
        body += ('<a class="button secondary small" rel="prev" href="/assets?'
                 + e(urlencode({**filters, 'offset': max(0, offset - PAGE_SIZE)}))
                 + '#asset-results">← 上一页</a>')
    body += ('<span class="cell-meta" aria-current="page">第 ' + str(page) + ' / ' + str(pages)
             + ' 页</span><span class="cell-meta">每页最多 ' + str(PAGE_SIZE) + ' 个名称</span>')
    if offset + PAGE_SIZE < total:
        body += ('<a class="button secondary small" rel="next" href="/assets?'
                 + e(urlencode({**filters, 'offset': offset + PAGE_SIZE}))
                 + '#asset-results">下一页 →</a>')
    if position == 'bottom':
        body += '<a class="text-link" href="#asset-filters">返回筛选 ↑</a>'
    return body + '</nav>'


def render(app, q):
    market = Market(app.workspace, app.state)
    companies = sorted(market.rows('companies'), key=lambda company: company['id'])
    cid, query, role = q.get('company', ''), q.get('q', '').strip(), q.get('role', '')
    phase, freshness, sort = q.get('phase', ''), q.get('freshness', ''), q.get('sort', 'name')
    if role and role not in ROLES:
        raise ValueError('未知的用药角色')
    if freshness and freshness not in FRESH:
        raise ValueError('未知的批次可见性')
    if sort not in SORTS:
        raise ValueError('未知的排序方式')
    selected_company = next((company for company in companies if company['id'] == cid), None)
    if cid and selected_company is None:
        raise ValueError('公司尚未配置')
    context = _company_context(q)
    assets = build_assets(market, cid) if cid else []
    phases = sorted({value for asset in assets for value in asset['phases']})
    filters = {'company': cid, 'q': query, 'role': role, **context,
               **({'phase': phase} if phase else {}), **({'freshness': freshness} if freshness else {}),
               **({'sort': sort} if sort != 'name' else {})}
    clear_url = _filter_url({'company': cid, **context}) if cid else '/assets'
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
    else:
        body += ('<nav class="asset-page-nav" aria-label="资产研究页内导航">'
                 '<a href="#asset-filters">筛选登记名称</a><a href="#asset-results">名称与试验依据</a>'
                 '<a href="#asset-collection">采集状态</a></nav>')
        body += _company_research_context(market, cid)
    body += ('<section id="asset-filters" tabindex="-1" class="card filter-toolbar asset-filter" aria-labelledby="asset-filter-title">'
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
                       for key, label in ROLES.items()) + '</select></div></div>')
    if cid:
        body += ('<details class="asset-filter-advanced"' + (' open' if phase or freshness or sort != 'name' else '')
                 + '><summary>原文阶段、批次与排序</summary><div class="form-grid">'
                 '<div class="field"><label for="asset-phase">试验阶段原文（任一命中）</label><select id="asset-phase" name="phase">'
                 '<option value="">全部原文阶段</option>'
                 + ''.join('<option value="' + e(value) + '"' + (' selected' if phase == value else '') + '>' + e(value) + '</option>' for value in phases)
                 + ('<option value="' + e(phase) + '" selected>' + e(phase) + ' · 当前来源未见</option>' if phase and phase not in phases else '')
                 + '</select></div><div class="field"><label for="asset-freshness">名称的批次可见性</label>'
                 '<select id="asset-freshness" name="freshness"><option value="">全部批次</option>'
                 + ''.join('<option value="' + key + '"' + (' selected' if freshness == key else '') + '>' + label + '</option>' for key, label in FRESH.items())
                 + '</select></div><div class="field"><label for="asset-sort">结果排序</label><select id="asset-sort" name="sort">'
                 + ''.join('<option value="' + key + '"' + (' selected' if sort == key else '') + '>' + label + '</option>' for key, label in SORTS.items())
                 + '</select></div></div><p class="data-note">筛选命中名称后，卡片仍保留该名称的全部试验依据；混合批次以至少一项在最新完整批次中可见为准。试验数排序仅帮助浏览。</p></details>')
    body += ('<div class="filter-actions"><button type="submit">查看关联</button><a class="text-action" href="'
             + e(clear_url) + '">清除筛选</a></div></form></section>')
    if not cid:
        return body + '</details>'

    folded = query.casefold()
    matching = [asset for asset in assets if (not phase or phase in asset['phases'])
                and (not freshness or asset['freshness'] == freshness)
                and (not folded or folded in ' '.join([asset['drug_name']] + asset['trials'] + asset['conditions']).casefold())]
    selected = [asset for asset in matching if not role or asset['role_status'] == role]
    if sort == 'trials':
        selected.sort(key=lambda asset: (-len(asset['trials']), asset['normalized_name'], asset['id']))
    try:
        offset = max(0, int(q.get('offset', 0))) // PAGE_SIZE * PAGE_SIZE
    except (ValueError, TypeError):
        offset = 0
    # A saved URL still produces a useful last page when the result set shrinks.
    offset = min(offset, max(0, (len(selected) - 1) // PAGE_SIZE * PAGE_SIZE))
    trial_count = len({nct for asset in selected for nct in asset['trials']})
    body += ('<section id="asset-results" tabindex="-1" aria-labelledby="asset-results-title"><div class="section-heading">'
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
    body += '<nav class="asset-role-tabs" aria-label="按用药角色切换">'
    for key, label in [('', '全部角色'), *ROLES.items()]:
        count = len(matching) if not key else sum(asset['role_status'] == key for asset in matching)
        body += ('<a href="' + e(_filter_url(filters, role=key)) + '"' + (' aria-current="page"' if role == key else '')
                 + '>' + e(label) + ' <span class="cell-meta">' + str(count) + '</span></a>')
    body += '</nav>'
    chips = [('q', query, '关键词：' + query), ('role', role, '用药角色：' + ROLES.get(role, '')),
             ('phase', phase, '阶段原文：' + phase), ('freshness', freshness, '批次：' + FRESH.get(freshness, '')),
             ('sort', sort if sort != 'name' else '', '排序：' + SORTS[sort])]
    if any(value for _, value, _ in chips):
        body += '<div class="filter-chips" aria-label="当前筛选">'
        for key, value, label in chips:
            if value:
                body += ('<a class="asset-clear-chip" href="' + e(_filter_url(filters, **{key: ''}))
                         + '" aria-label="' + e('移除' + label) + '">' + e(label) + ' ×</a>')
        body += '</div>'
    if selected:
        body += ('<div class="asset-result-toolbar"><p class="data-note">显示第 ' + str(offset + 1) + '–' + str(min(offset + PAGE_SIZE, len(selected)))
                 + ' 个名称，共 ' + str(len(selected)) + ' 个。展开卡片查看每项试验的原文、日期与校验值。</p>'
                 '<span class="cell-meta">' + e(SORTS[sort]) + ' · 本地已存观察</span></div>'
                 + _pagination(filters, offset, len(selected), 'top')
                 + '<div class="asset-grid">' + ''.join(_card(asset, _filter_url(filters, offset=offset)) for asset in selected[offset:offset + PAGE_SIZE]) + '</div>')
    elif assets:
        body += ('<div class="empty"><h3>当前筛选无匹配</h3><p>该公司已有 ' + str(len(assets))
                 + ' 个登记名称，当前条件未命中。<a href="' + e(clear_url) + '">清除该公司筛选 →</a>'
                 '</p><p class="cell-meta">筛选结果为空不能据此判定公司没有研发管线。</p></div>')
    else:
        body += ('<div class="empty"><h3>没有匹配的登记名称关联</h3><p>尚未采集、查询零条或分页未完成都可能导致此处为空，'
                 '也可能只有未纳入此视图的干预类型。不能据此判定公司没有研发管线。'
                 '<a href="/coverage#collection-queue">查看采集状态与缺口 →</a></p>'
                 '<a class="text-link" href="/market?' + e(urlencode({'company': cid})) + '">核对该公司全部已存登记记录 →</a></div>')
    body += _pagination(filters, offset, len(selected), 'bottom')
    return body + '</section>'
