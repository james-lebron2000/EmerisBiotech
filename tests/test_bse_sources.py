"""Synthetic connector-contract tests; these are not live exchange acceptance."""
import hashlib
import json
from urllib.parse import parse_qs

import pytest

from vbt import bse_sources as bse


def payload(page=0, total=3, size=2, codes=None, **updates):
    codes = codes if codes is not None else (['430001', '830001'] if page == 0 else ['920001'])
    data = {'content': [{'xxzqdm': code, 'xxzqjc': 'Synthetic issuer ' + code, 'xxhyzl': 'C27',
                         'xxfcbj': '2', 'xxzqjb': 'T'} for code in codes],
            'totalPages': (total + size - 1) // size, 'totalElements': total, 'size': size,
            'number': page, 'numberOfElements': len(codes), 'firstPage': page == 0,
            'lastPage': page == (total + size - 1) // size - 1}
    data.update(updates)
    return json.dumps([data], ensure_ascii=False).encode()


def fetcher(calls):
    def fetch(url, *, data):
        calls.append((url, data))
        return payload(page=int(parse_qs(data.decode())['page'][0]))
    return fetch


def test_complete_fixed_post_and_raw_captures():
    calls, archived = [], []
    result = bse.fetch_universe(fetcher(calls), on_capture=archived.append)
    assert [r['ticker'] for r in result['rows']] == ['430001', '830001', '920001']
    assert all(r['exchange'] == 'BSE' and r['market'] == 'A' for r in result['rows'])
    assert result['total'] == 3 and result['total_pages'] == 2
    assert result['remote_transactional_snapshot'] is False
    assert [parse_qs(data.decode())['page'][0] for _, data in calls] == ['0', '1', '0']
    assert all(url == bse.ENDPOINT for url, _ in calls)
    assert all(c['sha256'] == hashlib.sha256(c['raw']).hexdigest() for c in archived)
    assert all(c['method'] == 'POST' and c['status'] == 200 for c in archived)
    assert result['captures'] == archived


@pytest.mark.parametrize('page', [-1, 100, True, 0.0, '0'])
def test_request_page_bounds(page):
    with pytest.raises(ValueError):
        bse.request_body(page)


@pytest.mark.parametrize('url,data', [
    ('https://evil.test/', bse.request_body(0)),
    (bse.ENDPOINT + '?page=0', bse.request_body(0)),
    (bse.ENDPOINT.replace('https:', 'http:'), bse.request_body(0)),
    (bse.ENDPOINT, bse.request_body(0).replace(b'typejb=T', b'typejb=U')),
    (bse.ENDPOINT, bse.request_body(0).replace(b'xxzqdm=', b'xxzqdm=920001')),
    (bse.ENDPOINT, bse.request_body(0) + b'&page=1'),
    (bse.ENDPOINT, bse.request_body(0) + b'&callback=alert'),
])
def test_request_scope_rejects_mutation(url, data):
    with pytest.raises(ValueError):
        bse.validate_request(url, data)


def test_named_fields_independent_of_position_and_jsonp_safe():
    raw = payload()
    assert bse.parse(b'null(' + raw + b');')['rows'][0]['ticker'] == '430001'
    assert bse.parse(b'\xef\xbb\xbf' + raw)['rows'][1]['industry'] == 'C27'
    for bad in (b'alert(1);callback(' + raw + b')', b'callback(' + raw + b');alert(1)',
                b'foo.bar(' + raw + b')', b'<html>403 Forbidden</html>'):
        with pytest.raises(ValueError):
            bse.parse(bad)
    values = json.loads(raw)
    values[0]['content'] = [list(r.values()) for r in values[0]['content']]
    with pytest.raises(ValueError, match='named security'):
        bse.parse(json.dumps(values).encode())


@pytest.mark.parametrize('updates', [
    {'totalPages': 3}, {'totalElements': True}, {'totalPages': 101},
    {'numberOfElements': 1}, {'size': 0}, {'firstPage': False}, {'lastPage': True},
    {'content': []}, {'number': 2}, {'totalElements': 10001},
])
def test_missing_truncated_or_inconsistent_metadata_rejected(updates):
    data = json.loads(payload())
    data[0].update(updates)
    with pytest.raises(ValueError):
        bse.parse(json.dumps(data).encode())


def test_missing_explicit_total_and_named_fields_fail_closed():
    for field in ('totalElements', 'numberOfElements', 'content'):
        d = json.loads(payload())
        del d[0][field]
        with pytest.raises(ValueError):
            bse.parse(json.dumps(d).encode())
    for key, value in [('xxzqdm', 920001), ('xxzqdm', '000001'), ('xxzqjc', ''), ('xxhyzl', {}),
                       ('xxfcbj', '1'), ('xxzqjb', 'U')]:
        d = json.loads(payload())
        d[0]['content'][0][key] = value
        with pytest.raises(ValueError):
            bse.parse(json.dumps(d).encode())


def test_page_overlap_and_reordering_rejected():
    for codes in (['430001'], ['830000']):
        def fetch(url, *, data):
            page = int(parse_qs(data.decode())['page'][0])
            return payload(page=page, codes=codes) if page else payload()
        with pytest.raises(ValueError, match='overlap|ordering'):
            bse.fetch_universe(fetch)
    for codes in (['830001', '430001'], ['430001', '430001']):
        with pytest.raises(ValueError, match='unsorted'):
            bse.parse(payload(codes=codes))


def test_directory_drift_and_wrong_page_rejected():
    def drift(url, *, data):
        page = int(parse_qs(data.decode())['page'][0])
        return payload(page=page, total=4, codes=['920001', '920002']) if page else payload()
    with pytest.raises(ValueError, match='changed during pagination'):
        bse.fetch_universe(drift)
    with pytest.raises(ValueError, match='different page'):
        bse.fetch_universe(lambda url, data: payload())


def test_first_page_change_after_collection_rejected():
    calls = []
    def fetch(url, *, data):
        calls.append(data)
        page = int(parse_qs(data.decode())['page'][0])
        if len(calls) == 3:
            return payload(codes=['430002', '830001'])
        return payload(page=page)
    with pytest.raises(ValueError, match='first page changed'):
        bse.fetch_universe(fetch)


def test_http_failure_is_archived_and_raised_without_snapshot():
    captures = []
    def blocked(url, *, data):
        raise bse.BSEFetchError(403, b'<html>Forbidden</html>')
    with pytest.raises(bse.BSEFetchError, match='HTTP 403'):
        bse.fetch_universe(blocked, on_capture=captures.append)
    assert len(captures) == 1 and captures[0]['status'] == 403
    assert captures[0]['raw'] == b'<html>Forbidden</html>'


def test_cancellation_before_fetch_and_after_capture():
    with pytest.raises(InterruptedError):
        bse.fetch_universe(lambda *args, **kwargs: pytest.fail('must not fetch'), cancelled=lambda: True)
    captures = []
    with pytest.raises(InterruptedError):
        bse.fetch_universe(fetcher([]), cancelled=lambda: bool(captures), on_capture=captures.append)
    assert len(captures) == 1


def test_parser_size_and_schema_guard():
    with pytest.raises(ValueError):
        bse.parse(b' ' * (bse.MAX_BYTES + 1))
    with pytest.raises(ValueError):
        bse.parse(b'[{"content": []}, {"content": []}]')
    with pytest.raises(ValueError):
        bse.fetch_universe(lambda *args, **kwargs: 'not bytes')
