"""Customer-readable issuer discovery progress; all source material is escaped."""
import html
import json
import math
import re
from datetime import date, datetime, timezone
from itertools import islice
from pathlib import Path
from urllib.parse import urlencode

from .market import Market
from .market_coverage import candidate_rows, coverage_summary
from . import market_jobs
from .market_view import _timestamp, _source_link, SOURCES as DIRECTORY_LABELS
from .market_traversal import readiness, LABELS as TRAVERSAL_LABELS

REGISTRY_AUDIT = Path('artifacts/market/china_registry_source_audit/2026-10-17/source_audit.json')
MAX_AUDIT_BYTES = 65536

MARKET_LABELS = {"US": "美股", "A": "A股", "HK": "港股"}
CLASS_LABELS = {
    "drug_developer": "有公司资料支持的药物研发企业",
    "candidate": "待核对的药企候选", "drug_developer_candidate": "待核对的药企候选",
    "uncertain": "业务范围待核对", "excluded": "当前筛选规则排除",
    "unclassified": "尚未识别", "configured_company": "已登记申办方映射",
}
STATE_LABELS = {
    "unmapped": "尚未关联申办方", "uncollected": "等待首次采集", "running": "正在采集",
    "failed": "采集失败", "partial": "采集未完成", "interrupted": "采集中断",
    "succeeded_with_trials": "已取得临床登记", "succeeded_zero": "本次查询返回零条",
    "queued": "已排队", "retry_wait": "等待重试", "cancelled": "已取消", "succeeded": "已完成", "fetch_failed": "来源抓取失败", "parse_blocked": "来源解析待完成", "blocked": "主体待补充",
}
REVIEW_LABELS = {"pending": "待人工核对", "accepted": "分类已接受", "rejected": "分类已排除", "not_a_candidate": "尚无分类记录"}
GAPS = (
    "药企识别仍需逐家核对业务与当前证券身份；完整药企总数尚未独立确认。",
    "临床登记未覆盖所有临床前项目、中国境内登记、药物所有权及授权地域。",
    "需要继续与公司官网管线、年报及公告逐项核对；取得试验不代表完整管线已核验。",
)


def e(value):
    return html.escape(str(value if value is not None else "尚未记录"), quote=True)


def hidden(name, value):
    return '<input type="hidden" name="' + e(name) + '" value="' + e(value) + '">'


def action_form(app, action, label, fields=None):
    return '<form method="post" action="' + e(action) + '">' + hidden("token", app.token) + ''.join(hidden(k, v) for k, v in (fields or {}).items()) + '<button>' + e(label) + '</button></form>'


def _badge(label, state=""):
    return '<span class="badge ' + e(state) + '">' + e(label) + '</span>'


def _coverage_scope(summary):
    """Display stored workflow numerators without inventing a market denominator."""
    totals = summary['totals']
    body = ('<section class="card" id="coverage-scope" aria-labelledby="coverage-scope-title">'
            '<div class="section-heading"><div><h2 id="coverage-scope-title">本页统计范围</h2>'
            '<p class="cell-meta">三地市场全部已存记录 · 不随下方公司筛选变化</p></div>'
            '<a class="text-link" href="#coverage-source-status">核对目录来源与日期 ↓</a></div>'
            '<div class="metric-row" aria-label="已存范围与计数单位">')
    for key, label, note in (
            ('mapped_company_count', '已配置证券主体', '证券与申办方检索目标的已配置关联，不是药企总数'),
            ('companies_with_trials', '有已存登记的证券', '按证券标识计数；包含历史观察'),
            ('trial_records', '证券—试验关联', '同一 NCT 可关联多个证券；不是去重试验数或独立资产数')):
        body += ('<div class="metric-card" data-coverage-count="' + e(key) + '"><span class="metric-label">'
                 + label + '</span><strong class="metric">' + format(totals[key], ',') + '</strong><small>'
                 + note + '</small></div>')
    return body + ('</div><dl class="source-meta"><dt>完整药企分母</dt><dd>未知 · 尚未独立确认三地市场全部合资格药企</dd>'
                   '<dt>完整管线覆盖</dt><dd>尚未核验 · 采集成功、已有登记及名称数量均不能替代完整性与权益核对</dd>'
                   '<dt>统计观察时间</dt><dd>' + _timestamp(summary['as_of']) + '</dd></dl>'
                   '<p class="data-note">以上证券按市场与代码区分，多地上市不会自动合并为一个法人。'
                   '候选识别、已配置关联和来源采集是不同处理阶段；不据此计算全市场覆盖率或临床成功概率。</p></section>')


def _read_hk_directory_audit(market):
    """The versioned adapter performs bounded read-only raw/snapshot replay."""
    try:
        from .hk_issuer_names import directory_audit
        return directory_audit(market)
    except (ImportError, OSError, ValueError, TypeError):
        return None


def _hk_date_context(summary, audit):
    """Never combine an audit of a different snapshot with current source rows."""
    unknown = {'date': '未知 · 未取得与当前目录批次一致的可重验日期摘要', 'retrieved': None,
               'locator': None, 'audit': None, 'warning': '港股来源内容日期尚未核实；已有本地目录与既有关联分别保留，不能据此推定全部港股映射失效。'}
    if not isinstance(audit, dict):
        return unknown
    if (type(audit.get('schema_version')) is not int or audit['schema_version'] != 1
            or audit.get('adapter_version') != 'hk-issuer-names-1' or audit.get('source') != 'HK'
            or audit.get('status') not in ('verified', 'verified_bytes_date_anomaly')):
        return unknown
    source = next((source for source in summary['markets']['HK']['universe_sources'] if source['source'] == 'HK'), None)
    success = source.get('last_success_run') if source else None
    if (not source or not source['current_snapshot_verified'] or not success
            or success['id'] != audit.get('source_run_id')
            or type(audit.get('directory_count')) is not int or audit['directory_count'] <= 0
            or audit['directory_count'] != source['records'] or audit['directory_count'] != success['count']
            or not re.fullmatch(r'[0-9a-f]{64}', str(audit.get('raw_sha256')))
            or not re.fullmatch(r'[0-9a-f]{64}', str(audit.get('rows_sha256')))
            or type(audit.get('raw_bytes')) is not int or audit['raw_bytes'] <= 0):
        return unknown
    try:
        retrieved = audit.get('retrieved_at')
        if not isinstance(retrieved, str) or len(retrieved) > 100:
            return unknown
        stamp = datetime.fromisoformat(retrieved.replace('Z', '+00:00'))
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            return unknown
    except (TypeError, ValueError, OverflowError):
        return unknown
    context = {**unknown, 'retrieved': retrieved, 'audit': audit, 'warning': ''}
    content, locator, state = audit.get('content_as_of'), audit.get('date_locator'), audit.get('date_status')
    if state == 'unknown' and content is None:
        context.update(date='未知 · 原件日期标记缺失或尚未核对', warning='港股原件与当前目录批次已重验，但来源内容日期仍未知；不能用本地取回时间替代。')
        return context
    try:
        if not isinstance(content, str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', content):
            return unknown
        date.fromisoformat(content)
        if not isinstance(locator, str) or not re.fullmatch(r'sheet1![A-Z]{1,3}[1-9][0-9]{0,5}', locator):
            return unknown
    except ValueError:
        return unknown
    if state == 'future_dated' and audit['status'] == 'verified_bytes_date_anomaly':
        context.update(date=content + ' · 未来日期，尚待核实', locator=locator,
                       warning='港股 HKEX 目录原文内容日期为 ' + content + '，审计标为未来日期异常；本地取回时间另列。原件与当前目录批次已重验不等于来源日期有效，不能据此把内容升级为当前有效目录，也不据此推定全部港股映射失效。')
    elif state == 'known' and audit['status'] == 'verified':
        context.update(date=content + ' · 已定位原文日期标记，不是平台取回日期', locator=locator)
    else:
        return unknown
    return context


def _directory_sources(summary, hk_audit=None):
    """Expose directory publication, attempt and independently audited source clocks."""
    body = ('<section class="card" id="coverage-source-status" tabindex="-1" aria-labelledby="coverage-source-title">'
            '<div class="section-heading"><div><h2 id="coverage-source-title">目录来源状态与日期</h2>'
            '<p class="cell-meta">按已配置目录来源逐项核对，不是完整管线覆盖率</p></div>'
            '<a class="text-link" href="/market">查看市场页来源观察 →</a></div>'
            '<p class="data-note">库内发布关联一致只表示当前目录行、发布批次与成功记录相符，本页未据此证明原件完整性。'
            '本地取回／观察时间、成功抓取时间与来源内容有效日期分别记录；刚抓取不代表内容刚更新。'
            '本地目录为 0 行不等于源站确认零条，缺失或失败来源的返回数量仍未知。</p>')
    hk_date = _hk_date_context(summary, hk_audit) if hk_audit is not None else None
    if hk_date and hk_date['warning']:
        body += '<p class="notice" data-source-date-warning="HK">' + e(hk_date['warning']) + '</p>'
    for code, data in summary['markets'].items():
        sources = data['universe_sources']
        consistent = sum(source['current_snapshot_verified'] for source in sources)
        body += ('<details data-directory-market="' + e(code) + '"><summary>' + e(MARKET_LABELS[code])
                 + ' · ' + str(len(sources)) + ' 项已配置目录来源 · 库内发布关联一致 ' + str(consistent)
                 + ' 项 · 尚未核对 ' + str(len(sources) - consistent) + ' 项</summary>'
                 '<p class="cell-meta">本地目录证券 ' + str(data['securities_count']) + ' 条去重记录，其中代码可匹配 '
                 + str(data['normalized_security_count']) + '，标识待处理 ' + str(data['unsupported_identity_count'])
                 + '。待处理记录仍保留在原始目录中。</p>')
        for source in sources:
            latest, success = source['latest_run'], source['last_success_run']
            body += '<section class="source-details"><h3>' + e(DIRECTORY_LABELS.get(source['source'], '目录来源待核对')) + '</h3><dl class="source-meta">'
            values = [('本地已存目录行', e(source['records'])),
                      ('库内发布关联', '当前目录与成功批次一致' if source['current_snapshot_verified'] else '尚未核对'),
                      ('最近抓取尝试', (_badge(STATE_LABELS.get(latest['status'], latest['status']), latest['status'])
                                      + ' · ' + _timestamp(latest['finished'] or latest['started'])) if latest else '尚无抓取记录 · 来源返回数量未知'),
                      ('最近成功抓取', _timestamp(success['finished'] or success['started']) if success else '尚无成功记录'),
                      ('本地目录观察', _timestamp(source['last_observed'])),
                      ('来源内容日期', e(hk_date['date']) if source['source'] == 'HK' and hk_date else '未知 · 不能用本地取回或发布时间替代')]
            if source['source'] == 'HK' and hk_date:
                values.append(('原件取回时间', _timestamp(hk_date['retrieved']) if hk_date['retrieved'] else '未知 · 原件／当前批次绑定尚未核实'))
                if hk_date['locator']:
                    values.append(('原文日期位置', e(hk_date['locator'])))
            body += ''.join('<dt>' + label + '</dt><dd>' + value + '</dd>' for label, value in values) + '</dl>'
            if latest and latest['status'] in ('failed', 'partial', 'interrupted', 'cancelled'):
                body += '<p class="notice">最近刷新失败或未完成；已有目录另行保留，不代表源站没有证券。</p>'
            if source.get('status_note'):
                body += '<p class="ref">' + e(source['status_note']) + '</p>'
            if latest and latest.get('error'):
                body += '<details><summary>查看最近抓取原因</summary><p class="body">' + e(_queue_error(latest['error'])) + '</p></details>'
            if source['source'] == 'HK' and hk_date and hk_date['audit']:
                audit = hk_date['audit']
                body += ('<details><summary>查看原件日期审计的批次与校验值</summary><dl class="source-meta">'
                         '<dt>审计范围</dt><dd>当前本地 HKEX 证券目录原件及库内发布关联；不确认英文法人名、权益或完整管线</dd>'
                         '<dt>来源运行</dt><dd><code>' + e(audit['source_run_id']) + '</code></dd>'
                         '<dt>原件 SHA-256</dt><dd><code>' + e(audit['raw_sha256']) + '</code></dd>'
                         '<dt>原件字节数</dt><dd>' + e(audit['raw_bytes']) + '</dd></dl></details>')
            body += '</section>'
        body += '</details>'
    return body + '<p class="data-note">目录只用于识别证券与候选。ClinicalTrials.gov、公司官网／历史年报和中国境内登记是不同来源；目录可读或任务成功均不证明这些管线来源已经齐备。</p></section>'


QUEUE_KINDS = {'universe': '证券目录更新', 'resolve': '申办方名称检索',
               'identity': '来源身份核对', 'company': '临床登记采集', 'disclosure': '官方披露采集／验收'}
RUN_LABELS = {**STATE_LABELS, 'suggested': '已产生关联建议', 'ambiguous': '存在多项候选，待核对',
              'zero': '本次检索零结果', 'busy': '来源正在使用中'}


def _queue_error(value):
    """Keep an escaped diagnostic useful without disclosing paths or credentials."""
    lines = []
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\u200b-\u200f\u202a-\u202e\u2060-\u2069]', '', str(value or '')[:2000])
    for line in text.splitlines():
        if re.search(r'''(?i)\b(authorization|proxy-authorization|set-cookie|cookie|x-api-key|api[_-]?key|(?:access[_-]?|refresh[_-]?)?token|password|passwd|secret|client[_-]?secret)["']?\s*[:=]|\bbearer\s+|https?://[^/\s@]+@''', line):
            lines.append('[敏感凭据或响应头已隐藏]')
        else:
            # Paths can contain spaces. Hide the remainder rather than revealing
            # the username or a quoted suffix after the first space.
            lines.append(re.sub(r'(?<![:/\w])/(?:Users|Volumes|home|tmp|private|var|workspace|state|mnt|opt|etc)(?:/|\b)[^\n]*|(?<![:/\w])/(?:[A-Za-z0-9_.-]+/)[^\n]*|\bartifacts/[^\n]*', '[本地路径已隐藏]', line))
    return '\n'.join(lines)


def _retry_time(job):
    value = job.get('next_at')
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        return '重试时刻尚未核实'
    try:
        return '最早重试 ' + _timestamp(datetime.fromtimestamp(value, timezone.utc).isoformat())
    except (ValueError, OverflowError, OSError):
        return '重试时刻尚未核实'


def _queue_context(market, queue):
    """Bounded SELECT-only enrichment of the displayed jobs, never init/recover.

    A queue run_id is not a foreign key: check its kind-specific target before
    using it as a result or giving the reader a contextual page link.
    """
    from .disclosures import SOURCES as disclosure_sources
    queue = list(islice(queue, 30))
    with market.db() as db:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}

        def select(table, columns, key, values):
            values = sorted({v for v in values if isinstance(v, str) and v})
            if table not in tables or not values:
                return {}
            sql = 'SELECT ' + columns + ' FROM ' + table + ' WHERE ' + key + ' IN (' + ','.join('?' for _ in values) + ')'
            return {r[key]: dict(r) for r in db.execute(sql, values)}

        proposals = select('sponsor_proposals', 'id,candidate_id,candidate_hash,alias', 'id',
                           [j['reference'] for j in queue if j['kind'] == 'identity'])
        candidate_ids = [j['reference'] for j in queue if j['kind'] == 'resolve'] + [p['candidate_id'] for p in proposals.values()]
        candidates = select('issuer_candidates', 'id,market,ticker,name,content_hash', 'id', candidate_ids)
        companies = select('companies', 'id,market,ticker,name', 'id',
                           [j['reference'] for j in queue if j['kind'] == 'company'] + candidate_ids)
        runs = {}
        specs = {
            'universe': ('runs', 'id,source,company,started,finished,status,count,substr(error,1,2000) AS error'),
            'company': ('runs', 'id,source,company,started,finished,status,count,substr(error,1,2000) AS error'),
            'resolve': ('sponsor_resolution_runs', 'id,candidate_id,candidate_hash,started,finished,status,proposal_count,observation_count,truncated,substr(error,1,2000) AS error'),
            'identity': ('identity_collection_runs', 'id,proposal_id,candidate_id,candidate_hash,company,started,finished,status,substr(error,1,2000) AS error'),
            'disclosure': ('disclosure_captures', 'id,source_id,company,url,started,finished,status,count,substr(error,1,2000) AS error'),
        }
        for kind, (table, columns) in specs.items():
            runs[kind] = select(table, columns, 'id', [j.get('run_id') for j in queue if j['kind'] == kind])
        company_refs = sorted({j['reference'] for j in queue if j['kind'] == 'company' and j['reference'] in companies})
        stored_trials = {}
        if company_refs and 'trials' in tables:
            stored_trials = {r['company']: r['n'] for r in db.execute('SELECT company,count(*) AS n FROM trials WHERE company IN (' + ','.join('?' for _ in company_refs) + ') GROUP BY company', company_refs)}
        source_refs = sorted({j['reference'] for j in queue if j['kind'] == 'disclosure' and j['reference'] in disclosure_sources})
        stored_disclosures = {}
        if source_refs and 'disclosure_observations' in tables:
            stored_disclosures = {r['source_id']: r['n'] for r in db.execute('SELECT source_id,count(*) AS n FROM disclosure_observations WHERE source_id IN (' + ','.join('?' for _ in source_refs) + ') GROUP BY source_id', source_refs)}
    result = {}
    for job in queue:
        kind, reference = job['kind'], job['reference']
        proposal = proposals.get(reference) if kind == 'identity' else None
        candidate = candidates.get(proposal['candidate_id'] if proposal else reference)
        company = companies.get(reference) if kind == 'company' else None
        source = disclosure_sources.get(reference) if kind == 'disclosure' else None
        subject = ('证券目录来源尚未核实' if kind == 'universe' else '任务主体尚未核实')
        identifier = None
        if kind == 'universe' and reference in DIRECTORY_LABELS:
            subject, identifier = DIRECTORY_LABELS[reference], reference
        elif kind == 'company' and company:
            subject, identifier = company['name'], company['id']
        elif kind in ('resolve', 'identity') and candidate:
            subject, identifier = candidate['name'], candidate['id']
        elif source:
            subject = source['name']
        run = runs.get(kind, {}).get(job.get('run_id'))
        bound = False
        if run:
            if kind == 'universe':
                bound = reference in DIRECTORY_LABELS and run['source'] == reference and run['company'] == ''
            elif kind == 'company':
                bound = bool(company and run['source'] == 'ClinicalTrials.gov' and run['company'] == reference)
            elif kind == 'resolve':
                bound = bool(candidate and run['candidate_id'] == reference and run['candidate_hash'] == candidate['content_hash'])
            elif kind == 'identity':
                bound = bool(proposal and candidate and run['proposal_id'] == reference
                             and run['candidate_id'] == proposal['candidate_id']
                             and run['candidate_hash'] == proposal['candidate_hash'] == candidate['content_hash']
                             and (not run['company'] or run['company'] == candidate['id']))
            elif kind == 'disclosure':
                bound = bool(source and run['source_id'] == reference and run['company'] == source['companies'][0]
                             and run['url'] == source['url'])
        links = []
        details = []
        if bound:
            if kind == 'universe':
                details.append('本次目录条数 ' + (str(run['count']) if run['status'] == 'succeeded' and type(run['count']) is int and run['count'] >= 0 else '未知'))
                links.append(('/market', '查看目录来源与观察'))
            elif kind == 'company':
                details.append('本次返回登记 ' + (str(run['count']) + ' 条' if run['status'] == 'succeeded' and type(run['count']) is int and run['count'] >= 0 else '条数未知'))
                links.append(('/market?' + urlencode({'company': reference}), '查看该公司已存登记与历史'))
            elif kind == 'resolve':
                complete = run['status'] in ('suggested', 'ambiguous', 'zero', 'succeeded')
                proposals_count = str(run['proposal_count']) if complete and type(run['proposal_count']) is int and run['proposal_count'] >= 0 else '未知'
                observations_count = str(run['observation_count']) if complete and type(run['observation_count']) is int and run['observation_count'] >= 0 else '未知'
                details.append('本次关联建议 ' + proposals_count + ' 条 · 名称观察 ' + observations_count + ' 条')
                if run['truncated']:
                    details.append('本次检索未穷尽全部分页')
                links.append(('/mapping?' + urlencode({'market': candidate['market'], 'q': candidate['ticker']}), '查看该证券核对建议与记录'))
            elif kind == 'identity':
                details.append('来源身份核对结果；不代表人工签署、权益确认或临床验证')
                if re.fullmatch(r'[a-f0-9]{32}', run['id']):
                    links.append(('/mapping?' + urlencode({'identity_run': run['id']}) + '#identity-events', '查看该次身份核对事件'))
            else:
                details.append('关联来源记录 ' + (str(run['count']) + ' 行' if run['status'] == 'succeeded' and type(run['count']) is int and run['count'] >= 0 else '行数未知') + '（不是本次新增行数）')
                details.append('执行条数未经本页原件完整性复核；历史披露不升级为当前管线')
                links.append(('/disclosures?' + urlencode({'source_id': reference}), '查看披露原件、日期与完整性状态'))
        else:
            details.append('尚无可核对的来源运行记录' if not job.get('run_id') else '来源运行绑定尚未核实，结果未知')
        if kind == 'company' and company and 'trials' in tables:
            details.append('当前已存登记 ' + str(stored_trials.get(reference, 0)) + ' 条（含以往观察，不是本次返回条数）')
        elif kind == 'disclosure' and source and 'disclosure_observations' in tables:
            details.append('库内披露观察 ' + str(stored_disclosures.get(reference, 0)) + ' 行（跨批次，未在此验真）')
        result[job['id']] = dict(subject=subject, identifier=identifier, run=run if bound else None,
                                 details=details, links=links)
    return result


def _queue_table(app, market, queue):
    queue = list(islice(queue, 30))
    contexts = _queue_context(market, queue)
    body = '<div class="scroll"><table><thead><tr><th>任务与主体</th><th>队列状态</th><th>来源运行结果</th><th>操作与追溯</th></tr></thead><tbody>'
    for job in queue:
        context = contexts[job['id']]
        body += '<tr><td><strong>' + e(QUEUE_KINDS.get(job['kind'], '未知任务类型')) + '</strong><p>' + e(context['subject']) + '</p>'
        if context['identifier']:
            body += '<p class="ref">' + e(context['identifier']) + '</p>'
        body += '</td><td>' + _badge(STATE_LABELS.get(job['status'], job['status']), job['status'])
        body += '<p class="ref">队列更新 ' + _timestamp(job['updated']) + ' · 尝试 ' + e(job['attempts']) + ' 次</p>'
        if job['status'] == 'retry_wait':
            body += '<p class="ref">' + _retry_time(job) + '；实际执行取决于队列与来源限制。</p>'
        if job.get('cancel_requested') and job['status'] == 'running':
            body += '<p>已请求取消，等待执行器确认。</p>'
        body += '</td><td>'
        run = context['run']
        if run:
            body += _badge(RUN_LABELS.get(run['status'], run['status']), run['status']) + '<p class="ref">来源运行时间 ' + _timestamp(run['finished'] or run['started']) + '</p>'
        body += ''.join('<p class="cell-meta">' + e(detail) + '</p>' for detail in context['details']) + '</td><td>'
        body += ''.join('<p><a href="' + e(url) + '">' + e(label) + ' →</a></p>' for url, label in context['links'])
        if job.get('error') or (run and run.get('error')):
            body += '<details><summary>查看执行原因</summary>'
            if job.get('error'):
                body += '<p class="body">队列：' + e(_queue_error(job['error'])) + '</p>'
            if run and run.get('error'):
                body += '<p class="body">来源：' + e(_queue_error(run['error'])) + '</p>'
            body += '</details>'
        if run:
            run_label = run['id'] if re.fullmatch(r'[A-Za-z0-9_-]{1,80}', str(run['id'])) else '运行标识格式尚未核实'
            body += '<details><summary>来源运行标识</summary><p class="ref">' + e(run_label) + '</p></details>'
        if job['status'] in ('queued', 'retry_wait'):
            body += action_form(app, '/market/cancel', '取消待执行任务', {'jid': job['id']})
        body += '</td></tr>'
    return body + '</tbody></table></div><p class="ref">展示最近 30 个任务；队列总数按全部持久记录统计。来源结果只在运行类型、主体及候选版本可核对时展示；页面入口可能包含其他历史批次。执行完成不替代记录完整性、科学审查或权益证据。查询零结果不代表没有管线。</p>'


def _evidence(row):
    body = '<p class="body">' + e(row.get("reason", "尚未记录识别依据")) + '</p>'
    evidence = row.get("evidence", [])
    if not isinstance(evidence, list):
        evidence = [evidence]
    for item in evidence[:6]:
        if isinstance(item, dict):
            description = item.get("text", item.get("title", item.get("source", "来源观察")))
            url = item.get("url", item.get("source_url", ""))
            body += '<p>' + (_source_link(url, description) if url else e(description)) + '</p>'
        else:
            body += '<p>' + e(item) + '</p>'
    return body


def _traversal_panel(market, market_filter):
    snapshot = readiness(market, market_filter)
    scope = MARKET_LABELS.get(market_filter, '美股、A股与港股')
    body = ('<section class="card" aria-labelledby="traversal-title"><div class="section-heading"><div>'
            '<h2 id="traversal-title">当前未关联候选的处理分区</h2><p class="cell-meta">'
            + e(scope) + ' · 当前库内 ' + str(snapshot['total_unmapped']) + ' 项未关联候选</p></div>'
            '<a class="text-link" href="/mapping#source-identity">查看来源核对与身份任务 →</a></div>'
            '<p class="data-note">范围为当前识别、未排除且尚未关联申办方的候选证券，仅随市场筛选变化。'
            '完整药企分母仍未知；以下是处理状态数量，不是完整管线覆盖率，也不表示来源或主体核对已经通过。</p>'
            '<div class="metric-row" aria-label="未关联候选处理状态">')
    for key, label in TRAVERSAL_LABELS.items():
        body += ('<div class="metric-card" data-readiness="' + e(key) + '"><span class="metric-label">'
                 + e(label) + '</span><strong class="metric">' + str(snapshot['counts'][key]) + '</strong></div>')
    return body + '</div><p class="ref">状态观察时间 ' + _timestamp(snapshot['as_of']) + '</p></section>'


def _expansion_gaps(market):
    with market.db() as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='expansion_events'").fetchone():
            return ''
        latest = db.execute('''SELECT created,CASE WHEN length(CAST(payload AS BLOB))<=262144
            THEN payload ELSE NULL END AS payload FROM expansion_events ORDER BY created DESC LIMIT 1''').fetchone()
    if not latest:
        return ''
    try:
        payload = json.loads(latest['payload'])
        if not isinstance(payload, dict):
            raise ValueError()
        blocked = payload.get('blocked_evidence', [])
        pending = payload.get('pending_proposal_conflicts', [])
        if not isinstance(blocked, list) or not isinstance(pending, list):
            raise ValueError()
    except (ValueError, TypeError, RecursionError):
        return '<section class="card"><h2>最近扩展的核对缺口</h2><p class="notice">最近扩展记录无法解析，缺口数量尚未核实，不能记为零。</p></section>'
    if not blocked and not pending:
        return ''
    body = ('<details class="card"><summary>最近扩展的核对缺口 · 来源证据受阻 ' + str(len(blocked))
            + ' 项 · 既有建议待处理 ' + str(len(pending)) + ' 项</summary><p class="ref">扩展记录时间 '
            + _timestamp(latest['created']) + '</p><p class="data-note">来源核对、建议处理与人工签署分别记录；以下缺口不会自动接受关联。</p>')
    for label, values in [('来源证据受阻', blocked), ('既有建议待处理', pending)]:
        if not values:
            continue
        body += '<h3>' + label + '</h3>'
        for row in values[:20]:
            if not isinstance(row, dict):
                body += '<p>条目结构尚未核实。</p>'
                continue
            body += '<section class="card"><h3>' + e(row.get('id')) + '</h3>'
            if label == '来源证据受阻':
                errors = row.get('errors', [])
                if isinstance(errors, list) and errors:
                    for error in errors[:5]:
                        if isinstance(error, dict):
                            body += '<p>' + e(error.get('code', '原因尚未核实')) + ' · ' + e(error.get('message', '原因尚未核实')) + '</p>'
                        else:
                            body += '<p>具体原因尚未核实。</p>'
                else:
                    body += '<p>具体原因尚未核实。</p>'
            else:
                body += '<p>' + e(row.get('reason', '已有待核对建议，需在主体核对流程中明确处理。')) + '</p>'
                body += '<a class="text-link" href="/mapping#source-identity">查看关联建议与来源核对 →</a>'
            body += '</section>'
        if len(values) > 20:
            body += '<p class="ref">本分区展示前20项，完整数量按本次扩展记录统计。</p>'
    return body + '</details>'


def _registry_audit(app):
    """Read only a bounded allowlisted summary, never headers or raw captures."""
    status, observed, audited = '尚未核实', '尚未核实', None
    entry = None
    try:
        root = Path(app.workspace).resolve()
        path = (root / REGISTRY_AUDIT).resolve()
        if not path.is_relative_to(root):
            raise ValueError()
        with path.open('rb') as source:
            raw = source.read(MAX_AUDIT_BYTES + 1)
        if len(raw) > MAX_AUDIT_BYTES:
            raise ValueError()
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError()
        if payload.get('status') == 'blocked_access_verification':
            status = '访问核验受阻'
        probe = payload.get('probe')
        if status == '访问核验受阻' and isinstance(probe, dict) and type(probe.get('http_status')) is int and probe['http_status'] == 202:
            observed = '已观察到 HTTP 202；未取得可读试验列表'
        try:
            audited = date.fromisoformat(payload.get('date')).isoformat()
        except (ValueError, TypeError):
            pass
        if payload.get('verified_official_entry') == 'https://www.chinadrugtrials.org.cn/':
            entry = payload['verified_official_entry']
    except (OSError, ValueError, TypeError, RecursionError, RuntimeError):
        pass
    body = ('<section class="card" id="china-registry-audit" aria-labelledby="registry-audit-title">'
            '<h2 id="registry-audit-title">中国境内登记来源接入审计</h2><dl class="source-meta">'
            '<dt>接入审计状态</dt><dd>' + status + '</dd><dt>本次来源观察</dt><dd>' + observed
            + '</dd><dt>试验记录数量</dt><dd>未知</dd><dt>审计日期</dt><dd>'
            + (_timestamp(audited) if audited else '尚未核实') + '</dd></dl>')
    if entry:
        body += '<p>' + _source_link(entry, '药物临床试验登记与信息公示平台') + '</p>'
    return body + ('<p class="data-note">本地审计摘要不代表实时接入；访问受阻或尚未读取的来源不能记作零条试验。'
                   '未核实的中国登记、分页和完整管线仍是明确缺口。</p></section>')


def render(app, q):
    """Render /market/progress. See forms below for the authenticated POST contract."""
    market = Market(app.workspace, app.state)
    summary = coverage_summary(market)
    market_filter = q.get("market", "")
    status = q.get("status", "")
    query = q.get("q", "")
    classification = q.get("classification", "active")
    try:
        offset = max(0, int(q.get("offset", "0")))
    except (ValueError, TypeError):
        offset = 0
    rows, total = candidate_rows(market, q=query, market_filter=market_filter, status=status, offset=offset, limit=50, classification=classification)
    queue = market_jobs.jobs(market, 30)
    with market.db() as db:
        queue_counts = {r['status']: r['n'] for r in db.execute('SELECT status,count(*) AS n FROM collection_jobs GROUP BY status')}
    queue_summary = ('排队 ' + str(queue_counts.get('queued', 0)) + ' · 执行中 ' + str(queue_counts.get('running', 0))
                     + ' · 待重试 ' + str(queue_counts.get('retry_wait', 0)) + ' · 抓取失败 '
                     + str(queue_counts.get('failed', 0) + queue_counts.get('fetch_failed', 0)))
    body = ('<header class="page-header"><div><div class="eyebrow">ISSUER DISCOVERY & COLLECTION</div>'
            '<h1>全市场覆盖进度</h1><p class="lead">发现药企，关联申办方，逐家补齐可追溯的管线线索。</p></div>'
            '<div class="primary-actions"><a class="button" href="#collection-queue">查看持久队列</a>'
            '<a class="button secondary" href="/mapping#source-identity">来源核对与身份任务</a></div></header>'
            '<p class="data-note">当前持久队列 · ' + queue_summary + ' · 全部任务 ' + str(sum(queue_counts.values()))
            + '。<a href="#collection-queue">跳至任务与错误记录 ↓</a></p>'
            '<p><a href="/market">查看临床登记与来源观察 →</a> · <a href="/disclosures">公司披露与登记核对 →</a></p>')
    body += '<div class="notice">目录中的证券按市场与代码去重。药企候选需要核对；申办方映射用于检索登记，药物所有权另需证据。目前完整药企分母未知，暂不报告全市场覆盖率。</div>'
    body += _coverage_scope(summary)
    body += _traversal_panel(market, market_filter)
    body += _expansion_gaps(market) + _registry_audit(app)
    body += '<div class="case-grid">'
    for code, data in summary["markets"].items():
        body += '<div class="card"><div class="eyebrow">' + e(code) + '</div><h2>' + MARKET_LABELS[code] + '</h2><strong class="metric">' + str(data["candidate_count"]) + '</strong><p>药企与相关业务候选</p><p class="muted">目录证券 ' + str(data["securities_count"]) + ' · 已运行筛选 ' + str(data["classified_listing_count"]) + ' · 标识待处理 ' + str(data["unsupported_identity_count"]) + '</p><table><tbody>'
        for label, field in (("尚未判定业务", "unclassified_count"), ("当前规则筛除", "excluded_count"), ("已关联申办方", "mapped_company_count"), ("已有临床登记", "companies_with_trials"), ("尚未关联的候选", "unmapped_candidate_count"), ("已映射，等待采集", "uncollected_count"), ("最近采集失败", "failed_count"), ("结果需要更新", "stale_count")):
            body += '<tr><td>' + label + '</td><td>' + str(data[field]) + '</td></tr>'
        body += '</tbody></table><p class="ref">最近查询零结果 ' + str(data["success_zero_count"]) + ' 家；零结果不代表没有管线。</p><a href="/market/progress?' + e(urlencode({"market": code})) + '">查看' + MARKET_LABELS[code] + '识别记录 →</a></div>'
    body += '</div>' + _directory_sources(summary, _read_hk_directory_audit(market))
    body += '<div class="card"><h2>推进覆盖</h2><p><a class="button" href="/mapping">检索并核对新的申办方关联 →</a></p><p>刷新识别会重新检查当前已入库的证券目录，并保留人工分类决定。批量抓取把所有已登记申办方加入持久队列，可离开页面后再回来查看。</p><div class="primary-actions">' + action_form(app, "/market/discover", "刷新药企识别") + action_form(app, "/market/expand", "补入已核对来源的申办方映射") + action_form(app, "/market/batch", "抓取全部已关联公司") + '</div><p class="ref">采集任务按队列执行；页面不会承诺尚未取得的完整管线。</p></div>'
    body += '<h2>公司识别与关联进度</h2><form method="get" action="/market/progress"><label for="coverage-query">公司名称、证券代码或来源关键词</label><input id="coverage-query" type="search" name="q" value="' + e(query) + '"><select name="market" aria-label="市场"><option value="">三地市场</option>'
    for code, label in MARKET_LABELS.items():
        body += '<option value="' + code + '"' + (' selected' if market_filter == code else '') + '>' + label + '</option>'
    body += '</select><select name="classification" aria-label="识别分类">' + ''.join('<option value="' + value + '"' + (' selected' if classification == value else '') + '>' + label + '</option>' for value, label in (("active", "药企候选与已关联公司"), ("all", "全部证券识别记录"), ("drug_developer", "公司资料支持的研发药企"), ("candidate", "名称与行业候选"), ("uncertain", "业务待核对"), ("excluded", "当前规则排除"), ("unclassified", "尚未识别"))) + '</select><select name="status" aria-label="进度筛选"><option value="">所有进度</option>'
    filter_labels = {"needs_review": "分类待核对", "accepted": "分类已接受", "rejected": "分类已排除", "unmapped": "尚未关联申办方", "mapped": "已关联申办方", "uncollected": "等待首次采集", "failed": "采集失败", "partial": "采集未完成", "running": "正在采集", "cancelled": "已取消采集", "succeeded_zero": "查询零结果", "succeeded_with_trials": "已取得登记", "stale": "需要更新"}
    for key, label in filter_labels.items():
        body += '<option value="' + key + '"' + (' selected' if status == key else '') + '>' + label + '</option>'
    body += '</select><button>筛选</button></form><p class="muted">匹配 ' + str(total) + ' 条，每页最多展示 50 条。分类与采集状态分别展示。</p>'
    if not rows:
        body += '<div class="empty"><h3>' + ('尚无识别记录' if total == 0 and not query and not market_filter and not status and not any(data["classified_listing_count"] for data in summary["markets"].values()) else '没有匹配记录') + '</h3><p>先在市场页刷新证券目录，再点击“刷新药企识别”；也可以调整筛选条件。尚无记录不代表该市场没有药企。</p></div>'
    for row in rows:
        body += '<div class="card"><h3>' + e(row["name"]) + ' <span class="muted">' + e(row["id"]) + '</span></h3>'
        body += _badge(CLASS_LABELS.get(row["classification"], row["classification"])) + ' ' + _badge(REVIEW_LABELS[row["review_status"]]) + ' ' + _badge(STATE_LABELS[row["collection_status"]], row["collection_status"])
        if row["review_needs_refresh"]:
            body += ' ' + _badge("分类依据已变化，需重新核对", "blocked")
        if row["stale"]:
            body += ' ' + _badge("已有结果超过24小时，建议更新", "blocked")
        body += '<p>已保留 ' + str(row["trial_count"]) + ' 条试验观察。'
        if row["company_id"]:
            body += '申办方别名：' + e(' / '.join(row["aliases"])) + '。</p><a href="/market?' + e(urlencode({"company": row["company_id"]})) + '">查看该公司临床登记 →</a><details><summary>查看申办方映射依据</summary><p class="body">映射依据：' + e(row["mapping_provenance"]) + '</p></details>'
        else:
            body += '尚需核对准确英文申办方和母子公司关系。</p><a href="/mapping?' + e(urlencode({'market': row['market'], 'q': row['ticker']})) + '">登记申办方映射 →</a>'
        body += '<p class="ref">最近成功采集：' + (_timestamp(row["last_success_at"]) if row["last_success_at"] else "尚未成功采集") + '</p>'
        if row["latest_error"]:
            body += '<p class="notice">' + e(row["latest_error"]) + '</p>'
        body += '<details><summary>识别依据与人工核对</summary>' + _evidence(row)
        if row["last_review"]:
            review = row["last_review"]
            body += '<p class="ref">最近分类记录：' + e(review["reviewer"]) + ' · ' + _timestamp(review["created"]) + '</p><p>' + e(review["reason"]) + '</p>'
        if row["candidate_present"]:
            body += '<p class="muted">以下为本地记录人声明的分类核对，不会自动创建申办方映射或科学审批。</p><form method="post" action="/market/review">' + hidden("token", app.token) + hidden("candidate_id", row["id"]) + '<label>记录人</label><input name="reviewer" maxlength="100" required><label>分类判断</label><select name="decision"><option value="accepted">接受此分类</option><option value="rejected">排除此分类</option></select><label>判断依据</label><textarea name="reason" minlength="10" maxlength="5000" required></textarea><p><button>保存分类记录</button></p></form>'
        body += '</details></div>'
    if offset or offset + 50 < total:
        body += '<p>'
        if offset:
            body += '<a href="/market/progress?' + e(urlencode({**q, "offset": max(0, offset - 50)})) + '">← 上一页</a> '
        if offset + 50 < total:
            body += '<a href="/market/progress?' + e(urlencode({**q, "offset": offset + 50})) + '">下一页 →</a>'
        body += '</p>'
    body += '<details class="card"><summary>持续发现与来源更新</summary><p>自动更新开启时，证券目录每24小时检查，已配置公司的临床登记与支持网络采集的官方披露每6小时检查；离线历史披露仅验收本地归档，不作定时网络刷新。目录变更后重新识别候选，并每小时最多排队20个尚未检索且具有可用名称的主体。名称建议仍需有来源依据的核对后才能建立关联。抓取服务须保持运行，检查频率不是源内容更新时间或客户SLA。</p>'
    with market.db() as db:
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='market_discovery_events'").fetchone():
            for event in db.execute('SELECT * FROM market_discovery_events ORDER BY created DESC LIMIT 5'):
                body += '<details><summary>'+_timestamp(event['created'])+' · '+e(event['status'])+'</summary><pre>'+e(event['payload'])+'</pre></details>'
    body += '</details>'
    body += '<section id="collection-queue" tabindex="-1" aria-labelledby="collection-queue-title"><h2 id="collection-queue-title">采集队列</h2><p>' + queue_summary + '。任务与错误会在重启后保留。</p><p class="ref">以下为三地市场全部任务，不随上方公司筛选变化。</p>'
    if not queue:
        body += '<div class="empty">尚无采集任务。登记申办方后可批量抓取。</div>'
    else:
        body += _queue_table(app, market, queue)
    body += '</section><details class="card"><summary>覆盖缺口与目录来源日期</summary>'
    body += ''.join('<p>' + gap + '</p>' for gap in GAPS)
    body += '<p><a href="#coverage-source-status">核对各目录来源的发布关联、最近尝试和内容日期 ↑</a></p>'
    body += '<p class="ref">汇总时间 ' + _timestamp(summary["as_of"]) + '。当前“需要更新”表示最近一次成功采集已超过24小时，未成功过的公司另列等待或失败状态。</p></details>'
    return body
