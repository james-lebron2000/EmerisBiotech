"""Small temporary Git repositories test the source-only release boundary."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tarfile

import pytest


SPEC = importlib.util.spec_from_file_location('trial_bundle',
    Path(__file__).resolve().parents[1] / 'scripts/build_local_trial_bundle.py')
bundle = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bundle)


def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args], stderr=subprocess.STDOUT).decode().strip()


def commit(repo):
    git(repo, 'add', '.')
    git(repo, '-c', 'user.name=Bundle test', '-c', 'user.email=bundle@example.invalid',
        '-c', 'commit.gpgsign=false', 'commit', '-qm', 'isolated packaging fixture')
    return git(repo, 'rev-parse', 'HEAD')


@pytest.fixture
def source(tmp_path):
    repo = tmp_path / 'source'
    repo.mkdir()
    git(repo, 'init', '-q')
    paths = bundle.REQUIRED | {'vbt', '.gitignore', 'examples/public.json'}
    paths |= {f'src/vbt/module{i:02}.py' for i in range(58)}
    for path in paths:
        file = repo / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text('# synthetic public source\n', encoding='utf-8')
    (repo / 'vbt').chmod(0o755)
    return repo, commit(repo)


def test_deterministic_inventory_hashes_committed_bytes_and_safe_extract(source, tmp_path):
    repo, revision = source
    first = bundle.build_bundle(repo, revision, tmp_path / 'one')
    second = bundle.build_bundle(repo, revision, tmp_path / 'two')
    assert Path(first['archive']).read_bytes() == Path(second['archive']).read_bytes()
    assert Path(first['manifest']).read_bytes() == Path(second['manifest']).read_bytes()
    manifest = json.loads(Path(first['manifest']).read_bytes())
    assert manifest['source_commit'] == revision
    assert manifest['vbt_module_count'] == 61
    assert manifest['prebuilt_image_included'] is False
    assert manifest['runtime_data_included'] is False
    inventory = {r['archive_path']: r for r in manifest['files']}
    extracted = tmp_path / 'extracted'
    extracted.mkdir()
    with tarfile.open(first['archive']) as tar:
        assert len(tar.getmembers()) == manifest['file_count'] == len(inventory)
        for member in tar.getmembers():
            assert member.isfile() and bundle._safe_path(member.name)
            assert member.uid == member.gid == 0
            assert member.mtime == manifest['source_date_epoch']
            row = inventory[member.name]
            raw = tar.extractfile(member).read()
            assert len(raw) == row['bytes'] == member.size
            assert hashlib.sha256(raw).hexdigest() == row['sha256']
            assert hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest() == row['git_blob_oid']
            destination = extracted / member.name
            assert destination.resolve().is_relative_to(extracted.resolve())
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(raw)
        assert tar.getmember(manifest['archive_root'] + '/vbt').mode == 0o755
    for line in Path(first['checksums']).read_text().splitlines():
        digest, name = line.split('  ')
        assert hashlib.sha256((Path(first['checksums']).parent / name).read_bytes()).hexdigest() == digest


@pytest.mark.parametrize('change', ['modified', 'staged', 'untracked'])
def test_uncommitted_selected_source_cannot_claim_a_commit(source, tmp_path, change):
    repo, revision = source
    path = repo / ('src/vbt/new.py' if change == 'untracked' else 'README.md')
    path.write_text('uncommitted source\n')
    if change == 'staged':
        git(repo, 'add', 'README.md')
    with pytest.raises(ValueError, match='Uncommitted package source'):
        bundle.build_bundle(repo, revision, tmp_path / 'out')
    assert not (tmp_path / 'out').exists()


def test_runtime_private_database_and_environment_files_are_excluded(source, tmp_path):
    repo, _ = source
    excluded = ['artifacts/run.json', 'data/public.csv', 'private/note.txt', '.env',
        'docs/private/keys.json', 'deploy/cloud/secrets/access.caddy',
        'src/vbt/state.sqlite3', 'src/vbt/state.sqlite3-wal', '.venv/runtime.txt', 'output/log.txt']
    for relative in excluded:
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('never package this\n')
    revision = commit(repo)
    # Runtime leftovers do not make clean tracked source dirty.
    (repo / '.env.local').write_text('untracked credentials\n')
    (repo / 'artifacts/untracked.json').write_text('{}')
    result = bundle.build_bundle(repo, revision, tmp_path / 'out')
    rows = json.loads(Path(result['manifest']).read_bytes())['files']
    assert not set(excluded) & {row['path'] for row in rows}
    with tarfile.open(result['archive']) as tar:
        assert all(b'never package this' not in tar.extractfile(m).read() for m in tar.getmembers())


def test_selected_old_commit_reads_objects_not_current_files(source, tmp_path):
    repo, old_revision = source
    (repo / 'README.md').write_text('new committed version\n')
    commit(repo)
    result = bundle.build_bundle(repo, old_revision, tmp_path / 'out')
    with tarfile.open(result['archive']) as tar:
        name = 'EmerisBiotech-source-' + old_revision[:12] + '/README.md'
        assert tar.extractfile(name).read() == b'# synthetic public source\n'


@pytest.mark.parametrize('path', ['/etc/passwd', '../outside', 'src/../outside',
    'src//empty', 'src/./dot', 'src\\outside', 'C:/source', 'src/new\nline', 'src/tab\tname'])
def test_unsafe_paths_rejected(path):
    assert not bundle._safe_path(path)
    with pytest.raises(ValueError, match='Unsafe Git package path'):
        bundle._selected(path)


def test_tracked_source_symlink_is_rejected(source):
    repo, _ = source
    (repo / 'src/vbt/alias.py').symlink_to('cli.py')
    revision = commit(repo)
    with pytest.raises(ValueError, match='ordinary Git source files'):
        bundle.source_tree(repo, revision)


@pytest.mark.parametrize('payload', [b'\xffbinary', b'a\0b',
    b'version https://git-lfs.github.com/spec/v1\noid sha256:example\n',
    b'-----BEGIN ' + b'PRIVATE KEY-----\nfixture\n', b'sk-' + b'a' * 32])
def test_binary_lfs_and_credential_tripwires_fail_closed(source, tmp_path, payload):
    repo, _ = source
    (repo / 'examples/public.json').write_bytes(payload)
    revision = commit(repo)
    with pytest.raises(ValueError) as error:
        bundle.build_bundle(repo, revision, tmp_path / 'out')
    assert repr(payload) not in str(error.value)
    assert not (tmp_path / 'out').exists()


def test_modules_required_files_and_explicit_commit_are_checked(source):
    repo, revision = source
    for moving in ['HEAD', 'main', 'HEAD~1', '--all']:
        with pytest.raises(ValueError, match='hexadecimal'):
            bundle.source_tree(repo, moving)
    with pytest.raises(ValueError, match='Expected 62 vbt modules, found 61'):
        bundle.source_tree(repo, revision, expected_modules=62)
    (repo / 'uv.lock').unlink()
    revision = commit(repo)
    with pytest.raises(ValueError, match='Required source/configuration files missing: uv.lock'):
        bundle.source_tree(repo, revision)


def test_existing_release_cannot_be_overwritten(source, tmp_path):
    repo, revision = source
    result = bundle.build_bundle(repo, revision, tmp_path / 'out')
    before = Path(result['archive']).read_bytes()
    with pytest.raises(ValueError, match='already exists'):
        bundle.build_bundle(repo, revision, tmp_path / 'out')
    assert Path(result['archive']).read_bytes() == before


def test_actual_baseline_has_61_committed_modules_without_building_or_copying(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    try:
        bundle._git(repo, 'cat-file', '-t', '94e6f1d0ac8620564f44a5f09595560425c39196')
    except ValueError:
        pytest.skip('Historical Git object integration check requires the source checkout, not an extracted source archive')
    revision, _, rows = bundle.source_tree(repo, '94e6f1d')
    modules = [r for r in rows if r['path'].startswith('src/vbt/') and r['path'].endswith('.py')]
    assert revision == '94e6f1d0ac8620564f44a5f09595560425c39196'
    assert len(modules) == 61
    blobs = bundle._blobs(repo, modules)
    assert all(len(blobs[row['git_blob_oid']]) == row['bytes'] for row in modules)
    assert not list(tmp_path.iterdir())
