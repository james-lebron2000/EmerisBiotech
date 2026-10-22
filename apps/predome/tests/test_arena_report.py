"""Isolated report tests: synthetic inputs test rendering, not forecasting."""
from copy import deepcopy

import pytest

from emeris_live.arena_report import render_target_report


def detail():
    return {
        "target": {"id": "target-1", "trial_name": "示例试验", "scope": "prospective_challenge", "status": "pending",
                   "endpoint": "PFS", "success_definition": "指定人群主要终点达到预定义统计判据",
                   "information_cutoff_utc": "2026-10-22T08:00:00Z",
                   "readout": {"label": "2027 年上半年", "precision": "half_year", "start": "2027", "end": None,
                               "source_url": "https://example.org/guidance"}},
        "runs": [{"id": "run-1", "model_id": "model-a", "status": "predicted", "probability": .65,
                  "track": "same_evidence", "condition": "clinical_only", "reason": "主要终点证据有限。",
                  "assumptions": ["沿用预先定义的人群"], "unknowns": ["事件数未知"],
                  "evidence_ids": ["e1"], "patent_ids": [], "input_sha256": "a" * 64,
                  "supporting_evidence": [{"source_id": "e1", "text": "注册记录", "source_url": "https://example.org/study"}],
                  "token_usage": {"input_tokens": 0, "output_tokens": 80},
                  "cost_amount": 0.028, "cost_currency": "CNY", "cost_basis": "configured_rates_estimate"}],
        "evidence": [{"id": "e1", "title": "注册信息", "source_sha256": "b" * 64, "availability_date": "2026-10",
                      "source_url": "https://example.org/study"}], "patents": [], "result": None,
    }


def test_report_exact_input_output_and_dates_without_mutation():
    value = detail()
    original = deepcopy(value)
    report = render_target_report(value)
    assert value == original
    assert "2026-10-22T08:00:00Z" in report and "2027 年上半年" in report
    assert "| 来源精度 | 半年 |" in report and "2027-01-01" not in report
    assert "65%" in report and "主要终点证据有限" in report
    assert "0.028 CNY" in report and "输入 / 输出 token | 0 / 80" in report
    assert "缓存读取 / 写入 token | 未记录 / 未记录" in report
    assert "/api/arena/runs/run-1/packet" in report and "a" * 64 in report
    assert "尚无已核实并裁定的实际结果" in report
    assert "事件数未知" in report and "沿用预先定义的人群" in report


@pytest.mark.parametrize("value", [None, True, "0.9", -0.1, 1.1, float("nan"), float("inf")])
def test_invalid_probability_is_never_coerced_to_zero(value):
    value_detail = detail()
    value_detail["runs"][0]["probability"] = value
    report = render_target_report(value_detail)
    assert "未记录有效概率" in report and "0%" not in report


@pytest.mark.parametrize("status", ["failed", "abstained", "unavailable", "timed_out", "pending"])
def test_non_prediction_never_displays_stray_probability(status):
    value = detail()
    value["runs"][0].update(status=status, probability=.94321)
    report = render_target_report(value)
    assert "94.321%" not in report and "没有可用预测" in report
    assert "1 条运行记录" in report


def test_actual_zero_is_valid_and_missing_cost_is_not_free():
    value = detail()
    value["runs"][0].update(probability=0, cost_amount=None, cost_usd=None, known_cost_amount=0)
    report = render_target_report(value)
    assert "概率：0%" in report
    assert "未记录完整费用；不能视为免费" in report
    assert "部分费用：0 CNY" in report


def test_duplicate_evidence_ids_keep_their_hash_versions():
    value = detail()
    value["evidence"].append({"id": "e1", "source_sha256": "c" * 64})
    report = render_target_report(value)
    assert "b" * 64 in report and "c" * 64 in report
    assert "证据 1：e1" in report and "证据 2：e1" in report


def test_patent_use_requires_this_targets_run_linkage_not_global_flag():
    value = detail()
    value["patents"] = [{"id": "WO123", "included_in_predictions": True, "research_review": {"human_review_count": 0}}]
    report = render_target_report(value)
    assert "未在本目标返回的运行 patent" in report
    assert "已在本目标以下运行中记录为输入" not in report
    assert "人工审查人数 | 0" in report
    value["runs"][0].update(patent_ids=["WO123", "WO-MISSING"], status="failed")
    report = render_target_report(value)
    assert "run-1（失败）" in report and "WO-MISSING" in report
    assert "输入登记不等于已得到有效预测" in report


@pytest.mark.parametrize("signature", [False, None, "true"])
def test_tsa_claim_requires_true_signature_marker(signature):
    value = detail()
    value["runs"][0]["timestamp_proof"] = {"status": "attested", "signature_verified": signature, "url": "/api/arena/attestations/proof-1"}
    report = render_target_report(value)
    assert "未提供可核对的已验签第三方时间证明" in report
    assert "记录标记为已通过第三方 TSA" not in report


def test_valid_tsa_is_reported_with_verification_boundary():
    value = detail()
    value["runs"][0]["timestamp_proof"] = {"status": "attested", "signature_verified": True, "tsa": "FreeTSA",
        "url": "/api/arena/attestations/proof-1", "generated_at_utc": "2026-10-22T09:01:00Z", "manifest_sha256": "d" * 64}
    report = render_target_report(value)
    assert "记录标记为已通过第三方 TSA" in report and "本报告不重新执行密码学验签" in report
    assert "不是法律公证" in report and "d" * 64 in report


def test_announced_result_is_not_adjudication():
    value = detail()
    value["target"]["status"] = "announced"
    value["result"] = {"label": "success", "verification_status": "pending_second_review", "p_value": 0,
                       "first_public_at": "2026-10-23", "source_url": "https://example.org/result"}
    report = render_target_report(value)
    assert "尚未完成与本目标成功判据一致的裁定" in report
    assert "实际结果（已按预定义目标裁定）" not in report
    assert "P 值 | 0" in report
    value["target"]["status"] = "adjudicated"
    value["result"]["verification_status"] = "adjudicated"
    report = render_target_report(value)
    assert "实际结果（已按预定义目标裁定）：达到成功判据" in report


def test_archive_stays_separate_and_has_no_new_packet_link():
    value = detail()
    value["target"]["scope"] = "archived_research"
    value["runs"][0]["track"] = "archived"
    report = render_target_report(value)
    assert "既有冻结档案，未纳入新赛季" in report
    assert "/api/arena/runs/run-1/packet" not in report


def test_private_fields_hidden_thought_and_unsafe_links_are_never_exported():
    value = detail()
    value["api_key"] = "ROOT_SECRET"
    value["runs"][0].update(api_key="RUN_SECRET", reasoning_content="HIDDEN_REASONING", thinking="HIDDEN_THINKING",
                           provider_config={"password": "CONFIG_SECRET"}, attempts=[{"headers": {"Authorization": "ATTEMPT_SECRET"}}],
                           reason="<think>PRIVATE_THOUGHT</think>简洁理由 " + "sk-" + "abcdefghijklmnopqrstuvwxyz123456 api_key=PLAIN_SECRET <script>alert(1)</script>")
    value["target"]["readout"]["source_url"] = "javascript:alert(1)"
    value["evidence"][0]["source_url"] = "https://example.org/source?api_key=QUERY_SECRET"
    value["evidence"][0]["text"] = '{"api_key": "RAW_SOURCE_SECRET"}'
    value["runs"][0]["supporting_evidence"][0]["source_url"] = "https://user:URL_SECRET@example.org/source"
    report = render_target_report(value)
    for secret in ["ROOT_SECRET", "RUN_SECRET", "HIDDEN_REASONING", "HIDDEN_THINKING", "CONFIG_SECRET", "ATTEMPT_SECRET",
                   "PRIVATE_THOUGHT", ("sk-" + "abcdefghijklmnopqrstuvwxyz123456"), "PLAIN_SECRET", "QUERY_SECRET", "RAW_SOURCE_SECRET", "URL_SECRET"]:
        assert secret not in report
    assert "<script>" not in report and "javascript:" not in report
    assert "&lt;script&gt;" in report and "简洁理由" in report


def test_minimal_or_malformed_optional_data_is_renderable():
    for value in [{}, None, {"target": None, "runs": None, "evidence": None, "patents": None}]:
        report = render_target_report(value)
        assert "尚无运行记录" in report and "未记录" in report
