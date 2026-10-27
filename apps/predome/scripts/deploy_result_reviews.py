"""Fixed code-only 1.2.8 provenance, cost and failure-diagnostics update. No ledger imports or database restores."""
import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import importlib.util
import io
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile
import time
import urllib.request

BASE=Path('/opt/emeris-live/releases/20261024-arena08')
TARGET=Path('/opt/emeris-live/releases/20261026-arena09')
CURRENT=Path('/opt/emeris-live/current')
DATA=Path('/var/lib/emeris-live')
HOME=Path('/opt/emeris-live')
PYTHON='/opt/emeris-live/venv/bin/python'
BASE_COMMIT='4369a32e87b837a1e09a73e367540cf11a5ba872'
BASE_VERSION='1.2.7'
VERSION='1.2.8'
FILES=frozenset(('emeris_live/main.py','emeris_live/patent_evidence.py','scripts/ingest_patents.py',
    'emeris_live/arena_metrics.py','emeris_live/arena_api.py','static/arena.js',
    'scripts/arena_learning_report.py','docs/OPEN_FORECAST_RELIABILITY_1_2_8.md',
    'scripts/deploy_result_reviews.py'))
CONFIGS=(Path('/etc/nginx/sites-available/emeris-live'),Path('/etc/emeris-arena.env'),
    Path('/etc/emeris-arena-models-v2.json'),Path('/etc/systemd/system/emeris-live.service.d/arena.conf'))


def digest(raw):return hashlib.sha256(raw).hexdigest()


def read_archive(path,expected_hash,commit):
    raw=Path(path).read_bytes()
    if len(raw)>4*1024*1024 or digest(raw)!=expected_hash:raise ValueError('Pinned code transport hash differs')
    if not re.fullmatch('[a-f0-9]{40}',commit):raise ValueError('Full source commit required')
    files={}
    with tarfile.open(fileobj=io.BytesIO(raw),mode='r:gz') as tar:
        for member in tar.getmembers():
            p=PurePosixPath(member.name)
            if (member.name not in FILES|{'CODE_RELEASE.json'} or member.name in files or not member.isfile()
                    or str(p)!=member.name or member.size>2*1024*1024):raise ValueError('Unexpected code transport member')
            files[member.name]=tar.extractfile(member).read()
    meta=json.loads(files.pop('CODE_RELEASE.json',b'{}'))
    if (set(files)!=FILES or meta.get('git_commit')!=commit or meta.get('version')!=VERSION
            or meta.get('files')!={n:digest(v) for n,v in files.items()}):raise ValueError('Committed code manifest differs')
    return files


def helpers():
    spec=importlib.util.spec_from_file_location('existing_review_release_helpers',BASE/'scripts/upgrade_arena.py')
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);return mod


def switch(destination):
    marker=CURRENT.with_name('current-arena09-next')
    if marker.exists() or marker.is_symlink():raise ValueError('Concurrent release marker')
    marker.symlink_to(destination);marker.replace(CURRENT)


def restart():subprocess.run(['systemctl','restart','emeris-live'],check=True)


def await_health():
    for attempt in range(30):
        try:
            with urllib.request.urlopen('http://127.0.0.1:19320/api/health',timeout=2) as response:
                if json.load(response).get('version')==VERSION:return
        except (OSError,ValueError):pass
        time.sleep(1)
    raise RuntimeError('New code health did not become ready')


def deploy(archive,expected_hash,commit):
    files=read_archive(archive,expected_hash,commit)
    with (HOME/'.openforecast-deploy.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if (not CURRENT.is_symlink() or CURRENT.resolve()!=BASE or TARGET.exists() or TARGET.is_symlink()
                or any(p.is_symlink() for p in BASE.rglob('*'))):raise ValueError('Expected fixed base and unused code successor')
        h=helpers();d=h.d;manifest=h.verify_manifest(BASE)
        if manifest.get('version')!=BASE_VERSION or manifest.get('git_commit')!=BASE_COMMIT:raise ValueError('Unexpected live code version')
        config_hashes={str(p):d.sha(d.regular(p)) for p in CONFIGS}
        base_hash=d.sha(BASE/'RELEASE_MANIFEST.json')
        events=h.read_events(DATA/'arena.sqlite3');rows=d.sealed_rows(DATA/'workbench.sqlite3')
        backup=HOME/'backups/20261026-arena09';backup.mkdir(parents=True,mode=0o700,exist_ok=False)
        for name in ('arena.sqlite3','workbench.sqlite3'):d.backup_sqlite(DATA/name,backup/name)
        d.exclusive_write(backup/'base-manifest.json',(BASE/'RELEASE_MANIFEST.json').read_bytes())
        shutil.copytree(BASE,TARGET,ignore=shutil.ignore_patterns('__pycache__','.pytest_cache'))
        records={r['path']:r for r in manifest['files']}
        for name,raw in files.items():
            p=TARGET/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw)
            records[name]={'path':name,'sha256':digest(raw),'bytes':len(raw)}
        updated={**manifest,'version':VERSION,'git_commit':commit,'parent_release':str(BASE),
            'created_at_utc':datetime.now(timezone.utc).isoformat(),'files':[records[n] for n in sorted(records)]}
        (TARGET/'RELEASE_MANIFEST.json').write_text(json.dumps(updated,indent=2)+'\n')
        h.verify_manifest(TARGET)
        subprocess.run([PYTHON,'-m','compileall','-q',str(TARGET/'emeris_live')],check=True)
        switched=False
        def preserved():
            if {str(p):d.sha(d.regular(p)) for p in CONFIGS}!=config_hashes:raise ValueError('External configuration changed')
            h.require_prefix(events,h.read_events(DATA/'arena.sqlite3'))
            d.check_sealed_rows(rows,d.sealed_rows(DATA/'workbench.sqlite3'))
            h.verify_manifest(BASE)
        try:
            preserved()
            if CURRENT.resolve()!=BASE or d.sha(BASE/'RELEASE_MANIFEST.json')!=base_hash:raise ValueError('Current release changed before switch')
            switch(TARGET);switched=True;restart();await_health()
            verify=HOME/'arena09-operational.json'
            subprocess.run([PYTHON,str(TARGET/'deploy/verify_server_state.py'),str(verify)],check=True,stdout=subprocess.DEVNULL)
            preserved()
            receipt={'status':'deployed','version':VERSION,'git_commit':commit,'release':str(TARGET),
                'previous_release':str(BASE),'backup':str(backup),'arena_events_before':len(events),
                'arena_events_after':len(h.read_events(DATA/'arena.sqlite3')),'arena_previous_head':h.head(events),
                'arena_current_head':h.head(h.read_events(DATA/'arena.sqlite3')),'database_imports':0,
                'old_arena_events_preserved':True,'old_sealed_rows_preserved':True,'configurations_unchanged':True,
                'code_transport_sha256':expected_hash,'at':datetime.now(timezone.utc).isoformat()}
            d.exclusive_write(HOME/'arena09-receipt.json',json.dumps(receipt,indent=2).encode())
            return receipt
        except BaseException:
            # Roll back only code; never overwrite new data written by users.
            if switched and CURRENT.resolve()==TARGET:switch(BASE);restart()
            raise


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--archive',type=Path,required=True)
    p.add_argument('--sha256',required=True);p.add_argument('--commit',required=True)
    a=p.parse_args();print(json.dumps(deploy(a.archive,a.sha256,a.commit)))


if __name__=='__main__':main()
