"""Read-only research history with independent execution and review filters."""
import html
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit

KINDS = {'run': '分析运行', 'cycle': '研究循环', 'portfolio': '研究快照'}
STATES = {'succeeded': '执行成功', 'failed': '执行失败', 'blocked': '执行受阻',
          'interrupted': '任务中断', 'completed': '循环已完成', 'snapshot': '已冻结',
          'recorded': '已记录', 'unknown': '未知'}
REVIEWS = {'pending': '待科学审查', 'pass': '通过', 'passed': '通过', 'revise': '需修订',
           'insufficient_evidence': '证据不足', 'unknown': '未知'}
SCOPES = {'unmarked': '未标记为合成测试', 'synthetic': '仅合成工程测试'}
PAGE_SIZE = 20
FILTER_KEYS = ('q', 'kind', 'status', 'review', 'scope', 'offset')


def e(value):
    return html.escape(str(value if value is not None else '未记录'), quote=True)


def _badge(value, labels):
    css = value if value in labels else 'unknown'
    return '<span class="badge ' + e(css) + '">' + e(labels.get(value, value)) + '</span>'


def is_synthetic(record):
    # Only a pre-existing explicit marker is used; absence proves no validation.
    return record['title'].lstrip().upper().startswith('SYNTHETIC ENGINEERING TEST:')


def safe_return(value):
    """Retain local history filters without accepting a redirect destination."""
    if not isinstance(value, str) or len(value) > 2500 or any(ord(c) < 32 for c in value):
        return '/records'
    try:
        parsed = urlsplit(value)
        pairs = parse_qsl(parsed.query, keep_blank_values=True, max_num_fields=len(FILTER_KEYS))
    except ValueError:
        return '/records'
    if (parsed.scheme or parsed.netloc or parsed.path != '/records' or parsed.fragment
            or len({key for key, _ in pairs}) != len(pairs)
            or any(key not in FILTER_KEYS for key, _ in pairs)):
        return '/records'
    return '/records' + ('?' + urlencode(pairs) if pairs else '')


def _saved_time(value):
    """Parse an explicit aware timestamp; an ID, mtime or naive date is no substitute."""
    if (not isinstance(value, str) or not 1 <= len(value) <= 100
            or not re.fullmatch(r'\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:\d{2})', value)):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
        local = parsed.astimezone(timezone(timedelta(hours=8)))
        return parsed, local.strftime('%Y-%m-%d %H:%M') + ' UTC+8'
    except (ValueError, OverflowError):
        return None


def _record_time(record):
    """Render fields present in Workbench.records().data without reading artifacts.

    Runs save started_at/finished_at in status.json; cycles save created_at.
    Portfolio evaluation.json currently has no timestamp, so those rows remain
    unknown unless an explicit created_at is supplied in their saved data. The
    display is a transcription of stored fields, not an integrity attestation.
    """
    data = record.get('data')
    if not isinstance(data, dict):
        data = {}
    fields = (('started_at', '开始时间'), ('finished_at', '结束时间')) if record['kind'] == 'run' else (('created_at', '创建时间'),)
    parsed = [(key, label, _saved_time(data.get(key))) for key, label in fields]
    if len(parsed) == 2 and all(row[2] for row in parsed) and parsed[1][2][0] < parsed[0][2][0]:
        return '<div class="record-time cell-meta">记录时间未知 · 保存的开始与结束时间冲突</div>'
    if not any(value for _, _, value in parsed):
        return '<div class="record-time cell-meta">记录时间未知 · 未记录或时间字段尚未核实</div>'
    lines = []
    for key, label, value in parsed:
        if value is None:
            lines.append(e(label) + '未知')
        else:
            lines.append(e(label) + '：<time datetime="' + e(data[key]) + '" title="'
                         + e('保存字段 ' + key + '：' + data[key]) + '">' + e(value[1]) + '</time>')
    return '<div class="record-time cell-meta">' + '<br>'.join(lines) + '</div>'


def record_table(records, return_to=None):
    if not records:
        return ('<div class="empty"><h3>这里还没有匹配的研究记录</h3>'
                '<p>调整筛选条件，或先查看已知结局的回顾案例。</p>'
                '<a href="/case?id=osmr-uc">体验研究流程 →</a></div>')
    body = ('<p class="table-caption history-caption"><span>每条记录保留独立版本与原始状态 · 时间按保存字段显示</span>'
            '<span class="scroll-hint">窄屏可左右滚动，或聚焦表格后使用方向键</span></p>'
            '<div class="scroll history-scroll" role="region" aria-label="研究历史记录表" tabindex="0">'
            '<table class="history-table" role="table"><thead><tr><th scope="col">研究问题与版本</th>'
            '<th scope="col">记录类型</th><th scope="col">执行 / 记录状态</th>'
            '<th scope="col">科学审查</th></tr></thead><tbody>')
    for r in records:
        params = {'kind': r['kind'], 'id': r['id']}
        if return_to:
            params['return'] = safe_return(return_to)
        url = '/record?' + urlencode(params)
        sample = '<span class="badge pending">合成工程测试</span> ' if is_synthetic(r) else ''
        body += ('<tr><td data-label="研究问题与版本">' + sample + '<a class="record-title" href="' + e(url) + '">' + e(r['title']) + '</a>'
                 '<div class="ref record-id">' + e(r['id']) + '</div>' + _record_time(r) + '</td><td data-label="记录类型">'
                 + e(KINDS.get(r['kind'], r['kind'])) + '</td><td data-label="执行 / 记录状态">'
                 + _badge(r['status'], STATES) + '</td><td data-label="科学审查">'
                 + _badge(r['review'], REVIEWS) + '</td></tr>')
    return body + '</tbody></table></div>'


def _filter_chips(filters):
    labels = {'q': '搜索', 'kind': '类型', 'status': '执行状态', 'review': '科学审查', 'scope': '记录范围'}
    options = {'kind': KINDS, 'status': STATES, 'review': REVIEWS, 'scope': SCOPES}
    chips = []
    for key, value in filters.items():
        if not value:
            continue
        label = labels[key] + '：' + options.get(key, {}).get(value, value)
        remaining = {name: item for name, item in filters.items() if name != key and item}
        target = '/records' + ('?' + urlencode(remaining) if remaining else '') + '#history-results'
        chips.append('<a class="asset-clear-chip" href="' + e(target) + '" aria-label="'
                     + e('移除筛选 ' + label) + '">' + e(label) + '<span aria-hidden="true">×</span></a>')
    if not chips:
        return ''
    return '<nav class="history-active-filters" aria-label="已应用的研究历史筛选">' + ''.join(chips) + '</nav>'


def _pagination(filters, offset, count, position):
    if not count:
        return ''
    body = '<nav class="asset-pagination history-pagination" aria-label="研究历史分页 · ' + position + '">'
    body += '<span class="cell-meta">第 ' + str(offset // PAGE_SIZE + 1) + ' / ' + str((count - 1) // PAGE_SIZE + 1) + ' 页 · 每页最多 ' + str(PAGE_SIZE) + ' 条</span><div class="page-actions">'
    for rel, target, text in [('prev', offset - PAGE_SIZE, '← 上一页'), ('next', offset + PAGE_SIZE, '下一页 →')]:
        if 0 <= target < count:
            body += '<a class="button secondary small" rel="' + rel + '" href="/records?' + e(urlencode({**filters, 'offset': target})) + '#history-results">' + text + '</a>'
    return body + '</div></nav>'


def render(records, q):
    query = q.get('q', '').strip()
    kind, state, review = (q.get(key, '') for key in ('kind', 'status', 'review'))
    scope = q.get('scope', '')
    if scope and scope not in SCOPES:
        raise ValueError('未知的研究记录范围')
    review = 'passed' if review == 'pass' else review
    selected = [r for r in records if
                (not kind or r['kind'] == kind) and
                (not state or r['status'] == state) and
                (not review or ('passed' if r['review'] == 'pass' else r['review']) == review) and
                (not scope or (is_synthetic(r) if scope == 'synthetic' else not is_synthetic(r))) and
                query.casefold() in (r['title'] + ' ' + r['id']).casefold()]
    try:
        offset = max(0, int(q.get('offset', 0)))
    except (ValueError, TypeError):
        offset = 0
    offset = min(offset // PAGE_SIZE * PAGE_SIZE,
                 max(0, (len(selected) - 1) // PAGE_SIZE * PAGE_SIZE))
    body = ('<header class="page-header"><div><span class="eyebrow">RESEARCH HISTORY</span>'
            '<h1>历史记录</h1><p class="lead">找回研究问题、查看失败原因，沿用已有证据继续研究。</p>'
            '</div><div class="page-actions"><a class="button secondary" href="/jobs">查看校验任务</a>'
            '<a class="button" href="/notes">记录研究问题</a></div></header>')
    synthetic_count = sum(is_synthetic(r) for r in records)
    history_scope = '包含 ' + str(synthetic_count) + ' 条明确标记的合成工程测试' if synthetic_count else '包含成功、失败和受阻记录'
    metrics = [('全部研究记录', len(records), history_scope),
               ('执行失败或受阻', sum(r['status'] in ('failed', 'blocked', 'interrupted') for r in records), '保留原始原因与日志'),
               ('待审查或需补证据', sum(r['review'] in ('pending', 'revise', 'insufficient_evidence', 'unknown') for r in records), '按记录中的科学审查状态统计')]
    body += '<section class="metric-row" aria-label="研究历史概览">' + ''.join(
        '<div class="metric-card"><span class="metric-label">' + label + '</span><strong class="metric">'
        + format(count, ',') + '</strong><small>' + detail + '</small></div>' for label, count, detail in metrics) + '</section>'
    body += ('<section class="card filter-toolbar history-filter" aria-labelledby="history-filter-title">'
             '<div class="section-heading"><h2 id="history-filter-title">查找过往研究</h2>'
             '<span class="cell-meta">可分别筛选执行状态与科学审查</span></div>'
             '<form method="get" action="/records"><div class="form-grid">'
             '<div class="field field-search"><label for="history-query">研究问题或运行编号</label>'
             '<input type="search" id="history-query" name="q" value="' + e(query) + '" placeholder="例如 OSMR、UC 或运行编号"></div>')
    for key, label, value, options in [('kind', '记录类型', kind, KINDS),
                                        ('status', '执行 / 记录状态', state, STATES),
                                        ('review', '科学审查', review, REVIEWS)]:
        body += '<div class="field"><label for="history-' + key + '">' + label + '</label><select id="history-' + key + '" name="' + key + '"><option value="">全部</option>'
        actual_options = {k: v for k, v in options.items() if not (key == 'review' and k == 'pass')}
        for row in records:
            recorded = 'passed' if key == 'review' and row[key] == 'pass' else row[key]
            actual_options.setdefault(recorded, recorded)
        for option, text in actual_options.items():
            body += '<option value="' + e(option) + '"' + (' selected' if option == value else '') + '>' + e(text) + '</option>'
        body += '</select></div>'
    body += ('<div class="field"><label for="history-scope">记录范围</label>'
             '<select id="history-scope" name="scope"><option value="">全部记录</option>'
             + ''.join('<option value="' + key + '"' + (' selected' if scope == key else '')
                       + '>' + label + '</option>' for key, label in SCOPES.items())
             + '</select></div>')
    body += ('</div><div class="form-actions"><button type="submit">筛选记录</button>'
             '<a class="text-action" href="/records">清除筛选</a></div></form></section>')
    if synthetic_count:
        body += '<p class="data-note">本页统计包含明确标记的合成工程测试；其结果用于工程验收，不能计为真实临床研究或预测成绩。</p>'
    body += '<p class="data-note">“未标记为合成测试”仅按原始标题筛选，不代表真实临床验证或预测准确性已获确认。</p>'
    body += ('<section class="card" id="history-results" tabindex="-1" aria-labelledby="history-results-title"><div class="panel-title"><h2 id="history-results-title">研究记录</h2><span class="muted">'
             + str(len(selected)) + ' / ' + str(len(records)) + ' 条</span></div>')
    if selected:
        body += ('<p class="data-note">显示第 ' + str(offset + 1) + '–'
                 + str(min(offset + PAGE_SIZE, len(selected))) + ' 条匹配记录</p>')
    filters = {'q': query, 'kind': kind, 'status': state, 'review': review, 'scope': scope}
    body += _filter_chips(filters)
    body += _pagination(filters, offset, len(selected), '顶部')
    return_to = '/records?' + urlencode({**filters, 'offset': offset})
    body += record_table(selected[offset:offset + PAGE_SIZE], return_to)
    body += _pagination(filters, offset, len(selected), '底部')
    body += ('</section>'
             '<p class="data-note">历史版本保持不变。执行成功、完整性校验、科学审查及人工签署分别记录；'
             '没有记录的状态保留未知，不能由执行结果推定。</p>')
    return body
