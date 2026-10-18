"""Synthetic queue presentation tests; these do not establish market coverage."""
import sqlite3
from html.parser import HTMLParser
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from vbt.market import Market
from vbt.market_coverage import refresh_candidates
from vbt.sponsor_resolution import init_schema as resolution_schema
from vbt.identity_collection import init_schema as identity_schema
from vbt.disclosures import initialize_storage, SOURCES
from vbt.market_progress_view import _queue_context, _queue_table, _retry_time, _queue_error


START = '2026-10-18T01:02:03+00:00'
IDENTITY = 'a' * 32
PROPOSAL = 'b' * 32


class Links(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.links = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.links.append(dict(attrs).get('href'))


@pytest.fixture
def data(tmp_path):
    app = SimpleNamespace(workspace=tmp_path / 'workspace', state=tmp_path / 'state', token='synthetic<&token')
    market = Market(app.workspace, app.state)
    resolution_schema(market)
    identity_schema(market)
    initialize_storage(market)
    refresh_candidates(market, [dict(market='US', ticker='SYNTH', name='Synthetic <Research> Inc.',
                                    classification='candidate', reason='Synthetic fixture', source='Synthetic', evidence=['Synthetic fixture only'])])
    market.add_company('US', 'SYNTH', 'Synthetic <Research> Inc.', ['Synthetic Sponsor'], 'Synthetic only')
    with market.db() as db:
        digest = db.execute("SELECT content_hash FROM issuer_candidates WHERE id='US:SYNTH'").fetchone()[0]
        db.execute('''INSERT INTO sponsor_resolution_runs
          (id,candidate_id,candidate_hash,query,started,finished,status,proposal_count,observation_count)
          VALUES ('resolve-run','US:SYNTH',?,'synthetic research',?,?,'suggested',1,2)''', (digest, START, START))
        db.execute('''INSERT INTO sponsor_proposals
          (id,candidate_id,candidate_hash,alias,normalized_alias,match_type,evidence_nct,source_url,run_id,created,last_seen)
          VALUES (?,'US:SYNTH',?,'Synthetic Sponsor','synthetic sponsor','exact','NCT00000001',
          'https://clinicaltrials.gov/study/NCT00000001','resolve-run',?,?)''', (PROPOSAL, digest, START, START))
        db.execute('''INSERT INTO identity_collection_runs
          (id,proposal_id,candidate_id,candidate_hash,started,finished,status,company)
          VALUES (?,?,'US:SYNTH',?, ?,?,'succeeded','US:SYNTH')''', (IDENTITY, PROPOSAL, digest, START, START))
        db.execute("INSERT INTO runs VALUES ('universe-run','US','',?,?,'succeeded',0,'')", (START, START))
        db.execute("INSERT INTO runs VALUES ('company-run','ClinicalTrials.gov','US:SYNTH',?,?,'succeeded',0,'')", (START, START))
        db.execute("INSERT INTO disclosure_captures(id,source_id,company,url,started,finished,status,count) VALUES ('disclosure-run','pfizer_pipeline','US:PFE',?,?,?,'succeeded',0)", (SOURCES['pfizer_pipeline']['url'], START, START))
        db.execute("INSERT INTO trials VALUES ('US:SYNTH','NCT00000001','{}','hash',?)", (START,))
        db.execute("INSERT INTO disclosure_observations(id,capture_id,source_id,company,source_locator) VALUES ('prior-row','prior-capture','pfizer_pipeline','US:PFE','old-chart-row')")
    return app, market


def job(kind, reference, run_id=None, **updates):
    result = dict(id='job-' + kind, kind=kind, reference=reference, run_id=run_id,
                  status='succeeded', updated=START, attempts=1, error='', next_at=0, cancel_requested=0)
    result.update(updates)
    return result


def test_five_kinds_show_human_subjects_separate_execution_and_scoped_zero(data):
    app, market = data
    queue = [job('universe', 'US', 'universe-run'), job('resolve', 'US:SYNTH', 'resolve-run'),
             job('identity', PROPOSAL, IDENTITY, status='failed', error='Follow-up queue write failed'),
             job('company', 'US:SYNTH', 'company-run'), job('disclosure', 'pfizer_pipeline', 'disclosure-run')]
    contexts = _queue_context(market, queue)
    identity = contexts['job-identity']
    assert identity['subject'] == 'Synthetic <Research> Inc.'
    assert identity['run']['status'] == 'succeeded'  # Queue failure does not rewrite source success.
    assert PROPOSAL not in identity['subject']
    body = _queue_table(app, market, queue)
    for label in ('证券目录更新', '申办方名称检索', '来源身份核对', '临床登记采集', '官方披露采集／验收',
                  '队列状态', '来源运行结果', '本次返回登记 0 条', '当前已存登记 1 条', '关联来源记录 0 行（不是本次新增行数）',
                  '库内披露观察 1 行', '查询零结果不代表没有管线', '不代表人工签署、权益确认或临床验证'):
        assert label in body
    assert 'Synthetic &lt;Research&gt; Inc.' in body
    assert '<Research>' not in body and PROPOSAL not in body
    links = Links(body).links
    assert '/market?company=US%3ASYNTH' in links
    assert '/disclosures?source_id=pfizer_pipeline' in links
    assert '/mapping?identity_run=' + IDENTITY + '#identity-events' in links
    mapping = next(url for url in links if '/mapping?market=' in url)
    assert parse_qs(urlsplit(mapping).query) == {'market': ['US'], 'q': ['SYNTH']}


@pytest.mark.parametrize('kind,reference,run_id,sql', [
    ('universe', 'US', 'universe-run', "UPDATE runs SET source='HK' WHERE id='universe-run'"),
    ('universe', 'US', 'universe-run', "UPDATE runs SET company='US:SYNTH' WHERE id='universe-run'"),
    ('company', 'US:SYNTH', 'company-run', "UPDATE runs SET company='US:OTHER' WHERE id='company-run'"),
    ('company', 'US:SYNTH', 'company-run', "UPDATE runs SET source='Other registry' WHERE id='company-run'"),
    ('resolve', 'US:SYNTH', 'resolve-run', "UPDATE sponsor_resolution_runs SET candidate_id='US:OTHER' WHERE id='resolve-run'"),
    ('resolve', 'US:SYNTH', 'resolve-run', "UPDATE sponsor_resolution_runs SET candidate_hash='old-version' WHERE id='resolve-run'"),
    ('identity', PROPOSAL, IDENTITY, "UPDATE identity_collection_runs SET proposal_id='other' WHERE id='" + IDENTITY + "'"),
    ('identity', PROPOSAL, IDENTITY, "UPDATE identity_collection_runs SET candidate_hash='old-version' WHERE id='" + IDENTITY + "'"),
    ('identity', PROPOSAL, IDENTITY, "UPDATE identity_collection_runs SET company='US:OTHER' WHERE id='" + IDENTITY + "'"),
    ('disclosure', 'pfizer_pipeline', 'disclosure-run', "UPDATE disclosure_captures SET source_id='hengrui_pipeline' WHERE id='disclosure-run'"),
    ('disclosure', 'pfizer_pipeline', 'disclosure-run', "UPDATE disclosure_captures SET company='US:OTHER' WHERE id='disclosure-run'"),
    ('disclosure', 'pfizer_pipeline', 'disclosure-run', "UPDATE disclosure_captures SET url='https://example.invalid/wrong-source' WHERE id='disclosure-run'"),
])
def test_mismatched_target_or_version_never_links_result(data, kind, reference, run_id, sql):
    _, market = data
    with market.db() as db:
        db.execute(sql)
    context = _queue_context(market, [job(kind, reference, run_id)])['job-' + kind]
    assert context['run'] is None and context['links'] == []
    assert '来源运行绑定尚未核实，结果未知' in context['details']


def test_run_id_in_wrong_kind_namespace_is_not_a_result(data):
    app, market = data
    body = _queue_table(app, market, [job('identity', PROPOSAL, 'company-run')])
    assert '来源运行绑定尚未核实，结果未知' in body
    assert Links(body).links == []


def test_failed_source_count_zero_is_unknown_while_earlier_observations_remain(data):
    app, market = data
    with market.db() as db:
        db.execute("UPDATE runs SET status='failed',count=0 WHERE id='company-run'")
        db.execute("UPDATE sponsor_resolution_runs SET status='failed',proposal_count=0,observation_count=0")
        db.execute("UPDATE disclosure_captures SET status='failed',count=0")
    queue = [job('company', 'US:SYNTH', 'company-run', status='failed'),
             job('resolve', 'US:SYNTH', 'resolve-run', status='failed'),
             job('disclosure', 'pfizer_pipeline', 'disclosure-run', status='failed')]
    body = _queue_table(app, market, queue)
    assert '本次返回登记 条数未知' in body and '当前已存登记 1 条' in body
    assert '关联来源记录 行数未知（不是本次新增行数）' in body and '库内披露观察 1 行' in body
    assert '本次关联建议 未知 条 · 名称观察 未知 条' in body
    assert '本次返回登记 0 条' not in body and '关联来源记录 0 行' not in body


def test_repeat_acceptance_jobs_reusing_capture_do_not_claim_new_rows(data):
    app, market = data
    source_id = 'carsgen_annual_pipeline_2024'
    source = SOURCES[source_id]
    with market.db() as db:
        db.execute('''INSERT INTO disclosure_captures
          (id,source_id,company,url,started,finished,status,count)
          VALUES ('existing-offline-capture',?,?,?,?,?,'succeeded',10)''',
                   (source_id, source['companies'][0], source['url'], START, START))
    queue = [job('disclosure', source_id, 'existing-offline-capture', id='first-acceptance'),
             job('disclosure', source_id, 'existing-offline-capture', id='repeat-acceptance',
                 updated='2026-10-19T01:02:03+00:00')]
    body = _queue_table(app, market, queue)
    assert body.count('关联来源记录 10 行（不是本次新增行数）') == 2
    assert '本次写入' not in body and '本次新增 10 行' not in body
    with market.db() as db:
        assert db.execute("SELECT count(*) FROM disclosure_captures WHERE id='existing-offline-capture'").fetchone()[0] == 1


def test_no_run_unknown_kind_and_unknown_source_path_are_unknown_not_zero(data):
    app, market = data
    queue = [job('company', 'US:SYNTH', None, status='queued'),
             job('identity', 'opaque-proposal-id', None, status='queued'),
             job('universe', '/private/source/company.json', None),
             job('unknown-kind', 'Cookie: private-value', 'company-run')]
    body = _queue_table(app, market, queue)
    assert '尚无可核对的来源运行记录' in body and '未知任务类型' in body
    assert 'opaque-proposal-id' not in body and '/private/source/' not in body and 'private-value' not in body
    assert '本次返回登记 0 条' not in body


def test_optional_collection_tables_absent_do_not_create_them(tmp_path):
    market = Market(tmp_path / 'w', tmp_path / 's')
    context = _queue_context(market, [job('identity', PROPOSAL, IDENTITY)])['job-identity']
    assert context['run'] is None and context['links'] == []
    with market.db() as db:
        assert db.execute("SELECT 1 FROM sqlite_master WHERE name='identity_collection_runs'").fetchone() is None


def test_retry_time_errors_cancel_token_and_source_error_are_safe(data):
    app, market = data
    with market.db() as db:
        db.execute("UPDATE runs SET error=? WHERE id='company-run'", ('<svg onload=bad>\nSet-Cookie: SOURCE_SECRET\nMissing /Volumes/Private Data/source.json',))
    task = job('company', 'US:SYNTH', 'company-run', status='retry_wait', next_at=1792288800,
               error='<script>bad failure</script>\nAuthorization: Bearer QUEUE_SECRET\n{"Cookie": "JSON_SECRET"}\nCould not open /Users/private person/data.json')
    body = _queue_table(app, market, [task])
    assert '最早重试' in body and '2026-10-18' in body
    assert 'action="/market/cancel"' in body and 'name="jid" value="job-company"' in body
    assert 'synthetic&lt;&amp;token' in body
    assert '&lt;script&gt;bad failure&lt;/script&gt;' in body and '&lt;svg onload=bad&gt;' in body
    for secret in ('SOURCE_SECRET', 'QUEUE_SECRET', 'JSON_SECRET', '/Volumes/', '/Users/', 'private person', 'Private Data', '<script>', '<svg'):
        assert secret not in body
    assert '本地路径已隐藏' in body and '敏感凭据或响应头已隐藏' in body
    running = _queue_table(app, market, [job('company', 'US:SYNTH', status='running', cancel_requested=1)])
    assert '已请求取消，等待执行器确认' in running
    assert 'action="/market/cancel"' not in running


@pytest.mark.parametrize('value', [None, 0, -1, True, '1792288800', float('nan'), float('inf'), 10 ** 30])
def test_invalid_retry_time_is_unknown(value):
    assert _retry_time({'next_at': value}) == '重试时刻尚未核实'


@pytest.mark.parametrize('message', [
    'Transport failed at https://qa-user:SYNTHETIC_PASSWORD@example.invalid/api?token=SYNTHETIC_TOKEN',
    'Transport failed at https://SYNTHETIC_TOKEN@example.invalid/api',
    'password=SYNTHETIC_PASSWORD token=SYNTHETIC_TOKEN',
    'access_token=SYNTHETIC_TOKEN',
    'client_secret=SYNTHETIC_SECRET',
    'Authori\x00zation: SYNTHETIC_SECRET',
])
def test_credentials_and_hidden_control_characters_are_not_exposed(message):
    result = _queue_error(message)
    assert 'SYNTHETIC_' not in result and '\x00' not in result
    assert '敏感凭据或响应头已隐藏' in result


def test_enrichment_is_read_only_and_batches_thirty_jobs(data):
    _, market = data
    statements = []

    def read_only():
        connection = sqlite3.connect('file:' + str(market.path) + '?mode=ro', uri=True)
        connection.row_factory = sqlite3.Row
        connection.set_trace_callback(statements.append)
        return connection

    tasks = [job('company', 'US:SYNTH', 'company-run', id='job-' + str(i)) for i in range(31)]
    context = _queue_context(SimpleNamespace(db=read_only), tasks)
    assert len(context) == 30
    assert len(statements) <= 6  # Independent of displayed row count.
    assert all(sql.lstrip().upper().startswith('SELECT ') for sql in statements)
    body = _queue_table(SimpleNamespace(token='synthetic-token'), market, tasks)
    assert body.count('<tr>') == 31  # Header plus the bounded 30 rows.
