"""Actual source-only app/SQLite contracts using synthetic records only.

These fixtures do not import historical originals, run providers, start a server
or establish a clinical result. Only package/data paths are substituted.
"""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
from io import BytesIO
import json
from pathlib import Path
import socket
import sqlite3
from types import SimpleNamespace
from urllib.parse import parse_qsl, unquote, urlsplit
import zipfile

from fastapi.testclient import TestClient
import httpx
import pytest

from emeris_live import arena_api, arena_public, continuous_api, dashboard, expanded_api, product
from emeris_live import platform_store
from emeris_live.arena_store import ArenaLedger
from emeris_live.main import create_app
from emeris_live.platform_store import PlatformStore
from emeris_live.readout_updates import ReadoutUpdateStore
from test_arena_core import finish, spec


PLATFORM_TABLES = (
    'platform_prediction_snapshots', 'platform_actual_outcomes',
    'platform_outcome_verifications', 'platform_evaluation_results',
)
CONFIRMATIONS = dict.fromkeys((
    'endpoint_match', 'population_match', 'comparison_match', 'source_checked',
    'prespecified_analysis', 'earliest_result_checked', 'evidence_cutoff_checked',
), True)


@pytest.fixture
def source_app(tmp_path, monkeypatch):
    external_attempts = []

    def forbidden(*args, **kwargs):
        external_attempts.append(True)
        raise AssertionError('Source-only fixture attempted an external connection')

    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', forbidden)
    monkeypatch.setenv('EMERIS_MODE', 'local')
    monkeypatch.setenv('EMERIS_ALLOWED_HOSTS', 'testserver')
    monkeypatch.setenv('EMERIS_ENABLE_ARENA_LLM', 'false')
    monkeypatch.setenv('EMERIS_ENABLE_USER_LLM', 'false')
    root = tmp_path / 'source-only-package'
    root.mkdir()
    for module in (arena_public, arena_api, product, dashboard, continuous_api, expanded_api):
        monkeypatch.setattr(module, 'ROOT', root)
    monkeypatch.setattr(continuous_api, 'DATA', root / 'predome_data/continuous_v4')
    monkeypatch.setattr(expanded_api, 'DATA', root / 'predome_data/historical_v3')
    monkeypatch.setattr(dashboard, 'UPDATES', root / 'dashboard_data/evidence_updates.json')
    app = create_app(tmp_path / 'new-runtime')
    with TestClient(app, raise_server_exceptions=False) as client:
        yield SimpleNamespace(app=app, client=client, store=app.state.store, root=root)
    assert external_attempts == []


def file_state(root):
    result = {}
    for path in root.rglob('*'):
        if path.name.endswith(('-wal', '-shm')):
            continue
        if path.is_symlink():
            result[str(path.relative_to(root))] = ('symlink', str(path.readlink()))
        elif path.is_file():
            result[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
        elif path.is_dir():
            result[str(path.relative_to(root))] = 'directory'
    return result


@contextmanager
def readonly_gets(sample, monkeypatch):
    """Observe actual SQL and byte preservation after every fixture setup."""
    original = sqlite3.connect
    for name in ('workbench.sqlite3', 'arena.sqlite3'):
        path = sample.store.path / name
        if path.is_file() and not path.is_symlink():
            try:
                with original(path) as db:
                    db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            except sqlite3.DatabaseError:
                pass  # Intentionally corrupt input still must stay unchanged.
    before = (file_state(sample.store.path), file_state(sample.root))
    statements, invalid_connections = [], []

    def connect(value, *args, **kwargs):
        parsed = urlsplit(str(value))
        valid = (parsed.scheme == 'file' and not parsed.netloc and not parsed.fragment
                 and parse_qsl(parsed.query) == [('mode', 'ro')] and kwargs.get('uri') is True
                 and Path(unquote(parsed.path)).resolve().is_relative_to(sample.store.path.resolve()))
        if not valid:
            invalid_connections.append(str(value))
        assert valid, 'Public GET attempted a writable or unrelated database connection'
        db = original(value, *args, **kwargs)
        db.set_trace_callback(statements.append)
        return db

    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, 'connect', connect)
        yield statements
    assert (file_state(sample.store.path), file_state(sample.root)) == before
    assert not any(statement.lstrip().split()[0].upper() in (
        'INSERT', 'UPDATE', 'DELETE', 'CREATE', 'ALTER', 'REPLACE', 'DROP', 'VACUUM',
    ) for statement in statements if statement.strip())
    assert invalid_connections == []


def assert_unavailable(sample):
    response = sample.client.get('/api/arena/public')
    assert response.status_code in (500, 503), response.text
    assert response.json() in (
        {'detail': '临床展示记录暂不可用。'},
        {'detail': '操作未完成，数据未确认为成功。请重试。'},
    )
    assert 'targets' not in response.json()


def register_arena(sample):
    ledger = ArenaLedger(sample.store.path)
    target, slots = spec()
    target.update(trial_name='Synthetic source-only trial', scope='prospective_challenge',
                  drug='Synthetic study drug', readout={'label': 'Readout unconfirmed'})
    ids = ledger.register('synthetic-source-only-cohort', [target], slots,
                          {'season_kind': 'registered_12_project_season'})
    finish(ledger, ids[0], .7, returned_model='A-synthetic',
           reasoning='Synthetic evidence is insufficient for calibrated clinical probability.')
    ledger.start(ids[1])
    ledger.finish(ids[1], {'status': 'failed', 'probability': None, 'error_code': 'invalid_json'})
    return ledger, target, ids


def platform_records(sample):
    """Create and decode real encrypted public locks and approved outcome rows."""
    ledger = PlatformStore(sample.store)
    current = datetime.now(timezone.utc)
    target = {'endpoint': 'Synthetic PFS', 'population': 'Synthetic ITT',
              'comparison': 'Synthetic A versus B', 'success_definition': 'Synthetic superiority test'}
    run = ledger.create_run('synthetic-owner', {'nct_id': 'NCT12345678'},
        {'forecast_kind': 'uncalibrated_llm_research', 'forecasts': {
            'synthetic-model': {'status': 'predicted', 'probability': .6}}},
        model_version='synthetic-v1', pipeline_version='synthetic-source-only',
        data_cutoff=(current - timedelta(days=2)).isoformat(), target=target,
        evidence=[{'url': 'https://example.org/synthetic-prior',
                   'availability_date': (current - timedelta(days=3)).isoformat()}])
    snapshot = ledger.lock('synthetic-owner', run['id'], publish=True)
    outcome = ledger.submit_outcome('synthetic-submitter', snapshot['record']['id'], {
        'trial_id': 'NCT12345678', **{key: target[key] for key in ('endpoint', 'population', 'comparison')},
        'label': 1, 'readout_date': (current - timedelta(days=1)).isoformat(),
        'source': {'url': 'https://example.org/synthetic-result',
                   'published_at': (current - timedelta(hours=12)).isoformat(), 'sha256': 'a' * 64},
        'extraction_status': 'complete', 'conflicts': []}, allow_other_owner=True)
    ledger.verify_outcome('synthetic-independent-reviewer', outcome['id'], 'approved',
                          'Synthetic fixture approval, not scientific adjudication.', CONFIRMATIONS)
    return ledger, snapshot, outcome


@pytest.mark.parametrize('initialized', [False, True])
def test_actual_app_empty_source_install_is_readonly_200_with_exact_missing_404(source_app, monkeypatch, initialized):
    if initialized:
        PlatformStore(source_app.store)
        ReadoutUpdateStore(source_app.store)
    with readonly_gets(source_app, monkeypatch) as statements:
        response = source_app.client.get('/api/arena/public')
        assert response.status_code == 200, response.text
        view = response.json()
        assert view['version'] == arena_public.VERSION
        assert all(view[key] == [] for key in ('models', 'targets', 'runs', 'cohorts'))
        assert view['current_cohort_id'] is None
        for suffix in ('', '/record', '/brief/download'):
            missing = source_app.client.get('/api/arena/public/targets/missing' + suffix)
            assert missing.status_code == 404, missing.text
        assert 'PRAGMA query_only=ON' in statements
    assert not (source_app.store.path / 'arena.sqlite3').exists()


def test_source_only_actual_arena_records_detail_json_brief_keep_failure_and_no_get_writes(source_app, monkeypatch):
    ledger, target, ids = register_arena(source_app)
    platform, snapshot, outcome = platform_records(source_app)
    before = ledger.events()
    # Existing platform originals are decoded but are not invented as Arena assignments.
    assert len(platform.list_public_snapshots()) == len(platform.public_outcome_records()) == 1
    with readonly_gets(source_app, monkeypatch):
        overview = source_app.client.get('/api/arena/public')
        assert overview.status_code == 200, overview.text
        assert [item['id'] for item in overview.json()['targets']] == [target['id']]
        path = '/api/arena/public/targets/' + target['id']
        detail = source_app.client.get(path)
        assert detail.status_code == 200, detail.text
        record = source_app.client.get(path + '/record')
        assert record.status_code == 200 and record.json() == detail.json()
        rows = {row['id']: row for row in detail.json()['runs']}
        assert set(rows) == set(ids)
        assert rows[ids[0]]['status'] == 'predicted' and rows[ids[0]]['predicted_outcome'] == 'Success'
        assert rows[ids[1]]['status'] == 'failed' and rows[ids[1]]['predicted_outcome'] == 'Uncertain'
        assert all(row['probability'] is None and row['confidence'] is None for row in rows.values())
        assert detail.json()['consensus']['assigned'] == 2
        assert detail.json()['consensus']['agreement_denominator'] == 1
        assert detail.json()['consensus']['agreement'] is None
        assert detail.json()['result'] is None
        download = source_app.client.get(path + '/brief/download')
        assert download.status_code == 200, download.text
        with zipfile.ZipFile(BytesIO(download.content)) as archive:
            files = {name: archive.read(name) for name in archive.namelist()}
        assert json.loads(files['clinical-record.json']) == detail.json()
        manifest = json.loads(files['manifest.json'])
        for name, identity in manifest['files'].items():
            assert identity == {'sha256': hashlib.sha256(files[name]).hexdigest(), 'bytes': len(files[name])}
        assert manifest['provider_calls'] == manifest['ledger_writes'] == 0
        assert 'failed' in files['clinical-brief.md'].decode()
    assert ledger.events() == before
    assert platform.get_snapshot(snapshot['record']['id'], owner='synthetic-owner') == snapshot
    assert platform.public_outcome_records()[0]['outcome_id'] == outcome['id']


@pytest.mark.parametrize('table', PLATFORM_TABLES)
def test_partial_existing_platform_schema_is_not_an_empty_install(source_app, monkeypatch, table):
    PlatformStore(source_app.store)
    with source_app.store.connect() as db:
        db.execute('DROP TABLE ' + table)
    with readonly_gets(source_app, monkeypatch):
        assert_unavailable(source_app)


@pytest.mark.parametrize('table', PLATFORM_TABLES)
def test_existing_public_platform_original_integrity_is_checked_without_legacy_files(source_app, monkeypatch, table):
    platform_records(source_app)
    with source_app.store.connect() as db:
        db.execute('DROP TRIGGER ' + table + '_no_update')
        db.execute('UPDATE ' + table + " SET sha256='" + '0' * 64 + "'")
    with readonly_gets(source_app, monkeypatch):
        assert_unavailable(source_app)


@pytest.mark.parametrize('marker', ['provenance', 'archive_provenance'])
def test_synthetic_persisted_archive_snapshot_requires_its_missing_materials(source_app, monkeypatch, marker):
    # This is an explicitly synthetic imported-shaped row, not an execution of
    # import_legacy_archive or a claim to possess historical source originals.
    platform, original, _ = platform_records(source_app)
    record = deepcopy(original['record'])
    record['id'] = record['snapshot_id'] = 'ps_synthetic_archive'
    record['prediction_id'] = 'pr_synthetic_archive'
    record['previous_snapshot_sha256'] = None
    record[marker] = 'archived_record_import' if marker == 'provenance' else {'case_id': 'synthetic-only'}
    with source_app.store.connect() as db:
        db.execute('INSERT INTO platform_prediction_snapshots VALUES(?,?,?,?,?,?,?,?,?)', (
            record['id'], record['prediction_id'], 'synthetic-internal-trial', 'synthetic-archive-owner',
            1, platform_store.digest(record), None, source_app.store.pack(record), record['locked_at']))
    decoded = next(item for item in platform.list_public_snapshots()
                   if item['record']['snapshot_id'] == record['snapshot_id'])
    assert decoded['sha256'] == platform_store.digest(record)
    assert decoded['record'][marker] == record[marker]
    with readonly_gets(source_app, monkeypatch):
        with pytest.raises(ValueError, match='Recorded archive imports require legacy source material'):
            arena_public.read_archived(source_app.store)
        assert_unavailable(source_app)


@pytest.mark.parametrize('table', ['readout_update_proposals', 'readout_update_decisions'])
def test_partial_calendar_schema_still_fails_closed_without_archives(source_app, monkeypatch, table):
    ReadoutUpdateStore(source_app.store)
    with source_app.store.connect() as db:
        db.execute('DROP TABLE ' + table)
    with readonly_gets(source_app, monkeypatch):
        assert_unavailable(source_app)


@pytest.mark.parametrize('kind', ['corrupt', 'symlink', 'broken_symlink', 'directory'])
def test_existing_workbench_path_or_corruption_is_not_silently_skipped(source_app, monkeypatch, kind):
    path = source_app.store.path / 'workbench.sqlite3'
    if kind == 'corrupt':
        path.write_bytes(b'Synthetic deliberately corrupt SQLite bytes')
    else:
        moved = path.with_name('original-workbench.sqlite3')
        path.rename(moved)
        if kind == 'directory':
            path.mkdir()
        else:
            path.symlink_to(moved if kind == 'symlink' else path.with_name('absent.sqlite3'))
    with readonly_gets(source_app, monkeypatch):
        assert_unavailable(source_app)


def test_corrupt_existing_arena_chain_does_not_become_an_empty_install(source_app, monkeypatch):
    ledger, _, _ = register_arena(source_app)
    with ledger.connect() as db:
        db.execute('DROP TRIGGER arena_no_update')
        db.execute("UPDATE arena_events SET payload='{}' WHERE seq=1")
    with readonly_gets(source_app, monkeypatch):
        assert_unavailable(source_app)


@pytest.mark.parametrize('name', ['predome_data', 'dashboard_data', 'trial_data', 'public_data'])
@pytest.mark.parametrize('kind', ['directory', 'file', 'symlink', 'broken_symlink'])
def test_any_legacy_root_requires_the_original_full_integrity_path(source_app, monkeypatch, name, kind):
    path = source_app.root / name
    if kind == 'directory':
        path.mkdir()
    elif kind == 'file':
        path.write_bytes(b'Synthetic partial installation marker')
    else:
        target = source_app.root / 'synthetic-legacy-target'
        if kind == 'symlink':
            target.mkdir()
        path.symlink_to(target)
    with readonly_gets(source_app, monkeypatch):
        assert_unavailable(source_app)


def test_corrupt_present_legacy_release_cannot_take_source_only_fallback(source_app, monkeypatch):
    path = source_app.root / 'predome_data/continuous_v4/release.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'record': {'artifact_manifest': []}, 'sha256': '0' * 64}))
    with readonly_gets(source_app, monkeypatch):
        assert_unavailable(source_app)
