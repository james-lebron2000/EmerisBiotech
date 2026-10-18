"""Synthetic durable scheduling and handoff recovery; no live HTTP or approvals."""
import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest

from vbt import identity_collection as identity
from vbt.identity_collection import collect as collect_identity
from vbt import market_jobs as queue
from vbt.market import Market
from vbt.market_coverage import refresh_candidates
from vbt.sponsor_resolution import init_schema as resolution_schema, query_name_from_listing


def stamp(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


@pytest.fixture
def market(tmp_path):
    market = Market(tmp_path / 'workspace', tmp_path / 'state')
    resolution_schema(market)
    identity.init_schema(market)
    queue.init_schema(market)
    return market


def proposals(market, count=1):
    refresh_candidates(market, [dict(market='US', ticker=f'SYN{number:04}',
        name=f'Syntheticnova{number} Therapeutics Inc. - Common Stock', classification='candidate',
        reason='Synthetic scheduling fixture', source='Synthetic fixture', evidence=[{'kind': 'synthetic'}])
        for number in range(1, count + 1)])
    result = []
    with market.db() as db:
        rows = db.execute('SELECT * FROM issuer_candidates ORDER BY id').fetchall()
        for number, row in enumerate(rows, 1):
            pid, rid = f'{number:032x}', f'{number + 10000:032x}'
            alias = f'Syntheticnova{number} Therapeutics Inc.'
            nct = f'NCT{number:08}'
            timestamp = stamp(time.time() - 60)
            db.execute('''INSERT INTO sponsor_resolution_runs
              (id,candidate_id,candidate_hash,query,started,finished,status,truncated)
              VALUES (?,?,?,?,?,?,'suggested',0)''',
              (rid, row['id'], row['content_hash'], query_name_from_listing(row['name'], row['market']), timestamp, timestamp))
            db.execute('INSERT INTO sponsor_proposals VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
              (pid, row['id'], row['content_hash'], alias, alias.casefold(), 'normalized_exact',
               json.dumps([nct]), 'https://clinicaltrials.gov/study/' + nct, rid, timestamp, timestamp, 'pending'))
            result.append(dict(id=pid, candidate_id=row['id'], candidate_hash=row['content_hash'], alias=alias,
                               nct=nct, ticker=row['ticker']))
    return result


def attempt(market, proposal, status='failed', age=3600, **extra):
    rid = uuid.uuid4().hex
    values = dict(started=stamp(time.time() - age - 10), finished=stamp(time.time() - age), status=status)
    values.update(extra)
    with market.db() as db:
        db.execute('''INSERT INTO identity_collection_runs
          (id,proposal_id,candidate_id,candidate_hash,started,finished,status)
          VALUES (?,?,?,?,?,?,?)''', (rid, proposal['id'], proposal['candidate_id'], proposal['candidate_hash'],
                                    values['started'], values['finished'], values['status']))
    return rid


def queued_references(market):
    with market.db() as db:
        return [r[0] for r in db.execute("SELECT reference FROM collection_jobs WHERE kind='identity' AND status='queued' ORDER BY created,id")]


def state(market, pid, **kwargs):
    return next(row for row in queue.identity_schedule(market, **kwargs) if row['proposal_id'] == pid)


@pytest.mark.parametrize('status', ['failed', 'cancelled', 'interrupted', 'busy', 'superseded'])
def test_known_failure_has_24_hour_cooldown_and_observable_reason(market, status):
    proposal = proposals(market)[0]
    attempt(market, proposal, status)
    before = state(market, proposal['id'])
    assert before['status'] == 'cooldown' and '24小时' in before['reason']
    assert before['retry_at'] and before['last_attempt'] and before['candidate_hash'] == proposal['candidate_hash']
    assert queue.enqueue_identity(market) == []
    assert len(queue.enqueue_identity(market, force=True)) == 1


def test_never_attempted_proposals_precede_old_failures(market):
    old, new = proposals(market, 2)
    attempt(market, old, age=25 * 3600)
    assert len(queue.enqueue_identity(market, limit=1)) == 1
    assert queued_references(market) == [new['id']]
    assert len(queue.enqueue_identity(market, limit=1)) == 1
    assert set(queued_references(market)) == {old['id'], new['id']}


def test_pagination_skips_more_than_one_page_of_blocked_work(market, monkeypatch):
    rows = proposals(market, 108)
    for row in rows[:105]:
        attempt(market, row, 'blocked', age=30 * 3600)
    original = identity.eligible_proposals
    pages = []
    def observe(m, **kwargs):
        pages.append(kwargs['offset'])
        return original(m, **kwargs)
    monkeypatch.setattr(identity, 'eligible_proposals', observe)
    assert len(queue.enqueue_identity(market, limit=2)) == 2
    assert pages == [0, 100]
    assert set(queued_references(market)) == {rows[105]['id'], rows[106]['id']}


def test_active_work_does_not_consume_new_job_limit_and_manual_enqueue_stays_idempotent(market):
    rows = proposals(market, 3)
    active = queue.enqueue(market, 'identity', rows[0]['id'])
    assert queue.enqueue(market, 'identity', rows[0]['id']) == active
    added = queue.enqueue_identity(market, limit=2, force=True)
    assert len(added) == 2 and active not in added
    assert queue.enqueue_identity(market) == []
    assert state(market, rows[0]['id'])['status'] == 'active'
    assert len(queued_references(market)) == 3


def test_blocked_version_does_not_repeat_even_after_resolution_refresh_or_force(market):
    proposal = proposals(market)[0]
    attempt(market, proposal, 'blocked', age=48 * 3600)
    with market.db() as db:
        db.execute('UPDATE sponsor_proposals SET last_seen=?', (stamp(time.time()),))
    assert state(market, proposal['id'], force=True)['status'] == 'blocked'
    assert queue.enqueue_identity(market, force=True) == []
    # The explicit one-target action keeps its existing manual semantics.
    assert queue.enqueue(market, 'identity', proposal['id'])


def test_new_candidate_hash_is_not_blocked_by_old_version_history(market):
    proposal = proposals(market)[0]
    rid = attempt(market, proposal, 'blocked', age=48 * 3600)
    jid = queue.enqueue(market, 'identity', proposal['id'])
    with market.db() as db:
        db.execute("UPDATE collection_jobs SET status='blocked',run_id=? WHERE id=?", (rid, jid))
        db.execute("UPDATE issuer_candidates SET content_hash='new-synthetic-version'")
        db.execute("UPDATE sponsor_proposals SET candidate_hash='new-synthetic-version'")
    assert len(queue.enqueue_identity(market)) == 1
    assert len(queued_references(market)) == 1


@pytest.mark.parametrize('extra', [
    dict(status='unexpected-state'), dict(started='unknown'), dict(finished=None),
    dict(finished='2026-10-17T01:00:00'), dict(finished='9999-12-31T00:00:00+00:00'),
    dict(started=stamp(time.time()), finished=stamp(time.time() - 10)),
])
def test_abnormal_run_states_and_times_fail_closed_even_when_forced(market, extra):
    proposal = proposals(market)[0]
    attempt(market, proposal, **extra)
    assert state(market, proposal['id'], force=True)['status'] == 'unknown'
    assert queue.enqueue_identity(market, force=True) == []


@pytest.mark.parametrize('column,value', [('status', 'unexpected'), ('updated', 'unknown'),
                                         ('created', '2026-10-17T01:00:00')])
def test_abnormal_terminal_queue_state_fails_closed(market, column, value):
    proposal = proposals(market)[0]
    jid = queue.enqueue(market, 'identity', proposal['id'])
    with market.db() as db:
        db.execute("UPDATE collection_jobs SET status='failed',created=?,updated=? WHERE id=?",
                   (stamp(time.time() - 26 * 3600), stamp(time.time() - 25 * 3600), jid))
        db.execute('UPDATE collection_jobs SET ' + column + '=? WHERE id=?', (value, jid))
    assert state(market, proposal['id'], force=True)['status'] == 'unknown'
    assert queue.enqueue_identity(market, force=True) == []


@pytest.mark.parametrize('column,value', [('alias', 'Unrelated Therapeutics'), ('source_url', 'javascript:bad()')])
def test_forced_batch_does_not_bypass_source_eligibility(market, column, value):
    proposal = proposals(market)[0]
    with market.db() as db:
        db.execute('UPDATE sponsor_proposals SET ' + column + '=?', (value,))
    assert state(market, proposal['id'], force=True)['status'] == 'ineligible'
    assert queue.enqueue_identity(market, force=True) == []


def test_concurrent_helpers_enqueue_each_proposal_once(market):
    proposals(market, 4)
    with ThreadPoolExecutor(max_workers=4) as workers:
        results = list(workers.map(lambda _: queue.enqueue_identity(market, limit=2), range(4)))
    flattened = [jid for result in results for jid in result]
    assert len(flattened) == len(set(flattened)) == 4
    assert len(queued_references(market)) == 4


@pytest.mark.parametrize('limit,force', [(0, False), (1001, False), (True, False), (1, 'yes')])
def test_invalid_batch_controls_rejected(market, limit, force):
    with pytest.raises(ValueError):
        queue.enqueue_identity(market, limit=limit, force=force)


@pytest.fixture
def failed_handoff(market, monkeypatch):
    proposal = proposals(market)[0]
    protocol = {'protocolSection': {'identificationModule': {'nctId': proposal['nct']},
                'sponsorCollaboratorsModule': {'leadSponsor': {'name': proposal['alias']}}}}
    documents = {identity.DIRECTORY_URLS[0]: (
        'Symbol|Security Name|Test Issue|ETF\n' + proposal['ticker'] + '|'
        + proposal['alias'] + ' - Common Stock|N|N\n').encode(),
        identity.NCT_API + proposal['nct']: json.dumps(protocol).encode()}
    original_collect, original_enqueue = identity.collect, queue._enqueue_in_transaction
    monkeypatch.setattr(identity, 'collect', lambda m, pid, **kw:
                        original_collect(m, pid, fetch=documents.__getitem__, **kw))
    def fail_company(db, kind, reference):
        if kind == 'company':
            raise OSError('Synthetic handoff storage failure')
        return original_enqueue(db, kind, reference)
    monkeypatch.setattr(queue, '_enqueue_in_transaction', fail_company)
    jid = queue.enqueue(market, 'identity', proposal['id'])
    queue.execute(market, queue.claim(market))
    monkeypatch.setattr(queue, '_enqueue_in_transaction', original_enqueue)
    with market.db() as db:
        job = dict(db.execute('SELECT * FROM collection_jobs WHERE id=?', (jid,)).fetchone())
    assert job['status'] == 'failed' and market.count('companies') == 1
    assert identity.runs(market)[0]['status'] == 'succeeded'
    return market, proposal, job


def test_fresh_identity_sources_cannot_approve_a_stale_legacy_lookup_input(market):
    proposal = proposals(market)[0]
    # The historical lookup field is an input, not an alias display field.
    # Even exact fresh source names must not erase a changed lookup history.
    with market.db() as db:
        db.execute('UPDATE sponsor_resolution_runs SET query=?', (proposal['alias'],))
    documents = {identity.DIRECTORY_URLS[0]: (
        'Symbol|Security Name|Test Issue|ETF\n' + proposal['ticker'] + '|'
        + proposal['alias'] + ' - Common Stock|N|N\n').encode(),
        identity.NCT_API + proposal['nct']: json.dumps({'protocolSection': {
            'identificationModule': {'nctId': proposal['nct']},
            'sponsorCollaboratorsModule': {'leadSponsor': {'name': proposal['alias']}}}}).encode()}
    result = identity.collect(market, proposal['id'], fetch=documents.__getitem__)
    assert result['status'] == 'failed' and 'Lookup input changed' in result['error']
    assert market.count('companies') == 0
    with market.db() as db:
        assert db.execute('SELECT status FROM sponsor_proposals WHERE id=?', (proposal['id'],)).fetchone()[0] == 'pending'
        assert db.execute('SELECT count(*) FROM sponsor_proposal_reviews').fetchone()[0] == 0


def later(monkeypatch):
    current = time.time()
    monkeypatch.setattr(queue.time, 'time', lambda: current + 25 * 3600)


def test_publication_handoff_failure_cooldown_then_automatic_repair_closes_loop(failed_handoff, monkeypatch):
    market, proposal, failed = failed_handoff
    assert queue.enqueue_identity_repairs(market) == []
    later(monkeypatch)
    repaired = queue.enqueue_identity_repairs(market)
    assert len(repaired) == 1
    assert queue.enqueue_identity_repairs(market) == []
    # The collector must reuse its verified proof, not issue new source requests.
    monkeypatch.setattr(identity, 'collect', lambda m, pid, **kwargs:
                        collect_identity(m, pid, fetch=lambda url: pytest.fail('No source fetch during proof reuse'), **kwargs))
    queue.execute(market, queue.claim(market))
    with market.db() as db:
        assert db.execute('SELECT status FROM collection_jobs WHERE id=?', (repaired[0],)).fetchone()[0] == 'succeeded'
        company = db.execute("SELECT * FROM collection_jobs WHERE kind='company'").fetchall()
        assert len(company) == 1 and company[0]['reference'] == proposal['candidate_id']
        assert company[0]['status'] == 'queued'
        assert db.execute('SELECT count(*) FROM sponsor_proposal_reviews').fetchone()[0] == 1
    assert len(identity.runs(market)) == 1
    assert queue.enqueue_identity_repairs(market) == []


def test_explicit_cancelled_identity_is_not_automatically_repaired(failed_handoff, monkeypatch):
    market, proposal, failed = failed_handoff
    jid = queue.enqueue(market, 'identity', proposal['id'])
    queue.cancel(market, jid)
    later(monkeypatch)
    assert queue.enqueue_identity_repairs(market) == []


@pytest.mark.parametrize('status', ['queued', 'failed', 'cancelled', 'succeeded'])
def test_existing_company_task_blocks_identity_repair(failed_handoff, monkeypatch, status):
    market, proposal, failed = failed_handoff
    jid = queue.enqueue(market, 'company', proposal['candidate_id'])
    with market.db() as db:
        db.execute('UPDATE collection_jobs SET status=? WHERE id=?', (status, jid))
    later(monkeypatch)
    assert queue.enqueue_identity_repairs(market) == []


def test_existing_registry_run_blocks_identity_repair(failed_handoff, monkeypatch):
    market, proposal, failed = failed_handoff
    with market.db() as db:
        db.execute('INSERT INTO runs VALUES (?,?,?,?,?,?,?,?)',
                   ('synthetic-registry', 'ClinicalTrials.gov', proposal['candidate_id'], stamp(time.time()),
                    stamp(time.time()), 'failed', 0, 'Synthetic follow-up was attempted'))
    later(monkeypatch)
    assert queue.enqueue_identity_repairs(market) == []


def test_tampered_or_missing_archived_proof_cannot_trigger_repair(failed_handoff, monkeypatch):
    market, proposal, failed = failed_handoff
    run = identity.runs(market)[0]
    (market.root / 'identity_review' / run['id'] / 'registry.json').write_bytes(b'tampered')
    later(monkeypatch)
    assert queue.enqueue_identity_repairs(market) == []


def test_unknown_failed_job_time_cannot_trigger_repair(failed_handoff, monkeypatch):
    market, proposal, failed = failed_handoff
    with market.db() as db:
        db.execute("UPDATE collection_jobs SET updated='unknown' WHERE id=?", (failed['id'],))
    later(monkeypatch)
    assert queue.enqueue_identity_repairs(market) == []


def test_failed_repair_gets_another_cooldown_instead_of_immediate_loop(failed_handoff, monkeypatch):
    market, proposal, failed = failed_handoff
    base = time.time()
    monkeypatch.setattr(queue.time, 'time', lambda: base + 25 * 3600)
    first_repair = queue.enqueue_identity_repairs(market)
    assert len(first_repair) == 1
    original = queue._enqueue_in_transaction
    def fail_company(db, kind, reference):
        if kind == 'company':
            raise OSError('Synthetic repeated handoff storage failure')
        return original(db, kind, reference)
    monkeypatch.setattr(queue, '_enqueue_in_transaction', fail_company)
    # Advance the wall-clock string as well as the scheduler clock for this
    # second synthetic failure, without a real 25-hour wait.
    monkeypatch.setattr(queue, 'now', lambda: stamp(base + 25 * 3600))
    queue.execute(market, queue.claim(market))
    monkeypatch.setattr(queue, '_enqueue_in_transaction', original)
    with market.db() as db:
        row = db.execute('SELECT status,updated FROM collection_jobs WHERE id=?', (first_repair[0],)).fetchone()
        assert row['status'] == 'failed' and row['updated'] == stamp(base + 25 * 3600)
    assert queue.enqueue_identity_repairs(market) == []
    monkeypatch.setattr(queue.time, 'time', lambda: base + 50 * 3600)
    assert len(queue.enqueue_identity_repairs(market)) == 1


def test_changed_candidate_prevents_repair_even_with_intact_old_proof(failed_handoff, monkeypatch):
    market, proposal, failed = failed_handoff
    with market.db() as db:
        db.execute("UPDATE issuer_candidates SET content_hash='new-version'")
    later(monkeypatch)
    assert queue.enqueue_identity_repairs(market) == []


def test_oldest_due_handoff_repair_precedes_newer_failure(market, monkeypatch):
    rows = proposals(market, 2)
    directory = 'Symbol|Security Name|Test Issue|ETF\n' + ''.join(
        row['ticker'] + '|' + row['alias'] + ' - Common Stock|N|N\n' for row in rows)
    documents = {identity.DIRECTORY_URLS[0]: directory.encode()}
    for row in rows:
        documents[identity.NCT_API + row['nct']] = json.dumps({'protocolSection': {
            'identificationModule': {'nctId': row['nct']},
            'sponsorCollaboratorsModule': {'leadSponsor': {'name': row['alias']}}}}).encode()
    monkeypatch.setattr(identity, 'collect', lambda m, pid, **kw:
                        collect_identity(m, pid, fetch=documents.__getitem__, **kw))
    original = queue._enqueue_in_transaction
    def fail_company(db, kind, reference):
        if kind == 'company':
            raise OSError('Synthetic handoff failure')
        return original(db, kind, reference)
    monkeypatch.setattr(queue, '_enqueue_in_transaction', fail_company)
    for row in rows:
        queue.enqueue(market, 'identity', row['id'])
        queue.execute(market, queue.claim(market))
    monkeypatch.setattr(queue, '_enqueue_in_transaction', original)
    base = time.time()
    with market.db() as db:
        for index, row in enumerate(rows, 1):
            db.execute("UPDATE collection_jobs SET updated=? WHERE kind='identity' AND reference=?",
                       (stamp(base + index * 60), row['id']))
    monkeypatch.setattr(queue.time, 'time', lambda: base + 26 * 3600)
    first = queue.enqueue_identity_repairs(market, limit=1)
    assert len(first) == 1
    with market.db() as db:
        assert db.execute('SELECT reference FROM collection_jobs WHERE id=?', (first[0],)).fetchone()[0] == rows[0]['id']
    second = queue.enqueue_identity_repairs(market, limit=1)
    assert len(second) == 1
    with market.db() as db:
        assert db.execute('SELECT reference FROM collection_jobs WHERE id=?', (second[0],)).fetchone()[0] == rows[1]['id']


def test_batch_limit_supports_full_1000_item_traversal_bound(market):
    assert queue.enqueue_identity(market, limit=1000) == []
    assert queue.enqueue_identity_repairs(market, limit=1000) == []
    with pytest.raises(ValueError):
        queue.enqueue_identity_repairs(market, limit=1001)
