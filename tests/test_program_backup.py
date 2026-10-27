"""Backup, corruption, interruption and recovery tests use synthetic data."""
import hashlib
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlencode

import pytest
from test_programs import PROFILE, add, experiment
from test_program_diligence import linked
from test_ai_research import ready
from test_research_queue import create
from vbt.program_backup import MARKER, snapshot, verify, restore, state_inventory, source_status
from vbt.programs import Programs, canonical
from vbt.workbench import Workbench, handler, page

RELEASE='a'*40


def test_wal_snapshot_exact_exports_sequences_and_no_credentials(tmp_path):
    s=Programs(tmp_path/'source');pid=s.create(PROFILE,'fixture author');add(s,pid)
    with s.db() as db:assert db.execute('PRAGMA journal_mode=WAL').fetchone()[0]=='wal'
    (s.state/'secrets.json').write_text('SYNTHETIC_CONFIG_SECRET_NOT_IN_BACKUP')
    (s.state/'worker.lock').write_text('synthetic lock file, not a worker identity')
    before=s.export(pid);pack=tmp_path/'backup'
    manifest=snapshot(s.state,pack,RELEASE)
    assert manifest==verify(pack)
    assert {p.name for p in pack.iterdir()}=={'manifest.json','programs.sqlite3'}
    assert 'SYNTHETIC_CONFIG_SECRET_NOT_IN_BACKUP' not in (pack/'programs.sqlite3').read_bytes().decode('latin1')
    assert manifest['inventory']['sequence']==2 and manifest['inventory']['events']==2
    restored=tmp_path/'restored';report=restore(pack,restored)
    other=Programs(restored)
    assert other.read_only and other.export(pid)==before and s.export(pid)==before
    assert report['record_integrity']=='verified' and report['source_integrity']=='not_checked'
    assert (restored/MARKER).exists() and not (restored/'worker.lock').exists()
    assert (pack/'programs.sqlite3').stat().st_mode & 0o777==0o600
    with pytest.raises(ValueError,match='只读'):other.create(PROFILE,'no restored writer')
    with other.db() as db:
        with pytest.raises(sqlite3.OperationalError):db.execute("INSERT INTO program_events(id) VALUES('forbidden')")
    assert s.get(pid)['events'][0]['author']=='fixture author'


def test_concurrent_append_during_online_snapshot_produces_consistent_frozen_view(tmp_path):
    s=Programs(tmp_path/'source');pid=s.create(PROFILE,'fixture author');add(s,pid)
    with s.db() as db:db.execute('PRAGMA journal_mode=WAL')
    errors=[]
    def writer():
        try:
            for i in range(25):
                s._append(pid,'engineering_note',{'index':i},'synthetic writer',s.get(pid)['revision'])
        except Exception as exc:errors.append(exc)
    thread=threading.Thread(target=writer);thread.start()
    first=snapshot(s.state,tmp_path/'backup',RELEASE);thread.join()
    assert not errors
    assert first==verify(tmp_path/'backup')
    recovered=restore(tmp_path/'backup',tmp_path/'restore')
    assert recovered['inventory']==first['inventory']
    assert 2<=recovered['inventory']['events']<=27 and len(s.get(pid)['events'])==27


def test_recorded_results_and_rd_decisions_survive_without_identity_upgrade(tmp_path):
    s=Programs(tmp_path/'source');pid=s.create(PROFILE,'legacy self-declared author');ref=add(s,pid,review='reviewed')
    experiment(s,pid,[ref]);plan=s.get(pid)['experiments'][0]
    s.add_result(pid,dict(experiment_id=plan['event_id'],metric='synthetic endpoint',unit='fixture',
                 independent_unit='synthetic donor',n=10,population='synthetic engineering only',method='reported fixture interval',
                 source='https://example.invalid/fixture',available_at='2020-01-01T00:00:00Z',limitations='not clinical evidence',estimate=1,ci_low=0,ci_high=2),
                 'legacy fixture author',s.get(pid)['revision'])
    result=s.get(pid)['results'][0]['event_id']
    s.decide(pid,dict(action='hold',evidence_ids=[ref],result_ids=[result],reason='Hold for actual materials and independent validation',remaining_risk='unknown real clinical relevance'),'legacy fixture author',s.get(pid)['revision'])
    original=s.export(pid);manifest=snapshot(s.state,tmp_path/'backup',RELEASE);restore(tmp_path/'backup',tmp_path/'restore')
    assert Programs(tmp_path/'restore').export(pid)==original
    assert manifest['inventory']['programs'][0]['references_checked']>=4
    assert manifest['human_identity']=='historical_declarations_preserved_not_authenticated'


def test_queued_execution_and_inflight_requests_are_not_replayed(ready,tmp_path,monkeypatch):
    import vbt.research_queue as queue
    s,pid,rid,bid,_=create(ready)
    before=s.export(pid);snapshot(s.state,tmp_path/'backup',RELEASE);restore(tmp_path/'backup',tmp_path/'restore')
    other=Programs(tmp_path/'restore')
    monkeypatch.setattr(queue,'_worker',lambda *a,**k:pytest.fail('restored worker must not dispatch'))
    with pytest.raises(ValueError,match='只读'):queue.run_batch(other,pid,bid,'fixture operator',other.get(pid)['revision'])
    assert other.export(pid)==before
    s._append(pid,'ai_model_request',dict(schema_version=1,packet_hash='synthetic-packet',scope='synthetic interrupted request'),'fixture',s.get(pid)['revision'])
    unknown=s.get(pid)['events'][-1]['id'];snap=snapshot(s.state,tmp_path/'backup2',RELEASE)
    receipt=restore(tmp_path/'backup2',tmp_path/'restore2')
    assert receipt['inventory']['programs'][0]['unresolved_requests']==[dict(request_id=unknown,kind='ai_model_request',status='unknown_no_receipt')]
    assert snap['inventory']==receipt['inventory']


def test_bound_sources_missing_or_changed_do_not_forge_record_failure_or_success(linked,tmp_path):
    s,pid,_,source=linked
    manifest=snapshot(s.state,tmp_path/'backup',RELEASE);restore(tmp_path/'backup',tmp_path/'restore')
    original=Programs(tmp_path/'restore').export(pid)
    assert source_status(tmp_path/'restore')['complete']
    source.write_bytes(b'synthetic changed source')
    status=source_status(tmp_path/'restore');assert not status['complete'] and status['files'][0]['status']=='changed'
    source.unlink()
    status=source_status(tmp_path/'restore');assert not status['complete'] and status['files'][0]['status']=='unavailable'
    assert Programs(tmp_path/'restore').export(pid)==original
    assert state_inventory(tmp_path/'restore')==manifest['inventory']


@pytest.mark.parametrize('damage',['database','manifest','missing_manifest','extra_file','symlink_database','wrong_version','wrong_path','inventory','duplicate_key'])
def test_corrupted_or_incomplete_snapshot_rejected_before_destination_created(tmp_path,damage):
    s=Programs(tmp_path/'state');s.create(PROFILE,'fixture');pack=tmp_path/'backup';snapshot(s.state,pack,RELEASE)
    path=pack/'manifest.json';m=json.loads(path.read_text())
    if damage=='database':
        raw=(pack/'programs.sqlite3').read_bytes();(pack/'programs.sqlite3').write_bytes(raw+b'corrupt')
    elif damage=='manifest':path.write_text('{broken')
    elif damage=='missing_manifest':path.unlink()
    elif damage=='extra_file':(pack/'credentials.json').write_text('synthetic extra rejected')
    elif damage=='symlink_database':
        saved=tmp_path/'saved.db';(pack/'programs.sqlite3').rename(saved);(pack/'programs.sqlite3').symlink_to(saved)
    elif damage=='wrong_version':m['schema_version']=999;path.write_text(json.dumps(m))
    elif damage=='wrong_path':m['database']['name']='../../credentials';path.write_text(json.dumps(m))
    elif damage=='inventory':m['inventory']['events']=999;path.write_text(json.dumps(m))
    else:path.write_text('{"schema_version":1,"schema_version":1}')
    with pytest.raises((ValueError,OSError,sqlite3.DatabaseError)):restore(pack,tmp_path/'restore')
    assert not (tmp_path/'restore').exists()
    assert s.get(s.list()[0]['id'])['events'][0]['author']=='fixture'


def test_chain_and_reference_errors_rejected_even_if_database_hash_matches(tmp_path):
    s=Programs(tmp_path/'source');pid=s.create(PROFILE,'fixture')
    s._append(pid,'evidence_review',dict(evidence_id='missing-other-project',review='reviewed',reason='Synthetic broken reference fixture'),'fixture',s.get(pid)['revision'])
    with pytest.raises(ValueError,match='悬空'):snapshot(s.state,tmp_path/'bad-reference',RELEASE)
    assert not (tmp_path/'bad-reference'/'manifest.json').exists()
    s=Programs(tmp_path/'source2');pid=s.create(PROFILE,'fixture')
    with s.db() as db:
        db.execute('DROP TRIGGER program_no_update');db.execute("UPDATE program_events SET author='tampered'")
        db.execute("CREATE TRIGGER program_no_update BEFORE UPDATE ON program_events BEGIN SELECT RAISE(ABORT,'append only'); END")
    with pytest.raises(ValueError,match='哈希链'):snapshot(s.state,tmp_path/'bad-chain',RELEASE)


def test_unsupported_schema_and_invalid_release_do_not_mutate_source(tmp_path):
    s=Programs(tmp_path/'source');s.create(PROFILE,'fixture');before=s.list()[0]['revision']
    with pytest.raises(ValueError):snapshot(s.state,tmp_path/'invalid-release','not-fixed')
    with s.db() as db:db.execute('CREATE TABLE future_migration(id INTEGER)')
    with pytest.raises(ValueError,match='版本|结构'):snapshot(s.state,tmp_path/'unsupported',RELEASE)
    assert s.list()[0]['revision']==before


def test_existing_destinations_are_never_overwritten_and_partial_restore_stays_read_only(tmp_path,monkeypatch):
    import vbt.program_backup as backup
    s=Programs(tmp_path/'source');pid=s.create(PROFILE,'fixture');pack=tmp_path/'backup';snapshot(s.state,pack,RELEASE)
    with pytest.raises(ValueError):snapshot(s.state,pack,RELEASE)
    target=tmp_path/'existing';target.mkdir();(target/'user.txt').write_text('keep')
    with pytest.raises(ValueError):restore(pack,target)
    assert (target/'user.txt').read_text()=='keep'
    original=backup.write_new
    def interrupted(path,raw):
        if Path(path).name=='programs.sqlite3':raise OSError('synthetic copy interruption')
        return original(path,raw)
    monkeypatch.setattr(backup,'write_new',interrupted)
    with pytest.raises(OSError):restore(pack,tmp_path/'partial')
    assert (tmp_path/'partial'/MARKER).exists() and not (tmp_path/'partial'/'program_restore_receipt.json').exists()
    with pytest.raises(ValueError,match='只读'):Programs(tmp_path/'partial')
    assert s.get(pid)['revision']==s.list()[0]['revision']


def test_http_restored_view_exports_exact_history_and_rejects_all_writes(tmp_path):
    s=Programs(tmp_path/'source');pid=s.create(PROFILE,'legacy name');ref=add(s,pid)
    snapshot(s.state,tmp_path/'backup',RELEASE);restore(tmp_path/'backup',tmp_path/'restore')
    app=Workbench(tmp_path/'workspace',tmp_path/'restore');body=page(app,'/programs',{'id':pid})
    assert '恢复核对副本：只读' in body and '<fieldset disabled>' in body
    server=ThreadingHTTPServer(('127.0.0.1',0),handler(app));threading.Thread(target=server.serve_forever,daemon=True).start()
    base=f'http://127.0.0.1:{server.server_port}'
    try:
        assert urllib.request.urlopen(base+'/program/export?id='+pid).read()==s.export(pid)
        for path in ('/program/action','/note','/verify','/market/collect'):
            data=urlencode(dict(operation='review',id=pid,evidence_id=ref,review='reviewed',reason='Synthetic forbidden write despite valid token',revision=s.get(pid)['revision'],token=app.token,author='forged')).encode()
            with pytest.raises(urllib.error.HTTPError) as err:
                urllib.request.urlopen(urllib.request.Request(base+path,data=data,headers={'Origin':base}))
            assert err.value.code==403
        assert Programs(tmp_path/'restore').export(pid)==s.export(pid)
    finally:server.shutdown();server.server_close()


def test_cli_backup_verify_restore_and_missing_sources_exit_nonzero(tmp_path):
    s=Programs(tmp_path/'source');s.create(PROFILE,'fixture')
    script=Path(__file__).parents[1]/'scripts/program_backup.py'
    def cli(*args):return subprocess.run([sys.executable,str(script),*map(str,args)],capture_output=True,text=True)
    assert cli('create','--state-dir',s.state,'--output',tmp_path/'backup','--release',RELEASE).returncode==0
    assert cli('verify',tmp_path/'backup').returncode==0
    assert cli('restore',tmp_path/'backup','--state-dir',tmp_path/'restore').returncode==0
    result=cli('sources','--state-dir',tmp_path/'restore')
    assert result.returncode==2 and json.loads(result.stdout)['complete'] is False


def test_model_generation_cannot_send_before_read_only_ledger_write(ready,tmp_path,monkeypatch):
    import vbt.research_model as model
    from vbt.ai_research import packet
    s,pid,_,_=ready
    snapshot(s.state,tmp_path/'backup',RELEASE);restore(tmp_path/'backup',tmp_path/'restore')
    other=Programs(tmp_path/'restore');before=other.export(pid)
    monkeypatch.setenv('VBT_RESEARCH_MODEL_URL','https://models.example.invalid/v1/chat/completions')
    monkeypatch.setenv('VBT_RESEARCH_MODEL','synthetic-model')
    monkeypatch.setenv('VBT_RESEARCH_MODEL_API_KEY','SYNTHETIC_MODEL_SECRET_NOT_IN_RESTORE')
    monkeypatch.setattr(model,'call_once',lambda *a,**k:pytest.fail('must never send from restore'))
    config,public=model.configuration()
    with pytest.raises(ValueError,match='只读'):
        model.generate(other,pid,'fixture operator',other.get(pid)['revision'],packet(other,pid)['packet_hash'],public['config_hash'],True)
    assert other.export(pid)==before


def test_recovery_receipt_distinguishes_history_from_missing_sources_and_partial_copy(linked,tmp_path):
    from vbt.program_backup import recovery_status
    s,pid,_,source=linked;snapshot(s.state,tmp_path/'backup',RELEASE);restore(tmp_path/'backup',tmp_path/'restore')
    status=recovery_status(tmp_path/'restore',True)
    assert status['record_integrity']=='verified' and status['sources']['complete'] and status['mode']=='read_only_restore'
    source.unlink();status=recovery_status(tmp_path/'restore',True)
    assert status['record_integrity']=='verified' and not status['sources']['complete']
    (tmp_path/'restore'/'program_restore_receipt.json').unlink()
    assert recovery_status(tmp_path/'restore')['record_integrity']=='incomplete_no_receipt'
    with pytest.raises(ValueError):recovery_status(s.state)
