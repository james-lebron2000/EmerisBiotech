"""Execute the shipped remote Python programs locally; no SSH or host changes."""
import hashlib
import importlib.util
import io
from pathlib import Path
import subprocess
import sys
import tarfile

import pytest

from emeris_live.arena_store import ArenaLedger

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import test_source as source


def command_module(name):
    spec = importlib.util.spec_from_file_location('deployment_command_' + name, ROOT / 'scripts' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


arena = command_module('deploy_arena')
release = command_module('deploy_release')


def execute(program, args, mode):
    env = source.test_environment()
    if mode == 'env':
        env['PYTHONOPTIMIZE'] = '1'
    return subprocess.run([sys.executable, *(['-O'] if mode == 'flag' else []), '-c', program, *map(str, args)],
                          cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)


@pytest.mark.parametrize('mode', ['normal', 'flag', 'env'])
@pytest.mark.parametrize('matches', [True, False])
def test_staged_ledger_digest_required_in_optimized_python(tmp_path, mode, matches):
    ledger = ArenaLedger(tmp_path)
    with ledger.connect() as db:
        ledger._append(db, 'cohort_registered', 'engineering-fixture', {'protocol': {'purpose': 'offline test'}})
    before = ledger.events()
    expected = before[-1]['event_hash'] if matches else '0' * 64
    result = execute(arena.STAGED_LEDGER_CHECK, [ROOT, tmp_path, expected], mode)
    assert (result.returncode == 0) is matches, result.stderr
    if not matches:
        assert 'Staged Arena ledger SHA-256 differs' in result.stderr
    assert ledger.events() == before


@pytest.mark.parametrize('mode', ['normal', 'flag', 'env'])
@pytest.mark.parametrize('matches', [True, False])
def test_release_hash_checked_before_writing_bootstrap(tmp_path, mode, matches):
    payload = b'print("fixture bootstrap; never executed by this check")\n'
    archive = tmp_path / 'release.tar.gz'
    with tarfile.open(archive, 'w:gz') as bundle:
        item = tarfile.TarInfo('deploy/deploy_upgrade.py')
        item.size = len(payload)
        bundle.addfile(item, io.BytesIO(payload))
    original = archive.read_bytes()
    expected = hashlib.sha256(original).hexdigest() if matches else '0' * 64
    output = tmp_path / 'bootstrap.py'
    output.write_bytes(b'existing operator file')
    result = execute(release.BOOTSTRAP_PROGRAM, [archive, expected, output], mode)
    assert (result.returncode == 0) is matches, result.stderr
    assert output.read_bytes() == (payload if matches else b'existing operator file')
    assert archive.read_bytes() == original
    if not matches:
        assert 'Pinned release archive SHA-256 differs' in result.stderr
