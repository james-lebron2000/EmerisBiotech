"""Publication preserves the verified app and restores only its own Nginx bytes."""
import io
from pathlib import Path
import subprocess
import sys
import tarfile

import pytest
from test_public_campaign_export import records
from test_public_snapshot_proof import actual_proof

sys.path.insert(0,str(Path(__file__).parents[1]/'scripts'))
import configure_arena_frontends as frontends
import publish_public_proof as publish


def transport(files):
    meta={'schema_version':'public-proof-publication-1','source_commit':'a'*40,
        'files':{'assets/'+n:publish.digest(v) for n,v in files.items()}}
    out=io.BytesIO()
    with tarfile.open(fileobj=out,mode='w:gz') as tar:
        for name,value in {**{'assets/'+n:v for n,v in files.items()},'TRANSPORT.json':publish.proof.campaign.canonical(meta)}.items():
            item=tarfile.TarInfo(name);item.size=len(value);tar.addfile(item,io.BytesIO(value))
    return out.getvalue()


@pytest.fixture
def staged(tmp_path,monkeypatch):
    old=frontends.render();assert publish.digest(old)==publish.EXPECTED_SITE
    site=tmp_path/'site';site.write_bytes(old)
    files={name:('isolated fixture '+name).encode() for name in publish.proof.PUBLIC_FILES}
    raw=transport(files);archive=tmp_path/'transport.tar.gz';archive.write_bytes(raw)
    monkeypatch.setattr(publish,'HISTORICAL_BUNDLE_SHA256',publish.digest(files['clinical-records.zip']))
    for name,path in {'SITE':site,'BACKUP':tmp_path/'backup','DEST':tmp_path/'public'/'fixed-version',
        'RECEIPT':tmp_path/'receipt','LOCK':tmp_path/'lock','TRANSPORT':archive}.items():monkeypatch.setattr(publish,name,path)
    verifies=[]
    def verify(path,**kwargs):
        verifies.append(path)
        assert kwargs['require_outer'] is True
        assert {p.name:p.read_bytes() for p in path.iterdir()}==files
        return {'time_scope':'public_export_existence_only','clinical_prediction_validated':False}
    monkeypatch.setattr(publish.proof,'verify',verify)
    monkeypatch.setattr(publish.urllib.request,'urlopen',lambda *a,**kw:io.BytesIO(b'{"version":"1.2.1"}'))
    commands=[];monkeypatch.setattr(publish.subprocess,'run',lambda cmd,**kw:commands.append(cmd))
    return old,raw,files,verifies,commands


def test_success_changes_only_whitelisted_public_locations_and_reloads_nginx(staged):
    old,raw,files,verifies,commands=staged
    publish.remote(publish.digest(raw),publish.EXPECTED_SITE)
    new=publish.SITE.read_bytes()
    assert publish.BACKUP.read_bytes()==old
    assert len(verifies)==1 and commands==[['nginx','-t'],['systemctl','reload','nginx']]
    receipt=publish.proof.decode(publish.RECEIPT.read_bytes())
    assert receipt['status']=='published' and receipt['application_restarted'] is False
    assert receipt['ledger_writes']==receipt['provider_calls']==0
    assert {p.name:p.read_bytes() for p in publish.DEST.iterdir()}==files
    assert all(p.stat().st_mode&0o777==0o644 for p in publish.DEST.iterdir())
    assert new.split(b'    listen 18443 ssl http2;',1)[1]==old.split(b'    listen 18443 ssl http2;',1)[1]
    assert b'location ^~ /public-forecast/ { return 404; }' in new
    for name in files:
        assert ('location = '+publish.URL_PREFIX+name+' {').encode() in new
    assert new.count(b'types { }')==len(files)
    start=new.index(b'    location ^~ /public-forecast/')
    end=new.index(b'    location / {\n        limit_req zone=emeris_api',start)
    assert new[:start]+new[end:]==old


def test_changed_site_is_never_overwritten(staged):
    _,raw,_,verifies,commands=staged
    publish.SITE.write_bytes(b'concurrent operator config')
    with pytest.raises(ValueError,match='Frontend changed'):publish.remote(publish.digest(raw),publish.EXPECTED_SITE)
    assert publish.SITE.read_bytes()==b'concurrent operator config' and not publish.DEST.exists()
    assert verifies==commands==[] and not publish.BACKUP.exists()


def test_wrong_transport_or_existing_destination_fails_before_changes(staged):
    old,raw,_,verifies,commands=staged
    with pytest.raises(ValueError,match='Transport changed'):publish.remote('0'*64,publish.EXPECTED_SITE)
    publish.DEST.parent.mkdir();publish.DEST.mkdir()
    with pytest.raises(ValueError,match='already attempted'):publish.remote(publish.digest(raw),publish.EXPECTED_SITE)
    assert publish.SITE.read_bytes()==old and verifies==commands==[]


def test_invalid_nginx_restores_exact_site_keeps_unserved_data_and_no_success_receipt(staged,monkeypatch):
    old,raw,_,_,commands=staged
    def run(cmd,**kwargs):
        commands.append(cmd)
        if len(commands)==1:raise subprocess.CalledProcessError(1,cmd)
    monkeypatch.setattr(publish.subprocess,'run',run)
    with pytest.raises(subprocess.CalledProcessError):publish.remote(publish.digest(raw),publish.EXPECTED_SITE)
    assert publish.SITE.read_bytes()==old and publish.BACKUP.read_bytes()==old
    assert publish.DEST.is_dir() and not publish.RECEIPT.exists()
    assert commands==[['nginx','-t'],['nginx','-t'],['systemctl','reload','nginx']]


def test_concurrent_change_before_replacement_is_preserved_without_rollback(staged,monkeypatch):
    _,raw,_,_,commands=staged
    original=publish.proof.exclusive
    def write(path,data,mode=0o600):
        original(path,data,mode)
        if path==publish.BACKUP:publish.SITE.write_bytes(b'another operator won')
    monkeypatch.setattr(publish.proof,'exclusive',write)
    with pytest.raises(ValueError,match='Concurrent'):publish.remote(publish.digest(raw),publish.EXPECTED_SITE)
    assert publish.SITE.read_bytes()==b'another operator won' and commands==[]
    assert not publish.RECEIPT.exists()


def test_rollback_does_not_clobber_a_concurrent_new_config(staged,monkeypatch):
    _,raw,_,_,commands=staged
    def run(cmd,**kwargs):
        commands.append(cmd);publish.SITE.write_bytes(b'external newer config')
        raise subprocess.CalledProcessError(1,cmd)
    monkeypatch.setattr(publish.subprocess,'run',run)
    with pytest.raises(RuntimeError,match='Concurrent frontend'):publish.remote(publish.digest(raw),publish.EXPECTED_SITE)
    assert publish.SITE.read_bytes()==b'external newer config'
    assert commands==[['nginx','-t']] and not publish.RECEIPT.exists()


def test_wrong_application_version_does_not_touch_site_or_public_data(staged,monkeypatch):
    old,raw,_,_,commands=staged
    monkeypatch.setattr(publish.urllib.request,'urlopen',lambda *a,**kw:io.BytesIO(b'{"version":"unexpected"}'))
    with pytest.raises(ValueError,match='application version'):publish.remote(publish.digest(raw),publish.EXPECTED_SITE)
    assert publish.SITE.read_bytes()==old and not publish.DEST.exists() and commands==[]


def test_transport_extra_path_and_nonregular_member_are_rejected():
    files={name:b'fixture' for name in publish.proof.PUBLIC_FILES};raw=transport(files)
    assert len(publish.load_transport(raw)[0])==9
    out=io.BytesIO()
    with tarfile.open(fileobj=out,mode='w:gz') as tar:
        item=tarfile.TarInfo('../private');item.size=1;tar.addfile(item,io.BytesIO(b'x'))
    with pytest.raises(ValueError,match='member'):publish.load_transport(out.getvalue())


def current_calendar_bundle(records):
    from copy import deepcopy
    from test_public_campaign_export import synthetic_current_readout
    index,data,_,fetch=records
    data['fixture-0']['target']['current_readout']=synthetic_current_readout()
    return publish.proof.campaign.bundle(deepcopy(index),fetch)[0]


def test_local_historical_publisher_rejects_new_calendar_bundle_before_verification_or_network(records,tmp_path,monkeypatch):
    directory=tmp_path/'new-campaign';directory.mkdir()
    (directory/'clinical-records.zip').write_bytes(current_calendar_bundle(records))
    monkeypatch.setattr(publish.proof,'verify',lambda *a,**kw:pytest.fail('Reject new format before proof verification/upload'))
    monkeypatch.setattr(publish.subprocess,'run',lambda *a,**kw:pytest.fail('No SSH, scp or remote command'))
    monkeypatch.setattr(publish.subprocess,'check_output',lambda *a,**kw:pytest.fail('No Git or remote command'))
    receipt=tmp_path/'never-written.json'
    with pytest.raises(ValueError,match='only the original 20261023 bundle'):
        publish.local(tmp_path/'unused-identity',directory,receipt)
    assert not receipt.exists()


def test_remote_historical_publisher_rejects_new_calendar_bundle_before_site_or_publication_writes(staged,records,monkeypatch):
    old,_,files,verifies,commands=staged
    files['clinical-records.zip']=current_calendar_bundle(records)
    raw=transport(files);publish.TRANSPORT.write_bytes(raw)
    monkeypatch.setattr(publish.proof,'verify',lambda *a,**kw:pytest.fail('Reject before source-contract loading'))
    with pytest.raises(ValueError,match='only the original 20261023 bundle'):
        publish.remote(publish.digest(raw),publish.EXPECTED_SITE)
    assert publish.SITE.read_bytes()==old and not publish.DEST.exists()
    assert not publish.BACKUP.exists() and not publish.RECEIPT.exists() and commands==verifies==[]


def test_historical_publisher_pin_matches_unchanged_original_signed_bundle(actual_proof):
    raw=(actual_proof/'clinical-records.zip').read_bytes()
    publish.require_historical_bundle(raw)
    assert publish.proof.decode((actual_proof/'receipt.json').read_bytes())['bundle_sha256']==publish.HISTORICAL_BUNDLE_SHA256
