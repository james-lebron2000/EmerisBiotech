"""Connection lifecycle checks, including the service's small launchd FD budget."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import textwrap

import pytest

from vbt.market import Market
from vbt.sqlite_connections import ClosingConnection
from vbt.workbench import Workbench


def connection(path):
    return sqlite3.connect(path, factory=ClosingConnection)


def test_success_commits_then_closes_and_materialized_rows_survive(tmp_path):
    path = tmp_path / 'transaction.sqlite3'
    with connection(path) as db:
        db.row_factory = sqlite3.Row
        db.execute('CREATE TABLE values_seen (value TEXT)')
        db.execute('INSERT INTO values_seen VALUES (?)', ('saved',))
        row = db.execute('SELECT value FROM values_seen').fetchone()
    assert row['value'] == 'saved'
    with pytest.raises(sqlite3.ProgrammingError, match='closed'):
        db.execute('SELECT 1')
    with connection(path) as check:
        assert check.execute('SELECT value FROM values_seen').fetchall() == [('saved',)]


@pytest.mark.parametrize('error', [ValueError, KeyboardInterrupt])
def test_exception_rolls_back_and_closes_even_for_baseexception(tmp_path, error):
    path = tmp_path / 'rollback.sqlite3'
    with connection(path) as setup:
        setup.execute('CREATE TABLE values_seen (value TEXT)')
    with pytest.raises(error, match='interrupted'):
        with connection(path) as db:
            db.execute('INSERT INTO values_seen VALUES (?)', ('unsaved',))
            raise error('interrupted')
    with pytest.raises(sqlite3.ProgrammingError, match='closed'):
        db.execute('SELECT 1')
    with connection(path) as check:
        assert check.execute('SELECT count(*) FROM values_seen').fetchone()[0] == 0


def test_commit_error_is_not_swallowed_and_connection_still_closes(tmp_path):
    path = tmp_path / 'deferred.sqlite3'
    with connection(path) as setup:
        setup.executescript('''
          CREATE TABLE parent (id INTEGER PRIMARY KEY);
          CREATE TABLE child (parent_id INTEGER REFERENCES parent(id)
            DEFERRABLE INITIALLY DEFERRED);
        ''')
    with pytest.raises(sqlite3.IntegrityError, match='FOREIGN KEY'):
        with connection(path) as db:
            db.execute('PRAGMA foreign_keys=ON')
            db.execute('INSERT INTO child VALUES (42)')
    with pytest.raises(sqlite3.ProgrammingError, match='closed'):
        db.execute('SELECT 1')
    with connection(path) as check:
        assert check.execute('SELECT count(*) FROM child').fetchone()[0] == 0


@pytest.mark.parametrize('store_class,attribute,table', [
    (Market, 'path', 'companies'), (Workbench, 'dbpath', 'entries')])
def test_factories_keep_connection_api_for_explicit_non_context_callers(
        tmp_path, store_class, attribute, table):
    store = store_class(tmp_path / 'workspace', tmp_path / 'state')
    db = store.db()
    try:
        assert isinstance(db, ClosingConnection)
        assert db.row_factory is sqlite3.Row
        # Explicit callers may perform multiple operations before closing.
        db.execute('SELECT * FROM ' + table).fetchall()
        db.commit()
        assert db.execute('SELECT 1 AS value').fetchone()['value'] == 1
    finally:
        db.close()
    assert getattr(store, attribute).is_file()


@pytest.mark.skipif(os.name != 'posix', reason='Unix RLIMIT_NOFILE regression')
def test_repeated_reads_and_real_http_requests_stay_below_fd_budget(tmp_path):
    # Run in an isolated process: lowering this test runner's own FD limit would
    # affect unrelated tests. No scheduler, collection, network fetch or live
    # state is started; HTTP requests target one ephemeral loopback test server.
    script = textwrap.dedent('''
        import gc, json, os, resource, sqlite3, sys, threading, urllib.request
        from pathlib import Path
        from http.server import ThreadingHTTPServer
        from vbt.market import Market
        from vbt.workbench import Workbench, handler
        root = Path(sys.argv[1])
        market = Market(root / 'workspace', root / 'state')
        app = Workbench(root / 'workspace', root / 'state')
        gc.collect()
        gc.disable()
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        resource.setrlimit(resource.RLIMIT_NOFILE,
            (64 if hard == resource.RLIM_INFINITY else min(64, hard), hard))
        fd_root = Path('/proc/self/fd') if Path('/proc/self/fd').is_dir() else Path('/dev/fd')
        before = len(list(fd_root.iterdir()))
        for _ in range(200):
            assert market.rows('companies') == []
            assert market.count('trials') == 0
            assert market.setting('enabled', '0') == '0'
            assert app.rows('entries') == []
        after_reads = len(list(fd_root.iterdir()))
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler(app))
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            for _ in range(80):
                with opener.open(f'http://127.0.0.1:{server.server_port}/notes', timeout=3) as response:
                    assert response.status == 200
                    assert response.read()
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=3)
        after_http = len(list(fd_root.iterdir()))
        assert after_reads <= before + 2, (before, after_reads)
        assert after_http <= before + 2, (before, after_http)
        print(json.dumps(dict(before=before, after_reads=after_reads,
            after_http=after_http, read_cycles=200, http_requests=80)))
    ''')
    env = os.environ.copy()
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    env['PYTHONPATH'] = str(Path(__file__).resolve().parents[1] / 'src')
    # Hosted macOS runners can take longer than 30 seconds for the repeated
    # SQLite opens. This tests descriptor ownership, not throughput: retain
    # every cycle and the strict FD budget while bounding a stalled child.
    result = subprocess.run([sys.executable, '-B', '-c', script, str(tmp_path)],
                            capture_output=True, text=True, env=env, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report['read_cycles'] == 200 and report['http_requests'] == 80
