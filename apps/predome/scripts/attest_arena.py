"""Timestamp a public ledger snapshot with FreeTSA RFC 3161 and verify its signature.

Only a SHA-256 message imprint is transmitted. The authority's certificate hashes
are pinned to its HTTPS documentation retrieved 2026-10-22. This is a third-party
cryptographic timestamp, not a legal notarization or clinical validation.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import urllib.request
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from emeris_live.arena_store import ArenaLedger

PINS={'tsa.crt':'8bfb0305bb64e2571ca507552ef3245cb1c2fee8728e0ff8689225081ea13467',
      'cacert.pem':'2151b61137ffa86bf664691ba67e7da0b19f98c758e3d228d5d8ebf27e044438'}

def digest(raw):return hashlib.sha256(raw).hexdigest()

def attest(data_dir, out, purpose, public_base, openssl='openssl'):
    out=Path(out)
    if out.exists():raise ValueError('Timestamp directories are never overwritten')
    if not re.fullmatch(r'[a-zA-Z0-9-]{1,80}',out.name):raise ValueError('Invalid public attestation identifier')
    if not public_base.startswith('https://'):raise ValueError('HTTPS public base required')
    ledger=ArenaLedger(data_dir);snapshot=ledger.snapshot()
    if not snapshot['events']:raise ValueError('Cannot attest an empty ledger')
    manifest={'schema_version':'predome-arena-attestation-1','purpose':purpose,
        'ledger_sha256':snapshot['ledger_sha256'],'covered_event_count':len(snapshot['events']),
        'snapshot':snapshot,'trust_note':'Signature verification trusts the pinned FreeTSA CA. This is not legal notarization.'}
    out.mkdir(parents=True)
    raw=(json.dumps(manifest,ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False)+'\n').encode()
    (out/'manifest.json').write_bytes(raw)
    # Remove credentials from child processes; OpenSSL needs no model secrets.
    env={k:v for k,v in os.environ.items() if not any(word in k.upper() for word in ('KEY','TOKEN','SECRET','PASSWORD'))}
    def run(args):
        return subprocess.check_output([openssl,*args],env=env,stderr=subprocess.PIPE,text=True)
    for name,pin in PINS.items():
        response=urllib.request.urlopen('https://freetsa.org/files/'+name,timeout=30)
        cert=response.read(65536)
        if digest(cert)!=pin:raise ValueError('Authority certificate changed; review before accepting a new trust anchor')
        (out/name).write_bytes(cert)
    run(['ts','-query','-data',str(out/'manifest.json'),'-sha256','-cert','-out',str(out/'manifest.tsq')])
    query=(out/'manifest.tsq').read_bytes()
    request=urllib.request.Request('https://freetsa.org/tsr',data=query,headers={'Content-Type':'application/timestamp-query'})
    with urllib.request.urlopen(request,timeout=45) as response:
        signed=response.read(1048577)
        if len(signed)>1048576:raise ValueError('Oversized TSA response')
    (out/'manifest.tsr').write_bytes(signed)
    verify=run(['ts','-verify','-data',str(out/'manifest.json'),'-in',str(out/'manifest.tsr'),
                '-CAfile',str(out/'cacert.pem'),'-untrusted',str(out/'tsa.crt')])
    if 'Verification: OK' not in verify:raise ValueError('Timestamp signature did not verify')
    reply=run(['ts','-reply','-in',str(out/'manifest.tsr'),'-text'])
    (out/'verification.txt').write_text(verify+'\n'+reply)
    match=re.search(r'^Time stamp: (.+)$',reply,re.M)
    if not match:raise ValueError('Missing authenticated genTime')
    stamp=datetime.strptime(match.group(1).strip(),'%b %d %H:%M:%S %Y GMT').replace(tzinfo=timezone.utc)
    receipt={'id':out.name,'purpose':purpose,'ledger_sha256':snapshot['ledger_sha256'],
        'covered_event_count':len(snapshot['events']),'tsa':'FreeTSA','generated_at_utc':stamp.isoformat(),
        'verified_at_utc':datetime.now(timezone.utc).isoformat(),'verification_method':'openssl_ts_verify',
        'signature_verified':True,'timestamp_response_sha256':digest(signed),'manifest_sha256':digest(raw),
        'authority_cert_sha256':PINS['tsa.crt'],'ca_cert_sha256':PINS['cacert.pem'],
        'public_url':public_base.rstrip('/')+'/api/arena/attestations/'+out.name,
        'trust_note':manifest['trust_note'],'files':{p.name:digest(p.read_bytes()) for p in sorted(out.iterdir()) if p.is_file()}}
    (out/'receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n')
    ledger.attest(receipt)
    print(json.dumps({k:receipt[k] for k in ('id','purpose','generated_at_utc','ledger_sha256','signature_verified')}))
    return receipt

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data-dir',required=True);p.add_argument('--out',required=True)
    p.add_argument('--purpose',required=True,choices=['registration','predictions'])
    p.add_argument('--public-base',default='https://8.148.145.28');p.add_argument('--openssl',default='openssl')
    a=p.parse_args();attest(a.data_dir,a.out,a.purpose,a.public_base,a.openssl)

if __name__=='__main__':main()
