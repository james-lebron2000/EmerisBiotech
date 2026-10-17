"""Synthetic UI checks, independent of real drug ownership or clinical validity."""
import hashlib
import json
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
    assert {label['for'] for label in doc.find('label')} == {'asset-company', 'asset-query', 'asset-role'}
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
    with app.market.db() as db:
        assert list(db.iterdump()) == before


def test_date_precision_unknown_timezone_and_missing_evidence_are_not_filled():
    assert '>2018-04-01</time>' in _timestamp('2018-04-01')
    assert '时区未提供' in _timestamp('2026-10-17T01:00:00')
    assert '未记载' in _timestamp(None)
    assert '&lt;script&gt;' in _timestamp('<script>')
