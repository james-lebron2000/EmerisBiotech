"""Versioned deployment from the deployed v1 baseline; retains rollback and data backup."""
from pathlib import Path
from datetime import datetime, timezone
import fcntl, hashlib, json, os, shutil, sqlite3, subprocess, sys, tarfile, time, urllib.request
import xml.etree.ElementTree as ET
BASE=Path('/opt/emeris-live')
OLD=BASE/'releases/20261021-platform9'
NEW=BASE/'releases/20261021-platform10'
RECEIPTS=BASE/'platform10-verification-20261021'
PY=BASE/'venv/bin/python'
QA=Path('/opt/emeris-research/20261019-v4/venv/bin/python')
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def now():return datetime.now(timezone.utc).isoformat()
def get(route):
    req=urllib.request.Request('http://127.0.0.1:19320'+route,headers={'Host':'8.148.145.28'})
    with urllib.request.urlopen(req,timeout=30) as response:return json.load(response)
def switch(target):
    link=BASE/'current.platform-next'
    if link.is_symlink():link.unlink()
    link.symlink_to(target);link.replace(BASE/'current')
    subprocess.run(['systemctl','restart','emeris-live'],check=True)
def main(archive):
    if (BASE/'current').resolve()!=OLD:raise ValueError('Base changed, stop concurrent deployment')
    if NEW.exists():raise FileExistsError('Never overwrite a prior release')
    original=json.loads((OLD/'RELEASE_MANIFEST.json').read_text())
    for item in original['files']:
        if sha(OLD/item['path'])!=item['sha256']:raise ValueError('Original source drift: '+item['path'])
    RECEIPTS.mkdir(exist_ok=True)
    shutil.copytree(OLD,NEW,ignore=shutil.ignore_patterns('__pycache__','.pytest_cache'))
    with tarfile.open(archive) as tar:
        members=tar.getmembers()
        if any(not m.isfile() or Path(m.name).is_absolute() or '..' in Path(m.name).parts for m in members):raise ValueError('Unsafe overlay')
        tar.extractall(NEW)
    manifest=json.loads((NEW/'RELEASE_MANIFEST.json').read_text())
    for item in manifest['files']:
        if sha(NEW/item['path'])!=item['sha256']:raise ValueError('New source hash mismatch: '+item['path'])
    # All prior clinical payloads and bound modeling code must retain their bytes.
    for item in original['files']:
        if item['path'].startswith(('predome_data/','trial_data/')) and sha(NEW/item['path'])!=item['sha256']:
            raise ValueError('Frozen clinical data changed')
    browser=json.loads((NEW/'verification/upgrade/browser-local-1.1.8/receipt.json').read_text())
    if browser['status']!='passed' or browser['page_errors']:raise ValueError('Browser QA missing')
    env=os.environ.copy();env.update(EMERIS_DATA_DIR=str(RECEIPTS/'qa-store'),EMERIS_MODE='local',OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1')
    junit=RECEIPTS/'tests-server.xml'
    subprocess.run([str(QA),'-m','pytest','tests','-q','--junitxml='+str(junit)],cwd=NEW,env=env,check=True)
    suites=ET.parse(junit).getroot().findall('testsuite')
    stats={k:sum(int(s.get(k,0)) for s in suites) for k in ('tests','failures','errors','skipped')}
    stats['passed']=stats['tests']-stats['failures']-stats['errors']-stats['skipped']
    backup=BASE/'backups'/('pre-platform-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    subprocess.run([str(PY),str(OLD/'backup.py'),'backup','/var/lib/emeris-live',str(backup)],check=True)
    bm=json.loads((backup/'manifest.json').read_text());db=backup/'workbench.sqlite3'
    if sha(db)!=bm['sha256']:raise ValueError('Backup hash mismatch')
    with sqlite3.connect('file:'+str(db)+'?mode=ro',uri=True) as con:
        if con.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Invalid backup')
    prior={route:get(route)['sha256'] for route in ['/api/predome/research','/api/predome/historical','/api/predome/expanded','/api/predome/continuous']}
    old_forecast=get('/api/forecasts')['head_sha256']
    if (BASE/'current').resolve()!=OLD:raise ValueError('Current changed during tests')
    try:
        # Additive, idempotent import preserves original forecast times and bytes.
        migration=subprocess.run(['runuser','-u','emeris','--','env','EMERIS_DATA_DIR=/var/lib/emeris-live',str(PY),'-c',
          'import json; from emeris_live.store import Store; from emeris_live.platform_store import import_legacy_archive; print(json.dumps(import_legacy_archive(Store("/var/lib/emeris-live"))))'],cwd=NEW,text=True,capture_output=True,check=True)
        (RECEIPTS/'legacy-import.json').write_text(migration.stdout)
        baseline_migration=subprocess.run(['runuser','-u','emeris','--','env','EMERIS_DATA_DIR=/var/lib/emeris-live',str(PY),'-c',
          'import json; from emeris_live.store import Store; from emeris_live.prospective_baselines import register_existing_public_baselines; print(json.dumps(register_existing_public_baselines(Store("/var/lib/emeris-live"))))'],cwd=NEW,text=True,capture_output=True,check=True)
        (RECEIPTS/'baseline-registration.json').write_text(baseline_migration.stdout)
        switch(NEW)
        for _ in range(30):
            try:
                if get('/api/health')['version']=='1.1.8':break
            except Exception:pass
            time.sleep(1)
        else:raise RuntimeError('New release did not become healthy')
        dashboard=get('/api/dashboard')
        if dashboard['summary']['upcoming_trial_count']!=7 or dashboard['summary']['historical_trial_count']!=8:raise ValueError('Dashboard coverage changed')
        if dashboard['provenance']['prospective_record_sha256']!=browser['provenance']['prospective_record_sha256']:raise ValueError('Forecast changed')
        for route,value in prior.items():
            record=get(route)
            if record['sha256']!=value or not record.get('integrity_verified'):raise ValueError('Prior release changed')
        if get('/api/forecasts')['head_sha256']!=old_forecast:raise ValueError('Original PACIFIC forecast changed')
        public=get('/api/product/dashboard')
        if public['summary']['public_user_locked_predictions']!=0:raise ValueError('Unexpected public user records; inspect before activation')
        if len(get('/api/platform/snapshots')['records'])!=10:raise ValueError('Legacy snapshots missing')
        if get('/api/analytics')['summary']['n']!=0:raise ValueError('Unexpected prospective results; inspect')
        baseline_summary=get('/api/platform/baselines')['summary']
        if baseline_summary['registered_snapshot_count']!=10 or baseline_summary['missing_registration_count']!=0:
            raise ValueError('Public baseline registration incomplete')
        if baseline_summary['pre_readout_registered_verified_count']!=0:
            raise ValueError('Unexpected baseline results; inspect')
        receipt={'status':'activated','release':str(NEW),'previous':str(OLD),'activated_at_utc':now(),'overlay_sha256':sha(archive),'source_manifest_sha256':sha(NEW/'RELEASE_MANIFEST.json'),'files':len(manifest['files']),'tests':stats,'backup':str(backup),'backup_sha256':bm['sha256'],'backup_integrity':'ok','prior_hashes_unchanged':prior,'pacific_forecast_unchanged':old_forecast,'dashboard':dashboard['summary'],'health':get('/api/health')}
        receipt['baseline_registration_summary']=baseline_summary
        (RECEIPTS/'release-receipt.json').write_text(json.dumps(receipt,indent=2));print(json.dumps(receipt))
    except Exception:
        if (BASE/'current').resolve()==NEW:switch(OLD)
        raise
if __name__=='__main__':
    lock=(BASE/'predome-deploy.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX)
    main(Path(sys.argv[1]))
