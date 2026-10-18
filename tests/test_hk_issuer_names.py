"""Synthetic source contracts; passing these is not real HK pipeline coverage."""
import io
import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from xml.sax.saxutils import escape
import zipfile

import pytest

from vbt import hk_issuer_names as names
from vbt.market import Market
from vbt.market_coverage import refresh_candidates


def workbook(label='Updated as at 20/10/2099'):
    rows = [['List of Securities'], [label],
            ['Stock Code', 'Name of Securities', 'Category'],
            ['02410', 'SYNTHETIC PHARMA-B', 'Equity'],
            ['00512', 'SYNTHETIC GROUP', 'Equity']]
    xml = '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
    for number, row in enumerate(rows, 1):
        xml += f'<row r="{number}">' + ''.join(
            f'<c r="{chr(65+i)}{number}" t="inlineStr"><is><t>{escape(value)}</t></is></c>'
            for i, value in enumerate(row)) + '</row>'
    xml += '</sheetData></worksheet>'
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('xl/worksheets/sheet1.xml', xml)
    return buffer.getvalue()


@pytest.fixture
def market(tmp_path):
    result = Market(tmp_path / 'workspace', tmp_path / 'state')
    rid = result.refresh_universe('HK', fetch=lambda url: workbook())
    assert next(r for r in result.rows('runs') if r['id'] == rid)['status'] == 'succeeded'
    return result


def test_future_content_date_is_warning_not_capture_date_or_zero(market):
    result = names.directory_audit(market)
    assert result['schema_version'] == 1 and result['source'] == 'HK'
    assert result['status'] == 'verified_bytes_date_anomaly'
    assert result['date_status'] == 'future_dated'
    assert result['content_as_of'] == '2099-10-20'
    assert result['date_locator'] == 'sheet1!A2'
    assert not result['retrieved_at'].startswith('2099')
    assert result['directory_count'] == 2 and result['raw_bytes'] > 0
    assert result['issuer_legal_names_present'] is False


@pytest.mark.parametrize('label,expected', [
    ('Updated as at 17/10/2020', 'known'), ('Update unavailable', 'unknown'),
    ('Updated as at 99/99/2026', 'unknown')])
def test_content_date_comes_only_from_explicit_source_field(market, label, expected):
    market.refresh_universe('HK', fetch=lambda url: workbook(label))
    result = names.directory_audit(market)
    assert result['date_status'] == expected
    assert result['status'] == ('verified' if expected == 'known' else 'verified_bytes_date_anomaly')
    assert (result['content_as_of'] is None) == (expected == 'unknown')


@pytest.mark.parametrize('kind', ['raw', 'metadata', 'count', 'hash', 'listing', 'run', 'url', 'missing'])
def test_current_directory_is_replayed_not_trusted_from_status(market, kind):
    with market.db() as db:
        rid = db.execute("SELECT run_id FROM universe_snapshots WHERE source='HK'").fetchone()[0]
    folder = market.root / 'captures' / rid
    if kind == 'raw':
        (folder / '0.bin').write_bytes((folder / '0.bin').read_bytes()[:-1])
    elif kind in ('metadata', 'url'):
        path = folder / '0.json'; meta = json.loads(path.read_text())
        meta['bytes' if kind == 'metadata' else 'url'] = 42 if kind == 'metadata' else 'https://evil.test/source'
        path.write_text(json.dumps(meta))
    elif kind == 'missing':
        (folder / '0.bin').unlink()
    else:
        with market.db() as db:
            db.execute({'count': "UPDATE universe_snapshots SET count=count+1",
                'hash': "UPDATE universe_snapshots SET rows_sha256='bad'",
                'listing': "UPDATE listings SET name='Different entity'",
                'run': "UPDATE runs SET status='running' WHERE id=?"}[kind], (rid,) if kind == 'run' else ())
    result = names.directory_audit(market)
    assert result['status'] == 'blocked' and result['error']
    assert result['directory_count'] is None and result['date_status'] == 'unknown'


def test_ro_audit_uses_only_select_and_keeps_caller_transaction(market):
    db = sqlite3.connect(market.path.resolve().as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    try:
        db.execute('BEGIN'); before = list(db.iterdump()); statements = []
        db.set_trace_callback(statements.append)
        assert names.directory_audit(market, db=db)['directory_count'] == 2
        assert db.in_transaction
        assert all(sql.lstrip().upper().startswith('SELECT') for sql in statements)
        db.set_trace_callback(None)
        assert list(db.iterdump()) == before
    finally:
        db.close()


def test_missing_database_stays_missing_and_unknown(tmp_path):
    path = tmp_path / 'not-created.sqlite3'
    result = names.directory_audit(SimpleNamespace(path=path, root=tmp_path / 'market'))
    assert result['status'] == 'blocked' and result['directory_count'] is None
    assert not path.exists()


def holdings(rows=None, date='2026/10/16'):
    rows = rows or [('2410', 'Synthetic Medicines, Inc', '100', '1.50%'),
                    ('512', 'Synthetic Group Limited', '1,000', '')]
    html = f'<html><body><span>Shareholding Date: {date}</span><table id="mutualmarket-result"><thead><tr>'
    headers = ['Stock Code', 'Name', 'Shareholding in CCASS', '% of the total number of Issued Shares/Units']
    html += ''.join('<th>' + h + '</th>' for h in headers) + '</tr></thead><tbody>'
    classes = ['col-stock-code', 'col-stock-name', 'col-shareholding', 'col-shareholding-percent']
    for row in rows:
        html += '<tr>' + ''.join(f'<td class="{c}"><div class="mobile-list-heading">Label:</div>'
            f'<div class="mobile-list-body">{escape(v)}</div></td>' for c,v in zip(classes,row)) + '</tr>'
    return (html + '</tbody></table><script>ignored()</script></body></html>').encode()


def test_named_stock_fields_parse_without_translating_and_partial_scope():
    result = names.parse_southbound(holdings())
    assert result['content_as_of'] == '2026-10-16' and result['row_count'] == 2
    assert result['rows']['02410']['english_name'] == 'Synthetic Medicines, Inc'
    assert result['rows']['00512']['source_locator'].endswith('tr[2] td.col-stock-name')
    assert result['scope'] == 'observed_ccass_southbound_holdings_subset_names_only'
    assert result['source_url'] == names.SOUTHBOUND_URL


@pytest.mark.parametrize('raw', [b'', b'<html>CAPTCHA</html>', holdings()[:-30],
    holdings().replace(b'Stock Code</th>', b'Identifier</th>'),
    holdings().replace(b'col-stock-name', b'col-alias'),
    holdings([('2410', 'Synthetic Medicines, Inc', '100', '1%'),
              ('02410', 'Different issuer', '100', '1%')]),
    holdings(date='2026/99/99'), holdings().replace(b'2410', b'bad'),
    holdings().replace(b'</tbody>', b''), holdings() + holdings()])
def test_changed_truncated_or_ambiguous_source_is_not_a_receipt(raw):
    with pytest.raises((ValueError, UnicodeError)):
        names.parse_southbound(raw)


def test_trading_suffix_is_preserved_but_cannot_become_query_seed():
    value = 'Synthetic Limited -HKD TRADED SHARES'
    row = names.parse_southbound(holdings([('2410', value, '100', '1%')]))['rows']['02410']
    assert row['english_name'] == value and row['query_seed_eligible'] is False


def seed_names(market, *, first='Synthetic Medicines, Inc'):
    refresh_candidates(market, [dict(market='HK', ticker=r['ticker'], name=r['name'],
        classification='candidate' if r['ticker'] == '02410' else 'uncertain',
        source='HK', reason='Synthetic engineering fixture only', evidence=[{'kind': 'synthetic'}])
        for r in market.rows('listings')])
    raw = holdings([('2410', first, '100', '1.5%'),
                    ('512', 'Synthetic Group Limited', '1,000', '')],
                   date=datetime.now(timezone.utc).strftime('%Y/%m/%d'))
    metadata = {'url': names.SOUTHBOUND_URL, 'method': 'GET', 'status': 200,
        'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(),
        'retrieved_at': datetime.now(timezone.utc).isoformat()}
    return raw, metadata


def candidate_rows(market):
    with market.db() as db:
        return [dict(r) for r in db.execute('SELECT * FROM issuer_candidates ORDER BY id')]


def test_sealed_name_receipts_are_idempotent_and_never_change_classification_or_mapping(market):
    raw, metadata = seed_names(market)
    before = {key: market.rows(key) for key in ('companies', 'trials', 'listings')}
    candidates = candidate_rows(market)
    sealed = names.seal_southbound(market, raw, metadata)
    assert sealed['status'] == 'sealed_query_seeds_only' and sealed['added'] == 2
    again = names.seal_southbound(market, raw, metadata)
    assert again['capture_id'] == sealed['capture_id'] and again['added'] == 0
    assert names.query_names(market, candidates) == {
        'HK:00512': 'Synthetic Group Limited', 'HK:02410': 'Synthetic Medicines, Inc'}
    proof = names.query_evidence(market, next(r for r in candidates if r['ticker'] == '02410'))
    assert proof['source'] == 'HKEX_SOUTHBOUND' and proof['directory_date_status'] == 'future_dated'
    for key in ('human_sign_off', 'registry_alias_proven', 'drug_ownership_verified',
                'drug_developer_status_verified', 'full_pipeline_verified'):
        assert proof[key] is False
    assert len(proof['receipt_id']) == 32 and len(proof['raw_sha256']) == 64
    assert {key: market.rows(key) for key in before} == before
    assert candidate_rows(market) == candidates
    with market.db() as db:
        assert db.execute('SELECT count(*) FROM hk_issuer_name_captures').fetchone()[0] == 1


@pytest.mark.parametrize('field,value', [('url', 'https://evil.test/page'), ('method', 'POST'),
    ('status', 302), ('status', True), ('bytes', True), ('bytes', 1), ('sha256', '0'*64),
    ('retrieved_at', '2026-10-18T00:00:00'),
    ('retrieved_at', (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()),
    ('retrieved_at', (datetime.now(timezone.utc) - timedelta(days=9)).isoformat())])
def test_invalid_or_expired_capture_never_publishes_receipt(market, field, value):
    raw, metadata = seed_names(market); metadata[field] = value
    with pytest.raises(ValueError):
        names.seal_southbound(market, raw, metadata)
    assert names.query_names(market, candidate_rows(market)) == {}


@pytest.mark.parametrize('change', ['raw', 'metadata', 'candidate_hash', 'candidate_name',
                                   'rejected', 'listing', 'symlink'])
def test_replay_rejects_changed_current_inputs_and_source_bytes(market, change, tmp_path):
    raw, metadata = seed_names(market); result = names.seal_southbound(market, raw, metadata)
    candidates = candidate_rows(market)
    folder = market.root / 'hk_issuer_names/captures' / result['capture_id']
    if change == 'raw':
        (folder / 'source.html').write_bytes(raw[:-1])
    elif change == 'metadata':
        (folder / 'metadata.json').write_text('{}')
    elif change == 'symlink':
        copy = tmp_path / 'copied.html'; copy.write_bytes(raw)
        (folder / 'source.html').unlink(); (folder / 'source.html').symlink_to(copy)
    else:
        with market.db() as db:
            db.execute({'candidate_hash': "UPDATE issuer_candidates SET content_hash='changed'",
                'candidate_name': "UPDATE issuer_candidates SET name='Different issuer'",
                'rejected': "UPDATE issuer_candidates SET review_status='rejected'",
                'listing': "UPDATE listings SET name='Changed listing'"}[change])
    assert names.query_names(market, candidates) == {}


def test_conflicting_exact_official_names_remain_unknown_instead_of_last_writer_wins(market):
    raw, metadata = seed_names(market); names.seal_southbound(market, raw, metadata)
    raw2, meta2 = seed_names(market, first='Different Listed Legal Name Limited')
    names.seal_southbound(market, raw2, meta2)
    result = names.query_names(market, candidate_rows(market))
    assert 'HK:02410' not in result and result['HK:00512'] == 'Synthetic Group Limited'


@pytest.mark.parametrize('table', ['hk_issuer_name_captures', 'hk_issuer_name_receipts'])
def test_immutable_ledgers_block_replace_with_default_recursive_triggers(market, table):
    raw, metadata = seed_names(market); names.seal_southbound(market, raw, metadata)
    with market.db() as db:
        assert db.execute('PRAGMA recursive_triggers').fetchone()[0] == 0
        with pytest.raises(sqlite3.IntegrityError, match='append-only'):
            db.execute(f'INSERT OR REPLACE INTO {table} SELECT * FROM {table} LIMIT 1')
        with pytest.raises(sqlite3.IntegrityError, match='append-only'):
            db.execute(f'UPDATE {table} SET id=?', ('f'*32,))
        with pytest.raises(sqlite3.IntegrityError, match='append-only'):
            db.execute(f'DELETE FROM {table}')


def test_batch_read_replays_each_capture_once_and_is_pure_ro(market, monkeypatch):
    raw, metadata = seed_names(market); names.seal_southbound(market, raw, metadata)
    candidates = candidate_rows(market)
    original = names._load_capture; calls = []
    def load(*args):
        calls.append(args[1]['id']); return original(*args)
    monkeypatch.setattr(names, '_load_capture', load)
    db = sqlite3.connect(market.path.resolve().as_uri() + '?mode=ro', uri=True); db.row_factory = sqlite3.Row
    try:
        db.execute('BEGIN'); before = list(db.iterdump()); sql = []; db.set_trace_callback(sql.append)
        assert len(names.query_evidences(market, candidates, db=db)) == 2
        assert len(calls) == 1 and db.in_transaction
        assert all(statement.upper().lstrip().startswith('SELECT') for statement in sql)
        db.set_trace_callback(None); assert list(db.iterdump()) == before
    finally:
        db.close()


def test_invalid_current_candidate_hash_cannot_receive_new_receipt(market):
    raw, metadata = seed_names(market)
    with market.db() as db:
        db.execute("UPDATE issuer_candidates SET content_hash='not-a-SHA' WHERE id='HK:02410'")
    result = names.seal_southbound(market, raw, metadata)
    assert result['added'] == 1
    assert 'HK:02410' not in names.query_names(market, candidate_rows(market))


def test_future_content_date_and_oversized_source_fail_before_publication(market):
    raw, metadata = seed_names(market)
    future = (datetime.now(timezone.utc) + timedelta(days=1)).strftime('%Y/%m/%d')
    raw2 = holdings(date=future)
    metadata.update(bytes=len(raw2), sha256=hashlib.sha256(raw2).hexdigest())
    with pytest.raises(ValueError, match='content date'):
        names.seal_southbound(market, raw2, metadata)
    with pytest.raises(ValueError, match='4 MB'):
        names.seal_southbound(market, b'x' * (names.MAX_HTML_BYTES + 1), metadata)
