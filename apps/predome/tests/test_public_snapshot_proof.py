"""Offline public proof tamper tests; no providers, private ledger or TSA calls."""
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

import pytest
from test_public_campaign_export import records

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
    fields=proof.verify(actual_proof,require_outer=True)
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
    with pytest.raises(ValueError,match='never overwritten'):
        proof.attest(actual_proof/'clinical-records.zip',proof.digest((actual_proof/'clinical-records.zip').read_bytes()),actual_proof,actual_proof)
    assert (actual_proof/'receipt.json').read_bytes()==before
