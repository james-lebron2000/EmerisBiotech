"""Deploy a committed Open Forecast overlay without replacing existing ledgers.

The credential file is provisioned separately. This operator command changes only
emeris-live's release link and its own systemd drop-in; unrelated units are never
restarted. Failures retain the imported challenge ledger and staged release for
inspection, and restore the prior application link/configuration when possible.
"""
import argparse
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import sqlite3
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import unicodedata
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
BASE = Path('/opt/emeris-live/releases/20261021-editorial01')
TARGET = Path('/opt/emeris-live/releases/20261022-openforecast01')
CURRENT = Path('/opt/emeris-live/current')
DATA = Path('/var/lib/emeris-live')
DROPIN = Path('/etc/systemd/system/emeris-live.service.d/arena.conf')
ENV_FILE = Path('/etc/emeris-arena.env')
PYTHON = '/opt/emeris-live/venv/bin/python'
BASE_COMMIT = 'd17518bc0456e2d24845a7cc3e1c6e4e6bad1416'
VERSION = '1.2.0'
SEALED_PREFIXES = ('predome_data/', 'dashboard_data/', 'trial_data/')
SEALED_TABLES = ('platform_prediction_snapshots', 'platform_prospective_baselines',
                 'platform_actual_outcomes', 'platform_outcome_verifications')
MAX_MEMBER = 64 * 1024 * 1024
MAX_ARCHIVE = 256 * 1024 * 1024
ATTESTATION_FILES = frozenset(('manifest.json', 'manifest.tsq', 'manifest.tsr', 'tsa.crt',
                               'cacert.pem', 'verification.txt', 'receipt.json'))
SECRET = re.compile(rb'-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----|\bsk-[A-Za-z0-9_-]{20,}|\bgh[pousr]_[A-Za-z0-9]{20,}|\bgithub_pat_[A-Za-z0-9_]{30,}')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def exists(path):
    return Path(path).exists() or Path(path).is_symlink()


def allowed(name):
    if not isinstance(name, str) or not name or '\\' in name or '\x00' in name:
        return False
    p = PurePosixPath(name)
    if str(p) != name or p.is_absolute() or '..' in p.parts or len(p.parts) < 2:
        return False
    if p.parts[0] not in ('emeris_live', 'static', 'docs', 'scripts', 'arena_data', 'patent_data'):
        return False
    if any(part.startswith('.') for part in p.parts):
        return name == 'patent_data/.gitignore'
    if p.parts[:2] == ('arena_data', 'attestations'):
        return (len(p.parts) == 4 and bool(re.fullmatch(r'[A-Za-z0-9-]{1,80}', p.parts[2]))
                and p.name in ATTESTATION_FILES)
    if any(word in p.name.lower() for word in ('.env', '.sqlite', '.db', '.pem', '.key', '.p12', '.pfx')):
        return False
    return p.suffix.lower() in ('.py', '.html', '.css', '.js', '.json', '.md', '.svg', '.pdf', '.txt', '.png')


def public_bytes(name, raw):
    if SECRET.search(raw):
        raise ValueError('Credential-like material rejected: ' + name)
    return raw


def validate_attestations(files):
    """A public TSA proof is an exact, receipt-bound set, never a PEM wildcard."""
    groups = {}
    for name, raw in files.items():
        if not name.startswith('arena_data/attestations/'):
            continue
        if not allowed(name):
            raise ValueError('Unexpected attestation release path')
        parts = PurePosixPath(name).parts
        groups.setdefault(parts[2], {})[parts[3]] = public_bytes(name, raw)
    for identifier, content in groups.items():
        if set(content) != ATTESTATION_FILES:
            raise ValueError('Incomplete attestation file set: ' + identifier)
        receipt = json.loads(content['receipt.json'])
        hashes = receipt.get('files')
        if receipt.get('id') != identifier or not isinstance(hashes, dict) or set(hashes) != ATTESTATION_FILES - {'receipt.json'}:
            raise ValueError('Attestation receipt file set or identity mismatch')
        for name, expected in hashes.items():
            if not isinstance(expected, str) or not re.fullmatch(r'[a-f0-9]{64}', expected) or hashlib.sha256(content[name]).hexdigest() != expected:
                raise ValueError('Attestation receipt hash mismatch: ' + identifier + '/' + name)
        for field, filename in (('manifest_sha256', 'manifest.json'), ('timestamp_response_sha256', 'manifest.tsr'),
                                ('authority_cert_sha256', 'tsa.crt'), ('ca_cert_sha256', 'cacert.pem')):
            if receipt.get(field) != hashes[filename]:
                raise ValueError('Attestation top-level digest mismatch: ' + field)
        # These exact files may contain public certificates only. In particular,
        # an encrypted private key is not made acceptable by using a CA filename.
        for name in ('tsa.crt', 'cacert.pem'):
            if not re.fullmatch(rb'\s*(?:-----BEGIN CERTIFICATE-----\s+[A-Za-z0-9+/=\r\n]+-----END CERTIFICATE-----\s*)+', content[name]):
                raise ValueError('Attestation certificate file is not public PEM: ' + name)
    return groups


def regular(path):
    path = Path(path)
    if not path.is_file() or path.is_symlink():
        raise ValueError('Expected a regular file: ' + str(path))
    return path


def exclusive_write(path, raw, mode=0o600):
    """O_EXCL rejects existing files, dangling symlinks and concurrent imports."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def backup_sqlite(source, target):
    regular(source)
    if exists(target):
        raise ValueError('Backup destination already exists')
    source_db = sqlite3.connect(Path(source).resolve().as_uri() + '?mode=ro', uri=True)
    try:
        target_db = sqlite3.connect(target)
        try:
            source_db.backup(target_db)
            if target_db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('SQLite backup failed integrity check')
        finally:
            target_db.close()
    finally:
        source_db.close()
    Path(target).chmod(0o600)


def sealed_rows(path):
    """Record hashes, not row contents, protect old data while allowing new rows."""
    with sqlite3.connect(regular(path).resolve().as_uri() + '?mode=ro', uri=True) as db:
        db.execute('BEGIN')
        result = {}
        for table in SEALED_TABLES:
            columns = [row[1] for row in db.execute('PRAGMA table_info(' + table + ')')]
            if not columns:
                raise ValueError('Expected sealed ledger table missing: ' + table)
            hashes = Counter()
            for row in db.execute('SELECT * FROM ' + table):
                raw = json.dumps(row, sort_keys=True, separators=(',', ':'), default=lambda x: {'bytes': x.hex()}).encode()
                hashes[hashlib.sha256(raw).hexdigest()] += 1
            result[table] = {'columns': columns, 'rows': hashes}
        return result


def check_sealed_rows(before, after):
    for table, old in before.items():
        new = after[table]
        if old['columns'] != new['columns'] or old['rows'] - new['rows']:
            raise ValueError('Existing sealed clinical rows changed: ' + table)


def check_initial_database(path):
    with sqlite3.connect(regular(path).resolve().as_uri() + '?mode=ro', uri=True) as db:
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Initial challenge database failed integrity check')
        names = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if names != {'arena_events', 'sqlite_sequence'}:
            raise ValueError('Only the isolated Arena ledger may be imported')


def load_overlay(archive, expected, commit):
    if not re.fullmatch(r'[a-f0-9]{40}', commit or '') or not re.fullmatch(r'[a-f0-9]{64}', expected or ''):
        raise ValueError('Pinned commit and archive SHA-256 required')
    if regular(archive).stat().st_size > MAX_ARCHIVE:
        raise ValueError('Compressed release exceeds size limit')
    if sha(archive) != expected:
        raise ValueError('Archive hash mismatch')
    with tarfile.open(archive) as tar:
        members = tar.getmembers()
        if not members or len(members) > 5000 or sum(m.size for m in members) > MAX_ARCHIVE:
            raise ValueError('Release exceeds bounded archive size')
        if not all(m.isfile() and 0 <= m.size <= MAX_MEMBER and (allowed(m.name) or m.name in ('OVERLAY.json', 'arena-initial.sqlite3')) for m in members):
            raise ValueError('Unsafe release members')
        if len({m.name for m in members}) != len(members):
            raise ValueError('Duplicate release members')
        content = {m.name: public_bytes(m.name, tar.extractfile(m).read()) for m in members}
    if not {'OVERLAY.json', 'arena-initial.sqlite3'} <= content.keys():
        raise ValueError('Missing deployment manifest or initial ledger')
    overlay = json.loads(content.pop('OVERLAY.json'))
    database = content.pop('arena-initial.sqlite3')
    if overlay.get('git_commit') != commit or set(overlay.get('files', {})) != set(content):
        raise ValueError('Unexpected release content')
    for name, raw in content.items():
        if hashlib.sha256(raw).hexdigest() != overlay['files'][name]:
            raise ValueError('Overlay integrity mismatch')
    if hashlib.sha256(database).hexdigest() != overlay.get('arena_database_sha256'):
        raise ValueError('Ledger transport hash mismatch')
    if not re.fullmatch(r'[a-f0-9]{64}', overlay.get('arena_ledger_sha256', '')):
        raise ValueError('Missing challenge ledger identity')
    validate_attestations(content)
    return overlay, content, database


def preflight():
    if not CURRENT.is_symlink() or CURRENT.resolve() != BASE or exists(TARGET):
        raise ValueError('Expected base release and unused destination')
    if exists(CURRENT.with_name('current-openforecast-next')) or exists(DROPIN):
        raise ValueError('Unexpected existing switch or Arena service configuration')
    regular(ENV_FILE)
    info = ENV_FILE.stat()
    if info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600:
        raise ValueError('Operator environment must be root-owned mode 0600')
    regular(DATA / 'workbench.sqlite3')
    for suffix in ('', '-wal', '-shm', '-journal'):
        if exists(DATA / ('arena.sqlite3' + suffix)):
            raise ValueError('Existing Arena ledger or sidecar must never be overwritten')
    if BASE.is_symlink() or not BASE.is_dir() or DATA.is_symlink() or not DATA.is_dir():
        raise ValueError('Unexpected release or data path')
    # Inherited symlinks could redirect writes outside the new release.
    if any(p.is_symlink() for p in BASE.rglob('*')):
        raise ValueError('Inherited release must not contain symlinks')
    manifest = json.loads(regular(BASE / 'RELEASE_MANIFEST.json').read_text())
    seen = set()
    for item in manifest['files']:
        p = PurePosixPath(item['path'])
        if p.is_absolute() or '..' in p.parts or str(p) != item['path'] or item['path'] in seen:
            raise ValueError('Unsafe inherited manifest path')
        seen.add(item['path'])
        if sha(regular(BASE / p)) != item['sha256']:
            raise ValueError('Inherited file integrity mismatch: ' + str(p))
    return manifest


def switch_to(destination):
    link = CURRENT.with_name('current-openforecast-next')
    if exists(link):
        raise ValueError('Another switch may be in progress')
    link.symlink_to(destination)
    try:
        link.replace(CURRENT)
    finally:
        if link.is_symlink():
            link.unlink()


def systemctl(*args):
    subprocess.run(['systemctl', *args], check=True)


def verify_release(release, name):
    return json.loads(subprocess.check_output([PYTHON, str(release / 'deploy/verify_server_state.py'),
        str(CURRENT.parent / ('openforecast01-' + name + '.json'))], text=True, stderr=subprocess.PIPE))


def remote(archive, expected, commit):
    overlay, content, database = load_overlay(archive, expected, commit)
    if content.get('scripts/deploy_arena.py') != Path(__file__).read_bytes():
        raise ValueError('Remote operator does not match the pinned release script')
    lock_path = CURRENT.parent / '.openforecast-deploy.lock'
    lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(lock_fd, 'w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Another Open Forecast deployment is running') from None
        base_manifest = preflight()
        base_manifest_hash = sha(BASE / 'RELEASE_MANIFEST.json')
        before = verify_release(BASE, 'before')
        prior_rows = sealed_rows(DATA / 'workbench.sqlite3')
        backup = CURRENT.parent / 'backups/openforecast01'
        if exists(backup):
            raise ValueError('Backup destination is already in use')
        # Validate the database in a staging directory before any canonical import.
        with tempfile.TemporaryDirectory(prefix='arena-verify-', dir=CURRENT.parent) as stage:
            stage = Path(stage)
            exclusive_write(stage / 'arena.sqlite3', database)
            check_initial_database(stage / 'arena.sqlite3')
            backup.mkdir(parents=True, exist_ok=False)
            backup_sqlite(DATA / 'workbench.sqlite3', backup / 'workbench.sqlite3')
            (backup / 'base-manifest.json').write_bytes((BASE / 'RELEASE_MANIFEST.json').read_bytes())
            shutil.copytree(BASE, TARGET, ignore=shutil.ignore_patterns('__pycache__', '.pytest_cache'))
            for name, raw in content.items():
                p = TARGET / name
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(raw)
            manifest = json.loads(json.dumps(base_manifest))
            old = {r['path']: r for r in manifest['files']}
            frozen = [r for r in manifest['files'] if r['path'].startswith(SEALED_PREFIXES)]
            if any(r['path'] in content for r in frozen):
                raise ValueError('Cannot modify sealed clinical evidence')
            for name in content:
                old[name] = {'path': name, 'sha256': sha(TARGET / name), 'bytes': (TARGET / name).stat().st_size}
            manifest.update(version=VERSION, git_commit=commit, parent_release=str(BASE), files=[old[k] for k in sorted(old)])
            (TARGET / 'RELEASE_MANIFEST.json').write_text(json.dumps(manifest, indent=2) + '\n')
            for item in manifest['files']:
                if sha(TARGET / item['path']) != item['sha256']:
                    raise ValueError('Staged inherited file integrity mismatch')
            command = ('import sys;sys.path.insert(0,sys.argv[1]);from emeris_live.arena_store import ArenaLedger;'
                       'assert ArenaLedger(sys.argv[2]).snapshot()["ledger_sha256"]==sys.argv[3]')
            subprocess.run([PYTHON, '-c', command, str(TARGET), str(stage), overlay['arena_ledger_sha256']], check=True)
        # Recheck state immediately before making application-visible changes.
        if CURRENT.resolve() != BASE or sha(BASE / 'RELEASE_MANIFEST.json') != base_manifest_hash:
            raise ValueError('Base release changed while preparing deployment')
        switched = False
        wrote_dropin = False
        imported = False
        try:
            for suffix in ('-wal', '-shm', '-journal'):
                if exists(DATA / ('arena.sqlite3' + suffix)):
                    raise ValueError('Arena database sidecar appeared during staging')
            exclusive_write(DATA / 'arena.sqlite3', database)
            imported = True
            shutil.chown(DATA / 'arena.sqlite3', user='emeris', group='emeris')
            DROPIN.parent.mkdir(parents=True, exist_ok=True)
            exclusive_write(DROPIN, b'[Service]\nEnvironmentFile=/etc/emeris-arena.env\n', 0o644)
            wrote_dropin = True
            switch_to(TARGET)
            switched = True
            systemctl('daemon-reload')
            systemctl('restart', 'emeris-live')
            after = None
            for attempt in range(15):
                try:
                    after = verify_release(TARGET, 'after')
                    break
                except subprocess.CalledProcessError:
                    if attempt == 14:
                        raise
                    time.sleep(1)
            check_sealed_rows(prior_rows, sealed_rows(DATA / 'workbench.sqlite3'))
            if before.get('preserved_processes') != after.get('preserved_processes'):
                raise ValueError('Pre-existing process identities changed')
            if sha(BASE / 'RELEASE_MANIFEST.json') != base_manifest_hash:
                raise ValueError('Previous release manifest changed')
            request = urllib.request.Request('http://127.0.0.1:19320/api/arena', headers={'Host': '8.148.145.28'})
            with urllib.request.urlopen(request, timeout=30) as response:
                arena = json.load(response)
            if arena['ledger_sha256'] != overlay['arena_ledger_sha256']:
                raise ValueError('Deployed challenge ledger differs')
            receipt = {'status': 'deployed', 'application_version': VERSION, 'git_commit': commit, 'release': str(TARGET),
                'previous_release': str(BASE), 'checked_at_utc': datetime.now(timezone.utc).isoformat(), 'archive_sha256': expected,
                'inherited_frozen_files_verified': len(frozen), 'overlay_files': len(content),
                'arena_ledger_sha256': arena['ledger_sha256'], 'arena_runs': len(arena['runs']),
                'new_registered_tasks': sum(r.get('scope') != 'archived_research' for r in arena['runs']),
                'before': before, 'after': after, 'backup': str(backup), 'old_clinical_rows_preserved': True,
                'base_manifest_sha256': base_manifest_hash}
            (CURRENT.parent / 'openforecast01-release.json').write_text(json.dumps(receipt, indent=2) + '\n')
            print(json.dumps(receipt))
        except BaseException:
            recovery = []
            if switched:
                try:
                    if CURRENT.resolve() != TARGET:
                        raise ValueError('Current link changed externally; refusing to overwrite it')
                    switch_to(BASE)
                except BaseException as exc:
                    recovery.append(type(exc).__name__ + ': restore release link failed')
            if wrote_dropin:
                try:
                    DROPIN.unlink()
                except OSError:
                    recovery.append('Remove created drop-in failed')
            if switched or wrote_dropin:
                for args in (('daemon-reload',), ('restart', 'emeris-live')):
                    try:
                        systemctl(*args)
                    except BaseException:
                        recovery.append('systemctl ' + ' '.join(args) + ' failed')
                if not recovery:
                    try:
                        verify_release(BASE, 'rollback')
                    except BaseException:
                        recovery.append('Rollback health verification failed')
            failure = {'status': 'rollback_failed' if recovery else 'deployment_failed', 'recovery_errors': recovery,
                       'imported_ledger_preserved': imported, 'release_for_inspection': str(TARGET),
                       'backup': str(backup), 'previous_release': str(BASE)}
            (CURRENT.parent / 'openforecast01-failure.json').write_text(json.dumps(failure, indent=2) + '\n')
            raise


def patent_assets(files):
    """Only public objects anchored by the committed catalog, never whole folders."""
    catalog = json.loads(files['patent_data/catalog.json'])
    assets = {}
    for item in catalog['items']:
        ident = item['publication_number']
        if not re.fullmatch(r'[A-Z]{2}[0-9]+[A-Z][0-9]?', ident):
            raise ValueError('Invalid catalog publication identifier')
        name = 'patent_data/raw/' + ident + '.pdf'
        raw = regular(ROOT / name).read_bytes()
        if not raw.startswith(b'%PDF-') or hashlib.sha256(raw).hexdigest() != item['raw_sha256']:
            raise ValueError('Patent raw document differs from committed catalog')
        assets[name] = public_bytes(name, raw)
        for page in item.get('page_references', []):
            number = page['pdf_page_1_based']
            if type(number) is not int or number < 1:
                raise ValueError('Invalid patent page')
            name = f'patent_data/ocr/{ident}/page-{number:04d}.json'
            value = json.loads(regular(ROOT / name).read_bytes())
            allowed_fields = {'pdf_page_1_based', 'source_sha256', 'text', 'text_sha256', 'characters',
                              'extractor', 'dpi', 'extracted_at', 'review_status'}
            if set(value) - allowed_fields or value['pdf_page_1_based'] != number or value['source_sha256'] != item['raw_sha256']:
                raise ValueError('Unexpected patent OCR metadata')
            # Catalog text hashes use the evidence module's NFC canonical JSON
            # string representation, not the raw UTF-8 text file bytes.
            canonical_text = json.dumps(unicodedata.normalize('NFC', value['text']), ensure_ascii=False,
                                        sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
            if hashlib.sha256(canonical_text).hexdigest() != page['text_sha256'] or value['text_sha256'] != page['text_sha256']:
                raise ValueError('Patent OCR text differs from committed catalog')
            assets[name] = public_bytes(name, json.dumps(value, ensure_ascii=False, indent=2).encode())
        review = item.get('research_review')
        if review:
            review_name = 'patent_data/sources/' + ident + '.identity-review.json'
            if review_name not in files or json.loads(files[review_name]) != review:
                raise ValueError('Committed identity receipt must match the catalog')
            for check in review.get('page_checks', []):
                relative = check['preview_relative_path']
                name = 'patent_data/' + relative
                if not relative.startswith('receipts/review/') or not allowed(name) or not name.endswith('.png'):
                    raise ValueError('Unsafe patent preview path')
                raw = regular(ROOT / name).read_bytes()
                if not raw.startswith(b'\x89PNG\r\n\x1a\n') or hashlib.sha256(raw).hexdigest() != check['preview_sha256']:
                    raise ValueError('Patent preview differs from committed receipt')
                assets[name] = public_bytes(name, raw)
            for source in review.get('drug_mapping', {}).get('sources', []):
                if source.get('source_id') == 'patent':
                    continue  # The original PDF is already catalog-anchored above.
                url = source['url']
                if url == 'https://www.cancer.gov/publications/dictionaries/cancer-drug/def/datopotamab-deruxtecan':
                    stem, extension = 'nci_dato', '.html'
                elif re.fullmatch(r'https://clinicaltrials.gov/api/v2/studies/NCT[0-9]{8}', url):
                    stem, extension = url.rsplit('/', 1)[1], '.json'
                else:
                    raise ValueError('Identity snapshot requires an explicit public-source mapping')
                name = 'patent_data/receipts/review/' + stem + extension
                raw = regular(ROOT / name).read_bytes()
                if hashlib.sha256(raw).hexdigest() != source['raw_sha256']:
                    raise ValueError('Identity response snapshot differs from committed receipt')
                receipt_name = 'patent_data/receipts/review/' + stem + '.receipt.json'
                receipt_raw = regular(ROOT / receipt_name).read_bytes()
                receipt = json.loads(receipt_raw)
                if (set(receipt) != {'source_url', 'retrieved_at', 'raw_sha256', 'bytes'}
                        or receipt['source_url'] != url or receipt['raw_sha256'] != source['raw_sha256']
                        or receipt['retrieved_at'] != source['retrieved_at'] or receipt['bytes'] != len(raw)):
                    raise ValueError('Identity download receipt does not bind the snapshot')
                assets[name] = public_bytes(name, raw)
                assets[receipt_name] = public_bytes(receipt_name, receipt_raw)
    return assets


def committed_overlay(commit):
    if not re.fullmatch(r'[a-f0-9]{40}', commit):
        raise ValueError('Invalid source commit')
    records = subprocess.check_output(['git', 'diff', '--name-status', '--no-renames', BASE_COMMIT, commit,
                                      '--', 'apps/predome'], cwd=ROOT, text=True).splitlines()
    files = {}
    for record in records:
        change, full = record.split('\t', 1)
        name = full.removeprefix('apps/predome/')
        if name.startswith(SEALED_PREFIXES):
            raise ValueError('Release must not alter frozen source records: ' + name)
        if not allowed(name):
            continue
        if change not in ('A', 'M'):
            raise ValueError('Overlay cannot apply removed or changed-type files: ' + name)
        raw = subprocess.check_output(['git', 'show', commit + ':apps/predome/' + name], cwd=ROOT)
        if regular(ROOT / name).read_bytes() != raw:
            raise ValueError('Uncommitted release file: ' + name)
        files[name] = public_bytes(name, raw)
    if 'scripts/deploy_arena.py' not in files or files['scripts/deploy_arena.py'] != Path(__file__).read_bytes():
        raise ValueError('Running deployment script must be the committed source')
    if 'patent_data/catalog.json' not in files:
        raise ValueError('Committed patent catalog required')
    validate_attestations(files)
    files.update(patent_assets(files))
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host'); parser.add_argument('--identity', type=Path); parser.add_argument('--receipt', type=Path)
    parser.add_argument('--data-dir', type=Path); parser.add_argument('--remote', action='store_true')
    parser.add_argument('--archive'); parser.add_argument('--sha256'); parser.add_argument('--commit')
    args = parser.parse_args()
    if args.remote:
        remote(args.archive, args.sha256, args.commit)
        return
    if not args.host or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9.-]*', args.host) or not args.identity or not args.receipt or not args.data_dir:
        parser.error('host, identity, receipt and data-dir required')
    regular(args.identity.expanduser())
    regular(args.data_dir / 'arena.sqlite3')
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    files = committed_overlay(commit)
    from emeris_live.arena_store import ArenaLedger
    with tempfile.TemporaryDirectory(prefix='pd-arena-', dir='/tmp') as temporary:
        temp = Path(temporary)
        # A single SQLite snapshot produces both the transported bytes and ledger hash.
        backup = temp / 'arena.sqlite3'
        backup_sqlite(args.data_dir / 'arena.sqlite3', backup)
        check_initial_database(backup)
        state = ArenaLedger(temp).snapshot()
        manifest = {'git_commit': commit, 'files': {n: hashlib.sha256(raw).hexdigest() for n, raw in files.items()},
                    'arena_database_sha256': sha(backup), 'arena_ledger_sha256': state['ledger_sha256']}
        files['arena-initial.sqlite3'] = public_bytes('initial challenge ledger', backup.read_bytes())
        files['OVERLAY.json'] = json.dumps(manifest).encode()
        archive = temp / 'openforecast01.tar.gz'
        with tarfile.open(archive, 'w:gz') as tar:
            for name, raw in sorted(files.items()):
                info = tarfile.TarInfo(name); info.size = len(raw); info.mode = 0o600 if name == 'arena-initial.sqlite3' else 0o644
                tar.addfile(info, io.BytesIO(raw))
        archive.chmod(0o600)
        # Transfer exactly the committed operator script, not an independently read copy.
        operator = temp / 'deploy-openforecast01.py'
        operator.write_bytes(files['scripts/deploy_arena.py'])
        opts = ['-F', '/dev/null', '-i', str(args.identity.expanduser().resolve()), '-o', 'IdentitiesOnly=yes',
                '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=10',
                '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=3']
        destination = 'root@' + args.host
        subprocess.run(['scp', *opts, str(archive), destination + ':/opt/emeris-live/openforecast01.tar.gz'], check=True)
        subprocess.run(['scp', *opts, str(operator), destination + ':/opt/emeris-live/deploy-openforecast01.py'], check=True)
        command = [PYTHON, '/opt/emeris-live/deploy-openforecast01.py', '--remote', '--archive',
                   '/opt/emeris-live/openforecast01.tar.gz', '--sha256', sha(archive), '--commit', commit]
        receipt = json.loads(subprocess.check_output(['ssh', *opts, destination, shlex.join(command)], text=True))
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        args.receipt.write_text(json.dumps(receipt, indent=2) + '\n')
        print(json.dumps({k: receipt[k] for k in ('status', 'application_version', 'git_commit', 'inherited_frozen_files_verified', 'new_registered_tasks')}))


if __name__ == '__main__':
    sys.path.insert(0, str(ROOT))
    main()
