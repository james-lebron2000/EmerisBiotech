"""Synthetic coverage denominators and source clocks, separate from validation."""
import re
from types import SimpleNamespace

import pytest

from vbt.market import Market, universe_digest
from vbt.market_coverage import coverage_summary, refresh_candidates
from vbt.market_jobs import init_schema as jobs_schema
from vbt.market_progress_view import _coverage_scope, _directory_sources, render
from vbt.sponsor_resolution import init_schema as resolution_schema


@pytest.fixture
def app(tmp_path):
    app = SimpleNamespace(workspace=tmp_path / 'w', state=tmp_path / 's', token='synthetic-token')
    app.market = Market(app.workspace, app.state)
    jobs_schema(app.market)
    resolution_schema(app.market)
    return app


def configured(app):
    for market, ticker in [('US', 'ONE'), ('US', 'TWO'), ('A', '600001'), ('HK', '00001')]:
        app.market.add_company(market, ticker, 'Synthetic ' + ticker, ['Synthetic Sponsor'], 'Synthetic only')
    with app.market.db() as db:
        for cid, nct in [('US:ONE', 'NCT00000001'), ('US:ONE', 'NCT00000002'), ('A:600001', 'NCT00000001')]:
            db.execute('INSERT INTO trials VALUES (?,?,?,?,?)', (cid, nct, '{}', 'synthetic-sha', '2026-10-17'))
    refresh_candidates(app.market, [dict(market='US', ticker='UNMAPPED', name='Synthetic unmapped Pharma',
        classification='candidate', source='Synthetic', reason='Synthetic only', evidence=['Synthetic fixture'])])


def count(body, key):
    return re.search(r'data-coverage-count="' + key + r'".*?<strong class="metric">([^<]+)</strong>', body, re.S).group(1)


def test_scope_counts_securities_and_associations_without_creating_asset_or_issuer_denominator(app):
    configured(app)
    body = _coverage_scope(coverage_summary(app.market))
    assert count(body, 'mapped_company_count') == '4'
    assert count(body, 'companies_with_trials') == '2'
    assert count(body, 'trial_records') == '3'  # The same NCT belongs to two securities.
    assert '完整药企分母</dt><dd>未知' in body
    assert '完整管线覆盖</dt><dd>尚未核验' in body
    assert '不是去重试验数或独立资产数' in body
    assert '多地上市不会自动合并为一个法人' in body
    assert not re.search(r'覆盖率\s*\d', body)


def test_visible_scope_keeps_all_markets_when_lower_candidate_filter_has_no_results(app):
    configured(app)
    body = render(app, {'market': 'A', 'q': 'NO_MATCH'})
    assert count(body, 'mapped_company_count') == '4'
    assert '三地市场全部已存记录 · 不随下方公司筛选变化' in body
    assert '没有匹配记录' in body
    assert body.index('本页统计范围') < body.index('当前未关联候选的处理分区')
    assert body.index('id="coverage-source-status"') < body.index('公司识别与关联进度')
    assert 'href="#coverage-source-status"' in body and 'tabindex="-1" aria-labelledby="coverage-source-title"' in body


def test_empty_local_scope_zero_is_not_source_zero_or_full_coverage(app):
    body = _coverage_scope(coverage_summary(app.market))
    assert count(body, 'mapped_company_count') == count(body, 'companies_with_trials') == count(body, 'trial_records') == '0'
    sources = _directory_sources(coverage_summary(app.market))
    assert '本地目录为 0 行不等于源站确认零条' in sources
    assert '尚无抓取记录 · 来源返回数量未知' in sources
    assert '来源内容日期</dt><dd>未知' in sources
    assert '完整药企分母</dt><dd>未知' in body


def seed_hk_directory(app, *, failed=True):
    rows = [dict(market='HK', exchange='HKEX', ticker='00001', name='Synthetic HK', industry='', source='HK')]
    with app.market.db() as db:
        db.execute("INSERT INTO listings VALUES ('synthetic-hk','HK','HKEX','00001','Synthetic HK','','HK','2026-10-17T01:00:00+00:00')")
        db.execute("INSERT INTO runs VALUES ('successful-hk','HK','',?,?,'succeeded',1,'')", ('2026-10-17T01:00:00+00:00', '2026-10-17T01:01:00+00:00'))
        db.execute("INSERT INTO universe_snapshots VALUES ('HK','successful-hk',1,?,'2026-10-17T01:01:00+00:00')", (universe_digest(rows),))
        if failed:
            db.execute("INSERT INTO runs VALUES ('failed-hk','HK','',?,?,'failed',0,?)", ('2026-10-18T01:00:00+00:00', '2026-10-18T01:01:00+00:00',
                       '<script>synthetic failure</script>\nCookie: PRIVATE_TEST_COOKIE\nMissing /Users/private-person/raw.xlsx'))


def test_failed_latest_refresh_preserves_prior_directory_and_independent_clocks(app):
    seed_hk_directory(app)
    body = _directory_sources(coverage_summary(app.market))
    hk = body.split('data-directory-market="HK"', 1)[1]
    assert '库内发布关联一致 1 项' in hk
    assert '本地已存目录行</dt><dd>1</dd>' in hk
    assert '最近刷新失败或未完成；已有目录另行保留' in hk
    assert '最近成功抓取</dt><dd><time datetime="2026-10-17T01:01:00+00:00"' in hk
    assert '本地目录观察</dt><dd><time datetime="2026-10-17T01:00:00+00:00"' in hk
    assert 'datetime="2026-10-18T01:01:00+00:00"' in hk
    assert '来源内容日期</dt><dd>未知' in hk
    assert '刚抓取不代表内容刚更新' in body
    assert '&lt;script&gt;synthetic failure&lt;/script&gt;' in hk
    for secret in ('PRIVATE_TEST_COOKIE', '/Users/', 'private-person', '<script>'):
        assert secret not in hk


def test_successful_run_does_not_make_unreconciled_local_directory_verified(app):
    seed_hk_directory(app, failed=False)
    with app.market.db() as db:
        db.execute("UPDATE universe_snapshots SET rows_sha256='wrong-hash' WHERE source='HK'")
    hk = _directory_sources(coverage_summary(app.market)).split('data-directory-market="HK"', 1)[1]
    assert '库内发布关联一致 0 项 · 尚未核对 1 项' in hk
    assert '库内发布关联</dt><dd>尚未核对' in hk
    assert '来源内容日期</dt><dd>未知' in hk
    assert '本地已存目录行</dt><dd>1</dd>' in hk


def test_scope_and_source_rendering_do_not_modify_stored_observations(app):
    configured(app)
    seed_hk_directory(app)
    summary = coverage_summary(app.market)
    with app.market.db() as db:
        before = list(db.iterdump())
    _coverage_scope(summary)
    _directory_sources(summary)
    with app.market.db() as db:
        assert list(db.iterdump()) == before


def hk_audit(**changes):
    value = dict(schema_version=1, adapter_version='hk-issuer-names-1', source='HK',
                 source_run_id='successful-hk', raw_sha256='a' * 64, raw_bytes=1350,
                 rows_sha256='b' * 64, directory_count=1,
                 retrieved_at='2026-10-17T01:00:00+00:00', content_as_of='2026-10-20',
                 date_locator='sheet1!A2', date_status='future_dated', status='verified_bytes_date_anomaly')
    value.update(changes)
    return value


def test_current_hk_date_anomaly_is_visible_without_upgrading_date_or_discarding_history(app):
    seed_hk_directory(app)
    body = _directory_sources(coverage_summary(app.market), hk_audit())
    warning = re.search(r'<p class="notice" data-source-date-warning="HK">(.*?)</p>', body).group(1)
    assert '2026-10-20' in warning and '未来日期异常' in warning
    assert '不等于来源日期有效' in warning and '不据此推定全部港股映射失效' in warning
    assert body.index('data-source-date-warning="HK"') < body.index('data-directory-market="US"')
    hk = body.split('data-directory-market="HK"', 1)[1]
    assert '来源内容日期</dt><dd>2026-10-20 · 未来日期，尚待核实' in hk
    assert '原件取回时间</dt><dd><time datetime="2026-10-17T01:00:00+00:00"' in hk
    assert '原文日期位置</dt><dd>sheet1!A2' in hk
    assert '本地已存目录行</dt><dd>1</dd>' in hk
    assert '原件 SHA-256' in hk and 'a' * 64 in hk
    assert '最近刷新失败或未完成；已有目录另行保留' in hk


@pytest.mark.parametrize('changes', [
    {'schema_version': 2}, {'schema_version': True}, {'adapter_version': 'unexpected'},
    {'source': 'SH'}, {'source_run_id': 'other-run'}, {'directory_count': 0},
    {'directory_count': None}, {'directory_count': 2}, {'raw_sha256': 'invalid'},
    {'raw_bytes': 0}, {'raw_bytes': True}, {'rows_sha256': None},
    {'retrieved_at': '2026-10-17'}, {'retrieved_at': '<script>bad()</script>'},
    {'content_as_of': '2026-10-99'}, {'date_locator': '/Users/private/date-header'},
    {'status': 'verified', 'date_status': 'future_dated'}, {'date_status': 'invented'},
])
def test_unbound_or_invalid_hk_audit_never_displays_content_date_or_fabricates_zero(app, changes):
    seed_hk_directory(app, failed=False)
    body = _directory_sources(coverage_summary(app.market), hk_audit(**changes))
    hk = body.split('data-directory-market="HK"', 1)[1]
    assert '未取得与当前目录批次一致的可重验日期摘要' in hk
    assert '2026-10-20' not in body
    assert '本地已存目录行</dt><dd>1</dd>' in hk
    assert '原件取回时间</dt><dd>未知' in hk
    assert 'private/date-header' not in body and '<script>' not in body


def test_missing_or_corrupt_hk_original_is_date_unknown_while_local_rows_remain(app):
    seed_hk_directory(app)
    body = _directory_sources(coverage_summary(app.market), {
        'schema_version': 1, 'adapter_version': 'hk-issuer-names-1', 'source': 'HK',
        'status': 'blocked', 'directory_count': None, 'date_status': 'unknown',
        'error': 'Missing /Users/private/raw.xlsx\nCookie: PRIVATE_TEST_COOKIE'})
    assert '港股来源内容日期尚未核实' in body
    assert '本地已存目录行</dt><dd>1</dd>' in body
    assert '来源内容日期</dt><dd>未知' in body
    assert '/Users/' not in body and 'PRIVATE_TEST_COOKIE' not in body


def test_known_content_date_and_unknown_date_marker_keep_retrieval_clock_separate(app):
    seed_hk_directory(app, failed=False)
    summary = coverage_summary(app.market)
    known = _directory_sources(summary, hk_audit(status='verified', date_status='known', content_as_of='2026-10-16'))
    assert '来源内容日期</dt><dd>2026-10-16 · 已定位原文日期标记' in known
    assert 'data-source-date-warning="HK"' not in known
    assert '原件取回时间</dt><dd><time datetime="2026-10-17T01:00:00+00:00"' in known
    unknown = _directory_sources(summary, hk_audit(date_status='unknown', content_as_of=None, date_locator=None))
    assert '原件日期标记缺失或尚未核对' in unknown
    assert '来源内容日期仍未知；不能用本地取回时间替代' in unknown
    assert '原件取回时间</dt><dd><time datetime="2026-10-17T01:00:00+00:00"' in unknown


def test_render_calls_read_only_hk_contract_once_and_keeps_scope_counts(app, monkeypatch):
    from vbt import hk_issuer_names
    configured(app)
    seed_hk_directory(app)
    calls = []

    def audit(market):
        calls.append(market.path)
        return hk_audit()

    monkeypatch.setattr(hk_issuer_names, 'directory_audit', audit)
    body = render(app, {'market': 'US', 'q': 'missing'})
    assert calls == [app.market.path]
    assert '未来日期异常' in body  # Source status is global, independently of candidate filters.
    assert count(body, 'mapped_company_count') == '4'
    assert '离线历史披露仅验收本地归档，不作定时网络刷新' in body
