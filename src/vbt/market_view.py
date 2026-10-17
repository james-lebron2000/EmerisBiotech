"""Searchable clinical observations with visible provenance and collection health."""
import html
import json
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


def _source_link(url, label):
    # Imported metadata remains untrusted even when it is rendered as a link.
    try:
        parsed = urlsplit(str(url))
        safe = parsed.scheme == 'https' and bool(parsed.hostname) and not parsed.username and not parsed.password
    except ValueError:
        safe = False
    if safe:
        return '<a class="source-link" target="_blank" rel="noopener noreferrer" href="' + e(url) + '">' + e(label) + ' ↗</a>'
    return '<span class="source-link" title="来源链接不可用：' + e(url) + '">' + e(label) + '</span>'


def render(app, q):
    m = Market(app.workspace, app.state)
    companies = m.rows('companies')
    runs = sorted(m.rows('runs'), key=lambda r: r['started'], reverse=True)
    company_names = {c['id']: c['name'] for c in companies}

    def form(action, fields, label, button_class=''):
        return '<form method="post" action="' + action + '"><input type="hidden" name="token" value="' + e(app.token) + '">' + ''.join('<input type="hidden" name="' + e(k) + '" value="' + e(v) + '">' for k, v in fields.items()) + '<button' + (' class="' + e(button_class) + '"' if button_class else '') + '>' + e(label) + '</button></form>'

    with m.db() as db:
        unique_trials = db.execute('SELECT count(DISTINCT nct) FROM trials').fetchone()[0]
        source_counts = {r['source']: r['n'] for r in db.execute('SELECT source,count(*) n FROM listings GROUP BY source')}

    body = '<header class="page-header"><div><div class="eyebrow">CLINICAL ASSET RESEARCH</div><h1>市场与管线雷达</h1><p class="lead">从证券主体追踪临床项目，核对药物、阶段及公开证据。</p></div><div class="primary-actions"><a class="button" href="/coverage">覆盖进度与缺口</a><a class="button secondary" href="/assets">候选药物关联</a></div></header>'
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
    cid = q.get('company', '')
    body += '<section class="card filter-toolbar" aria-labelledby="market-filter-title"><div class="section-heading"><h2 id="market-filter-title">查找临床项目</h2><span class="cell-meta">可组合筛选，结果保留来源</span></div><form method="get" action="/market" aria-labelledby="market-filter-title"><div class="form-grid" role="group" aria-label="临床项目筛选条件">'
    body += '<div class="field field-search"><label for="market-query">公司、代码、药物或适应症</label><input id="market-query" name="q" value="' + e(query) + '" placeholder="恒瑞、PFE、PD-1、breast cancer"></div>'
    body += '<div class="field"><label for="market-filter">市场</label><select id="market-filter" name="market"><option value="">全部市场</option>' + ''.join('<option ' + ('selected ' if market == value else '') + 'value="' + value + '">' + label + '</option>' for value, label in [('US', '美股'), ('A', 'A 股'), ('HK', '港股')]) + '</select></div>'
    body += '<div class="field field-company"><label for="market-company">证券主体</label><select id="market-company" name="company"><option value="">全部已配置公司</option>' + ''.join('<option ' + ('selected ' if cid == c['id'] else '') + 'value="' + e(c['id']) + '">' + e(c['name'] + ' · ' + c['id']) + '</option>' for c in sorted(companies, key=lambda c: c['id'])) + '</select></div>'
    body += '<div class="field"><label for="market-phase">临床阶段</label><select id="market-phase" name="phase"><option value="">全部阶段</option>' + ''.join('<option ' + ('selected ' if q.get('phase') == value else '') + 'value="' + value + '">' + label + '</option>' for value, label in PHASES.items()) + '</select></div>'
    body += '<div class="field"><label for="market-status">登记状态</label><select id="market-status" name="status"><option value="">全部状态</option>' + ''.join('<option ' + ('selected ' if q.get('status') == value else '') + 'value="' + value + '">' + label + '</option>' for value, label in TRIAL_STATES.items()) + '</select></div></div><div class="form-actions"><button type="submit">检索项目</button><a class="text-action" href="/market">清除筛选</a></div></form></section>'

    try:
        offset = max(0, int(q.get('offset', '0')))
    except ValueError:
        offset = 0
    rows, total = m.search_trials(market, cid, query, q.get('phase', ''), q.get('status', ''), offset=offset)
    result_range = f'{offset + 1:,}–{offset + len(rows):,}' if rows else '0'
    body += '<section class="card results-card" aria-labelledby="market-results-title"><div class="section-heading"><div><h2 id="market-results-title">临床登记结果 <span class="result-count">' + f'{total:,}' + '</span></h2><p class="cell-meta result-status" role="status">显示 ' + result_range + ' 条，共 ' + f'{total:,}' + ' 条证券—试验关联</p></div>' + form('/market/export', {key: q[key] for key in ('market', 'company', 'q', 'phase', 'status') if q.get(key)}, '导出筛选结果 CSV', 'secondary') + '</div>'
    body += '<p class="cell-meta">每页 200 条，按来源更新日期排序。多地上市可产生重复关联；上方另列去重 NCT 数量。</p>'
    if rows:
        body += '<p id="market-table-scroll-hint" class="cell-meta scroll-cue">横向滚动表格，可查看完整的临床项目与来源信息。</p><div class="table-scroll scroll" role="region" aria-label="临床登记检索结果表，可横向滚动" aria-describedby="market-table-scroll-hint" tabindex="0"><table class="clinical-table"><thead><tr><th scope="col">证券主体 / 临床试验</th><th scope="col">干预与适应症</th><th scope="col">阶段 / 登记状态</th><th scope="col">来源与时间</th></tr></thead><tbody>'
        for row in rows:
            data = json.loads(row['data'])
            interventions = ' / '.join(item['name'] for item in data['interventions'])
            conditions = ' / '.join(data['conditions'])
            phase = ' / '.join(PHASES.get(p, p) for p in data['phases']) or '未分期'
            state = TRIAL_STATES.get(data['status'], data['status'])
            caution = ' caution' if data['status'] in ('TERMINATED', 'WITHDRAWN', 'SUSPENDED', 'UNKNOWN') else ''
            body += '<tr><td><a class="company-link" href="/assets?' + e(urlencode({'company': row['company']})) + '">' + e(company_names.get(row['company'], row['company'])) + '</a><div class="cell-meta ref">' + e(row['company']) + '</div><div class="trial-reference">' + _source_link(data['source_url'], row['nct']) + '</div><p class="trial-title">' + e(data['title']) + '</p></td>'
            body += '<td><div class="intervention-name">' + e(interventions or '未提供干预名称') + '</div><p class="cell-meta condition-list">' + e(conditions or '未提供适应症') + '</p></td>'
            body += '<td><span class="phase-label">' + e(phase) + '</span><div class="status-badge' + caution + '">' + e(state) + '</div></td>'
            body += '<td><div class="timestamp-row"><span class="cell-meta">来源更新</span>' + _timestamp(data['source_updated']) + '</div><div class="timestamp-row"><span class="cell-meta">平台抓取</span>' + _timestamp(row['observed']) + '</div><details class="provenance-details"><summary>精确时间与来源</summary><dl><dt>来源更新时间</dt><dd>' + e(data['source_updated']) + '</dd><dt>平台抓取时间</dt><dd>' + e(row['observed']) + '</dd><dt>来源地址</dt><dd>' + e(data['source_url']) + '</dd></dl></details></td></tr>'
        body += '</tbody></table></div>'
    else:
        body += '<div class="empty"><strong>没有匹配的已采集记录</strong><p>调整筛选，或查看覆盖页面，确认该公司是否尚未关联、采集失败或未返回记录。</p><a href="/coverage">查看覆盖进度与缺口 →</a></div>'
    if offset or offset + 200 < total:
        body += '<nav class="pagination" aria-label="临床登记结果分页">'
        if offset:
            body += '<a class="button secondary" rel="prev" href="/market?' + e(urlencode({**q, 'offset': max(0, offset - 200)})) + '">← 上一页</a>'
        if offset + 200 < total:
            body += '<a class="button secondary" rel="next" href="/market?' + e(urlencode({**q, 'offset': offset + 200})) + '">下一页 →</a>'
        body += '</nav>'
    body += '</section>'

    enabled = m.setting('enabled') == '1'
    body += '<details class="card secondary-panel"><summary>数据更新与来源 · ' + ('自动更新已开启' if enabled else '自动更新未开启') + '</summary><p>工作台或独立采集服务运行时，名录每 24 小时、已配置公司的登记每 6 小时检查一次。任务持久排队，最多同时采集两项；源站披露延迟仍然存在。</p>' + form('/market/schedule', {'enabled': '0' if enabled else '1'}, '暂停自动更新' if enabled else '开启自动更新', 'secondary') + '<div class="table-scroll"><table><thead><tr><th scope="col">来源</th><th scope="col">最近任务</th><th scope="col">条目</th><th scope="col">操作</th></tr></thead><tbody>'
    for source in URLS:
        last = next((r for r in runs if r['source'] == source), None)
        body += '<tr><td>' + e(SOURCES.get(source, source)) + '</td><td>' + e(STATES.get(last['status'], last['status']) + ' · ' + last['started'] if last else '尚未抓取') + '</td><td>' + f'{source_counts.get(source, 0):,}' + '</td><td>' + form('/market/refresh', {'kind': 'universe', 'reference': source}, '立即抓取', 'secondary') + '</td></tr>'
    body += '</tbody></table></div></details><details class="card secondary-panel"><summary>添加公司与申办方映射</summary><p>填写准确的登记申办方名称及主体映射来源；该操作不会确认药物所有权。现有映射不被静默覆盖。</p><form method="post" action="/market/company"><input type="hidden" name="token" value="' + e(app.token) + '"><div class="form-grid"><div class="field"><label for="company-market">市场</label><select id="company-market" name="market"><option>US</option><option>A</option><option>HK</option></select></div><div class="field"><label for="ticker">证券代码</label><input id="ticker" name="ticker" required maxlength="16"></div><div class="field"><label for="company-name">公司名称</label><input id="company-name" name="name" required maxlength="200"></div><div class="field"><label for="aliases">申办方别名（每行一个）</label><textarea id="aliases" name="aliases" required></textarea></div><div class="field"><label for="provenance">映射依据／来源链接</label><input id="provenance" name="provenance" required maxlength="2000"></div></div><div class="form-actions"><button>登记映射</button></div></form></details>'
    body += '<section aria-labelledby="market-changes-title"><div class="section-heading"><h2 id="market-changes-title">最近登记变化</h2><span class="cell-meta">最多显示 10 条原始差异记录</span></div>'
    changes = m.recent_changes(limit=10)
    if not changes:
        body += '<p class="cell-meta">尚未记录登记变更。</p>'
    for row in changes:
        before = json.loads(row['before_json']) if row['before_json'] else {}
        after = json.loads(row['after_json'])
        keys = [key for key in after if before.get(key) != after[key]]
        body += '<details class="card secondary-panel"><summary>' + e(company_names.get(row['company'], row['company']) + ' · ' + row['nct'] + ' · ' + ('首次观察' if not before else ' / '.join(keys))) + '</summary><p>' + e(row['observed']) + '</p><pre>' + e(json.dumps({'before': before, 'after': after}, ensure_ascii=False, indent=2)) + '</pre></details>'
    body += '</section>'
    return body
