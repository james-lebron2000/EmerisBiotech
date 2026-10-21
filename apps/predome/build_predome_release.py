from pathlib import Path
from datetime import datetime,timezone
import hashlib,json
from emeris_live.predome_statistics import sha
ROOT=Path(__file__).resolve().parent;D=ROOT/'predome_data'
def build():
 packet=json.loads((D/'evidence_packet_v1.json').read_text());stat=json.loads((D/'runs/statistical-topaz-v1.json').read_text());assert sha(stat['record'])==stat['sha256']
 llm=[]
 for p in sorted((D/'runs').glob('deepseek-*.json')):
  e=json.loads(p.read_text());assert sha(e['record'])==e['sha256'];assert e['record']['evidence_packet_sha256']==sha(packet);llm.append(e)
 # A distinct, human/agent review layer preserves raw wording, including numerical conflation.
 review={'reviewed_at_utc':datetime.now(timezone.utc).isoformat(),'type':'agent_assisted_source_review_not_medical_validation','issues':[{'model':'deepseek-v4-pro','comparison_ids':['topaz-1:high-dose','topaz-2:high-dose'],'issue':'Narrative cites adjusted26.4pp alongside raw36/64vs16/56 (raw27.68pp). These are different estimators; do not use the adjusted value with raw-count variance.'},{'model':'both','comparison_ids':['pacific-9:oleclumab','pacific-9:monalizumab','topaz-1:high-dose','topaz-2:high-dose'],'issue':'Statements that larger/blinded trials shrink effects are research hypotheses, not a fitted transport relationship.'}],'raw_predictions_modified':False,'fused_probability':None}
 old=json.loads((ROOT/'trial_data/forecasts/pacific9-20261010-v1.json').read_text());f=old['forecast'];rows=[]
 for c in f['comparisons']:rows.append({'comparison_id':'pacific-9:'+c['arm_id'],'label':'PACIFIC-9 '+c['arm_id'],'nct_id':f['trial_id'],'statistical_probability':c['statistical_success_probability'],'statistical_type':'既有2026-10-10固定模型研究预测','sensitivity_range':None,'statistical_record_id':old['id']})
 for c in stat['record']['payload']['comparisons']:rows.append({'comparison_id':c['comparison_id'],'label':c['case_id'].upper()+' high-dose','nct_id':c['nct_id'],'statistical_probability':c['central']['statistical_success_proxy_probability'],'statistical_type':'新计算 · 高剂量条件情景 · 非完整SAP','sensitivity_range':c['sensitivity_range_not_ci'],'statistical_record_id':stat['record']['id']})
 for c in packet['cases']:
  if c['id'].startswith('tak279'):rows.append({'comparison_id':c['id']+':trial','label':c['trial_name'],'nct_id':c['nct_id'],'statistical_probability':None,'statistical_type':'拒答：缺同适应症早期药效及设计信息','sensitivity_range':None,'statistical_record_id':None})
 for r in rows:
  r['llm']={}
  for e in llm:
   model=e['record']['requested_model'];answer=next((x for x in (e['record'].get('result') or {}).get('forecasts',[]) if x['comparison_id']==r['comparison_id']),None)
   r['llm'][model]=answer
  r['actual_result']='pending_not_adjudicated';r['fused_probability']=None
 training=json.loads((D/'training_audit_v1/audit.json').read_text())
 sources={s['id']:s for s in json.loads((ROOT/'trial_data/candidates.json').read_text())['sources']};sources.update({s['id']:s for s in packet['sources']})
 release={'schema_version':'predome-release-v1','created_at_utc':datetime.now(timezone.utc).isoformat(),'product_name':'Predome Agent System','evidence_packet_sha256':sha(packet),'cases':packet['cases'],'comparisons':rows,'statistical_record':stat,'llm_records':llm,'llm_review':review,'training':training,'source_links':list(sources.values()),'scope':{'therapeutic_areas':'all; selected evidence-qualified candidates only','horizon':['2026-10-16','2027-04-16'],'independent_target_trials_in_batch':5,'comparisons_or_trial_targets':6,'historical_topaz_efficacy_trials':1,'providers_called':['deepseek'],'other_providers':'paused_by_user','model_calls':len(llm),'clinical_results_adjudicated':0,'clinical_prediction_validated':False,'supervised_training_completed':False,'independent_timestamp_verified':False},'cost_upper_estimate_usd':sum(e['record'].get('peak_cache_miss_cost_upper_estimate_usd',0) for e in llm),'publication_verified':False,'public_mutation_allowed':False,'notice':'Statistics and LLM subjective probabilities are different, uncalibrated research outputs. No majority vote or combined success probability. Exact high dose, SAP, migration and missingness assumptions incomplete.'}
 envelope={'record':release,'sha256':sha(release)}
 with (D/'release_v1.json').open('x') as out:json.dump(envelope,out,ensure_ascii=False,indent=2,allow_nan=False)
 return envelope
if __name__=='__main__':e=build();print(e['sha256'])
