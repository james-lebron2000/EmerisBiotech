"""Freeze actual, reviewed R2 calls; keep R1 and abstentions, never cherry-pick."""
from pathlib import Path
from datetime import datetime,timezone
import json
from continuous_audit import ROOT,DATA,digest,validate_descriptor
from predome_pilot import sha,write_new

def freeze():
    base=DATA/'prospective';out=base/'frozen_forecasts_v1.json'
    if out.exists():raise FileExistsError('Never overwrite frozen forecasts')
    packet=json.loads((base/'candidate_packets.json').read_text())
    mapping=json.loads((base/'runs/forward-v1/target_mapping.json').read_text())
    inputs=json.loads((base/'runs/forward-v1/inputs.json').read_text())
    review=json.loads((base/'reviews/repair_llm_review.json').read_text())
    candidates={c['nct_id']:c for c in packet['candidates']};runs={};artifacts=[];cache={}
    paths=['candidate_packets.json','prior_effect_records.json','source_manifest.json','negative_search_receipts.json',
           'runs/forward-v1/inputs.json','runs/forward-v1/target_mapping.json','reviews/llm_review.json','reviews/repair_llm_review.json',
           'runs/repair-v1/parentage.json']
    for model in ['deepseek-flash','deepseek-v4-pro']:
        for folder in ['forward-v1','repair-v1']:paths.append('runs/'+folder+'/'+model+'.json')
        rr=next(r for r in review['models'] if r['model']==model)
        if rr['remaining_major_findings']!=0:raise ValueError('Material factuality issue requires publication veto')
        path=base/rr['output_file']
        if digest(path)!=rr['output_sha256']:raise ValueError('Reviewed output hash changed')
        record=json.loads(path.read_text())
        if sha(record['record'])!=record['sha256'] or record['record']['status']!='completed':raise ValueError('Invalid actual run')
        runs[model]={r['case_id']:r for r in record['record']['forecasts']}
        if set(runs[model])!={m['case_id'] for m in mapping}:raise ValueError('Target coverage mismatch')
        paths.append(rr['input_file_used'])
    for path in paths:
        p=base/path;artifacts.append({'path':str(p.relative_to(ROOT)),'sha256':digest(p)})
    for c in packet['candidates']:
        for key in ['registry_source','guidance_source']:
            validate_descriptor(c[key],cache)
            artifacts.append({k:c[key][k] for k in ['path','sha256']})
    cases=[]
    for m in mapping:
        c=candidates[m['nct_id']];inp=next(i for i in inputs['cases'] if i['case_id']==m['case_id'])
        cases.append(m|{'asset':c['asset_group'],'company':c['company'],'readout_window':c['guidance_window']['label'],
            'readout_window_bounds':c['guidance_window'],'status':'frozen_pending_real_outcome','target_success_rule':m['success_event'],
            'forecasts':{model:runs[model][m['case_id']] for model in runs},
            'statistical_assurance':{'status':'abstain','probability':None,'reason':'Only indirect B-tier priors; target alpha/precision or NI rules not jointly verified.'},
            'unknowns':c['unknown_target_inputs'],'sources':[c['registry_source'],c['guidance_source']],
            'minor_review_annotations':{'forward-007':['IRIS强化期时长需限定；CRR证据限定CRR0.5，不能泛化为所有CRR定义。']}.get(m['case_id'],[]),
            'probabilities_uncalibrated':True,'prior_evidence_tiers':[p['evidence_class'] for p in inp['prior_evidence']]})
    record={'id':'predome-prospective-20261019-v1','frozen_at_utc':datetime.now(timezone.utc).isoformat(),
            'evidence_as_of_utc':packet['as_of_utc'],'trial_count':len(candidates),'target_count':len(cases),
            'clinical_prediction_validated':False,'source_packet_sha256':digest(base/'candidate_packets.json'),
            'cases':cases,'artifact_manifest':list({p['path']:p for p in artifacts}.values()),
            'publication_policy':'Publish both R2 models regardless probability; retain R1, one factuality repair, source/target versions and all abstentions. Never revise an old forecast after outcomes.',
            'evidence_boundary':'Local UTC+SHA freeze, not external trusted timestamp or independent clinical validation. Public exposure receipt must be added separately.'}
    write_new(out,{'record':record,'sha256':sha(record)})
    print(json.dumps({'file':str(out.relative_to(ROOT)),'sha256':sha(record),'trials':len(candidates),'targets':len(cases)}))

if __name__=='__main__':freeze()
