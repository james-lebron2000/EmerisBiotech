"""History filters never conflate execution, integrity or scientific review."""
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit

import pytest

from vbt.history_view import PAGE_SIZE, record_table, render, safe_return


RECORDS = [
    dict(id='one', kind='run', title='OSMR analysis', status='succeeded', review='revise'),
    dict(id='two', kind='run', title='OSM validation', status='failed', review='pending'),
    dict(id='three', kind='cycle', title='OSMR cycle', status='completed', review='insufficient_evidence'),
    dict(id='four', kind='portfolio', title='Frozen plan', status='snapshot', review='unknown'),
]


class Page(HTMLParser):
    def __init__(self, body):
        super().__init__(); self.tags = []; self.feed(body)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def links(body):
    return [parse_qs(urlsplit(a['href']).query)['id'][0] for t, a in Page(body).tags
            if t == 'a' and a.get('class') == 'record-title']


@pytest.mark.parametrize('query, expected', [
    ({}, ['one', 'two', 'three', 'four']),
    ({'q': ' osmr ', 'kind': 'run'}, ['one']),
    ({'status': 'succeeded', 'review': 'pending'}, []),
    ({'status': 'succeeded', 'review': 'revise'}, ['one']),
    ({'review': 'insufficient_evidence'}, ['three']),
    ({'q': 'TWO'}, ['two']),
])
def test_filters_are_composable_and_independent(query, expected):
    before = [dict(r) for r in RECORDS]
    body = render(RECORDS, query)
    assert links(body) == expected
    assert RECORDS == before
    assert '全部研究记录' in body and '没有记录的状态保留未知' in body


def test_filter_labels_and_retained_values():
    parsed = Page(render(RECORDS, {'q': 'OSMR', 'kind': 'run', 'status': 'succeeded', 'review': 'revise'}))
    labels = {a['for'] for t, a in parsed.tags if t == 'label'}
    fields = {a['id'] for t, a in parsed.tags if t in ('input', 'select')}
    assert fields == labels
    assert {a['value'] for t, a in parsed.tags if t == 'option' and 'selected' in a} == {'run', 'succeeded', 'revise'}
    assert any(t == 'input' and a.get('value') == 'OSMR' for t, a in parsed.tags)


def test_imported_text_and_record_ids_cannot_create_markup():
    malicious = '<img src=x onerror=alert(1)>'
    rows = [dict(id='a&kind=cycle"', kind='run', title=malicious, status=malicious, review=malicious)]
    body = render(rows, {'q': '<img'})
    parsed = Page(body)
    assert not any(t in ('img', 'script') for t, _ in parsed.tags)
    assert '&lt;img' in body
    assert links(body) == ['a&kind=cycle"']
    assert not any('onerror' in a for _, a in parsed.tags)
    assert not any(malicious in a.get('class', '') for _, a in parsed.tags)


def test_keyboard_scroll_and_column_scopes():
    parsed = Page(record_table(RECORDS))
    assert any(t == 'div' and a.get('role') == 'region' and a.get('tabindex') == '0' for t, a in parsed.tags)
    assert sum(t == 'th' and a.get('scope') == 'col' for t, a in parsed.tags) == 4
    assert any(t == 'thead' for t, _ in parsed.tags)


def test_empty_history_is_explicit_and_not_a_completed_research():
    body = render([], {})
    assert '没有匹配的研究记录' in body
    assert '0 / 0 条' in body
    assert 'record-title' not in body


def test_legacy_pass_values_have_one_filter_and_preserve_raw_records():
    rows = [dict(RECORDS[0], review='pass'), dict(RECORDS[1], review='passed')]
    body = render(rows, {'review': 'passed'})
    assert links(body) == ['one', 'two']
    options = [a for t, a in Page(body).tags if t == 'option']
    assert sum(a.get('value') == 'passed' for a in options) == 1
    assert not any(a.get('value') == 'pass' for a in options)
    assert links(render(rows, {'review': 'pass'})) == ['one', 'two']
    assert [r['review'] for r in rows] == ['pass', 'passed']


def test_explicit_synthetic_marker_is_visible_and_not_a_real_outcome():
    rows = [dict(RECORDS[0], title='SYNTHETIC ENGINEERING TEST: baseline OSM'), RECORDS[1]]
    body = render(rows, {})
    assert '包含 1 条明确标记的合成工程测试' in body
    assert '<span class="badge pending">合成工程测试</span>' in body
    assert '不能计为真实临床研究或预测成绩' in body
    assert links(body) == ['one', 'two']


def test_sample_scope_does_not_call_unmarked_records_validated():
    rows = [dict(RECORDS[0], title='SYNTHETIC ENGINEERING TEST: baseline OSM'), RECORDS[1]]
    assert links(render(rows, {'scope': 'synthetic'})) == ['one']
    body = render(rows, {'scope': 'unmarked'})
    assert links(body) == ['two']
    assert '不代表真实临床验证或预测准确性已获确认' in body
    with pytest.raises(ValueError):
        render(rows, {'scope': '<script>'})


@pytest.mark.parametrize('offset,first,count', [('1', 0, 20), ('19', 0, 20), ('21', 20, 20),
                                            ('99999', 40, 3), ('-1', 0, 20), ('bad', 0, 20)])
def test_history_paging_aligns_clamps_and_keeps_filters(offset, first, count):
    rows = [dict(RECORDS[0], id=str(i)) for i in range(2 * PAGE_SIZE + 3)]
    body = render(rows, {'q': 'OSMR', 'status': 'succeeded', 'scope': 'unmarked', 'offset': offset})
    assert links(body) == [str(i) for i in range(first, first + count)]
    for tag, attrs in Page(body).tags:
        if tag == 'a' and attrs.get('rel') in ('prev', 'next'):
            query = parse_qs(urlsplit(attrs['href']).query)
            assert query['q'] == ['OSMR'] and query['status'] == ['succeeded']
            assert query['scope'] == ['unmarked'] and int(query['offset'][0]) % PAGE_SIZE == 0


def test_record_link_remembers_exact_history_context():
    body = render(RECORDS, {'q': 'OSMR', 'kind': 'run', 'status': 'succeeded', 'review': 'revise', 'scope': 'unmarked'})
    target = next(a['href'] for t, a in Page(body).tags if t == 'a' and a.get('class') == 'record-title')
    back = parse_qs(urlsplit(target).query)['return'][0]
    assert safe_return(back) == back
    assert parse_qs(urlsplit(back).query) == dict(q=['OSMR'], kind=['run'], status=['succeeded'],
                                               review=['revise'], scope=['unmarked'], offset=['0'])


@pytest.mark.parametrize('value', [None, 'https://evil.test/records', '//evil.test/records',
                                  '/notes', '/records#bad', '/records?bad=1', '/records?q=1&q=2',
                                  '/records?return=//evil.test', '/records\n', 'x' * 2501])
def test_back_link_rejects_external_or_unexpected_context(value):
    assert safe_return(value) == '/records'


def test_mobile_record_labels_do_not_change_state_semantics():
    page = Page(record_table(RECORDS))
    assert any(t == 'table' and a.get('role') == 'table' for t, a in page.tags)
    assert {a['data-label'] for t, a in page.tags if t == 'td'} == {
        '研究问题与版本', '记录类型', '执行 / 记录状态', '科学审查'}
