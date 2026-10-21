"""Join independently curated labels and authentic snapshot features; no model inference."""
from pathlib import Path
import argparse, json, hashlib
from predome_pilot import ROOT, DATA, FIELDS, write_new, audit_rows

def descriptor(path):
    p = path.resolve()
    return {'path': str(p.relative_to(ROOT)), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}

def assemble(review_path, output):
    labels = json.loads((DATA / 'label_candidates.json').read_text())['labels']
    snapshots = json.loads((DATA / 'history_probe/snapshot_features.json').read_text())
    by_nct = {r['nct_id']: r for r in snapshots}
    captures = json.loads((DATA / 'label_capture_descriptors.json').read_text()) if (DATA / 'label_capture_descriptors.json').exists() else {}
    review = json.loads(review_path.read_text()); reviews = {r['nct_id']: r for r in review['reviews']}
    rows = []
    for label in labels:
        nct = label['nct_id']; snapshot = by_nct.get(nct, {}); checked = reviews.get(nct, {})
        accepted = checked.get('decision') == 'accepted'
        source = checked.get('reviewed_source') if accepted else None
        if source: source = source | {'published_at': checked['first_result_at']}
        source = source or label.get('label_source') or captures.get(label.get('result_source_id'))
        if not source and label.get('result_source_local_path'):
            source = descriptor(ROOT / label['result_source_local_path']) | {'url': label['result_source_url'], 'published_at': label['first_official_result_date_candidate'], 'retrieval_method': 'official_source_bytes'}
        features = {key: snapshot.get('features', {}).get(key) for key in FIELDS}
        raw_phase = features['phase']
        if isinstance(raw_phase, str):
            features['phase'] = 3 if '3' in raw_phase else 2 if '2' in raw_phase else None
        for key in ['randomized', 'placebo_controlled']:
            if isinstance(features[key], bool): features[key] = int(features[key])
        row = {'record_id': 'historical-' + nct, 'nct_id': nct, 'trial_name': label['trial_name'], 'asset_group': label['asset_group'], 'label': checked.get('label') if accepted else label.get('binary_label_candidate'), 'label_scope': 'declared_primary_analysis', 'label_target': checked.get('label_target_override') or checked.get('target_definition') or label['target_definition'], 'snapshot_target_match_status': checked.get('snapshot_target_match_status', 'unknown'), 'first_result_at': checked.get('first_result_at') or label['first_official_result_date_candidate'], 'earliest_result_status': 'verified' if accepted else 'not_verified_or_excluded', 'features_as_of': snapshot.get('features_as_of'), 'features': features, 'feature_evidence': snapshot.get('feature_evidence', {}), 'feature_source': snapshot.get('feature_source', {}), 'label_source': source or {}, 'review_source': descriptor(review_path), 'review_status': 'source_curated_agent_checked' if accepted else 'excluded_or_pending', 'human_review_count': 0, 'formal_training_eligible': False, 'review_notes': checked.get('notes', checked.get('reason', '')), 'historical_primary_outcomes': snapshot.get('historical_primary_outcomes', []), 'source_extraction_method': snapshot.get('extraction_method'), 'transforms': {'phase_numeric': 'maximum explicitly registered phase in historical row; preserve raw in source', 'phase_raw': raw_phase, 'booleans': 'True/False to 1/0 only, unknown remains null'}, 'selection': 'directed retrospective pilot collection; not a representative sampling frame', 'rights_scope': 'public registry factual metadata and factual outcome labels; no copyrighted article text used as model features'}
        rows.append(row)
    write_new(output, rows)
    good, bad = audit_rows(rows)
    return {'records': len(rows), 'admissible': len(good), 'rejected': bad}

if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--review', type=Path, required=True); p.add_argument('--output', type=Path, default=DATA / 'dataset.json'); a = p.parse_args()
    print(json.dumps(assemble(a.review, a.output), ensure_ascii=False, indent=2))
