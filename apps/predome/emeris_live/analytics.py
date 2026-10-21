"""Auditable descriptive analytics; prospective records never mix with replay.

No model fitting or outcome retrieval occurs here. All archived comparisons use
exactly the same answered cases. Dates are grouped before cumulative scoring, so
same-day trial ordering cannot create a misleading trajectory.
"""
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from statistics import NormalDist

from .dashboard import build_dashboard
from .prospective_baselines import POLICY_SHA256

MODELS = ('deepseek-v4-pro', 'deepseek-flash')
MIN_SUBGROUP_N = 20
MIN_SUBGROUP_ASSETS = 5
BIN_EDGES = (0.0, .2, .4, .6, .8, 1.0)
BASELINES = ('beta_pooled', 'beta_area', 'constant_0_5', 'design_logistic')
PROSPECTIVE_BASELINE = 'fixed_0_5'
BASELINE_POLICY = 'fixed-0.5-no-features.v1'
TARGET_FIELDS = ('endpoint', 'population', 'comparison', 'success_definition')


def _prob(value):
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1


def _stamp(value):
    if not isinstance(value, str) or not value:
        raise ValueError('Missing timestamp')
    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def wilson(k, n):
    """95% Wilson interval for a descriptive binomial proportion, no continuity correction."""
    if not n:
        return None
    z = NormalDist().inv_cdf(.975)
    p = k / n
    denominator = 1 + z*z/n
    center = (p + z*z/(2*n)) / denominator
    half = z*math.sqrt(p*(1-p)/n + z*z/(4*n*n)) / denominator
    return [0.0 if k == 0 else max(0.0, center-half), 1.0 if k == n else min(1.0, center+half)]


def _metrics(rows):
    if not rows:
        return {'n': 0, 'brier': None, 'accuracy': None, 'accuracy_ci': None, 'log_loss': None, 'successes': 0, 'failures': 0}
    n = len(rows)
    correct = sum((r['probability'] >= .5) == bool(r['label']) for r in rows)
    return {'n': n, 'brier': sum((r['probability']-r['label'])**2 for r in rows)/n,
            'accuracy': correct/n, 'accuracy_ci': wilson(correct, n),
            'log_loss': -sum(r['label']*math.log(max(1e-15,r['probability']))+(1-r['label'])*math.log(max(1e-15,1-r['probability'])) for r in rows)/n,
            'successes': sum(r['label'] for r in rows), 'failures': sum(1-r['label'] for r in rows)}


def _asset_count(rows):
    return len({r['asset'] for r in rows if r.get('asset') and r['asset'] != 'unknown'})


def _retrospective(dashboard, model):
    rows = []
    for row in dashboard['historical']:
        prediction = row['forecasts'].get(model, {})
        value = prediction.get('probability')
        if value is not None and not _prob(value):
            raise ValueError('Invalid archived probability')
        if type(row['actual_label']) is not int or row['actual_label'] not in (0,1):
            raise ValueError('Invalid archived label')
        features = row.get('features', {})
        rows.append({'id': row['nct_id'], 'trial_id': row['nct_id'], 'trial_name': row['trial_name'],
                     'asset': row['asset'], 'endpoint': row['endpoint'], 'probability': value,
                     'label': row['actual_label'], 'model': model,
                     'prediction_status': prediction.get('status') or ('predicted' if _prob(value) else 'abstain'),
                     'date': row['result_date'], 'prediction_at': prediction.get('created_at_utc'),
                     'features_as_of': row['features_as_of'], 'source_url': row['label_source']['url'],
                     'scope': 'retrospective', 'eligible_for_prospective': False,
                     'features': {'phase': features.get('phase'), 'therapeutic_area': features.get('therapeutic_area'),
                                  'modality': features.get('modality'), 'design': 'randomized' if features.get('randomized') == 1 else 'unknown',
                                  'sponsor_category': None, 'prediction_horizon': 'retrospective_no_forward_horizon'},
                     'baselines': row.get('statistical_models', {})})
    return rows, []


def _model_key(raw):
    model = raw.get('model')
    version = raw.get('model_version')
    return f'{model} :: {version}' if version and version != model else model


def _digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def _baseline_audit(raw, target, full_target):
    """Defense in depth over authenticated PlatformStore evaluation rows.

    A raw numeric mapping is never proof of pre-readout registration. This pure
    aggregator does not authenticate arbitrary callers; only the ledger supplies
    integrity_verified after decoding and checking its immutable sidecar.
    """
    audits=raw.get('baseline_audits',[])
    matches=[a for a in audits if isinstance(a,dict) and a.get('baseline_key')==PROSPECTIVE_BASELINE] if isinstance(audits,list) else []
    if not matches:return {},{'baseline_key':PROSPECTIVE_BASELINE,'eligible':False,'exclusion_reasons':['baseline_not_registered']}
    if len(matches)!=1:return {},{'baseline_key':PROSPECTIVE_BASELINE,'eligible':False,'exclusion_reasons':['ambiguous_baseline_records']}
    audit=dict(matches[0]);reasons=list(audit.get('exclusion_reasons') or [])
    if reasons==['baseline_not_registered'] and audit.get('eligible') is False and not audit.get('baseline_id'):
        return {},audit
    if audit.get('eligible') is not True:reasons.append('baseline_ledger_ineligible')
    if audit.get('integrity_verified') is not True:reasons.append('baseline_integrity_unverified')
    if not full_target:reasons.append('baseline_missing_full_target')
    elif audit.get('target_sha256')!=_digest(target):reasons.append('baseline_target_mismatch')
    for field in ('trial_id','prediction_id','snapshot_id'):
        if not raw.get(field) or audit.get(field)!=raw.get(field):reasons.append('baseline_'+field+'_mismatch')
    if not raw.get('snapshot_sha256') or audit.get('snapshot_sha256')!=raw['snapshot_sha256']:
        reasons.append('baseline_snapshot_hash_mismatch')
    if audit.get('policy_version')!=BASELINE_POLICY or audit.get('policy_sha256')!=POLICY_SHA256:
        reasons.append('baseline_policy_mismatch')
    if not _prob(audit.get('probability')) or audit.get('probability')!=.5:reasons.append('baseline_not_fixed_half')
    if raw.get('baseline_probabilities',{}).get(PROSPECTIVE_BASELINE)!=.5:reasons.append('baseline_probability_not_eligible')
    for field in ('policy_sha256','record_sha256'):
        value=audit.get(field)
        if not isinstance(value,str) or len(value)!=64 or any(c not in '0123456789abcdef' for c in value):
            reasons.append('baseline_missing_'+field)
    if not audit.get('baseline_id'):reasons.append('baseline_missing_id')
    if audit.get('inputs_used')!=[]:reasons.append('baseline_feature_inputs_not_empty')
    try:
        if not _stamp(audit['recorded_at'])<_stamp(raw['readout_date']):reasons.append('baseline_not_before_readout')
        if _stamp(audit['source_forecast_locked_at'])!=_stamp(raw['locked_at']):reasons.append('baseline_source_lock_mismatch')
        cutoff=_stamp(raw['evidence_cutoff'])
        if _stamp(audit['effective_evidence_cutoff'])!=cutoff or _stamp(audit['source_forecast_cutoff'])!=cutoff:
            reasons.append('baseline_evidence_cutoff_mismatch')
        if cutoff>_stamp(raw['locked_at']) or cutoff>_stamp(audit['recorded_at']):reasons.append('baseline_invalid_cutoff_order')
    except (KeyError,ValueError,TypeError):reasons.append('baseline_missing_audit_timestamps')
    audit['exclusion_reasons']=sorted(set(reasons));audit['eligible']=not reasons
    return ({PROSPECTIVE_BASELINE:.5} if not reasons else {}),audit


def _prospective(raw_rows, model):
    rows, excluded = [], []
    for raw in raw_rows:
        if _model_key(raw) != model:
            continue
        reasons = []
        if raw.get('scope') != 'prospective' or raw.get('eligible_for_prospective') is not True:
            reasons.append('not_verified_prospective')
        p, label = raw.get('probability'), raw.get('label')
        status=raw.get('prediction_status', 'predicted')
        if status not in ('predicted','abstain','failed','unavailable'):
            reasons.append('invalid_prediction_status')
        elif status=='predicted' and not _prob(p):
            reasons.append('missing_probability')
        elif status!='predicted' and p is not None:
            reasons.append('non_prediction_has_probability')
        if type(label) is not int or label not in (0, 1):
            reasons.append('missing_binary_outcome')
        try:
            locked, readout = _stamp(raw['locked_at']), _stamp(raw['readout_date'])
            if not locked < readout:
                reasons.append('lock_not_strictly_before_readout')
            if _stamp(raw['generated_at']) > locked or _stamp(raw['verified_at']) < readout:
                reasons.append('invalid_timestamp_order')
        except (KeyError, ValueError, TypeError):
            reasons.append('missing_audit_timestamps')
        if not raw.get('trial_id') or not raw.get('endpoint') or not raw.get('prediction_id'):
            reasons.append('missing_target_identity')
        supplied_target=raw.get('target')
        full_target=isinstance(supplied_target,dict) and all(isinstance(supplied_target.get(f),str) and supplied_target[f].strip() for f in TARGET_FIELDS)
        if not full_target:reasons.append('missing_full_target_identity')
        target=supplied_target if full_target else {}
        if full_target and target['endpoint']!=raw.get('endpoint'):reasons.append('endpoint_target_mismatch')
        if reasons:
            excluded.append({'id':raw.get('prediction_id'),'reasons':reasons})
            continue
        baseline_values,baseline_audit=_baseline_audit(raw,target,full_target)
        case_id='case_'+_digest({'trial_id':raw['trial_id'],'target':target,'model':model,'prediction_id':raw['prediction_id']})
        horizon = (readout-locked).days
        features = {'phase': raw.get('phase'), 'therapeutic_area': raw.get('therapeutic_area'),
                    'modality': raw.get('modality'), 'design': raw.get('design'),
                    'sponsor_category': raw.get('sponsor_category') or raw.get('features', {}).get('sponsor_category'),
                    'prediction_horizon': '0–30 days' if horizon<=30 else '31–90 days' if horizon<=90 else '91–180 days' if horizon<=180 else '>180 days'}
        rows.append({'id':raw['prediction_id'],'case_id':case_id,'prediction_id':raw['prediction_id'],
                     'target':target,'target_sha256':_digest(target),'target_identity_status':'complete',
                     'baseline_audit':baseline_audit, 'trial_id':raw['trial_id'], 'trial_name':raw.get('trial_name',raw['trial_id']),
                     'asset':raw.get('asset') or 'unknown', 'endpoint':raw['endpoint'], 'probability':p,'label':label,
                     'prediction_status':status,'coverage_eligible':True,'score_eligible':status=='predicted',
                     'model_identifier':raw['model'],'primary_prediction_id':raw.get('primary_prediction_id',raw['prediction_id']),
                     'model':model,'model_version':raw.get('model_version') or raw.get('model'),'date':readout.date().isoformat(),'prediction_at':raw['generated_at'],
                     'locked_at':raw['locked_at'],'features_as_of':raw.get('evidence_cutoff'),'source_url':raw.get('source_url'),
                     'scope':'prospective','eligible_for_prospective':True,'features':features,
                     'baselines':baseline_values})
    # Full target identity includes population, comparison and success definition.
    # Every record missing its complete target is rejected.
    # Later revisions remain in the ledger but cannot inflate scored N.
    unique = {}
    for row in sorted(rows,key=lambda r:(_stamp(r['locked_at']),r['case_id'])):
        key=(row['trial_id'],row['target_sha256'],row['model'])
        if key in unique:
            reason='later_revision_same_trial_full_target_model'
            excluded.append({'id':row['id'],'case_id':row['case_id'],'reasons':[reason]})
        else:
            unique[key]=row
    return list(unique.values()), excluded


def build_analytics(scope='prospective', model='deepseek-v4-pro', rolling=5, prospective_records=None, dashboard=None):
    """Pure aggregation over verified dashboard/ledger inputs, with optional default archival reader.

    prospective_records must originate from PlatformStore.evaluation_rows(public_only=True).
    This function is not an adjudicator; it rejects temporal and identity gaps in
    addition to requiring the ledger's eligibility decision.
    """
    if scope not in ('prospective','retrospective'):
        raise ValueError('Unknown evaluation scope')
    if type(rolling) is not int or not 2 <= rolling <= 100:
        raise ValueError('Rolling window must be 2 to 100 records')
    prospective_records = prospective_records or []
    known_models = set(MODELS) | {_model_key(r) for r in prospective_records if isinstance(r.get('model'),str)}
    if model not in known_models:
        raise ValueError('Unknown model')
    dashboard = build_dashboard() if dashboard is None else dashboard
    rows, excluded = _retrospective(dashboard, model) if scope == 'retrospective' else _prospective(prospective_records, model)
    rows.sort(key=lambda r:(r['date'],r['id']))
    answered = [r for r in rows if _prob(r['probability'])]
    metrics = _metrics(answered)
    assets = _asset_count(answered)
    sufficient = len(answered)>=MIN_SUBGROUP_N and assets>=MIN_SUBGROUP_ASSETS
    status_counts={status:sum(r['prediction_status']==status for r in rows) for status in ('predicted','abstain','failed','unavailable')}
    summary = metrics | {'total_records':len(rows),'answered':len(answered),'abstained':status_counts['abstain'],
                         'failed':status_counts['failed'],'unavailable':status_counts['unavailable'],
                         'unanswered':len(rows)-len(answered),'status_counts':status_counts,
                         'coverage_scope':'independently_verified_first_public_locked_requests' if scope=='prospective' else 'archived_replay_requests',
                         'coverage_asset_groups':_asset_count(rows),
                         'coverage':len(answered)/len(rows) if rows else None,'asset_groups':assets,
                         'unknown_asset_records':sum(r['asset']=='unknown' for r in answered),
                         'performance_status':'descriptive_only' if sufficient else 'insufficient_verified_predictions',
                         'clinical_prediction_validated':False,'distinct_trials':len({r['trial_id'] for r in rows}),
                         'threshold':.5,'metrics_scope':scope}
    bins=[]
    for i,(lo,hi) in enumerate(zip(BIN_EDGES[:-1],BIN_EDGES[1:])):
        selected=[r for r in answered if lo<=r['probability'] and (r['probability']<hi or (i==4 and r['probability']==1))]
        n=len(selected); k=sum(r['label'] for r in selected)
        bins.append({'lower':lo,'upper':hi,'right_inclusive':i==4,'n':n,'asset_groups':_asset_count(selected),
                     'mean_probability':sum(r['probability'] for r in selected)/n if n else None,
                     'observed_rate':k/n if n else None,'successes':k,'wilson95':wilson(k,n),
                     'inference_status':'descriptive_binomial_interval_not_asset_adjusted'})
    distribution=[]
    for i in range(10):
        selected=[r for r in answered if i/10<=r['probability'] and (r['probability']<(i+1)/10 or i==9 and r['probability']==1)]
        distribution.append({'lower':i/10,'upper':(i+1)/10,'success':sum(r['label'] for r in selected),'failure':sum(1-r['label'] for r in selected)})
    cumulative=[]
    for when in sorted({r['date'] for r in answered}):
        prior=[r for r in answered if r['date']<=when]
        boundary=prior[max(0,len(prior)-rolling)]['date']
        recent=[r for r in prior if r['date']>=boundary]
        cumulative.append({'date':when,'cumulative':_metrics(prior),'rolling':_metrics(recent),'rolling_requested_n':rolling,
                           'rolling_start':boundary,'same_day_ties_kept':True,'asset_groups':_asset_count(prior)})
    subgroups={}
    for field in ('phase','therapeutic_area','modality','design','sponsor_category','prediction_horizon'):
        groups=defaultdict(list)
        for row in rows:
            val=row['features'].get(field)
            if isinstance(val,(list,dict)):val=str(val)
            groups[str(val) if val not in (None,'') else 'unknown'].append(row)
        subgroups[field]=[]
        for value,group in sorted(groups.items()):
            scored=[r for r in group if _prob(r['probability'])]; enough=len(scored)>=MIN_SUBGROUP_N and _asset_count(scored)>=MIN_SUBGROUP_ASSETS
            subgroups[field].append({'value':value,'total':len(group),'n':len(scored),'asset_groups':_asset_count(scored),
                                     'metrics':_metrics(scored) if enough else None,
                                     'status':'descriptive_only' if enough else 'insufficient_sample',
                                     'minimum_n':MIN_SUBGROUP_N,'minimum_asset_groups':MIN_SUBGROUP_ASSETS})
    # Intersection across all shown baselines + selected agent. Never compare
    # separate answered subsets or estimate base rates from test labels.
    names=BASELINES if scope=='retrospective' else (PROSPECTIVE_BASELINE,)
    baseline_names=[b for b in names if any(_prob(r['baselines'].get(b)) for r in answered)]
    common=[r for r in answered if all(_prob(r['baselines'].get(b)) for b in baseline_names)] if baseline_names else []
    baselines=[]
    def case_ids(group):return [r['case_id'] if scope=='prospective' else r['id'] for r in group]
    if common:
        baselines.append({'name':model,'kind':'selected_agent','case_ids':case_ids(common),**_metrics(common)})
        for name in baseline_names:
            scored=[r|{'probability':r['baselines'][name]} for r in common]
            baselines.append({'name':name,'kind':'archived_baseline' if scope=='retrospective' else 'prospective_fixed_baseline',
                              'case_ids':case_ids(common),**_metrics(scored)})
    coverage=None;baseline_provenance=None
    if scope=='prospective':
        rejected=[{'case_id':r['case_id'],'prediction_id':r['prediction_id'],'reasons':r['baseline_audit']['exclusion_reasons']} for r in answered if not r['baseline_audit']['eligible']]
        counts=defaultdict(int)
        for rejection in rejected:
            for reason in rejection['reasons']:counts[reason]+=1
        missing=sum('baseline_not_registered' in r['reasons'] for r in rejected)
        coverage={'scope':'selected_model_scored_cases','total_cases':len(answered),'eligible_cases':len(common),
                  'missing_cases':missing,'excluded_cases':len(rejected)-missing,
                  'coverage':len(common)/len(answered) if answered else None,'case_ids':case_ids(common),
                  'exclusions':rejected,'reason_counts':dict(sorted(counts.items())),
                  'status':'no_scored_cases' if not answered else 'no_eligible_baselines' if not common else 'partial' if rejected else 'complete',
                  'pending_status':'not_observable_from_verified_evaluation_rows'}
        baseline_provenance={'baseline_key':PROSPECTIVE_BASELINE,'policy_version':BASELINE_POLICY,'feature_free':True,
                             'uses_outcome_fitting':False,'estimated_historical_success_rate':False,
                             'records':[{'case_id':r['case_id'],**r['baseline_audit'],'source_forecast_locked_at':r['locked_at']} for r in common],
                             'time_policy':'actual_baseline_registration_strictly_before_readout; evidence_cutoff_matches_source_forecast; inputs_used_empty',
                             'trusted_input':'PlatformStore.evaluation_rows(public_only=True); numeric_baseline_mapping_alone_not_accepted'}
    return {'schema_version':'predome.analytics.v1','scope':scope,'model':model,'models':sorted(x for x in known_models if x),
            'rolling':rolling,'summary':summary,'calibration_bins':bins,'distribution':distribution,'cumulative':cumulative,
            'subgroups':subgroups,'baseline_coverage':coverage,'baseline_provenance':baseline_provenance,'baselines':baselines,'baseline_common_n':len(common),'records':rows,'excluded':excluded,
            'provenance':{'dashboard':dashboard.get('provenance',{}),'threshold':.5,'bin_edges':list(BIN_EDGES),
                          'ci_method':'Wilson 95% descriptive binomial; trial/asset dependence is not adjusted',
                          'subgroup_suppression':{'minimum_n':MIN_SUBGROUP_N,'minimum_asset_groups':MIN_SUBGROUP_ASSETS},
                          'baseline_policy':'archived_predictions_only_same_target_cases_and_frozen_feature_cutoffs_no_test_label_fitting' if scope=='retrospective' else 'verified_fixed_half_sidecar_only_identical_full_target_case_intersection_no_outcome_fitting',
                          'baseline_information_limit':'Archived design baselines and LLM evidence inputs differ in content; matched cases do not establish causal superiority.' if scope=='retrospective' else 'Feature-free fixed 50% reference is not an estimated historical success rate, conditional assurance or validated clinical probability; matched cases do not establish causal superiority.',
                          'version_policy':'first_public_locked_request_per_trial_full_target_model_across_versions; report_versions_separately; no_replacement_of_abstain_failed_unavailable',
                          'coverage_policy':'Eligible independently verified requests include predicted, abstain, failed and unavailable; probability metrics and baselines use answered records only. Unlocked drafts are outside this denominator.',
                          'target_identity_policy':'Every prospective record requires endpoint, population, comparison and success_definition; missing identity is excluded.',
                          'rolling_policy':'last_N_answered_records_include_all_records_on_boundary_date',
                          'historical_memory_contamination_excluded':False if scope=='retrospective' else None,
                          'subgroup_metadata_policy':'explicitly_consented_frozen_user_metadata_or_original_public_archive; unknown_when_not_shared; user_classification_not_independently_verified',
                          'human_double_reviewed_historical':False,'clinical_prediction_validated':False}}
