"""Field-allowlisted, deterministic Chinese reports from public target details.

This renderer performs no model calls or scoring. The frozen packet remains the
canonical input; the report is a readable index of that input and its outputs.
"""
from __future__ import annotations

import html
import math
import re
from collections.abc import Mapping
from urllib.parse import parse_qsl, quote, urlsplit


UNKNOWN = "未记录"
STATUSES = {
    "predicted": "已输出预测", "abstained": "拒答", "abstain": "拒答",
    "failed": "失败", "unavailable": "不可用", "timed_out": "超时",
    "pending": "等待执行", "registered": "已登记", "running": "执行中",
}
TRACKS = {
    "same_evidence": "同证据 LLM", "shared_agent": "统一 Agent",
    "open_agent": "开放 Agent", "patent_ablation": "专利配对实验",
    "archived": "既有冻结档案",
}
PRECISIONS = {
    "day": "日", "month": "月", "quarter": "季度", "half_year": "半年",
    "half": "半年", "year": "年", "range": "区间", "unknown": "未知",
}
_SECRET_NAME = re.compile(r"api[_-]?key|access[_-]?token|auth|password|secret|signature|credential", re.I)
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)(\b(?:api[_-]?key|access[_-]?token|password|authorization|client[_-]?secret)\b[\s\"']*[:=][\s\"']*)(?:Bearer\s+)?[^\s,;\"'<>]+"
)


def _dict(value):
    return value if isinstance(value, Mapping) else {}


def _list(value):
    return value if isinstance(value, (list, tuple)) else []


def _redact(value: str) -> str:
    value = re.sub(r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----", "[密钥已移除]", value, flags=re.S)
    value = re.sub(r"<(?:think|thinking|analysis)>.*?(?:</(?:think|thinking|analysis)>|$)", "[隐藏推理已移除]", value, flags=re.S | re.I)
    value = re.sub(r"\b(?:sk-[A-Za-z0-9_-]{12,}|AIza[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,})\b", "[密钥已移除]", value)
    value = _SECRET_ASSIGNMENT.sub(r"\1[密钥已移除]", value)
    value = re.sub(r"(?i)\bBearer\s+[A-Za-z0-9_.~+/-]{12,}=*", "Bearer [密钥已移除]", value)
    return value


def _text(value, *, missing=UNKNOWN):
    if value is None or value == "":
        return missing
    if isinstance(value, (list, tuple)):
        return "；".join(_text(item) for item in value) or missing
    if isinstance(value, Mapping):
        return "结构化字段未展开，请查看完整冻结输入包"
    # Prevent untrusted evidence from becoming HTML or Markdown instructions.
    result = html.escape(_redact(str(value)), quote=False).replace("\r", " ").replace("\n", " / ")
    return re.sub(r"([\\`*_[\]{}|#!])", r"\\\1", result)


def _url(value):
    if not isinstance(value, str) or not value or _redact(value) != value:
        return None
    if any(ord(char) < 33 for char in value) or "\\" in value:
        return None
    try:
        parsed = urlsplit(value)
        if parsed.username or parsed.password:
            return None
        if parsed.scheme not in {"https", "http"}:
            if parsed.scheme or parsed.netloc or not parsed.path.startswith("/api/arena/"):
                return None
        elif not parsed.hostname:
            return None
        if any(_SECRET_NAME.search(key) for key, _ in parse_qsl(parsed.query)):
            return None
        return quote(value, safe=":/?&=#%+,-._~@")
    except ValueError:
        return None


def _link(value, label="查看来源"):
    safe = _url(value)
    return f"[{_text(label)}]({safe})" if safe else "未提供可公开引用的链接"


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _numeric(value, suffix=""):
    return f"{value:g}{suffix}" if _number(value) else UNKNOWN


def _probability(run):
    value = run.get("probability")
    if run.get("status") != "predicted":
        return "—（没有可用预测）"
    if not _number(value) or not 0 <= value <= 1:
        return "未记录有效概率"
    return f"{value * 100:.6f}".rstrip("0").rstrip(".") + "%"


def _rows(lines, pairs):
    lines.extend(["", "| 字段 | 记录 |", "| --- | --- |"])
    lines.extend(f"| {name} | {value} |" for name, value in pairs)
    lines.append("")


def _items(lines, title, values):
    lines.extend([f"**{title}**", ""])
    entries = _list(values)
    lines.extend(f"- {_text(item)}" for item in entries)
    if not entries:
        lines.append("- 未记录；不代表不存在。")
    lines.append("")


def _refs(lines, title, values):
    lines.extend([f"**{title}**", ""])
    refs = [_dict(item) for item in _list(values)]
    if not refs:
        lines.append("- 未记录引用；不代表没有相关证据。")
    for ref in refs:
        page = "；页码：" + _text(ref["page"]) if ref.get("page") is not None else ""
        lines.append(f"- {_text(ref.get('source_id') or ref.get('id'))}：{_text(ref.get('text') or ref.get('title'))}{page}；{_link(ref.get('source_url') or ref.get('url'))}")
    lines.append("")


def _cost(run):
    amount, currency = run.get("cost_amount"), run.get("cost_currency")
    if _number(amount) and isinstance(currency, str) and re.fullmatch(r"[A-Z]{3}", currency):
        return f"{amount:g} {currency}"
    if _number(run.get("cost_usd")):
        return f"{run['cost_usd']:g} USD"
    return "未记录完整费用；不能视为免费"


def _proof(lines, run):
    proof = _dict(run.get("timestamp_proof"))
    verified = proof.get("status") == "attested" and proof.get("signature_verified") is True and _url(proof.get("url"))
    if verified:
        lines.append("时间凭证：记录标记为已通过第三方 TSA 签名验证；本报告不重新执行密码学验签。")
        _rows(lines, [
            ("凭证", _link(proof.get("url"), "查看时间凭证及下载验证材料")),
            ("TSA", _text(proof.get("tsa"))),
            ("TSA 记录时间（原样）", _text(proof.get("generated_at_utc"))),
            ("所覆盖事件数量", _numeric(proof.get("covered_event_count"))),
            ("账本 SHA-256", _text(proof.get("ledger_sha256"))),
            ("证明清单 SHA-256", _text(proof.get("manifest_sha256"))),
            ("验证方法", _text(proof.get("verification_method"))),
        ])
        lines.extend(["信任依赖于相应 TSA 证书机构；这不是法律公证，也不证明输入来源的真实公开时间、研究结论或临床有效性。", ""])
    else:
        lines.extend(["时间凭证：本记录未提供可核对的已验签第三方时间证明。服务器时间和哈希只能作为内部记录，不能单独证明预测早于结果公布。", "", f"凭证状态：{_text(proof.get('status'))}。", ""])


def render_target_report(detail) -> str:
    """Render a public target_detail dictionary without exposing private fields.

    Missing values stay missing. Failed runs never receive probabilities. Dates
    are emitted at their supplied precision; input dictionaries are not changed.
    """
    detail = _dict(detail)
    target = _dict(detail.get("target"))
    runs = [_dict(item) for item in _list(detail.get("runs"))]
    evidence = [_dict(item) for item in _list(detail.get("evidence"))]
    patents = [_dict(item) for item in _list(detail.get("patents"))]
    result = _dict(detail.get("result") if "result" in detail else target.get("result"))
    readout = _dict(target.get("readout"))
    title = target.get("trial_name") or target.get("id") or "未命名预测目标"
    lines = [f"# Predome 目标研究报告｜{_text(title)}", "",
             "本报告用于研发证据审查。模型概率为尚未完成临床验证的研究判断，不等于药物获批概率、患者疗效或投资回报。", ""]
    if target.get("scope") == "archived_research":
        lines.extend(["**记录范围：既有冻结档案，未纳入新赛季。历史记录不能自动视为新赛季的前瞻验证。**", ""])
    else:
        lines.extend([f"记录范围：{_text(target.get('scope'))}。登记、真实调用、时间证明与临床结果验证是不同状态。", ""])
    lines.append("## 1. 预测什么：确切目标与输入边界")
    _rows(lines, [(label, _text(target.get(key))) for label, key in [
        ("目标 ID", "id"), ("试验注册号", "nct_id"), ("独立开发项目", "project_id"),
        ("药物", "drug"), ("申办方", "sponsor"), ("适应症", "indication"),
        ("目标人群", "population"), ("对照与治疗比较", "comparison"), ("确切终点", "endpoint"),
        ("预先定义的成功判据", "success_definition"), ("分析时间点", "analysis_timepoint"),
        ("证据截止时间（原样）", "information_cutoff_utc"), ("数据截止字段（原样）", "data_cutoff"),
        ("提交截止时间（原样）", "submission_deadline_utc"), ("目标登记时间（原样）", "registered_at"),
    ]])
    lines.append("### 预计公布窗口")
    _rows(lines, [
        ("来源描述", _text(readout.get("label"))),
        ("来源精度", _text(PRECISIONS.get(readout.get("precision"), readout.get("precision")))),
        ("开始 / 结束（原样）", f"{_text(readout.get('start'))} / {_text(readout.get('end'))}"),
        ("指引来源", _link(readout.get("source_url"), readout.get("source_title") or "查看原始读出指引")),
        ("指引核查时间（原样）", _text(readout.get("checked_at"))),
    ])
    lines.extend(["预计窗口沿用来源精度；窗口边界不是已确认的新闻发布日期。未记录的时间不补齐为具体日期。", ""])
    lines.extend(["## 2. 每次运行的输出", "", f"本目标返回 {len(runs)} 条运行记录，包含失败、拒答、未执行及档案记录；不能只用有概率的运行作为全部任务分母。", "",
                  "| 运行 ID | 模型 | 轨道 / 条件 | 状态 | 达成上述成功判据的概率 |",
                  "| --- | --- | --- | --- | --- |"])
    for run in runs:
        lines.append(f"| {_text(run.get('id'))} | {_text(run.get('model_id'))} | {_text(TRACKS.get(run.get('track'), run.get('track')))} / {_text(run.get('condition'))} | {_text(STATUSES.get(run.get('status'), run.get('status')))} | {_probability(run)} |")
    if not runs:
        lines.append("| — | — | — | 尚无运行记录 | —（没有可用预测） |")
    lines.extend(["", "尚无经裁定结果时不排名。不同轨道、档案记录和不同输入条件不能直接合并比较；配对概率变化不等于预测改善。", ""])
    for index, run in enumerate(runs, 1):
        lines.extend([f"### 运行 {index}：{_text(run.get('id'))}", "",
                      f"状态：{_text(STATUSES.get(run.get('status'), run.get('status')))}；概率：{_probability(run)}。", "",
                      f"简洁理由或执行说明：{_text(run.get('reason'))}", ""])
        _refs(lines, "支持证据", run.get("supporting_evidence"))
        _refs(lines, "反对或限制性证据", run.get("counter_evidence"))
        _items(lines, "关键假设", run.get("assumptions"))
        _items(lines, "尚不清楚的信息", run.get("unknowns"))
        usage = _dict(run.get("token_usage") if run.get("token_usage") is not None else run.get("usage"))
        _rows(lines, [
            ("请求模型", _text(run.get("requested_model"))),
            ("实际返回模型 / 档案版本", _text(run.get("returned_model") or run.get("model_version"))),
            ("工作流版本", _text(run.get("workflow_version"))),
            ("输入 / 输出 token", f"{_numeric(usage.get('input_tokens'))} / {_numeric(usage.get('output_tokens'))}"),
            ("缓存读取 / 写入 token", f"{_numeric(usage.get('cache_read_input_tokens'))} / {_numeric(usage.get('cache_write_input_tokens'))}"),
            ("完整运行费用（原币）", _cost(run)),
            ("费用口径", _text(run.get("cost_basis"))),
            ("费用说明", _text(run.get("cost_note"))),
            ("耗时（秒）", _numeric(run.get("latency_seconds"))),
            ("生成时间（原样）", _text(run.get("generated_at_utc"))),
            ("冻结时间（原样）", _text(run.get("locked_at_utc"))),
            ("输入 SHA-256", _text(run.get("input_sha256"))),
            ("输出 SHA-256", _text(run.get("output_sha256"))),
            ("档案记录 SHA-256", _text(run.get("archive_record_sha256"))),
            ("提示词 SHA-256", _text(run.get("prompt_sha256"))),
            ("配置 SHA-256", _text(run.get("config_sha256"))),
            ("输入证据 ID", _text(run.get("evidence_ids"))),
            ("本次输入专利 ID", _text(run.get("patent_ids"), missing="未记录专利输入")),
        ])
        known = run.get("known_cost_amount")
        if _number(known):
            lines.extend([f"可核对的部分费用：{_numeric(known)} {_text(run.get('cost_currency'))}。部分费用不代表完整费用。", ""])
        elif _number(run.get("known_cost_usd")):
            lines.extend([f"可核对的部分费用：{_numeric(run['known_cost_usd'])} USD。部分费用不代表完整费用。", ""])
        lines.extend(["按配置费率计算的费用是估算值，不是服务商账单；缺少用量或费率时费用保持未知。token 数量只用于用量核对，本报告不包含隐藏思维链。", ""])
        if run.get("track") != "archived" and run.get("scope") != "archived_research" and isinstance(run.get("id"), str):
            lines.extend([_link("/api/arena/runs/" + quote(run["id"], safe="") + "/packet", "下载本次完整冻结输入包（JSON）"), ""])
        else:
            lines.extend(["既有档案未提供新赛季格式的完整输入包；请结合原始档案哈希核对。", ""])
        if run.get("paired_run_id"):
            lines.extend([f"配对运行：{_text(run['paired_run_id'])}；配对核验标记：{'已核对' if run.get('pair_verified') is True else '未核对'}。概率变化只能说明输入条件变化后的输出差异。", ""])
        _proof(lines, run)
    lines.extend(["## 3. 可核对的输入证据", "", "以下为该目标返回的来源清单。同一证据 ID 的不同哈希版本分别保留；各次调用究竟使用哪个版本，以该运行的完整冻结输入包与输入哈希为准。本报告不替代完整输入包。", ""])
    if not evidence:
        lines.extend(["未返回输入证据；不能据此断言模型获得了充分信息。", ""])
    for index, item in enumerate(evidence, 1):
        lines.append(f"### 证据 {index}：{_text(item.get('id'))}")
        _rows(lines, [
            ("标题 / 类型", f"{_text(item.get('title'))} / {_text(item.get('kind'))}"),
            ("原始来源", _link(item.get("source_url") or item.get("url"))),
            ("公开日期（原样）", _text(item.get("available_at") or item.get("availability_date") or item.get("public_available_at"))),
            ("获取时间（原样）", _text(item.get("retrieved_at") or item.get("retrieved_at_utc"))),
            ("来源 SHA-256", _text(item.get("source_sha256") or item.get("sha256"))),
            ("专利 / 页码 / 类型", f"{_text(item.get('publication_number'))} / {_text(item.get('page'))} / {_text(item.get('section_type'))}"),
            ("审查状态", _text(item.get("review_status"))),
        ])
    lines.extend(["## 4. 专利是否进入本目标", "", "候选关联、完成 OCR、来源核对与实际进入模型输入是不同状态。专利权利要求或拟开展实验不等于临床疗效观察。", ""])
    linked = {str(p) for run in runs for p in _list(run.get("patent_ids")) if isinstance(p, str)}
    listed = {str(p.get("id") or p.get("publication_number")) for p in patents}
    if not patents and not linked:
        lines.extend(["本目标没有可核对的专利输入记录。其他目标曾使用某项专利，不代表本目标使用过。", ""])
    for patent in patents + [{"id": ident} for ident in sorted(linked - listed)]:
        ident = patent.get("id") or patent.get("publication_number")
        using = [run for run in runs if ident in _list(run.get("patent_ids"))]
        lines.append(f"### {_text(ident)}")
        if using:
            lines.extend(["", "已在本目标以下运行中记录为输入：" + "；".join(f"{_text(r.get('id'))}（{_text(STATUSES.get(r.get('status'), r.get('status')))}）" for r in using) + "。输入登记不等于已得到有效预测。", ""])
        else:
            lines.extend(["", "未在本目标返回的运行 patent_ids 中查到输入记录；候选关联或全局使用标记不能替代本目标的输入证明。", ""])
        _rows(lines, [
            ("标题", _text(patent.get("title"))),
            ("公开日期（原样）", _text(patent.get("publication_date"))),
            ("原始专利", _link(patent.get("source_url") or patent.get("pdf_url"))),
            ("原始文档 SHA-256", _text(patent.get("raw_sha256") or patent.get("sha256"))),
            ("药物关联状态", _text(patent.get("linkage_status"))),
            ("OCR 状态", _text(patent.get("ocr_status"))),
            ("审查状态", _text(patent.get("review_status"))),
            ("人工审查人数", _numeric(_dict(patent.get("research_review")).get("human_review_count"))),
        ])
    lines.extend(["AI 来源复核不能替代独立人工科学审查；未记录人工复核时不宣称已经完成人工审查。", "", "## 5. 实际公布结果与验证状态", ""])
    adjudicated = target.get("status") == "adjudicated" and result.get("verification_status") == "adjudicated" and result.get("label") in {"success", "failure"}
    if adjudicated:
        lines.append("实际结果（已按预定义目标裁定）：" + ("达到成功判据。" if result["label"] == "success" else "未达到成功判据。"))
    elif result or target.get("status") == "announced":
        lines.append("已有结果记录或公布提示，尚未完成与本目标成功判据一致的裁定；暂不用于判定模型命中或排名。")
    else:
        lines.append("尚无已核实并裁定的实际结果；本目标的预测能力仍待未来结果验证。")
    _rows(lines, [
        ("目标状态 / 结果核验状态", f"{_text(target.get('status'))} / {_text(result.get('verification_status'))}"),
        ("首次公开时间（原样）", _text(result.get("first_public_at"))),
        ("结果原始来源", _link(result.get("source_url"))),
        ("来源 SHA-256", _text(result.get("source_sha256"))),
        ("结果记录 SHA-256", _text(result.get("result_sha256"))),
        ("来源记录的效应值", _text(result.get("effect_size"))),
        ("来源记录的置信区间", _text(result.get("confidence_interval"))),
        ("来源记录的 P 值", _text(result.get("p_value"))),
        ("核验时间（原样）", _text(result.get("verified_at"))),
    ])
    lines.extend(["## 6. 如何解读", "",
                  "本报告展示可追溯的研究输入、模型输出与结果核验状态。真实 API 调用或模型之间出现差异，只证明系统运行和输出存在，不能证明任何模型已具有可靠的临床预测能力。", "",
                  "在完整登记任务分母、共同可评估集合与独立开发项目聚类基础上，待足够的结果完成裁定后才能评价覆盖率、校准和预测误差。拒答、调用失败、缺失费用以及不利结果应保留。预训练记忆和历史结果泄漏不能仅凭同证据提示词排除。", ""])
    return "\n".join(lines)
