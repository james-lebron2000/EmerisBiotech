"""Searchable clinical observations with visible provenance and collection health."""
import html
import json
import hashlib
import re
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlencode, urlsplit

from .market import Market
from .market_sources import URLS

SOURCES = {'US': 'SEC 美股名录', 'HK': 'HKEX 港股证券', 'SH': '上交所主板 A 股', 'STAR': '上交所科创板', 'SZ': '深交所 A 股', 'BSE': '北交所 A 股', 'USNASDAQ': 'Nasdaq 上市证券', 'USOTHER': 'Nasdaq Trader 其他交易所证券'}
STATES = {'queued': '已排队', 'running': '采集中', 'succeeded': '查询完成', 'failed': '失败', 'partial': '分页未完成', 'interrupted': '中断', 'cancelled': '已取消'}
PHASES = {'PHASE1': '一期', 'PHASE2': '二期', 'PHASE3': '三期', 'PHASE4': '四期', 'EARLY_PHASE1': '早期一期'}
TRIAL_STATES = {'RECRUITING': '招募中', 'ACTIVE_NOT_RECRUITING': '进行中，停止招募', 'COMPLETED': '已完成', 'TERMINATED': '已终止', 'NOT_YET_RECRUITING': '尚未招募', 'WITHDRAWN': '已撤回', 'SUSPENDED': '暂停', 'UNKNOWN': '未知'}


def e(value):
    return html.escape(str(value if value is not None else '未知'), quote=True)


def _timestamp(value):
    """Keep the exact source timestamp available without crowding the table."""
    if not value:
        return '<span class="cell-meta">未知</span>'
    raw = str(value)
    try:
        # A registry date has day precision; never add an invented midnight.
        compact = date.fromisoformat(raw).strftime('%Y-%m-%d')
    except ValueError:
        try:
            parsed = datetime.fromisoformat(raw.replace('Z', '+00:00'))
        except ValueError:
            return '<span class="cell-meta" title="' + e(raw) + '">' + e(raw) + '</span>'
        if parsed.tzinfo is not None and parsed.utcoffset() is not None:
            compact = parsed.astimezone(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M') + ' UTC+8'
        else:
            compact = parsed.strftime('%Y-%m-%d %H:%M') + ' · 时区未提供'
    return '<time datetime="' + e(raw) + '" title="' + e(raw) + '">' + compact + '</time>'


def _source_link(url, label, nct=None):
    # Imported metadata remains untrusted even when it is rendered as a link.
    try:
        parsed = urlsplit(str(url))
        safe = parsed.scheme == 'https' and bool(parsed.hostname) and not parsed.username and not parsed.password
        if nct is not None:
            safe = (safe and re.fullmatch(r'NCT[0-9]{8}', str(nct)) and parsed.netloc == 'clinicaltrials.gov'
                    and parsed.path == '/study/' + str(nct) and not parsed.query and not parsed.fragment)
    except ValueError:
        safe = False
    if safe:
        return '<a class="source-link" target="_blank" rel="noopener noreferrer" href="' + e(url) + '">' + e(label) + ' ↗</a>'
    return '<span class="source-link" title="来源链接不可用：' + e(url) + '">' + e(label) + '</span>'


FILTER_KEYS = ('market', 'company', 'q', 'phase', 'status')


def _url(filters, **changes):
    params = {**filters, **changes}
    encoded = urlencode({key: params[key] for key in (*FILTER_KEYS, 'view', 'offset')
                         if params.get(key) is not None and params.get(key) != ''})
    return '/market' + ('?' + encoded if encoded else '')


def _trial_anchor(row):
    return 'market-trial-' + hashlib.sha256((row['company'] + ':' + row['nct']).encode()).hexdigest()[:20]


def _data(raw):
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        return {}


def _values(value):
    return value if isinstance(value, list) else []


def _trial_parts(data):
    names = [item.get('name') for item in _values(data.get('interventions'))
             if isinstance(item, dict) and isinstance(item.get('name'), str)]
    conditions = [str(value) for value in _values(data.get('conditions'))]
    phases = [str(value) for value in _values(data.get('phases'))]
    status = data.get('status') or 'UNKNOWN'
    return (' / '.join(names) or '未提供干预名称', ' / '.join(conditions) or '未提供适应症',
            ' / '.join(PHASES.get(value, value) for value in phases) or '未分期或未记载',
            TRIAL_STATES.get(str(status), str(status)))


def _trial_actions(row):
    params = {'company': row['company'], 'q': row['nct']}
    return ('<nav class="market-context-links" aria-label="本项试验研究路径"><a class="text-link" href="/assets?'
            + e(urlencode(params)) + '">研究关联用药 →</a><a class="text-link" href="' + e(_url(params))
            + '#market-changes">核对该试验登记变更 →</a></nav>')


def _trial_provenance(row, data):
    facts = [('登记申办方原文', data.get('sponsor')), ('来源更新时间', data.get('source_updated')),
             ('平台观测时间', row.get('observed')), ('阶段原文', ' / '.join(str(v) for v in _values(data.get('phases'))) or None),
             ('登记状态原文', data.get('status')), ('来源地址', data.get('source_url')), ('已记录 SHA-256', row.get('hash'))]
    unknown = ('<p class="notice">已存 JSON 为空或无法解析，字段保留未知。</p><pre>' + e(row.get('data')) + '</pre>') if not data else ''
    return ('<details class="provenance-details"><summary>原文、精确时间与校验记录</summary>' + unknown + '<dl>'
            + ''.join('<dt>' + label + '</dt><dd>' + e(value) + '</dd>' for label, value in facts)
            + '</dl><p class="cell-meta">这里展示已记录的来源与校验值，本页未重新验证原件。外部来源是当前页面，内容可能已经更新；登记状态不代表疗效结论。</p>'
            '<details><summary>完整干预原文与用药角色</summary><pre>'
            + e(json.dumps(data.get('interventions'), ensure_ascii=False, indent=2)) + '</pre></details></details>')


def _trial_card(row, names, filters, offset):
    data = _data(row['data'])
    interventions, conditions, phase, state = _trial_parts(data)
    local = _url(filters, view='cards', offset=offset) + '#' + _trial_anchor(row)
    return ('<article class="card market-trial-card" id="' + _trial_anchor(row) + '"><details><summary class="market-trial-summary">'
            '<div><span class="cell-meta">' + e(row['nct']) + ' · ' + e(names.get(row['company'], row['company']))
            + '</span><h3 class="trial-title">' + e(data.get('title') or '试验标题未记载') + '</h3></div>'
            '<div class="asset-badges"><span class="phase-label">' + e(phase) + '</span><span class="status-badge'
            + (' caution' if data.get('status') in ('TERMINATED', 'WITHDRAWN', 'SUSPENDED', 'UNKNOWN') else '')
            + '">' + e(state) + '</span></div></summary><div class="market-trial-body"><dl class="source-meta">'
            '<dt>证券主体</dt><dd>' + e(row['company']) + '</dd><dt>干预名称</dt><dd>' + e(interventions)
            + '</dd><dt>适应症原文</dt><dd>' + e(conditions) + '</dd><dt>来源更新</dt><dd>'
            + _timestamp(data.get('source_updated')) + '</dd><dt>平台观测</dt><dd>' + _timestamp(row.get('observed'))
            + '</dd></dl><div class="market-context-links">' + _source_link(data.get('source_url'), row['nct'], row['nct'])
            + '<a class="text-link" href="' + e(local) + '">定位此本地记录</a></div>'
            + _trial_actions(row) + _trial_provenance(row, data) + '</div></details></article>')


def _change_history(market, filters, names):
    # Filter before LIMIT. Only company/market and an exact NCT have defensible
    # history semantics; a current phase or name match is not an as-of replay.
    clauses, args = [], []
    if filters.get('market'):
        clauses.append('company LIKE ?'); args.append(filters['market'] + ':%')
    if filters.get('company'):
        clauses.append('company=?'); args.append(filters['company'])
    exact_nct = filters.get('q', '').upper()
    if re.fullmatch(r'NCT[0-9]{8}', exact_nct):
        clauses.append('nct=?'); args.append(exact_nct)
    else:
        exact_nct = ''
    where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
    with market.db() as db:
        total = db.execute('SELECT count(*) FROM changes' + where, args).fetchone()[0]
        changes = [dict(row) for row in db.execute('SELECT * FROM changes' + where + ' ORDER BY observed DESC,id DESC LIMIT 10', args)]
    scope = ' · '.join(value for value in [names.get(filters.get('company'), filters.get('company')),
                                         exact_nct, {'US': '美股', 'A': 'A 股', 'HK': '港股'}.get(filters.get('market'))] if value) or '全部已存证券'
    body = ('<section id="market-changes" tabindex="-1" aria-labelledby="market-changes-title"><div class="section-heading">'
            '<div><h2 id="market-changes-title">最近登记变化</h2><p class="cell-meta">范围：' + e(scope) + ' · 已存 '
            + str(total) + ' 条，最多展示最近 10 条</p></div><a class="text-link" href="#market-results">返回当前登记结果 ↑</a></div>'
            '<p class="data-note">按证券与明确 NCT 范围核对变化。普通关键词、阶段和登记状态未用于历史重建；这里保留已记录的差异，不代表完整事件历史。</p>')
    if not changes:
        body += '<div class="empty">当前证券／试验范围尚未记录登记变更；不能据此推断没有研发变化。</div>'
    for row in changes:
        before, after = _data(row['before_json']), _data(row['after_json'])
        keys = sorted(key for key in set(before) | set(after) if before.get(key) != after.get(key))
        first = not row['before_json']
        label = '首次观察' if first else ' / '.join(keys) or '差异字段尚未核实'
        body += ('<details class="card secondary-panel market-change-card"><summary><span>'
                 + e(names.get(row['company'], row['company']) + ' · ' + row['nct']) + '</span><span class="cell-meta"> · '
                 + e(label) + '</span></summary><dl class="source-meta"><dt>观测时间</dt><dd>' + _timestamp(row.get('observed'))
                 + '</dd><dt>采集运行</dt><dd><code>' + e(row.get('run_id')) + '</code></dd></dl>'
                 + _trial_actions(row) + '<details><summary>查看完整 before / after 原始差异</summary><pre>'
                 + e(json.dumps({'before': None if first else before, 'after': after}, ensure_ascii=False, indent=2))
                 + '</pre></details>')
        if (row['before_json'] and not before) or not after:
            body += '<p class="notice">部分历史内容为空或无法解析；保留原始存储文本，不能补成已知差异。</p><pre>' + e(row['before_json']) + '\n' + e(row['after_json']) + '</pre>'
        body += '</details>'
    return body + '</section>'


def render(app, q):
    m = Market(app.workspace, app.state)
    companies = m.rows('companies')
    runs = sorted(m.rows('runs'), key=lambda r: str(r.get('started') or ''), reverse=True)
    company_names = {c['id']: c['name'] for c in companies}

    def form(action, fields, label, button_class=''):
        return '<form method="post" action="' + action + '"><input type="hidden" name="token" value="' + e(app.token) + '">' + ''.join('<input type="hidden" name="' + e(k) + '" value="' + e(v) + '">' for k, v in fields.items()) + '<button' + (' class="' + e(button_class) + '"' if button_class else '') + '>' + e(label) + '</button></form>'

    with m.db() as db:
        unique_trials = db.execute('SELECT count(DISTINCT nct) FROM trials').fetchone()[0]
        source_counts = {r['source']: r['n'] for r in db.execute('SELECT source,count(*) n FROM listings GROUP BY source')}

    cid = q.get('company', '')
    asset_url = '/assets' + ('?' + urlencode({'company': cid}) if cid else '')
    body = '<header class="page-header"><div><div class="eyebrow">CLINICAL ASSET RESEARCH</div><h1>市场与管线雷达</h1><p class="lead">从证券主体追踪临床项目，核对药物、阶段及公开证据。</p></div><div class="primary-actions"><a class="button" href="/coverage">覆盖进度与缺口</a><a class="button secondary" href="' + e(asset_url) + '">候选药物关联</a></div></header>'
    metrics = [
        ('已配置证券', len(companies), '可检索的证券主体'),
        ('去重临床试验', unique_trials, '按 NCT 编号去重'),
        ('证券—试验关联', m.count('trials'), '包含多地上市的重复关联'),
        ('登记变更记录', m.count('changes'), '保留历次观察差异'),
    ]
    body += '<section class="grid metric-grid" aria-label="已采集范围">' + ''.join('<div class="card metric-card"><span class="metric-label">' + label + '</span><strong class="metric">' + f'{value:,}' + '</strong><span class="metric-detail cell-meta">' + detail + '</span></div>' for label, value, detail in metrics) + '</section>'
    body += '<p class="notice">公开登记包含历史试验、联合用药及对照治疗。公司归属、药物权益和完整管线仍需逐项核对；完成状态不表示临床成功。</p>'

    market = q.get('market', '')
    query = q.get('q', '').strip()
    view = q.get('view', 'table')
    if view not in ('table', 'cards'):
        raise ValueError('未知的登记展示方式')
    page_size = 50 if view == 'cards' else 200
    filters = {key: value for key, value in [('market', market), ('company', cid), ('q', query),
               ('phase', q.get('phase', '')), ('status', q.get('status', ''))] if value}
    if view != 'table':
        filters['view'] = view
    clear_url = _url({'company': cid, **({'view': view} if view != 'table' else {})})
    body += ('<nav class="market-context-links" aria-label="登记研究路径"><a href="#market-results">登记结果 ↓</a>'
             '<a href="#market-changes">' + ('同公司／试验登记变化' if cid or re.fullmatch(r'NCT[0-9]{8}', query.upper()) else '已存登记变化')
             + ' ↓</a><a href="/assets' + ('?' + e(urlencode({'company': cid})) if cid else '')
             + '">登记用药名称研究 →</a><a href="/records">研究运行与审查记录 →</a></nav>')
    body += '<section class="card filter-toolbar" aria-labelledby="market-filter-title"><div class="section-heading"><h2 id="market-filter-title">查找临床项目</h2><span class="cell-meta">可组合筛选，结果保留来源</span></div><form method="get" action="/market" aria-labelledby="market-filter-title"><div class="form-grid" role="group" aria-label="临床项目筛选条件">'
    body += '<div class="field field-search"><label for="market-query">公司、代码、药物或适应症</label><input id="market-query" name="q" value="' + e(query) + '" placeholder="恒瑞、PFE、PD-1、breast cancer"></div>'
    body += '<div class="field"><label for="market-filter">市场</label><select id="market-filter" name="market"><option value="">全部市场</option>' + ''.join('<option ' + ('selected ' if market == value else '') + 'value="' + value + '">' + label + '</option>' for value, label in [('US', '美股'), ('A', 'A 股'), ('HK', '港股')]) + '</select></div>'
    body += '<div class="field field-company"><label for="market-company">证券主体</label><select id="market-company" name="company"><option value="">全部已配置公司</option>' + ''.join('<option ' + ('selected ' if cid == c['id'] else '') + 'value="' + e(c['id']) + '">' + e(c['name'] + ' · ' + c['id']) + '</option>' for c in sorted(companies, key=lambda c: c['id'])) + '</select></div>'
    body += '<div class="field"><label for="market-phase">临床阶段</label><select id="market-phase" name="phase"><option value="">全部阶段</option>' + ''.join('<option ' + ('selected ' if q.get('phase') == value else '') + 'value="' + value + '">' + label + '</option>' for value, label in PHASES.items()) + '</select></div>'
    body += '<div class="field"><label for="market-status">登记状态</label><select id="market-status" name="status"><option value="">全部状态</option>' + ''.join('<option ' + ('selected ' if q.get('status') == value else '') + 'value="' + value + '">' + label + '</option>' for value, label in TRIAL_STATES.items()) + '</select></div></div>'
    if view != 'table':
        body += '<input type="hidden" name="view" value="cards">'
    body += '<div class="form-actions"><button type="submit">检索项目</button><a class="text-action" href="' + e(clear_url) + '">清除筛选' + ('（保留公司）' if cid else '') + '</a></div></form></section>'

    try:
        offset = max(0, int(q.get('offset', '0'))) // page_size * page_size
    except (ValueError, TypeError):
        offset = 0
    rows, total = m.search_trials(market, cid, query, q.get('phase', ''), q.get('status', ''), offset=offset)
    bounded = min(offset, max(0, (total - 1) // page_size * page_size))
    if bounded != offset:
        offset = bounded
        rows, total = m.search_trials(market, cid, query, q.get('phase', ''), q.get('status', ''), offset=offset)
    rows = rows[:page_size]
    result_range = f'{offset + 1:,}–{offset + len(rows):,}' if rows else '0'
    body += '<section id="market-results" tabindex="-1" class="card results-card" aria-labelledby="market-results-title"><div class="section-heading"><div><h2 id="market-results-title">临床登记结果 <span class="result-count">' + f'{total:,}' + '</span></h2><p class="cell-meta result-status" role="status">显示 ' + result_range + ' 条，共 ' + f'{total:,}' + ' 条证券—试验关联</p></div>' + form('/market/export', {key: q[key] for key in FILTER_KEYS if q.get(key)}, '导出筛选结果 CSV', 'secondary') + '</div>'
    body += '<div class="market-result-toolbar"><p class="cell-meta">每页 ' + str(page_size) + ' 条，按来源更新日期排序。多地上市可产生重复关联；上方另列去重 NCT 数量。</p><nav class="market-view-switch" aria-label="登记结果展示方式">'
    for mode, label, size in [('table', '对照表格', 200), ('cards', '折叠卡片', 50)]:
        target = _url(filters, view=mode if mode != 'table' else '', offset=offset // size * size) + '#market-results'
        body += '<a href="' + e(target) + '"' + (' aria-current="page"' if view == mode else '') + '>' + label + '</a>'
    body += '</nav></div>'
    chip_labels = {'market': '市场', 'company': '证券主体', 'q': '关键词', 'phase': '登记阶段', 'status': '登记状态'}
    if any(filters.get(key) for key in FILTER_KEYS):
        body += '<nav class="filter-chips" aria-label="当前登记筛选">'
        for key in FILTER_KEYS:
            if filters.get(key):
                value = company_names.get(filters[key], filters[key]) if key == 'company' else PHASES.get(filters[key], filters[key]) if key == 'phase' else TRIAL_STATES.get(filters[key], filters[key]) if key == 'status' else filters[key]
                label = chip_labels[key] + '：' + str(value)
                body += '<a class="asset-clear-chip" href="' + e(_url(filters, **{key: ''})) + '#market-results" aria-label="' + e('移除' + label) + '">' + e(label) + ' ×</a>'
        body += '</nav>'
    if rows and view == 'cards':
        body += '<p class="data-note">先扫读标题、阶段与登记状态，展开单项查看干预原文、来源及登记变化。卡片只展示本地观察。</p><div class="market-trial-grid">' + ''.join(_trial_card(row, company_names, filters, offset) for row in rows) + '</div>'
    elif rows:
        body += '<p id="market-table-scroll-hint" class="cell-meta scroll-cue">横向滚动表格，可查看完整的临床项目与来源信息。</p><div class="table-scroll scroll" role="region" aria-label="临床登记检索结果表，可横向滚动" aria-describedby="market-table-scroll-hint" tabindex="0"><table class="clinical-table"><thead><tr><th scope="col">证券主体 / 临床试验</th><th scope="col">干预与适应症</th><th scope="col">阶段 / 登记状态</th><th scope="col">来源与时间</th></tr></thead><tbody>'
        for row in rows:
            data = _data(row['data'])
            interventions, conditions, phase, state = _trial_parts(data)
            caution = ' caution' if data.get('status') in ('TERMINATED', 'WITHDRAWN', 'SUSPENDED', 'UNKNOWN') else ''
            body += '<tr><td><a class="company-link" href="/assets?' + e(urlencode({'company': row['company'], 'q': row['nct']})) + '">' + e(company_names.get(row['company'], row['company'])) + '</a><div class="cell-meta ref">' + e(row['company']) + '</div><div class="trial-reference">' + _source_link(data.get('source_url'), row['nct'], row['nct']) + '</div><p class="trial-title">' + e(data.get('title') or '试验标题未记载') + '</p>' + _trial_actions(row) + '</td>'
            body += '<td><div class="intervention-name">' + e(interventions or '未提供干预名称') + '</div><p class="cell-meta condition-list">' + e(conditions or '未提供适应症') + '</p></td>'
            body += '<td><span class="phase-label">' + e(phase) + '</span><div class="status-badge' + caution + '">' + e(state) + '</div></td>'
            body += '<td><div class="timestamp-row"><span class="cell-meta">来源更新</span>' + _timestamp(data.get('source_updated')) + '</div><div class="timestamp-row"><span class="cell-meta">平台抓取</span>' + _timestamp(row['observed']) + '</div>' + _trial_provenance(row, data) + '</td></tr>'
        body += '</tbody></table></div>'
    else:
        body += '<div class="empty"><strong>没有匹配的已采集记录</strong><p>调整筛选，或查看覆盖页面，确认该公司是否尚未关联、采集失败或未返回记录。</p><a href="/coverage">查看覆盖进度与缺口 →</a></div>'
    if offset or offset + page_size < total:
        body += '<nav class="pagination" aria-label="临床登记结果分页">'
        if offset:
            body += '<a class="button secondary" rel="prev" href="' + e(_url(filters, offset=max(0, offset - page_size))) + '">← 上一页</a>'
        if offset + page_size < total:
            body += '<a class="button secondary" rel="next" href="' + e(_url(filters, offset=offset + page_size)) + '">下一页 →</a>'
        body += '</nav>'
    body += '</section>'
    body += _change_history(m, filters, company_names)

    enabled = m.setting('enabled') == '1'
    body += '<details class="card secondary-panel"><summary>数据更新与来源 · ' + ('自动更新已开启' if enabled else '自动更新未开启') + '</summary><p>工作台或独立采集服务运行时，名录每 24 小时、已配置公司的登记每 6 小时检查一次。任务持久排队，最多同时采集两项；源站披露延迟仍然存在。</p>' + form('/market/schedule', {'enabled': '0' if enabled else '1'}, '暂停自动更新' if enabled else '开启自动更新', 'secondary') + '<div class="table-scroll"><table><thead><tr><th scope="col">来源</th><th scope="col">最近任务</th><th scope="col">条目</th><th scope="col">操作</th></tr></thead><tbody>'
    for source in URLS:
        last = next((r for r in runs if r['source'] == source), None)
        body += '<tr><td>' + e(SOURCES.get(source, source)) + '</td><td>' + e(STATES.get(last['status'], last['status']) + ' · ' + last['started'] if last else '尚未抓取') + '</td><td>' + f'{source_counts.get(source, 0):,}' + '</td><td>' + form('/market/refresh', {'kind': 'universe', 'reference': source}, '立即抓取', 'secondary') + '</td></tr>'
    body += '</tbody></table></div></details><details class="card secondary-panel"><summary>添加公司与申办方映射</summary><p>填写准确的登记申办方名称及主体映射来源；该操作不会确认药物所有权。现有映射不被静默覆盖。</p><form method="post" action="/market/company"><input type="hidden" name="token" value="' + e(app.token) + '"><div class="form-grid"><div class="field"><label for="company-market">市场</label><select id="company-market" name="market"><option>US</option><option>A</option><option>HK</option></select></div><div class="field"><label for="ticker">证券代码</label><input id="ticker" name="ticker" required maxlength="16"></div><div class="field"><label for="company-name">公司名称</label><input id="company-name" name="name" required maxlength="200"></div><div class="field"><label for="aliases">申办方别名（每行一个）</label><textarea id="aliases" name="aliases" required></textarea></div><div class="field"><label for="provenance">映射依据／来源链接</label><input id="provenance" name="provenance" required maxlength="2000"></div></div><div class="form-actions"><button>登记映射</button></div></form></details>'
    return body
