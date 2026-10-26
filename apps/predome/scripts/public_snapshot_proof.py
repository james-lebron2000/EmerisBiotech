"""Detached RFC3161 proof of a sanitized public export, never a new forecast."""
import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import urllib.request
import zipfile
import zlib

import export_public_campaign as campaign

PINS={'tsa.crt':'8bfb0305bb64e2571ca507552ef3245cb1c2fee8728e0ff8689225081ea13467',
      'cacert.pem':'2151b61137ffa86bf664691ba67e7da0b19f98c758e3d228d5d8ebf27e044438'}
README_PINS={
    'predome-public-launch-bundle-1':'99d0bfa9c299419abb94dfc969f28a29d67263b63ba67f0e319559d9eb7cb584',
    'predome-public-launch-bundle-2':'a0439c566f87f66af68e911216743650f57394f5bf4d4df13609dc888da3eb62',
    'predome-public-launch-bundle-3':'15231fb7b3ef96bf06da4979b63dcadc39ee316f7b591b0f1771057c2235a876'}
FILES=frozenset(('clinical-records.zip','request.tsq','timestamp.tsr','cacert.pem','tsa.crt',
                 'receipt.json','verification.txt','README.md'))
PUBLIC_FILES=FILES|{'verification-kit.zip'}


def digest(raw):return hashlib.sha256(raw).hexdigest()


def regular(path,maximum=8*1024*1024):
    path=Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size>maximum:raise ValueError('A bounded regular file is required')
    return path.read_bytes()


def decode(raw):
    def unique(pairs):
        result={}
        for key,value in pairs:
            if key in result:raise ValueError('Duplicate JSON key')
            result[key]=value
        return result
    return json.loads(raw,object_pairs_hook=unique,
        parse_constant=lambda value:(_ for _ in ()).throw(ValueError('Nonfinite JSON')))


def zip_members(raw):
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries=archive.infolist();names=[e.filename for e in entries]
            if len(set(names))!=len(names) or sum(e.file_size for e in entries)>32*1024*1024:
                raise ValueError('Duplicate or oversized ZIP content')
            if any(e.is_dir() or (e.external_attr>>16)&0o170000 not in (0,0o100000) for e in entries):
                raise ValueError('ZIP links and special files are forbidden')
            return {e.filename:archive.read(e) for e in entries}
    except (zipfile.BadZipFile,zlib.error,RuntimeError,EOFError):
        raise ValueError('Public ZIP integrity or compression check failed') from None


def validate_bundle(raw):
    files=zip_members(raw)
    if 'manifest.json' not in files:raise ValueError('Missing public export manifest')
    m=decode(files['manifest.json'])
    required={'schema_version','exported_at_utc','source_origin','cohort_id','formal_trials',
        'formal_assigned_records','historical_replays','models','provider_calls','ledger_writes',
        'clinical_prediction_validated','independent_timestamp_attestation','records','files'}
    if (set(m)!=required or m['schema_version'] not in README_PINS
            or m['source_origin']!=campaign.BASE or not m['cohort_id']):raise ValueError('Unsupported public export')
    if (m['provider_calls']!=0 or m['ledger_writes']!=0 or m['clinical_prediction_validated'] is not False
            or m['independent_timestamp_attestation'] is not False):raise ValueError('Unsupported execution or clinical claim')
    if set(m['files'])!=set(files)-{'manifest.json'}:raise ValueError('Unmanifested public export file')
    for name,entry in m['files'].items():
        if set(entry)!={'sha256','bytes'} or entry!={'sha256':digest(files[name]),'bytes':len(files[name])}:
            raise ValueError('Export member hash or size mismatch')
    if digest(files.get('README.md',b''))!=README_PINS[m['schema_version']]:raise ValueError('Unknown public export explanatory text')
    expected={'README.md','manifest.json'};formal=[];history=[];models=None;ids=set()
    for item in m['records']:
        if set(item)!={'target_id','trial_name','kind','assigned_records','page_url'}:raise ValueError('Unknown record index metadata')
        ident=item['target_id']
        if not isinstance(ident,str) or not campaign.IDENTIFIER.fullmatch(ident) or ident in ids:raise ValueError('Unsafe or duplicate record identity')
        ids.add(ident);json_name='records/'+ident+'.json';report_name='reports/'+ident+'.md'
        if json_name not in files or report_name not in files:raise ValueError('Missing clinical record/report')
        record=decode(files[json_name]);target=record['target']
        legacy=m['schema_version']!=campaign.BUNDLE_VERSION
        expected_version={'predome-public-launch-bundle-1':campaign.LEGACY_VERSION,
            'predome-public-launch-bundle-2':campaign.PREVIOUS_VERSION,
            campaign.BUNDLE_VERSION:campaign.VERSION}[m['schema_version']]
        if record['version']!=expected_version:raise ValueError('Public export and record versions differ')
        replay=campaign.validate_record(record,target,allow_legacy=legacy)
        if target['id']!=ident or files[report_name]!=campaign.report_text(record):raise ValueError('Report differs from public clinical object')
        expected.add(json_name);expected.add(report_name)
        derived={'target_id':ident,'trial_name':target['trial_name'],
            'kind':'historical_replay' if replay else 'current_formal','assigned_records':len(record['runs']),
            'page_url':campaign.BASE+'/arena?target='+ident}
        if item!=derived:raise ValueError('Record index differs from exact clinical question')
        if replay:history.append(record)
        else:
            own={r['model_id'] for r in record['runs']}
            if target['cohort_id']!=m['cohort_id'] or len(own)!=4 or (models is not None and models!=own):
                raise ValueError('Formal targets must share a cohort and four models')
            models=own;formal.append(record)
    if set(files)!=expected:raise ValueError('Unexpected public export member')
    if (m['formal_trials']!=len(formal) or m['historical_replays']!=len(history)
            or m['formal_assigned_records']!=sum(len(r['runs']) for r in formal)
            or m['models']!=sorted(models or []) or not formal):raise ValueError('False public export denominator')
    instant(m['exported_at_utc'])
    return m


def instant(value):
    parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
    if parsed.tzinfo is None:raise ValueError('Timezone required')
    return parsed


def openssl_run(args,openssl='openssl'):
    env={k:os.environ[k] for k in ('PATH','SYSTEMROOT','TMPDIR') if k in os.environ}
    env['LANG']='C';env['LC_ALL']='C'
    return subprocess.check_output([openssl,*map(str,args)],env=env,stderr=subprocess.PIPE,text=True)


def verified_fields(directory,openssl='openssl'):
    directory=Path(directory)
    raw=regular(directory/'clinical-records.zip');manifest=validate_bundle(raw)
    for name,pin in PINS.items():
        if digest(regular(directory/name,65536))!=pin:raise ValueError('Timestamp trust anchor differs from pinned certificate')
    response=regular(directory/'timestamp.tsr',1048576);regular(directory/'request.tsq',65536)
    common=['-in',directory/'timestamp.tsr','-CAfile',directory/'cacert.pem','-untrusted',directory/'tsa.crt']
    for basis in (['-data',directory/'clinical-records.zip'],['-queryfile',directory/'request.tsq']):
        if 'Verification: OK' not in openssl_run(['ts','-verify',*basis,*common],openssl):
            raise ValueError('Timestamp signature, message imprint or query nonce verification failed')
    reply=openssl_run(['ts','-reply','-in',directory/'timestamp.tsr','-text'],openssl)
    query=openssl_run(['ts','-query','-in',directory/'request.tsq','-text'],openssl)
    for document in (query,reply):
        if not re.search(r'^Hash Algorithm:\s*sha256\s*$',document,re.M):
            raise ValueError('Public timestamp message imprint must use SHA-256')
    query_nonce=re.search(r'^Nonce:\s*(0x[0-9A-Fa-f]+)\s*$',query,re.M)
    reply_nonce=re.search(r'^Nonce:\s*(0x[0-9A-Fa-f]+)\s*$',reply,re.M)
    if not query_nonce or not reply_nonce or int(query_nonce.group(1),16)!=int(reply_nonce.group(1),16):
        raise ValueError('Public timestamp query and signed response require the same explicit nonce')
    match=re.search(r'^Time stamp: (.+)$',reply,re.M)
    if not match:raise ValueError('Missing authenticated timestamp generation time')
    stamp=datetime.strptime(match.group(1).strip(),'%b %d %H:%M:%S %Y GMT').replace(tzinfo=timezone.utc)
    if stamp<instant(manifest['exported_at_utc']) or stamp>datetime.now(timezone.utc):
        raise ValueError('Timestamp contradicts the public export time or current clock')
    return {'schema_version':'predome-public-bundle-rfc3161-1','tsa':'FreeTSA',
        'generated_at_utc':stamp.isoformat(),'bundle_exported_at_utc':manifest['exported_at_utc'],
        'bundle_sha256':digest(raw),'bundle_bytes':len(raw),'timestamp_response_sha256':digest(response),
        'authority_cert_sha256':PINS['tsa.crt'],'ca_cert_sha256':PINS['cacert.pem'],
        'verification_method':'openssl_ts_verify_data_and_query','time_scope':'public_export_existence_only',
        'original_prediction_time_proven':False,'clinical_prediction_validated':False,
        'new_predictions_generated':False,'provider_calls':0,'ledger_writes':0,
        'formal_trials':manifest['formal_trials'],'formal_assigned_records':manifest['formal_assigned_records'],
        'historical_replays':manifest['historical_replays']}


def verification_text(fields):
    return ('Data verification: OK\nQuery nonce verification: OK\nSigned genTime UTC: '+fields['generated_at_utc']+
        '\nSigned public ZIP SHA-256: '+fields['bundle_sha256']+'\n').encode()


def readme(fields):
    return f'''# Predome public clinical snapshot — independent timestamp

The clinical-records.zip file contains actual public records and readable reports for {fields['formal_trials']} formal trial questions ({fields['formal_assigned_records']} assigned model records) and {fields['historical_replays']} historical replays.

FreeTSA signed its SHA-256 message imprint at **{fields['generated_at_utc']}**. OpenSSL verifies both the ZIP bytes and the original query nonce. Only a digest and nonce were sent to the authority.

This proves the public ZIP existed by that signature time. It does not prove the earlier original model-generation times, clinical accuracy, patent benefit, Agent superiority, or human scientific review. Existing registration/run proofs remain separate. Historical replays are not prospective wins. The ZIP's own export manifest was written before this detached timestamp and therefore correctly states that it contains no independent attestation of its own.

Public ZIP SHA-256: `{fields['bundle_sha256']}`

## Verify offline

Obtain a trusted copy of the verifier from the EmerisBiotech repository, scripts/public_snapshot_proof.py. In apps/predome run:

```sh
python scripts/public_snapshot_proof.py verify --directory /path/to/extracted-kit
```

For the underlying cryptography:

```sh
openssl ts -verify -data clinical-records.zip -in timestamp.tsr -CAfile cacert.pem -untrusted tsa.crt
openssl ts -verify -queryfile request.tsq -in timestamp.tsr -CAfile cacert.pem -untrusted tsa.crt
openssl ts -reply -in timestamp.tsr -text
```

The supplied CA alone is not an independent identity check. Compare the certificates with the pinned hashes in the trusted repository or the authority's HTTPS files at https://freetsa.org/files/cacert.pem and https://freetsa.org/files/tsa.crt.

CA SHA-256: `{PINS['cacert.pem']}`

TSA certificate SHA-256: `{PINS['tsa.crt']}`

No model call, ledger change, paid prediction, patient data upload, or new clinical adjudication is performed by this verification kit. This is a cryptographic research record, not legal notarization.
'''.encode()


def verify(directory,openssl='openssl',require_outer=False):
    directory=Path(directory)
    if directory.is_symlink() or not directory.is_dir():raise ValueError('Existing regular proof directory required')
    names={p.name for p in directory.iterdir()}
    if names not in (FILES,PUBLIC_FILES) or (require_outer and names!=PUBLIC_FILES):raise ValueError('Unexpected proof directory members')
    fields=verified_fields(directory,openssl)
    if decode(regular(directory/'receipt.json'))!=fields:raise ValueError('Receipt is not the independently verified timestamp claim')
    if regular(directory/'verification.txt')!=verification_text(fields) or regular(directory/'README.md')!=readme(fields):
        raise ValueError('Proof explanations differ from the verified limited claim')
    if 'verification-kit.zip' in names:
        members=zip_members(regular(directory/'verification-kit.zip'))
        if set(members)!=FILES or any(members[name]!=regular(directory/name) for name in FILES):
            raise ValueError('Download kit differs from the independently verified files')
    return fields


def exclusive(path,raw,mode=0o600):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,mode)
    with os.fdopen(fd,'wb') as stream:stream.write(raw)


def attest(bundle_path,expected_sha256,directory,cert_dir,openssl='openssl'):
    raw=regular(bundle_path)
    if digest(raw)!=expected_sha256:raise ValueError('Selected public ZIP changed')
    manifest=validate_bundle(raw)
    if manifest['schema_version']!=campaign.BUNDLE_VERSION:
        raise ValueError('Legacy public bundles are verification-only; new attestations require the current schema')
    directory=Path(directory)
    if directory.exists() or directory.is_symlink():raise ValueError('Proof directories are never overwritten')
    certs={name:regular(Path(cert_dir)/name,65536) for name in PINS}
    if any(digest(certs[name])!=pin for name,pin in PINS.items()):raise ValueError('Untrusted certificate input')
    directory.mkdir(parents=True,mode=0o700)
    exclusive(directory/'clinical-records.zip',raw)
    for name,cert in certs.items():exclusive(directory/name,cert)
    openssl_run(['ts','-query','-data',directory/'clinical-records.zip','-sha256','-cert','-out',directory/'request.tsq'],openssl)
    request=urllib.request.Request('https://freetsa.org/tsr',data=regular(directory/'request.tsq',65536),
        headers={'Content-Type':'application/timestamp-query'})
    with urllib.request.urlopen(request,timeout=45) as response:
        signed=response.read(1048577)
        if len(signed)>1048576:raise ValueError('Timestamp authority response exceeds limit')
    exclusive(directory/'timestamp.tsr',signed)
    fields=verified_fields(directory,openssl)
    exclusive(directory/'receipt.json',campaign.canonical(fields))
    exclusive(directory/'verification.txt',verification_text(fields));exclusive(directory/'README.md',readme(fields))
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(FILES):archive.writestr(name,regular(directory/name))
    exclusive(directory/'verification-kit.zip',output.getvalue())
    return verify(directory,openssl,require_outer=True)


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='action',required=True)
    for action in ('attest','verify'):
        s=sub.add_parser(action);s.add_argument('--directory',type=Path,required=True);s.add_argument('--openssl',default='openssl')
        if action=='attest':
            s.add_argument('--bundle',type=Path,required=True);s.add_argument('--expected-sha256',required=True);s.add_argument('--cert-dir',type=Path,required=True)
    a=p.parse_args()
    fields=(attest(a.bundle,a.expected_sha256,a.directory,a.cert_dir,a.openssl) if a.action=='attest'
            else verify(a.directory,a.openssl))
    print(json.dumps({'status':'verified',**fields}))


if __name__=='__main__':main()
