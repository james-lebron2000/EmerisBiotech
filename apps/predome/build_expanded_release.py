"""Immutable v3 research release. Verify before publishing; production never unpickles."""
from predome_pilot import ROOT,sha,write_new,audit_rows,partition
from predome_prior import DATA,validate_prior,effect_features
from predome_expanded import contract
from datetime import datetime,timezone
from pathlib import Path
import hashlib,json

def build():
    rows=json.loads((DATA/'dataset.json').read_text());run=DATA/'runs/expanded-v1'; report=json.loads((run/'report.json').read_text());comparison=json.loads((run/'comparison.json').read_text())
    assert sha(rows)==report['dataset_sha256'];assert sha(contract())==report['protocol_sha256']
    accepted,rejected=audit_rows(rows);train,test,_=partition(accepted)
    assert len(accepted)==report['accepted'];assert len(train)==report['counts']['train']
    for r in accepted:
        if r.get('selected_prior_record'):
            assert validate_prior(r['selected_prior_record'],r['nct_id'],r['forecast_as_of'])[0]
            assert effect_features(r['selected_prior_record'])==r['selected_prior_math']
    calls=[]
    for name in ['deepseek-flash','deepseek-v4-pro']:
        e=json.loads((run/(name+'.json')).read_text());assert sha(e['record'])==e['sha256'];calls.append(e)
    paths={p for p in DATA.rglob('*') if p.is_file() and p.name!='release.json' and '__pycache__' not in p.parts and 'stream_cache' not in p.parts}
    for r in rows:
        for k in ['feature_source','label_source','review_source']:
            if r.get(k,{}).get('path'):paths.add(ROOT/r[k]['path'])
    paths.update(ROOT/p for p in ['predome_prior.py','predome_expanded.py','predome_expanded_llm.py','assemble_expanded_dataset.py','build_expanded_release.py','write_expanded_report.py','docs/Predome_扩展训练与比较_20261017.md'])
    selected_counts={s:sum(bool(r.get('features_as_of')) for r in rows) for s in ['snapshot_verified_rows']}
    record={'id':'predome-expanded-20261017-v1','created_at_utc':datetime.now(timezone.utc).isoformat(),'protocol':contract(),'data_counts':{'candidate_rows':len(rows),'snapshot_verified_rows':selected_counts['snapshot_verified_rows'],'independent_agent_accepted_rows':len(accepted),'human_double_adjudicated_rows':0,'train':len(train),'test':len(test),'new_negative_rows':6,'new_independent_test_rows':0},'dataset_rows':rows,'prior_evidence_records':json.loads((DATA/'prior_records.json').read_text())['records'],'benchmark':report,'llm_comparison':comparison,'coverage_audit':json.loads((run/'coverage_audit.json').read_text()),'reasoning_review':json.loads((DATA/'independent_output_review.json').read_text()),'llm_runs':calls,'formal_training_gate':{'status':'not_met','minimums':{'train':200,'calibration':50,'test':50},'human_double_review_required':True},'clinical_prediction_validated':False,'frozen_prior_releases_unchanged':True,'artifact_manifest':[{'path':str(p.relative_to(ROOT)),'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'bytes':p.stat().st_size} for p in sorted(paths)]}
    write_new(DATA/'release.json',{'record':record,'sha256':sha(record)})
    print(json.dumps({'sha256':sha(record),'data_counts':record['data_counts'],'artifacts':len(paths)},ensure_ascii=False))
if __name__=='__main__':build()
