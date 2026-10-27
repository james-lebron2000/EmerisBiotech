"""Publish only the original fixed historical kit; leave app and ledger intact.

This three-script publisher is not a deployment path for newer campaign bundles.
New calendar-aware exports require verification in the complete Predome repository.
"""
import argparse
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import tarfile
import tempfile
import urllib.request

import public_snapshot_proof as proof

ROOT=Path(__file__).resolve().parents[1]
SITE=Path('/etc/nginx/sites-available/emeris-live')
BACKUP=Path('/etc/nginx/emeris-live.before-public-proof-20261023.conf')
DEST=Path('/opt/emeris-live/public-forecast/20261023-clinical-v1')
RECEIPT=Path('/opt/emeris-live/public-proof-publication-20261023.json')
TRANSPORT=Path('/opt/emeris-live/public-proof-transport-20261023.tar.gz')
TOOLS=Path('/opt/emeris-live/public-proof-tools-20261023')
LOCK=Path('/opt/emeris-live/.public-proof-publish.lock')
HOST='8.148.145.28'
BASE='https://'+HOST
URL_PREFIX='/public-forecast/20261023-clinical-v1/'
EXPECTED_SITE='9044ac171851a7b09e604449ed2a234b5b232d78800cf8784a7548a2ad5dadd6'
SCRIPT_NAMES=('publish_public_proof.py','public_snapshot_proof.py','export_public_campaign.py')
HISTORICAL_BUNDLE_SHA256='1a0144906b743e03c50b13b2c1e9554ec488381b88e4939d9d8ec53fa116825c'


def digest(raw):return hashlib.sha256(raw).hexdigest()


def render(old):
    marker=b'    location / {\n        limit_req zone=emeris_api burst=40 nodelay;\n'
    if old.count(marker)!=1 or b'/public-forecast/' in old:raise ValueError('Unexpected existing frontend configuration')
    locations='    location ^~ /public-forecast/ { return 404; }\n'
    for name in sorted(proof.PUBLIC_FILES):
        locations+='    location = '+URL_PREFIX+name+' {\n'
        locations+='        alias '+str(DEST/name)+';\n'
        locations+='        limit_except GET HEAD { deny all; }\n'
        locations+='        default_type application/octet-stream;\n'
        locations+='        types { }\n'
        locations+='        add_header X-Content-Type-Options nosniff always;\n'
        locations+='        add_header Cache-Control "public, max-age=31536000, immutable" always;\n'
        locations+='        add_header Content-Disposition \'attachment; filename="'+name+'"\';\n'
        locations+='    }\n'
    return old.replace(marker,locations.encode()+marker,1)


def load_transport(raw):
    if len(raw)>16*1024*1024:raise ValueError('Oversized public proof transport')
    with tarfile.open(fileobj=io.BytesIO(raw),mode='r:gz') as archive:
        members=archive.getmembers();names=[m.name for m in members]
        expected={'assets/'+name for name in proof.PUBLIC_FILES}|{'TRANSPORT.json'}
        if set(names)!=expected or len(names)!=len(expected) or any(not m.isfile() or m.size>8*1024*1024 for m in members):
            raise ValueError('Unexpected transport member, path or link')
        if sum(m.size for m in members)>16*1024*1024:raise ValueError('Expanded public transport too large')
        files={m.name:archive.extractfile(m).read() for m in members}
    meta=proof.decode(files.pop('TRANSPORT.json'))
    if (set(meta)!={'schema_version','source_commit','files'} or meta['schema_version']!='public-proof-publication-1'
            or set(meta['files'])!=set(files) or any(digest(raw)!=meta['files'][name] for name,raw in files.items())):
        raise ValueError('Transport metadata/hash mismatch')
    return {name.removeprefix('assets/'):value for name,value in files.items()},meta


def require_historical_bundle(raw):
    if digest(raw)!=HISTORICAL_BUNDLE_SHA256:
        raise ValueError('This fixed historical publisher accepts only the original 20261023 bundle; newer campaign bundles require the complete Predome repository and a separate publication plan')


def remote(transport_sha256,expected_site):
    if expected_site!=EXPECTED_SITE:raise ValueError('Fixed previously verified frontend hash required')
    if LOCK.is_symlink():raise ValueError('Unsafe publication lock path')
    with open(LOCK,'a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        raw=proof.regular(TRANSPORT,16*1024*1024)
        if digest(raw)!=transport_sha256:raise ValueError('Transport changed after upload')
        files,meta=load_transport(raw)
        require_historical_bundle(files['clinical-records.zip'])
        old=proof.regular(SITE)
        if digest(old)!=expected_site or any(path.exists() or path.is_symlink() for path in (BACKUP,RECEIPT,DEST)):
            raise ValueError('Frontend changed or publication already attempted; inspect receipts')
        new=render(old)
        with tempfile.TemporaryDirectory(prefix='public-proof-verify-') as temporary:
            candidate=Path(temporary)
            for name,value in files.items():proof.exclusive(candidate/name,value)
            fields=proof.verify(candidate,require_outer=True)
        req=urllib.request.Request('http://127.0.0.1:19320/api/health',headers={'Host':HOST})
        with urllib.request.urlopen(req,timeout=15) as response:
            if json.load(response).get('version')!='1.2.1':raise ValueError('Unexpected current application version')
        parent=DEST.parent
        if parent.is_symlink() or (parent.exists() and not parent.is_dir()):raise ValueError('Unsafe public destination parent')
        if not parent.exists():parent.mkdir(mode=0o755);parent.chmod(0o755)
        if parent.stat().st_mode&0o555!=0o555:raise ValueError('Existing public parent is not readable')
        DEST.mkdir(mode=0o755);DEST.chmod(0o755)
        for name,value in files.items():
            proof.exclusive(DEST/name,value,0o644);(DEST/name).chmod(0o644)
        # Stage all approved bytes before exposing any location. No release file,
        # application process, session, user, provider config or DB is touched.
        proof.exclusive(BACKUP,old)
        replaced=False
        try:
            if proof.regular(SITE)!=old:raise ValueError('Concurrent frontend modification')
            staged=SITE.with_name(SITE.name+'.public-proof.tmp')
            proof.exclusive(staged,new,0o644);os.replace(staged,SITE);replaced=True
            subprocess.run(['nginx','-t'],check=True,capture_output=True)
            subprocess.run(['systemctl','reload','nginx'],check=True)
            receipt={'status':'published','source_commit':meta['source_commit'],
                'public_url':BASE+URL_PREFIX+'verification-kit.zip','public_prefix':BASE+URL_PREFIX,
                'previous_nginx_sha256':digest(old),'nginx_sha256':digest(new),'backup':str(BACKUP),
                'files':{name:digest(value) for name,value in sorted(files.items())},
                'proof':fields,'application_version_unchanged':'1.2.1','application_restarted':False,
                'ledger_writes':0,'provider_calls':0,'unrelated_nginx_sites_modified':False}
            proof.exclusive(RECEIPT,proof.campaign.canonical(receipt))
            print(json.dumps(receipt))
        except BaseException:
            # The complete public data folder is kept unserved for inspection.
            # Restore only this site's bytes, then test and hot-reload.
            if replaced:
                if proof.regular(SITE)!=new:raise RuntimeError('Concurrent frontend change prevents safe rollback')
                restore=SITE.with_name(SITE.name+'.public-proof-restore.tmp')
                proof.exclusive(restore,old,0o644);os.replace(restore,SITE)
                subprocess.run(['nginx','-t'],check=True,capture_output=True)
                subprocess.run(['systemctl','reload','nginx'],check=True)
            raise


def local(identity,directory,receipt):
    if receipt.exists() or receipt.is_symlink():raise ValueError('New local receipt required')
    require_historical_bundle(proof.regular(directory/'clinical-records.zip'))
    fields=proof.verify(directory,require_outer=True)
    commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    sources={name:subprocess.check_output(['git','show',commit+':apps/predome/scripts/'+name],cwd=ROOT) for name in SCRIPT_NAMES}
    if any(proof.regular(ROOT/'scripts'/name)!=value for name,value in sources.items()):
        raise ValueError('Publish committed operator sources only')
    files={'assets/'+name:proof.regular(directory/name) for name in proof.PUBLIC_FILES}
    meta={'schema_version':'public-proof-publication-1','source_commit':commit,'files':{n:digest(v) for n,v in files.items()}}
    buffer=io.BytesIO()
    with tarfile.open(fileobj=buffer,mode='w:gz') as archive:
        for name,value in sorted({**files,'TRANSPORT.json':proof.campaign.canonical(meta)}.items()):
            item=tarfile.TarInfo(name);item.size=len(value);item.mode=0o600;archive.addfile(item,io.BytesIO(value))
    opts=['-F','/dev/null','-i',str(identity.resolve()),'-o','IdentitiesOnly=yes','-o','BatchMode=yes',
        '-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=10','-o','ServerAliveInterval=15','-o','ServerAliveCountMax=3']
    destination='root@'+HOST
    check=' && '.join('test ! -e '+shlex.quote(str(path))+' && test ! -L '+shlex.quote(str(path))
                      for path in (TOOLS,TRANSPORT,DEST,BACKUP,RECEIPT))
    subprocess.run(['ssh',*opts,destination,check],check=True)
    subprocess.run(['ssh',*opts,destination,'mkdir -m 700 '+shlex.quote(str(TOOLS))],check=True)
    with tempfile.TemporaryDirectory(prefix='public-proof-upload-') as temporary:
        temporary=Path(temporary);paths=[]
        for name,value in {**sources,'transport.tar.gz':buffer.getvalue()}.items():
            path=temporary/name;path.write_bytes(value);path.chmod(0o600)
            remote_path=TRANSPORT if name=='transport.tar.gz' else TOOLS/name
            subprocess.run(['scp',*opts,str(path),destination+':'+str(remote_path)],check=True)
            paths.append((path,remote_path))
        pins=''.join(digest(path.read_bytes())+'  '+str(target)+'\n' for path,target in paths)
        subprocess.run(['ssh',*opts,destination,'sha256sum --check --status -'],input=pins,text=True,check=True)
        command=['/opt/emeris-live/venv/bin/python',str(TOOLS/'publish_public_proof.py'),'--remote',
            '--transport-sha256',digest(buffer.getvalue()),'--expected-site-sha256',EXPECTED_SITE]
        result=proof.decode(subprocess.check_output(['ssh',*opts,destination,shlex.join(command)]))
    receipt.parent.mkdir(parents=True,exist_ok=True);proof.exclusive(receipt,proof.campaign.canonical(result))
    print(json.dumps({'status':result['status'],'public_url':result['public_url'],'signed_at_utc':fields['generated_at_utc']}))


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--remote',action='store_true')
    p.add_argument('--identity',type=Path);p.add_argument('--directory',type=Path);p.add_argument('--receipt',type=Path)
    p.add_argument('--transport-sha256');p.add_argument('--expected-site-sha256');a=p.parse_args()
    if a.remote:
        if any((a.identity,a.directory,a.receipt)):p.error('Remote requires pinned hashes only')
        remote(a.transport_sha256,a.expected_site_sha256)
    else:
        if not all((a.identity,a.directory,a.receipt)):p.error('Identity, existing proof directory and new receipt are required')
        local(a.identity,a.directory,a.receipt)


if __name__=='__main__':main()
