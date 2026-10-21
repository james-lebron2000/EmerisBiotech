"""Validated pre-cutoff prior RCT effects. No target-label access in selection."""
from datetime import date, timedelta
from statistics import NormalDist
import math, json, hashlib
from pathlib import Path
from predome_pilot import ROOT, source_ok
DATA = ROOT/'predome_data/historical_v3'
RATIOS = {'HR', 'OR', 'rate_ratio', 'risk_ratio'}

def effect_features(record):
    metric = record['effect_measure']; value = float(record['effect_estimate'])
    lo, hi = map(float, record['confidence_interval']); level = record['confidence_level']
    if level not in [.90,.95] or not lo < hi or not lo <= value <= hi:
        raise ValueError('Invalid reported effect or CI')
    zcrit = NormalDist().inv_cdf((1+level)/2)
    if metric in RATIOS:
        if lo <= 0: raise ValueError('Ratios must be positive')
        theta = math.log(value); se = (math.log(hi)-math.log(lo))/(2*zcrit)
    elif metric in {'mean_difference','risk_difference'}:
        theta = value; se = (hi-lo)/(2*zcrit)
    else: raise ValueError('Unsupported effect scale')
    direction = -1 if record['lower_effect_favors_experimental'] else 1
    return {'prior_benefit_z': direction*theta/se, 'prior_logratio_se': se if metric in RATIOS else None,
            'effect_theta':theta,'derived_se':se,'critical_value':zcrit,'se_approximate_from_rounded_CI':True}

def validate_prior(record, target, forecast_as_of):
    source = record.get('source',{})
    desc = {'path':source.get('local_path'),'sha256':source.get('sha256')}
    if not source_ok(desc): raise ValueError('Prior source hash mismatch: '+record['record_id'])
    if source['publication_date'] > forecast_as_of: return False, 'source_after_forecast_cutoff'
    if record.get('prior_nct_id') == target: return False, 'same_target_result'
    apps = [a for a in record.get('applicability',[]) if a['target_nct_id']==target]
    if len(apps)!=1: return False,'target_not_reviewed'
    a=apps[0]
    if not record.get('randomized') or a['evidence_grade'][0] not in ['A','B'] or not a.get('eligible_as_indirect_numeric_feature_candidate'):
        return False,'ineligible_tier_or_design'
    effect_features(record)
    return True,'eligible_indirect' if a['evidence_grade'][0]=='B' else 'eligible_direct'

def attach_priors(rows, records, selections):
    byid={r['record_id']:r for r in records}; out=[]
    for row in rows:
        r=dict(row); cutoff=(date.fromisoformat(r['first_result_at'])-timedelta(days=7)).isoformat()
        r['forecast_as_of']=cutoff; r['registry_snapshot_as_of']=r['features_as_of']
        r['prior_features']={k:None for k in ['prior_benefit_z','prior_logratio_se','prior_target_endpoint_match','prior_population_match','prior_comparator_match','prior_time_match','prior_metric','prior_tier']}
        sel=selections.get(r['nct_id']); r['prior_selection']=sel
        r['prior_evidence_status']='no_eligible_randomized_effect_located_in_current_search'
        r['prior_candidates_audit']=[]
        for prior in records:
            if any(a['target_nct_id']==r['nct_id'] for a in prior.get('applicability',[])):
                eligible,why=validate_prior(prior,r['nct_id'],cutoff)
                r['prior_candidates_audit'].append({'record_id':prior['record_id'],'eligible':eligible,'reason':why})
        if sel and sel.get('record_id'):
            prior=byid[sel['record_id']]; valid,why=validate_prior(prior,r['nct_id'],cutoff)
            if not valid: raise ValueError('Selected prior inadmissible: '+r['nct_id']+' '+why)
            math_features=effect_features(prior)
            a=next(a for a in prior['applicability'] if a['target_nct_id']==r['nct_id'])
            r['prior_features'].update({k:math_features[k] for k in ['prior_benefit_z','prior_logratio_se']})
            for flag in ['target_endpoint','population','comparator','time']:
                value=sel.get(flag+'_match'); r['prior_features']['prior_'+flag+'_match']=int(value) if type(value) is bool else None
            r['prior_features'].update(prior_metric=prior['effect_measure'],prior_tier=a['evidence_grade'][0])
            r['selected_prior_record']=prior; r['selected_prior_math']=math_features; r['prior_evidence_status']=why
        r['assurance']={'status':'abstain','probability':None,'reason':'No jointly verified directly compatible A-tier RCT and target precision/alpha/hypothesis; no n-only precision guess.'}
        out.append(r)
    return out
