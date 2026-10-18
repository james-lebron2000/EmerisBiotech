"""Synthetic coverage UI checks; unknown source counts are never invented zeros."""
import json
import re
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from types import SimpleNamespace
from urllib.parse import parse_qs,urlsplit

import pytest

from vbt.market import Market
from vbt.market_coverage import refresh_candidates
from vbt.market_progress_view import MAX_AUDIT_BYTES, REGISTRY_AUDIT, render
from vbt.market_jobs import init_schema as jobs_schema, _enqueue_in_transaction
from vbt.sponsor_resolution import init_schema as resolution_schema


class Document(HTMLParser):
    def __init__(self, body):
        super().__init__(convert_charrefs=True)
        self.tags, self.visible = [], []
        self.depth, self.summary = 0, False
        self.feed(body)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))
        if tag == 'details': self.depth += 1
        if tag == 'summary': self.summary = True

    def handle_endtag(self, tag):
        if tag == 'details': self.depth -= 1
        if tag == 'summary': self.summary = False

    def handle_data(self, value):
        if not self.depth or self.depth == 1 and self.summary:
            self.visible.append(value)


@pytest.fixture
def app(tmp_path):
    app = SimpleNamespace(workspace=tmp_path / 'workspace', state=tmp_path / 'state', token='synthetic-token')
    app.market = Market(app.workspace, app.state)
    jobs_schema(app.market)
    resolution_schema(app.market)
    return app


def stamp():
    return datetime.fromtimestamp(time.time(), timezone.utc).isoformat()


def candidates(app):
    statuses = [('READY', None), ('ACTIVE', None), ('HELD', 'suggested'), ('RETRY', 'failed'),
                ('ZERO', 'zero'), ('INVALID', 'failed'), ('MYSTERY', 'other-state'), ('RUNNING', 'running')]
    rows = [dict(market='US', ticker=ticker, name='Synthetic' + ticker + ' Therapeutics Inc.',
                 classification='candidate', reason='Synthetic fixture', source='Synthetic', evidence=['Synthetic fixture, no real market evidence'])
            for ticker, _ in statuses]
    rows.append(dict(market='A', ticker='920000', name='合成药业', classification='candidate',
                     reason='Synthetic fixture', source='Synthetic', evidence=['Synthetic fixture, no real market evidence']))
    refresh_candidates(app.market, rows)
    with app.market.db() as db:
        for ticker, status in statuses:
            if status:
                cid = 'US:' + ticker
                content_hash = db.execute('SELECT content_hash FROM issuer_candidates WHERE id=?', (cid,)).fetchone()[0]
                db.execute('''INSERT INTO sponsor_resolution_runs
                    (id,candidate_id,candidate_hash,query,started,status) VALUES (?,?,?,?,?,?)''',
                    ('resolution-' + ticker, cid, content_hash, 'Synthetic query', 'unknown-time' if ticker == 'INVALID' else stamp(), status))
        _enqueue_in_transaction(db, 'resolve', 'US:ACTIVE')
    app.market.add_company('US', 'MAPPED', 'Synthetic mapped company', ['Synthetic Sponsor'], 'MAPPING_PROVENANCE_FULL_TEXT')


def audit(app, **changes):
    value = dict(status='blocked_access_verification', date='2026-10-17', timezone='Asia/Taipei',
                 verified_official_entry='https://www.chinadrugtrials.org.cn/',
                 probe={'http_status': 202, 'record_count': None},
                 pagination={'verified': False, 'total_rows': None},
                 observations=[{'name': 'registry-shr1701-search', 'http_status': 202}])
    value.update(changes)
    path = app.workspace / REGISTRY_AUDIT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    return path


def audit_section(body):
    return re.search(r'<section class="card" id="china-registry-audit".*?</section>', body, re.S).group()


def metric(body, key):
    return int(re.search('data-readiness="' + key + r'".*?<strong class="metric">([0-9]+)</strong>', body, re.S).group(1))


def test_current_candidate_partitions_show_actual_counts_without_coverage_percentage(app):
    candidates(app)
    body = render(app, {})
    for key in ('ready', 'active', 'needs_name', 'held_review', 'retry_wait', 'zero_wait',
                'invalid_time', 'unknown_state', 'source_running'):
        assert metric(body, key) == 1
    assert '当前库内 9 项未关联候选' in body
    assert '完整药企分母仍未知' in body and '不是完整管线覆盖率' in body
    assert '覆盖率 100%' not in body and '0%' not in body
    assert '/mapping#source-identity' in body


def test_partition_market_scope_is_independent_of_lower_keyword_filters(app):
    candidates(app)
    body = render(app, {'market': 'US', 'q': 'NO_MATCH'})
    assert '当前库内 8 项未关联候选' in body
    assert metric(body, 'needs_name') == 0 and metric(body, 'ready') == 1
    assert '仅随市场筛选变化' in body and '没有匹配记录' in body


def test_top_queue_anchor_and_summary_use_all_durable_jobs(app):
    candidates(app)
    with app.market.db() as db:
        for index, status in enumerate(('running', 'retry_wait', 'failed', 'fetch_failed')):
            db.execute('''INSERT INTO collection_jobs(id,kind,reference,status,created,updated)
                          VALUES (?,?,?,?,?,?)''', ('extra-' + str(index), 'company', 'US:EXTRA' + str(index), status, stamp(), stamp()))
    body = render(app, {})
    doc = Document(body)
    assert any(tag == 'a' and attrs.get('href') == '#collection-queue' for tag, attrs in doc.tags)
    assert any(tag == 'section' and attrs.get('id') == 'collection-queue' and attrs.get('tabindex') == '-1' for tag, attrs in doc.tags)
    assert '当前持久队列 · 排队 1 · 执行中 1 · 待重试 1 · 抓取失败 2 · 全部任务 5' in body
    assert body.index('当前持久队列') < body.index('当前未关联候选的处理分区')


def test_mapping_provenance_is_retained_but_default_collapsed(app):
    candidates(app)
    body = render(app, {})
    assert '查看申办方映射依据' in body and 'MAPPING_PROVENANCE_FULL_TEXT' in body
    assert 'MAPPING_PROVENANCE_FULL_TEXT' not in ''.join(Document(body).visible)
    assert all('open' not in attrs for tag, attrs in Document(body).tags if tag == 'details')


def test_unmapped_company_links_to_matching_market_and_ticker_in_mapping_workflow(app):
    candidates(app)
    body=render(app,{'market':'A'})
    targets=[attrs['href'] for tag,attrs in Document(body).tags if tag=='a' and urlsplit(attrs.get('href','')).path=='/mapping' and urlsplit(attrs['href']).query]
    assert len(targets)==1
    params={key:value[0] for key,value in parse_qs(urlsplit(targets[0]).query).items()}
    assert params=={'market':'A','q':'920000'}
    from vbt.sponsor_view import render as mapping
    mapped_page=mapping(app,params)
    assert '选择未关联公司 · 匹配 1 条' in mapped_page and '合成药业 · A:920000' in mapped_page
    assert '待核对建议' in mapped_page and app.market.count('companies')==1


def test_missing_audit_is_unverified_with_unknown_trial_count(app):
    section = audit_section(render(app, {}))
    assert '接入审计状态</dt><dd>尚未核实' in section
    assert '试验记录数量</dt><dd>未知' in section
    assert 'HTTP 202' not in section and '0 条试验' not in section


@pytest.mark.parametrize('record_count', [None, 0, 1000])
def test_blocked_registry_audit_keeps_trial_count_unknown_even_if_import_contains_number(app, record_count):
    audit(app, probe={'http_status': 202, 'record_count': record_count})
    section = audit_section(render(app, {}))
    assert '访问核验受阻' in section and '已观察到 HTTP 202' in section
    assert '试验记录数量</dt><dd>未知' in section and 'datetime="2026-10-17"' in section
    assert 'href="https://www.chinadrugtrials.org.cn/"' in section
    assert '0 条试验' not in section and '1000' not in section


def test_audit_summary_does_not_expose_headers_cookie_paths_or_untrusted_markup(app):
    secret = '/Users/private-person/secret-path Set-Cookie: SESSION=not-public'
    attack = '<img src=x onerror=evil()>'
    audit(app, status=attack, date=attack, headers=secret, local_path=secret,
          observations=[{'headers': secret}], next_actions=[secret],
          verified_official_entry='javascript:evil()', probe={'http_status': 202, 'body': secret})
    section = audit_section(render(app, {}))
    assert secret not in section and attack not in section
    assert 'javascript:' not in section and '试验记录数量</dt><dd>未知' in section
    assert '接入审计状态</dt><dd>尚未核实' in section
    assert not any(tag in ('img', 'script') for tag, _ in Document(section).tags)


@pytest.mark.parametrize('content', [b'{invalid', b'[]', b'x' * (MAX_AUDIT_BYTES + 1)])
def test_invalid_or_oversized_audit_is_unverified(app, content):
    path = app.workspace / REGISTRY_AUDIT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    section = audit_section(render(app, {}))
    assert '接入审计状态</dt><dd>尚未核实' in section
    assert '试验记录数量</dt><dd>未知' in section


def test_fixed_audit_path_cannot_follow_a_symlink_outside_workspace(app, tmp_path):
    outside = tmp_path / 'outside.json'
    outside.write_text(json.dumps({'status': 'blocked_access_verification', 'probe': {'http_status': 202}}))
    path = app.workspace / REGISTRY_AUDIT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to(outside)
    section = audit_section(render(app, {}))
    assert '接入审计状态</dt><dd>尚未核实' in section and 'HTTP 202' not in section


def expansion(app, payload, created='2026-10-17T01:00:00+00:00'):
    with app.market.db() as db:
        db.execute('CREATE TABLE IF NOT EXISTS expansion_events(created TEXT,payload TEXT)')
        db.execute('INSERT INTO expansion_events VALUES (?,?)', (created, payload))


def test_latest_expansion_gaps_are_counted_separately_and_escaped(app):
    attack = '<img src=x onerror=evil()>'
    expansion(app, json.dumps({'blocked_evidence': [dict(id='OLD')] * 4}), '2026-10-16')
    expansion(app, json.dumps({'blocked_evidence': [dict(id='US:SYN', errors=[dict(code='missing_capture', message=attack)])],
                               'pending_proposal_conflicts': [dict(id='HK:06660', reason=attack)]}))
    body = render(app, {})
    assert '来源证据受阻 1 项 · 既有建议待处理 1 项' in body
    assert '来源证据受阻 4 项' not in body and 'missing_capture' in body
    assert '&lt;img' in body and attack not in body
    assert not any(tag in ('img', 'script') for tag, _ in Document(body).tags)
    assert '2026-10-17 09:00 UTC+8' in body


@pytest.mark.parametrize('payload', ['{invalid', '[]', '{"blocked_evidence":{}}'])
def test_invalid_latest_expansion_is_unknown_not_zero(app, payload):
    expansion(app, payload)
    body = render(app, {})
    assert '最近扩展记录无法解析，缺口数量尚未核实，不能记为零' in body
    assert '来源证据受阻 0 项' not in body


def test_empty_expansion_gap_lists_do_not_create_unnecessary_panel(app):
    expansion(app, json.dumps({'blocked_evidence': [], 'pending_proposal_conflicts': []}))
    assert '最近扩展的核对缺口' not in render(app, {})
