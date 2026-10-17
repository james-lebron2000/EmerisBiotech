"""Synthetic workflow tests; never evidence of real-market pipeline coverage."""
import json
import pytest
from vbt.market import Market
from vbt.market_coverage import (
    init_schema, refresh_candidates, review_candidate, candidate_rows, coverage_summary,
)


@pytest.fixture
def market(tmp_path):
    return Market(tmp_path / "workspace", tmp_path / "state")


def candidate(ticker="TEST", **updates):
    row = dict(market="US", ticker=ticker, name="Synthetic issuer <script>",
               classification="candidate", source="synthetic fixture",
               reason="Synthetic name and industry classification; requires review",
               evidence=[{"source": "synthetic fixture", "text": "Not a real issuer"}],
               observed="2026-10-17T00:00:00+00:00")
    row.update(updates)
    return row


def add_mapping(market, ticker="TEST", market_code="US"):
    return market.add_company(market_code, ticker, "Synthetic pharma", ["Synthetic Sponsor"], "Synthetic mapping fixture")


def finish(market, cid, status, count, at="2026-10-17T00:00:00+00:00"):
    rid = market.begin("ClinicalTrials.gov", cid)
    market.finish(rid, status, count, "Synthetic failure" if status == "failed" else "")
    with market.db() as db:
        db.execute("UPDATE runs SET started=?,finished=? WHERE id=?", (at, at, rid))
    return rid


def test_idempotent_schema_and_candidate_history(market):
    init_schema(market)
    init_schema(market)
    first = refresh_candidates(market, [candidate()])
    assert first == dict(inserted=1, updated=0, unchanged=0, observed=1)
    second = refresh_candidates(market, [candidate(observed="2026-10-17T01:00:00+00:00")])
    assert second["unchanged"] == 1
    rows, total = candidate_rows(market)
    assert total == 1
    assert rows[0]["last_seen"] == "2026-10-17T01:00:00+00:00"
    assert rows[0]["evidence"][0]["source"] == "synthetic fixture"
    with market.db() as db:
        assert db.execute("SELECT count(*) FROM issuer_candidate_observations").fetchone()[0] == 1
    assert rows[0]["mapping_status"] == "unmapped"
    assert rows[0]["full_pipeline_verified"] is False


def test_auto_input_cannot_approve_and_manual_decision_survives_change(market):
    refresh_candidates(market, [candidate(review_status="accepted", confirmed=True, approved=True)])
    row = candidate_rows(market)[0][0]
    assert row["review_status"] == "pending"
    assert "approved" not in row["payload"]
    review_candidate(market, "US:TEST", "accepted", "Declared fixture reviewer", "Reviewed synthetic classification evidence")
    assert market.rows("companies") == []  # review does not establish sponsor linkage
    refresh_candidates(market, [candidate(reason="Updated synthetic classification evidence", observed="2026-10-17T02:00:00+00:00")])
    row = candidate_rows(market)[0][0]
    assert row["review_status"] == "accepted" and row["review_needs_refresh"]
    assert row["last_review"]["reviewer"] == "Declared fixture reviewer"
    review_candidate(market, "US:TEST", "rejected", "Second fixture reviewer", "Classification excluded after reviewing a newer source")
    assert candidate_rows(market, status="rejected")[1] == 1
    assert candidate_rows(market, status="needs_review")[1] == 0
    with market.db() as db:
        assert db.execute("SELECT count(*) FROM issuer_candidate_reviews").fetchone()[0] == 2


def test_invalid_batch_rolls_back_and_rejects_ambiguous_duplicates(market):
    with pytest.raises(ValueError):
        refresh_candidates(market, [candidate(), candidate(ticker="BAD", evidence=[])])
    assert candidate_rows(market)[1] == 0
    with pytest.raises(ValueError):
        refresh_candidates(market, [candidate(), candidate()])
    assert candidate_rows(market)[1] == 0
    with pytest.raises(ValueError):
        refresh_candidates(market, [candidate(observed="2026-10-17")])
    with pytest.raises(ValueError):
        review_candidate(market, "US:UNKNOWN", "accepted", "fixture", "Unknown issuer should fail")


def test_securities_dedup_and_unknown_denominator(market):
    with market.db() as db:
        for ident, source, ticker in (("s1", "US", "TEST"), ("s2", "USNASDAQ", "TEST"), ("s3", "USNASDAQ", "OTHER")):
            db.execute("INSERT INTO listings VALUES (?,?,?,?,?,?,?,?)", (ident, "US", "Nasdaq", ticker, "Synthetic", "", source, "2026-10-17T00:00:00+00:00"))
    refresh_candidates(market, [candidate(), candidate(ticker="FUND", classification="excluded"), candidate(ticker="OTHER", classification="unclassified")])
    summary = coverage_summary(market)["markets"]["US"]
    assert summary["securities_count"] == 2 and summary["listing_records"] == 3
    assert summary["candidate_count"] == 1 and summary["classified_listing_count"] == 3
    assert summary["excluded_count"] == summary["unclassified_count"] == 1
    assert summary["eligible_issuer_denominator"] is None
    assert summary["full_market_coverage_rate"] is None
    assert summary["full_pipeline_verified_count"] == 0
    assert "Beijing" in " ".join(coverage_summary(market)["markets"]["A"]["source_gaps"])


def test_mapped_state_counts_staleness_and_zero_results_preserve_history(market):
    for ticker in ("DATA", "ZERO", "FAILED", "PARTIAL", "EMPTY", "RUNNING", "INTERRUPTED"):
        add_mapping(market, ticker)
    finish(market, "US:DATA", "succeeded", 1, "2026-10-15T00:00:00+00:00")
    finish(market, "US:ZERO", "succeeded", 0)
    finish(market, "US:FAILED", "failed", 0)
    finish(market, "US:PARTIAL", "partial", 2)
    finish(market, "US:INTERRUPTED", "interrupted", 0)
    market.begin("ClinicalTrials.gov", "US:RUNNING")
    with market.db() as db:
        for cid in ("US:DATA", "US:ZERO"):
            db.execute("INSERT INTO trials VALUES (?,?,?,?,?)", (cid, "NCT00000001", "{}", "synthetic", "2026-10-15T00:00:00+00:00"))
    summary = coverage_summary(market, as_of="2026-10-17T01:00:00+00:00")["markets"]["US"]
    assert summary["mapped_company_count"] == 7
    assert summary["companies_with_trials"] == 2  # retained historical observations
    assert summary["success_zero_count"] == 1  # latest successful result was zero
    assert summary["failed_count"] == summary["partial_count"] == summary["uncollected_count"] == 1
    assert summary["interrupted_count"] == summary["running_count"] == summary["stale_count"] == 1
    rows, count = candidate_rows(market, status="succeeded_zero")
    assert count == 1 and rows[0]["trial_count"] == 1
    assert rows[0]["latest_result_count"] == 0


def test_join_filters_pages_and_normalized_hk_ticker(market):
    refresh_candidates(market, [candidate(), candidate(ticker="1", market="HK", name="Synthetic HK")])
    add_mapping(market, ticker="1", market_code="HK")
    rows, total = candidate_rows(market, market_filter="HK")
    assert total == 1 and rows[0]["id"] == "HK:00001" and rows[0]["company_id"] == "HK:00001"
    assert candidate_rows(market, q="Synthetic Sponsor")[1] == 1
    assert candidate_rows(market, status="unmapped")[1] == 1
    assert len(candidate_rows(market, offset=1, limit=1)[0]) == 1
    assert candidate_rows(market, offset=20)[0] == []
    with pytest.raises(ValueError):
        candidate_rows(market, market_filter="INVALID")
    with pytest.raises(ValueError):
        candidate_rows(market, status="complete")
    with pytest.raises(ValueError):
        candidate_rows(market, limit=500)


def test_out_of_order_refresh_keeps_latest_snapshot_and_archives_history(market):
    refresh_candidates(market, [candidate(reason="Newer synthetic evidence", observed="2026-10-17T03:00:00+00:00")])
    refresh_candidates(market, [candidate(reason="Older synthetic evidence", observed="2026-10-16T03:00:00+00:00")])
    row = candidate_rows(market)[0][0]
    assert row["reason"] == "Newer synthetic evidence"
    with market.db() as db:
        assert db.execute("SELECT count(*) FROM issuer_candidate_observations").fetchone()[0] == 2


def test_progress_view_empty_workflow_and_csrf(tmp_path):
    from vbt.workbench import Workbench
    from vbt.market_progress_view import render
    app = Workbench(tmp_path / "workspace", tmp_path / "state")
    text = render(app, {})
    assert "全市场覆盖进度" in text and "尚无识别记录" in text
    assert "完整药企分母未知" in text
    assert 'action="/market/discover"' in text and 'action="/market/batch"' in text
    assert app.token in text
    assert "尚无采集任务" in text
    assert "全市场覆盖率 100" not in text


def test_progress_view_escapes_sources_reviews_queue_and_filters(tmp_path):
    from vbt.workbench import Workbench
    from vbt.market_progress_view import render
    from vbt import market_jobs
    app = Workbench(tmp_path / "workspace", tmp_path / "state")
    market = Market(app.workspace, app.state)
    refresh_candidates(market, [candidate(evidence=[{"text": "<img src=x onerror=alert(1)>", "url": "javascript:alert(1)"}]), candidate(market="HK", ticker="1", name="Synthetic HK")])
    add_mapping(market)
    jid = market_jobs.enqueue(market, "company", "US:TEST")
    with market.db() as db:
        db.execute("UPDATE collection_jobs SET error=? WHERE id=?", ("<script>bad failure</script>", jid))
    text = render(app, {"market": "US", "q": "TEST"})
    assert "Synthetic HK" not in text
    assert "&lt;script&gt;" in text and "<script>" not in text
    assert "&lt;img src=x onerror=alert(1)&gt;" in text
    assert 'href="javascript:' not in text
    assert "分类与采集状态分别展示" in text
    assert 'action="/market/review"' in text and 'name="candidate_id" value="US:TEST"' in text
    assert 'name="decision"' in text and 'name="reviewer"' in text and 'name="reason"' in text
    assert 'action="/market/cancel"' in text and 'name="jid" value="' + jid + '"' in text
    assert "/market?company=US%3ATEST" in text
    assert "没有匹配记录" in render(app, {"q": "DOES_NOT_EXIST"})


def test_candidate_pagination_does_not_materialize_other_payloads(market):
    refresh_candidates(market, [candidate(ticker="AONE"), candidate(ticker="ZTWO")])
    # Out-of-page corruption must not affect rendering the first page. This also
    # proves candidate_rows does not deserialize every issuer's payload to page.
    with market.db() as db:
        db.execute("UPDATE issuer_candidates SET payload_json='invalid-json' WHERE id='US:ZTWO'")
    assert candidate_rows(market, limit=1)[0][0]["id"] == "US:AONE"


def test_search_treats_wildcards_as_literal_input(market):
    refresh_candidates(market, [candidate(name="Synthetic 100% issuer"), candidate(ticker="TWO", name="Other")])
    assert candidate_rows(market, q="100%")[1] == 1
    assert candidate_rows(market, q="_")[1] == 0


def test_unsupported_identity_visible_and_cancelled_not_failed(market):
    with market.db() as db:
        for ident, ticker, source in (("a", "TEST", "US"), ("b", "ABR$D", "USNASDAQ"), ("c", "ABR$D", "USOTHER")):
            db.execute("INSERT INTO listings VALUES (?,?,?,?,?,?,?,?)", (ident, "US", "NYSE", ticker, "Synthetic", "", source, "2026-10-17T00:00:00+00:00"))
    add_mapping(market)
    finish(market, "US:TEST", "cancelled", 0)
    result = coverage_summary(market)
    summary = result["markets"]["US"]
    assert summary["securities_count"] == summary["raw_unique_security_count"] == 2
    assert summary["normalized_security_count"] == summary["unsupported_identity_count"] == 1
    assert summary["listing_records"] == 3
    assert summary["cancelled_count"] == 1 and summary["failed_count"] == 0
    assert result["totals"]["unsupported_identity_count"] == 1
    assert candidate_rows(market, status="cancelled")[1] == 1
    assert "unsupported identities" in " ".join(summary["source_gaps"])


def test_active_classification_filter_and_view_default(tmp_path):
    from vbt.workbench import Workbench
    from vbt.market_progress_view import render
    app = Workbench(tmp_path / "workspace", tmp_path / "state")
    market = Market(app.workspace, app.state)
    refresh_candidates(market, [candidate(), candidate(ticker="FUND", name="Synthetic excluded firm", classification="excluded"), candidate(ticker="MYSTERY", name="Synthetic unclassified firm", classification="unclassified")])
    assert candidate_rows(market, classification="active")[1] == 1
    assert candidate_rows(market, classification="all")[1] == 3
    assert candidate_rows(market, classification="unclassified")[1] == 1
    text = render(app, {})
    assert "Synthetic excluded firm" not in text and "Synthetic unclassified firm" not in text
    assert "Synthetic excluded firm" in render(app, {"classification": "all"})
