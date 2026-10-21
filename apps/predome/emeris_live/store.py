import contextlib
import json
import os
import sqlite3
from pathlib import Path
from cryptography.fernet import Fernet

class Store:
    def __init__(self, path):
        self.path=Path(path);self.path.mkdir(parents=True,exist_ok=True,mode=0o700);os.chmod(self.path,0o700)
        key=self.path/'master.key'
        if not key.exists():
            try:
                fd=os.open(key,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                with os.fdopen(fd,'wb') as f: f.write(Fernet.generate_key())
            except FileExistsError: pass
        self.cipher=Fernet(key.read_bytes())
        with self.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,name TEXT UNIQUE,password TEXT);
            CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY,owner TEXT,csrf TEXT,expires REAL);
            CREATE TABLE IF NOT EXISTS datasets(id TEXT PRIMARY KEY,owner TEXT,sha TEXT,payload BLOB,created TEXT,UNIQUE(owner,sha));
            CREATE TABLE IF NOT EXISTS policies(id TEXT PRIMARY KEY,owner TEXT,parent TEXT,payload BLOB,created TEXT);
            CREATE TABLE IF NOT EXISTS active(owner TEXT PRIMARY KEY,policy TEXT);
            CREATE TABLE IF NOT EXISTS confirmations(owner TEXT,dataset TEXT,source TEXT,target TEXT,PRIMARY KEY(owner,dataset,source));
            CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,owner TEXT,kind TEXT,payload BLOB,created TEXT);
            CREATE TABLE IF NOT EXISTS auth_limits(bucket TEXT PRIMARY KEY,starts REAL,count INTEGER);
            CREATE TABLE IF NOT EXISTS research_runs(id TEXT PRIMARY KEY,owner TEXT,payload BLOB,created TEXT);
            ''')
        os.chmod(self.path/'workbench.sqlite3',0o600)
    @contextlib.contextmanager
    def connect(self):
        db=sqlite3.connect(self.path/'workbench.sqlite3',timeout=15)
        db.row_factory=sqlite3.Row
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('PRAGMA secure_delete=ON')
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback();raise
        finally: db.close()
    def pack(self,value):return self.cipher.encrypt(json.dumps(value,ensure_ascii=False,allow_nan=False).encode())
    def unpack(self,value):return json.loads(self.cipher.decrypt(value))
