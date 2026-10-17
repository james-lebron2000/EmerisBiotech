"""Synthetic end-to-end durable identity collection and follow-up safety."""
import json
import os
import time

import pytest

from vbt.identity_collection import DIRECTORY_URLS, NCT_API, collect, init_schema as identity_schema, runs
from vbt.market import Market
from vbt.market_coverage import refresh_candidates
from vbt.market_jobs import cancel, claim, enqueue, enqueue_identity, execute, jobs, recover
from vbt.sponsor_resolution import proposals, resolve


@pytest.fixture
def case(tmp_path, monkeypatch):
    import vbt.identity_collection as identity
    market = Market(tmp_path / 'workspace', tmp_path / 'state')
    refresh_candidates(market, [{'market': 'US', 'ticker': 'SYN',
        'name': 'Syntheticnova Therapeutics Inc. - Common Stock',
        'classification': 'candidate', 'reason': 'Synthetic fixture only',
        'source': 'Synthetic fixture', 'evidence': [{'kind': 'synthetic'}]}])
    record = {'protocolSection': {'identificationModule': {'nctId': 'NCT00000001'},
        'sponsorCollaboratorsModule': {'leadSponsor': {'name': 'Syntheticnova Therapeutics Inc.'}}}}
    resolve(market, 'US:SYN', fetch=lambda url: json.dumps({'studies': [record]}).encode())
    proposal = proposals(market)[0]
    docs = {DIRECTORY_URLS[0]: b'Symbol|Security Name|Test Issue|ETF\nSYN|Syntheticnova Therapeutics Inc. - Common Stock|N|N\n',
        NCT_API + 'NCT00000001': json.dumps(record).encode()}
    original = identity.collect
    monkeypatch.setattr(identity, 'collect', lambda m, pid, **kw: original(m, pid, fetch=docs.__getitem__, **kw))
    return market, proposal, docs


def by_id(market, job_id):
    with market.db() as db:
        return dict(db.execute('SELECT * FROM collection_jobs WHERE id=?', (job_id,)).fetchone())


def test_real_identity_job_atomically_accepts_then_queues_company(case):
    m, p, docs = case
    jid = enqueue(m, 'identity', p['id'])
    assert enqueue(m, 'identity', p['id']) == jid and enqueue_identity(m) == []
    execute(m, claim(m))
    identity = by_id(m, jid)
    assert identity['status'] == 'succeeded' and identity['run_id']
    assert m.count('companies') == 1 and m.count('trials') == 0
    company = [r for r in jobs(m) if r['kind'] == 'company']
    assert len(company) == 1 and company[0]['reference'] == 'US:SYN' and company[0]['status'] == 'queued'
    # A second successful idempotent job reuses proof and the active company job.
    jid2 = enqueue(m, 'identity', p['id'])
    job = claim(m)
    # The company follow-up predates the second identity request; choose the
    # identity job explicitly after moving the unrelated job into the future.
    if job['kind'] == 'company':
        with m.db() as db:
            db.execute("UPDATE collection_jobs SET status='queued',owner_pid=NULL,next_at=? WHERE id=?", (time.time() + 3600, job['id']))
        job = claim(m)
    assert job['id'] == jid2
    execute(m, job)
    assert by_id(m, jid2)['status'] == 'succeeded'
    assert len([r for r in jobs(m) if r['kind'] == 'company']) == 1
    assert len(runs(m)) == 1


def test_company_followup_runs_existing_registry_collector(case, monkeypatch):
    m, p, docs = case
    enqueue(m, 'identity', p['id']); execute(m, claim(m))
    def sync(company, cancelled):
        record = json.loads(docs[NCT_API + 'NCT00000001'])
        record['protocolSection']['armsInterventionsModule']={'interventions':[{'type':'DRUG','name':'Synthetic test drug'}]}
        return Market.sync_company(m, company, fetch=lambda url: json.dumps({'studies': [record]}).encode(), cancelled=cancelled)
    monkeypatch.setattr(m, 'sync_company', sync)
    execute(m, claim(m))
    assert all(row['status'] == 'succeeded' for row in jobs(m)) and m.count('trials') == 1


def test_invalid_or_rejected_identity_target_never_enqueues(case):
    m, p, docs = case
    for reference in ('../escape', 'a' * 32, p['id'] + '?bad=1'):
        with pytest.raises(ValueError): enqueue(m, 'identity', reference)
    with m.db() as db: db.execute("UPDATE sponsor_proposals SET status='rejected'")
    with pytest.raises(ValueError): enqueue(m, 'identity', p['id'])


def test_busy_identity_waits_without_scheduling_followup(case):
    m, p, docs = case
    assert m.claim('identity:' + p['id'])
    jid = enqueue(m, 'identity', p['id']); execute(m, claim(m))
    assert by_id(m, jid)['status'] == 'retry_wait' and by_id(m, jid)['next_at'] > time.time()
    assert not any(row['kind'] == 'company' for row in jobs(m))
    m.release('identity:' + p['id'])
    with m.db() as db: db.execute('UPDATE collection_jobs SET next_at=0 WHERE id=?', (jid,))
    execute(m, claim(m))
    assert by_id(m, jid)['status'] == 'succeeded'


def test_cancelled_before_identity_publication_creates_no_company(case, monkeypatch):
    import vbt.identity_collection as identity
    m, p, docs = case
    jid = enqueue(m, 'identity', p['id'])
    def request(url):
        if url.startswith(NCT_API): cancel(m, jid)
        return docs[url]
    monkeypatch.setattr(identity, 'collect', lambda market, pid, **kw: collect(market, pid, fetch=request, **kw))
    execute(m, claim(m))
    assert by_id(m, jid)['status'] == 'cancelled' and m.count('companies') == 0
    assert runs(m)[0]['status'] == 'cancelled'
    assert not any(row['kind'] == 'company' for row in jobs(m))


def test_cancel_after_mapping_prevents_unwanted_followup(case, monkeypatch):
    import vbt.identity_collection as identity
    m, p, docs = case
    jid = enqueue(m, 'identity', p['id'])
    def complete_then_cancel(market, pid, **kw):
        result = collect(market, pid, fetch=docs.__getitem__, **kw)
        cancel(market, jid)
        return result
    monkeypatch.setattr(identity, 'collect', complete_then_cancel)
    execute(m, claim(m))
    assert by_id(m, jid)['status'] == 'cancelled' and m.count('companies') == 1
    assert runs(m)[0]['status'] == 'succeeded'
    assert not any(row['kind'] == 'company' for row in jobs(m))


def test_crash_after_accepted_identity_is_recovered_and_followup_repaired(case, monkeypatch):
    import vbt.identity_collection as identity
    m, p, docs = case
    jid = enqueue(m, 'identity', p['id'])
    def complete_then_crash(market, pid, **kw):
        result = collect(market, pid, fetch=docs.__getitem__, **kw)
        assert result['status'] == 'succeeded'
        raise SystemExit('Synthetic process death before follow-up scheduling')
    monkeypatch.setattr(identity, 'collect', complete_then_crash)
    with pytest.raises(SystemExit): execute(m, claim(m))
    assert by_id(m, jid)['status'] == 'running' and m.count('companies') == 1
    assert not any(row['kind'] == 'company' for row in jobs(m))
    with m.db() as db: db.execute('UPDATE collection_jobs SET owner_pid=99999999,heartbeat=0 WHERE id=?', (jid,))
    recover(m)
    assert by_id(m, jid)['status'] == 'queued' and runs(m)[0]['status'] == 'succeeded'
    monkeypatch.setattr(identity, 'collect', lambda market, pid, **kw: collect(market, pid, fetch=lambda u: pytest.fail('Recovery must reuse verified proof'), **kw))
    execute(m, claim(m))
    assert by_id(m, jid)['status'] == 'succeeded' and by_id(m, jid)['attempts'] == 2
    assert len([row for row in jobs(m) if row['kind'] == 'company']) == 1
    assert len(runs(m)) == 1


def test_stale_attempt_cannot_enqueue_company_or_overwrite_new_attempt(case, monkeypatch):
    import vbt.identity_collection as identity
    m, p, docs = case
    jid = enqueue(m, 'identity', p['id']); old = claim(m); newer = []
    def complete_and_recover(market, pid, **kw):
        result = collect(market, pid, fetch=docs.__getitem__, **kw)
        with m.db() as db: db.execute('UPDATE collection_jobs SET heartbeat=0 WHERE id=?', (jid,))
        recover(m)
        newer.append(claim(m))
        return result
    monkeypatch.setattr(identity, 'collect', complete_and_recover)
    execute(m, old)
    assert by_id(m, jid)['status'] == 'running' and by_id(m, jid)['attempts'] == 2
    assert not any(row['kind'] == 'company' for row in jobs(m))
    monkeypatch.setattr(identity, 'collect', lambda market, pid, **kw: collect(market, pid, fetch=docs.__getitem__, **kw))
    execute(m, newer[0])
    assert by_id(m, jid)['status'] == 'succeeded'
    assert len([row for row in jobs(m) if row['kind'] == 'company']) == 1


def test_recovery_changes_only_exact_run_namespace_and_owner(case):
    m, p, docs = case
    identity_schema(m)
    jid = enqueue(m, 'identity', p['id']); claim(m)
    key = 'identity:' + p['id']; assert m.claim(key)
    token = m.lease_tokens()[key]
    rid = 'a' * 32
    with m.db() as db:
        db.execute('''INSERT INTO identity_collection_runs
          (id,proposal_id,candidate_id,candidate_hash,started,status,lease_owner)
          VALUES (?,?,?,?,?,'running',?)''', (rid, p['id'], p['candidate_id'], p['candidate_hash'], '2026-10-17', token))
        db.execute('INSERT INTO runs VALUES (?,?,?,?,?,?,?,?)', (rid, 'Synthetic namespace sentinel', '', '2026-10-17', None, 'running', 0, ''))
        db.execute('UPDATE collection_jobs SET run_id=?,owner_pid=99999999,heartbeat=0 WHERE id=?', (rid, jid))
        db.execute("UPDATE leases SET owner='new-owner' WHERE key=?", (key,))
    recover(m)
    assert by_id(m, jid)['status'] == 'queued'
    with m.db() as db:
        assert db.execute('SELECT status FROM identity_collection_runs WHERE id=?', (rid,)).fetchone()[0] == 'interrupted'
        assert db.execute('SELECT status FROM runs WHERE id=?', (rid,)).fetchone()[0] == 'running'
        assert db.execute('SELECT owner FROM leases WHERE key=?', (key,)).fetchone()[0] == 'new-owner'
        assert db.execute("SELECT event FROM identity_collection_events WHERE run_id=?", (rid,)).fetchone()[0] == 'worker_interrupted'


def test_recovery_does_not_touch_mismatched_proposal_run(case):
    m, p, docs = case
    identity_schema(m)
    jid = enqueue(m, 'identity', p['id']); claim(m)
    unrelated_id, unrelated_proposal = 'c' * 32, 'd' * 32
    key = 'identity:' + unrelated_proposal; assert m.claim(key)
    token = m.lease_tokens()[key]
    with m.db() as db:
        db.execute('''INSERT INTO identity_collection_runs
          (id,proposal_id,candidate_id,candidate_hash,started,status,lease_owner)
          VALUES (?,?,?,?,?,'running',?)''', (unrelated_id, unrelated_proposal, 'US:OTHER', 'other-hash', '2026-10-17', token))
        db.execute('UPDATE collection_jobs SET run_id=?,owner_pid=99999999,heartbeat=0 WHERE id=?', (unrelated_id, jid))
    recover(m)
    with m.db() as db:
        assert db.execute('SELECT status FROM identity_collection_runs WHERE id=?', (unrelated_id,)).fetchone()[0] == 'running'
        assert db.execute('SELECT owner FROM leases WHERE key=?', (key,)).fetchone()[0] == token
    assert by_id(m, jid)['status'] == 'queued'


def test_run_is_linked_before_callback_so_earliest_crash_is_recoverable(case, monkeypatch):
    import vbt.identity_collection as identity
    m, p, docs = case
    jid = enqueue(m, 'identity', p['id'])
    def crash_before_callback(market, pid, **kw):
        def on_start(run_id):
            assert by_id(market, jid)['run_id'] == run_id
            raise SystemExit('Synthetic death before callback writes anything')
        kw['on_start'] = on_start
        return collect(market, pid, fetch=docs.__getitem__, **kw)
    monkeypatch.setattr(identity, 'collect', crash_before_callback)
    with pytest.raises(SystemExit): execute(m, claim(m))
    row = by_id(m, jid)
    assert row['run_id'] and row['status'] == 'running'
    with m.db() as db: db.execute('UPDATE collection_jobs SET owner_pid=99999999,heartbeat=0 WHERE id=?', (jid,))
    recover(m)
    assert by_id(m, jid)['status'] == 'queued'
    assert runs(m)[0]['status'] == 'interrupted'
    with m.db() as db:
        assert db.execute('SELECT 1 FROM leases WHERE key=?', ('identity:' + p['id'],)).fetchone() is None


def test_no_identity_tables_created_for_unrelated_job_recovery(tmp_path):
    m = Market(tmp_path / 'workspace', tmp_path / 'state')
    m.add_company('US', 'TEST', 'Synthetic', ['Synthetic'], 'Synthetic fixture')
    jid = enqueue(m, 'company', 'US:TEST'); claim(m)
    with m.db() as db: db.execute('UPDATE collection_jobs SET owner_pid=99999999,heartbeat=0 WHERE id=?', (jid,))
    recover(m)
    with m.db() as db:
        assert db.execute("SELECT 1 FROM sqlite_master WHERE name='identity_collection_runs'").fetchone() is None
    assert by_id(m, jid)['status'] == 'queued'


def test_completed_mapping_followup_transaction_failure_does_not_fake_job_success(case, monkeypatch):
    import vbt.market_jobs as queue
    m, p, docs = case
    jid = enqueue(m, 'identity', p['id'])
    original = queue._enqueue_in_transaction
    def fail_followup(db, kind, reference):
        if kind == 'company': raise OSError('Synthetic queue storage failure')
        return original(db, kind, reference)
    monkeypatch.setattr(queue, '_enqueue_in_transaction', fail_followup)
    execute(m, claim(m))
    assert by_id(m, jid)['status'] == 'failed' and m.count('companies') == 1
    assert runs(m)[0]['status'] == 'succeeded'
    assert not any(row['kind'] == 'company' for row in jobs(m))
    monkeypatch.setattr(queue, '_enqueue_in_transaction', original)
    jid2 = enqueue(m, 'identity', p['id']); execute(m, claim(m))
    assert by_id(m, jid2)['status'] == 'succeeded'
    assert len([row for row in jobs(m) if row['kind'] == 'company']) == 1
