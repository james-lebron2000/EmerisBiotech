"""Successor deployment uses isolated files, SQLite and mocked services only."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import tarfile
from types import SimpleNamespace

import pytest

from emeris_live.arena_store import ArenaLedger

SPEC=importlib.util.spec_from_file_location('arena_upgrade_test',Path(__file__).parents[1]/'scripts/upgrade_arena.py')
upgrade=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(upgrade)


def append_note(ledger, text):
    with ledger.connect() as db:
        ledger._append(db,'isolated_fixture','fixture-'+text,{'text':text})


def branch_pair(tmp_path):
    live=ArenaLedger(tmp_path/'live');append_note(live,'original')
    incoming_dir=tmp_path/'incoming';incoming_dir.mkdir()
    upgrade.d.backup_sqlite(live.path,incoming_dir/'arena.sqlite3')
    incoming=ArenaLedger(incoming_dir);append_note(incoming,'transport-new')
    return live,incoming


def test_append_keeps_old_rows_exact_and_does_not_replace_database(tmp_path):
    live,incoming=branch_pair(tmp_path);before=upgrade.read_events(live.path);inode=live.path.stat().st_ino
    rows=upgrade.read_events(incoming.path)
    assert upgrade.append_events(live.path,rows,upgrade.head(before))==1
    assert upgrade.read_events(live.path)==rows and upgrade.read_events(live.path)[:len(before)]==before
    assert live.path.stat().st_ino==inode
    with sqlite3.connect(live.path) as db:
        with pytest.raises(sqlite3.IntegrityError):db.execute('DELETE FROM arena_events')
        with pytest.raises(sqlite3.IntegrityError):db.execute("UPDATE arena_events SET payload='{}'")


def test_divergent_and_concurrent_events_fail_without_dropping_either_branch(tmp_path):
    live,incoming=branch_pair(tmp_path);before=upgrade.read_events(live.path)
    append_note(live,'concurrent-live-only');actual=upgrade.read_events(live.path)
    with pytest.raises(ValueError,match='changed since'):
        upgrade.append_events(live.path,upgrade.read_events(incoming.path),upgrade.head(before))
    with pytest.raises(ValueError,match='exact transport prefix'):
        upgrade.append_events(live.path,upgrade.read_events(incoming.path),upgrade.head(actual))
    assert upgrade.read_events(live.path)==actual


def test_partial_append_transaction_rolls_back_on_later_invalid_row(tmp_path):
    live,incoming=branch_pair(tmp_path);append_note(incoming,'another')
    before=upgrade.read_events(live.path);rows=upgrade.read_events(incoming.path)
    rows[-1]['event_hash']='f'*64
    with pytest.raises(ValueError,match='hash mismatch'):
        upgrade.append_events(live.path,rows,upgrade.head(before))
    assert upgrade.read_events(live.path)==before


def test_missing_append_only_trigger_fails_closed(tmp_path):
    live,incoming=branch_pair(tmp_path);before=upgrade.read_events(live.path)
    with sqlite3.connect(live.path) as db:db.execute('DROP TRIGGER arena_no_delete')
    with pytest.raises(ValueError,match='trigger'):
        upgrade.append_events(live.path,upgrade.read_events(incoming.path),upgrade.head(before))


def acquisition(raw=b'<html>Official public fixture</html>', suffix='html'):
    name='arena_data/source_snapshots/public-fixture.'+suffix
    manifest={'schema_version':'arena-public-acquisition-v1','assets':[{'path':name,
        'sha256':upgrade.digest(raw),'bytes':len(raw),'source_url':'https://example.org/official',
        'retrieved_at':datetime.now(timezone.utc).isoformat(),'public_date':'2026-01-01','public_date_basis':'fixture'}]}
    return json.dumps(manifest).encode(),{name:raw}


def proof(identifier='isolated-proof'):
    base='arena_data/attestations/'+identifier+'/'
    cert=b'-----BEGIN CERTIFICATE-----\nQUJD\n-----END CERTIFICATE-----\n'
    values={'manifest.json':b'{}','manifest.tsq':b'query','manifest.tsr':b'response',
        'tsa.crt':cert,'cacert.pem':cert,'verification.txt':b'Verification: OK'}
    hashes={name:upgrade.digest(raw) for name,raw in values.items()}
    values['receipt.json']=json.dumps({'id':identifier,'files':hashes,
        'manifest_sha256':hashes['manifest.json'],'timestamp_response_sha256':hashes['manifest.tsr'],
        'authority_cert_sha256':hashes['tsa.crt'],'ca_cert_sha256':hashes['cacert.pem']}).encode()
    return {base+name:raw for name,raw in values.items()}


def archive(tmp_path,source,receipts,database,events, **metadata):
    commit='a'*40
    meta={'version':upgrade.VERSION,'git_commit':commit,'base_commit':upgrade.BASE_COMMIT,
        'files':{n:upgrade.digest(raw) for n,raw in source.items()},
        'receipt_files':{n:upgrade.digest(raw) for n,raw in receipts.items()},
        'attestation_ids':sorted({n.split('/')[2] for n in source if n.startswith('arena_data/attestations/')}),
        'arena_database_sha256':upgrade.digest(database),'arena_ledger_sha256':upgrade.head(events),
        'arena_event_count':len(events), **metadata}
    path=tmp_path/'upgrade.tar.gz'
    with tarfile.open(path,'w:gz') as tar:
        for name,raw in {**source,**receipts,'UPGRADE.json':json.dumps(meta).encode(),'arena-transport.sqlite3':database}.items():
            info=tarfile.TarInfo(name);info.size=len(raw);tar.addfile(info,io.BytesIO(raw))
    return path,upgrade.d.sha(path),commit


def make_workbench(path):
    with sqlite3.connect(path) as db:
        for table in upgrade.d.SEALED_TABLES:
            db.execute('CREATE TABLE '+table+' (id INTEGER PRIMARY KEY,value TEXT)')
            db.execute('INSERT INTO '+table+' VALUES (1,"original sealed value")')


@pytest.fixture
def server(tmp_path,monkeypatch):
    home=tmp_path/'service';base=home/'releases/base';base.mkdir(parents=True)
    data=tmp_path/'runtime';data.mkdir();current=home/'current';current.symlink_to(base)
    frozen=base/'predome_data/old.json';frozen.parent.mkdir();frozen.write_bytes(b'{"frozen":true}')
    manifest={'version':'1.2.0','git_commit':upgrade.BASE_COMMIT,'files':[{
        'path':'predome_data/old.json','sha256':upgrade.d.sha(frozen),'bytes':frozen.stat().st_size}]}
    (base/'RELEASE_MANIFEST.json').write_text(json.dumps(manifest))
    env=tmp_path/'fixture.env';env.write_text('ISOLATED=fixture\n');env.chmod(0o600)
    dropin=tmp_path/'arena.conf';dropin.write_bytes(upgrade.DROPIN_BYTES)
    for name,value in {'BASE':base,'TARGET':home/'releases/new','CURRENT':current,'DATA':data,
                       'DROPIN':dropin,'ENV_FILE':env,'HISTORICAL_ATTESTATIONS':frozenset({'isolated-proof'})}.items():monkeypatch.setattr(upgrade,name,value)
    original_stat=Path.stat
    def fake_stat(path,*a,**kw):
        result=original_stat(path,*a,**kw)
        return SimpleNamespace(st_mode=result.st_mode,st_uid=0) if path==env else result
    monkeypatch.setattr(Path,'stat',fake_stat)
    make_workbench(data/'workbench.sqlite3')
    ledger=ArenaLedger(data);append_note(ledger,'original')
    (data/'arena_receipts').mkdir()
    for n in range(3):
        filename=f'{n:032x}.json';name='arena_receipts/'+filename;ident='fixture-run-'+str(n)
        raw=json.dumps({'schema_version':'arena-result-receipt-v1','run_id':ident,'result':{'status':'failed','probability':None}}).encode()
        (data/name).write_bytes(raw)
        with ledger.connect() as db:ledger._append(db,'run_completed',ident,{'receipt_path':name,'receipt_sha256':upgrade.digest(raw)})
    original_events=upgrade.read_events(ledger.path)
    incoming_dir=tmp_path/'transport';incoming_dir.mkdir();upgrade.d.backup_sqlite(ledger.path,incoming_dir/'arena.sqlite3')
    incoming=ArenaLedger(incoming_dir);append_note(incoming,'new-sealed-cohort')
    events=upgrade.read_events(incoming.path);receipts=upgrade.referenced_receipts(events,data)
    manifest_raw,assets=acquisition()
    source={'scripts/upgrade_arena.py':Path(upgrade.__file__).read_bytes(),
        'scripts/deploy_arena.py':upgrade.HELPER.read_bytes(),'static/arena.html':b'<main>successor fixture</main>',
        upgrade.MANIFEST:manifest_raw,**assets,**proof()}
    args=archive(tmp_path,source,receipts,incoming.path.read_bytes(),events)
    commands=[]
    monkeypatch.setattr(upgrade.d,'systemctl',lambda *a:commands.append(a))
    monkeypatch.setattr(upgrade.subprocess,'run',lambda *a,**kw:subprocess.CompletedProcess(a[0],0))
    monkeypatch.setattr(upgrade,'verify_release',lambda *a:{'preserved_processes':'fixture-unrelated-pids','health':{'version':'fixture'}})
    def response(*a,**kw):
        return io.BytesIO(json.dumps({'ledger_sha256':upgrade.head(upgrade.read_events(ledger.path)),'runs':[]}).encode())
    monkeypatch.setattr(upgrade.urllib.request,'urlopen',response)
    return SimpleNamespace(args=args,ledger=ledger,original_events=original_events,incoming=events,source=source,
        receipts=receipts,base=base,current=current,data=data,home=home,dropin=dropin,commands=commands,
        manifest_bytes=(base/'RELEASE_MANIFEST.json').read_bytes(),env=env)


def test_success_upgrade_preserves_database_inode_rows_files_and_three_receipts(server):
    original_inode=server.ledger.path.stat().st_ino
    original_rows=upgrade.d.sealed_rows(server.data/'workbench.sqlite3')
    dropin=server.dropin.read_bytes()
    upgrade.remote(*server.args)
    assert server.current.resolve()==upgrade.TARGET
    assert server.commands==[('restart','emeris-live')]
    assert server.ledger.path.stat().st_ino==original_inode
    assert upgrade.read_events(server.ledger.path)==server.incoming
    upgrade.d.check_sealed_rows(original_rows,upgrade.d.sealed_rows(server.data/'workbench.sqlite3'))
    assert server.dropin.read_bytes()==dropin
    assert (server.base/'RELEASE_MANIFEST.json').read_bytes()==server.manifest_bytes
    assert (upgrade.TARGET/'predome_data/old.json').read_bytes()==b'{"frozen":true}'
    assert len(upgrade.receipt_inventory(server.data/'arena_receipts'))==3
    assert upgrade.read_events(server.home/'backups/openforecast02/arena.sqlite3')==server.original_events
    result=json.loads((server.home/'openforecast02-release.json').read_text())
    assert result['appended_events']==1 and result['old_arena_events_preserved'] and result['existing_receipts_preserved']==3


def test_failure_rollback_preserves_appended_and_concurrent_events(server,monkeypatch):
    def service(*args):
        server.commands.append(args)
        if server.current.resolve()==upgrade.TARGET:
            append_note(server.ledger,'concurrent-after-upgrade')
            raise subprocess.CalledProcessError(1,['systemctl','restart','emeris-live'])
    monkeypatch.setattr(upgrade.d,'systemctl',service)
    with pytest.raises(subprocess.CalledProcessError):upgrade.remote(*server.args)
    assert server.current.resolve()==server.base
    after=upgrade.read_events(server.ledger.path)
    assert after[:len(server.incoming)]==server.incoming and len(after)==len(server.incoming)+1
    assert server.dropin.read_bytes()==upgrade.DROPIN_BYTES
    assert server.commands==[('restart','emeris-live'),('restart','emeris-live')]
    result=json.loads((server.home/'openforecast02-failure.json').read_text())
    assert result['appended_events_preserved']==1 and result['live_database_never_replaced']


def test_concurrent_append_during_staging_rejected_before_switch(server,monkeypatch):
    def compile_only(*a,**kw):
        append_note(server.ledger,'concurrent-before-transaction')
        return subprocess.CompletedProcess(a[0],0)
    monkeypatch.setattr(upgrade.subprocess,'run',compile_only)
    with pytest.raises(ValueError,match='changed since'):upgrade.remote(*server.args)
    assert server.current.resolve()==server.base and server.commands==[]
    rows=upgrade.read_events(server.ledger.path)
    assert rows[:len(server.original_events)]==server.original_events
    assert rows[-1]['entity_id']=='fixture-concurrent-before-transaction'
    assert all(row['entity_id']!='fixture-new-sealed-cohort' for row in rows)


def test_old_sealed_row_mutation_during_staging_blocks_append_and_switch(server,monkeypatch):
    def changed(*a,**kw):
        with sqlite3.connect(server.data/'workbench.sqlite3') as db:
            db.execute('UPDATE platform_prediction_snapshots SET value="external mutation" WHERE id=1')
        return subprocess.CompletedProcess(a[0],0)
    monkeypatch.setattr(upgrade.subprocess,'run',changed)
    with pytest.raises(ValueError,match='sealed clinical rows changed'):upgrade.remote(*server.args)
    assert server.current.resolve()==server.base and server.commands==[]
    assert upgrade.read_events(server.ledger.path)==server.original_events


def test_changed_service_dropin_is_not_overwritten(server):
    server.dropin.write_bytes(b'operator configuration changed\n')
    with pytest.raises(ValueError,match='drop-in differs'):upgrade.remote(*server.args)
    assert server.dropin.read_bytes()==b'operator configuration changed\n'
    assert not upgrade.TARGET.exists() and not (server.home/'backups').exists()


def test_overlay_cannot_change_old_frozen_file(server,tmp_path):
    source={**server.source,'predome_data/old.json':b'changed'}
    args=archive(tmp_path,source,server.receipts,server.args[0].read_bytes(),server.incoming)
    # The path is denied at archive parsing, before any canonical mutation.
    with pytest.raises(ValueError,match='Unsafe archive member'):upgrade.remote(*args)
    assert upgrade.read_events(server.ledger.path)==server.original_events and not upgrade.TARGET.exists()


def test_manifest_only_assets_no_recursive_directory_inclusion():
    raw,files=acquisition();reads=[]
    def get(name):reads.append(name);return files[name]
    assert upgrade.manifest_assets(raw,get)==files and reads==list(files)
    changed=json.loads(raw);changed['assets'][0]['sha256']='0'*64
    with pytest.raises(ValueError,match='hash or size'):upgrade.manifest_assets(json.dumps(changed).encode(),get)
    unsafe=deepcopy(changed);unsafe['assets'][0]['path']='arena_data/source_snapshots/../../private.env'
    with pytest.raises(ValueError,match='Unsafe'):upgrade.manifest_assets(json.dumps(unsafe).encode(),get)


def test_manifest_raw_secret_is_rejected_even_with_matching_hash():
    raw,files=acquisition(b'private fixture sk-'+b'x'*30)
    with pytest.raises(ValueError,match='Credential-like'):upgrade.manifest_assets(raw,lambda n:files[n])


def test_existing_receipt_mutation_is_detected(tmp_path):
    folder=tmp_path/'arena_receipts';folder.mkdir();path=folder/('0'*32+'.json');path.write_bytes(b'old')
    before=upgrade.receipt_inventory(folder);path.write_bytes(b'new')
    with pytest.raises(ValueError,match='receipt changed'):upgrade.preserve_receipts(before,folder)


def test_missing_historical_tsa_bundle_blocks_before_any_server_mutation(server,monkeypatch):
    monkeypatch.setattr(upgrade,'HISTORICAL_ATTESTATIONS',frozenset({'isolated-proof','required-history-proof'}))
    with pytest.raises(ValueError,match='Missing historical TSA'):upgrade.remote(*server.args)
    assert not upgrade.TARGET.exists() and upgrade.read_events(server.ledger.path)==server.original_events


def test_current_manifest_changed_during_stage_blocks_append(server,monkeypatch):
    def changed(*a,**kw):
        value=json.loads((server.base/'RELEASE_MANIFEST.json').read_text());value['external_change']=True
        (server.base/'RELEASE_MANIFEST.json').write_text(json.dumps(value))
        return subprocess.CompletedProcess(a[0],0)
    monkeypatch.setattr(upgrade.subprocess,'run',changed)
    with pytest.raises(ValueError,match='Base changed before switch'):upgrade.remote(*server.args)
    assert server.commands==[] and server.current.resolve()==server.base
    assert upgrade.read_events(server.ledger.path)==server.original_events


def test_existing_frozen_arena_packet_cannot_be_overlaid(server,tmp_path):
    name='arena_data/old-frozen-packet.json';path=server.base/name;path.parent.mkdir();path.write_bytes(b'{"sealed":true}')
    manifest=json.loads((server.base/'RELEASE_MANIFEST.json').read_text())
    manifest['files'].append({'path':name,'sha256':upgrade.d.sha(path),'bytes':path.stat().st_size})
    (server.base/'RELEASE_MANIFEST.json').write_text(json.dumps(manifest))
    transport=tmp_path/'snapshot.sqlite3';upgrade.d.backup_sqlite(server.ledger.path,transport)
    # Preserve the actual transport's new event by copying the known rows into a
    # second isolated ledger, never using an archive as a database.
    upgrade.append_events(transport,server.incoming,upgrade.head(server.original_events))
    args=archive(tmp_path,{**server.source,name:b'{"sealed":false}'},server.receipts,transport.read_bytes(),server.incoming)
    with pytest.raises(ValueError,match='frozen evidence'):upgrade.remote(*args)
    assert path.read_bytes()==b'{"sealed":true}' and server.commands==[]
    assert upgrade.read_events(server.ledger.path)==server.original_events


def test_uploaded_operator_must_match_committed_archive(server,tmp_path):
    database=tmp_path/'transport.sqlite3';upgrade.d.backup_sqlite(server.ledger.path,database)
    upgrade.append_events(database,server.incoming,upgrade.head(server.original_events))
    args=archive(tmp_path,{**server.source,'scripts/upgrade_arena.py':b'print("unmatched")'},server.receipts,database.read_bytes(),server.incoming)
    with pytest.raises(ValueError,match='Uploaded operator/helper differ'):upgrade.remote(*args)
    assert not upgrade.TARGET.exists() and not (server.home/'backups').exists()


def test_manifest_unlisted_raw_asset_is_rejected(server,tmp_path):
    database=tmp_path/'transport.sqlite3';upgrade.d.backup_sqlite(server.ledger.path,database)
    upgrade.append_events(database,server.incoming,upgrade.head(server.original_events))
    args=archive(tmp_path,{**server.source,'arena_data/source_snapshots/unlisted.json':b'{}'},server.receipts,database.read_bytes(),server.incoming)
    with pytest.raises(ValueError,match='Unlisted acquisition'):upgrade.remote(*args)
    assert not upgrade.TARGET.exists()


def transport_bytes(server, folder):
    folder.mkdir()
    path=folder/'arena.sqlite3'
    upgrade.d.backup_sqlite(server.ledger.path,path)
    upgrade.append_events(path,server.incoming,upgrade.head(server.original_events))
    return path


def test_manifest_bound_xml_survives_remote_unpack_and_deployment(server,tmp_path):
    raw=b'<?xml version="1.0"?><clinical-study>Isolated public source</clinical-study>'
    manifest,assets=acquisition(raw,suffix='xml')
    source={k:v for k,v in server.source.items() if not k.startswith('arena_data/source_snapshots/')}
    source.update({upgrade.MANIFEST:manifest,**assets})
    assert not upgrade.d.allowed(next(iter(assets)))  # The original helper remains strict.
    database=transport_bytes(server,tmp_path/'xml-transport')
    args=archive(tmp_path,source,server.receipts,database.read_bytes(),server.incoming)
    upgrade.remote(*args)
    name=next(iter(assets))
    assert (upgrade.TARGET/name).read_bytes()==raw
    release=json.loads((upgrade.TARGET/'RELEASE_MANIFEST.json').read_text())
    entry=next(v for v in release['files'] if v['path']==name)
    assert entry['sha256']==upgrade.digest(raw) and entry['bytes']==len(raw)


@pytest.mark.parametrize('name,message',[
    ('arena_data/source_snapshots/unlisted.xml','Unlisted acquisition'),
    ('arena_data/arbitrary.xml','Unsafe archive member'),
    ('static/arbitrary.xml','Unsafe archive member'),
])
def test_arbitrary_xml_fails_before_server_mutation(server,tmp_path,name,message):
    database=transport_bytes(server,tmp_path/'xml-denied')
    args=archive(tmp_path,{**server.source,name:b'<unlisted/>'},server.receipts,database.read_bytes(),server.incoming)
    with pytest.raises(ValueError,match=message):upgrade.remote(*args)
    assert not upgrade.TARGET.exists()
    assert upgrade.read_events(server.ledger.path)==server.original_events


def test_dynamic_tsa_set_accepts_eight_complete_pinned_bundles(server,tmp_path):
    source=dict(server.source)
    for index in range(7):source.update(proof('additional-proof-'+str(index)))
    database=transport_bytes(server,tmp_path/'eight-proofs')
    args=archive(tmp_path,source,server.receipts,database.read_bytes(),server.incoming)
    upgrade.remote(*args)
    receipt=json.loads((server.home/'openforecast02-release.json').read_text())
    assert receipt['tsa_bundles']==8
    assert receipt['attestation_ids']==sorted(['isolated-proof']+['additional-proof-'+str(i) for i in range(7)])
    for name,raw in source.items():
        if name.startswith('arena_data/attestations/'):
            assert (upgrade.TARGET/name).read_bytes()==raw


@pytest.mark.parametrize('ids',[[],['different-proof'],['isolated-proof','isolated-proof'],None])
def test_tsa_manifest_id_set_must_match_actual_bundles(server,tmp_path,ids):
    database=transport_bytes(server,tmp_path/'pinned-proofs')
    args=archive(tmp_path,server.source,server.receipts,database.read_bytes(),server.incoming,attestation_ids=ids)
    with pytest.raises(ValueError,match='TSA bundle set'):upgrade.remote(*args)
    assert not upgrade.TARGET.exists()


def test_incomplete_extra_tsa_bundle_cannot_be_silently_skipped(server,tmp_path):
    source={**server.source,**proof('additional-proof')}
    del source['arena_data/attestations/additional-proof/verification.txt']
    database=transport_bytes(server,tmp_path/'incomplete-proof')
    args=archive(tmp_path,source,server.receipts,database.read_bytes(),server.incoming)
    with pytest.raises(ValueError,match='Incomplete attestation'):upgrade.remote(*args)
    assert not upgrade.TARGET.exists()


def test_uncommitted_release_file_is_rejected(tmp_path,monkeypatch):
    monkeypatch.setattr(upgrade,'ROOT',tmp_path)
    source=tmp_path/'static/arena.js';source.parent.mkdir();source.write_bytes(b'uncommitted source edit')
    monkeypatch.setattr(upgrade.subprocess,'check_output',lambda *a,**kw:b'committed source bytes')
    with pytest.raises(ValueError,match='Uncommitted release source'):
        upgrade.git_bytes('a'*40,'static/arena.js')


@pytest.mark.parametrize('case',['missing','changed'])
def test_ledger_proof_missing_or_different_blocks_before_backup(server,tmp_path,case):
    source=dict(server.source)
    extra=proof('new-ledger-proof')
    payload=json.loads(extra['arena_data/attestations/new-ledger-proof/receipt.json'])
    if case=='changed':
        source.update(extra)
        payload['manifest_sha256']='f'*64
    database=transport_bytes(server,tmp_path/'ledger-proof')
    ledger=ArenaLedger(database.parent)
    with ledger.connect() as db:ledger._append(db,'timestamp_attested','attestation-fixture',payload)
    events=upgrade.read_events(database)
    args=archive(tmp_path,source,server.receipts,database.read_bytes(),events)
    with pytest.raises(ValueError,match='Ledger timestamp receipt'):upgrade.remote(*args)
    assert not upgrade.TARGET.exists() and not (server.home/'backups').exists()
    assert upgrade.read_events(server.ledger.path)==server.original_events


def test_identical_ledger_proof_bundle_is_included(server,tmp_path):
    extra=proof('new-ledger-proof')
    payload=json.loads(extra['arena_data/attestations/new-ledger-proof/receipt.json'])
    database=transport_bytes(server,tmp_path/'valid-ledger-proof')
    ledger=ArenaLedger(database.parent)
    with ledger.connect() as db:ledger._append(db,'timestamp_attested','attestation-fixture',payload)
    events=upgrade.read_events(database)
    args=archive(tmp_path,{**server.source,**extra},server.receipts,database.read_bytes(),events)
    upgrade.remote(*args)
    assert upgrade.read_events(server.ledger.path)==events
