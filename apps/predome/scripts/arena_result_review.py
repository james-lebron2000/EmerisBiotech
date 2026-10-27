"""Prepare exact-target outcome worksheets; append explicitly typed source reviews.

No model calls or source downloads. Existing source bytes and acquisition metadata
are required for submission. An operator-declared human is not an authenticated
clinical credential; AI agents must declare kind=ai.
"""
import argparse
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from emeris_live.arena_store import ArenaLedger, instant, result_target_binding, sha, validate_result_review_input
from arena_learning_report import read_snapshot

VERSION='predome-result-review-1'
FIELDS={'schema_version','target_id','target_binding_sha256','reviewer_id','reviewer_kind','review_note',
        'supersedes_result_sha256','supersedes_review_sha256','result'}


def read(path,maximum=2*1024*1024):
    path=Path(path)
    if path.is_symlink() or not path.is_file() or not 0<path.stat().st_size<=maximum:
        raise ValueError('Existing bounded regular input file required')
    def unique(pairs):
        result={}
        for key,value in pairs:
            if key in result:raise ValueError('Duplicate review JSON key')
            result[key]=value
        return result
    return json.loads(path.read_text(),object_pairs_hook=unique,
        parse_constant=lambda value:(_ for _ in ()).throw(ValueError('Nonfinite JSON')))


def outcome_version(target):
    current=target.get('result') if target.get('status')=='adjudicated' else target.get('previous_adjudicated_result')
    return (current or {}).get('result_sha256')


def latest_review(snapshot,target_id,reviewer_id,version):
    rounds=[e['seq'] for e in snapshot['events'] if e['kind']=='result_adjudicated' and e['entity_id']==target_id]
    round_start=rounds[-1] if rounds else 0
    values=[e for e in snapshot['events'] if e['kind']=='result_review' and e['entity_id']==target_id
        and e['seq']>round_start and not e['payload'].get('reviewer_authentication')
        and e['payload']['reviewer_id'].casefold()==reviewer_id.casefold() and e['payload'].get('supersedes')==version]
    return values[-1]['payload'].get('vote_sha256',values[-1]['event_hash']) if values else None


def prepare(data_dir,target_id,output,reviewer_id=None):
    snapshot=read_snapshot(Path(data_dir)/'arena.sqlite3')
    target=next((t for t in snapshot['targets'] if t['id']==target_id),None)
    if target is None:raise ValueError('Unknown registered target')
    binding=result_target_binding(target)
    if reviewer_id is not None and not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}',reviewer_id):raise ValueError('Stable reviewer ID required')
    version=outcome_version(target)
    value={'schema_version':VERSION,'target_id':target_id,'target_binding_sha256':sha(binding),
        'reviewer_id':reviewer_id,'reviewer_kind':None,'review_note':None,
        'supersedes_result_sha256':version,
        'supersedes_review_sha256':latest_review(snapshot,target_id,reviewer_id,version) if reviewer_id else None,
        'result':{'target_binding':binding,'label':None,'source_url':None,'source_sha256':None,
            'first_public_at':None,'endpoint_match_note':None,'effect_size':None,'confidence_interval':None,'p_value':None}}
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
    fd=os.open(output,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w',encoding='utf-8') as stream:json.dump(value,stream,ensure_ascii=False,indent=2,allow_nan=False);stream.write('\n')
    return {'status':'worksheet_prepared_not_a_review','target_id':target_id,'output':str(output.resolve()),
        'ledger_sha256':snapshot['ledger_sha256'],'provider_calls':0,'ledger_writes':0,'human_reviews_created':0}


def submit(data_dir,review_file,source_file,acquisition_manifest):
    snapshot=read_snapshot(Path(data_dir)/'arena.sqlite3')
    review=read(review_file)
    if set(review)!=FIELDS or review['schema_version']!=VERSION:raise ValueError('Explicit typed review schema required')
    if review['reviewer_kind'] not in ('ai','human'):raise ValueError('Declare ai or human; missing types are not inferred')
    if re.search(r'(?<![A-Za-z])sk-[A-Za-z0-9_-]{24,}',json.dumps(review)):
        raise ValueError('Credential-like content must not enter a review')
    target=next((t for t in snapshot['targets'] if t['id']==review['target_id']),None)
    if target is None or review['target_binding_sha256']!=sha(result_target_binding(target)):
        raise ValueError('Review worksheet does not bind the registered target')
    result=review['result']
    reviewer=validate_result_review_input(result,review['reviewer_id'],review['reviewer_kind'],review['review_note'])
    if result.get('target_binding')!=result_target_binding(target):raise ValueError('Exact registered hypothesis changed')
    version=outcome_version(target)
    if (review['supersedes_result_sha256']!=version
            or review['supersedes_review_sha256']!=latest_review(snapshot,target['id'],reviewer,version)):
        raise ValueError('Stale result version or own review correction pointer')
    source=Path(source_file)
    if source.is_symlink() or not source.is_file() or not 0<source.stat().st_size<=32*1024*1024:
        raise ValueError('Existing original source bytes required')
    raw=source.read_bytes();digest=hashlib.sha256(raw).hexdigest()
    if digest!=result.get('source_sha256'):raise ValueError('Original source hash differs from proposed result')
    manifest=read(acquisition_manifest)
    if (not isinstance(manifest,dict) or manifest.get('schema_version') not in ('arena-public-acquisition-v1','predome-outcome-acquisition-v1')
            or not isinstance(manifest.get('assets'),list) or any(not isinstance(a,dict) for a in manifest['assets'])):
        raise ValueError('Explicit public source acquisition schema required')
    matches=[a for a in manifest.get('assets',[]) if a.get('source_url')==result.get('source_url')
        and a.get('sha256')==digest and type(a.get('bytes')) is int and a['bytes']==len(raw)]
    if not matches:raise ValueError('Source URL, original bytes and acquisition manifest do not agree')
    if not all(instant(result.get('first_public_at'))<=instant(a.get('retrieved_at'))<=datetime.now(timezone.utc) for a in matches):
        raise ValueError('Publication and source acquisition times are inconsistent')
    # The existing ledger is opened for mutation only after all external inputs
    # have passed checks. Constructor cannot create a new accidental runtime.
    ledger=ArenaLedger(data_dir)
    status=ledger.record_result(review['target_id'],result,reviewer_id=review['reviewer_id'],
        reviewer_kind=review['reviewer_kind'],review_note=review['review_note'],
        supersedes=review['supersedes_result_sha256'],review_supersedes=review['supersedes_review_sha256'])
    state=ledger.snapshot();target=next(t for t in state['targets'] if t['id']==review['target_id'])
    return {'status':status,'target_id':target['id'],'reviewer_kind':review['reviewer_kind'],
        'review_provenance':target['result']['review_provenance'],'ledger_sha256':state['ledger_sha256'],
        'provider_calls':0,'independent_human_identity_authenticated':False}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--data-dir',required=True,type=Path)
    sub=p.add_subparsers(dest='action',required=True)
    prep=sub.add_parser('prepare');prep.add_argument('--target',required=True);prep.add_argument('--output',required=True,type=Path);prep.add_argument('--reviewer')
    apply=sub.add_parser('submit');apply.add_argument('--review-file',required=True,type=Path)
    apply.add_argument('--source-file',required=True,type=Path);apply.add_argument('--acquisition-manifest',required=True,type=Path)
    a=p.parse_args()
    result=(prepare(a.data_dir,a.target,a.output,a.reviewer) if a.action=='prepare'
        else submit(a.data_dir,a.review_file,a.source_file,a.acquisition_manifest))
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
