"""Offline public proof tamper tests; no providers, private ledger or TSA calls."""
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
import zipfile

import pytest
from test_public_campaign_export import records, real_public_record
from test_arena_imports import signed

SCRIPTS=Path(__file__).parents[1]/'scripts'
sys.path.insert(0,str(SCRIPTS))
import public_snapshot_proof as proof


def packed(files):
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_DEFLATED) as z:
        for name,raw in files.items():z.writestr(name,raw)
    return output.getvalue()


def test_public_bundle_can_be_independently_rebuilt_after_json_roundtrip(records):
    index,_,_,fetch=records
    raw,m=proof.campaign.bundle(index,fetch)
    assert proof.validate_bundle(raw)==m


@pytest.mark.parametrize('state,label',[('pending','success'),('agreement','success'),
    ('accepted','success'),('accepted','mixed')])
def test_new_schema_proof_validation_uses_actual_ledger_projection(tmp_path,monkeypatch,state,label):
    ledger,before,index,_,client=real_public_record(tmp_path,monkeypatch,state,label)
    raw,m=proof.campaign.bundle(index,lambda path:client.get(path).json())
    assert m['schema_version']=='predome-public-launch-bundle-2'
    assert proof.validate_bundle(raw)==m
    assert ledger.events()==before


@pytest.mark.parametrize('version',['predome-public-launch-bundle-1','unknown'])
def test_new_bundle_cannot_be_downgraded_to_legacy_or_unknown_version(records,version):
    index,_,_,fetch=records
    raw,_=proof.campaign.bundle(index,fetch);files=proof.zip_members(raw)
    m=proof.decode(files['manifest.json']);m['schema_version']=version
    if version=='predome-public-launch-bundle-1':
        # Correct legacy README does not authorize 1.1 records in a 1.0 export.
        files['README.md']=files['README.md'].replace(
            b'Source agreement alone is not scientific acceptance. Pending independent review and nonbinary outcomes cannot earn formal binary scores.\n',b'')
        assert proof.digest(files['README.md'])==proof.README_PINS[version]
        m['files']['README.md']={'sha256':proof.digest(files['README.md']),'bytes':len(files['README.md'])}
    files['manifest.json']=proof.campaign.canonical(m)
    with pytest.raises(ValueError):proof.validate_bundle(packed(files))


@pytest.mark.parametrize('fault',['extra','hash','report','private','denominator','index','models','readme','false_attestation'])
def test_bundle_boundary_rejects_tampering_even_if_member_hashes_are_recomputed(records,fault):
    index,_,_,fetch=records
    raw,_=proof.campaign.bundle(index,fetch);files=proof.zip_members(raw)
    m=proof.decode(files['manifest.json'])
    if fault=='extra':files['../private.txt']=b'PRIVATE'
    elif fault=='hash':files['reports/fixture-0.md']+=b' changed'
    elif fault=='report':files['reports/fixture-0.md']+=b' clinical accuracy is proven'
    elif fault=='private':
        r=proof.decode(files['records/fixture-0.json']);r['runs'][0]['token_usage']={'secret':1}
        files['records/fixture-0.json']=proof.campaign.canonical(r)
    elif fault=='denominator':m['formal_assigned_records']=999
    elif fault=='index':m['records'][0]['page_url']='https://unrelated.example/'
    elif fault=='models':m['models']=['invented-model']
    elif fault=='readme':files['README.md']=b'PRIVATE operator note'
    elif fault=='false_attestation':m['independent_timestamp_attestation']=True
    if fault not in ('hash','extra'):
        m['files']={n:{'sha256':proof.digest(v),'bytes':len(v)} for n,v in files.items() if n!='manifest.json'}
    files['manifest.json']=proof.campaign.canonical(m)
    with pytest.raises(ValueError):proof.validate_bundle(packed(files))


def test_zip_duplicates_and_links_are_rejected():
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w') as z:
        z.writestr('same',b'a')
        with pytest.warns(UserWarning,match='Duplicate name'):z.writestr('same',b'b')
    with pytest.raises(ValueError,match='Duplicate'):proof.zip_members(out.getvalue())
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w') as z:
        item=zipfile.ZipInfo('link');item.external_attr=0o120777<<16;z.writestr(item,b'/etc/passwd')
    with pytest.raises(ValueError,match='links'):proof.zip_members(out.getvalue())


@pytest.fixture
def actual_proof(tmp_path):
    source=Path(__file__).parents[1]/'arena_data/public_proofs/clinical-20261023-v1'
    target=tmp_path/'proof';shutil.copytree(source,target)
    return target


def test_actual_freetsa_signature_data_and_query_verify_without_network(actual_proof,monkeypatch):
    monkeypatch.setattr(proof.urllib.request,'urlopen',lambda *a,**kw:pytest.fail('Offline verification must not call the authority'))
    before={p.name:p.read_bytes() for p in actual_proof.iterdir()}
    fields=proof.verify(actual_proof,require_outer=True)
    assert {p.name:p.read_bytes() for p in actual_proof.iterdir()}==before
    assert fields['formal_trials']==12 and fields['historical_replays']==8
    assert fields['time_scope']=='public_export_existence_only'
    assert fields['original_prediction_time_proven'] is False
    assert fields['clinical_prediction_validated'] is False
    assert fields['provider_calls']==fields['ledger_writes']==0


@pytest.mark.parametrize('fault',['signature','query','nonce','zip','certificate','receipt','readme','outer','extra'])
def test_actual_public_proof_detects_byte_changes_false_claims_and_wrong_nonce(actual_proof,fault):
    if fault in ('signature','query','nonce','zip','certificate','outer'):
        name={'signature':'timestamp.tsr','query':'request.tsq','nonce':'request.tsq','zip':'clinical-records.zip','certificate':'cacert.pem','outer':'verification-kit.zip'}[fault]
        p=actual_proof/name;raw=bytearray(p.read_bytes())
        # In this recorded70-byte query the final three bytes are certReq;
        # the preceding byte is the nonce, outside the SHA-256 imprint.
        raw[-4 if fault=='nonce' else len(raw)//2]^=1;p.write_bytes(raw)
    elif fault=='receipt':
        p=actual_proof/'receipt.json';x=proof.decode(p.read_bytes());x['original_prediction_time_proven']=True;p.write_bytes(proof.campaign.canonical(x))
    elif fault=='readme':(actual_proof/'README.md').write_text('Clinical accuracy is proven')
    elif fault=='extra':(actual_proof/'private.env').write_text('PRIVATE')
    with pytest.raises((ValueError,subprocess.CalledProcessError,zipfile.BadZipFile,UnicodeError,json.JSONDecodeError)):
        proof.verify(actual_proof,require_outer=True)


def test_openssl_child_receives_no_model_credentials(monkeypatch):
    monkeypatch.setenv('API_KEY','private');monkeypatch.setenv('OTHER_PASSWORD','private')
    def check(args,**kwargs):
        assert set(kwargs['env'])<= {'PATH','SYSTEMROOT','TMPDIR','LANG','LC_ALL'}
        assert 'private' not in kwargs['env'].values()
        return 'Verification: OK'
    monkeypatch.setattr(proof.subprocess,'check_output',check)
    assert proof.openssl_run(['ts','-help'])=='Verification: OK'


def test_existing_proof_directory_is_not_overwritten_or_reissued(actual_proof,monkeypatch):
    monkeypatch.setattr(proof.urllib.request,'urlopen',lambda *a,**kw:pytest.fail('No duplicate timestamp call'))
    before=(actual_proof/'receipt.json').read_bytes()
    with pytest.raises(ValueError,match='verification-only'):
        proof.attest(actual_proof/'clinical-records.zip',proof.digest((actual_proof/'clinical-records.zip').read_bytes()),actual_proof,actual_proof)
    assert (actual_proof/'receipt.json').read_bytes()==before


def test_legacy_signed_bundle_cannot_start_a_new_attestation(actual_proof,tmp_path,monkeypatch):
    source=actual_proof/'clinical-records.zip';before=source.read_bytes()
    assert proof.verify(actual_proof,require_outer=True)['clinical_prediction_validated'] is False
    output=tmp_path/'new-proof'
    monkeypatch.setattr(proof,'openssl_run',lambda *a,**kw:pytest.fail('No new timestamp query'))
    monkeypatch.setattr(proof.urllib.request,'urlopen',lambda *a,**kw:pytest.fail('No authority request'))
    with pytest.raises(ValueError,match='verification-only'):
        proof.attest(source,proof.digest(before),output,actual_proof)
    assert not output.exists() and source.read_bytes()==before


def test_current_bundle_still_rejects_existing_proof_directory_before_timestamp_io(records,tmp_path,monkeypatch):
    index,_,_,fetch=records
    raw,_=proof.campaign.bundle(index,fetch)
    source=tmp_path/'current.zip';source.write_bytes(raw)
    existing=tmp_path/'existing';existing.mkdir();(existing/'untouched').write_bytes(b'original')
    monkeypatch.setattr(proof,'openssl_run',lambda *a,**kw:pytest.fail('No new timestamp query'))
    monkeypatch.setattr(proof.urllib.request,'urlopen',lambda *a,**kw:pytest.fail('No authority request'))
    with pytest.raises(ValueError,match='never overwritten'):
        proof.attest(source,proof.digest(raw),existing,tmp_path/'missing-certs')
    assert (existing/'untouched').read_bytes()==b'original'


@pytest.fixture
def signed_public_fixture(tmp_path, records, signed, monkeypatch):
    """Actual synthetic export and local test-CA signature, without network.

    The same existing offline authority fixture is used by sealed record tests.
    Only test pins are replaced; callers cannot select a production trust anchor.
    """
    monkeypatch.setattr(proof,'PINS',signed['pins'])
    monkeypatch.setattr(proof.urllib.request,'urlopen',lambda *a,**kw:pytest.fail('Offline signatures must not call the authority'))
    index,_,_,fetch=records
    raw,manifest=proof.campaign.bundle(index,fetch)
    directory=tmp_path/'normal-proof';directory.mkdir()
    (directory/'clinical-records.zip').write_bytes(raw)
    for name in signed['pins']:
        (directory/name).write_bytes((signed['root']/name).read_bytes())
    config=tmp_path/'tsa.cnf'
    config.write_text((signed['root']/'ts.cnf').read_text().replace('digests=sha256\n','digests=sha256,sha384\n'))

    def timestamp(path,*,with_nonce=True,algorithm='sha256'):
        args=['ts','-query','-data',path/'clinical-records.zip','-'+algorithm,'-cert','-out',path/'request.tsq']
        if not with_nonce:args.append('-no_nonce')
        proof.openssl_run(args)
        # OpenSSL emits a second-precision genTime. Wait for a real later second
        # instead of backdating the export or fabricating its signature time.
        while datetime.now(timezone.utc).replace(microsecond=0)<=proof.instant(manifest['exported_at_utc']):
            time.sleep(.02)
        proof.openssl_run(['ts','-reply','-config',config,'-section','testing',
            '-queryfile',path/'request.tsq','-out',path/'timestamp.tsr'])
        reply=proof.openssl_run(['ts','-reply','-in',path/'timestamp.tsr','-text'])
        text=next(line.split(': ',1)[1] for line in reply.splitlines() if line.startswith('Time stamp: '))
        return datetime.strptime(text,'%b %d %H:%M:%S %Y GMT').replace(tzinfo=timezone.utc).isoformat()

    def write_unsigned(path,fields):
        (path/'receipt.json').write_bytes(proof.campaign.canonical(fields))
        (path/'verification.txt').write_bytes(proof.verification_text(fields))
        (path/'README.md').write_bytes(proof.readme(fields))

    timestamp(directory)
    fields=proof.verified_fields(directory)
    write_unsigned(directory,fields)

    def altered(name,*,with_nonce=True,algorithm='sha256',retain_response=False):
        case=tmp_path/name;shutil.copytree(directory,case)
        if retain_response:
            proof.openssl_run(['ts','-query','-data',case/'clinical-records.zip','-'+algorithm,
                '-cert','-no_nonce','-out',case/'request.tsq'])
        else:
            generated=timestamp(case,with_nonce=with_nonce,algorithm=algorithm)
            changed={**fields,'generated_at_utc':generated,
                'timestamp_response_sha256':proof.digest((case/'timestamp.tsr').read_bytes())}
            # Preserve the existing unsigned contract. The verification gate,
            # rather than an unrelated receipt mismatch, must reject this case.
            write_unsigned(case,changed)
        return case
    return directory,altered


def test_actual_test_ca_signature_with_explicit_matching_nonces_passes_without_rewriting(signed_public_fixture):
    directory,_=signed_public_fixture
    before={p.name:p.read_bytes() for p in directory.iterdir()}
    value=proof.verify(directory)
    assert value['verification_method']=='openssl_ts_verify_data_and_query'
    assert value['original_prediction_time_proven'] is False
    assert {p.name:p.read_bytes() for p in directory.iterdir()}==before


def test_nonce_free_query_and_signed_response_are_rejected_by_full_verifier(signed_public_fixture):
    _,altered=signed_public_fixture
    directory=altered('no-nonces',with_nonce=False)
    with pytest.raises(ValueError,match='explicit nonce'):
        proof.verify(directory)


def test_nonce_free_query_cannot_borrow_unchanged_response_with_nonce(signed_public_fixture):
    original,altered=signed_public_fixture
    directory=altered('removed-query-nonce',retain_response=True)
    assert (directory/'timestamp.tsr').read_bytes()==(original/'timestamp.tsr').read_bytes()
    with pytest.raises(ValueError,match='explicit nonce'):
        proof.verify(directory)


def test_non_sha256_message_imprint_is_rejected_after_actual_signature_checks(signed_public_fixture):
    _,altered=signed_public_fixture
    directory=altered('sha384-imprint',algorithm='sha384')
    with pytest.raises(ValueError,match='SHA-256'):
        proof.verify(directory)
