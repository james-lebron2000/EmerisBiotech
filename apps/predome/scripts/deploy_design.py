"""Deploy the committed Editorial 01 static overlay onto verified Predome 1.1.8.

Run locally with --host and --identity. The remote mode accepts only a pinned
archive hash and a six-file allowlist; it preserves the previous release.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
FILES = ['static/'+name for name in ('dashboard.html','track-record.html','trial-detail.html','analytics.html','predict.html','design-system.css')]
BASE = Path('/opt/emeris-live/releases/20261021-platform10')
TARGET = Path('/opt/emeris-live/releases/20261021-editorial01')
CURRENT = Path('/opt/emeris-live/current')
PYTHON = '/opt/emeris-live/venv/bin/python'

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def remote(archive, expected, commit):
    if sha(archive) != expected:
        raise ValueError('Archive hash mismatch')
    if CURRENT.resolve() != BASE or TARGET.exists():
        raise ValueError('Expected untouched base release and unused destination')
    before_path=Path('/opt/emeris-live/editorial01-before.json')
    before=json.loads(subprocess.check_output([PYTHON,str(BASE/'deploy/verify_server_state.py'),str(before_path)],text=True))
    with tarfile.open(archive) as tar:
        members=tar.getmembers()
        if len(members)!=len(FILES) or {m.name for m in members}!=set(FILES) or not all(m.isfile() for m in members):
            raise ValueError('Static overlay must contain exactly the six approved regular files')
        content={m.name:tar.extractfile(m).read() for m in members}
    shutil.copytree(BASE,TARGET,ignore=shutil.ignore_patterns('__pycache__','.pytest_cache'))
    for name,raw in content.items():
        (TARGET/name).write_bytes(raw)
    manifest=json.loads((TARGET/'RELEASE_MANIFEST.json').read_text())
    records={r['path']:r for r in manifest['files']}
    for name in FILES:
        records[name]={'path':name,'sha256':sha(TARGET/name),'bytes':(TARGET/name).stat().st_size}
    manifest.update(files=[records[k] for k in sorted(records)],ui_revision='editorial-01',git_commit=commit,parent_release=str(BASE))
    (TARGET/'RELEASE_MANIFEST.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    # Check every inherited frozen file as well as the changed presentation files.
    for item in manifest['files']:
        if sha(TARGET/item['path']) != item['sha256']:
            raise ValueError('Release integrity mismatch: '+item['path'])
    link=CURRENT.with_name('current-editorial-next')
    if link.exists() or link.is_symlink():
        raise ValueError('Another switch may be in progress')
    switched=False
    try:
        link.symlink_to(TARGET);link.replace(CURRENT);switched=True
        subprocess.run(['systemctl','restart','emeris-live'],check=True)
        after=None
        for attempt in range(10):
            try:
                after=json.loads(subprocess.check_output([PYTHON,str(TARGET/'deploy/verify_server_state.py'),'/opt/emeris-live/editorial01-after.json'],text=True,stderr=subprocess.PIPE));break
            except subprocess.CalledProcessError:
                if attempt==9:raise
                time.sleep(1)
        for table in ('platform_prediction_snapshots','platform_prospective_baselines','platform_actual_outcomes','platform_outcome_verifications'):
            if before['counts'][table]!=after['counts'][table]:
                raise ValueError('Clinical ledger count changed during static deployment')
        receipt={'status':'deployed','ui_revision':'editorial-01','application_version':after['health']['version'],'git_commit':commit,
                 'checked_at_utc':datetime.now(timezone.utc).isoformat(),'archive_sha256':expected,
                 'previous_release':str(BASE),'release':str(TARGET),'static_files':{n:sha(TARGET/n) for n in FILES},
                 'inherited_files_verified':len(manifest['files'])-len(FILES),'before':before,'after':after}
        Path('/opt/emeris-live/editorial01-release.json').write_text(json.dumps(receipt,indent=2))
        print(json.dumps(receipt))
    except BaseException:
        if switched:
            link.symlink_to(BASE);link.replace(CURRENT)
            subprocess.run(['systemctl','restart','emeris-live'],check=True)
        raise

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--host');p.add_argument('--identity',type=Path);p.add_argument('--receipt',type=Path)
    p.add_argument('--remote',action='store_true');p.add_argument('--archive');p.add_argument('--sha256');p.add_argument('--commit')
    a=p.parse_args()
    if a.remote:
        remote(a.archive,a.sha256,a.commit);return
    import re
    if not a.host or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9.-]*',a.host) or not a.identity or not a.identity.is_file() or not a.receipt:
        p.error('Local deployment requires --host, --identity and --receipt')
    commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    with tempfile.TemporaryDirectory(prefix='pd-ui-',dir='/tmp') as temp:
        temp=Path(temp);archive=temp/'editorial01.tar.gz'
        with tarfile.open(archive,'w:gz') as tar:
            for name in FILES:
                committed=subprocess.check_output(['git','show',commit+':apps/predome/'+name],cwd=ROOT)
                if committed!=(ROOT/name).read_bytes():raise ValueError('Uncommitted static file: '+name)
                tar.add(ROOT/name,arcname=name,recursive=False)
        # The remotely executed operator code must also be committed.
        if subprocess.check_output(['git','show',commit+':apps/predome/scripts/deploy_design.py'],cwd=ROOT)!=Path(__file__).read_bytes():
            raise ValueError('Uncommitted deployment script')
        opts=['-F','/dev/null','-i',str(a.identity.resolve()),'-o','IdentitiesOnly=yes','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=10','-o','ServerAliveInterval=15','-o','ServerAliveCountMax=3','-o','ControlMaster=auto','-o','ControlPersist=60','-o','ControlPath='+str(temp/'%C')]
        dest='root@'+a.host
        try:
            subprocess.run(['scp',*opts,str(archive),dest+':/opt/emeris-live/editorial01.tar.gz'],check=True)
            subprocess.run(['scp',*opts,str(Path(__file__).resolve()),dest+':/opt/emeris-live/deploy-editorial01.py'],check=True)
            command=[PYTHON,'/opt/emeris-live/deploy-editorial01.py','--remote','--archive','/opt/emeris-live/editorial01.tar.gz','--sha256',sha(archive),'--commit',commit]
            raw=subprocess.check_output(['ssh',*opts,dest,shlex.join(command)],text=True)
            receipt=json.loads(raw);a.receipt.parent.mkdir(parents=True,exist_ok=True);a.receipt.write_text(json.dumps(receipt,indent=2)+'\n')
            print(json.dumps({k:receipt[k] for k in ('status','ui_revision','git_commit','release','application_version','inherited_files_verified')}))
        finally:
            subprocess.run(['ssh',*opts,'-O','exit',dest],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)

if __name__=='__main__':main()
