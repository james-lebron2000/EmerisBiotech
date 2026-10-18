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


@pytest.mark.parametrize('company',['','US:TEST'])
def test_header_asset_link_preserves_only_the_current_company(synthetic_market,company):
    app,_=synthetic_market
    source=render(app,{'company':company,'q':'NCT00000001','phase':'PHASE3','view':'cards'})
    header=Markup(source.split('</header>',1)[0])
    target=next(attrs['href'] for tag,attrs in header.tags if tag=='a' and attrs.get('href','').startswith('/assets'))
    assert urlsplit(target).path=='/assets'
    assert parse_qs(urlsplit(target).query)==({'company':[company]} if company else {})
    # The target is the actual asset renderer, not a separate inferred identity.
    from vbt.market_asset_view import render as assets
    target_body=assets(app,{key:value[0] for key,value in parse_qs(urlsplit(target).query).items()})
    assert '临床资产研究' in target_body


def test_header_company_context_is_url_encoded_and_cannot_add_markup(synthetic_market):
    app,_=synthetic_market
    payload='US:TEST\"><img src=x onerror=evil()>&q=OTHER'
    source=render(app,{'company':payload})
    header=Markup(source.split('</header>',1)[0])
    target=next(attrs['href'] for tag,attrs in header.tags if tag=='a' and attrs.get('href','').startswith('/assets'))
    assert parse_qs(urlsplit(target).query)=={'company':[payload]}
    assert not any(tag in ('img','script') or any(key.startswith('on') for key in attrs) for tag,attrs in header.tags)


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


def links(markup, **required):
    return [attrs for tag, attrs in markup.tags if tag == 'a' and all(attrs.get(key) == value for key, value in required.items())]


def params(link):
    return {key: value[0] for key, value in parse_qs(urlsplit(link['href']).query).items()}


def trial_cards(markup):
    return [attrs for tag, attrs in markup.tags if tag == 'article' and attrs.get('class') == 'card market-trial-card']


def test_cards_are_fifty_collapsed_records_with_filter_preserving_navigation(synthetic_market):
    app, _ = synthetic_market
    filters = {'market': 'US', 'company': 'US:TEST', 'q': 'Synthetic condition', 'phase': 'PHASE3',
               'status': 'TERMINATED', 'view': 'cards'}
    body = render(app, {**filters, 'ignored': 'untrusted'})
    markup = Markup(body)
    assert len(trial_cards(markup)) == 50 and '每页 50 条' in body
    assert not any(tag == 'table' and attrs.get('class') == 'clinical-table' for tag, attrs in markup.tags)
    assert all('open' not in attrs for tag, attrs in markup.tags if tag == 'details')
    assert '来源更新时间' in body and '已记录 SHA-256' in body and '本页未重新验证原件' in body
    assert params(links(markup, rel='next')[0]) == {**filters, 'offset': '50'}
    second = Markup(render(app, {**filters, 'offset': '50'}))
    assert not ({card['id'] for card in trial_cards(markup)} & {card['id'] for card in trial_cards(second)})
    assert params(links(second, rel='prev')[0]) == {**filters, 'offset': '0'}
    query = next(form for form in markup.forms if form['attrs'].get('action') == '/market')
    assert query['inputs']['view'] == 'cards'
    export = next(form for form in markup.forms if form['attrs'].get('action') == '/market/export')
    assert export['inputs'] == {**{key: value for key, value in filters.items() if key != 'view'}, 'token': app.token}


@pytest.mark.parametrize('view,offset,start,expected', [('table', '1', '1', 200), ('table', '201', '201', 5),
                                                      ('cards', '49', '1', 50), ('cards', '51', '51', 50),
                                                      ('cards', '999999', '201', 5)])
def test_view_offsets_align_and_saved_links_clamp_to_available_page(synthetic_market, view, offset, start, expected):
    app, _ = synthetic_market
    body = render(app, {'company': 'US:TEST', 'view': view, 'offset': offset})
    assert f'显示 {start}–' in body
    if view == 'cards':
        assert len(trial_cards(Markup(body))) == expected
    else:
        assert '每页 200 条' in body


def test_view_switch_and_removable_chips_keep_only_supported_context(synthetic_market):
    app, _ = synthetic_market
    filters = {'market': 'US', 'company': 'US:TEST', 'q': 'Synthetic', 'phase': 'PHASE3',
               'status': 'TERMINATED', 'view': 'cards'}
    body = render(app, {**filters, 'offset': '50', 'return_to': 'https://evil.test'})
    markup = Markup(body)
    chips = links(markup, **{'class': 'asset-clear-chip'})
    assert len(chips) == 5
    for link in chips:
        values = params(link)
        assert len(set(filters) - set(values)) == 1 and values['view'] == 'cards'
        assert 'offset' not in values and 'return_to' not in values
    nav = body.split('aria-label="登记结果展示方式"', 1)[1].split('</nav>', 1)[0]
    modes = links(Markup('<nav' + nav + '</nav>'))
    table = next(link for link in modes if 'view' not in params(link))
    assert params(table) == {key: value for key, value in {**filters, 'offset': '0'}.items() if key != 'view'}
    clear = links(markup, **{'class': 'text-action'})[0]
    assert params(clear) == {'company': 'US:TEST', 'view': 'cards'}
    assert links(markup, href='#market-results') and links(markup, href='#market-changes')


@pytest.mark.parametrize('view', ['table', 'cards'])
def test_legacy_missing_json_keys_keep_local_trial_readable_without_fake_source(synthetic_market, view):
    app, market = synthetic_market
    with market.db() as db:
        db.execute("DELETE FROM trials WHERE nct!='NCT00000000'")
        db.execute('UPDATE trials SET data=?', (json.dumps({'interventions': [{'name': 'Legacy original', 'arm_roles': ['ACTIVE_COMPARATOR']}]}),))
    body = render(app, {'company': 'US:TEST', 'view': view})
    markup = Markup(body)
    assert '试验标题未记载' in body and '未提供适应症' in body and '未知' in body
    assert 'Legacy original' in body and 'ACTIVE_COMPARATOR' in body
    assert not any(link.get('href', '').startswith('https://clinicaltrials.gov') for link in links(markup))
    assert links(markup, href='/assets?company=US%3ATEST&q=NCT00000000')
    assert links(markup, href='/market?company=US%3ATEST&q=NCT00000000#market-changes')


@pytest.mark.parametrize('url,accepted', [('https://clinicaltrials.gov/study/NCT00000000', True),
                                        ('https://clinicaltrials.gov/study/NCT00000001', False),
                                        ('https://clinicaltrials.gov.evil.test/study/NCT00000000', False),
                                        ('https://clinicaltrials.gov/study/NCT00000000?changed=1', False)])
def test_trial_external_link_must_match_actual_row_nct(synthetic_market, url, accepted):
    app, market = synthetic_market
    with market.db() as db:
        db.execute("DELETE FROM trials WHERE nct!='NCT00000000'")
        data = json.loads(db.execute('SELECT data FROM trials').fetchone()[0])
        data['source_url'] = url
        db.execute('UPDATE trials SET data=?', (json.dumps(data),))
    body = render(app, {'view': 'cards'})
    assert bool(links(Markup(body), href=url)) is accepted
    assert url in body


def test_history_filters_company_and_exact_nct_before_limit_and_keeps_deleted_fields(synthetic_market):
    app, market = synthetic_market
    market.add_company('HK', '1234', 'Other issuer', ['Other sponsor'], 'Synthetic history fixture')
    with market.db() as db:
        for index in range(15):
            db.execute('INSERT INTO changes VALUES (?,?,?,?,?,?,?)',
                       ('other-' + str(index), 'HK:01234', 'NCT99999999', '2099-01-01', None, json.dumps({'unrelated': index}), 'other-run'))
        db.execute('INSERT INTO changes VALUES (?,?,?,?,?,?,?)',
                   ('wanted', 'US:TEST', 'NCT00000001', '2020-01-01', json.dumps({'removed_key': 'original'}), json.dumps({'added_key': 'revised'}), 'wanted-run'))
        db.execute('INSERT INTO changes VALUES (?,?,?,?,?,?,?)',
                   ('different-nct', 'US:TEST', 'NCT00000002', '2021-01-01', None, json.dumps({'other_trial': 'retained'}), 'different-run'))
    body = render(app, {'company': 'US:TEST', 'q': 'NCT00000001', 'phase': 'PHASE3'})
    history = body.split('id="market-changes"', 1)[1].split('数据更新与来源', 1)[0]
    assert '已存 1 条' in history and 'wanted-run' in history
    assert 'other-run' not in history and 'different-run' not in history
    assert 'removed_key' in history and 'added_key' in history
    assert '普通关键词、阶段和登记状态未用于历史重建' in history
    assert body.index('最近登记变化') < body.index('数据更新与来源')
    ordinary = render(app, {'company': 'US:TEST', 'q': 'Synthetic', 'status': 'TERMINATED'})
    ordinary_history = ordinary.split('id="market-changes"', 1)[1].split('数据更新与来源', 1)[0]
    assert '已存 2 条' in ordinary_history and '首次观察' in ordinary_history


def test_card_history_escape_and_render_stays_read_only(synthetic_market):
    app, market = synthetic_market
    attack = '\"><script>evil()</script><img src=x onerror=evil()>'
    with market.db() as db:
        db.execute("DELETE FROM trials WHERE nct!='NCT00000000'")
        data = json.loads(db.execute('SELECT data FROM trials').fetchone()[0])
        data.update(title=attack, interventions=[{'name': attack, 'arm_roles': [attack]}])
        db.execute('UPDATE trials SET data=?', (json.dumps(data),))
        db.execute('INSERT INTO changes VALUES (?,?,?,?,?,?,?)', ('unsafe', 'US:TEST', 'NCT00000000', '2020-01-01', 'malformed <script>', json.dumps({'value': attack}), attack))
        before = list(db.iterdump())
    body = render(app, {'company': 'US:TEST', 'view': 'cards', 'q': '<script>'})
    markup = Markup(body)
    assert '&lt;script&gt;' in body and '无法解析' in body
    assert not any(tag in ('script', 'img') for tag, _ in markup.tags)
    assert not any(key.startswith('on') for _, attributes in markup.tags for key in attributes)
    with market.db() as db:
        assert list(db.iterdump()) == before
    with pytest.raises(ValueError, match='未知的登记展示方式'):
        render(app, {'view': 'script'})
