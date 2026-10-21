"""New version join; old dataset and benchmarks remain byte-for-byte frozen."""
import json,hashlib
from pathlib import Path
from predome_pilot import ROOT,FIELDS,audit_rows,write_new
from predome_prior import DATA,attach_priors

def descriptor(path):return {'path':str(path.relative_to(ROOT)),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}

def assemble():
    rows=json.loads((ROOT/'predome_data/historical_v2/dataset.json').read_text())
    queue=json.loads((ROOT/'predome_data/historical_v2/next_dataset_queue.json').read_text())['candidates']
    snaps={r['nct_id']:r for r in json.loads((DATA/'history_probe/snapshot_features.json').read_text())}
    review_path=DATA/'review_v1.json'; reviews={r['nct_id']:r for r in json.loads(review_path.read_text())['reviews']}
    for c in queue:
        nct=c['nct_id']; s=snaps[nct]; v=reviews[nct]; f={k:s['features'].get(k) for k in FIELDS}
        f['phase']=s['features']['phase_numeric']
        for k in ['randomized','placebo_controlled']:
            if type(f[k]) is bool:f[k]=int(f[k])
        rows.append({'record_id':'expanded-'+nct,'nct_id':nct,'trial_name':c['trial_name'],'asset_group':c['experimental_asset_group'],'label':v['label'],'label_scope':'declared_primary_analysis','label_target':v['efficacy_label_scope'],'snapshot_target_match_status':v['snapshot_target_match_status'],'first_result_at':v['first_result_at'],'earliest_result_status':'verified','features_as_of':s['features_as_of'],'features':f,'feature_evidence':s['feature_evidence'],'feature_source':s['feature_source'],'label_source':v['reviewed_source'],'review_source':descriptor(review_path),'review_status':'source_curated_agent_checked','human_review_count':0,'formal_training_eligible':False,'historical_primary_outcomes':s['historical_primary_outcomes'],'selection':'outcome_selected_negative_addition','review_notes':v['notes']})
    packets=[json.loads((DATA/'prior_evidence'/p).read_text()) for p in ['prior_effect_records.json','train_prior_records.json','additional_prior_records.json']]
    records=[r for p in packets for r in p['records']]
    selection=json.loads((DATA/'selection_review.json').read_text())
    selections={r['target_nct_id']:r for r in selection['reviews']}
    # Unknown/unaccepted rows stay in the audit queue; they do not receive invented priors.
    rows=attach_priors(rows,records,selections)
    good,bad=audit_rows(rows)
    write_new(DATA/'dataset.json',rows)
    write_new(DATA/'prior_records.json',{'records':records,'selection_review_source':descriptor(DATA/'selection_review.json')})
    print(json.dumps({'rows':len(rows),'accepted':len(good),'rejected':bad},ensure_ascii=False))
if __name__=='__main__':assemble()
