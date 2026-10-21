"""Freeze reviewed historical cohort, benchmark and comparison as a new release."""
from pathlib import Path
from datetime import datetime, timezone
import argparse, json, hashlib
from predome_pilot import sha, write_new, audit_rows, partition, DATA, ROOT

def build(dataset, run, output):
    dataset, run = dataset.resolve(), run.resolve()
    rows = json.loads(dataset.read_text()); report = json.loads((run / 'report.json').read_text())
    if sha(rows) != report['dataset_sha256']: raise ValueError('Data changed since fitting')
    protocol = json.loads((DATA / 'pilot_protocol.json').read_text())
    if sha(protocol) != report['protocol_sha256']: raise ValueError('Protocol changed since fitting')
    accepted, rejected = audit_rows(rows)
    if len(accepted) != report['accepted']: raise ValueError('Admissibility changed')
    comparison = json.loads((run / 'comparison.json').read_text()) if (run / 'comparison.json').exists() else {'status': 'not_run', 'metrics': {}}
    calls = []
    for name in ['deepseek-flash', 'deepseek-v4-pro']:
        p = run / (name + '.json')
        if p.exists():
            envelope = json.loads(p.read_text())
            if sha(envelope['record']) != envelope['sha256']: raise ValueError('LLM envelope changed')
            calls.append(envelope)
    paths = {dataset, DATA / 'pilot_protocol.json', run / 'report.json', ROOT / 'predome_pilot.py', ROOT / 'predome_pilot_llm.py'}
    paths.update([DATA / 'label_candidates.json', DATA / 'history_probe/snapshot_feature_receipt.json', DATA / 'history_probe/snapshot_features.json', DATA / 'history_probe/build_snapshot_features.py', DATA / 'history_probe/aact_range_probe.py', DATA / 'history_probe/stream_outcomes.py', ROOT / 'assemble_historical_dataset.py'])
    paths.update((DATA / 'history_probe').glob('*/retrieval_receipt*.json'))
    paths.update((DATA / 'history_probe').glob('*/archive_index.json'))
    paths.update((DATA / 'history_probe').glob('*cleanup*.json'))
    if (run / 'pilot_models.pkl').exists(): paths.add(run / 'pilot_models.pkl')
    paths.add(DATA / 'formal_audit_v2/audit.json')
    queue = json.loads((DATA / 'next_dataset_queue.json').read_text())
    paths.add(DATA / 'next_dataset_queue.json')
    paths.update((DATA / 'reviews').glob('*.json'))
    paths.update((DATA / 'next_dataset_sources').rglob('*.json'))
    paths.update([ROOT / 'docs/Predome_历史训练与验证_20261017.md', ROOT / 'docs/Predome_项目优化路线_20261017.md'])
    reasoning_review = json.loads((DATA / 'reviews/independent_pilot_llm_reasoning_review.json').read_text())
    if (run / 'comparison.json').exists(): paths.add(run / 'comparison.json')
    for r in rows:
        for key in ['feature_source', 'label_source', 'review_source']:
            if r.get(key, {}).get('path'): paths.add(ROOT / r[key]['path'])
    for name in ['llm_inputs.json', 'deepseek-flash.json', 'deepseek-v4-pro.json']:
        if (run / name).exists(): paths.add(run / name)
    train, test, _ = partition(accepted)
    semantic_unknown = {split: {k: sum(r['features'].get(k) in [None, 'unknown'] for r in subset) for k in report['feature_contract']} for split, subset in [('train', train), ('test', test)]}
    record = {'reasoning_review': reasoning_review, 'semantic_unknown_or_missing': semantic_unknown, 'next_dataset_queue': queue, 'id': 'predome-historical-20261017-v1', 'created_at_utc': datetime.now(timezone.utc).isoformat(), 'protocol': protocol, 'dataset_rows': rows, 'data_counts': {'candidate_rows': len(rows), 'snapshot_verified_rows': sum(bool(r.get('features_as_of')) for r in rows), 'binary_label_candidate_rows': sum(type(r.get('label')) is int and r['label'] in [0, 1] for r in rows), 'primary_snapshot_available_rows': sum(bool(r.get('historical_primary_outcomes')) for r in rows), 'independent_agent_accepted_rows': len(accepted), 'human_double_adjudicated_rows': sum(r.get('review_status') == 'human_double_reviewed' for r in accepted), 'train': len(train), 'test': len(test), 'train_asset_groups': len({r['asset_group'] for r in train}), 'test_asset_groups': len({r['asset_group'] for r in test})}, 'benchmark': report, 'llm_comparison': comparison, 'llm_runs': calls, 'formal_training_gate': {'status': 'not_met', 'minimums': {'train': 200, 'calibration': 50, 'test': 50}, 'human_double_review_required': True}, 'frozen_forward_predictions_modified': False, 'clinical_prediction_validated': False, 'artifact_manifest': [{'path': str(p.relative_to(ROOT)), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest(), 'bytes': p.stat().st_size} for p in sorted(paths)]}
    write_new(output, {'record': record, 'sha256': sha(record)})
    return {'sha256': sha(record), 'counts': record['data_counts'], 'status': report['status']}

if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--data', type=Path, required=True); p.add_argument('--run', type=Path, required=True); p.add_argument('--output', type=Path, default=DATA / 'release.json'); a = p.parse_args()
    print(json.dumps(build(a.data, a.run, a.output), ensure_ascii=False))
