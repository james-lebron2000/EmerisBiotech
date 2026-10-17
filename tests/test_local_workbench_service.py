"""Managed local launchd contracts; tests never call the actual service manager."""
import importlib.util
from pathlib import Path
import plistlib
import subprocess
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location('local_workbench_service',
        Path(__file__).resolve().parents[1] / 'scripts/local_workbench_service.py')
service = importlib.util.module_from_spec(spec)
spec.loader.exec_module(service)


@pytest.fixture(autouse=True)
def prevent_real_service_operations(monkeypatch):
    monkeypatch.setattr(service, '_run', lambda *_: pytest.fail('Actual launchctl is forbidden in isolated tests'))
    monkeypatch.setattr(service.subprocess, 'run', lambda *_a, **_k: pytest.fail('Actual subprocess is forbidden in isolated tests'))
    monkeypatch.setattr(service.urllib.request, 'urlopen', lambda *_a, **_k: pytest.fail('Unexpected live HTTP request'))


def launch_output(configuration, destination, pid=123):
    return ('\tpath = ' + str(destination) + '\n\tprogram = ' + configuration['ProgramArguments'][0]
            + '\n\tworking directory = ' + configuration['WorkingDirectory'] + '\n\targuments = {\n'
            + ''.join('\t\t' + value + '\n' for value in configuration['ProgramArguments'])
            + '\t}\n\tpid = ' + str(pid) + '\n')


@pytest.fixture
def prepared(tmp_path):
    workspace = tmp_path / 'work space'
    (workspace / 'src/vbt').mkdir(parents=True)
    (workspace / 'src/vbt/workbench.py').write_text('# synthetic service contract only')
    venv = tmp_path / 'venv'
    (venv / 'bin').mkdir(parents=True)
    (venv / 'bin/python').write_text('# explicit fixture; never executed')
    (venv / 'lib/python3.11/site-packages').mkdir(parents=True)
    state = tmp_path / 'state'
    return workspace, state, venv / 'bin/python'


def test_plist_uses_argument_array_clean_environment_and_recovery(prepared):
    result = service.specification(*prepared)
    assert plistlib.loads(plistlib.dumps(result)) == result
    args = result['ProgramArguments']
    assert args[:2] == ['/usr/bin/env', '-i']
    assert 'HOME=' in args[2] and 'PATH=' in args[3]
    assert args[-4:-1] == [str(prepared[2]), '-S', '-c']
    assert str(prepared[0]) in args[-1] and str(prepared[1]) in args[-1]
    assert result['KeepAlive'] is True and result['RunAtLoad'] is True
    assert result['Umask'] == 0o077 and service._managed(result)
    assert all('API_KEY' not in value for value in args)


@pytest.mark.parametrize('port', [0, 80, -1, 65536, True])
def test_invalid_ports_are_rejected(prepared, port):
    with pytest.raises(ValueError):
        service.specification(*prepared, port=port)


def test_removable_or_symlink_state_is_rejected(prepared, tmp_path):
    workspace, _, python = prepared
    with pytest.raises(ValueError):
        service.specification(workspace, '/Volumes/Example/active-state', python)
    target = tmp_path / 'target';target.mkdir()
    alias = tmp_path / 'alias';alias.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError):
        service.specification(workspace, alias, python)


def test_install_bootstraps_only_this_user_label_once(prepared, tmp_path, monkeypatch):
    calls = [];loaded = False
    def launch(args):
        nonlocal loaded
        calls.append(args)
        if args[0] == 'bootstrap':
            loaded = True
        return SimpleNamespace(returncode=0 if loaded else 3,
                               stdout=launch_output(result, destination) if loaded else '')
    monkeypatch.setattr(service, '_run', launch)
    monkeypatch.setattr(service, '_port_available', lambda port: True)
    destination = tmp_path / 'LaunchAgents/service.plist'
    result = service.specification(*prepared)
    service.install(result, destination)
    assert destination.stat().st_mode & 0o777 == 0o600
    assert plistlib.loads(destination.read_bytes()) == result
    assert service.install(result, destination)['already_loaded'] is True
    assert sum(args[0] == 'bootstrap' for args in calls) == 1
    assert all(args[0] in ('print', 'bootstrap') for args in calls)


def test_unmanaged_plist_is_not_overwritten(prepared, tmp_path, monkeypatch):
    destination = tmp_path / 'other.plist'
    original = plistlib.dumps({'Label': 'unrelated.application'})
    destination.write_bytes(original)
    monkeypatch.setattr(service, '_run', lambda args: pytest.fail('Must not stop or inspect unrelated service'))
    with pytest.raises(ValueError):
        service.install(service.specification(*prepared), destination)
    assert destination.read_bytes() == original


def test_occupied_port_is_not_taken_over(prepared, tmp_path, monkeypatch):
    calls = []
    def launch(args):
        calls.append(args);return SimpleNamespace(returncode=3)
    monkeypatch.setattr(service, '_run', launch)
    monkeypatch.setattr(service, '_port_available', lambda port: False)
    destination = tmp_path / 'not-created.plist'
    with pytest.raises(ValueError, match='already in use'):
        service.install(service.specification(*prepared), destination)
    assert not destination.exists() and all(args[0] == 'print' for args in calls)


def test_status_requires_matching_loaded_pid_and_owned_application_response(prepared, tmp_path, monkeypatch):
    destination = tmp_path / 'service.plist'
    configuration = service.specification(*prepared)
    destination.write_bytes(plistlib.dumps(configuration))
    monkeypatch.setattr(service, '_run', lambda args: SimpleNamespace(returncode=0, stdout=launch_output(configuration, destination)))
    monkeypatch.setattr(service, '_owns_port', lambda pid, port: pid == 123 and port == 18764)
    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, size): return b'<title>Assets - EmerisBiotech</title>'
    monkeypatch.setattr(service.urllib.request, 'urlopen', lambda *args, **kwargs: Response())
    result = service.status(destination=destination)
    assert result['pid'] == 123 and result['http_ready']
    assert result['configuration_matches'] and result['pid_owns_port']
    monkeypatch.setattr(service, '_run', lambda args: SimpleNamespace(returncode=3, stdout=''))
    result = service.status(destination=destination)
    assert not result['loaded'] and not result['http_ready']


@pytest.mark.parametrize('name', ['market.sqlite3', 'workbench.sqlite3-wal', 'market.sqlite3-shm',
                                 'registry.db', 'registry.db-journal',
                                 'workbench-service.stdout.log', 'workbench-service.stderr.log'])
@pytest.mark.parametrize('kind', ['symlink', 'broken_symlink', 'hardlink', 'directory'])
def test_database_side_file_and_log_links_are_rejected(prepared, tmp_path, name, kind):
    workspace, state, python = prepared
    state.mkdir()
    outside = tmp_path / 'unrelated-original'
    outside.write_bytes(b'UNCHANGED unrelated bytes')
    path = state / name
    if kind == 'symlink':
        path.symlink_to(outside)
    elif kind == 'broken_symlink':
        path.symlink_to(tmp_path / 'missing-original')
    elif kind == 'hardlink':
        path.hardlink_to(outside)
    else:
        path.mkdir()
    with pytest.raises(ValueError):
        service.specification(workspace, state, python)
    assert outside.read_bytes() == b'UNCHANGED unrelated bytes'


def test_regular_database_side_files_and_logs_are_accepted(prepared):
    workspace, state, python = prepared
    state.mkdir()
    for name in ('market.sqlite3', 'market.sqlite3-wal', 'market.sqlite3-shm',
                 'workbench-service.stdout.log', 'workbench-service.stderr.log'):
        (state / name).write_bytes(b'Synthetic independent fixture')
    assert service._managed(service.specification(workspace, state, python))


def test_install_rechecks_storage_after_specification(prepared, tmp_path, monkeypatch):
    configuration = service.specification(*prepared)
    prepared[1].mkdir()
    outside = tmp_path / 'unrelated';outside.write_text('preserve')
    (prepared[1] / 'market.sqlite3-wal').hardlink_to(outside)
    calls = []
    monkeypatch.setattr(service, '_run', lambda args: calls.append(args) or SimpleNamespace(returncode=3, stdout=''))
    destination = tmp_path / 'not-created.plist'
    with pytest.raises(ValueError):
        service.install(configuration, destination)
    assert not destination.exists() and outside.read_text() == 'preserve'
    assert all(args[0] == 'print' for args in calls)


@pytest.mark.parametrize('value', [None, [], ['x'], 'text', 42, True,
                                  {'Label': service.LABEL, 'ProgramArguments': [1] * 9}])
def test_managed_shape_is_total_and_rejects_non_objects(value):
    assert service._managed(value) is False


@pytest.mark.parametrize('raw', [b'not a plist', b'<?xml version="1.0"?><plist><dict>',
                                plistlib.dumps([]), plistlib.dumps('text'),
                                plistlib.dumps({'Label': service.LABEL}), b'x' * 64_000])
def test_malformed_plist_status_fails_closed_and_install_does_not_replace(prepared, tmp_path, monkeypatch, raw):
    destination = tmp_path / 'service.plist'
    destination.write_bytes(raw)
    monkeypatch.setattr(service, '_run', lambda args: SimpleNamespace(returncode=0, stdout='\tpid = 123\n'))
    result = service.status(destination=destination)
    assert result['loaded'] and not result['configuration_matches']
    assert not result['pid_owns_port'] and not result['http_ready']
    with pytest.raises(ValueError):
        service.install(service.specification(*prepared), destination)
    assert destination.read_bytes() == raw


@pytest.mark.parametrize('mutation', ['program', 'workspace', 'plist_path', 'arguments', 'duplicate_program', 'duplicate_arguments'])
def test_loaded_configuration_mismatch_cannot_install_or_become_ready(prepared, tmp_path, monkeypatch, mutation):
    configuration = service.specification(*prepared)
    destination = tmp_path / 'service.plist'
    original = plistlib.dumps(configuration);destination.write_bytes(original)
    output = launch_output(configuration, destination)
    if mutation == 'program':
        output = output.replace('program = /usr/bin/env', 'program = /bin/sh')
    elif mutation == 'workspace':
        output = output.replace('working directory = ' + configuration['WorkingDirectory'], 'working directory = /unrelated')
    elif mutation == 'plist_path':
        output = output.replace('path = ' + str(destination), 'path = /unrelated.plist')
    elif mutation == 'arguments':
        output = output.replace('port=18764)', 'port=18765)')
    elif mutation == 'duplicate_program':
        output += '\tprogram = /usr/bin/env\n'
    else:
        output += '\targuments = {\n x\n\t}\n'
    calls = []
    monkeypatch.setattr(service, '_run', lambda args: calls.append(args) or SimpleNamespace(returncode=0, stdout=output))
    result = service.status(destination=destination)
    assert result['loaded'] and not result['configuration_matches'] and not result['http_ready']
    with pytest.raises(ValueError, match='Loaded service arguments differ'):
        service.install(configuration, destination)
    assert destination.read_bytes() == original and all(args[0] == 'print' for args in calls)


def test_pid_not_owning_port_never_probes_or_reports_unrelated_http_ready(prepared, tmp_path, monkeypatch):
    configuration = service.specification(*prepared)
    destination = tmp_path / 'service.plist';destination.write_bytes(plistlib.dumps(configuration))
    monkeypatch.setattr(service, '_run', lambda args: SimpleNamespace(returncode=0, stdout=launch_output(configuration, destination)))
    monkeypatch.setattr(service, '_owns_port', lambda *_: False)
    result = service.status(destination=destination)
    assert result['loaded'] and result['configuration_matches'] and result['pid'] == 123
    assert not result['pid_owns_port'] and not result['http_ready']


@pytest.mark.parametrize('suffix', ['\tpid = 0\n', '\tpid = -123\n', '\tpid = 456\n', ''])
def test_missing_or_ambiguous_loaded_pid_never_checks_http(prepared, tmp_path, monkeypatch, suffix):
    configuration = service.specification(*prepared)
    destination = tmp_path / 'service.plist';destination.write_bytes(plistlib.dumps(configuration))
    output = launch_output(configuration, destination)
    output = output + suffix if suffix.startswith('\tpid = 456') else output.replace('\tpid = 123\n', suffix)
    monkeypatch.setattr(service, '_run', lambda args: SimpleNamespace(returncode=0, stdout=output))
    monkeypatch.setattr(service, '_owns_port', lambda *_: pytest.fail('Unproven PID must not query socket ownership'))
    result = service.status(destination=destination)
    assert result['pid'] is None and not result['http_ready']


def test_status_port_must_match_the_installed_configuration(prepared, tmp_path, monkeypatch):
    configuration = service.specification(*prepared)
    destination = tmp_path / 'service.plist';destination.write_bytes(plistlib.dumps(configuration))
    monkeypatch.setattr(service, '_run', lambda args: SimpleNamespace(returncode=0, stdout=launch_output(configuration, destination)))
    monkeypatch.setattr(service, '_owns_port', lambda *_: pytest.fail('Other configured port must not be probed'))
    assert not service.status(port=18765, destination=destination)['configuration_matches']


@pytest.mark.parametrize('http_status,body', [(503, b'EmerisBiotech</title>'), (200, b'<title>Other application</title>')])
def test_owned_port_without_the_actual_application_response_is_not_ready(prepared, tmp_path, monkeypatch, http_status, body):
    configuration = service.specification(*prepared)
    destination = tmp_path / 'service.plist';destination.write_bytes(plistlib.dumps(configuration))
    monkeypatch.setattr(service, '_run', lambda args: SimpleNamespace(returncode=0, stdout=launch_output(configuration, destination)))
    monkeypatch.setattr(service, '_owns_port', lambda *_: True)
    class Response:
        status = http_status
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def read(self, _): return body
    monkeypatch.setattr(service.urllib.request, 'urlopen', lambda *_a, **_k: Response())
    result = service.status(destination=destination)
    assert result['configuration_matches'] and result['pid_owns_port'] and not result['http_ready']


@pytest.mark.parametrize('kind', ['symlink', 'hardlink'])
def test_linked_launchagent_is_neither_trusted_nor_replaced(prepared, tmp_path, monkeypatch, kind):
    configuration = service.specification(*prepared)
    original = tmp_path / 'original.plist';original.write_bytes(plistlib.dumps(configuration))
    destination = tmp_path / 'linked.plist'
    if kind == 'symlink':
        destination.symlink_to(original)
    else:
        destination.hardlink_to(original)
    monkeypatch.setattr(service, '_run', lambda args: SimpleNamespace(returncode=0, stdout=launch_output(configuration, destination)))
    assert not service.status(destination=destination)['configuration_matches']
    with pytest.raises(ValueError):
        service.install(configuration, destination)
    assert original.read_bytes() == plistlib.dumps(configuration)


def test_new_launchagent_cannot_be_written_through_a_linked_parent(prepared, tmp_path):
    real = tmp_path / 'real';real.mkdir()
    alias = tmp_path / 'alias';alias.symlink_to(real, target_is_directory=True)
    with pytest.raises(ValueError):
        service.install(service.specification(*prepared), alias / 'new.plist')
    assert list(real.iterdir()) == []


def test_status_rechecks_storage_links_introduced_after_install(prepared, tmp_path, monkeypatch):
    configuration = service.specification(*prepared)
    prepared[1].mkdir()
    outside = tmp_path / 'preserved';outside.write_bytes(b'unchanged')
    (prepared[1] / 'workbench-service.stderr.log').symlink_to(outside)
    destination = tmp_path / 'service.plist';destination.write_bytes(plistlib.dumps(configuration))
    monkeypatch.setattr(service, '_run', lambda args: SimpleNamespace(returncode=0, stdout=launch_output(configuration, destination)))
    result = service.status(destination=destination)
    assert result['loaded'] and not result['configuration_matches'] and not result['http_ready']
    assert outside.read_bytes() == b'unchanged'


@pytest.mark.parametrize('output,returncode,expected', [('p123\n', 0, True), ('p456\n', 0, False),
                                                      ('p123\np456\n', 0, False), ('', 0, False), ('p123\n', 1, False)])
def test_lsof_proves_only_the_requested_pid_owns_the_port(monkeypatch, output, returncode, expected):
    calls = []
    monkeypatch.setattr(service.subprocess, 'run', lambda args, **kwargs: calls.append(args) or SimpleNamespace(returncode=returncode, stdout=output))
    assert service._owns_port(123, 18764) is expected
    assert calls[0] == ['/usr/sbin/lsof', '-nP', '-a', '-p', '123', '-iTCP:18764', '-sTCP:LISTEN', '-Fp']


@pytest.mark.parametrize('error', [OSError('synthetic missing lsof'), subprocess.TimeoutExpired('synthetic lsof', 10)])
def test_lsof_failure_is_not_port_ownership(monkeypatch, error):
    def failure(*_a, **_k):
        raise error
    monkeypatch.setattr(service.subprocess, 'run', failure)
    assert service._owns_port(123, 18764) is False


@pytest.mark.parametrize('error', [OSError('synthetic launchctl unavailable'), subprocess.TimeoutExpired('synthetic launchctl', 30)])
def test_status_manager_failure_is_not_a_ready_service(tmp_path, monkeypatch, error):
    def failure(*_):
        raise error
    monkeypatch.setattr(service, '_run', failure)
    result = service.status(destination=tmp_path / 'absent.plist')
    assert not result['loaded'] and result['pid'] is None and not result['http_ready']


@pytest.mark.parametrize('action', ['restart', 'uninstall'])
@pytest.mark.parametrize('kind', ['malformed', 'non_object', 'loaded_mismatch'])
def test_cli_destructive_actions_refuse_malformed_or_mismatched_configuration(prepared, tmp_path, monkeypatch, action, kind):
    home = tmp_path / 'home'
    destination = home / 'Library/LaunchAgents' / (service.LABEL + '.plist')
    destination.parent.mkdir(parents=True)
    monkeypatch.setattr(service.Path, 'home', lambda: home)
    monkeypatch.setattr(service.sys, 'platform', 'darwin')
    configuration = service.specification(*prepared)
    raw = b'malformed' if kind == 'malformed' else plistlib.dumps([]) if kind == 'non_object' else plistlib.dumps(configuration)
    destination.write_bytes(raw)
    calls = []
    monkeypatch.setattr(service, '_run', lambda args: calls.append(args) or SimpleNamespace(returncode=0, stdout=launch_output(configuration, destination).replace('program = /usr/bin/env', 'program = /bin/sh')))
    with pytest.raises(ValueError):
        service.main([action])
    assert destination.read_bytes() == raw and all(args[0] == 'print' for args in calls)
