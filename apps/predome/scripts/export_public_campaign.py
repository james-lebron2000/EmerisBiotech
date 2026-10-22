"""Export existing public clinical records for a campaign; never run a model.

Fixed same-origin GET endpoints, ordinary TLS, no credentials or internal API.
The ZIP is a dated public snapshot, not an immutable prediction or new TSA proof.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import html
import io
import json
import math
import os
from pathlib import Path
import re
import urllib.request
from urllib.parse import quote
import zipfile

BASE = 'https://8.148.145.28'
PREFIX = '/api/arena/public'
IDENTIFIER = re.compile(r'[A-Za-z0-9_.-]{1,180}')
FORBIDDEN = {'prompt', 'system_prompt', 'reasoning', 'packet', 'usage', 'token_usage',
    'api_key', 'provider_config_snapshot', 'cost_usd', 'latency_ms', 'metrics',
    'accuracy', 'brier', 'workflow_trace', 'input_sha256', 'output_sha256'}
ROOT_KEYS = {'version','target','runs','consensus','sources','evidence','result','public_policy'}
RUN_KEYS = {'id','target_id','cohort_id','model_id','requested_model','scope','track',
    'condition','generated_at_utc','locked_at_utc','model_version','status',
    'predicted_outcome','probability','confidence','calibration_status','brief_reason',
    'reason','sources','match','match_status'}
TARGET_KEYS = {'id','cohort_id','trial_name','nct_id','drug','sponsor','indication',
    'endpoint','population','comparison','success_definition','analysis_timepoint',
    'scope','registered_at','execution_status','information_cutoff_utc','record_kind',
    'verification_note','status','registry_url','phase','design','registry_status',
    'readout','result','sources','consensus'}
SOURCE_KEYS = {'id','title','source_url','source_snapshot_url','publication_date','kind','page'}
POLICY_KEYS = {'calibration_status','probability','confidence','direction_rule','consensus_rule',
    'comparison_scope','clinical_prediction_validated'}
CONSENSUS_KEYS = {'predicted_outcome','assigned','counts','agreement','agreement_denominator',
    'average_probability','confidence','calibration_status'}
RESULT_KEYS = {'status','label','first_public_at','source_url','summary','effect_size',
    'confidence_interval','p_value','numeric_source_url','verification_note'}
READOUT_KEYS = {'label','start','end','precision','source_url','source_title','source_granularity','source_note'}


def canonical(value):
    return (json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False)+'\n').encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def fetch_public(path):
    if path != PREFIX and not re.fullmatch(PREFIX+r'/targets/[A-Za-z0-9_.-]{1,180}/record',path):
        raise ValueError('Only fixed public clinical GET routes are allowed')
    request=urllib.request.Request(BASE+path,headers={'Accept':'application/json','User-Agent':'Predome-public-export/1.0'})
    with urllib.request.build_opener(NoRedirect).open(request,timeout=45) as response:
        raw=response.read(8*1024*1024+1)
        if len(raw)>8*1024*1024:raise ValueError('Public response exceeds export limit')
    return json.loads(raw,parse_constant=lambda value:(_ for _ in ()).throw(ValueError('Nonfinite JSON')))


def check_public(value):
    if isinstance(value,dict):
        for key,item in value.items():
            if key.casefold() in FORBIDDEN:raise ValueError('Internal metadata is not public campaign material')
            if key in ('probability','confidence','average_probability') and item is not None:
                raise ValueError('Uncalibrated numbers cannot enter the public bundle')
            check_public(item)
    elif isinstance(value,list):
        for item in value:check_public(item)
    elif isinstance(value,float) and not math.isfinite(value):raise ValueError('Nonfinite value')
    elif isinstance(value,str) and re.search(r'(?<![A-Za-z])sk-[A-Za-z0-9_-]{24,}|-----BEGIN .*PRIVATE KEY-----',value):
        raise ValueError('Credential-like text rejected')


def validate_record(record, selected):
    check_public(record)
    if not isinstance(record,dict) or set(record)!=ROOT_KEYS:raise ValueError('Unexpected public record schema')
    target=record['target'];runs=record['runs']
    if set(target)-TARGET_KEYS or target!=selected:raise ValueError('Target changed during export; start a new snapshot')
    if (set(record['public_policy'])!=POLICY_KEYS or set(record['consensus'])!=CONSENSUS_KEYS
            or set(target['readout'])-READOUT_KEYS):raise ValueError('Unexpected nested public metadata')
    if record['public_policy']['clinical_prediction_validated'] is not False:
        raise ValueError('This export does not support a clinical validation claim')
    design=target.get('design')
    if design is not None:
        if not isinstance(design,dict) or set(design)-{'allocation','intervention_model','masking','primary_purpose','enrollment'}:
            raise ValueError('Unknown clinical design metadata')
        enrollment=design.get('enrollment')
        if enrollment is not None and (not isinstance(enrollment,dict) or set(enrollment)-{'count','type'}):
            raise ValueError('Unknown enrollment metadata')
    if target['result'] is not None and set(target['result'])!=RESULT_KEYS:raise ValueError('Unexpected result metadata')
    if not isinstance(runs,list) or not runs or len({r['id'] for r in runs})!=len(runs):
        raise ValueError('Missing or duplicate assigned records')
    historical=target.get('record_kind')=='historical_replay'
    for run in runs:
        if set(run)-RUN_KEYS:raise ValueError('Unknown model metadata')
        if (run['target_id']!=target['id'] or run['condition']!='clinical_only'
                or run['track']!=('archived' if historical else 'same_evidence')
                or run['cohort_id']!=target['cohort_id']
                or not run.get('model_version') or run['probability'] is not None or run['confidence'] is not None):
            raise ValueError('Model records do not share the exact public question')
        if run.get('match') is not None and (historical or target['status']!='adjudicated'):
            raise ValueError('Historical or unresolved records cannot earn prospective matches')
        if run['predicted_outcome'] not in ('Success','Failure','Uncertain'):raise ValueError('Unknown direction')
        if run['status']!='predicted' and run['predicted_outcome']!='Uncertain':raise ValueError('Missing outputs are not directional predictions')
    if len({r['model_id'] for r in runs})!=len(runs):raise ValueError('Duplicate model assignments')
    counts={label:sum(r['predicted_outcome']==label for r in runs) for label in ('Success','Failure','Uncertain')}
    c=record['consensus']
    if (c!=target['consensus'] or c['assigned']!=len(runs) or c['counts']!=counts
            or c['agreement_denominator']!=len(runs) or c['agreement']!=max(counts.values())/len(runs)
            or c['predicted_outcome']!=next((k for k,v in counts.items() if v>len(runs)/2),'Uncertain')):
        raise ValueError('Consensus does not match the assigned records')
    if record['result']!=target['result']:raise ValueError('Result does not match the exact target')
    for source in record['sources']+record['evidence']+target['sources']+[s for r in runs for s in r['sources']]:
        if not isinstance(source,dict) or set(source)-SOURCE_KEYS:raise ValueError('Unknown source metadata')
    return historical


def cell(value):
    # A JSON round trip sorts nested registry fields; rendering must not depend
    # on dictionary insertion order if a report is to be independently rebuilt.
    value=json.dumps(value,ensure_ascii=False,sort_keys=True) if isinstance(value,(dict,list)) else value
    text=str(value if value is not None else 'N/A').replace('\n',' ').replace('\r',' ')
    return re.sub(r'([\\`*_{}\[\]()#+!|])',r'\\\1',html.escape(text,quote=False))


def report_text(record):
    t=record['target'];historical=t.get('record_kind')=='historical_replay'
    lines=[f"# {cell(t['trial_name'])}", '',
        'Historical replay — not a prospective prediction.' if historical else 'Current formal clinical question.', '',
        f"[Open this exact record]({BASE}/arena?target={quote(t['id'])})", '',
        '## Clinical trial', '', '| Field | Recorded value |','| --- | --- |']
    for label,key in [('NCT','nct_id'),('Drug','drug'),('Sponsor','sponsor'),('Indication','indication'),
        ('Phase','phase'),('Study design','design'),('Trial status','registry_status'),('Primary endpoint','endpoint'),('Population','population'),('Comparison','comparison'),
        ('Success definition','success_definition'),('Information cutoff','information_cutoff_utc')]:
        lines.append(f'| {label} | {cell(t.get(key))} |')
    lines += [f"| Expected readout / archived readout | {cell(t.get('readout',{}).get('label'))} |",'',
        '## AI Clinical Trial Predictions','','| Model / version | Outcome | Success probability | Confidence | Status | Record time UTC | Prediction vs reality |',
        '| --- | --- | --- | --- | --- | --- | --- |']
    for r in sorted(record['runs'],key=lambda r:r['model_id']):
        model=r['model_id']+(' / '+r['model_version'] if r['model_id']!=r['model_version'] else '')
        lines.append('| '+' | '.join(map(cell,[model,r['predicted_outcome'],'N/A','N/A',r['status'],r['generated_at_utc'],r['match_status']]))+' |')
    lines += ['','These are actual stored records. Probability and confidence are not clinically calibrated. Refusals and failures remain in the assigned denominator.', '',
        '## Consensus and actual result','',f"Majority prediction: {cell(record['consensus']['predicted_outcome'])}.",
        f"Assigned model records: {record['consensus']['assigned']}."]
    result=t.get('result')
    lines += [f"Actual result: {cell(result.get('label'))}; public date: {cell(result.get('first_public_at'))}." if result else 'Actual result: Awaiting results. No correctness score is available.']
    if result:
        lines += [cell(result.get('verification_note')),f"Official result source: {cell(result.get('source_url'))}"]
    if historical:lines += ['Historical replay does not earn prospective hits; no accuracy claim is made.']
    lines += ['','Public sources:']
    for source in record['sources']:
        lines.append(f"- {cell(source.get('title'))}: {cell(source.get('source_url'))}")
    return ('\n'.join(lines)+'\n').encode()


def bundle(index, fetch=fetch_public):
    check_public(index)
    cohort=index.get('current_cohort_id')
    formal=[t for t in index['targets'] if t['cohort_id']==cohort and t.get('execution_status') not in ('withdrawn_before_execution','withdrawn','superseded')]
    historical=[t for t in index['targets'] if t.get('record_kind')=='historical_replay']
    if not cohort or not formal:raise ValueError('A current formal cohort is required')
    selected=formal+historical
    if len({t['id'] for t in selected})!=len(selected):raise ValueError('Duplicate target identities')
    files={};summary=[];formal_models=None
    for target in selected:
        ident=target['id']
        if not IDENTIFIER.fullmatch(ident):raise ValueError('Unsafe target identity')
        record=fetch(PREFIX+'/targets/'+ident+'/record')
        replay=validate_record(record,target)
        if not replay:
            models={r['model_id'] for r in record['runs']}
            if len(models)!=4 or (formal_models is not None and models!=formal_models):
                raise ValueError('The formal season must compare the same four models for every target')
            formal_models=models
        files['records/'+ident+'.json']=canonical(record)
        files['reports/'+ident+'.md']=report_text(record)
        summary.append({'target_id':ident,'trial_name':target['trial_name'],'kind':'historical_replay' if replay else 'current_formal',
            'assigned_records':len(record['runs']),'page_url':BASE+'/arena?target='+quote(ident)})
    files['README.md']=('''# Predome Open Forecast — public launch bundle

One Trial. Multiple Models. One Clear Comparison.

This is an export of actual public clinical records from https://8.148.145.28/arena.
Each records/ file preserves the public API object; each reports/ file is a readable clinical summary of that same object.
Use the exact page URLs in manifest.json when sharing. Model versions, record times, official result links and missing responses remain visible.
Success probability and confidence are N/A because clinical calibration is not established.
Historical replays are separate from current formal predictions and do not earn prospective hits.
The snapshot creation time is an export time, not a model prediction time or an independent timestamp proof.
SHA-256 entries verify the ZIP's files; they do not prove clinical validity or third-party timestamp attestation.
This bundle does not include private data, prompts, tokens, cost, internal metrics, or admin access.
No marketing messages have been sent and no sales conversion or clinical performance is claimed.
''').encode()
    manifest={'schema_version':'predome-public-launch-bundle-1','exported_at_utc':datetime.now(timezone.utc).isoformat(),
        'source_origin':BASE,'cohort_id':cohort,'formal_trials':len(formal),'formal_assigned_records':sum(s['assigned_records'] for s in summary if s['kind']=='current_formal'),
        'historical_replays':len(historical),'models':sorted(formal_models or []),'provider_calls':0,'ledger_writes':0,
        'clinical_prediction_validated':False,'independent_timestamp_attestation':False,'records':summary,
        'files':{name:{'sha256':digest(raw),'bytes':len(raw)} for name,raw in sorted(files.items())}}
    files['manifest.json']=canonical(manifest)
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        for name,raw in sorted(files.items()):archive.writestr(name,raw)
    return buffer.getvalue(),manifest


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    if a.output.exists() or a.output.is_symlink():raise ValueError('Existing exports are never overwritten')
    raw,manifest=bundle(fetch_public(PREFIX))
    a.output.parent.mkdir(parents=True,exist_ok=True)
    fd=os.open(a.output,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'wb') as stream:stream.write(raw)
    print(json.dumps({'output':str(a.output.resolve()),'zip_sha256':digest(raw),
        'formal_trials':manifest['formal_trials'],'formal_assigned_records':manifest['formal_assigned_records'],
        'historical_replays':manifest['historical_replays'],'provider_calls':0,'ledger_writes':0}))


if __name__=='__main__':main()
