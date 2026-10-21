"""Root-owned server job. Keeps daily snapshots for at most fourteen days."""
from datetime import datetime,timezone,timedelta
from pathlib import Path
import json
import shutil
import sys
sys.path.insert(0,'/opt/emeris-live/current')
from backup import backup

now=datetime.now(timezone.utc)
folder=Path('/opt/emeris-live/backups')
dest=folder/('daily-'+now.strftime('%Y%m%dT%H%M%SZ'))
result=backup('/var/lib/emeris-live',dest)
removed=0
for p in folder.glob('daily-*'):
    try:created=datetime.strptime(p.name,'daily-%Y%m%dT%H%M%SZ').replace(tzinfo=timezone.utc)
    except ValueError:continue
    if created<=now-timedelta(days=14) and p.is_dir() and not p.is_symlink():
        shutil.rmtree(p);removed+=1
print(json.dumps({'backup':dest.name,'sha256':result['sha256'],'removed_expired':removed,'key_included':False}))
