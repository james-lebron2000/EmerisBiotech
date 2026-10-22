"""Read only already-acquired public intake sources; prepare exact-target reviews.

No network or model calls. No labels, actors, reviews or Arena events are created.
The existing runtime key decrypts only intake tables, never user datasets/sessions.
"""
import argparse
import base64
from contextlib import closing
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
from urllib.parse import urlsplit
from cryptography.fernet import Fernet

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from emeris_live.arena_public import read_frozen
from emeris_live.arena_watch import current_targets
from emeris_live.arena_store import instant,result_target_binding,sha

VERSION='predome-arena-outcome-inbox-1'
KINDS={'registry_update','reported_results_candidate','official_announcement_candidate','pubmed_literature_candidate'}
LIMIT=2000


def regular(path,maximum):
    p=Path(path)
    if p.is_symlink() or not p.is_file() or not 0<p.stat().st_size<=maximum:raise ValueError('Existing bounded regular file required')
    return p


def source_url(value):
    if not isinstance(value,str):raise ValueError('Public source URL required')
    p=urlsplit(value)
    if (p.scheme!='https' or not p.hostname or p.username or p.password or p.port not in (None,443)
            or p.fragment or len(value)>4000):raise ValueError('Public acquisition URL is invalid')
    return value


def read_acquired(data_dir,ncts):
    ncts=list(dict.fromkeys(ncts))
    if len(ncts)>30 or any(not re.fullmatch(r'NCT[0-9]{8}',n) for n in ncts):raise ValueError('Bounded exact registry identities required')
    if not ncts:return []
    root=Path(data_dir)
    if root.is_symlink() or not root.is_dir():raise ValueError('Existing runtime directory required')
    path=regular(root/'workbench.sqlite3',8*1024**3)
    cipher=Fernet(regular(root/'master.key',256).read_bytes())
    unpack=lambda value:json.loads(cipher.decrypt(value))
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row;db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
        rows=db.execute('SELECT id,nct,kind,source_url,sha256,created,payload FROM intake_candidates WHERE nct IN ('+','.join('?' for _ in ncts)+') ORDER BY created DESC,id DESC LIMIT ?', (*ncts,LIMIT+1))
        values=[];total_bytes=0
        for number,row in enumerate(rows,1):
            if number>LIMIT:raise ValueError('Intake inbox exceeds explicit bound; partition before reading')
            if len(row['payload'])>12*1024*1024:raise ValueError('Acquisition payload exceeds explicit bound')
            if row['kind'] not in KINDS:continue
            item=unpack(row['payload']);source=item.get('source',{});url=source_url(source.get('url'))
            if row['kind'] in ('registry_update','reported_results_candidate') and url!='https://clinicaltrials.gov/api/v2/studies/'+row['nct']:
                raise ValueError('Registry source does not bind the exact trial')
            if row['kind']=='official_announcement_candidate':
                from emeris_live.official_intake import allowed
                if not allowed(url):raise ValueError('Official source left approved domains')
            if row['kind']=='pubmed_literature_candidate' and urlsplit(url).hostname!='www.ebi.ac.uk':
                raise ValueError('Unapproved literature index source')
            digest=source.get('sha256')
            if (item.get('nct_id')!=row['nct'] or item.get('kind')!=row['kind']
                    or row['source_url'] not in (url,url+'#predome-trial='+row['nct'])
                    or not re.fullmatch(r'[a-f0-9]{64}',str(digest)) or digest!=row['sha256']):
                raise ValueError('Acquired candidate identity differs from its source record')
            if not re.fullmatch(r'NCT[0-9]{8}',row['nct']):raise ValueError('Invalid acquired registry identity')
            if instant(source.get('retrieved_at'))>datetime.now(timezone.utc):raise ValueError('Future acquisition time')
            blob=db.execute('SELECT payload FROM intake_source_blobs WHERE sha256=?',(digest,)).fetchone()
            if blob:
                if len(blob['payload'])>12*1024*1024:raise ValueError('Source archive exceeds explicit bound')
                raw=base64.b64decode(unpack(blob['payload'])['bytes_b64'],validate=True)
            else:
                text=item.get('raw_text')
                if not isinstance(text,str):raise ValueError('Missing acquired original source')
                raw=text.encode('utf-8')
            if not 0<len(raw)<=6*1024*1024 or hashlib.sha256(raw).hexdigest()!=digest:
                raise ValueError('Acquired original source bytes failed integrity')
            total_bytes+=len(raw)
            if total_bytes>32*1024*1024:raise ValueError('Selected source corpus exceeds 32 MiB; partition before reading')
            structured=item.get('structured',{})
            if row['kind'] in ('registry_update','reported_results_candidate'):
                value=json.loads(raw)
                if value.get('protocolSection',{}).get('identificationModule',{}).get('nctId')!=row['nct']:
                    raise ValueError('Original registry response has a different identity')
                from emeris_live.intake import registry_summary
                structured=registry_summary(value)
                expected='reported_results_candidate' if structured['has_results'] else 'registry_update'
                if expected!=row['kind']:raise ValueError('Registry candidate kind differs from original bytes')
            values.append({'id':row['id'],'nct_id':row['nct'],'kind':row['kind'],
                'url':url,'sha256':digest,'retrieved_at':source['retrieved_at'],
                'bytes':len(raw),'raw':raw,'structured':structured})
    return values


def build(data_dir,target_id=None):
    root=Path(data_dir)
    if root.is_symlink() or not root.is_dir():raise ValueError('Existing runtime directory required')
    regular(root/'workbench.sqlite3',8*1024**3);regular(root/'master.key',256)
    class View:path=root
    snapshot=read_frozen(View());targets=current_targets(snapshot)
    if target_id is not None:
        targets=[t for t in targets if t['id']==target_id]
        if not targets:raise ValueError('Unknown current formal target')
    acquired=read_acquired(data_dir,[t['nct_id'] for t in targets])
    rows=[]
    for t in targets:
        candidates=[{'id':c['id'],'kind':c['kind'],'source_url':c['url'],'source_sha256':c['sha256'],
            'retrieved_at':c['retrieved_at'],'bytes':c['bytes'],
            'source_says_results_available':c['structured'].get('has_results') if c['kind'] in ('registry_update','reported_results_candidate') else None,
            'document_publication_metadata':c['structured'].get('published_at'),
            'publication_date_basis':c['structured'].get('publication_date_basis'),
            'review_status':'needs_exact_endpoint_source_review','outcome_label':None}
            for c in acquired if c['nct_id']==t['nct_id']]
        rows.append({'target_id':t['id'],'nct_id':t['nct_id'],'trial_name':t.get('trial_name'),
            'target_binding':result_target_binding(t),'target_binding_sha256':sha(result_target_binding(t)),
            'candidates':candidates,'coverage':'acquired_candidates' if candidates else 'no_matching_acquired_source'})
    return {'schema_version':VERSION,'checked_at':datetime.now(timezone.utc).isoformat(),
        'ledger_sha256':snapshot.get('ledger_sha256'),'targets':rows,
        'target_count':len(rows),'targets_with_acquired_sources':sum(bool(r['candidates']) for r in rows),
        'sources_are_not_adjudications':True,'absence_is_not_evidence_of_no_result':True,
        'network_calls':0,'provider_calls':0,'ledger_writes':0,'actual_outcomes_created':0}


def write_new(path,value):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'wb') as stream:stream.write(value)


def encoded(value):return (json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n').encode()


def export(data_dir,target_id,candidate_id,output):
    report=build(data_dir,target_id)
    target=next((t for t in report['targets'] if t['target_id']==target_id),None)
    if not target or not any(c['id']==candidate_id for c in target['candidates']):raise ValueError('Candidate does not bind selected formal target')
    expected=next(c for c in target['candidates'] if c['id']==candidate_id)
    candidate=next(c for c in read_acquired(data_dir,[target['nct_id']]) if c['id']==candidate_id)
    if any(candidate[a]!=expected[b] for a,b in (('url','source_url'),('sha256','source_sha256'),('retrieved_at','retrieved_at'))):raise ValueError('Selected acquisition changed during preparation')
    if candidate['kind']=='pubmed_literature_candidate':raise ValueError('Literature index response is discovery, not an official outcome source')
    # A registry response is not an announcement, and its metadata does not
    # establish the earliest public release of the exact primary-endpoint result.
    # Reuse the canonical preparer so corrections carry current result pointers.
    from arena_result_review import prepare,read
    output=Path(output)
    if output.exists() or output.is_symlink():raise FileExistsError('Review output must be a new private directory')
    output.mkdir(parents=True,mode=0o700,exist_ok=False)
    prepared=prepare(data_dir,target_id,output/'review.json')
    canonical=read(output/'review.json');canonical['result'].update(source_url=candidate['url'],source_sha256=candidate['sha256'])
    # This newly prepared editable worksheet is not an immutable forecast record.
    with (output/'review.json').open('w') as stream:json.dump(canonical,stream,ensure_ascii=False,indent=2,allow_nan=False);stream.write('\n')
    write_new(output/'source.bin',candidate['raw'])
    write_new(output/'source-manifest.json',encoded({'schema_version':'predome-outcome-acquisition-v1','assets':[{
        'source_url':candidate['url'],'sha256':candidate['sha256'],'bytes':candidate['bytes'],
        'retrieved_at':candidate['retrieved_at'],'candidate_id':candidate_id}]}))
    receipt={'schema_version':VERSION,'target_id':target_id,'candidate_id':candidate_id,'ledger_sha256':prepared['ledger_sha256'],
        'source_sha256':candidate['sha256'],'label':None,'first_public_at':None,'reviewer_id':None,
        'files':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in output.iterdir()},
        'network_calls':0,'provider_calls':0,'ledger_writes':0,'actual_outcomes_created':0}
    write_new(output/'receipt.json',encoded(receipt));return receipt


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--data-dir',required=True,type=Path)
    sub=p.add_subparsers(dest='command',required=True)
    listing=sub.add_parser('list');listing.add_argument('--output',required=True,type=Path);listing.add_argument('--target')
    item=sub.add_parser('export');item.add_argument('--target',required=True);item.add_argument('--candidate',required=True);item.add_argument('--output-dir',required=True,type=Path)
    a=p.parse_args()
    if a.command=='list':
        report=build(a.data_dir,a.target);write_new(a.output,encoded(report));print(json.dumps({k:report[k] for k in ('target_count','targets_with_acquired_sources','ledger_writes')}))
    else:print(json.dumps(export(a.data_dir,a.target,a.candidate,a.output_dir)))


if __name__=='__main__':main()
