"""Synthetic claim ordering; no collectors, source requests or production data."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from vbt.market import Market
from vbt import market_jobs as queue


@pytest.fixture
def market(tmp_path):
    market = Market(tmp_path / 'workspace', tmp_path / 'state')
    queue.init_schema(market)
    return market


def add(market, kind, number=0, *, created='2026-01-01T00:00:00+00:00',
        status='queued', next_at=0, cancel_requested=0, job_id=None):
    job_id = job_id or kind + '-' + str(number).zfill(4)
    with market.db() as db:
        db.execute('''INSERT INTO collection_jobs
          (id,kind,reference,status,created,updated,next_at,cancel_requested)
          VALUES (?,?,?,?,?,?,?,?)''',
                   (job_id, kind, kind + ':' + str(number), status, created, created, next_at, cancel_requested))
    return job_id


def cursor(market):
    with market.db() as db:
        return db.execute('SELECT position FROM collection_claim_cursor WHERE id=1').fetchone()[0]


def test_bulk_resolve_cannot_hide_new_identity_company_or_refresh(market):
    for number in range(250):
        add(market, 'resolve', number)
    identity = add(market, 'identity', created='2026-10-17T00:00:00+00:00')
    company = add(market, 'company', created='2026-10-17T00:00:01+00:00')
    refresh = add(market, 'universe', created='2026-10-17T00:00:02+00:00')
    claimed = [queue.claim(market) for _ in range(4)]
    assert [row['kind'] for row in claimed] == ['universe', 'identity', 'resolve', 'company']
    assert {row['id'] for row in claimed} >= {identity, company, refresh}


def test_saturated_kinds_follow_weighted_cycle_without_starvation(market):
    for kind in set(queue.CLAIM_CYCLE):
        for number in range(8):
            add(market, kind, number)
    observed = [queue.claim(market)['kind'] for _ in range(18)]
    assert observed == list(queue.CLAIM_CYCLE) * 3
    assert observed.count('resolve') == 6
    assert all(observed.count(kind) == 3 for kind in set(queue.CLAIM_CYCLE) - {'resolve'})


def test_fifo_within_kind_uses_created_then_id(market):
    add(market, 'resolve', 1, created='2026-02-01T00:00:00+00:00', job_id='b')
    add(market, 'resolve', 2, created='2026-02-01T00:00:00+00:00', job_id='a')
    add(market, 'resolve', 3, created='2026-01-01T00:00:00+00:00', job_id='oldest')
    assert [queue.claim(market)['id'] for _ in range(3)] == ['oldest', 'a', 'b']


def test_future_retries_and_cancel_requests_are_not_claimed(market):
    future = add(market, 'resolve', status='retry_wait', next_at=time.time() + 600)
    flagged = add(market, 'identity', cancel_requested=1)
    cancelled = add(market, 'universe')
    queue.cancel(market, cancelled)
    due = add(market, 'company', status='retry_wait', next_at=time.time() - 1)
    assert queue.claim(market)['id'] == due
    assert queue.claim(market) is None
    with market.db() as db:
        rows = {r['id']: dict(r) for r in db.execute('SELECT * FROM collection_jobs')}
    assert rows[future]['status'] == 'retry_wait' and rows[future]['attempts'] == 0
    assert rows[flagged]['status'] == 'queued' and rows[flagged]['attempts'] == 0
    assert rows[cancelled]['status'] == 'cancelled'


def test_empty_kinds_are_skipped_and_idle_claim_does_not_reset_cursor(market):
    add(market, 'company')
    assert queue.claim(market)['kind'] == 'company'
    assert cursor(market) == 4
    assert queue.claim(market) is None
    assert cursor(market) == 4
    add(market, 'identity')
    add(market, 'disclosure')
    assert queue.claim(market)['kind'] == 'disclosure'
    assert queue.claim(market)['kind'] == 'identity'


def test_concurrent_threads_claim_distinct_fenced_attempts(market):
    for number in range(50):
        add(market, queue.CLAIM_CYCLE[number % len(queue.CLAIM_CYCLE)], number)
    with ThreadPoolExecutor(max_workers=8) as workers:
        observed = list(workers.map(lambda _: queue.claim(market), range(60)))
    observed = [row for row in observed if row]
    assert len(observed) == len({row['id'] for row in observed}) == 50
    assert all(row['attempts'] == 1 for row in observed)
    with market.db() as db:
        rows = db.execute('SELECT status,attempts,owner_pid FROM collection_jobs').fetchall()
    assert all(tuple(row) == ('running', 1, os.getpid()) for row in rows)


CHILD = '''import json,sys
sys.path.insert(0,sys.argv[1])
from vbt.market import Market
from vbt.market_jobs import init_schema,claim
m=Market(sys.argv[2],sys.argv[3]);init_schema(m)
result=[]
for number in range(int(sys.argv[4])):
    row=claim(m)
    if row is None:break
    result.append({'id':row['id'],'kind':row['kind'],'attempts':row['attempts']})
print(json.dumps(result))
'''


def child_args(market, count=1):
    return [sys.executable, '-S', '-c', CHILD, str(Path(__file__).resolve().parents[1] / 'src'),
            str(market.root.parent.parent), str(market.state), str(count)]


def test_new_processes_keep_the_committed_cursor(market):
    for number, kind in enumerate(queue.CLAIM_CYCLE):
        add(market, kind, number)
    assert queue.claim(market)['kind'] == 'universe'
    assert cursor(market) == 1
    first = json.loads(subprocess.run(child_args(market), capture_output=True, text=True, check=True, timeout=15).stdout)
    second = json.loads(subprocess.run(child_args(market), capture_output=True, text=True, check=True, timeout=15).stdout)
    assert first[0]['kind'] == 'identity' and second[0]['kind'] == 'resolve'
    assert cursor(market) == 3
    queue.init_schema(market)
    assert cursor(market) == 3


def test_parallel_process_claims_cannot_duplicate_jobs(market):
    for number in range(40):
        add(market, queue.CLAIM_CYCLE[number % len(queue.CLAIM_CYCLE)], number)
    workers = [subprocess.Popen(child_args(market, 30), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
               for _ in range(2)]
    results = []
    for worker in workers:
        output, error = worker.communicate(timeout=15)
        assert worker.returncode == 0, error
        results.extend(json.loads(output))
    assert len(results) == len({row['id'] for row in results}) == 40
    assert all(row['attempts'] == 1 for row in results)


def test_cursor_and_job_claim_rollback_together(market):
    jid = add(market, 'universe')
    with market.db() as db:
        db.execute("CREATE TRIGGER deny_cursor BEFORE UPDATE ON collection_claim_cursor BEGIN SELECT RAISE(ABORT,'synthetic cursor write failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match='synthetic cursor write failure'):
        queue.claim(market)
    assert cursor(market) == 0
    with market.db() as db:
        row = db.execute('SELECT status,attempts,owner_pid FROM collection_jobs WHERE id=?', (jid,)).fetchone()
        assert tuple(row) == ('queued', 0, None)
        db.execute('DROP TRIGGER deny_cursor')
    assert queue.claim(market)['id'] == jid
    assert cursor(market) == 1
