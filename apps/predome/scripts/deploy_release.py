"""Operator-driven, pinned 1.1.8 deployment over verified SSH; no credentials in Git."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess
import tarfile
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
REMOTE = '/opt/emeris-live'
TARGET = REMOTE + '/releases/20261021-platform10'
PREVIOUS = REMOTE + '/releases/20261021-platform9'
BOOTSTRAP_PROGRAM = '''from pathlib import Path
import hashlib, sys, tarfile
archive = Path(sys.argv[1])
if hashlib.sha256(archive.read_bytes()).hexdigest() != sys.argv[2]:
    raise ValueError("Pinned release archive SHA-256 differs")
with tarfile.open(archive) as bundle:
    Path(sys.argv[3]).write_bytes(bundle.extractfile('deploy/deploy_upgrade.py').read())
'''

def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()

def runtime_files():
    original=json.loads((ROOT/'source-files-1.1.8.json').read_text())
    return [f for f in original['files'] if Path(f['path']).parts[0] in ('emeris_live','static')
            or (len(Path(f['path']).parts)==1 and (f['path'].endswith('.py') or f['path'].startswith('requirements')))]

def verify_local(archive):
    manifest=json.loads((ROOT/'evidence-files-1.1.8.json').read_text())
    expected=manifest['release_archive_sha256']
    if digest(archive)!=expected:raise ValueError('Wrong release archive SHA-256')
    files=runtime_files()
    with tarfile.open(archive,'r:gz') as tar:
        for f in files:
            raw=tar.extractfile(f['path']).read()
            if hashlib.sha256(raw).hexdigest()!=f['sha256'] or digest(ROOT/f['path'])!=f['sha256']:
                raise ValueError('Repository runtime and release bundle differ: '+f['path'])
    # A provenance receipt must refer to committed bytes, never a dirty checkout.
    commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    prefix=Path(subprocess.check_output(['git','rev-parse','--show-prefix'],cwd=ROOT,text=True).strip())
    for f in files:
        raw=subprocess.check_output(['git','show',commit+':'+str(prefix/f['path'])],cwd=ROOT)
        if hashlib.sha256(raw).hexdigest()!=f['sha256']:raise ValueError('Commit does not contain deployed runtime')
    return commit,files,expected

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--host',required=True);p.add_argument('--identity',type=Path,required=True)
    p.add_argument('--archive',type=Path,required=True)
    action=p.add_mutually_exclusive_group(required=True)
    action.add_argument('--apply',action='store_true');action.add_argument('--verify',action='store_true')
    p.add_argument('--receipt',type=Path,required=True)
    a=p.parse_args()
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9.-]*',a.host):raise ValueError('Invalid SSH host')
    if not a.identity.is_file():raise ValueError('SSH identity not found')
    commit,files,expected=verify_local(a.archive)
    # macOS TMPDIR can exceed the 104-byte Unix socket limit after %C expands.
    # mkdtemp creates a private 0700 directory under the short Unix temp root.
    with tempfile.TemporaryDirectory(prefix='pd-ssh-', dir='/tmp') as control:
        options=['-F','/dev/null','-i',str(a.identity.resolve()),'-o','IdentitiesOnly=yes','-o','BatchMode=yes',
                 '-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=10','-o','ServerAliveInterval=15',
                 '-o','ServerAliveCountMax=3','-o','ControlMaster=auto','-o','ControlPersist=60',
                 '-o','ControlPath='+control+'/%C']
        destination='root@'+a.host
        def ssh(args,stdin=None):
            # Retries only establish the connection; mutating deployments are
            # not blindly rerun after an ambiguous transport failure.
            return subprocess.check_output(['ssh',*options,destination,shlex.join(args)],input=stdin,text=True)
        try:
            for attempt in range(3):
                try:current=ssh(['readlink',REMOTE+'/current']).strip();break
                except subprocess.CalledProcessError:
                    if attempt==2:raise
                    time.sleep(2**attempt)
            if current not in (TARGET,PREVIOUS):raise ValueError('Unexpected deployed base: '+current)
            if a.apply and current==PREVIOUS:
                remote_archive=REMOTE+'/predome-1.1.8.tar.gz'
                subprocess.run(['scp',*options,str(a.archive.resolve()),destination+':'+remote_archive],check=True)
                ssh([REMOTE+'/venv/bin/python','-',remote_archive,expected,REMOTE+'/deploy-platform10.py'],BOOTSTRAP_PROGRAM)
                print(ssh([REMOTE+'/venv/bin/python',REMOTE+'/deploy-platform10.py',remote_archive]))
            elif current!=TARGET:
                raise ValueError('1.1.8 is not deployed; --verify cannot deploy')
            remote_check=REMOTE+'/platform10-verification-20261021/git-check.json'
            receipt=json.loads(ssh([REMOTE+'/venv/bin/python',REMOTE+'/current/deploy/verify_server_state.py',remote_check]))
            if receipt['health']['version']!='1.1.8' or receipt['release']!=TARGET:raise ValueError('Unexpected deployed version')
            remote_manifest=json.loads(ssh(['cat',TARGET+'/RELEASE_MANIFEST.json']))
            published={f['path']:f['sha256'] for f in remote_manifest['files']}
            if any(published.get(f['path'])!=f['sha256'] for f in files):raise ValueError('Remote runtime differs from repository')
            provenance={'schema':'predome.git-deployment.v1','checked_at_utc':datetime.now(timezone.utc).isoformat(),
                        'git_commit':commit,'release':'1.1.8','archive_sha256':expected,'runtime_files_bound':len(files),
                        'source_manifest_sha256':digest(ROOT/'source-files-1.1.8.json'),
                        'remote_manifest_sha256':hashlib.sha256(json.dumps(remote_manifest,sort_keys=True).encode()).hexdigest(),
                        'manifest_hash_scope':'canonical_json_sorted_keys','health':receipt['health'],
                        'sqlite_integrity':receipt['sqlite_integrity'],'release_directory':TARGET,
                        'clinical_prediction_validated':False}
            # Separate receipt; never change the sealed release manifest or data.
            remote=REMOTE+'/platform10-verification-20261021/git-source-'+commit+'.json'
            ssh([REMOTE+'/venv/bin/python','-c','import sys,pathlib; pathlib.Path(sys.argv[1]).write_text(sys.stdin.read())',remote],json.dumps(provenance,indent=2))
            a.receipt.parent.mkdir(parents=True,exist_ok=True);a.receipt.write_text(json.dumps(provenance,indent=2))
            print(json.dumps(provenance))
        finally:
            subprocess.run(['ssh',*options,'-O','exit',destination],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)

if __name__=='__main__':main()
