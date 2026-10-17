"""History filters never conflate execution, integrity or scientific review."""
from html.parser import HTMLParser
import copy
import json
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


def test_run_times_use_saved_start_and_end_fields_with_explicit_timezone():
    row = dict(RECORDS[0], data={'started_at': '2026-10-17T17:20:00Z',
                               'finished_at': '2026-10-18T02:25:00+09:00'})
    body = record_table([row])
    clocks = [a for t, a in Page(body).tags if t == 'time']
    assert [a['datetime'] for a in clocks] == [row['data']['started_at'], row['data']['finished_at']]
    assert '开始时间：<time' in body and '>2026-10-18 01:20 UTC+8</time>' in body
    assert '结束时间：<time' in body and '>2026-10-18 01:25 UTC+8</time>' in body
    assert '保存字段 started_at' in clocks[0]['title']
    assert '保存字段 finished_at' in clocks[1]['title']


def test_cycle_uses_created_at_and_a_portfolio_without_saved_time_stays_unknown():
    cycle = dict(RECORDS[2], data={'created_at': '2026-10-17T20:00:00+08:00'})
    assert '创建时间：<time' in record_table([cycle])
    portfolio = dict(RECORDS[3], id='2026-10-17T120000+0000-fake-id', data={'items': []})
    body = record_table([portfolio])
    assert '记录时间未知' in body
    assert not any(t == 'time' for t, _ in Page(body).tags)


@pytest.mark.parametrize('value', [None, '', '2026-10-17', '2026-10-17T12:00:00',
    '2026-02-30T12:00:00+08:00', '9999-12-31T23:59:59-12:00',
    '20261017T12:00:00Z', '2026-10-17"12:00:00+08:00',
    '<img src=x onerror=evil()>', '2026-10-17T12:00:00Z\n', 1792224000, True])
def test_missing_invalid_naive_and_overflowing_timestamps_are_unknown(value):
    body = record_table([dict(RECORDS[2], data={'created_at': value})])
    assert '记录时间未知' in body
    assert not any(t in ('time', 'img', 'script') for t, _ in Page(body).tags)


@pytest.mark.parametrize('data', [None, [], 'untrusted record', {}])
def test_record_time_never_infers_from_number_path_or_filesystem(data):
    row = dict(RECORDS[0], id='2026-10-17T190000+0000-12345678', data=data,
               started_at='2026-10-17T01:00:00Z', path='/unrelated/path')
    body = record_table([row])
    assert '记录时间未知' in body
    assert not any(t == 'time' for t, _ in Page(body).tags)


def test_end_time_does_not_invent_a_start_or_a_successful_review():
    row = dict(RECORDS[1], data={'finished_at': '2026-10-17T03:04:00Z'})
    body = record_table([row])
    assert '开始时间未知' in body and '结束时间：<time' in body
    assert '<span class="badge failed">执行失败</span>' in body
    assert '<span class="badge pending">待科学审查</span>' in body
    assert '执行成功' not in body and '创建时间' not in body


def test_conflicting_start_and_end_times_are_flagged_without_changing_execution():
    row = dict(RECORDS[0], data={'started_at': '2026-10-17T02:00:00Z',
                               'finished_at': '2026-10-17T01:00:00Z'})
    body = record_table([row])
    assert '保存的开始与结束时间冲突' in body
    assert not any(t == 'time' for t, _ in Page(body).tags)
    assert '<span class="badge succeeded">执行成功</span>' in body
    assert '<span class="badge revise">需修订</span>' in body


def test_time_display_preserves_synthetic_state_boundaries_filters_and_return_context():
    row = dict(RECORDS[0], title='SYNTHETIC ENGINEERING TEST: OSMR',
               data={'started_at': '2026-10-17T01:00:00Z', 'record_integrity': 'failed',
                     'scientific_review': 'revise', 'human_signoff': False})
    before = copy.deepcopy(row)
    body = render([row], {'scope': 'synthetic', 'review': 'revise', 'status': 'succeeded'})
    assert row == before and links(body) == ['one']
    assert '合成工程测试</span>' in body and '执行成功</span>' in body and '需修订</span>' in body
    assert '完整性通过' not in body and '人工签署通过' not in body
    target = next(a['href'] for t, a in Page(body).tags if t == 'a' and a.get('class') == 'record-title')
    back = parse_qs(urlsplit(parse_qs(urlsplit(target).query)['return'][0]).query)
    assert back['scope'] == ['synthetic'] and back['review'] == ['revise']
    assert {a['data-label'] for t, a in Page(body).tags if t == 'td'} == {
        '研究问题与版本', '记录类型', '执行 / 记录状态', '科学审查'}


def test_actual_workbench_record_shape_renders_explicit_run_and_cycle_times(tmp_path):
    from vbt.workbench import Workbench
    app = Workbench(tmp_path / 'workspace', tmp_path / 'state')
    for folder, filename, payload in [
        ('runs/run-fixture', 'status.json', {'execution': 'succeeded', 'scientific_review': 'revise',
                                          'started_at': '2026-10-17T01:00:00Z'}),
        ('cycles/cycle-fixture', 'cycle.json', {'created_at': '2026-10-17T02:00:00Z',
                                              'hypothesis': 'Synthetic fixture', 'complete_research_cycle': True}),
        ('portfolio/snapshots/portfolio-fixture', 'evaluation.json', {'items': []}),
    ]:
        path = app.workspace / 'artifacts' / folder; path.mkdir(parents=True)
        (path / filename).write_text(json.dumps(payload))
    rows = app.records()
    body = record_table(rows)
    clocks = [a['datetime'] for t, a in Page(body).tags if t == 'time']
    assert set(clocks) == {'2026-10-17T01:00:00Z', '2026-10-17T02:00:00Z'}
    assert body.count('记录时间未知') == 1
