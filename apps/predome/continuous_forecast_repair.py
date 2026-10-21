"""One fixed prepublication factuality repair round. No peer probabilities or outcome labels.
Original round remains immutable; publication gating is factuality, never prediction direction.
"""
import json,hashlib
from pathlib import Path
from predome_pilot import sha,write_new
import continuous_forecast_llm as base
DATA=base.DATA;ORIGINAL=base.RUN;REPAIR=DATA/'prospective/runs/repair-v1'
REPAIR_SYSTEM_SUFFIX='''\nThis is the single prepublication factuality repair round. The appended feedback concerns only your own previous explanation, not correctness of forecast direction or another model. Re-evaluate from the same original evidence. Repair unsupported facts; unknown must stay unknown. Do not assume a hoped-for significant result as a supporting premise. OCEAN is known noninferiority: margin unknown, not comparison type. AVANZAR OS are primary, order/alpha and maturity unknown. BEYOND is104-week eGFR change, not slope. Do not treat not-reported-in-window as ultimate failure. No new evidence or target outcomes have been added.'''
def main():
 REPAIR.mkdir(parents=True,exist_ok=False);packet=json.loads((ORIGINAL/'inputs.json').read_text());review=json.loads((DATA/'prospective/reviews/llm_review.json').read_text());parents=[]
 base.RUN=REPAIR;base.SYSTEM+=REPAIR_SYSTEM_SUFFIX
 for model in ['deepseek-flash','deepseek-v4-pro']:
  reviewed=next(m for m in review['models'] if m['model']==model)
  feedback=[{'case_id':r['case_id'],'reasoning_issues':r.get('reasoning_issues',[]),'claim_reviews':r.get('claim_reviews',[]),'assumption_reviews':r.get('assumption_reviews',[])} for r in reviewed['case_reviews']]
  repaired_input=packet|{'repair_feedback_own_model_only':feedback,'repair_rule':'One round only, before publication and outcomes. No peer predictions or labels. Original outputs preserved.'}
  write_new(REPAIR/(model+'-inputs.json'),repaired_input)
  rec=base.call(model,repaired_input)
  parents.append({'model':model,'parent_record_file_sha256':hashlib.sha256((ORIGINAL/(model+'.json')).read_bytes()).hexdigest(),'review_file_sha256':hashlib.sha256((DATA/'prospective/reviews/llm_review.json').read_bytes()).hexdigest(),'inputs_sha256':sha(repaired_input),'output_sha256':sha(rec),'status':rec['status']})
  print(json.dumps({k:rec.get(k) for k in ['model_requested','status','usage','cost_upper_estimate_usd']},ensure_ascii=False),flush=True)
 write_new(REPAIR/'parentage.json',{'round':2,'max_repair_rounds':1,'raw_round_preserved':True,'peer_outputs_visible':False,'historical_test_labels_visible':False,'parents':parents})
if __name__=='__main__':main()
