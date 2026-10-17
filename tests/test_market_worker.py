"""Worker lifecycle checks; static config checks are not deployment acceptance."""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import threading

import pytest

from vbt import market_worker


ROOT = Path(__file__).resolve().parents[1]


def test_shutdown_drains_active_work_and_stops_scheduler(monkeypatch):
    stop = threading.Event()
    active = threading.Event()
    finish = threading.Event()
    scheduler_stopped = threading.Event()
    drained = threading.Event()
    failures = []

    class FakeMarket:
        def scheduler(self, event):
            assert event is stop
            event.wait()
            scheduler_stopped.set()

    def run_worker(market, event, workers):
        assert workers == 2

        def in_flight():
            active.set()
            finish.wait(5)
            drained.set()

        worker = threading.Thread(target=in_flight)
        worker.start()
        return [worker]

    monkeypatch.setattr(market_worker, 'run_worker', run_worker)

    def serve():
        try:
            market_worker.serve_worker(FakeMarket(), stop)
        except Exception as exc:
            failures.append(exc)

    service = threading.Thread(target=serve)
    service.start()
    try:
        assert active.wait(2)
        stop.set()
        assert scheduler_stopped.wait(2)
        service.join(.05)
        assert service.is_alive(), 'In-flight work must settle before process exits'
        finish.set()
        service.join(2)
        assert not service.is_alive() and drained.is_set() and not failures
    finally:
        stop.set()
        finish.set()
        service.join(3)


def test_pre_stopped_worker_claims_no_new_jobs(monkeypatch):
    stop = threading.Event()
    stop.set()
    monkeypatch.setattr(market_worker, 'run_worker', lambda *args, **kw: pytest.fail('No jobs after shutdown'))
    market_worker.serve_worker(object(), stop)


def test_main_uses_shared_state_and_handles_sigterm(monkeypatch, tmp_path):
    handlers = {}
    old_handlers = {signal.SIGTERM: object(), signal.SIGINT: object()}
    expected = object()
    calls = []
    monkeypatch.setattr(signal, 'getsignal', old_handlers.__getitem__)
    monkeypatch.setattr(signal, 'signal', lambda signum, handler: handlers.__setitem__(signum, handler))

    def make_market(workspace, state):
        calls.append((workspace, state))
        return expected

    def serve(market, stop, workers):
        assert market is expected and workers == 2 and not stop.is_set()
        handlers[signal.SIGTERM](signal.SIGTERM, None)
        assert stop.is_set()

    monkeypatch.setattr(market_worker, 'Market', make_market)
    monkeypatch.setattr(market_worker, 'serve_worker', serve)
    workspace, state = tmp_path/'workspace', tmp_path/'state'
    market_worker.main(workspace, state)
    assert calls == [(workspace, state)]
    assert handlers == old_handlers, 'Restore host handlers on normal exit'


def test_worker_restores_signal_handlers_after_startup_failure(monkeypatch):
    calls = []
    monkeypatch.setattr(signal, 'getsignal', lambda signum: signal.SIG_DFL)
    monkeypatch.setattr(signal, 'signal', lambda signum, handler: calls.append((signum, handler)))
    monkeypatch.setattr(market_worker, 'Market', lambda *args: (_ for _ in ()).throw(OSError('state unavailable')))
    with pytest.raises(OSError, match='state unavailable'):
        market_worker.main()
    assert calls[-2:] == [(signal.SIGTERM, signal.SIG_DFL), (signal.SIGINT, signal.SIG_DFL)]


def test_private_compose_source_wiring():
    # Guard the checked-in deployment wiring without claiming it has run.
    text = (ROOT/'deploy/cloud/compose.yaml').read_text()
    app = text.split('  app:\n', 1)[1].split('  worker:\n', 1)[0]
    worker = text.split('  worker:\n', 1)[1].split('  gateway:\n', 1)[0]
    gateway = text.split('  gateway:\n', 1)[1].split('\nsecrets:', 1)[0]
    assert 'ports:' not in app and 'networks: [backend]' in app
    assert 'ports:' not in worker and 'networks: [collection_egress]' in worker
    assert 'vbt.market_worker' in worker and 'stop_grace_period: 2m' in worker
    assert 'research:/workspace' in worker and 'state:/state' in worker
    assert 'secrets: [access_config]' in gateway
    assert 'backend:\n    internal: true' in text
    assert 'collection_egress:' in text
    dockerfile = (ROOT/'deploy/cloud/Dockerfile').read_text()
    assert 'curl ca-certificates' in dockerfile and 'USER 10001:10001' in dockerfile


def test_compose_resolved_isolation_when_available():
    docker = shutil.which('docker')
    standalone = shutil.which('docker-compose')
    candidates = ([[docker, 'compose']] if docker else []) + ([[standalone]] if standalone else [])
    command = None
    for candidate in candidates:
        probe = subprocess.run(candidate + ['version'], capture_output=True, text=True, timeout=15)
        if probe.returncode == 0:
            command = candidate
            break
    if not command:
        pytest.skip('Docker Compose is unavailable; resolved configuration remains unverified')
    result = subprocess.run(
        command + ['--file', str(ROOT/'deploy/cloud/compose.yaml'), 'config', '--format', 'json'],
        env={**os.environ, 'DOMAIN': 'research.example.invalid'}, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    config = json.loads(result.stdout)
    app, worker, gateway = (config['services'][name] for name in ('app', 'worker', 'gateway'))
    assert not app.get('ports') and not worker.get('ports')
    assert set(app['networks']) == {'backend'}
    assert set(worker['networks']) == {'collection_egress'}
    assert config['networks']['backend']['internal'] is True
    assert not config['networks']['collection_egress'].get('internal', False)
    assert {item['source'] for item in gateway['secrets']} == {'access_config'}


def test_real_worker_process_stops_on_sigterm_without_collecting(tmp_path):
    """Exercise OS signal handling and the real empty persistent queue locally."""
    import selectors
    import sys

    workspace, state = tmp_path/'workspace', tmp_path/'state'
    script = (
        'from pathlib import Path; from vbt.market_worker import main; '
        f'main(Path({str(workspace)!r}), Path({str(state)!r}))'
    )
    process = subprocess.Popen(
        # The collector uses the standard library only. Skip unrelated editable
        # site hooks, which may point at an unavailable removable research disk.
        [sys.executable, '-S', '-c', script], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, env={**os.environ, 'PYTHONPATH': str(ROOT/'src')},
    )
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            assert selector.select(timeout=10), 'Worker did not report readiness'
            assert 'Market collector ready' in process.stdout.readline()
        process.terminate()
        _, stderr = process.communicate(timeout=5)
        assert process.returncode == 0, stderr
        import sqlite3
        with sqlite3.connect(state/'market.sqlite3') as db:
            assert db.execute('SELECT count(*) FROM collection_jobs').fetchone()[0] == 0
            assert db.execute('SELECT count(*) FROM runs').fetchone()[0] == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
