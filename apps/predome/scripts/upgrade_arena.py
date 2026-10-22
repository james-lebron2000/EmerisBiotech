"""Fixed 1.2.0 -> 1.2.1 successor: append events, never replace the live ledger."""
import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import importlib.util
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
import tarfile
import tempfile
import time
from urllib.parse import urlsplit
import urllib.request

HELPER = Path(__file__).with_name('upgrade-openforecast02-helper.py' if Path(__file__).name == 'upgrade-openforecast02.py' else 'deploy_arena.py')
_spec = importlib.util.spec_from_file_location('arena_upgrade_helpers', HELPER)
d = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(d)
ROOT = Path(__file__).resolve().parents[1]
BASE_COMMIT = '3026062c7936a90b06cc40095b915cc2c5803b60'
BASE = Path('/opt/emeris-live/releases/20261022-openforecast01')
TARGET = Path('/opt/emeris-live/releases/20261023-openforecast02')
CURRENT = Path('/opt/emeris-live/current')
DATA = Path('/var/lib/emeris-live')
DROPIN = Path('/etc/systemd/system/emeris-live.service.d/arena.conf')
ENV_FILE = Path('/etc/emeris-arena.env')
PYTHON = '/opt/emeris-live/venv/bin/python'
VERSION = '1.2.1'
MANIFEST = 'arena_data/season_acquisition_manifest.json'
HISTORICAL_ATTESTATIONS = frozenset(('pilot-registration-20261022', 'pilot-predictions-20261022',
                                   'ablation-registration-20261022', 'ablation-predictions-20261022'))
REMOTE_ARCHIVE = '/opt/emeris-live/openforecast02.tar.gz'
REMOTE_OPERATOR = '/opt/emeris-live/upgrade-openforecast02.py'
REMOTE_HELPER = '/opt/emeris-live/upgrade-openforecast02-helper.py'
COLUMNS = ('seq','id','kind','entity_id','created_at','payload','previous_hash','event_hash')
DROPIN_BYTES = b'[Service]\nEnvironmentFile=/etc/emeris-arena.env\n'
IMMUTABLE_PREFIXES = (*d.SEALED_PREFIXES, 'arena_data/', 'patent_data/')


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(value).hexdigest()


def checked_events(db):
    """Verify exact schema, active no-update/delete triggers, sequence and hashes."""
    if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
        raise ValueError('Arena SQLite integrity failed')
    if tuple(row[1] for row in db.execute('PRAGMA table_info(arena_events)')) != COLUMNS:
        raise ValueError('Unexpected Arena event schema')
    triggers = dict(db.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger' AND tbl_name='arena_events'"))
    if set(triggers)!={'arena_no_update','arena_no_delete'}:
        raise ValueError('Unexpected Arena trigger set')
    for operation in ('update','delete'):
        name = 'arena_no_' + operation
        expected = f"CREATE TRIGGER {name} BEFORE {operation.upper()} ON arena_events BEGIN SELECT RAISE(ABORT, 'Arena ledger is append only'); END"
        normalize = lambda sql: re.sub(r'\s+', '', sql.lower()).replace('ifnotexists','').rstrip(';')
        if name not in triggers or normalize(triggers[name]) != normalize(expected):
            raise ValueError('Arena append-only trigger changed or missing')
    rows = [dict(zip(COLUMNS,row)) for row in db.execute('SELECT '+','.join(COLUMNS)+' FROM arena_events ORDER BY seq')]
    previous = '0'*64
    for number,row in enumerate(rows,1):
        if row['seq'] != number or row['previous_hash'] != previous:
            raise ValueError('Arena sequence or chain divergence')
        check = {k:v for k,v in row.items() if k not in ('seq','event_hash')}
        check['payload'] = json.loads(check['payload'])
        if digest(canonical(check).encode()) != row['event_hash']:
            raise ValueError('Arena event hash mismatch')
        previous = row['event_hash']
    return rows


def read_events(path):
    with sqlite3.connect(d.regular(path).resolve().as_uri()+'?mode=ro',uri=True) as db:
        db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
        return checked_events(db)


def head(events):
    return events[-1]['event_hash'] if events else None


def require_prefix(existing, incoming):
    if len(existing)>len(incoming) or existing != incoming[:len(existing)]:
        raise ValueError('Live Arena events are not the exact transport prefix')


def append_events(path, incoming, expected_head):
    """Recheck under a write lock, INSERT only; any conflict rolls back this append."""
    d.regular(path)
    db = sqlite3.connect(Path(path).resolve().as_uri()+'?mode=rw',uri=True,timeout=15)
    try:
        db.execute('BEGIN IMMEDIATE')
        existing = checked_events(db)
        if head(existing) != expected_head:
            raise ValueError('Live Arena changed since the deployment backup')
        require_prefix(existing,incoming)
        for row in incoming[len(existing):]:
            db.execute('INSERT INTO arena_events('+','.join(COLUMNS)+') VALUES ('+','.join('?' for _ in COLUMNS)+')',
                       tuple(row[k] for k in COLUMNS))
        if checked_events(db) != incoming:
            raise ValueError('Post-append Arena verification failed')
        db.commit()
        return len(incoming)-len(existing)
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def receipt_inventory(directory):
    if not d.exists(directory):return {}
    if directory.is_symlink() or not directory.is_dir():raise ValueError('Unsafe Arena receipt directory')
    result = {}
    for path in directory.iterdir():
        if not re.fullmatch(r'[a-f0-9]{32}\.json',path.name):
            raise ValueError('Unexpected runtime receipt path')
        result[path.name] = d.sha(d.regular(path))
    return result


def preserve_receipts(before, directory):
    after = receipt_inventory(directory)
    if any(after.get(name)!=value for name,value in before.items()):
        raise ValueError('An existing result receipt changed')


def manifest_assets(manifest_raw, get_bytes):
    value = json.loads(manifest_raw)
    if value.get('schema_version')!='arena-public-acquisition-v1' or not isinstance(value.get('assets'),list):
        raise ValueError('Explicit public acquisition manifest required')
    assets = {}
    for item in value['assets']:
        name = item.get('path','')
        if not re.fullmatch(r'arena_data/source_snapshots/[A-Za-z0-9][A-Za-z0-9_.-]{0,160}\.(json|html|txt|pdf|xml)',name) or name in assets:
            raise ValueError('Unsafe or duplicate acquisition asset path')
        url = urlsplit(str(item.get('source_url','')))
        if url.scheme!='https' or not url.hostname or url.username or url.password:
            raise ValueError('Public HTTPS acquisition source required')
        retrieved = datetime.fromisoformat(str(item.get('retrieved_at','')).replace('Z','+00:00'))
        if retrieved.tzinfo is None or retrieved>datetime.now(timezone.utc):
            raise ValueError('Invalid acquisition retrieval time')
        raw = get_bytes(name)
        if (type(item.get('bytes')) is not int or not 0<len(raw)<=d.MAX_MEMBER
                or item['bytes']!=len(raw) or digest(raw)!=item.get('sha256')):
            raise ValueError('Acquisition asset hash or size mismatch')
        if name.endswith('.pdf') and not raw.startswith(b'%PDF-'):
            raise ValueError('Acquisition PDF is not a PDF')
        assets[name] = d.public_bytes(name,raw)
    return assets


def validated_attestations(files, expected_ids=...):
    """All committed bundles travel together; the original four cannot disappear."""
    groups = d.validate_attestations(files)
    if not HISTORICAL_ATTESTATIONS <= set(groups):
        raise ValueError('Missing historical TSA bundle')
    if expected_ids is not ... and (not isinstance(expected_ids, list)
            or any(not isinstance(value, str) for value in expected_ids)
            or expected_ids != sorted(groups)):
        raise ValueError('TSA bundle set differs from pinned upgrade manifest')
    return groups


def verify_ledger_attestations(events, files):
    """No appended timestamp may point at an omitted/uncommitted proof bundle."""
    groups = validated_attestations(files)
    receipts = {identifier: json.loads(content['receipt.json']) for identifier, content in groups.items()}
    for event in events:
        if event['kind'] != 'timestamp_attested':
            continue
        payload = json.loads(event['payload'])
        if payload.get('id') not in receipts or payload != receipts[payload['id']]:
            raise ValueError('Ledger timestamp receipt is absent from or differs from committed TSA bundle')


def git_bytes(commit,name):
    raw = subprocess.check_output(['git','show',commit+':apps/predome/'+name],cwd=ROOT)
    if d.regular(ROOT/name).read_bytes()!=raw:raise ValueError('Uncommitted release source: '+name)
    return d.public_bytes(name,raw)


def committed_overlay(commit):
    if not re.fullmatch(r'[a-f0-9]{40}',commit or ''):raise ValueError('Pinned source commit required')
    subprocess.run(['git','merge-base','--is-ancestor',BASE_COMMIT,commit],cwd=ROOT,check=True)
    changed = subprocess.check_output(['git','diff','--name-status','--no-renames',BASE_COMMIT,commit,'--',':(top)apps/predome'],cwd=ROOT,text=True)
    files = {}
    for line in changed.splitlines():
        action,full = line.split('\t',1);name = full.removeprefix('apps/predome/')
        if name.startswith(d.SEALED_PREFIXES):raise ValueError('Frozen clinical files cannot change')
        if name.startswith('arena_data/source_snapshots/'):continue  # Manifest is the only raw inclusion authority.
        if not d.allowed(name):continue
        if action not in ('A','M'):raise ValueError('Upgrade overlay cannot delete or change file types')
        files[name] = git_bytes(commit,name)
    for name in ('scripts/upgrade_arena.py','scripts/deploy_arena.py',MANIFEST):
        files[name] = git_bytes(commit,name)
    if files['scripts/upgrade_arena.py'] != Path(__file__).read_bytes() or files['scripts/deploy_arena.py'] != HELPER.read_bytes():
        raise ValueError('Operator and helper must be the committed implementation')
    attestation_paths = subprocess.check_output(['git','ls-tree','-r','--full-tree','--name-only',commit,'--','apps/predome/arena_data/attestations'],cwd=ROOT,text=True)
    for full in attestation_paths.splitlines():
        name = full.removeprefix('apps/predome/')
        if not d.allowed(name):raise ValueError('Unsafe committed TSA path')
        files[name] = git_bytes(commit,name)
    validated_attestations(files)
    files.update(manifest_assets(files[MANIFEST],lambda name:d.regular(ROOT/name).read_bytes()))
    return files


def referenced_receipts(events,data_dir):
    result = {}
    for event in events:
        payload = json.loads(event['payload'])
        if event['kind']!='run_completed' or not payload.get('receipt_path'):continue
        name = payload['receipt_path']
        if not re.fullmatch(r'arena_receipts/[a-f0-9]{32}\.json',name):raise ValueError('Unsafe ledger receipt reference')
        raw = d.regular(data_dir/name).read_bytes()
        if digest(raw)!=payload.get('receipt_sha256'):raise ValueError('Result receipt differs from ledger reference')
        value = json.loads(raw)
        if value.get('run_id')!=event['entity_id'] or value.get('schema_version')!='arena-result-receipt-v1':
            raise ValueError('Result receipt identity mismatch')
        result[name] = d.public_bytes(name,raw)
    return result


def load_archive(path,expected,commit):
    if not re.fullmatch(r'[a-f0-9]{40}',commit or '') or not re.fullmatch(r'[a-f0-9]{64}',expected or ''):
        raise ValueError('Pinned archive and commit required')
    if d.regular(path).stat().st_size>d.MAX_ARCHIVE or d.sha(path)!=expected:raise ValueError('Upgrade archive hash or size mismatch')
    with tarfile.open(path) as tar:
        members=tar.getmembers()
        if not members or len(members)>5000 or sum(m.size for m in members)>d.MAX_ARCHIVE:raise ValueError('Archive exceeds bounds')
        if len({m.name for m in members})!=len(members):raise ValueError('Duplicate archive member')
        for m in members:
            if (not m.isfile() or not 0<=m.size<=d.MAX_MEMBER or not (d.allowed(m.name)
                    # XML is only provisionally read here; below it must be an
                    # exact hash/size-bound acquisition-manifest entry.
                    or re.fullmatch(r'arena_data/source_snapshots/[A-Za-z0-9][A-Za-z0-9_.-]{0,160}\.xml',m.name)
                    or re.fullmatch(r'arena_receipts/[a-f0-9]{32}\.json',m.name)
                    or m.name in ('UPGRADE.json','arena-transport.sqlite3'))):raise ValueError('Unsafe archive member')
        all_files={m.name:d.public_bytes(m.name,tar.extractfile(m).read()) for m in members}
    meta=json.loads(all_files.pop('UPGRADE.json'));database=all_files.pop('arena-transport.sqlite3')
    if meta.get('git_commit')!=commit or meta.get('base_commit')!=BASE_COMMIT or meta.get('version')!=VERSION:
        raise ValueError('Unexpected fixed upgrade identity')
    expected_files={**meta.get('files',{}),**meta.get('receipt_files',{})}
    if set(expected_files)!=set(all_files) or any(digest(raw)!=expected_files[name] for name,raw in all_files.items()):
        raise ValueError('Upgrade member hash mismatch')
    if digest(database)!=meta.get('arena_database_sha256'):raise ValueError('Transport database hash mismatch')
    source={name:raw for name,raw in all_files.items() if name in meta['files']}
    receipts={name:raw for name,raw in all_files.items() if name in meta['receipt_files']}
    if any(name.startswith('arena_receipts/') for name in source) or any(not re.fullmatch(r'arena_receipts/[a-f0-9]{32}\.json',name) for name in receipts):
        raise ValueError('Unexpected runtime/source partition')
    assets=manifest_assets(source[MANIFEST],lambda name:source[name])
    if {name for name in source if name.startswith('arena_data/source_snapshots/')}!=set(assets):
        raise ValueError('Unlisted acquisition assets rejected')
    if 'attestation_ids' not in meta:raise ValueError('Pinned TSA bundle set required')
    validated_attestations(source, meta['attestation_ids'])
    return meta,source,receipts,database


def verify_manifest(release):
    manifest=json.loads(d.regular(release/'RELEASE_MANIFEST.json').read_text());seen=set()
    for item in manifest['files']:
        name=item['path'];p=PurePosixPath(name)
        if p.is_absolute() or '..' in p.parts or str(p)!=name or name in seen:raise ValueError('Unsafe inherited manifest')
        seen.add(name)
        if d.sha(d.regular(release/name))!=item['sha256']:raise ValueError('Inherited release file changed')
    return manifest


def preflight():
    if not CURRENT.is_symlink() or CURRENT.resolve()!=BASE or d.exists(TARGET):raise ValueError('Expected fixed base and unused successor')
    if d.exists(CURRENT.with_name('current-openforecast02-next')):raise ValueError('Existing release switch marker')
    if BASE.is_symlink() or DATA.is_symlink() or not DATA.is_dir() or any(p.is_symlink() for p in BASE.rglob('*')):
        raise ValueError('Unexpected release/data symlink')
    manifest=verify_manifest(BASE)
    if manifest.get('git_commit')!=BASE_COMMIT or manifest.get('version')!='1.2.0':raise ValueError('Unexpected deployed base commit/version')
    if d.regular(DROPIN).read_bytes()!=DROPIN_BYTES:raise ValueError('Existing Arena service drop-in differs; never overwrite')
    info=d.regular(ENV_FILE).stat()
    if info.st_uid!=0 or stat.S_IMODE(info.st_mode)!=0o600:raise ValueError('Existing credential environment must stay root-owned 0600')
    d.regular(DATA/'workbench.sqlite3');events=read_events(DATA/'arena.sqlite3')
    if not events:raise ValueError('Successor requires an existing Arena ledger')
    return manifest,events


def switch_to(destination):
    link=CURRENT.with_name('current-openforecast02-next')
    if d.exists(link):raise ValueError('Concurrent release switch')
    link.symlink_to(destination)
    try:link.replace(CURRENT)
    finally:
        if link.is_symlink():link.unlink()


def verify_release(release,label):
    return json.loads(subprocess.check_output([PYTHON,str(release/'deploy/verify_server_state.py'),
        str(CURRENT.parent/('openforecast02-'+label+'.json'))],text=True,stderr=subprocess.PIPE))


def remote(archive,expected,commit):
    meta,files,receipts,database=load_archive(archive,expected,commit)
    if files.get('scripts/upgrade_arena.py')!=Path(__file__).read_bytes() or files.get('scripts/deploy_arena.py')!=HELPER.read_bytes():
        raise ValueError('Uploaded operator/helper differ from the pinned committed archive')
    fd=os.open(CURRENT.parent/'.openforecast-deploy.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise ValueError('Another Arena deployment holds the lock') from None
        base_manifest,original_events=preflight();base_hash=d.sha(BASE/'RELEASE_MANIFEST.json')
        prior_rows=d.sealed_rows(DATA/'workbench.sqlite3');prior_receipts=receipt_inventory(DATA/'arena_receipts')
        before=verify_release(BASE,'before');backup=CURRENT.parent/'backups/openforecast02'
        if d.exists(backup):raise ValueError('Upgrade backup directory already exists')
        with tempfile.TemporaryDirectory(prefix='arena-upgrade-',dir=CURRENT.parent) as temporary:
            staged=Path(temporary)/'arena.sqlite3';d.exclusive_write(staged,database)
            incoming=read_events(staged);require_prefix(original_events,incoming)
            if head(incoming)!=meta.get('arena_ledger_sha256') or len(incoming)!=meta.get('arena_event_count'):
                raise ValueError('Transport ledger manifest mismatch')
            verify_ledger_attestations(incoming, files)
            # Validate receipt identities from their event references before canonical writes.
            reference={json.loads(e['payload']).get('receipt_path'):json.loads(e['payload']).get('receipt_sha256')
                       for e in incoming if e['kind']=='run_completed' and json.loads(e['payload']).get('receipt_path')}
            if set(reference)!=set(receipts) or any(digest(raw)!=reference[name] for name,raw in receipts.items()):
                raise ValueError('Transport receipt set does not match ledger')
            for event in incoming:
                payload=json.loads(event['payload']);name=payload.get('receipt_path') if event['kind']=='run_completed' else None
                if name:
                    value=json.loads(receipts[name])
                    if value.get('run_id')!=event['entity_id'] or value.get('schema_version')!='arena-result-receipt-v1':
                        raise ValueError('Transport receipt identity differs from its event')
            backup.mkdir(parents=True,exist_ok=False)
            d.backup_sqlite(DATA/'arena.sqlite3',backup/'arena.sqlite3')
            if read_events(backup/'arena.sqlite3')!=original_events:raise ValueError('Arena changed before backup completed')
            d.backup_sqlite(DATA/'workbench.sqlite3',backup/'workbench.sqlite3')
            d.exclusive_write(backup/'base-manifest.json',(BASE/'RELEASE_MANIFEST.json').read_bytes())
            d.exclusive_write(backup/'arena-dropin.conf',DROPIN_BYTES)
            (backup/'arena_receipts').mkdir(mode=0o700)
            for name,expected_hash in prior_receipts.items():
                raw=d.regular(DATA/'arena_receipts'/name).read_bytes()
                if digest(raw)!=expected_hash:raise ValueError('Receipt changed during backup')
                d.exclusive_write(backup/'arena_receipts'/name,raw)
            shutil.copytree(BASE,TARGET,ignore=shutil.ignore_patterns('__pycache__','.pytest_cache'))
            records={item['path']:item for item in base_manifest['files']}
            for name,raw in files.items():
                path=TARGET/name
                if name.startswith(IMMUTABLE_PREFIXES) and d.exists(path) and d.regular(path).read_bytes()!=raw:
                    raise ValueError('Cannot replace existing frozen evidence/TSA file')
                path.parent.mkdir(parents=True,exist_ok=True)
                if not d.exists(path) or d.regular(path).read_bytes()!=raw:path.write_bytes(raw)
                records[name]={'path':name,'sha256':digest(raw),'bytes':len(raw)}
            manifest={**base_manifest,'version':VERSION,'git_commit':commit,'parent_release':str(BASE),
                      'files':[records[name] for name in sorted(records)]}
            (TARGET/'RELEASE_MANIFEST.json').write_text(json.dumps(manifest,indent=2)+'\n')
            verify_manifest(TARGET)
            subprocess.run([PYTHON,'-m','compileall','-q',str(TARGET/'emeris_live')],check=True)
        switched=False;appended=0
        try:
            if CURRENT.resolve()!=BASE or d.sha(BASE/'RELEASE_MANIFEST.json')!=base_hash:raise ValueError('Base changed before switch')
            verify_manifest(BASE)
            if d.regular(DROPIN).read_bytes()!=DROPIN_BYTES:raise ValueError('Drop-in changed before switch')
            d.check_sealed_rows(prior_rows,d.sealed_rows(DATA/'workbench.sqlite3'))
            preserve_receipts(prior_receipts,DATA/'arena_receipts')
            (DATA/'arena_receipts').mkdir(mode=0o700,exist_ok=True)
            for name,raw in receipts.items():
                path=DATA/name
                if d.exists(path):
                    if d.regular(path).read_bytes()!=raw:raise ValueError('Existing receipt cannot be overwritten')
                else:d.exclusive_write(path,raw)
            appended=append_events(DATA/'arena.sqlite3',incoming,head(original_events))
            # A process that appended after our transaction must retain its events,
            # even if health verification later causes the code release to roll back.
            if CURRENT.resolve()!=BASE or d.sha(BASE/'RELEASE_MANIFEST.json')!=base_hash:raise ValueError('Base changed at switch boundary')
            switch_to(TARGET);switched=True;d.systemctl('restart','emeris-live')
            after=None
            for attempt in range(15):
                try:after=verify_release(TARGET,'after');break
                except subprocess.CalledProcessError:
                    if attempt==14:raise
                    time.sleep(1)
            d.check_sealed_rows(prior_rows,d.sealed_rows(DATA/'workbench.sqlite3'))
            preserve_receipts(prior_receipts,DATA/'arena_receipts');require_prefix(incoming,read_events(DATA/'arena.sqlite3'))
            if not before.get('preserved_processes') or before.get('preserved_processes')!=after.get('preserved_processes'):
                raise ValueError('Unrelated process identities changed or were not verified')
            if d.sha(BASE/'RELEASE_MANIFEST.json')!=base_hash or d.regular(DROPIN).read_bytes()!=DROPIN_BYTES:
                raise ValueError('Prior release or drop-in changed')
            verify_manifest(BASE)
            req=urllib.request.Request('http://127.0.0.1:19320/api/arena',headers={'Host':'8.148.145.28'})
            with urllib.request.urlopen(req,timeout=30) as response:arena=json.load(response)
            live=read_events(DATA/'arena.sqlite3');require_prefix(incoming,live)
            if arena.get('ledger_sha256')!=head(live):raise ValueError('Application ledger does not match live append-only state')
            receipt={'status':'deployed','application_version':VERSION,'git_commit':commit,'release':str(TARGET),
                'previous_release':str(BASE),'archive_sha256':expected,'backup':str(backup),
                'base_manifest_sha256':base_hash,'arena_previous_ledger_sha256':head(original_events),
                'arena_ledger_sha256':head(live),'appended_events':appended,'old_arena_events_preserved':True,
                'old_clinical_rows_preserved':True,'existing_receipts_preserved':len(prior_receipts),
                'transport_receipts':len(receipts),'tsa_bundles':len(meta['attestation_ids']),
                'attestation_ids':meta['attestation_ids'],'overlay_files':len(files),
                'before':before,'after':after,'checked_at_utc':datetime.now(timezone.utc).isoformat()}
            d.exclusive_write(CURRENT.parent/'openforecast02-release.json',(json.dumps(receipt,indent=2)+'\n').encode())
            print(json.dumps(receipt))
        except BaseException:
            recovery=[]
            if switched:
                try:
                    if CURRENT.resolve()!=TARGET:raise ValueError('External release switch')
                    switch_to(BASE);d.systemctl('restart','emeris-live');verify_release(BASE,'rollback')
                except BaseException:recovery.append('Release link/service rollback requires operator review')
            failure={'status':'rollback_failed' if recovery else 'deployment_failed','recovery_errors':recovery,
                'appended_events_preserved':appended,'live_database_never_replaced':True,'backup':str(backup),
                'previous_release':str(BASE),'staged_release':str(TARGET)}
            d.exclusive_write(CURRENT.parent/'openforecast02-failure.json',(json.dumps(failure,indent=2)+'\n').encode())
            raise


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host');parser.add_argument('--identity',type=Path);parser.add_argument('--receipt',type=Path)
    parser.add_argument('--data-dir',type=Path);parser.add_argument('--remote',action='store_true')
    parser.add_argument('--sha256');parser.add_argument('--commit')
    args=parser.parse_args()
    if args.remote:
        if any((args.host,args.identity,args.receipt,args.data_dir)):parser.error('Remote command accepts only pinned commit/hash')
        remote(REMOTE_ARCHIVE,args.sha256,args.commit);return
    if args.host!='8.148.145.28' or not all((args.identity,args.receipt,args.data_dir)):
        parser.error('Fixed host 8.148.145.28 plus identity, receipt and data-dir required')
    d.regular(args.identity.expanduser())
    if d.exists(args.receipt):raise ValueError('Operator receipt must be a new file')
    commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip();files=committed_overlay(commit)
    with tempfile.TemporaryDirectory(prefix='predome-upgrade-') as temporary:
        temp=Path(temporary);database=temp/'arena.sqlite3';d.backup_sqlite(args.data_dir/'arena.sqlite3',database)
        events=read_events(database);receipts=referenced_receipts(events,args.data_dir)
        verify_ledger_attestations(events, files)
        meta={'version':VERSION,'git_commit':commit,'base_commit':BASE_COMMIT,
            'files':{n:digest(raw) for n,raw in files.items()},'receipt_files':{n:digest(raw) for n,raw in receipts.items()},
            'attestation_ids':sorted(validated_attestations(files)),
            'arena_database_sha256':d.sha(database),'arena_ledger_sha256':head(events),'arena_event_count':len(events)}
        archive=temp/'openforecast02.tar.gz'
        with tarfile.open(archive,'w:gz') as tar:
            for name,raw in sorted({**files,**receipts,'UPGRADE.json':canonical(meta).encode(),'arena-transport.sqlite3':database.read_bytes()}.items()):
                info=tarfile.TarInfo(name);info.size=len(raw);info.mode=0o600;tar.addfile(info,io.BytesIO(raw))
        archive.chmod(0o600)
        operator=temp/'upgrade-openforecast02.py';operator.write_bytes(files['scripts/upgrade_arena.py'])
        helper=temp/'upgrade-openforecast02-helper.py';helper.write_bytes(files['scripts/deploy_arena.py'])
        opts=['-F','/dev/null','-i',str(args.identity.expanduser().resolve()),'-o','IdentitiesOnly=yes','-o','BatchMode=yes',
              '-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=10','-o','ServerAliveInterval=15','-o','ServerAliveCountMax=3']
        destination='root@'+args.host
        for local,remote_path in ((archive,REMOTE_ARCHIVE),(operator,REMOTE_OPERATOR),(helper,REMOTE_HELPER)):
            subprocess.run(['scp',*opts,str(local),destination+':'+remote_path],check=True)
        pins=''.join(d.sha(path)+'  '+remote_path+'\n' for path,remote_path in ((archive,REMOTE_ARCHIVE),(operator,REMOTE_OPERATOR),(helper,REMOTE_HELPER)))
        subprocess.run(['ssh',*opts,destination,'sha256sum --check --status -'],input=pins,text=True,check=True)
        command=[PYTHON,REMOTE_OPERATOR,'--remote','--sha256',d.sha(archive),'--commit',commit]
        result=json.loads(subprocess.check_output(['ssh',*opts,destination,shlex.join(command)],text=True))
        args.receipt.parent.mkdir(parents=True,exist_ok=True)
        d.exclusive_write(args.receipt,(json.dumps(result,indent=2)+'\n').encode())
        print(json.dumps({k:result[k] for k in ('status','application_version','git_commit','appended_events','old_arena_events_preserved')}))


if __name__=='__main__':main()
