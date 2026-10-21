"""Joint binomial historical update and explicitly conditional TOPAZ forecasts.
No external data fetching, no parameter fitting to target outcomes, no LLM needed.
"""
import bisect,hashlib,json,math,random,statistics
from statistics import NormalDist
NORM=NormalDist()
def sha(x):return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()
def logistic(x):return 1/(1+math.exp(-x))
def posterior_grid(spec):
 c=spec['historical_response_counts'];g=spec['joint_posterior_grid'];step=g['step'];points=[];logs=[]
 if not 0<c['treatment_responders']<c['treatment_n'] or not 0<c['control_responders']<c['control_n']:raise ValueError('Require valid non-boundary historical counts')
 if spec['prior_control_beta']!=[.5,.5] or spec['prior_log_odds_ratio']['mean']!=0:raise ValueError('Unsupported prior recipe')
 sd=spec['prior_log_odds_ratio']['sd']
 for i in range(round((g['control_logit_max']-g['control_logit_min'])/step)+1):
  b=g['control_logit_min']+i*step;p0=logistic(b)
  for j in range(round((g['effect_max']-g['effect_min'])/step)+1):
   delta=g['effect_min']+j*step;p1=logistic(b+delta)
   # Jeffreys Beta(.5,.5) prior on p0, including logit-coordinate Jacobian.
   ll=(c['control_responders']+.5)*math.log(p0)+(c['control_n']-c['control_responders']+.5)*math.log1p(-p0)
   ll+=c['treatment_responders']*math.log(p1)+(c['treatment_n']-c['treatment_responders'])*math.log1p(-p1)-.5*(delta/sd)**2
   points.append((b,delta));logs.append(ll)
 top=max(logs);weights=[math.exp(x-top) for x in logs];total=sum(weights);weights=[x/total for x in weights]
 cumulative=[];running=0.
 for w in weights:running+=w;cumulative.append(running)
 cumulative[-1]=1.
 edge=sum(w for (b,d),w in zip(points,weights) if b<g['control_logit_min']+step*1.5 or b>g['control_logit_max']-step*1.5 or d<g['effect_min']+step*1.5 or d>g['effect_max']-step*1.5)
 if edge>.005:raise ValueError('Posterior grid truncation too large')
 return points,weights,cumulative,edge

def scenario(draws,enrollment,retention,transport,drift,alpha,fraction):
 if enrollment<60 or not 0<alpha<.2 or not 0<fraction<=1 or not 0<=retention<=1 or transport<0 or drift<0:raise ValueError('Invalid design scenario')
 n=math.floor(enrollment/3*fraction);direction=[];success=[];observed=[];risks=[];crit=NORM.inv_cdf(1-alpha/2)
 for b,d,z1,z2,z3 in draws:
  p0=logistic(b+drift*z1);effect=retention*d+transport*z2;p1=logistic(b+drift*z1+effect)
  variance=1/(n*p0*(1-p0))+1/(n*p1*(1-p1));se=math.sqrt(variance)
  direction.append(NORM.cdf(effect/se));success.append(NORM.cdf(effect/se-crit));observed.append(effect+se*z3);risks.append(p1-p0)
 k=len(draws);prob=statistics.fmean(success)
 observed.sort();risks.sort()
 quant=lambda v,q:v[min(k-1,round(q*(k-1)))]
 return {'assumptions':{'effect_retention':retention,'transport_log_or_sd':transport,'control_logit_drift_sd':drift,'two_sided_alpha_proxy':alpha,'analyzable_fraction':fraction,'assumed_per_comparison_arm_n':n},'direction_probability':statistics.fmean(direction),'statistical_success_proxy_probability':prob,'mc_standard_error':statistics.stdev(success)/math.sqrt(k),'future_reported_or_prediction_interval_95':[math.exp(quant(observed,.025)),math.exp(quant(observed,.975))],'latent_risk_difference_interval_95':[quant(risks,.025),quant(risks,.975)]}

def calculate(packet,spec):
 if sha(packet)!=spec['evidence_packet_sha256']:raise ValueError('Evidence hash mismatch')
 points,weights,cumulative,edge=posterior_grid(spec);rng=random.Random(spec['seed']);draws=[]
 for _ in range(spec['monte_carlo_draws']):
  b,d=points[bisect.bisect_left(cumulative,rng.random())];draws.append((b,d,rng.gauss(0,1),rng.gauss(0,1),rng.gauss(0,1)))
 outputs=[]
 for case in packet['cases']:
  if not case['id'].startswith('topaz'):continue
  if case['registry_has_results'] is not False or case['current_target_result_found']:raise ValueError('Already reported target cannot be prospective')
  n=case['enrollment']['count'];central=scenario(draws,n,spec['effect_retention'],spec['transport_log_or_sd'],spec['control_logit_drift_sd'],spec['central_two_sided_alpha_proxy'],spec['central_analyzable_fraction'])
  sensitivity=[];g=spec['sensitivity_grid']
  for retention in g['effect_retention']:
   for transport in g['transport_log_or_sd']:
    for alpha in g['two_sided_alpha_proxy']:
     for fraction in g['analyzable_fraction']:
      s=scenario(draws[:spec['sensitivity_draws']],n,retention,transport,spec['control_logit_drift_sd'],alpha,fraction);sensitivity.append({'assumptions':s['assumptions'],'probability':s['statistical_success_proxy_probability']})
  outputs.append({'case_id':case['id'],'nct_id':case['nct_id'],'comparison_id':case['id']+':high-dose','target':spec['target'],'status':'conditional_uncalibrated_research_forecast','clinical_prediction_validated':False,'central':central,'sensitivity':sensitivity,'sensitivity_range_not_ci':[min(s['probability'] for s in sensitivity),max(s['probability'] for s in sensitivity)],'classification':central['statistical_success_proxy_probability']>=spec['threshold_classification'],'low_dose_prediction':None,'trial_level_overall_success_probability':None,'actual_result':'pending','limitations':spec['limitations']})
 return {'model_version':spec['model_version'],'spec_sha256':sha(spec),'evidence_packet_sha256':sha(packet),'historical_independent_trials':1,'posterior_grid_edge_mass':edge,'posterior_log_or_mean':sum(w*d for (_,d),w in zip(points,weights)),'posterior_log_or_direction_probability':sum(w for (_,d),w in zip(points,weights) if d>0),'comparisons':outputs,'same_asset_joint_probability':None,'notice':'Conditional research approximation; full SAP, dose mapping and migration calibration unavailable. Not a trained cross-disease model.'}
