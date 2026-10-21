"""Resume only the uncalled Pro repair after local ENOSPC; never overwrites raw or Flash runs."""
import json,hashlib
from predome_pilot import sha,write_new
import continuous_forecast_llm as base
from continuous_forecast_repair import REPAIR_SYSTEM_SUFFIX,DATA,ORIGINAL,REPAIR

def main():
    packet=json.loads((ORIGINAL/'inputs.json').read_text());review=json.loads((DATA/'prospective/reviews/llm_review.json').read_text());model='deepseek-v4-pro'
    assert not (REPAIR/(model+'.json')).exists(),'Actual Pro receipt already exists; do not repeat call'
    reviewed=next(m for m in review['models'] if m['model']==model)
    feedback=[{'case_id':r['case_id'],'reasoning_issues':r.get('reasoning_issues',[]),'claim_reviews':r.get('claim_reviews',[]),'assumption_reviews':r.get('assumption_reviews',[])} for r in reviewed['case_reviews']]
    inp=packet|{'repair_feedback_own_model_only':feedback,'repair_rule':'One round only, before publication and outcomes. No peer predictions or labels. Original outputs preserved.'}
    write_new(REPAIR/(model+'-inputs-recovered.json'),inp)
    base.RUN=REPAIR;base.SYSTEM+=REPAIR_SYSTEM_SUFFIX
    r=base.call(model,inp)
    print(json.dumps({k:r.get(k) for k in ['model_requested','status','usage','cost_upper_estimate_usd']},ensure_ascii=False),flush=True)
    parents=[]
    for name in ['deepseek-flash','deepseek-v4-pro']:
        e=json.loads((REPAIR/(name+'.json')).read_text())
        parents.append({'model':name,'parent_record_file_sha256':hashlib.sha256((ORIGINAL/(name+'.json')).read_bytes()).hexdigest(),'repair_record_sha256':e['sha256'],'status':e['record']['status']})
    write_new(REPAIR/'parentage.json',{'round':2,'max_repair_rounds':1,'raw_round_preserved':True,'peer_outputs_visible':False,'historical_test_labels_visible':False,'io_interruption':'First repair process stopped before Pro API call due ENOSPC while creating Pro input file. Only the uncalled Pro repair resumed; Flash not repeated.','parents':parents})
if __name__=='__main__':main()
