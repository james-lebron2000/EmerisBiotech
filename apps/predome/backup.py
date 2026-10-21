"""Private local backup/recovery utility. Backups deliberately omit the master key."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
from emeris_live.store import Store

def backup(source,dest):
    source=Path(source);dest=Path(dest);dest.mkdir(mode=0o700,parents=True,exist_ok=False)
    out=dest/'workbench.sqlite3'
    with sqlite3.connect(source/'workbench.sqlite3') as src,sqlite3.connect(out) as target:src.backup(target)
    os.chmod(out,0o600)
    manifest={'sha256':hashlib.sha256(out.read_bytes()).hexdigest(),'key_included':False}
    (dest/'manifest.json').write_text(json.dumps(manifest,indent=2))
    return manifest

def restore(source,dest,key):
    source=Path(source);dest=Path(dest);key=Path(key)
    manifest=json.loads((source/'manifest.json').read_text());dbfile=source/'workbench.sqlite3'
    if hashlib.sha256(dbfile.read_bytes()).hexdigest()!=manifest['sha256']:raise ValueError('Backup hash mismatch')
    dest.mkdir(mode=0o700,parents=True,exist_ok=False)
    shutil.copyfile(dbfile,dest/'workbench.sqlite3');shutil.copyfile(key,dest/'master.key');os.chmod(dest/'master.key',0o600)
    store=Store(dest)
    with store.connect() as db:
        if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Invalid SQLite database')
        count=0
        for row in db.execute('SELECT payload FROM datasets'):store.unpack(row[0]);count+=1
        runs=0
        for row in db.execute('SELECT payload FROM research_runs'):store.unpack(row[0]);runs+=1
        db.execute('DELETE FROM sessions')
    return {'integrity':'ok','decrypted_datasets':count,'decrypted_research_runs':runs,'sessions_removed':True,'restored_to_new_directory':True}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['backup','restore']);p.add_argument('source');p.add_argument('destination');p.add_argument('--key');a=p.parse_args()
    if a.action=='restore' and not a.key:p.error('restore requires --key pointing to separately retained master.key')
    print(json.dumps(backup(a.source,a.destination) if a.action=='backup' else restore(a.source,a.destination,a.key),indent=2))
