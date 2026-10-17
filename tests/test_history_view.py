"""History filters never conflate execution, integrity or scientific review."""
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit

import pytest

from vbt.history_view import record_table, render


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
