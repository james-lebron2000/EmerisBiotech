from pathlib import Path
from datetime import datetime,timezone
import urllib.request,json,hashlib
ROOT=Path(__file__).resolve().parent;DATA=ROOT/'predome_data'
def sha(x):return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()
def write(p,x):
 p.parent.mkdir(exist_ok=True,parents=True)
 with p.open('x') as f:json.dump(x,f,ensure_ascii=False,indent=2,allow_nan=False)
def now():return datetime.now(timezone.utc).isoformat()
def main():
 catalog=json.loads((ROOT/'trial_data/candidates.json').read_text());sources=[];cases=[]
 for c in catalog['candidates']:
  url='https://clinicaltrials.gov/api/v2/studies/'+c['nct_id'];raw=urllib.request.urlopen(url,timeout=25).read();j=json.loads(raw);p=j['protocolSection']
  fname=c['nct_id']+'.json';(DATA/'sources'/fname).write_bytes(raw)
  status=p['statusModule'];sources.append({'id':c['nct_id'],'url':url,'retrieved_at_utc':now(),'sha256':hashlib.sha256(raw).hexdigest(),'local_file':'sources/'+fname})
  case={'id':c['id'],'nct_id':c['nct_id'],'trial_name':c['trial_name'],'asset':c['asset'],'sponsor':c['sponsor'],'indication':c['indication'],'phase':c['phase'],'registry_has_results':j.get('hasResults'), 'registry_status':status['overallStatus'],'registry_last_update':status['lastUpdatePostDateStruct'],'primary_completion':status['primaryCompletionDateStruct'],'enrollment':p['designModule']['enrollmentInfo'],'arms':p['armsInterventionsModule']['armGroups'],'primary_endpoints':p['outcomesModule']['primaryOutcomes'],'readout_window':c['readout_window'],'historical_evidence':c.get('prior_evidence',[]),'source_ids':c['source_ids'],'current_target_result_found':False,'result_search_scope':'Company/partner official releases and targeted primary-source search reviewed 2026-10-16 by readout_evidence agent; dynamic indexes may be incomplete. hasResults=false alone is insufficient.','model_support':'conditional_high_dose_only' if c['id'].startswith('topaz') else 'existing_frozen_v1' if c['id']=='pacific-9' else 'abstain_no_same_indication_early_effect'}
  cases.append(case)
 # Independent raw registry confirmation of historical numerator/denominator.
 url='https://clinicaltrials.gov/api/v2/studies/NCT02847598';raw=urllib.request.urlopen(url,timeout=25).read();(DATA/'sources/NCT02847598.json').write_bytes(raw)
 sources.append({'id':'LILAC_registry','url':url,'retrieved_at_utc':now(),'sha256':hashlib.sha256(raw).hexdigest(),'local_file':'sources/NCT02847598.json'})
 official=[
 {'id':'lilac_sle','url':'https://www.nejm.org/doi/full/10.1056/NEJMoa2118025','published_at':'2022-09-08','locator':'Table 2, Week 24 SRI-4 MITT; 36/64 versus 16/56; unadjusted secondary endpoint'},
 {'id':'topaz_design','url':'https://investors.biogen.com/static-files/46900c9d-795d-4963-9431-728f4f846252','published_at':'2025-09-03','locator':'PDF p47: high/low/placebo 1:1:1, Week52 SRI4; exact high dose mg not disclosed'},
 {'id':'biogen_q2_2026','url':'https://investors.biogen.com/static-files/1c72f31a-766b-4c0d-a65a-ee8838bfd4a2','published_at':'2026-07-29','locator':'PDF p2: SLE registrational data by year-end 2026'},
 {'id':'innate_h1_2026','url':'https://www.innate-pharma.com/media/all-press-releases/innate-pharma-reports-first-half-2026-business-update-and-financial-results','published_at':'2026-09-17','locator':'PACIFIC9 H2/year-end 2026 readout'},
 {'id':'takeda_rd','url':'https://www.sec.gov/Archives/edgar/data/1395064/000139506424000172/a2024-rd_day.htm','published_at':'2024-12-12','locator':'TAK279 CD 1:1:1:1, UC 1:1:1; FY2026 through 2027-03-31; current enrollment from registry'}]
 frozen=json.loads((ROOT/'trial_data/forecasts/pacific9-20261010-v1.json').read_text())
 # Input layer excludes all existing PACIFIC9 forecast probabilities and media forecasts.
 packet={'schema_version':'predome-evidence-v1','created_at_utc':now(),'forecast_horizon':['2026-10-16','2027-04-16'],'target':'First official prespecified primary endpoint statistical success; high-dose comparison only for TOPAZ','cases':cases,'sources':sources+official,'notes':['Only factual prior evidence and trial design are supplied to LLMs. No existing forecast probabilities, no other-model outputs.','Enrollment count is not event count.','TOPAZ high dose cannot be assumed identical to LILAC450mg.','All official success labels remain pending; no private patient data supplied.']}
 write(DATA/'evidence_packet_v1.json',packet)
 spec={'schema_version':'predome-stat-spec-v1','selected_at_utc':now(),'selection_stage':'fixed_before_first_new_forecast_calculation','model_version':'topaz-joint-binomial-v1','source_trial':'NCT02847598','historical_response_counts':{'treatment_responders':36,'treatment_n':64,'control_responders':16,'control_n':56},'prior_control_beta':[.5,.5],'prior_log_odds_ratio':{'mean':0.,'sd':.5},'joint_posterior_grid':{'control_logit_min':-6.,'control_logit_max':3.,'effect_min':-3.,'effect_max':4.,'step':.05},'effect_retention':.75,'transport_log_or_sd':.5,'control_logit_drift_sd':.5,'central_two_sided_alpha_proxy':.025,'allocation':{'treatment':1/3,'control':1/3,'rounding':'floor(total_enrollment/3); actual analyzable per-arm numbers unknown'},'central_analyzable_fraction':1.,'monte_carlo_draws':60000,'sensitivity_draws':12000,'seed':20261016,'sensitivity_grid':{'effect_retention':[.5,.75,1.],'transport_log_or_sd':[.25,.5,.75],'two_sided_alpha_proxy':[.025,.05],'analyzable_fraction':[.85,1.]},'threshold_classification':.5,'evidence_packet_sha256':sha(packet),'clinical_prediction_validated':False,'parameters_learned_from_52_week_data':False,'target':'official favorable high-dose vs placebo primary Week52 SRI4 superiority at first eligible prespecified primary analysis','statistical_proxy':'Wald log-OR normal approximation with anticipated binomial sampling variance; incomplete SAP, assumed alpha allocation; not actual CMH/missingness/multiplicity implementation','limitations':['Dose/exposure mapping from historical450mg to target high-dose is unconfirmed.','24-week secondary MITT endpoint to52-week ITT primary, baseline therapy and population differ.','Retentions/transport/control drift are prespecified research assumptions, not learned or calibrated.','Low-dose not forecast; no trial-level overall success probability.','Same historical evidence underlies both TOPAZ; never multiply probabilities.']}
 write(DATA/'stat_spec_v1.json',spec)
 write(DATA/'selection_receipt.json',{'selected_at_utc':now(),'evidence_packet_sha256':sha(packet),'stat_spec_sha256':sha(spec),'numeric_results_generated':False,'independent_timestamp_verified':False})
 print('Frozen evidence and statistical recipe before calculation:',sha(spec))
if __name__=='__main__':main()
