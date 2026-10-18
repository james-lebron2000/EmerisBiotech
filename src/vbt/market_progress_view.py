"""Customer-readable issuer discovery progress; all source material is escaped."""
import html
import json
from datetime import date
from pathlib import Path
from urllib.parse import urlencode

from .market import Market
from .market_coverage import candidate_rows, coverage_summary
from . import market_jobs
from .market_view import _timestamp, _source_link
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
    body += _traversal_panel(market, market_filter)
    body += _expansion_gaps(market) + _registry_audit(app)
    body += '<div class="case-grid">'
    for code, data in summary["markets"].items():
        body += '<div class="card"><div class="eyebrow">' + e(code) + '</div><h2>' + MARKET_LABELS[code] + '</h2><strong class="metric">' + str(data["candidate_count"]) + '</strong><p>药企与相关业务候选</p><p class="muted">目录证券 ' + str(data["securities_count"]) + ' · 已运行筛选 ' + str(data["classified_listing_count"]) + ' · 标识待处理 ' + str(data["unsupported_identity_count"]) + '</p><table><tbody>'
        for label, field in (("尚未判定业务", "unclassified_count"), ("当前规则筛除", "excluded_count"), ("已关联申办方", "mapped_company_count"), ("已有临床登记", "companies_with_trials"), ("尚未关联的候选", "unmapped_candidate_count"), ("已映射，等待采集", "uncollected_count"), ("最近采集失败", "failed_count"), ("结果需要更新", "stale_count")):
            body += '<tr><td>' + label + '</td><td>' + str(data[field]) + '</td></tr>'
        body += '</tbody></table><p class="ref">最近查询零结果 ' + str(data["success_zero_count"]) + ' 家；零结果不代表没有管线。</p><a href="/market/progress?' + e(urlencode({"market": code})) + '">查看' + MARKET_LABELS[code] + '识别记录 →</a></div>'
    body += '</div><div class="card"><h2>推进覆盖</h2><p><a class="button" href="/mapping">检索并核对新的申办方关联 →</a></p><p>刷新识别会重新检查当前已入库的证券目录，并保留人工分类决定。批量抓取把所有已登记申办方加入持久队列，可离开页面后再回来查看。</p><div class="primary-actions">' + action_form(app, "/market/discover", "刷新药企识别") + action_form(app, "/market/expand", "补入已核对来源的申办方映射") + action_form(app, "/market/batch", "抓取全部已关联公司") + '</div><p class="ref">采集任务按队列执行；页面不会承诺尚未取得的完整管线。</p></div>'
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
    body += '<details class="card"><summary>持续发现与来源更新</summary><p>自动更新开启时，证券目录每24小时检查，已配置公司的临床登记与官方披露每6小时检查。目录变更后重新识别候选，并每小时最多排队20个尚未检索且具有可用名称的主体。名称建议仍需有来源依据的核对后才能建立关联。抓取服务须保持运行，源站发布时间和访问限制影响时效。</p>'
    with market.db() as db:
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='market_discovery_events'").fetchone():
            for event in db.execute('SELECT * FROM market_discovery_events ORDER BY created DESC LIMIT 5'):
                body += '<details><summary>'+_timestamp(event['created'])+' · '+e(event['status'])+'</summary><pre>'+e(event['payload'])+'</pre></details>'
    body += '</details>'
    body += '<section id="collection-queue" tabindex="-1" aria-labelledby="collection-queue-title"><h2 id="collection-queue-title">采集队列</h2><p>' + queue_summary + '。任务与错误会在重启后保留。</p>'
    if not queue:
        body += '<div class="empty">尚无采集任务。登记申办方后可批量抓取。</div>'
    else:
        body += '<div class="scroll"><table><tr><th>目标</th><th>状态与时间</th><th>结果或操作</th></tr>'
        for job in queue:
            body += '<tr><td>' + e(job["reference"]) + '</td><td>' + _badge(STATE_LABELS.get(job["status"], job["status"]), job["status"]) + '<p class="ref">' + _timestamp(job["updated"]) + ' · 尝试 ' + str(job["attempts"]) + ' 次</p></td><td>'
            if job.get("error"):
                body += '<details><summary>查看原因</summary><p class="body">' + e(job["error"]) + '</p></details>'
            if job["status"] in ("queued", "retry_wait"):
                body += action_form(app, "/market/cancel", "取消待执行任务", {"jid": job["id"]})
            body += '</td></tr>'
        body += '</table></div><p class="ref">展示最近 30 个任务；队列总数按全部持久记录统计。</p>'
    body += '</section><details class="card"><summary>覆盖缺口与目录来源日期</summary>'
    body += ''.join('<p>' + gap + '</p>' for gap in GAPS)
    for code, data in summary["markets"].items():
        body += '<h3>' + MARKET_LABELS[code] + '</h3>'
        body += '<p>目录证券 ' + str(data["securities_count"]) + ' 条去重记录，其中代码可匹配 ' + str(data["normalized_security_count"]) + '，标识待处理 ' + str(data["unsupported_identity_count"]) + '。待处理记录仍保留在原始目录中。</p>'
        for source in data["universe_sources"]:
            latest = source["latest_run"]
            label = '北交所 A 股' if source['source']=='BSE' else source['source']
            body += '<p>' + e(label) + ' · 当前目录 ' + str(source["records"]) + ' 条 · 目录观察时间 ' + _timestamp(source["last_observed"]) + ' · 最近任务 ' + (_timestamp(latest["finished"] or latest["started"]) if latest else "尚无抓取任务记录") + (' · ' + e(STATE_LABELS.get(latest["status"], latest["status"])) if latest else '') + '</p>'
            if source.get('status_note'):
                body += '<p class="ref">'+e(source['status_note'])+'</p>'
            if source['source']=='BSE' and latest and latest.get('error'):
                body += '<details><summary>北交所最近采集原因</summary><p>'+e(latest['error'])+'</p></details>'
    body += '<p class="ref">汇总时间 ' + _timestamp(summary["as_of"]) + '。当前“需要更新”表示最近一次成功采集已超过24小时，未成功过的公司另列等待或失败状态。</p></details>'
    return body
