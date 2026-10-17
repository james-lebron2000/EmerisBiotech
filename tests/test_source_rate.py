"""Deterministic source scheduling tests; no HTTP or long real sleeps."""
import concurrent.futures
import multiprocessing
import sqlite3
import threading
from pathlib import Path

import pytest

from vbt.source_rate import DATABASE_NAME, INTERVALS, MAX_WAIT, wait_slot


CTG = 'https://clinicaltrials.gov/api/v2/studies/NCT00000001'
NASDAQ = 'https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt'
BSE = 'https://www.bse.cn/nqxxController/nqxxCnzq.do'


class Clock:
    def __init__(self, current=1000.0):
        self.current, self.sleeps = current, []
    def __call__(self):
        return self.current
    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.current += seconds


def state_row(path, host='clinicaltrials.gov'):
    with sqlite3.connect(Path(path) / DATABASE_NAME) as db:
        return db.execute('SELECT next_at,last_reserved,interval_seconds FROM source_request_slots WHERE host=?', (host,)).fetchone()


def reserve_and_cancel(state, url=CTG):
    # Before reservation and in the transaction: false. After commit: true.
    calls = [0]
    def cancelled():
        calls[0] += 1
        return calls[0] >= 3
    try:
        wait_slot(state, url, cancelled=cancelled, clock=lambda: 1000.0, sleep=lambda _: pytest.fail('Cancelled before waiting'))
    except InterruptedError:
        return 'cancelled'
    return 'unexpected'


def _subprocess_reserve(state):
    # Top-level function is picklable under macOS multiprocessing spawn.
    count = 0
    def cancelled():
        nonlocal count
        count += 1
        return count >= 3
    try:
        wait_slot(state, CTG, cancelled=cancelled, clock=lambda: 1000.0, sleep=lambda _: None)
    except InterruptedError:
        return
    raise AssertionError('Expected cancellation after reservation')


@pytest.mark.parametrize('url,host,interval', [
    (CTG, 'clinicaltrials.gov', 3.0), (NASDAQ, 'www.nasdaqtrader.com', 2.0),
    (BSE, 'www.bse.cn', 0.5), ('https://www.hengrui.com/en/pipeline.html', 'www.hengrui.com', 1.0),
])
def test_first_slot_is_immediate_second_waits_platform_policy(tmp_path, url, host, interval):
    clock = Clock()
    assert wait_slot(tmp_path, url, clock=clock, sleep=clock.sleep) is None
    assert clock.sleeps == [] and state_row(tmp_path, host) == (1000.0 + interval, 1000.0, interval)
    assert wait_slot(tmp_path, url, clock=clock, sleep=clock.sleep) is None
    assert sum(clock.sleeps) == pytest.approx(interval)
    assert all(0 < step <= 0.1 for step in clock.sleeps)
    assert state_row(tmp_path, host) == (1000.0 + interval * 2, 1000.0 + interval, interval)


def test_distinct_hosts_do_not_share_debt(tmp_path):
    clock = Clock()
    wait_slot(tmp_path, CTG, clock=clock, sleep=clock.sleep)
    wait_slot(tmp_path, NASDAQ, clock=clock, sleep=clock.sleep)
    assert clock.sleeps == []
    assert state_row(tmp_path)[0] == 1003 and state_row(tmp_path, 'www.nasdaqtrader.com')[0] == 1002


def test_reservations_survive_fresh_connections_and_cancel_does_not_reclaim(tmp_path):
    assert reserve_and_cancel(tmp_path) == 'cancelled'
    assert state_row(tmp_path)[0] == 1003
    assert reserve_and_cancel(tmp_path) == 'cancelled'
    assert state_row(tmp_path)[0] == 1006
    clock = Clock()
    wait_slot(tmp_path, CTG, clock=clock, sleep=clock.sleep)
    assert sum(clock.sleeps) == pytest.approx(6.0)


def test_cancel_before_reservation_creates_no_database(tmp_path):
    with pytest.raises(InterruptedError):
        wait_slot(tmp_path, CTG, cancelled=lambda: True)
    assert not (tmp_path / DATABASE_NAME).exists()


def test_cancel_inside_transaction_does_not_consume_slot(tmp_path):
    counter = [0]
    def cancelled():
        counter[0] += 1
        return counter[0] >= 2
    with pytest.raises(InterruptedError):
        wait_slot(tmp_path, CTG, cancelled=cancelled, clock=lambda: 1000.0)
    assert state_row(tmp_path) is None


def test_cancellation_during_wait_is_checked_in_small_steps(tmp_path):
    clock = Clock()
    wait_slot(tmp_path, CTG, clock=clock, sleep=clock.sleep)
    with pytest.raises(InterruptedError):
        wait_slot(tmp_path, CTG, cancelled=lambda: bool(clock.sleeps), clock=clock, sleep=clock.sleep)
    assert clock.sleeps == [0.1] and state_row(tmp_path)[0] == 1006


def test_wait_does_not_hold_sqlite_write_transaction(tmp_path):
    clock = Clock()
    wait_slot(tmp_path, CTG, clock=clock, sleep=clock.sleep)
    observed = []
    def sleep(seconds):
        # A separate connection can write while the first caller waits.
        with sqlite3.connect(tmp_path / DATABASE_NAME, timeout=0.1) as db:
            db.execute('BEGIN IMMEDIATE')
            observed.append(db.execute('SELECT count(*) FROM source_request_slots').fetchone()[0])
        clock.sleep(seconds)
    wait_slot(tmp_path, CTG, clock=clock, sleep=sleep)
    assert observed and all(count == 1 for count in observed)


def test_concurrent_connections_reserve_unique_serial_slots(tmp_path):
    # Same fake instant across worker threads, cancellation after each commit.
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: reserve_and_cancel(tmp_path), range(8)))
    assert results == ['cancelled'] * 8
    assert state_row(tmp_path) == (1024.0, 1021.0, 3.0)


def test_separate_processes_share_one_reservation_database(tmp_path):
    context = multiprocessing.get_context('spawn')
    children = [context.Process(target=_subprocess_reserve, args=(str(tmp_path),)) for _ in range(3)]
    for child in children: child.start()
    for child in children:
        child.join(timeout=10)
        if child.is_alive():
            child.terminate();child.join(timeout=2)
            pytest.fail('Synthetic reservation process did not terminate')
        assert child.exitcode == 0
    assert state_row(tmp_path) == (1009.0, 1006.0, 3.0)


def test_debt_over_60_seconds_is_rejected_without_extending_it(tmp_path):
    clock = Clock()
    wait_slot(tmp_path, CTG, clock=clock, sleep=clock.sleep)
    with sqlite3.connect(tmp_path / DATABASE_NAME) as db:
        db.execute('UPDATE source_request_slots SET next_at=1060.001')
    with pytest.raises(TimeoutError, match='debt'):
        wait_slot(tmp_path, CTG, clock=clock, sleep=lambda _: pytest.fail('Debt must fail before waiting'))
    assert state_row(tmp_path)[0] == 1060.001


def test_exact_60_second_debt_is_bounded_and_allowed(tmp_path):
    clock = Clock()
    wait_slot(tmp_path, CTG, clock=clock, sleep=clock.sleep)
    with sqlite3.connect(tmp_path / DATABASE_NAME) as db:
        db.execute('UPDATE source_request_slots SET next_at=1060.0')
    wait_slot(tmp_path, CTG, clock=clock, sleep=clock.sleep)
    assert sum(clock.sleeps) == pytest.approx(60.0)
    assert state_row(tmp_path)[0] == 1063.0


def test_frozen_clock_fails_closed_instead_of_waiting_forever(tmp_path):
    wait_slot(tmp_path, CTG, clock=lambda: 1000.0, sleep=lambda _: None)
    calls = []
    with pytest.raises(TimeoutError, match='wait exceeded'):
        wait_slot(tmp_path, CTG, clock=lambda: 1000.0, sleep=calls.append)
    assert sum(calls) == pytest.approx(60.0) and len(calls) <= 601


def test_backwards_clock_fails_closed(tmp_path):
    clock = Clock()
    wait_slot(tmp_path, CTG, clock=clock, sleep=clock.sleep)
    def backwards(seconds): clock.current -= 1
    with pytest.raises(TimeoutError, match='backwards'):
        wait_slot(tmp_path, CTG, clock=clock, sleep=backwards)


@pytest.mark.parametrize('value', [float('nan'), float('inf'), -1, True, '1000'])
def test_invalid_clock_is_rejected_without_reservation(tmp_path, value):
    with pytest.raises(ValueError, match='clock'):
        wait_slot(tmp_path, CTG, clock=lambda: value)
    assert state_row(tmp_path) is None


def test_persisted_invalid_debt_is_not_treated_as_zero(tmp_path):
    wait_slot(tmp_path, CTG, clock=lambda: 1000.0)
    with sqlite3.connect(tmp_path / DATABASE_NAME) as db:
        db.execute("UPDATE source_request_slots SET next_at='invalid'")
    with pytest.raises(ValueError, match='persisted debt'):
        wait_slot(tmp_path, CTG, clock=lambda: 1000.0)


@pytest.mark.parametrize('url', [
    'http://clinicaltrials.gov/api/v2/studies', 'https://clinicaltrials.gov.evil.example/',
    'https://clinicaltrials.gov:8443/', 'https://user:password@clinicaltrials.gov/',
    'https://@clinicaltrials.gov/', 'https://clinicaltrials.gov/#fragment', 'https://127.0.0.1/', 'file:///tmp/source',
    'https://www.nasdaqtrader.com.evil.example/', ' https://clinicaltrials.gov/',
    'https://clinicaltrials.gov/\nwrong', 'https://clinicaltrials.gov:invalid/', '', None,
])
def test_bad_url_fails_before_filesystem_or_sleep(tmp_path, url):
    state = tmp_path / 'must-not-be-created'
    with pytest.raises(ValueError):
        wait_slot(state, url, sleep=lambda _: pytest.fail('Rejected URL slept'))
    assert not state.exists()


def test_local_state_resolves_symlink_and_blocks_external_volume(tmp_path, monkeypatch):
    original = Path.resolve
    target = tmp_path / 'state-alias'
    def resolve(path, *args, **kwargs):
        if path == target: return Path('/Volumes/External/source-rate')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'resolve', resolve)
    with pytest.raises(ValueError, match='local disk'):
        wait_slot(target, CTG, clock=lambda: 1000.0)
    assert not target.exists()


def test_direct_external_volume_state_is_rejected_before_creation():
    with pytest.raises(ValueError, match='local disk'):
        wait_slot('/Volumes/NotAMountedVolume/source-rate-fixture', CTG)


def test_database_file_symlink_cannot_escape_local_state(tmp_path):
    root, elsewhere = tmp_path / 'state', tmp_path / 'other.sqlite3'
    root.mkdir();elsewhere.write_text('protected')
    (root / DATABASE_NAME).symlink_to(elsewhere)
    with pytest.raises(ValueError, match='database'):
        wait_slot(root, CTG)
    assert elsewhere.read_text() == 'protected'


def test_expired_debt_resets_to_current_time_without_wait(tmp_path):
    clock = Clock()
    wait_slot(tmp_path, CTG, clock=clock, sleep=clock.sleep)
    clock.current = 2000.0
    wait_slot(tmp_path, CTG, clock=clock, sleep=clock.sleep)
    assert clock.sleeps == [] and state_row(tmp_path) == (2003.0, 2000.0, 3.0)
