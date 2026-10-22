"""Code release tests with isolated SQLite; no network, SSH or systemd."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import sys
import tarfile

import pytest
from test_arena_core import registered
from test_deploy_arena import make_workbench

sys.path.insert(0,str(Path(__file__).parents[1]/'scripts'))
import deploy_result_reviews as release
import upgrade_arena as existing


def archive(tmp_path,extra=None):
    files={n:b'isolated source fixture' for n in release.FILES}
    meta={'version':release.VERSION,'git_commit':'a'*40,'files':{n:release.digest(v) for n,v in files.items()}}
    files['CODE_RELEASE.json']=json.dumps(meta).encode()
    if extra:files.update(extra)
    p=tmp_path/'code.tar.gz'
    with tarfile.open(p,'w:gz') as tar:
        for n,v in files.items():
            item=tarfile.TarInfo(n);item.size=len(v);tar.addfile(item,io.BytesIO(v))
    return p,release.digest(p.read_bytes()),'a'*40


def test_transport_is_exact_code_allowlist(tmp_path):
    assert set(release.read_archive(*archive(tmp_path)))==release.FILES


@pytest.mark.parametrize('name',['arena.sqlite3','arena_data/receipt.json','../escape.py','static/arena.css','scripts/arena_admin.py'])
def test_transport_cannot_modify_runtime_or_sealed_runner(tmp_path,name):
    with pytest.raises(ValueError,match='Unexpected code transport'):release.read_archive(*archive(tmp_path,{name:b'forbidden'}))


def test_wrong_pinned_hash_rejected(tmp_path):
    p,_,commit=archive(tmp_path)
    with pytest.raises(ValueError,match='hash differs'):release.read_archive(p,'f'*64,commit)


@pytest.fixture
def isolated(tmp_path,monkeypatch):
    home=tmp_path/'app';base=home/'releases/base';base.mkdir(parents=True);data=tmp_path/'data'
    ledger,_,_=registered(data);make_workbench(data/'workbench.sqlite3')
    sealed=base/'arena_data/sealed.json';sealed.parent.mkdir();sealed.write_bytes(b'original frozen data')
    manifest={'version':'1.2.2','git_commit':release.BASE_COMMIT,'files':[{'path':'arena_data/sealed.json','sha256':release.digest(sealed.read_bytes()),'bytes':sealed.stat().st_size}]}
    (base/'RELEASE_MANIFEST.json').write_text(json.dumps(manifest))
    current=home/'current';current.symlink_to(base);config=home/'config';config.write_bytes(b'private config fixture')
    monkeypatch.setattr(release,'BASE',base);monkeypatch.setattr(release,'TARGET',home/'releases/new')
    monkeypatch.setattr(release,'CURRENT',current);monkeypatch.setattr(release,'DATA',data)
    monkeypatch.setattr(release,'HOME',home);monkeypatch.setattr(release,'CONFIGS',(config,))
    monkeypatch.setattr(release,'helpers',lambda:existing)
    calls=[];monkeypatch.setattr(release,'restart',lambda:calls.append('restart'))
    monkeypatch.setattr(release,'await_health',lambda:None)
    monkeypatch.setattr(release.subprocess,'run',lambda *a,**k:None)
    return home,base,data,ledger,calls


def test_code_only_success_preserves_database_and_sealed_files(tmp_path,isolated):
    home,base,data,ledger,calls=isolated;events=ledger.events()
    result=release.deploy(*archive(tmp_path))
    assert result['database_imports']==0 and result['old_arena_events_preserved']
    assert ledger.events()==events and calls==['restart']
    assert (home/'current/arena_data/sealed.json').read_bytes()==b'original frozen data'
    assert (home/'backups/20261023-publiccompare04/arena.sqlite3').is_file()
    assert (home/'current').resolve()==release.TARGET


def test_failed_health_rolls_back_only_code_and_preserves_concurrent_data(tmp_path,isolated,monkeypatch):
    home,base,data,ledger,calls=isolated;events=ledger.events()
    def failure():
        with sqlite3.connect(data/'workbench.sqlite3') as db:
            db.execute('INSERT INTO platform_prediction_snapshots VALUES(2,"concurrent user record")')
        raise RuntimeError('isolated health failure')
    monkeypatch.setattr(release,'await_health',failure)
    with pytest.raises(RuntimeError,match='health failure'):release.deploy(*archive(tmp_path))
    assert (home/'current').resolve()==base and calls==['restart','restart']
    assert ledger.events()==events
    with sqlite3.connect(data/'workbench.sqlite3') as db:
        assert db.execute('SELECT value FROM platform_prediction_snapshots WHERE id=2').fetchone()[0]=='concurrent user record'
