"""Synthetic offline-source recovery; no real service, source requests or data."""
import os

import pytest

from vbt import disclosures, market_jobs as jobs, official_disclosure_intake as intake
from vbt.market import Market
from test_official_disclosure_intake import prepared  # shared explicit synthetic evidence fixture


class AbruptWorkerStop(BaseException):
    """A process-death fixture which does not run collector lease cleanup."""


def one_job(market, jid):
    with market.db() as db:
        return dict(db.execute('SELECT * FROM collection_jobs WHERE id=?', (jid,)).fetchone())


def proof_rows(market):
    with market.db() as db:
        return [dict(row) for row in db.execute('SELECT * FROM collection_source_leases ORDER BY attempt,run_id')]


def lease(market, source_id):
    with market.db() as db:
        row = db.execute('SELECT * FROM leases WHERE key=?', ('disclosure:' + source_id,)).fetchone()
        return dict(row) if row else None


def expire_heartbeat(market, jid):
    # Keep the real PID so the persisted proof must match this exact attempt.
    # An expired heartbeat is also an existing supported recovery condition.
    with market.db() as db:
        db.execute('UPDATE collection_jobs SET heartbeat=0 WHERE id=?', (jid,))


def crash_verification(market, source_id, monkeypatch):
    published = intake.ingest_offline(market, source_id)
    assert published['status'] == 'succeeded', published['error']
    jid = jobs.enqueue(market, 'disclosure', source_id)
    job = jobs.claim(market)
    actual, saved = disclosures.ingest, {}

    def stopped(market, source_id, **kwargs):
        callback = kwargs['on_start']

        def on_start(run_id):
            callback(run_id)  # actual execute() callback, including fenced lease binding
            saved['callback'] = callback
            raise AbruptWorkerStop()

        return actual(market, source_id, **{**kwargs, 'on_start': on_start})

    with monkeypatch.context() as patch:
        patch.setattr(disclosures, 'ingest', stopped)
        # A killed process cannot execute the intake finally block. Keep its
        # actual lease in place rather than manufacturing an arbitrary token.
        patch.setattr(market, 'release', lambda *args, **kwargs: None)
        with pytest.raises(AbruptWorkerStop):
            jobs.execute(market, job)
    expire_heartbeat(market, jid)
    return published, job, saved['callback']


def test_real_idempotent_on_start_recovers_lease_after_restart_and_retries(prepared, monkeypatch):
    market, source_id, *_ = prepared
    published, job, _ = crash_verification(market, source_id, monkeypatch)
    current_token = lease(market, source_id)['owner']
    assert current_token != published['lease_owner']
    proof = proof_rows(market)[0]
    assert {key: proof[key] for key in ('job_id', 'attempt', 'owner_pid', 'source_id', 'run_id', 'lease_owner')} == {
        'job_id': job['id'], 'attempt': 1, 'owner_pid': os.getpid(), 'source_id': source_id,
        'run_id': published['id'], 'lease_owner': current_token}

    restored = Market(market.root.parent.parent, market.state)
    jobs.recover(restored)
    assert one_job(restored, job['id'])['status'] == 'queued'
    assert lease(restored, source_id) is None
    retry = jobs.claim(restored)
    assert retry['attempts'] == 2
    jobs.execute(restored, retry)
    result = one_job(restored, job['id'])
    assert result['status'] == 'succeeded' and result['run_id'] == published['id']
    assert lease(restored, source_id) is None
    assert len(proof_rows(restored)) == 2
    assert intake.verify_capture(restored, published['id'])['integrity_status'] == 'verified'
    with restored.db() as db:
        assert db.execute('SELECT count(*) FROM disclosure_captures').fetchone()[0] == 1
        assert db.execute('SELECT count(*) FROM disclosure_observations').fetchone()[0] == 1
        assert db.execute('SELECT lease_owner FROM disclosure_captures').fetchone()[0] == published['lease_owner']
    assert [row['status'] for row in intake.intake_history(restored, source_id)] == ['verified_existing', 'published']


def test_recovery_proof_cannot_delete_new_owner_or_stale_callback_rebind(prepared, monkeypatch):
    market, source_id, *_ = prepared
    published, old_job, callback = crash_verification(market, source_id, monkeypatch)
    with market.db() as db:
        db.execute('UPDATE leases SET until=0 WHERE key=?', ('disclosure:' + source_id,))
    newer = Market(market.root.parent.parent, market.state)
    assert newer.claim('disclosure:' + source_id)
    newer_token = lease(newer, source_id)['owner']
    jobs.recover(market)
    assert lease(newer, source_id)['owner'] == newer_token
    new_job = jobs.claim(newer)
    assert new_job['attempts'] == 2
    assert jobs._bind_started_run(newer, new_job, os.getpid(), published['id'])
    before = proof_rows(newer)
    callback(published['id'])  # retained real callback from attempt one
    assert proof_rows(newer) == before
    assert one_job(newer, old_job['id'])['attempts'] == 2
    assert lease(newer, source_id)['owner'] == newer_token


@pytest.mark.parametrize('field,value', [('attempt', 999), ('owner_pid', 99999999),
                                        ('source_id', 'unrelated_source'), ('run_id', 'f' * 32)])
def test_incomplete_attempt_binding_never_authorizes_recovery_delete(prepared, monkeypatch, field, value):
    market, source_id, *_ = prepared
    published, job, _ = crash_verification(market, source_id, monkeypatch)
    token = lease(market, source_id)['owner']
    with market.db() as db:
        db.execute('UPDATE collection_source_leases SET ' + field + '=?', (value,))
    jobs.recover(market)
    assert one_job(market, job['id'])['status'] == 'queued'
    assert lease(market, source_id)['owner'] == token
    assert intake.verify_capture(market, published['id'])['integrity_status'] == 'verified'


def test_legacy_successful_capture_has_no_authority_over_verification_lease(prepared):
    market, source_id, *_ = prepared
    published = intake.ingest_offline(market, source_id)
    jid = jobs.enqueue(market, 'disclosure', source_id)
    jobs.claim(market)
    # Legacy on_start stored only run_id. Even a coincidentally equal source
    # token is not proof of the current verification worker's ownership.
    with market.db() as db:
        db.execute('UPDATE collection_jobs SET run_id=?,heartbeat=0 WHERE id=?', (published['id'], jid))
        db.execute('INSERT INTO leases VALUES (?,?,?)', ('disclosure:' + source_id, 10 ** 12, published['lease_owner']))
    jobs.recover(market)
    assert one_job(market, jid)['status'] == 'queued'
    assert lease(market, source_id)['owner'] == published['lease_owner']
    assert proof_rows(market) == []
    assert intake.verify_capture(market, published['id'])['integrity_status'] == 'verified'


def test_thread_local_token_without_actual_source_lease_cannot_create_proof(prepared):
    market, source_id, *_ = prepared
    published = intake.ingest_offline(market, source_id)
    jid = jobs.enqueue(market, 'disclosure', source_id)
    job = jobs.claim(market)
    newer = Market(market.root.parent.parent, market.state)
    assert newer.claim('disclosure:' + source_id)
    new_token = lease(newer, source_id)['owner']
    assert market.lease_tokens()['disclosure:' + source_id] != new_token
    assert jobs._bind_started_run(market, job, os.getpid(), published['id'])
    assert proof_rows(market) == []
    expire_heartbeat(market, jid)
    jobs.recover(market)
    assert lease(newer, source_id)['owner'] == new_token


@pytest.mark.parametrize('wrong_source', [False, True])
def test_missing_run_or_wrong_source_cannot_create_recovery_proof(prepared, wrong_source):
    market, source_id, *_ = prepared
    published = intake.ingest_offline(market, source_id)
    if wrong_source:
        with market.db() as db:
            cap = dict(db.execute('SELECT * FROM disclosure_captures WHERE id=?', (published['id'],)).fetchone())
            cap.update(id='a' * 32, source_id='hengrui_pipeline')
            columns = list(cap)
            db.execute('INSERT INTO disclosure_captures (' + ','.join(columns) + ') VALUES ('
                       + ','.join('?' for _ in columns) + ')', [cap[key] for key in columns])
    jid = jobs.enqueue(market, 'disclosure', source_id)
    job = jobs.claim(market)
    assert market.claim('disclosure:' + source_id)
    assert jobs._bind_started_run(market, job, os.getpid(), 'a' * 32)
    assert proof_rows(market) == []
    assert one_job(market, jid)['run_id'] == 'a' * 32


@pytest.mark.parametrize('change', ['attempt', 'owner', 'reference', 'kind'])
def test_started_callback_requires_current_job_owner_attempt_kind_and_reference(prepared, change):
    market, source_id, *_ = prepared
    published = intake.ingest_offline(market, source_id)
    jid = jobs.enqueue(market, 'disclosure', source_id)
    job = jobs.claim(market)
    assert market.claim('disclosure:' + source_id)
    owner = os.getpid()
    if change == 'owner':
        owner += 1
    else:
        job = {**job, {'attempt': 'attempts', 'reference': 'reference', 'kind': 'kind'}[change]:
               job['attempts'] + 1 if change == 'attempt' else 'unrelated'}
    assert not jobs._bind_started_run(market, job, owner, published['id'])
    assert one_job(market, jid)['run_id'] is None
    assert proof_rows(market) == []


def test_cancelled_verification_recovery_releases_only_its_lease_and_retains_publication(prepared, monkeypatch):
    market, source_id, *_ = prepared
    published, job, _ = crash_verification(market, source_id, monkeypatch)
    jobs.cancel(market, job['id'])
    jobs.recover(market)
    assert one_job(market, job['id'])['status'] == 'cancelled'
    assert lease(market, source_id) is None
    assert jobs.claim(market) is None
    assert intake.verify_capture(market, published['id'])['integrity_status'] == 'verified'


def test_recovery_snapshot_does_not_consume_a_changed_run_binding(prepared, monkeypatch):
    market, source_id, *_ = prepared
    _, job, _ = crash_verification(market, source_id, monkeypatch)
    token = lease(market, source_id)['owner']

    def change_before_recovery_write(pid, signal):
        assert jobs._bind_started_run(market, job, os.getpid(), 'b' * 32)

    monkeypatch.setattr(jobs.os, 'kill', change_before_recovery_write)
    jobs.recover(market)
    current = one_job(market, job['id'])
    assert current['status'] == 'running' and current['run_id'] == 'b' * 32
    assert lease(market, source_id)['owner'] == token
