"""Exercise real check entry points on temporary source trees; no model services."""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import check as checks
import test_source as source


@pytest.fixture
def package(tmp_path):
    for name in ('scripts', 'emeris_live', 'tests', 'static'):
        (tmp_path / name).mkdir()
    for name in ('check.py', 'test_source.py'):
        shutil.copyfile(SCRIPTS / name, tmp_path / 'scripts' / name)
    (tmp_path / 'emeris_live' / '__init__.py').write_text('')
    (tmp_path / 'scripts' / 'entry.py').write_text('raise RuntimeError("CLI must never be imported")\n')
    (tmp_path / 'tests' / 'test_case.py').write_text('def test_actual_case():\n    assert True\n')
    (tmp_path / 'ci-tests.txt').write_text('tests/test_case.py\n')
    return tmp_path


def invoke(package, script='test_source.py', *args, env=None):
    # Execute from outside the package to verify that commands select their own cwd.
    return subprocess.run([sys.executable, str(package / 'scripts' / script), *args],
                          cwd=package.parent, env=env, capture_output=True, text=True, timeout=30)


def test_real_source_entry_point_uses_package_cwd_and_allowlisted_environment(package):
    (package / 'tests' / 'test_case.py').write_text('''import os
from pathlib import Path
def test_actual_environment():
    assert Path.cwd().name == Path(__file__).parents[1].name
    for key in ('APIKEY', 'EPO_OPS_SECRET', 'USPTO_ODP_API_KEY', 'EMERIS_DATA_DIR',
                'ARENA_PUBLIC_LIVE_BASE', 'ARENA_LAB_STORAGE_STATE', 'PYTEST_ADDOPTS', 'NODE_OPTIONS'):
        assert key not in os.environ
    assert os.environ['EMERIS_MODE'] == 'local'
    assert os.environ['EMERIS_SOURCE_ONLY_CI'] == 'true'
    assert os.environ['EMERIS_ENABLE_ARENA_LLM'] == 'false'
''')
    env = dict(os.environ, APIKEY='isolated-not-a-key', EPO_OPS_SECRET='isolated-not-a-secret',
               USPTO_ODP_API_KEY='isolated-not-a-key', EMERIS_DATA_DIR='/never/production',
               ARENA_PUBLIC_LIVE_BASE='https://never.invalid', ARENA_LAB_STORAGE_STATE='/never/session',
               PYTEST_ADDOPTS='--invalid-injected-option', NODE_OPTIONS='--invalid-injected-option')
    result = invoke(package, env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert '1 passed' in result.stdout


@pytest.mark.parametrize('path', source.EVIDENCE_DIRS)
def test_source_entry_rejects_local_evidence_without_deleting_it(package, path):
    evidence = package / path
    evidence.mkdir(parents=True)
    original = evidence / 'original.bin'
    original.write_bytes(b'original frozen material')
    result = invoke(package)
    assert result.returncode != 0 and 'Source-only CI requires no evidence directories' in result.stderr
    assert original.read_bytes() == b'original frozen material'
    assert 'passed' not in result.stdout


def test_public_catalogues_and_configs_remain_valid_source_inputs(package):
    for name in ('arena_data/season_spec.json', 'patent_data/catalog.json'):
        path = package / name
        path.parent.mkdir(exist_ok=True)
        path.write_text('{}')
    assert invoke(package).returncode == 0


@pytest.mark.parametrize('selection', ['', 'scripts/check.py', '../tests/test_case.py',
    'tests/test_missing.py', 'tests/test_case.py\ntests/test_case.py', '/tmp/test_case.py'])
def test_invalid_source_selection_is_rejected_before_pytest(package, selection):
    (package / 'ci-tests.txt').write_text(selection)
    result = invoke(package)
    assert result.returncode != 0 and 'Invalid or empty source test selection' in result.stderr
    assert 'passed' not in result.stdout


def test_source_selection_rejects_symlink_to_external_test(package):
    external = package.parent / 'external_case.py'
    external.write_text('raise RuntimeError("must never execute")')
    (package / 'tests' / 'test_case.py').unlink()
    (package / 'tests' / 'test_case.py').symlink_to(external)
    result = invoke(package)
    assert result.returncode != 0 and 'Invalid or empty' in result.stderr


def test_source_selection_rejects_symlinked_test_directory(package):
    external = package.parent / 'external-tests'
    external.mkdir()
    (external / 'test_case.py').write_text('raise RuntimeError("must never execute")')
    shutil.rmtree(package / 'tests')
    (package / 'tests').symlink_to(external, target_is_directory=True)
    result = invoke(package)
    assert result.returncode != 0 and 'Invalid or empty' in result.stderr


def test_real_pytest_failure_exit_code_is_preserved(package):
    (package / 'tests' / 'test_case.py').write_text('def test_actual_failure():\n    assert False\n')
    result = invoke(package)
    assert result.returncode == 1 and '1 failed' in result.stdout


@pytest.mark.parametrize('statement', ['import scripts.entry', 'from scripts import entry',
    'import entry', 'from ..scripts import entry', 'from apps.predome import scripts'])
def test_static_runtime_boundary_rejects_direct_cli_imports(package, statement):
    (package / 'emeris_live' / 'service.py').write_text(statement)
    with pytest.raises(ValueError, match='Runtime imports CLI'):
        checks.check_runtime(package)


def test_same_named_runtime_module_cannot_hide_absolute_cli_execution(package):
    (package / 'emeris_live' / 'entry.py').write_text('VALUE = 1\n')
    (package / 'emeris_live' / 'service.py').write_text('import entry\n')
    runner = package / 'scripts' / 'runner.py'
    runner.write_text('import sys\nfrom pathlib import Path\nsys.path.insert(1, str(Path(__file__).parents[1]))\nimport emeris_live.service\n')
    actual = subprocess.run([sys.executable, str(runner)], cwd=package, env=source.test_environment(),
                            capture_output=True, text=True, timeout=10)
    assert actual.returncode != 0 and 'CLI must never be imported' in actual.stderr
    with pytest.raises(ValueError, match='Runtime imports CLI'):
        checks.check_runtime(package)


def test_legitimate_vendor_scripts_and_nested_runtime_imports_are_allowed(package):
    vendor = package / 'vendor'
    vendor.mkdir()
    (vendor / 'scripts.py').write_text('constants = 42\n')
    sub = package / 'emeris_live' / 'sub'
    sub.mkdir()
    (sub / 'entry.py').write_text('VALUE = 1\n')
    (sub / 'service.py').write_text('from .entry import VALUE\nfrom vendor.scripts import constants\n')
    actual = subprocess.run([sys.executable, '-c', 'from emeris_live.sub.service import VALUE, constants; assert (VALUE, constants) == (1, 42)'],
                            cwd=package, env=source.test_environment(), capture_output=True, text=True, timeout=10)
    assert actual.returncode == 0, actual.stderr
    checks.check_runtime(package)


def test_static_checks_parse_actual_code_without_executing_it(package):
    (package / 'emeris_live' / 'service.py').write_text('from . import model\nraise RuntimeError("must not execute")')
    (package / 'emeris_live' / 'model.py').write_text('VALUE = 1\n')
    assert checks.check_runtime(package) >= 3
    assert not (package / 'emeris_live' / '__pycache__').exists()
    assert invoke(package, 'check.py', '--suite', 'static').returncode == 0


@pytest.mark.parametrize('name', ['emeris_live/service.py', 'scripts/release.py', 'deploy/verify.py'])
def test_production_assert_rejected_but_test_assert_remains_allowed(package, name):
    path = package / name
    path.parent.mkdir(exist_ok=True)
    path.write_text('assert False, "required integrity check"\n')
    result = invoke(package, 'check.py', '--suite', 'static')
    assert result.returncode != 0
    assert 'Production assert disappears under Python -O' in result.stderr
    assert name in result.stderr


def test_deploy_syntax_checked_without_running_commands(package):
    folder = package / 'deploy'
    folder.mkdir()
    script = folder / 'verify.py'
    script.write_text('raise RuntimeError("must not run during syntax checking")\n')
    assert invoke(package, 'check.py', '--suite', 'static').returncode == 0
    script.write_text('def invalid(:\n')
    result = invoke(package, 'check.py', '--suite', 'static')
    assert result.returncode != 0 and 'deploy/verify.py' in result.stderr


def test_assert_text_is_not_executable_assert(package):
    (package / 'emeris_live' / 'service.py').write_text('"""Do not use assert for validation."""\nTEXT = "assert False"\n')
    assert invoke(package, 'check.py', '--suite', 'static').returncode == 0


def test_python_and_javascript_syntax_errors_fail_real_static_entry(package):
    (package / 'emeris_live' / 'broken.py').write_text('def invalid(:\n')
    result = invoke(package, 'check.py', '--suite', 'static')
    assert result.returncode != 0 and 'Python syntax check failed' in result.stderr
    (package / 'emeris_live' / 'broken.py').unlink()
    (package / 'static' / 'broken.js').write_text('const invalid = ;')
    assert invoke(package, 'check.py', '--suite', 'static').returncode != 0


@pytest.mark.parametrize('name,statement', [
    ('emeris_live/service.py', 'return "compile-only-secret"\n'),
    ('scripts/release.py', 'break  # compile-only-secret\n'),
    ('deploy/verify.py', 'nonlocal compile_only_secret\n'),
    ('emeris_live/service.py', 'f(secret="compile-only-secret", secret=2)\n'),
])
def test_compile_level_syntax_errors_fail_real_static_entry_without_source_echo(package, name, statement):
    path = package / name
    path.parent.mkdir(exist_ok=True)
    path.write_text(statement)
    # These errors survive AST parsing; the actual Python compiler rejects them.
    result = invoke(package, 'check.py', '--suite', 'static', env=source.test_environment())
    assert result.returncode != 0
    assert f'Python syntax check failed: {name}:1' in result.stderr
    assert 'compile-only-secret' not in result.stdout + result.stderr
    assert 'compile_only_secret' not in result.stdout + result.stderr
    assert not list(package.rglob('*.pyc'))


@pytest.mark.parametrize('name', ['emeris_live/service.py', 'scripts/release.py', 'deploy/verify.py'])
def test_compiler_check_does_not_execute_or_write_bytecode(package, name):
    path = package / name
    path.parent.mkdir(exist_ok=True)
    path.write_text('raise RuntimeError("static compilation must never execute this")\n')
    result = invoke(package, 'check.py', '--suite', 'static', env=source.test_environment())
    assert result.returncode == 0, result.stdout + result.stderr
    assert not list(package.rglob('*.pyc'))


def test_browser_environment_keeps_runtime_paths_but_never_live_opt_ins(monkeypatch):
    monkeypatch.setenv('PLAYWRIGHT_MODULE', '/authorized/runtime/playwright')
    monkeypatch.setenv('CHROMIUM_EXECUTABLE', '/authorized/runtime/chromium')
    monkeypatch.setenv('ARENA_LIVE_BASE', 'https://never.invalid')
    monkeypatch.setenv('ARENA_LAB_LIVE_BASE', 'https://never.invalid')
    monkeypatch.setenv('ARENA_LAB_STORAGE_STATE', '/never/session')
    env = source.test_environment(browser=True)
    assert env['PLAYWRIGHT_MODULE'] == '/authorized/runtime/playwright'
    assert env['CHROMIUM_EXECUTABLE'] == '/authorized/runtime/chromium'
    assert not any(name in env for name in ('ARENA_LIVE_BASE', 'ARENA_LAB_LIVE_BASE', 'ARENA_LAB_STORAGE_STATE'))


def test_check_module_import_has_no_execution_side_effects(package):
    spec = importlib.util.spec_from_file_location('isolated_development_check', package / 'scripts' / 'check.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.BROWSER_TESTS == checks.BROWSER_TESTS
    assert not (package / 'verification').exists()
