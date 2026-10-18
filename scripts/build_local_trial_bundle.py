"""Build a small, deterministic source-only trial package from committed Git blobs.

No Docker, dependency installation, network, dataset, database, or running service
is used. The external manifest inventories every archive member; SHA256SUMS
anchors both the archive and manifest without a self-referential manifest hash.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
import tempfile

VERSION = 'local-trial-source-bundle-1'
MAX_TOTAL_BYTES = 10_000_000
MAX_FILE_BYTES = 2_000_000
MAX_FILES = 2000
ROOT_FILES = {'.dockerignore', '.gitignore', 'AGENTS.md', 'LICENSE', 'README.md',
              'pyproject.toml', 'uv.lock', 'setup.sh', 'vbt'}
DIRECTORIES = {'src', 'tests', 'scripts', 'docs', 'examples', 'deploy'}
FORBIDDEN_PARTS = {'artifacts', 'data', 'db', 'env', 'private', 'secrets', 'secret', 'credentials',
    'keys', 'backups', 'output', '.git', '.venv', 'venv', '__pycache__', '.pytest_cache', 'node_modules'}
FORBIDDEN_EXTENSIONS = {'.db', '.sqlite', '.sqlite3', '.pem', '.key', '.p12', '.pfx', '.log',
    '.pyc', '.parquet', '.h5', '.h5ad', '.fastq', '.fq', '.bam', '.cram', '.vcf', '.rds',
    '.rdata', '.pdf', '.zip', '.gz', '.tar', '.csv', '.tsv'}
REQUIRED = {'AGENTS.md', 'LICENSE', 'README.md', 'pyproject.toml', 'uv.lock',
    'src/vbt/__init__.py', 'src/vbt/cli.py', 'src/vbt/workbench.py',
    'deploy/cloud/Dockerfile', 'deploy/cloud/compose.yaml', 'deploy/cloud/Caddyfile',
    'scripts/local_workbench_service.py'}


def _git(repo, *args, input=None):
    env = {k:v for k,v in os.environ.items() if k in ('PATH', 'HOME', 'LANG', 'LC_ALL', 'TMPDIR', 'SYSTEMROOT')}
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull, GIT_OPTIONAL_LOCKS='0')
    run = subprocess.run(['git', '--no-replace-objects', '-c', 'core.fsmonitor=false',
        '-C', str(repo), *args], input=input, capture_output=True, timeout=30, env=env)
    if run.returncode:
        raise ValueError('Git source verification failed: ' + run.stderr.decode(errors='replace')[:500])
    return run.stdout


def _safe_path(path):
    return (isinstance(path, str) and bool(path) and not path.startswith('/')
        and '\\' not in path and ':' not in path and not any(ord(c) < 32 or ord(c) == 127 for c in path)
        and all(part not in ('', '.', '..') for part in path.split('/')))


def _selected(path):
    if not _safe_path(path):
        raise ValueError('Unsafe Git package path')
    parts = PurePosixPath(path).parts
    lower = [part.casefold() for part in parts]
    if (any(part in FORBIDDEN_PARTS or part.startswith('.env') for part in lower)
            or any(part.endswith(('.sqlite3-wal', '.sqlite3-shm', '.sqlite3-journal',
                                  '.db-wal', '.db-shm', '.db-journal')) for part in lower)
            or PurePosixPath(path).suffix.casefold() in FORBIDDEN_EXTENSIONS):
        return False
    return path in ROOT_FILES or parts[0] in DIRECTORIES


def _clean_source(repo):
    changed = _git(repo, 'diff', '--no-ext-diff', '--no-textconv', '--name-only', '-z', 'HEAD')
    untracked = _git(repo, 'ls-files', '--others', '--exclude-standard', '-z')
    relevant = sorted({p.decode('utf-8') for p in (changed + untracked).split(b'\0')
                       if p and _selected(p.decode('utf-8'))})
    if relevant:
        raise ValueError('Uncommitted package source; commit or separate it before building: '
                         + ', '.join(relevant[:12]))


def source_tree(repo, revision, *, expected_modules=61):
    """Read only the selected commit's object tree, never working-tree contents."""
    if not isinstance(revision, str) or not re.fullmatch(r'[0-9a-fA-F]{7,64}', revision):
        raise ValueError('A committed hexadecimal Git revision is required; moving refs are rejected')
    commit = _git(repo, 'rev-parse', '--verify', revision + '^{commit}').decode().strip()
    epoch = int(_git(repo, 'show', '-s', '--format=%ct', commit).decode())
    rows = []
    for entry in _git(repo, 'ls-tree', '-r', '-l', '-z', commit).split(b'\0'):
        if not entry:
            continue
        header, encoded_path = entry.split(b'\t', 1)
        mode, kind, oid, size = header.decode('ascii').split()
        path = encoded_path.decode('utf-8')
        if not _selected(path):
            continue
        if kind != 'blob' or mode not in ('100644', '100755'):
            raise ValueError('Only ordinary Git source files are permitted: ' + path)
        count = int(size)
        if count > MAX_FILE_BYTES:
            raise ValueError('Source member exceeds the size limit: ' + path)
        rows.append({'path': path, 'git_blob_oid': oid, 'bytes': count,
                     'mode': '0755' if mode == '100755' else '0644'})
    rows.sort(key=lambda row: row['path'])
    paths = {row['path'] for row in rows}
    missing = REQUIRED - paths
    if missing:
        raise ValueError('Required source/configuration files missing: ' + ', '.join(sorted(missing)))
    module_count = sum(row['path'].startswith('src/vbt/') and row['path'].endswith('.py') for row in rows)
    if type(expected_modules) is not int or expected_modules < 1 or module_count != expected_modules:
        raise ValueError(f'Expected {expected_modules} vbt modules, found {module_count}')
    if sum(row['bytes'] for row in rows) > MAX_TOTAL_BYTES:
        raise ValueError('Source package exceeds the 10 MB uncompressed limit')
    if len(rows) > MAX_FILES:
        raise ValueError('Source package exceeds the 2000-file limit')
    return commit, epoch, rows


def _blobs(repo, rows):
    oids = sorted({row['git_blob_oid'] for row in rows})
    raw = _git(repo, 'cat-file', '--batch', input=('\n'.join(oids) + '\n').encode())
    cursor = 0
    found = {}
    for oid in oids:
        end = raw.index(b'\n', cursor)
        actual, kind, size = raw[cursor:end].decode().split()
        size = int(size); start = end + 1; stop = start + size
        if actual != oid or kind != 'blob' or raw[stop:stop + 1] != b'\n':
            raise ValueError('Git blob inventory mismatch')
        found[oid] = raw[start:stop]; cursor = stop + 1
        addressed = b'blob ' + str(size).encode() + b'\0' + found[oid]
        if (hashlib.sha1(addressed).hexdigest() if len(oid) == 40 else hashlib.sha256(addressed).hexdigest()) != oid:
            raise ValueError('Git blob bytes do not match their committed object ID')
    if cursor != len(raw):
        raise ValueError('Unexpected trailing Git blob data')
    return found


def _content_gate(path, raw):
    try:
        raw.decode('utf-8')
    except UnicodeError as exc:
        raise ValueError('Non-text source content rejected: ' + path) from exc
    if raw.startswith(b'version https://git-lfs.github.com/spec/v1'):
        raise ValueError('Unmaterialized Git LFS source pointer is not deployable: ' + path)
    if (b'\0' in raw or re.search(rb'(?m)^-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----', raw)
            or re.search(rb'\b(?:sk-[A-Za-z0-9_-]{24,}|ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})\b', raw)):
        raise ValueError('Binary content or credential-like material rejected: ' + path)


def build_bundle(repo, revision, output_dir, *, expected_modules=61):
    repo = Path(repo).resolve()
    _clean_source(repo)
    commit, epoch, rows = source_tree(repo, revision, expected_modules=expected_modules)
    blobs = _blobs(repo, rows)
    prefix = 'EmerisBiotech-source-' + commit[:12]
    for row in rows:
        raw = blobs[row['git_blob_oid']]
        if len(raw) != row['bytes']:
            raise ValueError('Git tree and source blob size differ')
        _content_gate(row['path'], raw)
        row['sha256'] = hashlib.sha256(raw).hexdigest()
        row['archive_path'] = prefix + '/' + row['path']
    manifest = {'schema_version': 1, 'builder_version': VERSION,
        'package_kind': 'committed_source_only', 'source_commit': commit,
        'source_date_epoch': epoch, 'archive_root': prefix, 'file_count': len(rows),
        'source_bytes': sum(row['bytes'] for row in rows),
        'vbt_module_count': sum(row['path'].startswith('src/vbt/') and row['path'].endswith('.py') for row in rows),
        'working_tree_source_clean': True, 'prebuilt_image_included': False,
        'runtime_data_included': False, 'model_keys_required_for_local_ui': False,
        'manifest_scope': 'Every archive member; this external manifest is not inside the archive.',
        'archive_metadata': 'sorted paths; Git executable bit; uid/gid=0; tar mtime=commit epoch; gzip mtime=0',
        'selection': 'tracked text source, tests, configuration, public examples and documentation; runtime/private/data/env/binary files excluded',
        'files': rows}
    manifest_raw = (json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode()
    output = Path(output_dir).resolve(); output.mkdir(parents=True, exist_ok=True)
    targets = [output / (prefix + '.tar.gz'), output / (prefix + '.manifest.json'), output / (prefix + '.SHA256SUMS')]
    if any(path.exists() or path.is_symlink() for path in targets):
        raise ValueError('Immutable bundle output already exists; use an empty output directory')
    # Temporary publication stays under the output filesystem; no source tree is
    # extracted, copied, changed or added to the Git index.
    with tempfile.TemporaryDirectory(prefix='.source-bundle-', dir=output) as temporary:
        temporary = Path(temporary); archive = temporary / targets[0].name
        with archive.open('wb') as handle:
            with gzip.GzipFile(filename='', mode='wb', fileobj=handle, mtime=0, compresslevel=9) as compressed:
                with tarfile.open(fileobj=compressed, mode='w', format=tarfile.PAX_FORMAT) as tar:
                    for row in rows:
                        info = tarfile.TarInfo(row['archive_path']); info.size = row['bytes']
                        info.mode = int(row['mode'], 8); info.mtime = epoch
                        info.uid = info.gid = 0; info.uname = info.gname = ''
                        tar.addfile(info, io.BytesIO(blobs[row['git_blob_oid']]))
        (temporary / targets[1].name).write_bytes(manifest_raw)
        checksums = ''.join(hashlib.sha256((temporary / path.name).read_bytes()).hexdigest() + '  ' + path.name + '\n'
                            for path in targets[:2])
        (temporary / targets[2].name).write_text(checksums, encoding='ascii')
        for path in targets:
            # Hard-link publication fails rather than overwriting concurrent or
            # previously published outputs. Temporary files are cleaned on exit.
            os.link(temporary / path.name, path)
    return {'status': 'source_bundle_created', 'source_commit': commit,
        'archive': str(targets[0]), 'manifest': str(targets[1]), 'checksums': str(targets[2]),
        'archive_sha256': hashlib.sha256(targets[0].read_bytes()).hexdigest(),
        'archive_bytes': targets[0].stat().st_size, 'vbt_module_count': manifest['vbt_module_count'],
        'file_count': len(rows), 'prebuilt_image_included': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--commit', required=True, help='Explicit committed hexadecimal Git revision')
    parser.add_argument('--output-dir', type=Path, default=Path('artifacts/releases/local-trial'))
    parser.add_argument('--expected-vbt-modules', type=int, default=61)
    args = parser.parse_args(argv)
    try:
        result = build_bundle(args.repo, args.commit, args.output_dir, expected_modules=args.expected_vbt_modules)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print(json.dumps({'status': 'blocked', 'error': str(exc)}, ensure_ascii=False)); return 2
    print(json.dumps(result, ensure_ascii=False, indent=2)); return 0


if __name__ == '__main__':
    raise SystemExit(main())
