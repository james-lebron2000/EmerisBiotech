"""Read only already-acquired public intake sources; prepare exact-target reviews.

No network or model calls. No labels, actors, reviews or Arena events are created.
The existing runtime key decrypts only intake tables, never user datasets/sessions.
"""
import argparse
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from emeris_live.arena_public import read_frozen
from emeris_live.arena_watch import current_targets
from emeris_live.arena_acquired import KINDS, LIMIT, regular, source_url, read_acquired
from emeris_live.arena_store import result_target_binding,sha

VERSION='predome-arena-outcome-inbox-1'

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
