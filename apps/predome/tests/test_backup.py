import json
import sqlite3
from pathlib import Path
import pytest
from cryptography.fernet import InvalidToken
from backup import backup,restore
from emeris_live.store import Store

def test_backup_restore_without_live_sessions(tmp_path):
    store=Store(tmp_path/'live')
    with store.connect() as db:
        db.execute('INSERT INTO datasets VALUES(?,?,?,?,?)',('dataset','owner','sha',store.pack({'synthetic_test':'recovery'}),'now'))
        db.execute('INSERT INTO sessions VALUES(?,?,?,?)',('token','owner','csrf',9999999999))
        db.execute('INSERT INTO research_runs VALUES(?,?,?,?)',('run','owner',store.pack({'evidence_kind':'synthetic','result':'recovery'}),'now'))
    receipt=backup(store.path,tmp_path/'backup')
    assert not (tmp_path/'backup/master.key').exists()
    result=restore(tmp_path/'backup',tmp_path/'restored',store.path/'master.key')
    assert result['decrypted_datasets']==1
    assert result['decrypted_research_runs']==1
    restored=Store(tmp_path/'restored')
    with restored.connect() as db:
        row=db.execute('SELECT payload FROM research_runs WHERE id=?',('run',)).fetchone()
        assert restored.unpack(row[0])=={'evidence_kind':'synthetic','result':'recovery'}
    with sqlite3.connect(tmp_path/'restored/workbench.sqlite3') as db:assert db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0]==0
    with store.connect() as db:assert db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0]==1
    with pytest.raises(FileExistsError):restore(tmp_path/'backup',tmp_path/'restored',store.path/'master.key')

def test_tampered_backup_rejected_before_destination_creation(tmp_path):
    store=Store(tmp_path/'live');backup(store.path,tmp_path/'backup')
    with (tmp_path/'backup/workbench.sqlite3').open('ab') as f:f.write(b'changed')
    with pytest.raises(ValueError):restore(tmp_path/'backup',tmp_path/'restored',store.path/'master.key')
    assert not (tmp_path/'restored').exists()
