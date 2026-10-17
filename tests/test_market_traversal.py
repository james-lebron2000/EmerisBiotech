"""Synthetic full-candidate traversal, retry boundaries and atomic queue records."""
import json
from datetime import datetime, timezone

import pytest

from vbt.market import Market
from vbt.market_coverage import refresh_candidates
from vbt.market_traversal import enqueue_traversal, readiness, batches

CURRENT = datetime(2026, 10, 17, 12, tzinfo=timezone.utc)


def seed(market, number=65):
    refresh_candidates(market, [dict(market='US', ticker='S'+str(i).zfill(3),
        name='Synthetic'+str(i).zfill(3)+' Therapeutics', classification='candidate',
        reason='Synthetic engineering fixture', source='synthetic', evidence=['Synthetic fixture, not market evidence'])
        for i in range(number)])


@pytest.fixture
def market(tmp_path):
    m=Market(tmp_path/'w',tmp_path/'s'); seed(m)
    return m


def observe(market, cid, status, started, candidate_hash=None):
    readiness(market, current=CURRENT)
    with market.db() as db:
        current_hash=db.execute('SELECT content_hash FROM issuer_candidates WHERE id=?',(cid,)).fetchone()[0]
        db.execute('''INSERT INTO sponsor_resolution_runs
          (id,candidate_id,candidate_hash,query,started,status) VALUES (?,?,?,?,?,?)''',
          (cid+'-'+status,cid,candidate_hash or current_hash,'synthetic',started,status))


def test_traverses_all_current_eligible_candidates_and_is_idempotent(market):
    before=readiness(market,current=CURRENT)
    assert before['counts']['ready']==65
    result=enqueue_traversal(market,current=CURRENT)
    assert len(result['new_jobs'])==65 and result['remaining_ready']==0
    assert result['after']['active']==65
    assert enqueue_traversal(market,current=CURRENT)['new_jobs']==[]
    with market.db() as db:
        assert db.execute('SELECT count(*) FROM companies').fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM sponsor_proposals').fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM collection_jobs').fetchone()[0]==65
    assert len(batches(market))==2


def test_active_front_of_universe_does_not_starve_later_candidates(market):
    first=enqueue_traversal(market,limit=20,current=CURRENT)
    second=enqueue_traversal(market,limit=20,current=CURRENT)
    assert len(first['new_jobs'])==len(second['new_jobs'])==20
    assert not set(first['candidate_ids'])&set(second['candidate_ids'])
    assert second['remaining_ready']==25


@pytest.mark.parametrize('status, started, expected',[
    ('failed','2026-10-17T00:00:00+00:00','retry_wait'),
    ('failed','2026-10-16T00:00:00+00:00','ready'),
    ('zero','2026-10-01T00:00:00+00:00','zero_wait'),
    ('zero','2026-09-01T00:00:00+00:00','ready'),
    ('busy','2026-10-17T11:59:00+00:00','retry_wait'),
    ('busy','2026-10-17T11:50:00+00:00','ready'),
    ('suggested','2026-01-01T00:00:00+00:00','held_review'),
    ('ambiguous','2026-01-01T00:00:00+00:00','held_review'),
    ('partial','2026-01-01T00:00:00+00:00','held_review'),
    ('failed','not-a-time','invalid_time'),
    ('failed','2026-01-01','invalid_time'),
    ('failed','2027-01-01T00:00:00+00:00','invalid_time'),
    ('unexpected','2026-01-01T00:00:00+00:00','unknown_state'),
])
def test_retry_and_review_states_are_explicit(market,status,started,expected):
    observe(market,'US:S000',status,started)
    row=next(r for r in readiness(market,current=CURRENT)['rows'] if r['id']=='US:S000')
    assert row['readiness']==expected


def test_changed_candidate_is_revisited_but_unchanged_review_is_held(market):
    observe(market,'US:S000','suggested','2026-10-17T00:00:00+00:00')
    observe(market,'US:S001','suggested','2026-10-17T00:00:00+00:00',candidate_hash='old-version')
    states={r['id']:r['readiness'] for r in readiness(market,current=CURRENT)['rows']}
    assert states['US:S000']=='held_review' and states['US:S001']=='ready'


def test_chinese_name_gap_and_market_filters_are_not_zero_pipeline_claims(market):
    refresh_candidates(market,[dict(market='A',ticker='920000',name='合成药业',classification='candidate',
        reason='Synthetic engineering fixture',source='synthetic',evidence=['Synthetic fixture, not market evidence'])])
    scoped=readiness(market,'A',current=CURRENT)
    assert scoped['total_unmapped']==1 and scoped['counts']['needs_name']==1
    assert enqueue_traversal(market,market_filter='A',current=CURRENT)['new_jobs']==[]
    with pytest.raises(ValueError):readiness(market,'ALL',current=CURRENT)


def test_queue_batch_and_journal_rollback_together(market):
    readiness(market,current=CURRENT)
    with market.db() as db:
        db.execute("CREATE TRIGGER reject_batch BEFORE INSERT ON market_traversal_batches BEGIN SELECT RAISE(ABORT,'synthetic disk failure'); END")
    with pytest.raises(Exception,match='synthetic disk failure'):
        enqueue_traversal(market,current=CURRENT)
    with market.db() as db:
        assert db.execute('SELECT count(*) FROM collection_jobs').fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM market_traversal_batches').fetchone()[0]==0


def test_concurrent_snapshots_fill_from_later_candidates_without_duplicates(market,monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from vbt import market_traversal as module
    original=module.readiness; barrier=threading.Barrier(2); local=threading.local()
    def simultaneous(*args,**kwargs):
        result=original(*args,**kwargs)
        if not getattr(local,'observed',False):
            local.observed=True;barrier.wait(timeout=5)
        return result
    monkeypatch.setattr(module,'readiness',simultaneous)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:module.enqueue_traversal(market,limit=20,current=CURRENT),range(2)))
    assert [len(r['new_jobs']) for r in results]==[20,20]
    assert not set(results[0]['candidate_ids'])&set(results[1]['candidate_ids'])


@pytest.mark.parametrize('limit',[0,1001,True,'20'])
def test_unbounded_or_invalid_batch_is_rejected(market,limit):
    with pytest.raises(ValueError):enqueue_traversal(market,limit=limit,current=CURRENT)
