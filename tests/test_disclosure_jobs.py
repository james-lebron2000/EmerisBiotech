"""Durable official-disclosure queue integration; all captures are synthetic."""
import functools
import os

import pytest

from vbt.market import Market
from vbt import disclosures
from vbt import market_jobs as jobs


def synthetic_chart():
    parts=['<h4>Clinical Development Pipeline</h4><p>*By Jul,2026</p>']
    for name,grid in [('dataIII','grid-iii'),('dataNDA','grid-nda'),('dataII','grid-ii'),('dataI','grid-i')]:
        parts.append(f"const {name} = [[{{name:'SYNTHETIC',target:'TEST ADC',indications:['SYNTHETIC indication'],type:'tumor'}}]];renderGrid('{grid}', {name});")
    return (parts[0] + '<script>' + ''.join(parts[1:]) + '</script>').encode()


@pytest.fixture
def market(tmp_path):
    return Market(tmp_path/'workspace', tmp_path/'state')


def execute_with_fetch(market,monkeypatch,source,fetch):
    actual=disclosures.ingest
    monkeypatch.setattr(disclosures,'ingest',functools.partial(actual,fetch=fetch))
    jid=jobs.enqueue(market,'disclosure',source)
    jobs.execute(market,jobs.claim(market))
    return next(r for r in jobs.jobs(market) if r['id']==jid)


def test_fixed_sources_validation_deduplication_and_company_batch_unchanged(market):
    first=jobs.enqueue(market,'disclosure','hengrui_pipeline')
    assert jobs.enqueue(market,'disclosure','hengrui_pipeline')==first
    with pytest.raises(ValueError):jobs.enqueue(market,'disclosure','https://example.com/')
    with pytest.raises(ValueError):jobs.enqueue(market,'disclosure','unknown')
    assert jobs.enqueue_all(market)==[]
    queued=jobs.enqueue_disclosures(market)
    assert len(queued)==len(disclosures.SOURCES) and first in queued
    assert len(jobs.jobs(market))==len(disclosures.SOURCES)
    restored=Market(market.root.parent.parent,market.state)
    assert len(jobs.enqueue_disclosures(restored))==len(disclosures.SOURCES)
    assert len(jobs.jobs(restored))==len(disclosures.SOURCES)


def test_successful_disclosure_uses_capture_namespace_and_binds_on_start(market,monkeypatch):
    seen=[]
    def fetch(url):
        job=jobs.jobs(market)[0]
        assert job['status']=='running' and job['run_id']
        seen.append(job['run_id'])
        with market.db() as db:
            assert db.execute('SELECT status FROM disclosure_captures WHERE id=?',(job['run_id'],)).fetchone()[0]=='running'
        return synthetic_chart()
    result=execute_with_fetch(market,monkeypatch,'hengrui_pipeline',fetch)
    assert result['status']=='succeeded' and result['run_id']==seen[0]
    assert disclosures.captures(market)[0]['count']==4
    assert len(disclosures.observations(market))==4
    assert market.rows('runs')==[] and market.count('trials')==0


def test_parse_blocked_is_terminal_meaningful_and_archive_retained(market,monkeypatch):
    result=execute_with_fetch(market,monkeypatch,'innovent_interim_pipeline_2025',lambda url:b'%PDF-1.7 synthetic')
    assert result['status']=='parse_blocked' and result['run_id']
    assert result['attempts']==1 and 'layout-aware' in result['error']
    capture=disclosures.captures(market)[0]
    assert (market.root/capture['archive_path']).read_bytes().startswith(b'%PDF')
    assert jobs.claim(market) is None and not disclosures.observations(market)


def test_nontransient_fetch_failure_does_not_claim_success_or_retry(market,monkeypatch):
    def failure(url):raise OSError('HTTP 403 access denied')
    result=execute_with_fetch(market,monkeypatch,'pfizer_pipeline',failure)
    assert result['status']=='fetch_failed' and '403' in result['error']
    assert disclosures.captures(market)[0]['status']=='fetch_failed'
    assert jobs.claim(market) is None


def test_transient_disclosure_retry_bounded_and_attempt_capture_ids_change(market,monkeypatch):
    actual=disclosures.ingest
    def failure(url):raise OSError('Connection timed out')
    monkeypatch.setattr(disclosures,'ingest',functools.partial(actual,fetch=failure))
    jid=jobs.enqueue(market,'disclosure','pfizer_pipeline')
    seen=[]
    for attempt in range(1,4):
        job=jobs.claim(market)
        assert job['attempts']==attempt
        jobs.execute(market,job)
        result=jobs.jobs(market)[0];seen.append(result['run_id'])
        assert result['status']==('retry_wait' if attempt<3 else 'fetch_failed')
        with market.db() as db:db.execute('UPDATE collection_jobs SET next_at=0 WHERE id=?',(jid,))
    assert len(set(seen))==3 and len(disclosures.captures(market))==3
    assert jobs.claim(market) is None


def test_busy_disclosure_waits_without_querying_generic_runs(market):
    assert market.claim('disclosure:hengrui_pipeline')
    jobs.enqueue(market,'disclosure','hengrui_pipeline');jobs.execute(market,jobs.claim(market))
    result=jobs.jobs(market)[0]
    assert result['status']=='retry_wait' and result['run_id'] is None
    assert 'leased' in result['error'] and market.rows('runs')==[]


def test_cancelled_queue_does_not_fetch_or_create_capture(market,monkeypatch):
    monkeypatch.setattr(disclosures,'ingest',lambda *args,**kwargs:pytest.fail('Cancelled job must not collect'))
    jid=jobs.enqueue(market,'disclosure','hengrui_pipeline');job=jobs.claim(market)
    jobs.cancel(market,jid);jobs.execute(market,job)
    assert jobs.jobs(market)[0]['status']=='cancelled'
    assert disclosures.captures(market)==[]


def test_disclosure_freshness_separate_from_ctg_runs(market):
    disclosures.ingest(market,'hengrui_pipeline',fetch=lambda url:synthetic_chart())
    assert len(jobs.enqueue_disclosures(market,only_due=True))==2
    assert {r['reference'] for r in jobs.jobs(market)}=={'pfizer_pipeline','innovent_interim_pipeline_2025'}
    with market.db() as db:db.execute("UPDATE disclosure_captures SET started='2000-01-01T00:00:00+00:00'")
    assert len(jobs.enqueue_disclosures(market,only_due=True))==3
    assert len(jobs.jobs(market))==3


def test_recover_disclosure_interrupts_capture_releases_only_proven_owner(market):
    result=disclosures.ingest(market,'hengrui_pipeline',fetch=lambda url:synthetic_chart())
    jid=jobs.enqueue(market,'disclosure','hengrui_pipeline');jobs.claim(market)
    with market.db() as db:
        capture=db.execute('SELECT * FROM disclosure_captures WHERE id=?',(result['id'],)).fetchone()
        assert capture['lease_owner']
        db.execute("UPDATE disclosure_captures SET status='running' WHERE id=?",(result['id'],))
        db.execute('INSERT OR REPLACE INTO leases(key,until,owner) VALUES (?,?,?)',('disclosure:hengrui_pipeline',10**12,capture['lease_owner']))
        db.execute('UPDATE collection_jobs SET run_id=?,owner_pid=99999999,heartbeat=0 WHERE id=?',(result['id'],jid))
        # Identical text IDs in unrelated runs must not be interpreted as capture runs.
        db.execute("INSERT INTO runs VALUES (?, 'ClinicalTrials.gov','US:SYNTHETIC','2000',NULL,'running',0,'')",(result['id'],))
    jobs.recover(market)
    with market.db() as db:
        assert db.execute('SELECT status FROM disclosure_captures WHERE id=?',(result['id'],)).fetchone()[0]=='interrupted'
        assert db.execute('SELECT status FROM runs WHERE id=?',(result['id'],)).fetchone()[0]=='running'
        assert db.execute("SELECT 1 FROM leases WHERE key='disclosure:hengrui_pipeline'").fetchone() is None
    assert jobs.jobs(market)[0]['status']=='queued'


def test_recovery_keeps_newer_disclosure_collector_lease(market):
    result=disclosures.ingest(market,'hengrui_pipeline',fetch=lambda url:synthetic_chart())
    jid=jobs.enqueue(market,'disclosure','hengrui_pipeline');jobs.claim(market)
    with market.db() as db:
        assert 'lease_owner' in {r[1] for r in db.execute('PRAGMA table_info(disclosure_captures)')}
        db.execute("UPDATE disclosure_captures SET status='running' WHERE id=?",(result['id'],))
        db.execute("INSERT INTO leases(key,until,owner) VALUES ('disclosure:hengrui_pipeline',?,'new-owner')",(10**12,))
        db.execute('UPDATE collection_jobs SET run_id=?,owner_pid=99999999,heartbeat=0 WHERE id=?',(result['id'],jid))
    jobs.recover(market)
    with market.db() as db:
        assert db.execute("SELECT owner FROM leases WHERE key='disclosure:hengrui_pipeline'").fetchone()[0]=='new-owner'
        assert db.execute('SELECT status FROM disclosure_captures WHERE id=?',(result['id'],)).fetchone()[0]=='interrupted'


def test_recover_disclosure_without_capture_owner_fails_closed(market):
    result=disclosures.ingest(market,'hengrui_pipeline',fetch=lambda url:synthetic_chart())
    jid=jobs.enqueue(market,'disclosure','hengrui_pipeline');jobs.claim(market)
    with market.db() as db:
        db.execute("UPDATE disclosure_captures SET status='running',lease_owner=NULL WHERE id=?",(result['id'],))
        db.execute("INSERT INTO leases(key,until,owner) VALUES ('disclosure:hengrui_pipeline',?,'unknown-owner')",(10**12,))
        db.execute('UPDATE collection_jobs SET run_id=?,owner_pid=99999999,heartbeat=0 WHERE id=?',(result['id'],jid))
    jobs.recover(market)
    with market.db() as db:
        assert db.execute("SELECT owner FROM leases WHERE key='disclosure:hengrui_pipeline'").fetchone()[0]=='unknown-owner'


def test_live_disclosure_worker_not_interrupted(market):
    jid=jobs.enqueue(market,'disclosure','hengrui_pipeline');jobs.claim(market)
    jobs.recover(market)
    assert jobs.jobs(market)[0]['status']=='running'


def test_cancellation_during_disclosure_fetch_keeps_archived_evidence(market,monkeypatch):
    jid=jobs.enqueue(market,'disclosure','hengrui_pipeline')
    actual=disclosures.ingest
    def fetch(url):
        jobs.cancel(market,jid)
        return synthetic_chart()
    monkeypatch.setattr(disclosures,'ingest',functools.partial(actual,fetch=fetch))
    jobs.execute(market,jobs.claim(market))
    assert jobs.jobs(market)[0]['status']=='cancelled'
    capture=disclosures.captures(market)[0]
    assert capture['status']=='cancelled' and capture['sha256']
    assert not disclosures.observations(market)


def test_stale_disclosure_worker_cannot_overwrite_recovered_success(market,monkeypatch):
    jid=jobs.enqueue(market,'disclosure','hengrui_pipeline')
    old_job=jobs.claim(market)
    actual=disclosures.ingest
    collected=[]
    nested=[False]
    def fetch(url):
        if not nested[0]:
            nested[0]=True
            old_capture=jobs.jobs(market)[0]['run_id']
            with market.db() as db:db.execute('UPDATE collection_jobs SET heartbeat=0 WHERE id=?',(jid,))
            jobs.recover(market)
            with market.db() as db:
                assert db.execute('SELECT status FROM disclosure_captures WHERE id=?',(old_capture,)).fetchone()[0]=='interrupted'
            replacement=jobs.claim(market)
            assert replacement['attempts']==2
            jobs.execute(market,replacement)
            collected.append(jobs.jobs(market)[0]['run_id'])
            return synthetic_chart().replace(b'SYNTHETIC',b'STALE')
        return synthetic_chart().replace(b'SYNTHETIC',b'CURRENT')
    monkeypatch.setattr(disclosures,'ingest',functools.partial(actual,fetch=fetch))
    jobs.execute(market,old_job)
    result=jobs.jobs(market)[0]
    assert result['status']=='succeeded' and result['attempts']==2
    assert result['run_id']==collected[0]
    assert {row['drug_name'] for row in disclosures.observations(market)}=={'CURRENT'}
    assert {row['status'] for row in disclosures.captures(market)}=={'succeeded','interrupted'}
    with market.db() as db:
        assert db.execute("SELECT count(*) FROM leases WHERE key='disclosure:hengrui_pipeline'").fetchone()[0]==0
