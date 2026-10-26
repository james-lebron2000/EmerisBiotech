"""Fixed-base 1.2.9 code and existing-site deployment; database backups are never rollback inputs."""
import argparse
from datetime import datetime, timezone
import fcntl
import gzip
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tarfile
import time
import urllib.request

INSTALL_ROOT = Path('/opt/emeris-live')
BASE = INSTALL_ROOT / 'releases/20261024-arena08'
TARGET = INSTALL_ROOT / 'releases/20261026-arena10'
CURRENT = INSTALL_ROOT / 'current'
DATA = Path('/var/lib/emeris-live')
PYTHON = '/opt/emeris-live/venv/bin/python'
BASE_COMMIT = '4369a32e87b837a1e09a73e367540cf11a5ba872'
BASE_VERSION = '1.2.7'
VERSION = '1.2.9'
ROOT_UID = 0
NGINX_SITE = Path('/etc/nginx/sites-available/emeris-live')
CONFIGS = (Path('/etc/emeris-live.env'), Path('/etc/systemd/system/emeris-live.service'),
           Path('/etc/emeris-arena.env'), Path('/etc/emeris-arena-models-v2.json'),
           Path('/etc/systemd/system/emeris-live.service.d/arena.conf'))
PRIVATE_CONFIGS = frozenset((Path('/etc/emeris-live.env'), Path('/etc/emeris-arena.env')))
FILES = frozenset((
    'emeris_live/main.py', 'emeris_live/patent_evidence.py', 'emeris_live/patent_identity.py', 'emeris_live/arena_metrics.py',
    'emeris_live/arena_providers.py',
    'emeris_live/arena_api.py', 'emeris_live/arena_public.py', 'emeris_live/arena_store.py', 'emeris_live/arena_report.py',
    'emeris_live/arena_review_auth.py', 'emeris_live/arena_review_api.py', 'emeris_live/arena_acquired.py',
    'emeris_live/arena_result_assurance.py', 'scripts/arena_scientific_acceptance.py',
    'emeris_live/official_intake.py', 'emeris_live/intake_api.py', 'scripts/ingest_patents.py',
    'scripts/arena_learning_report.py', 'scripts/arena_outcome_inbox.py', 'scripts/arena_result_review.py',
    'scripts/export_public_campaign.py', 'scripts/public_snapshot_proof.py', 'scripts/install_patent_review_bundle.py',
    'scripts/prepare_patent_pair.py', 'docs/PATENT_PAIR_PILOT.md',
    'docs/records/triumph5-patent-pair-20261027/run-summary.json',
    'scripts/arena_reviewers.py', 'scripts/configure_arena_frontends.py', 'scripts/deploy_result_reviews.py',
    'scripts/deploy_review_workspace.py', 'static/arena.js', 'static/arena-public.js', 'static/arena-home.js', 'static/arena-review.html',
    'static/arena-review.js', 'static/arena-review.css', 'docs/OPEN_FORECAST_RELIABILITY_1_2_8.md',
    'docs/RESULT_REVIEW_WORKSPACE_1_2_9.md', 'docs/SCIENTIFIC_RESULT_ACCEPTANCE.md',
    'patent_data/catalog.json', 'patent_data/README.md',
    'patent_data/sources/WO2019125938A1.identity-review.json',
    'patent_data/sources/WO2019125938A1.retatrutide-ex12-v1.json'))
MAX_ARCHIVE = 8 * 1024 * 1024
MAX_MEMBER = 2 * 1024 * 1024
MAX_EXPANDED = 16 * 1024 * 1024


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def strict_json(raw):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError('Duplicate JSON field')
            value[key] = item
        return value
    def invalid_constant(value):
        raise ValueError('Non-finite JSON')
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid_constant)


def regular(path):
    path = Path(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError('Expected a single regular file')
    return path


def bounded_bytes(path, maximum):
    path = regular(path)
    with path.open('rb') as stream:
        raw = stream.read(maximum + 1)
    if len(raw) > maximum:
        raise ValueError('File exceeds deployment size limit')
    return raw


def read_archive(path, expected_hash, commit):
    if not re.fullmatch(r'[a-f0-9]{64}', expected_hash) or not re.fullmatch(r'[a-f0-9]{40}', commit):
        raise ValueError('Pinned transport digest and full commit required')
    raw = bounded_bytes(path, MAX_ARCHIVE)
    if digest(raw) != expected_hash:
        raise ValueError('Pinned code transport hash differs')
    # Bound expansion before tarfile interprets GNU/PAX extension payloads.
    with gzip.GzipFile(fileobj=io.BytesIO(raw)) as compressed:
        expanded_tar = compressed.read(MAX_EXPANDED + 128 * 1024 + 1)
    if len(expanded_tar) > MAX_EXPANDED + 128 * 1024:
        raise ValueError('Expanded code transport exceeds size limit')
    files = {}
    expanded = 0
    with tarfile.open(fileobj=io.BytesIO(expanded_tar), mode='r:') as archive:
        for member in archive:
            expanded += member.size
            if (member.name not in FILES | {'CODE_RELEASE.json'} or member.name in files
                    or str(PurePosixPath(member.name)) != member.name or not member.isfile()
                    or member.size < 0 or member.size > MAX_MEMBER or expanded > MAX_EXPANDED
                    or member.sparse is not None or member.pax_headers):
                raise ValueError('Unexpected code transport member')
            files[member.name] = archive.extractfile(member).read(MAX_MEMBER + 1)
            if len(files[member.name]) != member.size:
                raise ValueError('Incomplete code transport member')
    meta = strict_json(files.pop('CODE_RELEASE.json', b'{}'))
    if (set(files) != FILES or not isinstance(meta, dict) or set(meta) != {'version', 'git_commit', 'files'}
            or meta['version'] != VERSION or meta['git_commit'] != commit
            or meta['files'] != {name: digest(value) for name, value in files.items()}):
        raise ValueError('Committed code manifest differs')
    return files


def verify_manifest(root):
    """Validate base bytes before executing its reusable deployment helpers."""
    if not root.is_dir() or root.is_symlink() or any(path.is_symlink() for path in root.rglob('*')):
        raise ValueError('Release contains links or is missing')
    manifest = strict_json(regular(root / 'RELEASE_MANIFEST.json').read_bytes())
    seen = set()
    for item in manifest['files']:
        name = item['path']
        path = PurePosixPath(name)
        if (str(path) != name or path.is_absolute() or '..' in path.parts or name in seen
                or name == 'RELEASE_MANIFEST.json' or '\\' in name):
            raise ValueError('Invalid release manifest path')
        seen.add(name)
        raw = regular(root / name).read_bytes()
        if digest(raw) != item['sha256'] or ('bytes' in item and item['bytes'] != len(raw)):
            raise ValueError('Release manifest hash differs')
    return manifest


def helpers():
    spec = importlib.util.spec_from_file_location('review_workspace_deploy_helpers', BASE / 'scripts/upgrade_arena.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def config_state(path):
    raw = regular(path).read_bytes()
    info = path.stat()
    mode = stat.S_IMODE(info.st_mode)
    if info.st_uid != ROOT_UID or mode & 0o022 or (path in PRIVATE_CONFIGS and mode != 0o600):
        raise ValueError('External configuration ownership or permissions differ')
    return {'sha256': digest(raw), 'mode': mode, 'uid': info.st_uid, 'gid': info.st_gid}


def child_environment():
    return {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8', 'PYTHONDONTWRITEBYTECODE': '1',
            'EMERIS_ENABLE_USER_LLM': 'false', 'EMERIS_ENABLE_ARENA_LLM': 'false'}


# Imports may read source/dependencies, but may not create state, connect or start jobs.
IMPORT_GUARD = """
import os, sys
def guard(event, args):
    if event == 'open':
        mode, flags = args[1], args[2]
        if (isinstance(mode, str) and any(c in mode for c in 'wax+')) or (isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)):
            raise RuntimeError('Import attempted a write')
    if event.startswith('socket.') or event in {'sqlite3.connect', 'subprocess.Popen', 'os.system', 'os.mkdir', 'os.remove', 'os.rename', 'os.rmdir', 'os.symlink', 'os.link', 'os.truncate'}:
        raise RuntimeError('Import attempted runtime activity')
sys.addaudithook(guard)
sys.path.insert(0, os.getcwd())
"""


def import_smoke():
    modules = sorted(name[:-3].replace('/', '.') for name in FILES if name.startswith('emeris_live/'))
    program = IMPORT_GUARD + """
import importlib
from pathlib import Path
for name in MODULES:
    module = importlib.import_module(name)
    if not Path(module.__file__).resolve().is_relative_to(Path.cwd().resolve()):
        raise RuntimeError('Import escaped the new release')
from emeris_live.main import create_app
if not callable(create_app):
    raise RuntimeError('Missing app factory')
""".replace('MODULES', repr(modules))
    subprocess.run([PYTHON, '-B', '-I', '-c', program], cwd=TARGET, env=child_environment(),
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)


def render_nginx(expected_renderer_hash, old_site):
    if not isinstance(old_site, bytes) or not old_site or len(old_site) > 262144:
        raise ValueError('Invalid existing Nginx configuration')
    source = TARGET / 'scripts/configure_arena_frontends.py'
    if digest(regular(source).read_bytes()) != expected_renderer_hash:
        raise ValueError('Transported Nginx renderer changed')
    program = IMPORT_GUARD + """
import importlib.util
spec = importlib.util.spec_from_file_location('verified_frontend_renderer', 'scripts/configure_arena_frontends.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
original = sys.stdin.buffer.read(262145)
if not original or len(original) > 262144:
    raise ValueError('Invalid existing Nginx configuration')
raw = module.upgrade_review_routes(original)
if not isinstance(raw, bytes) or not raw or len(raw) > 262144:
    raise ValueError('Invalid rendered Nginx configuration')
sys.stdout.buffer.write(raw)
"""
    result = subprocess.run([PYTHON, '-B', '-I', '-c', program], cwd=TARGET, env=child_environment(),
                            input=old_site, check=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=60)
    if not result.stdout or len(result.stdout) > 262144:
        raise ValueError('Invalid rendered Nginx configuration')
    return result.stdout


def switch(destination):
    marker = CURRENT.with_name('current-arena10-next')
    if marker.exists() or marker.is_symlink():
        raise ValueError('Concurrent release marker')
    marker.symlink_to(destination)
    marker.replace(CURRENT)


def replace_site(raw, expected, d):
    """Same-directory exclusive staging plus replace preserves the known file metadata."""
    if config_state(NGINX_SITE) != expected:
        raise ValueError('Nginx site changed before replacement')
    marker = NGINX_SITE.with_name(NGINX_SITE.name + '.arena10-next')
    d.exclusive_write(marker, raw, expected['mode'])
    os.chown(marker, expected['uid'], expected['gid'])
    os.chmod(marker, expected['mode'])
    if config_state(NGINX_SITE) != expected:
        raise ValueError('Nginx site changed during replacement')
    marker.replace(NGINX_SITE)


def restart():
    subprocess.run(['systemctl', 'restart', 'emeris-live'], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def nginx_test():
    subprocess.run(['nginx', '-t'], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def nginx_reload():
    subprocess.run(['systemctl', 'reload', 'nginx'], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def await_health(version):
    for attempt in range(30):
        try:
            with urllib.request.urlopen('http://127.0.0.1:19320/api/health', timeout=2) as response:
                if json.load(response).get('version') == version:
                    return
        except (OSError, ValueError):
            pass
        time.sleep(1)
    raise RuntimeError('Expected release health did not become ready')


def deploy(archive, expected_hash, commit, expected_nginx_sha256,
           patent_evidence_archive=None, patent_evidence_sha256=None):
    files = read_archive(archive, expected_hash, commit)
    if files['scripts/deploy_review_workspace.py'] != regular(Path(__file__)).read_bytes():
        raise ValueError('Transported deployment entry point differs')
    if not re.fullmatch(r'[a-f0-9]{64}', expected_nginx_sha256):
        raise ValueError('Explicit pinned Nginx digest required')
    catalog = strict_json(files['patent_data/catalog.json'])
    needs_originals = any(source.get('original_relative_path')
        for item in catalog.get('items', []) for source in
        item.get('research_review', {}).get('drug_mapping', {}).get('sources', []))
    if needs_originals and (patent_evidence_archive is None
            or not re.fullmatch(r'[a-f0-9]{64}', str(patent_evidence_sha256 or ''))):
        raise ValueError('The new patent review requires its separately pinned original-evidence archive')
    lock_path = INSTALL_ROOT / '.openforecast-deploy.lock'
    lock_fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(lock_fd, 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        backup = INSTALL_ROOT / 'backups/20261026-arena10'
        receipt_path = INSTALL_ROOT / 'arena10-receipt.json'
        reserved = (TARGET, backup, receipt_path, CURRENT.with_name('current-arena10-next'),
                    NGINX_SITE.with_name(NGINX_SITE.name + '.arena10-next'))
        if (not CURRENT.is_symlink() or CURRENT.resolve() != BASE
                or any(path.exists() or path.is_symlink() for path in reserved)):
            raise ValueError('Expected fixed base and unused deployment paths')
        manifest = verify_manifest(BASE)
        if manifest.get('version') != BASE_VERSION or manifest.get('git_commit') != BASE_COMMIT:
            raise ValueError('Unexpected live code version or commit')
        if not {'scripts/upgrade_arena.py', 'scripts/deploy_arena.py'} <= {item['path'] for item in manifest['files']}:
            raise ValueError('Deployment helpers are not bound to the base manifest')
        base_manifest_hash = digest((BASE / 'RELEASE_MANIFEST.json').read_bytes())
        site_state = config_state(NGINX_SITE)
        if site_state['sha256'] != expected_nginx_sha256:
            raise ValueError('Pinned Nginx configuration hash differs')
        old_site = NGINX_SITE.read_bytes()
        configs = {str(path): config_state(path) for path in CONFIGS}
        h = helpers()
        d = h.d
        events = h.read_events(DATA / 'arena.sqlite3')
        rows = d.sealed_rows(DATA / 'workbench.sqlite3')
        backup.mkdir(parents=True, mode=0o700, exist_ok=False)
        for name in ('arena.sqlite3', 'workbench.sqlite3'):
            d.backup_sqlite(DATA / name, backup / name)
        d.exclusive_write(backup / 'base-manifest.json', (BASE / 'RELEASE_MANIFEST.json').read_bytes())
        d.exclusive_write(backup / 'nginx-site.conf', old_site)
        d.exclusive_write(backup / 'configuration-state.json', json.dumps({'site': site_state, 'unchanged': configs}).encode())
        shutil.copytree(BASE, TARGET, ignore=shutil.ignore_patterns('__pycache__', '.pytest_cache'))
        records = {item['path']: item for item in manifest['files']}
        for name, raw in files.items():
            if name.endswith('.py'):
                compile(raw, name, 'exec')
            path = TARGET / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.parent.chmod(0o755)
            path.write_bytes(raw)
            path.chmod(0o644)
            records[name] = {'path': name, 'sha256': digest(raw), 'bytes': len(raw)}
        source = TARGET / 'scripts/install_patent_review_bundle.py'
        if digest(regular(source).read_bytes()) != digest(files['scripts/install_patent_review_bundle.py']):
            raise ValueError('Patent evidence installer changed')
        spec = importlib.util.spec_from_file_location('verified_patent_evidence_installer', source)
        installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)
        evidence_records = installer.stage(TARGET, patent_evidence_archive, patent_evidence_sha256)
        for entry in evidence_records:
            records[entry['path']] = entry
        updated = {**manifest, 'version': VERSION, 'git_commit': commit, 'parent_release': str(BASE),
                   'created_at_utc': datetime.now(timezone.utc).isoformat(),
                   'files': [records[name] for name in sorted(records)]}
        (TARGET / 'RELEASE_MANIFEST.json').write_text(json.dumps(updated, indent=2) + '\n')
        verify_manifest(TARGET)
        import_smoke()
        new_site = render_nginx(digest(files['scripts/configure_arena_frontends.py']), old_site)
        new_site_state = {**site_state, 'sha256': digest(new_site)}

        def preserved(expected_site):
            if {str(path): config_state(path) for path in CONFIGS} != configs:
                raise ValueError('External configuration changed')
            if config_state(NGINX_SITE) != expected_site:
                raise ValueError('Nginx configuration changed')
            h.require_prefix(events, h.read_events(DATA / 'arena.sqlite3'))
            d.check_sealed_rows(rows, d.sealed_rows(DATA / 'workbench.sqlite3'))
            verify_manifest(BASE)
            verify_manifest(TARGET)
            if digest((BASE / 'RELEASE_MANIFEST.json').read_bytes()) != base_manifest_hash:
                raise ValueError('Base release manifest changed')

        site_changed = False
        switched = False
        try:
            preserved(site_state)
            if CURRENT.resolve() != BASE:
                raise ValueError('Current release changed before switch')
            replace_site(new_site, site_state, d)
            site_changed = True
            nginx_test()
            switch(TARGET)
            switched = True
            restart()
            await_health(VERSION)
            nginx_reload()
            preserved(new_site_state)
            if CURRENT.resolve() != TARGET:
                raise ValueError('Current release changed after switch')
            after = h.read_events(DATA / 'arena.sqlite3')
            receipt = {'status': 'deployed', 'version': VERSION, 'git_commit': commit, 'release': str(TARGET),
                       'previous_release': str(BASE), 'backup': str(backup), 'database_imports': 0,
                       'arena_events_before': len(events), 'arena_events_after': len(after),
                       'arena_previous_head': h.head(events), 'arena_current_head': h.head(after),
                       'old_arena_events_preserved': True, 'old_sealed_rows_preserved': True,
                       'external_configurations_unchanged': True, 'nginx_before_sha256': expected_nginx_sha256,
                       'nginx_after_sha256': new_site_state['sha256'], 'code_transport_sha256': expected_hash,
                       'patent_evidence_transport_sha256': patent_evidence_sha256,
                       'patent_original_files_checked': len(evidence_records),
                       'import_smoke': 'passed_without_app_construction',
                       'scope': 'engineering deployment only; no reviewer enrollment or scientific validation',
                       'at': datetime.now(timezone.utc).isoformat()}
            d.exclusive_write(receipt_path, json.dumps(receipt, indent=2).encode())
            return receipt
        except BaseException as failure:
            # Forensic backups stay untouched. Concurrent user writes must survive rollback.
            rollback_errors = []
            app_restored = not switched
            site_restored = not site_changed
            try:
                if switched:
                    if not CURRENT.is_symlink() or CURRENT.resolve() != TARGET:
                        raise RuntimeError('Current release changed; refusing to overwrite it')
                    switch(BASE)
                    app_restored = True
            except BaseException as error:
                rollback_errors.append(error)
            try:
                if site_changed:
                    replace_site(old_site, new_site_state, d)
                    site_restored = True
            except BaseException as error:
                rollback_errors.append(error)
            try:
                if switched and app_restored:
                    restart()
                    await_health(BASE_VERSION)
            except BaseException as error:
                rollback_errors.append(error)
            try:
                if site_changed and site_restored:
                    nginx_test()
                    nginx_reload()
            except BaseException as error:
                rollback_errors.append(error)
            try:
                preserved(site_state)
            except BaseException as error:
                rollback_errors.append(error)
            if rollback_errors:
                raise RuntimeError('Deployment failed and restoration could not be fully verified') from rollback_errors[0]
            raise failure


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--commit', required=True)
    parser.add_argument('--expected-nginx-sha256', required=True)
    parser.add_argument('--patent-evidence-archive', type=Path)
    parser.add_argument('--patent-evidence-sha256')
    args = parser.parse_args()
    try:
        receipt = deploy(args.archive, args.sha256, args.commit, args.expected_nginx_sha256,
                         args.patent_evidence_archive, args.patent_evidence_sha256)
    except (Exception, KeyboardInterrupt):
        parser.exit(1, 'Deployment failed; inspect local deployment state before retrying. No database restore was performed.\n')
    print(json.dumps(receipt))


if __name__ == '__main__':
    main()
