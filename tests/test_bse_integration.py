"""Synthetic BSE publication tests; no network or production state is used."""
import hashlib
import json
from types import SimpleNamespace
from urllib.parse import parse_qs

import pytest

from vbt import bse_sources, market_jobs, market_sources
from vbt.issuer_discovery import discover
from vbt.market import Market, universe_digest
from vbt.market_coverage import coverage_summary, refresh_candidates
from vbt.market_progress_view import render as progress
from vbt.market_view import render as market_page


@pytest.fixture
def market(tmp_path):
    return Market(tmp_path / 'workspace', tmp_path / 'state')


def response(page, codes=None):
    codes = codes if codes is not None else (['830001', '920001'] if page == 0 else ['920002'])
    return json.dumps([{
        'content': [{'xxzqdm': code, 'xxzqjc': 'Fixture ' + code, 'xxhyzl': '医药制造业',
                     'xxfcbj': '2', 'xxzqjb': 'T'} for code in codes],
        'firstPage': page == 0, 'lastPage': page == 1, 'number': page,
        'numberOfElements': len(codes), 'size': 2, 'totalElements': 3, 'totalPages': 2,
    }], ensure_ascii=False).encode()


def fetched(url, *, data):
    assert url == bse_sources.ENDPOINT
    return response(int(parse_qs(data.decode())['page'][0]))


def summary(market):
    return coverage_summary(market)['markets']['A']


def test_bse_source_queued_and_scheduled_from_fixed_targets(market, monkeypatch):
    assert market_sources.URLS['BSE'] == bse_sources.ENDPOINT
    jid = market_jobs.enqueue(market, 'universe', 'BSE')
    assert market_jobs.jobs(market)[0]['id'] == jid
    calls = []
    monkeypatch.setattr(market, 'enqueue', lambda kind, reference: calls.append((kind, reference)))
    market.schedule(True)
    market.tick()
    assert ('universe', 'BSE') in calls


def test_atomic_publication_and_all_post_capture_metadata(market):
    rid = market.refresh_universe('BSE', bse_fetch=fetched)
    rows = market.rows('listings')
    assert len(rows) == 3 and all(r['source'] == 'BSE' and r['market'] == 'A' for r in rows)
    run = next(r for r in market.rows('runs') if r['id'] == rid)
    assert run['status'] == 'succeeded' and run['count'] == 3
    with market.db() as db:
        snap = dict(db.execute("SELECT * FROM universe_snapshots WHERE source='BSE'").fetchone())
    assert snap['run_id'] == rid and snap['count'] == 3 and snap['rows_sha256'] == universe_digest(rows)
    captures = sorted((market.root / 'captures' / rid).glob('*.json'))
    assert len(captures) == 3
    assert [json.loads(p.read_text())['page'] for p in captures] == [0, 1, 0]
    for p in captures:
        meta = json.loads(p.read_text())
        assert meta['method'] == 'POST' and meta['status'] == 200
        assert parse_qs(meta['request_body'])['typejb'] == ['T']
        assert hashlib.sha256(p.with_suffix('.bin').read_bytes()).hexdigest() == meta['sha256']
    assert market.rows('companies') == [] and market.rows('trials') == []


def test_partial_pages_never_replace_previous_directory(market):
    previous = market.refresh_universe('BSE', bse_fetch=fetched)
    before = market.rows('listings')
    def truncated(url, *, data):
        page = int(parse_qs(data.decode())['page'][0])
        raw = response(page)
        if page:
            parsed = json.loads(raw)
            parsed[0]['content'] = []
            return json.dumps(parsed).encode()
        return raw
    rid = market.refresh_universe('BSE', bse_fetch=truncated)
    assert market.rows('listings') == before
    assert market.rows('runs')[-1]['status'] == 'failed' and market.rows('runs')[-1]['count'] == 0
    assert len(list((market.root / 'captures' / rid).glob('*.bin'))) == 2
    with market.db() as db:
        assert db.execute("SELECT run_id FROM universe_snapshots WHERE source='BSE'").fetchone()[0] == previous


def test_database_error_rolls_back_delete_partial_insert_and_snapshot(market):
    previous = market.refresh_universe('BSE', bse_fetch=fetched)
    before = market.rows('listings')
    with market.db() as db:
        db.execute("""CREATE TRIGGER fixture_publication_failure BEFORE INSERT ON listings
                    WHEN NEW.source='BSE' AND NEW.ticker='920002'
                    BEGIN SELECT RAISE(ABORT,'Synthetic database failure'); END""")
    rid = market.refresh_universe('BSE', bse_fetch=fetched)
    assert market.rows('listings') == before
    assert next(r for r in market.rows('runs') if r['id'] == rid)['status'] == 'failed'
    with market.db() as db:
        assert db.execute("SELECT run_id FROM universe_snapshots WHERE source='BSE'").fetchone()[0] == previous


def test_http_failure_archived_and_previous_snapshot_retained(market):
    market.refresh_universe('BSE', bse_fetch=fetched)
    before = market.rows('listings')
    def denied(url, *, data):
        raise bse_sources.BSEFetchError(403, b'<html>Forbidden</html>')
    rid = market.refresh_universe('BSE', bse_fetch=denied)
    assert market.rows('listings') == before
    assert market.rows('runs')[-1]['status'] == 'failed'
    metadata = json.loads((market.root / 'captures' / rid / '0.json').read_text())
    assert metadata['status'] == 403 and metadata['page'] == 0
    assert (market.root / 'captures' / rid / '0.bin').read_bytes() == b'<html>Forbidden</html>'


def test_cancelled_after_capture_keeps_snapshot_and_cancelled_state(market):
    market.refresh_universe('BSE', bse_fetch=fetched)
    before = market.rows('listings')
    state = {'cancelled': False}
    def fetch_and_cancel(url, *, data):
        state['cancelled'] = True
        return fetched(url, data=data)
    rid = market.refresh_universe('BSE', bse_fetch=fetch_and_cancel, cancelled=lambda: state['cancelled'])
    assert market.rows('listings') == before
    assert market.rows('runs')[-1]['status'] == 'cancelled'
    assert len(list((market.root / 'captures' / rid).glob('*.bin'))) == 1


def test_cancelled_before_capture_does_not_fetch(market):
    rid = market.refresh_universe('BSE', bse_fetch=lambda *a, **k: pytest.fail('must not fetch'), cancelled=lambda: True)
    assert market.rows('runs')[-1]['status'] == 'cancelled'
    assert not (market.root / 'captures' / rid).exists()
    assert market.rows('listings') == []


def test_cancellation_during_database_publication_rolls_back(market, monkeypatch):
    previous = market.refresh_universe('BSE', bse_fetch=fetched)
    before = market.rows('listings')
    state = {'cancelled': False}
    original_db = market.db
    def connection():
        db = original_db()
        db.create_function('fixture_cancel', 0, lambda: state.update(cancelled=True) or 1)
        return db
    monkeypatch.setattr(market, 'db', connection)
    with market.db() as db:
        db.execute("""CREATE TRIGGER fixture_cancel_publication AFTER INSERT ON listings
                    WHEN NEW.source='BSE' BEGIN SELECT fixture_cancel(); END""")
    market.refresh_universe('BSE', bse_fetch=fetched, cancelled=lambda: state['cancelled'])
    assert state['cancelled'] and market.rows('listings') == before
    assert market.rows('runs')[-1]['status'] == 'cancelled'
    with market.db() as db:
        assert db.execute("SELECT run_id FROM universe_snapshots WHERE source='BSE'").fetchone()[0] == previous


def test_replaced_lease_owner_cannot_publish(market):
    market.refresh_universe('BSE', bse_fetch=fetched)
    before = market.rows('listings')
    def lose_lease(url, *, data):
        with market.db() as db:
            db.execute("UPDATE leases SET owner='new-owner' WHERE key='universe:BSE'")
        return fetched(url, data=data)
    market.refresh_universe('BSE', bse_fetch=lose_lease)
    assert market.rows('listings') == before
    assert market.rows('runs')[-1]['status'] == 'failed'
    assert 'lease lost' in market.rows('runs')[-1]['error']
    with market.db() as db:
        assert db.execute("SELECT owner FROM leases WHERE key='universe:BSE'").fetchone()[0] == 'new-owner'


def test_beijing_gap_removed_only_for_verified_current_rows(market):
    assert 'Beijing' in ' '.join(summary(market)['source_gaps'])
    rid = market.refresh_universe('BSE', bse_fetch=fetched)
    after = summary(market)
    assert 'Beijing' not in ' '.join(after['source_gaps'])
    bse = next(s for s in after['universe_sources'] if s['source'] == 'BSE')
    assert bse['current_snapshot_verified'] and bse['last_success_run']['id'] == rid
    assert bse['records'] == 3
    with market.db() as db:
        db.execute("UPDATE listings SET name='tampered' WHERE source='BSE' AND ticker='920001'")
    assert 'Beijing' in ' '.join(summary(market)['source_gaps'])
    assert not next(s for s in summary(market)['universe_sources'] if s['source'] == 'BSE')['current_snapshot_verified']


def test_directory_rows_without_successful_capture_are_not_verified(market):
    with market.db() as db:
        db.execute("INSERT INTO listings VALUES ('BSE:BSE:920001','A','BSE','920001','Fixture','医药制造业','BSE','2026-10-17')")
    assert 'Beijing' in ' '.join(summary(market)['source_gaps'])


def test_latest_failure_visible_with_retained_success(market):
    successful = market.refresh_universe('BSE', bse_fetch=fetched)
    def failed(url, *, data):
        raise OSError('Synthetic <source> unavailable')
    market.refresh_universe('BSE', bse_fetch=failed)
    data = summary(market)
    assert 'previous verified directory is retained' in ' '.join(data['source_gaps'])
    bse = next(s for s in data['universe_sources'] if s['source'] == 'BSE')
    assert bse['current_snapshot_verified'] and bse['last_success_run']['id'] == successful
    app = SimpleNamespace(workspace=market.root.parent.parent, state=market.state, token='fixture')
    rendered = progress(app, {})
    assert '北交所最近刷新未完成' in rendered
    assert 'Synthetic &lt;source&gt; unavailable' in rendered
    assert '北交所目录尚未接入。' not in rendered
    assert '北交所 A 股' in market_page(app, {})


def test_industry_drives_candidate_without_claiming_ownership(market):
    market.refresh_universe('BSE', bse_fetch=fetched)
    found = discover(market.rows('listings'))
    assert len(found) == 3 and all(r['classification'] == 'candidate' for r in found)
    assert all('suggested_aliases' not in r for r in found)
    refresh_candidates(market, found)
    data = summary(market)
    assert data['candidate_count'] == 3 and data['unmapped_candidate_count'] == 3
    assert data['full_pipeline_verified_count'] == 0 and data['full_market_coverage_rate'] is None


def test_queue_executes_bse_special_post_path(market, monkeypatch):
    monkeypatch.setattr(bse_sources, 'fetch_page', fetched)
    jid = market_jobs.enqueue(market, 'universe', 'BSE')
    market_jobs.execute(market, market_jobs.claim(market))
    job = next(j for j in market_jobs.jobs(market) if j['id'] == jid)
    assert job['status'] == 'succeeded' and job['run_id']
    assert len(market.rows('listings')) == 3


def test_capture_metadata_cannot_overwrite_integrity_fields(market):
    with pytest.raises(ValueError):
        market.archive('fixture', 0, bse_sources.ENDPOINT, b'public', {'sha256': 'invented'})
    with pytest.raises(ValueError):
        market.archive('fixture', 0, bse_sources.ENDPOINT, b'public', {'method': 'DELETE'})
