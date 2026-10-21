"""Exploratory temporal benchmark. Does not change the formal training gate.

Inputs require archived pre-result sources and explicit primary-outcome evidence.
No result text, NCT, drug, sponsor, dates or post-result variables enter X.
"""
import argparse
from pathlib import Path
from datetime import date, datetime, timezone
import json, hashlib, math

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'predome_data/historical_v2'
NUMERIC = ['planned_enrollment', 'phase', 'primary_endpoint_count', 'randomized', 'placebo_controlled']
CATEGORICAL = ['therapeutic_area', 'endpoint_family', 'modality', 'comparator_type']
FIELDS = NUMERIC + CATEGORICAL

def sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()).hexdigest()

def write_new(path, value):
    with Path(path).open('x') as f:
        json.dump(value, f, indent=2, ensure_ascii=False, allow_nan=False)

def source_ok(source):
    try:
        path = (ROOT / source['path']).resolve()
        return path.is_relative_to(ROOT) and path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == source['sha256']
    except (ValueError, KeyError, TypeError):
        return False

def audit_rows(rows):
    accepted, rejected, seen = [], [], set()
    for r in rows:
        reasons = []
        identity = r.get('nct_id')
        if not identity or identity in seen: reasons.append('missing_or_duplicate_nct')
        seen.add(identity)
        if type(r.get('label')) is not int or r['label'] not in [0, 1]: reasons.append('primary_label_unknown_or_ambiguous')
        if r.get('label_scope') != 'declared_primary_analysis': reasons.append('not_exact_primary_label')
        if r.get('earliest_result_status') != 'verified': reasons.append('first_disclosure_not_verified')
        try:
            snapshot = date.fromisoformat(r['features_as_of'])
            outcome = date.fromisoformat(r['first_result_at'])
            if not snapshot < outcome: reasons.append('pre_result_date_order_failed')
        except (ValueError, KeyError, TypeError): reasons.append('invalid_or_missing_dates')
        for key in ['feature_source', 'label_source']:
            if not source_ok(r.get(key, {})): reasons.append(key + '_hash_or_path_failed')
        if r.get('feature_source', {}).get('published_at') != r.get('features_as_of'): reasons.append('feature_source_date_mismatch')
        if r.get('label_source', {}).get('published_at') != r.get('first_result_at'): reasons.append('label_source_date_mismatch')
        if source_ok(r.get('feature_source', {})):
            try:
                raw = json.loads((ROOT / r['feature_source']['path']).read_text())
                study = raw['raw_tables']['studies']; design = raw['raw_tables']['designs']
                if raw['snapshot_date'] != r.get('features_as_of') or raw['nct_id'] != identity: reasons.append('snapshot_identity_or_date_mismatch')
                f = r.get('features') or {}
                if f.get('planned_enrollment') is not None and (study.get('enrollment_type', '').lower() not in ['anticipated', 'estimated'] or float(study['enrollment']) != f['planned_enrollment']): reasons.append('not_historical_planned_enrollment')
                expected_phase = 3 if '3' in study.get('phase', '') else 2 if '2' in study.get('phase', '') else None
                if f.get('phase') != expected_phase: reasons.append('phase_does_not_match_snapshot')
                if design.get('allocation', '').lower() != 'randomized': reasons.append('snapshot_not_randomized')
                primary = [x for x in raw['raw_tables'].get('design_outcomes', []) if x.get('outcome_type', '').lower() == 'primary']
                if not primary or f.get('primary_endpoint_count') != len(primary): reasons.append('historical_primary_measure_count_not_verified')
                if study.get('last_update_posted_date') and study['last_update_posted_date'] > raw['snapshot_date']: reasons.append('snapshot_contains_future_update')
            except (ValueError, KeyError, TypeError): reasons.append('snapshot_row_not_verifiable')
        if not r.get('asset_group'): reasons.append('asset_group_missing')
        if r.get('snapshot_target_match_status') != 'matched': reasons.append('primary_snapshot_target_not_matched')
        features = r.get('features')
        if not isinstance(features, dict): reasons.append('features_missing')
        else:
            if set(features) - set(FIELDS): reasons.append('unapproved_feature')
            if any(features.get(k) is not None and not r.get('feature_evidence', {}).get(k) for k in features): reasons.append('feature_evidence_missing')
            if features.get('phase') not in [2, 3]: reasons.append('phase_out_of_scope')
            if features.get('randomized') != 1: reasons.append('not_verified_randomized')
        if r.get('review_status') not in ['source_curated_agent_checked', 'human_double_reviewed']: reasons.append('independent_review_missing')
        if not source_ok(r.get('review_source', {})): reasons.append('review_source_missing')
        else:
            try:
                review = json.loads((ROOT / r['review_source']['path']).read_text())
                matches = [x for x in review['reviews'] if x.get('nct_id') == identity]
                if len(matches) != 1 or matches[0].get('decision') != 'accepted' or matches[0].get('label') != r.get('label') or matches[0].get('first_result_at') != r.get('first_result_at'): reasons.append('review_decision_not_matching_row')
                elif matches[0].get('reviewed_source', {}).get('sha256') != r.get('label_source', {}).get('sha256') or not matches[0].get('source_hash_verified'): reasons.append('label_source_not_independently_reviewed')
                elif matches[0].get('snapshot_target_match_status') != 'matched' or matches[0].get('reviewed_snapshot', {}).get('sha256') != r.get('feature_source', {}).get('sha256'): reasons.append('primary_alignment_review_not_matching_snapshot')
            except (ValueError, KeyError, TypeError): reasons.append('invalid_review_document')
        if reasons: rejected.append({'nct_id': identity, 'reasons': reasons})
        else: accepted.append(r)
    return accepted, rejected

def partition(rows):
    train = [r for r in rows if r['first_result_at'] < '2023-01-01']
    test = [r for r in rows if '2023-01-01' <= r['first_result_at'] < '2026-01-01']
    assets = {r['asset_group'] for r in test}
    purged = [r['nct_id'] for r in train if r['asset_group'] in assets]
    return [r for r in train if r['asset_group'] not in assets], test, purged

def metrics(y, p):
    import numpy as np
    from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score, average_precision_score, accuracy_score
    y, p = np.asarray(y), np.asarray(p)
    return {'n': len(y), 'brier': float(brier_score_loss(y, p)), 'log_loss': float(log_loss(y, np.clip(p, 1e-6, 1-1e-6), labels=[0, 1])), 'auroc': float(roc_auc_score(y, p)) if len(set(y)) == 2 else None, 'average_precision': float(average_precision_score(y, p)) if any(y) else None, 'accuracy_threshold_0_5': float(accuracy_score(y, p >= .5))}

def paired_bootstrap(test, predictions):
    import numpy as np
    rng = np.random.default_rng(20261017)
    groups = sorted({r['asset_group'] for r in test})
    by_group = {g: [i for i, r in enumerate(test) if r['asset_group'] == g] for g in groups}
    y = np.array([r['label'] for r in test]); arrays = {k: np.array(v) for k, v in predictions.items()}
    values = {k: [] for k in predictions}; differences = {k: [] for k in predictions if k != 'beta_pooled'}
    for _ in range(2000):
        ix = [i for g in rng.choice(groups, len(groups), replace=True) for i in by_group[g]]
        errs = {k: float(np.mean((p[ix] - y[ix]) ** 2)) for k, p in arrays.items()}
        for k, err in errs.items(): values[k].append(err)
        for k in differences: differences[k].append(errs[k] - errs['beta_pooled'])
    interval = lambda v: [float(x) for x in np.quantile(v, [.025, .975])]
    return {'unit': 'asset_group', 'groups': len(groups), 'replicates': 2000, 'seed': 20261017, 'brier_percentile_95_intervals': {k: interval(v) for k, v in values.items()}, 'paired_brier_difference_vs_beta_pooled': {k: interval(v) for k, v in differences.items()}, 'interpretation': 'Small selected-cohort exploratory uncertainty, not population clinical validation; negative difference favors method.'}

def fit(rows, output):
    import numpy as np
    from sklearn.compose import ColumnTransformer
    from sklearn.pipeline import Pipeline
    from sklearn.impute import SimpleImputer
    from sklearn.preprocessing import StandardScaler, OneHotEncoder
    from sklearn.linear_model import LogisticRegression
    from sklearn.ensemble import ExtraTreesClassifier
    output = Path(output); output.mkdir(parents=True, exist_ok=False)
    protocol = json.loads((DATA / 'pilot_protocol.json').read_text())
    accepted, rejected = audit_rows(rows); train, test, purged = partition(accepted)
    report = {'id': protocol['id'], 'created_at_utc': datetime.now(timezone.utc).isoformat(), 'protocol_sha256': sha(protocol), 'dataset_sha256': sha(rows), 'status': 'blocked_insufficient_point_in_time_data', 'exploratory_training_completed': False, 'formal_training_completed': False, 'clinical_prediction_validated': False, 'accepted': len(accepted), 'rejected': rejected, 'counts': {'train': len(train), 'test': len(test)}, 'purged_train_asset_overlap': purged, 'feature_contract': FIELDS, 'metrics': {}, 'test_predictions': [], 'feature_quality': {split: {k: {'nonmissing': sum(r['features'].get(k) is not None for r in subset), 'missing': sum(r['features'].get(k) is None for r in subset), 'unique_nonmissing': len({r['features'][k] for r in subset if r['features'].get(k) is not None})} for k in FIELDS} for split, subset in [('train', train), ('test', test)]}, 'limitations': ['Selected small cohort, not representative of all MNC readouts.', 'Independent agent review is not human double adjudication.', 'Primary disclosure may precede journal publication; only verified first disclosures accepted.', 'No individual patient efficacy or approval prediction.', 'Retrospective LLM replay can be contaminated by pretrained memory.', 'Feature-only statistical baselines differ from the existing drug-specific TOPAZ assurance method.', 'Bootstrap intervals are conditional on fitted models and selected test cohort; they omit training and source-adjudication uncertainty.', 'Snapshot-to-readout horizon varies across cases; this is not a fixed-horizon prospective cohort.']}
    if len(train) < 12 or len(test) < 6 or any(len({r['label'] for r in split}) < 2 for split in [train, test]):
        write_new(output / 'report.json', report); return report
    def xy(records):
        return np.array([[np.nan if r['features'].get(k) is None else r['features'][k] for k in NUMERIC] + [r['features'].get(k) or 'unknown' for k in CATEGORICAL] for r in records], dtype=object), np.array([r['label'] for r in records])
    xt, yt = xy(train); xe, ye = xy(test)
    base = float((yt.sum() + .5) / (len(yt) + 1))
    predictions = {'beta_pooled': [base] * len(test), 'beta_area': []}
    for r in test:
        area = r['features'].get('therapeutic_area')
        local = [t['label'] for t in train if t['features'].get('therapeutic_area') == area]
        predictions['beta_area'].append((sum(local) + 8 * base) / (len(local) + 8))
    models = {}
    for name, estimator in [('logistic_l2', LogisticRegression(C=.1, max_iter=2000, random_state=20261017)), ('extra_trees', ExtraTreesClassifier(n_estimators=300, max_depth=3, min_samples_leaf=4, random_state=20261017))]:
        prep = ColumnTransformer([('numeric', Pipeline([('impute', SimpleImputer(strategy='median', keep_empty_features=True)), ('scale', StandardScaler())]), list(range(len(NUMERIC)))), ('category', OneHotEncoder(handle_unknown='ignore'), list(range(len(NUMERIC), len(FIELDS))))])
        model = Pipeline([('features', prep), ('model', estimator)]).fit(xt, yt)
        models[name] = model; predictions[name] = model.predict_proba(xe)[:, 1].tolist()
    import pickle, sklearn
    blob = pickle.dumps({'models': models, 'feature_contract': FIELDS, 'protocol_sha256': sha(protocol), 'dataset_sha256': sha(rows)})
    (output / 'pilot_models.pkl').write_bytes(blob)
    report.update(status='exploratory_temporal_benchmark_complete', exploratory_training_completed=True, train_prevalence=float(yt.mean()), test_prevalence=float(ye.mean()), label_counts={'train': {'success': int(yt.sum()), 'failure': int(len(yt)-yt.sum())}, 'test': {'success': int(ye.sum()), 'failure': int(len(ye)-ye.sum())}}, statistical_pooled_posterior_mean=base, metrics={k: metrics(ye, v) for k, v in predictions.items()}, uncertainty=paired_bootstrap(test, predictions), training_ncts=[r['nct_id'] for r in train], model_sha256=hashlib.sha256(blob).hexdigest(), sklearn_version=sklearn.__version__, transformed_feature_names=models['logistic_l2'].named_steps['features'].get_feature_names_out().tolist())
    for i, r in enumerate(test):
        report['test_predictions'].append({'nct_id': r['nct_id'], 'asset_group': r['asset_group'], 'label': r['label'], 'features_as_of': r['features_as_of'], 'first_result_at': r['first_result_at'], 'probabilities': {k: v[i] for k, v in predictions.items()}})
    # LLM inputs exclude identity, dates, result sources, labels and all peer outputs.
    anonymized = [{'case_id': 'case-%03d' % i, 'features': {k: r['features'].get(k) for k in FIELDS}} for i, r in enumerate(test)]
    write_new(output / 'llm_inputs.json', {'protocol_sha256': sha(protocol), 'task': 'Estimate probability of declared primary endpoint success using ONLY pre-result design features. No drug-specific evidence is supplied. Abstain if insufficient.', 'cases': anonymized})
    write_new(output / 'report.json', report)
    return report

if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--data', type=Path, required=True); p.add_argument('--output', type=Path, required=True)
    a = p.parse_args(); report = fit(json.loads(a.data.read_text()), a.output)
    print(json.dumps({k: report[k] for k in ['status', 'accepted', 'counts', 'metrics']}, ensure_ascii=False))
