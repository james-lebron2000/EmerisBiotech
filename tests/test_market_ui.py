"""Semantic and safety checks for the market UI; all records are synthetic."""
import json
from html.parser import HTMLParser
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from vbt.market import Market
from vbt.market_view import _timestamp, render


class Markup(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.tags = []
        self.forms = []
        self.current_form = None
        self.current_select = None
        self.selections = {}
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.tags.append((tag, attrs))
        if tag == 'form':
            self.current_form = {'attrs': attrs, 'inputs': {}}
            self.forms.append(self.current_form)
        if tag == 'input' and self.current_form is not None:
            self.current_form['inputs'][attrs.get('name')] = attrs.get('value', '')
        if tag == 'select':
            self.current_select = attrs.get('name')
        if tag == 'option' and 'selected' in attrs:
            self.selections[self.current_select] = attrs.get('value')

    def handle_endtag(self, tag):
        if tag == 'form':
            self.current_form = None
        if tag == 'select':
            self.current_select = None


@pytest.fixture
def synthetic_market(tmp_path):
    app = SimpleNamespace(workspace=tmp_path / 'w', state=tmp_path / 'state', token='synthetic-csrf-token')
    market = Market(app.workspace, app.state)
    market.add_company('US', 'TEST', 'Synthetic Pharma', ['Synthetic sponsor'], 'Synthetic fixture')
    with market.db() as db:
        for index in range(205):
            data = {
                'title': 'Synthetic condition trial',
                'sponsor': 'Synthetic sponsor',
                'interventions': [{'name': 'Synthetic drug'}],
                'conditions': ['Synthetic condition'],
                'phases': ['PHASE3'],
                'status': 'TERMINATED',
                'source_updated': '2026-10-16',
                'source_url': 'https://clinicaltrials.gov/study/NCT00000001',
            }
            db.execute('INSERT INTO trials VALUES (?,?,?,?,?)', ('US:TEST', f'NCT{index:08}', json.dumps(data), 'synthetic-hash', '2026-10-17T01:02:03+00:00'))
    return app, market


def test_filter_labels_selections_and_post_exports_remain_intact(synthetic_market):
    app, _ = synthetic_market
    filters = {'market': 'US', 'company': 'US:TEST', 'q': 'Synthetic condition', 'phase': 'PHASE3', 'status': 'TERMINATED'}
    source = render(app, filters)
    markup = Markup(source)
    query_form = next(form for form in markup.forms if form['attrs'].get('action') == '/market')
    assert query_form['attrs']['method'] == 'get'
    assert query_form['inputs']['q'] == filters['q']
    assert markup.selections == {key: value for key, value in filters.items() if key != 'q'}
    export = next(form for form in markup.forms if form['attrs'].get('action') == '/market/export')
    assert export['attrs']['method'] == 'post'
    assert export['inputs'] == {**filters, 'token': app.token}
    for form in markup.forms:
        if form['attrs'].get('method') == 'post':
            assert form['inputs']['token'] == app.token
    labels = {attrs['for'] for tag, attrs in markup.tags if tag == 'label'}
    assert {'market-query', 'market-filter', 'market-company', 'market-phase', 'market-status'} <= labels
    assert any(tag == 'p' and attrs.get('role') == 'status' for tag, attrs in markup.tags)
    assert '已终止' in source and '完成状态不表示临床成功' in source


def test_pagination_retains_every_filter_and_exact_source_times(synthetic_market):
    app, _ = synthetic_market
    filters = {'market': 'US', 'company': 'US:TEST', 'q': 'Synthetic condition', 'phase': 'PHASE3', 'status': 'TERMINATED'}
    markup = Markup(render(app, filters))
    next_link = next(attrs for tag, attrs in markup.tags if tag == 'a' and attrs.get('rel') == 'next')
    assert parse_qs(urlsplit(next_link['href']).query) == {**{key: [value] for key, value in filters.items()}, 'offset': ['200']}
    second = Markup(render(app, {**filters, 'offset': '200'}))
    previous = next(attrs for tag, attrs in second.tags if tag == 'a' and attrs.get('rel') == 'prev')
    assert parse_qs(urlsplit(previous['href']).query) == {**{key: [value] for key, value in filters.items()}, 'offset': ['0']}
    times = [attrs for tag, attrs in second.tags if tag == 'time']
    assert any(attrs.get('title') == attrs.get('datetime') == '2026-10-17T01:02:03+00:00' for attrs in times)
    assert '2026-10-17T01:02:03+00:00' in render(app, filters)


def test_untrusted_search_metadata_and_source_urls_cannot_inject_markup(synthetic_market):
    app, market = synthetic_market
    payload = '\"><img src=x onerror=alert(1)>'
    with market.db() as db:
        row = db.execute('SELECT data FROM trials LIMIT 1').fetchone()
        data = json.loads(row['data'])
        data.update(title=payload, source_url='javascript:alert(1)', conditions=[payload], interventions=[{'name': payload}])
        db.execute('UPDATE trials SET data=?', (json.dumps(data),))
        db.execute('UPDATE companies SET name=?', (payload,))
    source = render(app, {})
    markup = Markup(source)
    assert '&lt;img' in source
    assert not any(tag in ('img', 'script') for tag, _ in markup.tags)
    assert not any(attrs.get('href', '').lower().startswith('javascript:') for _, attrs in markup.tags)
    assert not any(key.startswith('on') for _, attrs in markup.tags for key in attrs)
    query_markup = Markup(render(app, {'q': payload}))
    query_form = next(form for form in query_markup.forms if form['attrs'].get('action') == '/market')
    assert query_form['inputs']['q'] == payload
    assert not any(tag == 'img' for tag, _ in query_markup.tags)


def test_empty_result_keeps_recovery_and_source_controls_accessible(synthetic_market):
    app, _ = synthetic_market
    source = render(app, {'q': 'No synthetic record matches'})
    assert '没有匹配的已采集记录' in source
    assert '查看覆盖进度与缺口' in source
    assert source.index('临床登记结果') < source.index('数据更新与来源')
    assert '添加公司与申办方映射' in source
    markup = Markup(source)
    assert any(form['attrs'].get('action') == '/market/schedule' for form in markup.forms)
    assert any(form['attrs'].get('action') == '/market/company' for form in markup.forms)


@pytest.mark.parametrize('raw, visible', [
    ('2026-10-16', '2026-10-16'),
    ('2026-10-17T01:02:03+00:00', '2026-10-17 09:02 UTC+8'),
    ('2026-10-16T20:04:59Z', '2026-10-17 04:04 UTC+8'),
    ('2026-10-17T01:02:03+08:00', '2026-10-17 01:02 UTC+8'),
    ('2026-10-17T01:02:03', '2026-10-17 01:02 · 时区未提供'),
])
def test_timestamp_precision_timezone_and_original_provenance(raw, visible):
    source = _timestamp(raw)
    assert f'>{visible}</time>' in source
    attrs = next(attrs for tag, attrs in Markup(source).tags if tag == 'time')
    assert attrs['datetime'] == attrs['title'] == raw
    if len(raw) == 10:
        assert '00:00' not in source and 'UTC' not in source and '时区未提供' not in source


def test_visible_scroll_guidance_describes_result_region(synthetic_market):
    app, _ = synthetic_market
    source = render(app, {})
    markup = Markup(source)
    cue = next(attrs for tag, attrs in markup.tags if tag == 'p' and attrs.get('id') == 'market-table-scroll-hint')
    region = next(attrs for tag, attrs in markup.tags if tag == 'div' and attrs.get('role') == 'region')
    assert region['aria-describedby'] == cue['id']
    assert 'sr-only' not in cue['class']
    assert '横向滚动表格' in source
    assert '2026-10-17 09:02 UTC+8' in source
