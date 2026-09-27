import json, sqlite3, hashlib
from pathlib import Path
from .models import DatasetVersion
from .datasets import validate
from .io import now, write, load, digest, ensure_storage

class Registry:
    def __init__(self, workspace, state_dir=None):
        self.workspace=ensure_storage(workspace); self.workspace.mkdir(parents=True,exist_ok=True)
        project_hash=hashlib.sha256(str(self.workspace).encode()).hexdigest()[:12]
        self.state_dir=Path(state_dir or Path.home()/'Library'/'Application Support'/'VirtualBiotech'/project_hash).resolve()
        if str(self.state_dir).startswith('/Volumes/'): raise ValueError('Active SQLite registry must reside on local disk')
        self.state_dir.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(self.state_dir/'registry.sqlite3',timeout=30)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('CREATE TABLE IF NOT EXISTS datasets (key TEXT PRIMARY KEY, payload TEXT NOT NULL, detail TEXT NOT NULL, verified_at TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS runs (run_id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
        self.db.commit()
    def close(self): self.db.close()
    def register(self, ds, root):
        key=f'{ds.dataset_id}@{ds.version}'
        payload=ds.model_dump_json()
        old=self.db.execute('SELECT payload FROM datasets WHERE key=?',(key,)).fetchone()
        if old and DatasetVersion.model_validate_json(old[0]).model_dump()!=ds.model_dump(): raise ValueError(f'Immutable dataset version conflict: {key}; create a new version')
        detail=validate(ds,root)
        with self.db: self.db.execute('INSERT OR IGNORE INTO datasets VALUES (?,?,?,?)',(key,payload,json.dumps(detail),now()))
        self.export(); return key
    def get(self,key):
        row=self.db.execute('SELECT payload FROM datasets WHERE key=?',(key,)).fetchone()
        if not row: raise ValueError(f'Unregistered dataset: {key}')
        return DatasetVersion.model_validate_json(row[0])
    def entries(self):
        return [{'key':k,'dataset':json.loads(p),'validation':json.loads(d),'verified_at':t} for k,p,d,t in self.db.execute('SELECT * FROM datasets ORDER BY key')]
    def save_run(self,status):
        with self.db: self.db.execute('INSERT OR REPLACE INTO runs VALUES (?,?)',(status['run_id'],json.dumps(status)))
        self.export()
    def export(self, destination=None):
        obj={'schema_version':1,'exported_at':now(),'datasets':self.entries(),'runs':[json.loads(x[0]) for x in self.db.execute('SELECT payload FROM runs ORDER BY run_id')]}
        write(destination or self.workspace/'artifacts'/'registry_export.json',obj)
        return obj
    def restore(self, backup, root):
        obj=load(backup)
        if obj.get('schema_version')!=1: raise ValueError('Unsupported registry export')
        # Validate all entries BEFORE writing; a partial restore is not accepted.
        pending=[]
        for entry in obj['datasets']:
            ds=DatasetVersion.model_validate(entry['dataset']); detail=validate(ds,root)
            key=f'{ds.dataset_id}@{ds.version}'
            if key!=entry['key']: raise ValueError('Export dataset key mismatch')
            old=self.db.execute('SELECT payload FROM datasets WHERE key=?',(key,)).fetchone()
            if old and DatasetVersion.model_validate_json(old[0]).model_dump()!=ds.model_dump(): raise ValueError('Restore conflicts with local version')
            pending.append((key,ds.model_dump_json(),json.dumps(detail),now()))
        from .engine import verify_run
        for run in obj['runs']:
            result=verify_run(self.workspace/'artifacts'/'runs'/run['run_id'])
            if run.get('manifest_sha256')!=digest(self.workspace/'artifacts'/'runs'/run['run_id']/'manifest.json'): raise ValueError('Run manifest differs from trusted export anchor')
            if not result['integrity_ok']: raise ValueError(f"Cannot restore invalid run: {run['run_id']}")
        with self.db:
            self.db.executemany('INSERT OR IGNORE INTO datasets VALUES (?,?,?,?)',pending)
            self.db.executemany('INSERT OR REPLACE INTO runs VALUES (?,?)',[(r['run_id'],json.dumps(r)) for r in obj['runs']])
        return len(pending)
