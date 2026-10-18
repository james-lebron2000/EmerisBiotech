"""Synthetic UI checks, independent of real drug ownership or clinical validity."""
import hashlib
import json
import re
from html.parser import HTMLParser
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from vbt.market import Market
from vbt.market_asset_view import _timestamp, render


class Document(HTMLParser):
    def __init__(self, body):
        super().__init__(convert_charrefs=True)
        self.elements = []
        self.text = []
        self.feed(body)

    def handle_starttag(self, tag, attributes):
        self.elements.append((tag, dict(attributes)))

    def handle_data(self, value):
        self.text.append(value)

    def find(self, tag, **attrs):
        return [a for name, a in self.elements if name == tag and all(a.get(k) == v for k, v in attrs.items())]


@pytest.fixture
def app(tmp_path):
    app = SimpleNamespace(workspace=tmp_path / 'workspace', state=tmp_path / 'state')
    app.market = Market(app.workspace, app.state)
    return app


def company(app, name='Synthetic company', symbol='ONE'):
    app.market.add_company('US', symbol, name, ['Synthetic Sponsor'], 'Synthetic fixture only')
    return 'US:' + symbol


def put_trial(app, nct='NCT00000001', drugs=None, company_id='US:ONE', **extra):
    data = dict(interventions=drugs or [drug('Synthetic candidate')], phases=['PHASE2'],
                conditions=['Synthetic indication'], title='Synthetic title', sponsor='Synthetic Sponsor',
                status='COMPLETED', source_updated='2018-04-01', source_url='https://clinicaltrials.gov/study/' + nct)
    data.update(extra)
    serial = json.dumps(data)
    with app.market.db() as db:
        db.execute('INSERT INTO trials VALUES (?,?,?,?,?)',
                   (company_id, nct, serial, hashlib.sha256(serial.encode()).hexdigest(), '2026-10-17T01:00:00+00:00'))


def drug(name, role='EXPERIMENTAL'):
    return dict(type='DRUG', name=name, arm_roles=[role] if role else [])


def cards(body):
    return Document(body).find('article', **{'class': 'card asset-card'})


def test_empty_state_has_labeled_compact_filters_and_no_invented_results(app):
    body = render(app, {})
    doc = Document(body)
    assert '先选择一家证券主体' in body
    assert '0 家已配置证券主体' in body
    assert not cards(body)
    assert '全市场完整管线' in body and '权益与授权地域均待核对' in body
    assert doc.find('form', action='/assets', method='get')
    assert {label['for'] for label in doc.find('label')} == {'asset-company', 'asset-query', 'asset-role', 'asset-company-search', 'asset-company-market'}
    assert doc.find('select', id='asset-company')[0]['required'] is None
    assert not doc.find('script') and not doc.find('table')


def test_company_results_have_correct_distinct_trial_denominators_and_unknown_rights(app):
    cid = company(app)
    put_trial(app, drugs=[drug('Candidate A'), drug('Candidate B')])
    put_trial(app, 'NCT00000002', drugs=[drug('Candidate A')], phases=['PHASE3'])
    body = render(app, {'company': cid})
    assert len(cards(body)) == 2
    assert '匹配名称</span><div class="metric">2</div>' in body
    assert '关联登记试验</span><div class="metric">2</div>' in body
    assert '药物权益</span><div class="metric">待核对</div>' in body
    assert 'PHASE2 / PHASE3' in body and '不推断药物最高研发阶段' in body
    assert '2 个资产' not in body and '临床成功' not in body
    assert body.count('class="asset-evidence"') == 2
    assert body.count('class="asset-source"') == 3
    assert not any('open' in attrs for attrs in Document(body).find('details'))


def test_local_evidence_link_opens_registry_observation_without_json_nct(app):
    from vbt.market_view import render as registry_render
    cid = company(app)
    company(app, symbol='TWO')
    put_trial(app, source_url=None)
    put_trial(app, company_id='US:TWO', source_url=None, title='Other company only')
    app.token = 'synthetic-csrf-token'
    links = Document(render(app, {'company': cid})).find('a')
    link = next(row['href'] for row in links if row.get('href', '').startswith('/market?')
                and parse_qs(urlsplit(row['href']).query).get('q') == ['NCT00000001'])
    query = {key: values[0] for key, values in parse_qs(urlsplit(link).query).items()}
    assert query == {'company': cid, 'q': 'NCT00000001'}
    body = registry_render(app, query)
    assert '<span class="result-count">1</span>' in body
    assert 'Synthetic title' in body and 'Other company only' not in body


def test_literal_casefold_search_and_role_filter_do_not_leak_other_company(app):
    cid = company(app)
    company(app, symbol='TWO')
    put_trial(app, drugs=[drug('Candidate_%'), drug('Candidate other'), drug('Comparator', 'ACTIVE_COMPARATOR')])
    put_trial(app, 'NCT00000002', [drug('Candidate_%')], company_id='US:TWO')
    body = render(app, {'company': cid, 'q': 'cAnDiDaTe_%', 'role': 'experimental_arm'})
    assert len(cards(body)) == 1 and 'Candidate_%' in body
    assert 'Candidate other</h3>' not in body and 'Comparator</h3>' not in body
    assert 'NCT00000002' not in body
    assert '关键词：cAnDiDaTe_%' in body
    assert 'selected' in Document(body).find('option', value='experimental_arm')[0]
    assert Document(body).find('input', name='q')[0]['value'] == 'cAnDiDaTe_%'
    assert '公司全部 3 个登记名称' in body


@pytest.mark.parametrize('query', ['NCT00000001', 'synthetic INDICATION'])
def test_search_includes_trial_and_condition(app, query):
    cid = company(app)
    put_trial(app)
    assert len(cards(render(app, {'company': cid, 'q': query}))) == 1


def test_pagination_keeps_only_supported_filters_without_duplicate_results(app):
    cid = company(app)
    put_trial(app, drugs=[drug(f'Candidate {number:02}') for number in range(52)])
    filters = {'company': cid, 'q': 'Candidate', 'role': 'experimental_arm', 'unexpected': 'untrusted value'}
    first = render(app, filters)
    first_doc = Document(first)
    assert len(cards(first)) == 50 and '显示第 1–50 个名称，共 52 个' in first
    next_href = first_doc.find('a', rel='next')[0]['href']
    next_filters = {k: v[0] for k, v in parse_qs(urlsplit(next_href).query).items()}
    assert next_filters == {'company': cid, 'q': 'Candidate', 'role': 'experimental_arm', 'offset': '50'}
    second = render(app, next_filters)
    assert len(cards(second)) == 2 and '显示第 51–52 个名称，共 52 个' in second
    assert 'Candidate 49</h3>' in first and 'Candidate 50</h3>' not in first
    assert 'Candidate 50</h3>' in second and 'Candidate 49</h3>' not in second
    assert not Document(second).find('a', rel='next')
    prev = Document(second).find('a', rel='prev')[0]['href']
    assert parse_qs(urlsplit(prev).query)['offset'] == ['0']


@pytest.mark.parametrize('offset', ['not-a-number', '-3', None, '999999999'])
def test_invalid_or_stale_offsets_still_show_available_records(app, offset):
    cid = company(app)
    put_trial(app)
    body = render(app, {'company': cid, 'offset': offset})
    assert len(cards(body)) == 1 and '显示第 1–1 个名称' in body


def test_missing_results_and_unknown_company_are_explicit(app):
    cid = company(app)
    body = render(app, {'company': cid, 'q': 'missing'})
    assert '没有匹配的登记名称关联' in body and '不能据此判定公司没有研发管线' in body
    assert not cards(body)
    with pytest.raises(ValueError, match='公司尚未配置'):
        render(app, {'company': 'US:MISSING'})
    with pytest.raises(ValueError, match='未知的用药角色'):
        render(app, {'company': cid, 'role': '<script>'})


def test_source_roles_dates_lineage_and_checksums_remain_visible_without_upgrade(app):
    cid = company(app)
    put_trial(app, drugs=[drug('Candidate', 'ACTIVE_COMPARATOR')])
    put_trial(app, 'NCT00000002', [drug('Candidate')], phases=['PHASE3'])
    with app.market.db() as db:
        for rid, day, status in [('old', '01', 'succeeded'), ('new', '02', 'succeeded'), ('failed', '03', 'failed')]:
            stamp = '2026-10-' + day + 'T01:00:00+00:00'
            db.execute('INSERT INTO runs VALUES (?,?,?,?,?,?,?,?)', (rid, 'ClinicalTrials.gov', cid, stamp, stamp, status, 1, ''))
        db.execute('INSERT INTO trial_lineage VALUES (?,?,?,?,?)', (cid, 'NCT00000001', '2026-10-01', '2026-10-01', 'old'))
        db.execute('INSERT INTO trial_lineage VALUES (?,?,?,?,?)', (cid, 'NCT00000002', '2026-10-02', '2026-10-02', 'new'))
    body = render(app, {'company': cid})
    assert len(cards(body)) == 1
    assert 'ACTIVE_COMPARATOR' in body and 'EXPERIMENTAL' in body
    assert '仅在比较组中出现' in body and '在实验组中出现' in body
    assert '1 项试验仅保留于历史观察' in body and '不代表试验终止' in body
    assert '最新完整抓取中可见' in body and '最近查询尝试</dt><dd>失败' in body
    assert '来源更新时间</dt><dd><time datetime="2018-04-01"' in body
    assert '本地观测时间</dt><dd><time datetime="2026-10-17T01:00:00+00:00"' in body
    assert '2026-10-17 09:00 UTC+8' in body
    assert '数据 SHA-256' in body and '本条来源批次</dt><dd><code>old</code>' in body
    assert '最近完整批次</dt><dd><code>new</code>' in body


@pytest.mark.parametrize('url', ['javascript:alert(1)', 'https://clinicaltrials.gov.evil.test/study/NCT00000001',
                                 'https://user@clinicaltrials.gov/study/NCT00000001',
                                 'https://clinicaltrials.gov/study/NCT00000002',
                                 'https://clinicaltrials.gov/study/NCT00000001?x=1'])
def test_untrusted_source_urls_are_text_only_and_not_executable(app, url):
    cid = company(app)
    put_trial(app, source_url=url)
    body = render(app, {'company': cid})
    assert '来源链接待核对' in body
    assert not [a for a in Document(body).find('a') if a.get('href') == url]
    assert url in ''.join(Document(body).text)


def test_safe_registry_link_and_all_untrusted_text_escape(app):
    payload = '\"><script>alert(1)</script><img src=x onerror=evil()>'
    cid = company(app, payload)
    put_trial(app, drugs=[drug(payload)], title=payload, sponsor=payload, status=payload,
              conditions=[payload], phases=[payload])
    body = render(app, {'company': cid})
    doc = Document(body)
    assert '&lt;script&gt;' in body and '&lt;img' in body
    assert not doc.find('script') and not doc.find('img')
    assert not any(key.startswith('on') for _, attrs in doc.elements for key in attrs)
    source = doc.find('a', href='https://clinicaltrials.gov/study/NCT00000001')[0]
    assert source['rel'] == 'noopener noreferrer' and source['target'] == '_blank'
    filtered = render(app, {'company': cid, 'q': payload})
    assert Document(filtered).find('input', name='q')[0]['value'] == payload
    assert '<script>' not in filtered


def test_render_is_read_only(app):
    cid = company(app)
    put_trial(app)
    with app.market.db() as db:
        before = list(db.iterdump())
    render(app, {'company': cid})
    render(app, {'company': cid, 'q': 'NCT00000001'})
    with app.market.db() as db:
        assert list(db.iterdump()) == before


def test_date_precision_unknown_timezone_and_missing_evidence_are_not_filled():
    assert '>2018-04-01</time>' in _timestamp('2018-04-01')
    assert '时区未提供' in _timestamp('2026-10-17T01:00:00')
    assert '未记载' in _timestamp(None)
    assert '&lt;script&gt;' in _timestamp('<script>')


def company_cards(body):
    return [attrs for attrs in Document(body).find('article') if 'data-company-id' in attrs]


def put_attempt(app, cid, rid, status, *, started='2020-10-16T01:00:00+00:00',
                finished='2020-10-16T02:00:00+00:00', count=0, error=''):
    with app.market.db() as db:
        db.execute('INSERT INTO runs VALUES (?,?,?,?,?,?,?,?)',
                   (rid, 'ClinicalTrials.gov', cid, started, finished, status, count, error))


def test_default_company_directory_is_clickable_and_missing_attempt_is_unknown(app):
    cid = company(app)
    body = render(app, {})
    assert [row['data-company-id'] for row in company_cards(body)] == [cid]
    assert Document(body).find('a', href='/assets?company=US%3AONE')
    assert '尚未记录查询' in body and '最近查询时间</dt><dd>未知' in body
    assert '结果数量未知' in body and '每页最多 18 家' in body
    assert '/coverage#collection-queue' in body
    assert not cards(body)


@pytest.mark.parametrize('query', ['us:one', 'synthetic COMPANY', 'specific sponsor_%'])
def test_company_directory_searches_literal_names_codes_and_stored_aliases(app, query):
    cid = company(app)
    company(app, name='Other business', symbol='TWO')
    with app.market.db() as db:
        db.execute('UPDATE companies SET aliases=? WHERE id=?', (json.dumps(['Specific Sponsor_%']), cid))
    body = render(app, {'company_q': query})
    assert [row['data-company-id'] for row in company_cards(body)] == [cid]
    assert Document(body).find('input', name='company_q')[0]['value'] == query
    assert '匹配 1 家' in body


def test_company_directory_no_match_is_explicit_and_not_a_no_pipeline_claim(app):
    company(app)
    body = render(app, {'company_q': 'unmatched'})
    assert not company_cards(body)
    assert '没有匹配的已配置公司' in body and '没有入口不表示公司没有管线' in body


def seed_company_directory(app, count=24):
    for number in range(count):
        company(app, name=f'Directory company {number:02}', symbol=f'S{number:02}')


def test_company_directory_pagination_is_bounded_keeps_search_and_returns_context(app):
    seed_company_directory(app)
    first = render(app, {'company_q': 'Directory'})
    assert len(company_cards(first)) == 18
    next_url = Document(first).find('a', rel='next')[0]['href']
    filters = {key: values[0] for key, values in parse_qs(urlsplit(next_url).query).items()}
    assert filters == {'company_q': 'Directory', 'company_offset': '18'}
    second = render(app, filters)
    assert len(company_cards(second)) == 6 and '显示第 19–24 家' in second
    assert not ({r['data-company-id'] for r in company_cards(first)} & {r['data-company-id'] for r in company_cards(second)})
    link = next(a for a in Document(second).find('a') if a.get('href', '').startswith('/assets?company='))
    selected_q = {key: values[0] for key, values in parse_qs(urlsplit(link['href']).query).items()}
    selected_body = render(app, selected_q)
    back = next(a for a in Document(selected_body).find('a') if a.get('href') == '/assets?company_q=Directory&company_offset=18')
    assert back
    assert Document(selected_body).find('input', name='company_q')[0]['value'] == 'Directory'
    assert Document(selected_body).find('input', name='company_offset')[0]['value'] == '18'


@pytest.mark.parametrize('value,expected', [('1', 0), ('17', 0), ('19', 18), ('999999', 18), ('bad', 0), ('-3', 0)])
def test_company_directory_offsets_align_clamp_and_do_not_overlap(app, value, expected):
    seed_company_directory(app)
    body = render(app, {'company_offset': value})
    ids = [row['data-company-id'] for row in company_cards(body)]
    assert ids[0] == f'US:S{expected:02}'
    assert len(ids) == (18 if expected == 0 else 6)


def test_selected_company_header_uses_same_company_registry_and_disclosure_routes(app):
    cid = company(app)
    body = render(app, {'company': cid, 'q': 'Synthetic'})
    doc = Document(body)
    assert doc.find('a', href='/market?company=US%3AONE')
    assert doc.find('a', href='/disclosures?company=US%3AONE')
    assert '同公司官方披露与缺口' in body and '当前证券：Synthetic company · US:ONE' in body


def test_failed_latest_attempt_preserves_actual_stored_count_and_does_not_become_zero_result(app):
    cid = company(app)
    put_trial(app)
    put_trial(app, 'NCT00000002')
    put_attempt(app, cid, 'old-success', 'succeeded', count=9)
    put_attempt(app, cid, 'new-failure', 'failed', started='2020-10-17T01:00:00+00:00',
                finished='2020-10-17T02:00:00+00:00', error='Synthetic source failure')
    body = render(app, {})
    assert '<strong>2</strong> 项已存登记试验' in body
    assert '最近查询失败' in body and '失败或不完整结果不能解释为没有管线' in body
    assert '最近完整查询返回零条' not in body and '返回 9 条' not in body
    assert '2020-10-17 10:00 UTC+8' in body and 'Synthetic source failure' in body


def test_successful_zero_is_scoped_and_retained_history_is_not_termination(app):
    cid = company(app)
    put_attempt(app, cid, 'zero', 'succeeded', count=0)
    empty = render(app, {})
    assert '<strong>0</strong> 项已存登记试验' in empty
    assert '最近完整查询返回零条' in empty and '此来源范围的零结果不代表公司没有研发管线' in empty
    put_trial(app)
    retained = render(app, {})
    assert '<strong>1</strong> 项已存登记试验' in retained
    assert '仍保留历史观察' in retained and '不表示项目终止' in retained


def test_unknown_query_time_and_missing_count_stay_unverified(app):
    cid = company(app)
    put_attempt(app, cid, 'missing-time', 'succeeded', started='unknown-time', count=0)
    body = render(app, {})
    assert '查询时间待核实' in body and '最近结果数量未知' in body
    assert '最近完整查询返回零条' not in body


def test_active_queue_and_latest_query_failure_are_separate(app):
    from vbt.market_jobs import enqueue
    cid = company(app)
    put_attempt(app, cid, 'failed', 'failed')
    enqueue(app.market, 'company', cid)
    body = render(app, {})
    assert '当前队列</dt><dd>已排队' in body and '最近查询失败' in body


def test_company_directory_escapes_search_company_and_error_text(app):
    attack = '\"><img src=x onerror=evil()><script>evil()</script>'
    cid = company(app, name=attack)
    put_attempt(app, cid, 'failure', 'failed', error=attack)
    body = render(app, {'company_q': '<img'})
    parsed = Document(body)
    assert len(company_cards(body)) == 1 and '&lt;img' in body
    assert not parsed.find('img') and not parsed.find('script')
    assert not any(key.startswith('on') for _, attrs in parsed.elements for key in attrs)
    assert parsed.find('input', name='company_q')[0]['value'] == '<img'
    assert 'company_q=%3Cimg' in body


@pytest.mark.parametrize('value,expected_start,expected_count', [('1', 1, 50), ('49', 1, 50), ('51', 51, 2)])
def test_asset_offsets_align_to_existing_fifty_name_pages(app, value, expected_start, expected_count):
    cid = company(app)
    put_trial(app, drugs=[drug(f'Candidate {number:02}') for number in range(52)])
    body = render(app, {'company': cid, 'offset': value})
    assert len(cards(body)) == expected_count
    assert f'显示第 {expected_start}–' in body
    assert ('Candidate 00</h3>' in body) == (expected_start == 1)
    assert ('Candidate 50</h3>' in body) == (expected_start == 51)


def test_each_trial_is_individually_collapsed_with_complete_evidence_retained(app):
    cid = company(app)
    put_trial(app)
    put_trial(app, 'NCT00000002', phases=['PHASE3'], title='Full synthetic title remains available')
    body = render(app, {'company': cid})
    doc = Document(body)
    trials = doc.find('details', **{'class': 'asset-trial-details'})
    assert len(trials) == 2 and all('open' not in trial for trial in trials)
    for nct, phase, title in [('NCT00000001', 'PHASE2', 'Synthetic title'),
                              ('NCT00000002', 'PHASE3', 'Full synthetic title remains available')]:
        summary = body.split('data-nct="' + nct + '"', 1)[1].split('</summary>', 1)[0]
        assert '<strong>' + nct + '</strong>' in summary
        assert 'class="asset-trial-summary-title">' + title in summary
        assert 'COMPLETED · ' + phase in summary
    assert body.count('class="asset-source"') == 2
    assert body.count('数据 SHA-256') == 2
    assert 'Full synthetic title remains available' in body
    assert '逐项核对 2 条试验依据' in body


def filters_from_link(link):
    return {key: value[0] for key, value in parse_qs(urlsplit(link['href']).query).items()}


def role_links(body):
    section = body.split('<nav class="asset-role-tabs"', 1)[1].split('</nav>', 1)[0]
    return Document('<nav' + section + '</nav>').find('a')


def test_selected_company_has_actual_attempt_queue_and_separate_review_boundaries(app):
    from vbt.market_jobs import enqueue
    cid = company(app)
    other = company(app, symbol='TWO')
    put_trial(app)
    put_trial(app, 'NCT00000002')
    put_trial(app, 'NCT00000003', company_id=other)
    put_attempt(app, cid, 'failed', 'failed', error='<img src=x onerror=evil()>', count=0)
    put_attempt(app, other, 'unrelated-success', 'succeeded', count=999)
    enqueue(app.market, 'company', cid)
    body = render(app, {'company': cid})
    panel = body.split('id="asset-context-title"', 1)[1].split('id="asset-filter-title"', 1)[0]
    assert '<strong>2</strong> 项 NCT' in panel and '999' not in panel
    assert '最近查询失败' in panel and '失败或不完整结果不能解释为没有管线' in panel
    assert '当前公司采集队列</dt><dd>已排队' in panel
    assert '2020-10-16 10:00 UTC+8' in panel
    assert '记录完整性' in panel and '本页未重新校验来源原件与校验值' in panel
    assert '科学审查' in panel and '尚未在此页判定' in panel
    assert '本页不确认人工签署、所有权或授权地域' in panel
    assert not Document(body).find('img') and '&lt;img' in body
    assert Document(body).find('a', href='/coverage#collection-queue')
    assert Document(body).find('a', href='#asset-results')
    assert Document(body).find('section', id='asset-results')[0]['tabindex'] == '-1'


def test_selected_company_missing_or_unknown_attempt_is_not_a_completed_zero(app):
    cid = company(app)
    missing = render(app, {'company': cid})
    assert '尚未记录查询' in missing and '结果数量未知' in missing
    assert '未记录活跃任务' in missing and '最近完整查询返回零条' not in missing
    put_attempt(app, cid, 'bad-time', 'succeeded', started='unresolved', count=0)
    unknown = render(app, {'company': cid})
    assert '查询时间待核实' in unknown and '最近结果数量未知' in unknown
    assert '最近完整查询返回零条' not in unknown


def test_raw_phase_filter_is_exact_preserves_all_trial_evidence_and_dynamic_options(app):
    cid = company(app)
    put_trial(app, drugs=[drug('Mixed candidate')], phases=['Phase II/III'])
    put_trial(app, 'NCT00000002', [drug('Mixed candidate')], phases=['Clinical POC'])
    put_trial(app, 'NCT00000003', [drug('Other candidate')], phases=['Pre-clinical'])
    body = render(app, {'company': cid, 'phase': 'Phase II/III'})
    doc = Document(body)
    assert len(cards(body)) == 1 and 'Mixed candidate</h3>' in body
    assert 'Other candidate</h3>' not in body
    assert all(doc.find('option', value=value) for value in ['Phase II/III', 'Clinical POC', 'Pre-clinical'])
    assert 'selected' in doc.find('option', value='Phase II/III')[0]
    assert 'Phase II/III' in body and 'Clinical POC' in body
    assert 'NCT00000001' in body and 'NCT00000002' in body and 'NCT00000003' not in body
    assert body.count('class="asset-source"') == 2
    assert '卡片仍保留该名称的全部试验依据' in body
    assert not cards(render(app, {'company': cid, 'phase': 'PHASE2'}))


def test_raw_phase_whitespace_is_not_silently_recoded(app):
    cid = company(app)
    put_trial(app, phases=[' Phase II/III '])
    assert len(cards(render(app, {'company': cid, 'phase': ' Phase II/III '}))) == 1
    assert not cards(render(app, {'company': cid, 'phase': 'Phase II/III'}))


def seed_freshness(app, cid):
    put_attempt(app, cid, 'old', 'succeeded', count=2)
    put_attempt(app, cid, 'new', 'succeeded', started='2020-10-17T01:00:00+00:00',
                finished='2020-10-17T02:00:00+00:00', count=1)
    put_trial(app, drugs=[drug('History candidate'), drug('Mixed candidate')])
    put_trial(app, 'NCT00000002', [drug('Current candidate'), drug('Mixed candidate')])
    put_trial(app, 'NCT00000003', [drug('Unknown candidate')])
    with app.market.db() as db:
        for nct, run in [('NCT00000001', 'old'), ('NCT00000002', 'new')]:
            db.execute('INSERT INTO trial_lineage VALUES (?,?,?,?,?)', (cid, nct, '2020-10-16', '2020-10-17', run))


@pytest.mark.parametrize('freshness,names', [
    ('seen_in_latest', ['Current candidate', 'Mixed candidate']),
    ('not_seen_in_latest', ['History candidate']),
    ('unknown', ['Unknown candidate']),
])
def test_freshness_filters_keep_unknown_and_mixed_history_semantics(app, freshness, names):
    cid = company(app)
    seed_freshness(app, cid)
    body = render(app, {'company': cid, 'freshness': freshness})
    assert len(cards(body)) == len(names)
    for name in ['Current candidate', 'Mixed candidate', 'History candidate', 'Unknown candidate']:
        assert (name + '</h3>' in body) == (name in names)
    if freshness == 'seen_in_latest':
        assert '1 项试验仅保留于历史观察' in body
        assert 'NCT00000001' in body and 'NCT00000002' in body
    assert 'selected' in Document(body).find('option', value=freshness)[0]


def test_trial_count_sort_has_stable_name_ties_and_no_priority_inference(app):
    cid = company(app)
    put_trial(app, drugs=[drug('Alpha'), drug('Beta'), drug('Gamma')])
    put_trial(app, 'NCT00000002', [drug('Beta'), drug('Gamma')])
    body = render(app, {'company': cid, 'sort': 'trials'})
    assert body.index('>Beta</h3>') < body.index('>Gamma</h3>') < body.index('>Alpha</h3>')
    assert '试验数排序仅帮助浏览' in body
    assert 'selected' in Document(body).find('option', value='trials')[0]
    default = render(app, {'company': cid})
    assert default.index('>Alpha</h3>') < default.index('>Beta</h3>')


def test_role_shortcuts_count_other_roles_within_other_filters_and_keep_context(app):
    cid = company(app)
    put_trial(app, drugs=[drug('Candidate active'), drug('Candidate control', 'ACTIVE_COMPARATOR'),
                          drug('Candidate unknown', None), drug('Unrelated')])
    body = render(app, {'company': cid, 'q': 'Candidate', 'role': 'experimental_arm', 'phase': 'PHASE2',
                        'freshness': 'unknown', 'sort': 'trials', 'offset': '50',
                        'company_q': 'Synthetic', 'company_offset': '18'})
    links = role_links(body)
    assert len(links) == 4 and len(cards(body)) == 1
    assert sum('aria-current' in link for link in links) == 1
    assert '全部角色 <span class="cell-meta">3</span>' in body
    assert '仅在比较组中出现 <span class="cell-meta">1</span>' in body
    for link in links:
        params = filters_from_link(link)
        assert params['company'] == cid and params['q'] == 'Candidate'
        assert params['phase'] == 'PHASE2' and params['freshness'] == 'unknown' and params['sort'] == 'trials'
        assert params['company_q'] == 'Synthetic' and params['company_offset'] == '18'
        assert 'offset' not in params


def test_each_filter_can_be_removed_without_losing_company_or_other_filters(app):
    cid = company(app)
    put_trial(app)
    filters = {'company': cid, 'q': 'Synthetic', 'role': 'experimental_arm', 'phase': 'PHASE2',
               'freshness': 'unknown', 'sort': 'trials', 'offset': '50',
               'company_q': 'Directory', 'company_offset': '18'}
    body = render(app, filters)
    links = Document(body).find('a', **{'class': 'asset-clear-chip'})
    assert len(links) == 5
    missing = []
    for link in links:
        params = filters_from_link(link)
        removed = set(filters) - {'offset'} - set(params)
        assert len(removed) == 1
        missing.extend(removed)
        assert params == {key: value for key, value in filters.items() if key not in removed | {'offset'}}
        assert '移除' in link['aria-label']
    assert set(missing) == {'q', 'role', 'phase', 'freshness', 'sort'}
    clear = Document(body).find('a', **{'class': 'text-action'})[0]
    assert filters_from_link(clear) == {'company': cid, 'company_q': 'Directory', 'company_offset': '18'}


def test_advanced_filters_and_context_survive_result_pagination(app):
    cid = company(app)
    put_trial(app, drugs=[drug(f'Candidate {number:02}') for number in range(52)])
    filters = {'company': cid, 'q': 'Candidate', 'role': 'experimental_arm', 'phase': 'PHASE2',
               'freshness': 'unknown', 'sort': 'trials', 'company_q': 'Synthetic', 'company_offset': '18'}
    first = render(app, filters)
    next_params = filters_from_link(Document(first).find('a', rel='next')[0])
    assert next_params == {**filters, 'offset': '50'}
    second = render(app, next_params)
    assert len(cards(second)) == 2
    assert filters_from_link(Document(second).find('a', rel='prev')[0]) == {**filters, 'offset': '0'}


def test_filtered_empty_state_is_distinct_from_uncollected_company_and_clear_is_scoped(app):
    cid = company(app)
    put_trial(app)
    body = render(app, {'company': cid, 'q': 'missing', 'company_q': 'Synthetic', 'company_offset': '18'})
    assert '当前筛选无匹配' in body and '该公司已有 1 个登记名称' in body
    assert '尚未采集、查询零条' not in body
    assert Document(body).find('a', href='/assets?company=US%3AONE&company_q=Synthetic&company_offset=18')
    assert '不能据此判定公司没有研发管线' in body
    empty_cid = company(app, symbol='EMPTY')
    empty = render(app, {'company': empty_cid})
    assert '没有匹配的登记名称关联' in empty and '当前筛选无匹配' not in empty
    assert '未纳入此视图的干预类型' in empty


def test_trial_links_distinguish_local_company_observation_from_external_current_page(app):
    cid = company(app)
    put_trial(app)
    body = render(app, {'company': cid})
    local = Document(body).find('a', href='/market?company=US%3AONE&q=NCT00000001')
    assert len(local) == 1 and 'target' not in local[0]
    assert '外部当前页面，内容可能已更新' in body
    assert Document(body).find('a', href='https://clinicaltrials.gov/study/NCT00000001')[0]['target'] == '_blank'


def test_advanced_filter_values_and_removal_labels_escape_untrusted_text(app):
    payload = '\"><script>evil()</script><img src=x onerror=evil()>'
    cid = company(app)
    put_trial(app, phases=[payload], conditions=[payload])
    body = render(app, {'company': cid, 'q': payload, 'phase': payload, 'company_q': payload})
    doc = Document(body)
    assert len(cards(body)) == 1 and '&lt;script&gt;' in body
    assert not doc.find('script') and not doc.find('img')
    assert not any(key.startswith('on') for _, attrs in doc.elements for key in attrs)
    assert 'selected' in doc.find('option', value=payload)[0]
    assert any(payload in link['aria-label'] for link in doc.find('a', **{'class': 'asset-clear-chip'}))
    unseen = render(app, {'company': cid, 'phase': '<unknown>'})
    assert '当前来源未见' in unseen and 'selected' in Document(unseen).find('option', value='<unknown>')[0]


@pytest.mark.parametrize('params,message', [({'freshness': 'invented'}, '未知的批次可见性'),
                                           ({'sort': 'probability'}, '未知的排序方式')])
def test_unsupported_new_filters_fail_explicitly(app, params, message):
    cid = company(app)
    with pytest.raises(ValueError, match=message):
        render(app, {'company': cid, **params})


def test_company_market_is_exact_bounded_and_preserved_through_directory_paging(app):
    seed_company_directory(app, 20)
    app.market.add_company('HK', '1234', 'Directory HK', ['HK sponsor'], 'Synthetic fixture')
    app.market.add_company('A', '600001', 'Directory A', ['A sponsor'], 'Synthetic fixture')
    body = render(app, {'company_market': 'US', 'company_q': 'Directory'})
    assert len(company_cards(body)) == 18
    assert all(row['data-company-id'].startswith('US:') for row in company_cards(body))
    next_query = filters_from_link(Document(body).find('a', rel='next')[0])
    assert next_query == {'company_market': 'US', 'company_q': 'Directory', 'company_offset': '18'}
    last = render(app, next_query)
    selected = next(link for link in Document(last).find('a') if link.get('href', '').startswith('/assets?company='))
    selected_body = render(app, filters_from_link(selected))
    assert Document(selected_body).find('input', name='company_market')[0]['value'] == 'US'
    assert Document(selected_body).find('a', href='/assets?company_market=US&company_q=Directory&company_offset=18')
    chips = Document(body).find('a', **{'class': 'asset-clear-chip'})
    assert {link['href'] for link in chips} == {'/assets?company_market=US', '/assets?company_q=Directory'}
    with pytest.raises(ValueError, match='未知的公司入口市场'):
        render(app, {'company_market': '<script>'})


def test_company_query_range_is_collapsed_and_asset_history_links_are_scoped(app):
    cid = company(app)
    put_attempt(app, cid, 'failed', 'failed', error='Retained synthetic reason')
    directory = render(app, {})
    folds = Document(directory).find('details', **{'class': 'source-details company-state-details'})
    assert len(folds) == 1 and 'open' not in folds[0]
    assert 'Retained synthetic reason' in directory and '失败或不完整结果不能解释为没有管线' in directory
    put_trial(app)
    selected = render(app, {'company': cid, 'q': 'Synthetic', 'company_market': 'US'})
    doc = Document(selected)
    assert doc.find('a', href='/market?company=US%3AONE&q=NCT00000001#market-changes')
    card = cards(selected)[0]
    permalink = next(a for a in doc.find('a') if urlsplit(a.get('href', '')).fragment == card['id'])
    assert filters_from_link(permalink)['company_market'] == 'US'
    assert doc.find('details', id='asset-evidence-' + card['id'].removeprefix('asset-'))


def navigation_markup(body, identifier):
    return Document('<nav ' + body.split('id="' + identifier + '"', 1)[1].split('</nav>', 1)[0] + '</nav>')


def test_top_and_bottom_pagination_match_all_filters_context_and_visible_page_numbers(app):
    cid = company(app)
    put_trial(app, drugs=[drug(f'Candidate {number:03}') for number in range(103)])
    filters = {'company': cid, 'q': 'Candidate', 'role': 'experimental_arm', 'phase': 'PHASE2',
               'freshness': 'unknown', 'sort': 'trials', 'company_market': 'US',
               'company_q': 'Synthetic & Company', 'company_offset': '18', 'offset': '50'}
    body = render(app, filters)
    assert body.count('第 2 / 3 页') == 2
    assert len(Document(body).find('nav', **{'class': 'pagination asset-pagination'})) == 2
    top, bottom = [navigation_markup(body, 'asset-pagination-' + location) for location in ('top', 'bottom')]
    for direction, expected_offset in [('prev', '0'), ('next', '100')]:
        top_link, bottom_link = top.find('a', rel=direction)[0], bottom.find('a', rel=direction)[0]
        assert top_link['href'] == bottom_link['href']
        assert filters_from_link(top_link) == {**filters, 'offset': expected_offset}
        assert urlsplit(top_link['href']).fragment == 'asset-results'
    assert body.index('id="asset-pagination-top"') < body.index('class="asset-grid"') < body.index('id="asset-pagination-bottom"')
    assert bottom.find('a', href='#asset-filters')


@pytest.mark.parametrize('total,offset,page,has_previous,has_next', [
    (1, '0', '第 1 / 1 页', False, False),
    (52, '1', '第 1 / 2 页', False, True),
    (52, '99999', '第 2 / 2 页', True, False),
])
def test_both_pagers_respect_first_last_single_and_clamped_pages(app, total, offset, page, has_previous, has_next):
    cid = company(app)
    put_trial(app, drugs=[drug(f'Candidate {number:03}') for number in range(total)])
    body = render(app, {'company': cid, 'offset': offset})
    assert body.count(page) == 2
    for location in ('top', 'bottom'):
        nav = navigation_markup(body, 'asset-pagination-' + location)
        assert bool(nav.find('a', rel='prev')) is has_previous
        assert bool(nav.find('a', rel='next')) is has_next


def test_page_navigation_targets_are_unique_focusable_and_return_keeps_company_directory(app):
    cid = company(app)
    body = render(app, {'company': cid, 'company_market': 'US', 'company_q': '<Synthetic>', 'company_offset': '18'})
    doc = Document(body)
    assert doc.find('nav', **{'class': 'asset-page-nav'})
    for target in ('asset-filters', 'asset-results', 'asset-collection'):
        assert len(doc.find('section', id=target)) == 1
        assert doc.find('section', id=target)[0]['tabindex'] == '-1'
        assert doc.find('a', href='#' + target)
    assert '筛选登记名称' in body and '名称与试验依据' in body and '采集状态' in body
    assert doc.find('a', href='/assets?company_market=US&company_q=%3CSynthetic%3E&company_offset=18')
    assert not doc.find('script') and not any(key.startswith('on') for _, attributes in doc.elements for key in attributes)
    assert not doc.find('nav', **{'class': 'pagination asset-pagination'})  # zero matches have no invented page
    default = Document(render(app, {}))
    assert not default.find('nav', **{'class': 'asset-page-nav'})  # no links to absent company-only sections


def test_exact_nct_context_prioritizes_only_the_matching_evidence_and_keeps_all_sources(app):
    from vbt.market_view import render as registry_render
    cid = company(app)
    company(app, symbol='TWO')
    put_trial(app, title='Earlier observation')
    put_trial(app, 'NCT00000002', title='Other trial for the same name')
    put_trial(app, 'NCT00000003', phases=['PHASE3'], title='The queried trial')
    put_trial(app, 'NCT00000003', company_id='US:TWO', title='Other company observation')
    filters = {'company': cid, 'q': 'nct00000003', 'phase': 'PHASE3', 'role': 'experimental_arm',
               'company_market': 'US', 'company_q': 'Synthetic', 'company_offset': '18'}
    body = render(app, filters)
    doc = Document(body)
    assert len(cards(body)) == 1
    assert [source['data-nct'] for source in doc.find('details', **{'class': 'asset-trial-details'})] == [
        'NCT00000003', 'NCT00000001', 'NCT00000002']
    marked = doc.find('details', **{'data-current-query': 'true'})
    assert len(marked) == 1 and marked[0]['data-nct'] == 'NCT00000003'
    assert body.count('class="asset-source"') == 3
    assert '逐项核对 3 条试验依据' in body and '仍保留此名称的全部 3 条试验依据' in body
    assert '关联登记试验</span><div class="metric">3</div>' in body
    assert '阶段、角色与批次也按名称汇总' in body
    assert 'Other company observation' not in body
    for source in doc.find('details', **{'class': 'asset-trial-details'}):
        assert 'open' not in source
    context = re.search(r'<section class="card asset-nct-context".*?</section>', body, re.S).group()
    links = Document(context).find('a')
    assert [filters_from_link(link) for link in links] == [{'company': cid, 'q': 'NCT00000003'}] * 2
    assert urlsplit(links[1]['href']).fragment == 'market-changes'
    app.token = 'synthetic-token'
    registry = registry_render(app, filters_from_link(links[1]))
    assert '<span class="result-count">1</span>' in registry
    assert 'The queried trial' in registry and 'Other company observation' not in registry
    assert 'id="market-changes"' in registry
    permalink = next(link for link in doc.find('a') if urlsplit(link.get('href', '')).fragment == cards(body)[0]['id'])
    assert filters_from_link(permalink) == {**filters, 'offset': '0'}


@pytest.mark.parametrize('query', ['NCT0000000', 'NCT000000001', 'prefix NCT00000001',
                                  'NCT00000001<script>bad()</script>', 'https://example.invalid/NCT00000001'])
def test_only_exact_valid_nct_queries_create_the_trial_context_panel(app, query):
    cid = company(app)
    put_trial(app)
    body = render(app, {'company': cid, 'q': query})
    assert 'asset-nct-context-title' not in body
    assert not Document(body).find('details', **{'data-current-query': 'true'})
    assert not Document(body).find('script')


def test_nct_absent_from_drug_name_view_does_not_claim_no_local_registry_record(app):
    from vbt.market_view import render as registry_render
    cid = company(app)
    put_trial(app, interventions=[{'type': 'PROCEDURE', 'name': 'Synthetic procedure'}])
    body = render(app, {'company': cid, 'q': 'NCT00000001'})
    assert not cards(body)
    assert '此 NCT 尚未出现在该公司已存名称依据中' in body
    assert '其他干预类型或空结果' in body
    assert '此 NCT 已出现在该公司已存名称依据中' not in body
    app.token = 'synthetic-token'
    target = Document(body).find('a', href='/market?company=US%3AONE&q=NCT00000001')[0]
    registry = registry_render(app, filters_from_link(target))
    assert '<span class="result-count">1</span>' in registry
    assert 'Synthetic procedure' in registry


def test_exact_nct_context_survives_pagination_without_changing_filter_parameters(app):
    cid = company(app)
    put_trial(app, drugs=[drug(f'Candidate {number:02}') for number in range(52)])
    filters = {'company': cid, 'q': 'nct00000001', 'company_market': 'US', 'company_q': 'Directory',
               'company_offset': '18', 'role': 'experimental_arm'}
    first = render(app, filters)
    next_params = filters_from_link(Document(first).find('a', rel='next')[0])
    assert next_params == {**filters, 'offset': '50'}
    second = render(app, next_params)
    assert len(cards(first)) == 50 and len(cards(second)) == 2
    assert '当前试验检索 · NCT00000001' in first and '当前试验检索 · NCT00000001' in second
    assert len(Document(second).find('details', **{'data-current-query': 'true'})) == 2
    assert filters_from_link(Document(second).find('a', rel='prev')[0]) == {**filters, 'offset': '0'}


def test_collapsed_trial_titles_are_complete_escaped_and_have_missing_title_fallback(app):
    payload = '<img src=x onerror=evil()> ' + 'Long synthetic title ' * 30
    cid = company(app)
    put_trial(app, title=payload)
    put_trial(app, 'NCT00000002', title=None)
    body = render(app, {'company': cid})
    doc = Document(body)
    assert not doc.find('img') and not doc.find('script')
    assert not any(key.startswith('on') for _, attrs in doc.elements for key in attrs)
    assert ''.join(doc.text).count(payload) == 2  # Summary and expanded retained original.
    assert 'class="asset-trial-summary-title">试验标题未记载</span>' in body
    assert all('open' not in detail for detail in doc.find('details', **{'class': 'asset-trial-details'}))
