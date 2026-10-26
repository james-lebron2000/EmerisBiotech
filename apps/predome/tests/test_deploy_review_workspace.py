"""Real temporary transports, isolated imports and SQLite; no production service commands."""
import gzip
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tarfile
from types import SimpleNamespace

import pytest
from test_arena_core import registered
from test_deploy_arena import make_workbench

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
import deploy_review_workspace as release

ROOT = Path(__file__).parents[1]
COMMIT = 'a' * 40
OLD_SITE = b'old isolated nginx site\n'
NEW_SITE = b'new isolated nginx site\n'


def source_files():
    files = {name: b'# isolated fixture\n' if name.endswith('.py') else b'isolated asset\n' for name in release.FILES}
    files['emeris_live/main.py'] = b'''import os, sys
if os.environ.get('PROVIDER_SECRET') or os.environ.get('PYTHONPATH') or os.environ.get('EMERIS_DATA_DIR'):
    raise RuntimeError('Inherited credentials or runtime')
if not sys.flags.isolated or os.environ.get('EMERIS_ENABLE_ARENA_LLM') != 'false':
    raise RuntimeError('Unsafe child configuration')
def create_app():
    raise RuntimeError('The deployment must never construct an app')
'''
    files['scripts/configure_arena_frontends.py'] = (
        b'def upgrade_review_routes(original):\n    assert original == ' + repr(OLD_SITE).encode()
        + b'\n    return ' + repr(NEW_SITE).encode() + b'\n')
    files['scripts/deploy_review_workspace.py'] = (ROOT / 'scripts/deploy_review_workspace.py').read_bytes()
    return files


def transport(tmp_path, *, files=None, extra=None, remove=None, metadata=None, additions=()):
    files = source_files() if files is None else dict(files)
    if remove:
        files.pop(remove)
    meta = {'version': release.VERSION, 'git_commit': COMMIT,
            'files': {name: release.digest(raw) for name, raw in files.items()}}
    if metadata:
        meta.update(metadata)
    members = {**files, 'CODE_RELEASE.json': json.dumps(meta).encode(), **(extra or {})}
    target = tmp_path / 'code.tar.gz'
    with tarfile.open(target, 'w:gz', format=tarfile.USTAR_FORMAT) as archive:
        for name, raw in members.items():
            item = tarfile.TarInfo(name)
            item.size = len(raw)
            archive.addfile(item, io.BytesIO(raw))
        for item, raw in additions:
            archive.addfile(item, io.BytesIO(raw) if raw is not None else None)
    return target, release.digest(target.read_bytes()), COMMIT


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    install = tmp_path / 'installation'
    base = install / 'releases/base'
    base.mkdir(parents=True)
    data = tmp_path / 'data'
    ledger, target, ids = registered(data)
    make_workbench(data / 'workbench.sqlite3')
    (base / 'emeris_live').mkdir()
    (base / 'emeris_live/__init__.py').write_bytes(b'')
    (base / 'scripts').mkdir()
    for name in ('upgrade_arena.py', 'deploy_arena.py'):
        shutil.copyfile(ROOT / 'scripts' / name, base / 'scripts' / name)
    (base / 'arena_data').mkdir()
    (base / 'arena_data/sealed.json').write_bytes(b'original sealed fixture')
    records = [{'path': str(path.relative_to(base)), 'sha256': release.digest(path.read_bytes()),
                'bytes': path.stat().st_size} for path in base.rglob('*') if path.is_file()]
    manifest = {'version': release.BASE_VERSION, 'git_commit': release.BASE_COMMIT, 'files': records}
    (base / 'RELEASE_MANIFEST.json').write_text(json.dumps(manifest))
    current = install / 'current'
    current.symlink_to(base)
    site = tmp_path / 'existing-site'
    site.write_bytes(OLD_SITE)
    site.chmod(0o640)
    configs = (tmp_path / 'root.env', tmp_path / 'unit.service', tmp_path / 'arena.env')
    for path in configs:
        path.write_bytes(b'private configuration fixture')
        path.chmod(0o600)
    for name, value in {'INSTALL_ROOT': install, 'BASE': base, 'TARGET': install / 'releases/new',
                        'CURRENT': current, 'DATA': data, 'NGINX_SITE': site, 'CONFIGS': configs,
                        'PRIVATE_CONFIGS': frozenset(configs[:1]), 'ROOT_UID': os.geteuid(),
                        'PYTHON': sys.executable}.items():
        monkeypatch.setattr(release, name, value)
    monkeypatch.setenv('PROVIDER_SECRET', 'must-not-reach-import')
    monkeypatch.setenv('EMERIS_DATA_DIR', str(data))
    monkeypatch.setenv('PYTHONPATH', 'must-not-reach-import')
    calls = []
    monkeypatch.setattr(release, 'restart', lambda: calls.append('restart'))
    monkeypatch.setattr(release, 'nginx_test', lambda: calls.append('nginx-test'))
    monkeypatch.setattr(release, 'nginx_reload', lambda: calls.append('nginx-reload'))
    monkeypatch.setattr(release, 'await_health', lambda version: calls.append('health-' + version))
    return SimpleNamespace(install=install, base=base, data=data, ledger=ledger, target=target,
                           ids=ids, site=site, configs=configs, calls=calls)


def deploy(tmp_path, **kwargs):
    return release.deploy(*transport(tmp_path, **kwargs), release.digest(OLD_SITE))


def test_transport_exact_full_file_manifest(tmp_path):
    assert set(release.read_archive(*transport(tmp_path))) == release.FILES
    assert release.BASE.name == '20261024-arena08' and release.TARGET.name == '20261026-arena10'
    assert release.BASE_VERSION == '1.2.7' and release.VERSION == '1.2.9'


@pytest.mark.parametrize('name', ['arena.sqlite3', 'arena_data/receipt.json', '../escape.py',
                                  'static/arena.css', 'emeris_live/new.py', 'CODE_RELEASE.json/evil'])
def test_unlisted_members_fail_closed(tmp_path, name):
    with pytest.raises(ValueError, match='Unexpected code transport'):
        release.read_archive(*transport(tmp_path, extra={name: b'forbidden'}))


@pytest.mark.parametrize('kind', ['symlink', 'hardlink', 'duplicate', 'oversized'])
def test_tar_links_duplicates_and_limits(tmp_path, kind):
    item = tarfile.TarInfo('emeris_live/main.py')
    raw = None
    if kind in ('symlink', 'hardlink'):
        item.type = tarfile.SYMTYPE if kind == 'symlink' else tarfile.LNKTYPE
        item.linkname = '/outside'
    elif kind == 'duplicate':
        item.size = 1
        raw = b'x'
    else:
        item.size = release.MAX_MEMBER + 1
        raw = b'x' * item.size
    with pytest.raises(ValueError, match='Unexpected code transport'):
        release.read_archive(*transport(tmp_path, additions=((item, raw),)))


@pytest.mark.parametrize('change', [{'version': '1.2.8'}, {'git_commit': 'b' * 40}, {'files': {}}, {'extra': 1}])
def test_manifest_identity_and_all_hashes_are_exact(tmp_path, change):
    with pytest.raises(ValueError, match='manifest differs'):
        release.read_archive(*transport(tmp_path, metadata=change))


def test_missing_required_file_and_wrong_pinned_hash(tmp_path):
    with pytest.raises(ValueError, match='manifest differs'):
        release.read_archive(*transport(tmp_path, remove='emeris_live/arena_review_auth.py'))
    path, _, commit = transport(tmp_path)
    with pytest.raises(ValueError, match='hash differs'):
        release.read_archive(path, 'f' * 64, commit)


def test_actual_oversized_member_is_rejected(tmp_path):
    files = source_files()
    files['emeris_live/main.py'] = b'x' * (release.MAX_MEMBER + 1)
    with pytest.raises(ValueError, match='Unexpected code transport'):
        release.read_archive(*transport(tmp_path, files=files))


def test_compressed_expansion_is_bounded_before_tar_parsing(tmp_path, monkeypatch):
    monkeypatch.setattr(release, 'MAX_EXPANDED', 1024)
    path = tmp_path / 'bomb.gz'
    path.write_bytes(gzip.compress(b'x' * (1024 + 128 * 1024 + 1)))
    with pytest.raises(ValueError, match='Expanded code transport exceeds'):
        release.read_archive(path, release.digest(path.read_bytes()), COMMIT)


def test_success_real_import_render_backups_and_sqlite_preservation(tmp_path, isolated):
    state = isolated
    before = state.ledger.events()
    configs = {path: path.read_bytes() for path in state.configs}
    receipt = deploy(tmp_path)
    assert release.CURRENT.resolve() == release.TARGET
    assert state.site.read_bytes() == NEW_SITE and state.site.stat().st_mode & 0o777 == 0o640
    assert state.ledger.events() == before and {path: path.read_bytes() for path in state.configs} == configs
    assert receipt['database_imports'] == 0 and receipt['import_smoke'] == 'passed_without_app_construction'
    assert state.calls == ['nginx-test', 'restart', 'health-1.2.9', 'nginx-reload']
    backup = state.install / 'backups/20261026-arena10'
    assert (backup / 'nginx-site.conf').read_bytes() == OLD_SITE
    with sqlite3.connect(backup / 'workbench.sqlite3') as db:
        assert db.execute('SELECT value FROM platform_prediction_snapshots').fetchall() == [('immutable',)]
    assert (release.TARGET / 'arena_data/sealed.json').read_bytes() == b'original sealed fixture'
    assert release.verify_manifest(release.TARGET)['git_commit'] == COMMIT


@pytest.mark.parametrize('field,value', [('version', '1.2.8'), ('git_commit', 'b' * 40)])
def test_conflicting_base_refused_before_new_paths(tmp_path, isolated, field, value):
    path = isolated.base / 'RELEASE_MANIFEST.json'
    manifest = json.loads(path.read_bytes())
    manifest[field] = value
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='live code version or commit'):
        deploy(tmp_path)
    assert not release.TARGET.exists() and isolated.site.read_bytes() == OLD_SITE
    assert not (isolated.install / 'backups').exists() and isolated.calls == []


def test_bootstrap_must_match_the_pinned_entry_point(tmp_path, isolated):
    files = source_files()
    files['scripts/deploy_review_workspace.py'] += b'\n# different bootstrap\n'
    with pytest.raises(ValueError, match='entry point differs'):
        deploy(tmp_path, files=files)
    assert not release.TARGET.exists() and isolated.calls == []


@pytest.mark.parametrize('kind', ['target', 'backup', 'receipt', 'current-marker', 'site-marker'])
def test_existing_deployment_artifacts_never_overwritten(tmp_path, isolated, kind):
    paths = {'target': release.TARGET, 'backup': isolated.install / 'backups/20261026-arena10',
             'receipt': isolated.install / 'arena10-receipt.json',
             'current-marker': release.CURRENT.with_name('current-arena10-next'),
             'site-marker': isolated.site.with_name(isolated.site.name + '.arena10-next')}
    path = paths[kind]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'preexisting artifact')
    with pytest.raises(ValueError, match='unused deployment paths'):
        deploy(tmp_path)
    assert path.read_bytes() == b'preexisting artifact' and isolated.calls == []


@pytest.mark.parametrize('kind', ['hash', 'symlink', 'mode', 'owner', 'private-mode'])
def test_configuration_refusal_before_staging(tmp_path, isolated, monkeypatch, kind):
    if kind == 'hash':
        isolated.site.write_bytes(b'unexpected old bytes')
    elif kind == 'symlink':
        outside = tmp_path / 'outside'
        outside.write_bytes(OLD_SITE)
        isolated.site.unlink()
        isolated.site.symlink_to(outside)
    elif kind == 'mode':
        isolated.site.chmod(0o666)
    elif kind == 'owner':
        monkeypatch.setattr(release, 'ROOT_UID', os.geteuid() + 1)
    else:
        isolated.configs[0].chmod(0o644)
    with pytest.raises(ValueError):
        deploy(tmp_path)
    assert not release.TARGET.exists() and isolated.calls == []


@pytest.mark.parametrize('failure', ['nginx-test', 'nginx-reload', 'new-health'])
def test_failures_restore_only_code_and_site_preserving_new_sqlite_rows(tmp_path, isolated, monkeypatch, failure):
    state = isolated
    old_events = state.ledger.events()
    failed = False
    def fail_once(stage):
        nonlocal failed
        state.calls.append(stage)
        if stage == failure and not failed:
            failed = True
            state.ledger.start(state.ids[0])
            with sqlite3.connect(state.data / 'workbench.sqlite3') as db:
                db.execute('INSERT INTO platform_prediction_snapshots VALUES (2,"concurrent user record")')
            raise RuntimeError('isolated deployment failure')
    monkeypatch.setattr(release, 'nginx_test', lambda: fail_once('nginx-test'))
    monkeypatch.setattr(release, 'nginx_reload', lambda: fail_once('nginx-reload'))
    monkeypatch.setattr(release, 'await_health', lambda version: fail_once('new-health' if version == release.VERSION else 'old-health'))
    with pytest.raises(RuntimeError, match='isolated deployment failure'):
        deploy(tmp_path)
    assert release.CURRENT.resolve() == state.base and state.site.read_bytes() == OLD_SITE
    assert state.ledger.events()[:len(old_events)] == old_events
    assert len(state.ledger.events()) == len(old_events) + 1
    with sqlite3.connect(state.data / 'workbench.sqlite3') as db:
        assert db.execute('SELECT value FROM platform_prediction_snapshots WHERE id=2').fetchone()[0] == 'concurrent user record'
    assert not (state.install / 'arena10-receipt.json').exists()
    assert state.calls[-2:] == ['nginx-test', 'nginx-reload']
    if failure != 'nginx-test':
        assert 'old-health' in state.calls and state.calls.count('restart') == 2


@pytest.mark.parametrize('bad_source', [b'import absent_deployment_dependency\n',
                                      b'from pathlib import Path\nPath("forbidden-state").write_text("bad")\n',
                                      b'import sqlite3\nsqlite3.connect(":memory:")\n'])
def test_real_import_smoke_refuses_missing_dependency_and_state_creation(tmp_path, isolated, bad_source):
    files = source_files()
    files['emeris_live/arena_review_auth.py'] = bad_source
    with pytest.raises(subprocess.CalledProcessError):
        deploy(tmp_path, files=files)
    assert release.CURRENT.resolve() == isolated.base and isolated.site.read_bytes() == OLD_SITE
    assert not (release.TARGET / 'forbidden-state').exists() and isolated.calls == []


def test_renderer_is_rechecked_after_smoke_before_loading(tmp_path, isolated, monkeypatch):
    original = release.import_smoke
    def tamper():
        original()
        (release.TARGET / 'scripts/configure_arena_frontends.py').write_bytes(b'raise RuntimeError("unverified code")')
    monkeypatch.setattr(release, 'import_smoke', tamper)
    with pytest.raises(ValueError, match='renderer changed'):
        deploy(tmp_path)
    assert isolated.site.read_bytes() == OLD_SITE and isolated.calls == []


def test_rollback_health_failure_is_not_reported_as_restored(tmp_path, isolated, monkeypatch):
    def unhealthy(version):
        raise RuntimeError('health unavailable')
    monkeypatch.setattr(release, 'await_health', unhealthy)
    with pytest.raises(RuntimeError, match='restoration could not be fully verified'):
        deploy(tmp_path)
    assert release.CURRENT.resolve() == isolated.base and isolated.site.read_bytes() == OLD_SITE
    assert not (isolated.install / 'arena10-receipt.json').exists()
    assert isolated.calls[-2:] == ['nginx-test', 'nginx-reload']


def test_rollback_refuses_to_overwrite_a_concurrent_site_edit(tmp_path, isolated, monkeypatch):
    def health(version):
        if version == release.VERSION:
            isolated.site.write_bytes(b'concurrent operator edit')
            raise RuntimeError('new service failed')
    monkeypatch.setattr(release, 'await_health', health)
    with pytest.raises(RuntimeError, match='restoration could not be fully verified'):
        deploy(tmp_path)
    assert release.CURRENT.resolve() == isolated.base
    assert isolated.site.read_bytes() == b'concurrent operator edit'
    assert not (isolated.install / 'arena10-receipt.json').exists()


def test_restrictive_operator_umask_keeps_new_code_service_readable(tmp_path, isolated):
    previous_umask = os.umask(0o077)
    try:
        deploy(tmp_path)
    finally:
        os.umask(previous_umask)
    assert all((release.TARGET / name).stat().st_mode & 0o777 == 0o644 for name in release.FILES)
    assert all((release.TARGET / name).parent.stat().st_mode & 0o777 == 0o755 for name in release.FILES)


def test_real_current_runtime_imports_without_constructing_app(tmp_path, monkeypatch):
    target = tmp_path / 'source-release'
    shutil.copytree(ROOT / 'emeris_live', target / 'emeris_live', ignore=shutil.ignore_patterns('__pycache__'))
    monkeypatch.setattr(release, 'TARGET', target)
    monkeypatch.setattr(release, 'PYTHON', sys.executable)
    release.import_smoke()
    assert not list(target.rglob('*.sqlite3')) and not list(target.rglob('__pycache__'))


def test_cli_requires_operator_pinned_nginx_hash(tmp_path):
    path, sha, commit = transport(tmp_path)
    result = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/deploy_review_workspace.py'),
                             '--archive', str(path), '--sha256', sha, '--commit', commit],
                            cwd=tmp_path, env=release.child_environment(), capture_output=True, text=True)
    assert result.returncode != 0 and '--expected-nginx-sha256' in result.stderr
