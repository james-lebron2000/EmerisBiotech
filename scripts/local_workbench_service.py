"""Manage this user's local macOS workbench with launchd, without a shell.

This keeps the local single-user application available after a terminal closes.
It does not publish a site, install system services or change source credentials.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import socket
import subprocess
import sys
import tempfile
import urllib.request

LABEL = 'com.emerisbiotech.workbench'
BASE = Path.home() / 'Library/Application Support/VirtualBiotech'
SAFE_PATH = '/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin'


def _storage(state):
    state = Path(state).absolute()
    if state.resolve() != state:
        raise ValueError('Active state directory cannot use symbolic links')
    if state.exists() and not state.is_dir():
        raise ValueError('Local state must be a directory')
    files = set(state.glob('*.sqlite3*')) | set(state.glob('*.db*'))
    files.update(state / name for name in ('workbench-service.stdout.log', 'workbench-service.stderr.log'))
    for path in files:
        if path.is_symlink() or path.resolve() != path.absolute():
            raise ValueError('Active SQLite files, side files and service logs cannot use symbolic links')
        if path.exists() and (not path.is_file() or path.stat().st_nlink != 1):
            raise ValueError('Active state files and logs must be ordinary independent files')


def specification(workspace, state, python, port=18764):
    workspace, state, python = Path(workspace).absolute(), Path(state).absolute(), Path(python).absolute()
    if state.resolve().is_relative_to('/Volumes'):
        raise ValueError('Active SQLite state must be on the local disk')
    if workspace.resolve() != workspace or state.resolve() != state:
        raise ValueError('Service workspace and state must not use symbolic links')
    if any(ord(char) < 32 for path in (workspace, state, python) for char in str(path)):
        raise ValueError('Control characters in service paths are unsupported')
    _storage(state)
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError('Service port must be in 1024..65535')
    if not (workspace / 'src/vbt/workbench.py').is_file() or not python.is_file():
        raise ValueError('Existing workbench source and Python executable are required')
    packages = python.parent.parent / 'lib/python3.11/site-packages'
    if not packages.is_dir():
        raise ValueError('The existing Python 3.11 virtual environment is required')
    code = ('import sys; from pathlib import Path; '
            f'sys.path[:0]=[{str(workspace / "src")!r},{str(packages)!r}]; '
            'from vbt.workbench import serve; '
            f'serve(Path({str(workspace)!r}),Path({str(state)!r}),port={port})')
    # env -i drops inherited model keys and unrelated process configuration.
    args = ['/usr/bin/env', '-i', f'HOME={Path.home()}', f'PATH={SAFE_PATH}',
            'PYTHONDONTWRITEBYTECODE=1', str(python), '-S', '-c', code]
    return {'Label': LABEL, 'ProgramArguments': args, 'WorkingDirectory': str(workspace),
            'RunAtLoad': True, 'KeepAlive': True, 'ThrottleInterval': 30,
            'StandardOutPath': str(state / 'workbench-service.stdout.log'),
            'StandardErrorPath': str(state / 'workbench-service.stderr.log'),
            'Umask': 0o077}


def _run(args):
    return subprocess.run(['/bin/launchctl', *args], capture_output=True, text=True,
                          timeout=30, env={'HOME': str(Path.home()), 'PATH': SAFE_PATH})


def _domain():
    return f'gui/{os.getuid()}'


def _managed(existing):
    if not isinstance(existing, dict):
        return False
    args = existing.get('ProgramArguments', [])
    code = args[-1] if isinstance(args, list) and args and isinstance(args[-1], str) else ''
    port = re.search(r'port=([0-9]+)\)\s*$', code)
    return (existing.get('Label') == LABEL and isinstance(args, list)
            and len(args) >= 9 and all(isinstance(value, str) and value for value in args)
            and args[:2] == ['/usr/bin/env', '-i'] and args[-3:-1] == ['-S', '-c']
            and all(isinstance(existing.get(key), str) and Path(existing[key]).is_absolute()
                    for key in ('WorkingDirectory', 'StandardOutPath', 'StandardErrorPath'))
            and Path(existing['StandardOutPath']).name == 'workbench-service.stdout.log'
            and Path(existing['StandardErrorPath']).name == 'workbench-service.stderr.log'
            and Path(existing['StandardOutPath']).parent == Path(existing['StandardErrorPath']).parent
            and port is not None and 1024 <= int(port[1]) <= 65535
            and 'from vbt.workbench import serve;' in args[-1])


def _configuration(destination):
    """Bounded, independent regular plist; malformed content is never managed."""
    destination = Path(destination).absolute()
    if (destination.is_symlink() or destination.resolve() != destination or not destination.is_file()
            or destination.stat().st_nlink != 1):
        raise ValueError('Managed LaunchAgent must be an independent regular file without links')
    try:
        with destination.open('rb') as handle:
            raw = handle.read(64_000)
        if not raw or len(raw) >= 64_000:
            raise ValueError('Managed LaunchAgent size is invalid')
        existing = plistlib.loads(raw)
    except Exception as error:
        raise ValueError('Managed LaunchAgent plist is unavailable or malformed') from error
    if not _managed(existing):
        raise ValueError('No matching managed workbench configuration')
    return existing


def _loaded_matches(output, spec, destination):
    if not isinstance(output, str) or not _managed(spec):
        return False
    def field(name):
        matches = re.findall(r'^\s*' + re.escape(name) + r' = (.+)$', output, re.M)
        return matches[0] if len(matches) == 1 else None
    arguments = re.findall(r'^\s*arguments = \{\n(.*?)^\s*\}', output, re.M | re.S)
    return (len(arguments) == 1 and field('program') == spec['ProgramArguments'][0]
            and field('working directory') == spec['WorkingDirectory']
            and field('path') == str(destination)
            and [line.strip() for line in arguments[0].splitlines()] == spec['ProgramArguments'])


def _owns_port(pid, port):
    if type(pid) is not int or pid < 1 or type(port) is not int or not 1024 <= port <= 65535:
        return False
    try:
        result = subprocess.run(['/usr/sbin/lsof', '-nP', '-a', '-p', str(pid),
                                f'-iTCP:{port}', '-sTCP:LISTEN', '-Fp'],
                                capture_output=True, text=True, timeout=10)
        return result.returncode == 0 and {int(value) for value in re.findall(r'^p([0-9]+)$', result.stdout, re.M)} == {pid}
    except (OSError, subprocess.TimeoutExpired):
        return False


def status(port=18764, destination=None):
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError('Service port must be in 1024..65535')
    try:
        process = _run(['print', _domain() + '/' + LABEL])
        output = process.stdout if isinstance(process.stdout, str) else ''
        loaded = process.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        output, loaded = '', False
    pids = re.findall(r'^\s*pid = ([1-9][0-9]*)\s*$', output, re.M)
    pid = int(pids[0]) if len(pids) == 1 else None
    destination = Path(destination) if destination else Path.home() / 'Library/LaunchAgents' / (LABEL + '.plist')
    spec, matching = None, False
    try:
        spec = _configuration(destination)
        _storage(Path(spec['StandardOutPath']).parent)
        matching = (_loaded_matches(output, spec, destination)
                    and int(re.search(r'port=([0-9]+)\)\s*$', spec['ProgramArguments'][-1])[1]) == port)
    except (ValueError, TypeError, OSError, plistlib.InvalidFileException):
        pass
    alive = loaded and pid is not None and matching
    listening = alive and _owns_port(pid, port)
    ready = False
    if listening:
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{port}/assets', timeout=10) as response:
                raw = response.read(200_000)
                ready = response.status == 200 and b'EmerisBiotech</title>' in raw
        except OSError:
            pass
    return {'label': LABEL, 'loaded': loaded,
            'pid': pid, 'http_ready': ready,
            'configuration_matches': bool(matching), 'pid_owns_port': bool(listening),
            'url': f'http://127.0.0.1:{port}/', 'scope': 'local_single_user'}


def _port_available(port):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as handle:
            handle.bind(('127.0.0.1', port))
        return True
    except OSError:
        return False


def install(spec, destination):
    destination = Path(destination).absolute()
    if destination.resolve() != destination:
        raise ValueError('LaunchAgent destination cannot use symbolic links')
    if not _managed(spec):
        raise ValueError('Invalid managed service specification')
    raw = plistlib.dumps(spec, sort_keys=True)
    existing = None
    if destination.exists() or destination.is_symlink():
        existing = _configuration(destination)
        if existing != spec:
            raise ValueError('Existing LaunchAgent differs; inspect it before replacing')
    process = _run(['print', _domain() + '/' + LABEL])
    loaded = process.returncode == 0
    if loaded and existing is None:
        raise ValueError('Service label is already in use without a matching managed configuration')
    if loaded and not _loaded_matches(process.stdout, spec, destination):
        raise ValueError('Loaded service arguments differ from the requested managed instance')
    _storage(Path(spec['StandardOutPath']).parent)
    if not loaded:
        port = int(re.search(r'port=([0-9]+)\)\s*$', spec['ProgramArguments'][-1])[1])
        if not _port_available(port):
            raise ValueError('Loopback port is already in use; no existing server was stopped')
    for path in (Path(spec['WorkingDirectory']), Path(spec['StandardOutPath']).parent):
        path.mkdir(parents=True, exist_ok=True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if existing is None:
        fd, temporary = tempfile.mkstemp(prefix='.emeris-', dir=destination.parent)
        try:
            with os.fdopen(fd, 'wb') as handle:
                handle.write(raw);handle.flush();os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, destination)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    if not loaded:
        result = _run(['bootstrap', _domain(), str(destination)])
        if result.returncode:
            raise RuntimeError('User LaunchAgent bootstrap failed; configuration retained for inspection')
    return {'label': LABEL, 'installed': True, 'already_loaded': loaded,
            'plist': str(destination), 'configuration_sha256': hashlib.sha256(raw).hexdigest()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['install', 'status', 'restart', 'uninstall'])
    parser.add_argument('--workspace', type=Path, default=BASE / 'workbench-runtime')
    parser.add_argument('--state-dir', type=Path)
    parser.add_argument('--python', type=Path, default=BASE / 'venv/bin/python')
    parser.add_argument('--port', type=int, default=18764)
    args = parser.parse_args(argv)
    if sys.platform != 'darwin':
        parser.error('This user service requires macOS launchd; cloud deployment uses the separate compose service')
    destination = Path.home() / 'Library/LaunchAgents' / (LABEL + '.plist')
    if args.action == 'status':
        result = status(args.port)
    elif args.action in ('restart', 'uninstall'):
        installed = _configuration(destination)
        loaded = _run(['print', _domain() + '/' + LABEL])
        if loaded.returncode == 0 and not _loaded_matches(loaded.stdout, installed, destination):
            raise ValueError('Loaded service differs from the installed managed configuration')
        if args.action == 'uninstall':
            result = _run(['bootout', _domain() + '/' + LABEL])
            if result.returncode and _run(['print', _domain() + '/' + LABEL]).returncode == 0:
                raise RuntimeError('Service is still loaded; configuration retained')
            destination.unlink()
            result = {'label': LABEL, 'uninstalled': True}
        else:
            completed = _run(['kickstart', '-k', _domain() + '/' + LABEL])
            if completed.returncode:
                raise RuntimeError('Managed service restart failed')
            result = status(args.port)
    else:
        state = args.state_dir or BASE / hashlib.sha256(str(args.workspace.resolve()).encode()).hexdigest()[:12]
        spec = specification(args.workspace, state, args.python, args.port)
        result = install(spec, destination)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
