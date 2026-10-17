"""Read-only research history with independent execution and review filters."""
import html
from urllib.parse import urlencode

KINDS = {'run': '分析运行', 'cycle': '研究循环', 'portfolio': '研究快照'}
STATES = {'succeeded': '执行成功', 'failed': '执行失败', 'blocked': '执行受阻',
          'interrupted': '任务中断', 'completed': '循环已完成', 'snapshot': '已冻结',
          'recorded': '已记录', 'unknown': '未知'}
REVIEWS = {'pending': '待科学审查', 'pass': '通过', 'passed': '通过', 'revise': '需修订',
           'insufficient_evidence': '证据不足', 'unknown': '未知'}


def e(value):
    return html.escape(str(value if value is not None else '未记录'), quote=True)


def _badge(value, labels):
    css = value if value in labels else 'unknown'
    return '<span class="badge ' + e(css) + '">' + e(labels.get(value, value)) + '</span>'


def is_synthetic(record):
    # Only a pre-existing explicit marker is used; absence proves no validation.
    return record['title'].lstrip().upper().startswith('SYNTHETIC ENGINEERING TEST:')


def record_table(records):
    if not records:
        return ('<div class="empty"><h3>这里还没有匹配的研究记录</h3>'
                '<p>调整筛选条件，或先查看已知结局的回顾案例。</p>'
                '<a href="/case?id=osmr-uc">体验研究流程 →</a></div>')
    body = ('<p class="table-caption"><span>每条记录保留独立版本与原始状态</span>'
            '<span class="scroll-hint">窄屏可左右滚动，或聚焦表格后使用方向键</span></p>'
            '<div class="scroll" role="region" aria-label="研究历史记录表" tabindex="0">'
            '<table class="history-table"><thead><tr><th scope="col">研究问题与版本</th>'
            '<th scope="col">记录类型</th><th scope="col">执行 / 记录状态</th>'
            '<th scope="col">科学审查</th></tr></thead><tbody>')
    for r in records:
        url = '/record?' + urlencode({'kind': r['kind'], 'id': r['id']})
        sample = '<span class="badge pending">合成工程测试</span> ' if is_synthetic(r) else ''
        body += ('<tr><td>' + sample + '<a class="record-title" href="' + e(url) + '">' + e(r['title']) + '</a>'
                 '<div class="ref record-id">' + e(r['id']) + '</div></td><td>'
                 + e(KINDS.get(r['kind'], r['kind'])) + '</td><td>'
                 + _badge(r['status'], STATES) + '</td><td>'
                 + _badge(r['review'], REVIEWS) + '</td></tr>')
    return body + '</tbody></table></div>'


def render(records, q):
    query = q.get('q', '').strip()
    kind, state, review = (q.get(key, '') for key in ('kind', 'status', 'review'))
    review = 'passed' if review == 'pass' else review
    selected = [r for r in records if
                (not kind or r['kind'] == kind) and
                (not state or r['status'] == state) and
                (not review or ('passed' if r['review'] == 'pass' else r['review']) == review) and
                query.casefold() in (r['title'] + ' ' + r['id']).casefold()]
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
    body += ('</div><div class="form-actions"><button type="submit">筛选记录</button>'
             '<a class="text-action" href="/records">清除筛选</a></div></form></section>')
    chips = [label + '：' + value for label, value in [('关键词', query), ('类型', KINDS.get(kind, kind)),
              ('执行 / 记录', STATES.get(state, state)), ('科学审查', REVIEWS.get(review, review))] if value]
    if chips:
        body += '<div class="filter-chips" aria-label="当前筛选">' + ''.join('<span>' + e(c) + '</span>' for c in chips) + '</div>'
    if synthetic_count:
        body += '<p class="data-note">本页统计包含明确标记的合成工程测试；其结果用于工程验收，不能计为真实临床研究或预测成绩。</p>'
    body += ('<section class="card"><div class="panel-title"><h2>研究记录</h2><span class="muted">'
             + str(len(selected)) + ' / ' + str(len(records)) + ' 条</span></div>' + record_table(selected) + '</section>'
             '<p class="data-note">历史版本保持不变。执行成功、完整性校验、科学审查及人工签署分别记录；'
             '没有记录的状态保留未知，不能由执行结果推定。</p>')
    return body
