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
    assert 'All research records' in body and 'unrecorded states remain unknown' in body


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
    assert 'No matching research records' in body
    assert '0 / 0 records' in body
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
    assert 'Includes 1 explicitly marked synthetic engineering tests' in body
    assert '<span class="badge pending">Synthetic engineering test</span>' in body
    assert 'not real clinical research or forecast performance' in body
    assert links(body) == ['one', 'two']


def test_sample_scope_does_not_call_unmarked_records_validated():
    rows = [dict(RECORDS[0], title='SYNTHETIC ENGINEERING TEST: baseline OSM'), RECORDS[1]]
    assert links(render(rows, {'scope': 'synthetic'})) == ['one']
    body = render(rows, {'scope': 'unmarked'})
    assert links(body) == ['two']
    assert 'does not establish clinical validation or forecasting accuracy' in body
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


def test_removing_one_filter_keeps_other_filters_and_resets_page():
    query = {'q': 'OSMR', 'kind': 'run', 'status': 'succeeded', 'review': 'revise',
             'scope': 'unmarked', 'offset': '20'}
    rows = [dict(RECORDS[0], id=str(i)) for i in range(43)]
    page = Page(render(rows, query))
    chips = [a for t, a in page.tags if t == 'a' and a.get('class') == 'asset-clear-chip']
    assert len(chips) == 5
    target = next(a['href'] for a in chips if a['aria-label'].startswith('Remove filter Scientific review: '))
    parsed = urlsplit(target)
    assert parsed.path == '/records' and parsed.fragment == 'history-results'
    remaining = {key: values[0] for key, values in parse_qs(parsed.query).items()}
    assert remaining == {'q': 'OSMR', 'kind': 'run', 'status': 'succeeded', 'scope': 'unmarked'}
    assert links(render(rows, remaining)) == [str(i) for i in range(20)]
    assert query['offset'] == '20' and query['review'] == 'revise'


def test_history_top_and_bottom_pagination_follow_same_filtered_records():
    rows = [dict(RECORDS[0], id=str(i)) for i in range(43)]
    page = Page(render(rows, {'q': 'OSMR', 'review': 'revise', 'offset': '20'}))
    navs = [a for t, a in page.tags if t == 'nav' and 'history-pagination' in a.get('class', '')]
    assert [a['aria-label'] for a in navs] == ['Research history pages · Top', 'Research history pages · Bottom']
    targets = [a['href'] for t, a in page.tags if t == 'a' and a.get('rel') == 'next']
    assert len(targets) == 2 and targets[0] == targets[1]
    parsed = urlsplit(targets[0])
    assert parsed.fragment == 'history-results'
    assert links(render(rows, {k: v[0] for k, v in parse_qs(parsed.query).items()})) == ['40', '41', '42']
    assert 'Page 2 / 3' in render(rows, {'offset': '20'})
    assert any(t == 'section' and a.get('id') == 'history-results' and a.get('tabindex') == '-1'
               for t, a in page.tags)


def test_empty_history_can_remove_query_without_inventing_pages():
    body = render(RECORDS, {'q': '<script>'})
    page = Page(body)
    chip = next(a for t, a in page.tags if t == 'a' and a.get('class') == 'asset-clear-chip')
    assert chip['href'] == '/records#history-results'
    assert '<script>' not in body and '&lt;script&gt;' in body
    assert 'history-pagination' not in body


@pytest.mark.parametrize('value', [None, 'https://evil.test/records', '//evil.test/records',
                                  '/notes', '/records#bad', '/records?bad=1', '/records?q=1&q=2',
                                  '/records?return=//evil.test', '/records\n', 'x' * 2501])
def test_back_link_rejects_external_or_unexpected_context(value):
    assert safe_return(value) == '/records'


def test_mobile_record_labels_do_not_change_state_semantics():
    page = Page(record_table(RECORDS))
    assert any(t == 'table' and a.get('role') == 'table' for t, a in page.tags)
    assert {a['data-label'] for t, a in page.tags if t == 'td'} == {
        'Research question and version', 'Record type', 'Execution / record state', 'Scientific review'}


def test_run_times_use_saved_start_and_end_fields_with_explicit_timezone():
    row = dict(RECORDS[0], data={'started_at': '2026-10-17T17:20:00Z',
                               'finished_at': '2026-10-18T02:25:00+09:00'})
    body = record_table([row])
    clocks = [a for t, a in Page(body).tags if t == 'time']
    assert [a['datetime'] for a in clocks] == [row['data']['started_at'], row['data']['finished_at']]
    assert 'Started: <time' in body and '>2026-10-18 01:20 UTC+8</time>' in body
    assert 'Finished: <time' in body and '>2026-10-18 01:25 UTC+8</time>' in body
    assert 'Saved field  started_at' in clocks[0]['title']
    assert 'Saved field  finished_at' in clocks[1]['title']


def test_cycle_uses_created_at_and_a_portfolio_without_saved_time_stays_unknown():
    cycle = dict(RECORDS[2], data={'created_at': '2026-10-17T20:00:00+08:00'})
    assert 'Created: <time' in record_table([cycle])
    portfolio = dict(RECORDS[3], id='2026-10-17T120000+0000-fake-id', data={'items': []})
    body = record_table([portfolio])
    assert 'Record time unknown' in body
    assert not any(t == 'time' for t, _ in Page(body).tags)


@pytest.mark.parametrize('value', [None, '', '2026-10-17', '2026-10-17T12:00:00',
    '2026-02-30T12:00:00+08:00', '9999-12-31T23:59:59-12:00',
    '20261017T12:00:00Z', '2026-10-17"12:00:00+08:00',
    '<img src=x onerror=evil()>', '2026-10-17T12:00:00Z\n', 1792224000, True])
def test_missing_invalid_naive_and_overflowing_timestamps_are_unknown(value):
    body = record_table([dict(RECORDS[2], data={'created_at': value})])
    assert 'Record time unknown' in body
    assert not any(t in ('time', 'img', 'script') for t, _ in Page(body).tags)


@pytest.mark.parametrize('data', [None, [], 'untrusted record', {}])
def test_record_time_never_infers_from_number_path_or_filesystem(data):
    row = dict(RECORDS[0], id='2026-10-17T190000+0000-12345678', data=data,
               started_at='2026-10-17T01:00:00Z', path='/unrelated/path')
    body = record_table([row])
    assert 'Record time unknown' in body
    assert not any(t == 'time' for t, _ in Page(body).tags)


def test_end_time_does_not_invent_a_start_or_a_successful_review():
    row = dict(RECORDS[1], data={'finished_at': '2026-10-17T03:04:00Z'})
    body = record_table([row])
    assert 'Started unknown' in body and 'Finished: <time' in body
    assert '<span class="badge failed">Execution failed</span>' in body
    assert '<span class="badge pending">Scientific review pending</span>' in body
    assert 'Execution succeeded' not in body and 'Created' not in body


def test_conflicting_start_and_end_times_are_flagged_without_changing_execution():
    row = dict(RECORDS[0], data={'started_at': '2026-10-17T02:00:00Z',
                               'finished_at': '2026-10-17T01:00:00Z'})
    body = record_table([row])
    assert 'Saved start/end timestamps conflict' in body
    assert not any(t == 'time' for t, _ in Page(body).tags)
    assert '<span class="badge succeeded">Execution succeeded</span>' in body
    assert '<span class="badge revise">Revision required</span>' in body


def test_time_display_preserves_synthetic_state_boundaries_filters_and_return_context():
    row = dict(RECORDS[0], title='SYNTHETIC ENGINEERING TEST: OSMR',
               data={'started_at': '2026-10-17T01:00:00Z', 'record_integrity': 'failed',
                     'scientific_review': 'revise', 'human_signoff': False})
    before = copy.deepcopy(row)
    body = render([row], {'scope': 'synthetic', 'review': 'revise', 'status': 'succeeded'})
    assert row == before and links(body) == ['one']
    assert 'Synthetic engineering test</span>' in body and 'Execution succeeded</span>' in body and 'Revision required</span>' in body
    assert '完整性Passed' not in body and 'Human sign-offPassed' not in body
    target = next(a['href'] for t, a in Page(body).tags if t == 'a' and a.get('class') == 'record-title')
    back = parse_qs(urlsplit(parse_qs(urlsplit(target).query)['return'][0]).query)
    assert back['scope'] == ['synthetic'] and back['review'] == ['revise']
    assert {a['data-label'] for t, a in Page(body).tags if t == 'td'} == {
        'Research question and version', 'Record type', 'Execution / record state', 'Scientific review'}


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
    assert body.count('Record time unknown') == 1
