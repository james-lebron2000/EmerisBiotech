"""Read-only presentation adapter over the independently integrity-checked releases.

No fitting, paid model call, adjudication, or rewrite of a frozen record happens
here. Historical model execution dates are deliberately distinct from feature
cutoffs. Source refreshes cannot replace probabilities or count as adjudications.
"""
from copy import deepcopy
from datetime import date, datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from urllib.parse import urlsplit

from .continuous_api import read_continuous
from .expanded_api import read_expanded
from .predome_statistics import sha

ROOT = Path(__file__).resolve().parents[1]
UPDATES = ROOT / 'dashboard_data/evidence_updates.json'


def _probability(value):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError('Invalid dashboard probability')
    return value


def _sources(items):
    result = []
    seen = set()
    for item in items:
        url = item.get('url')
        if not isinstance(url, str) or urlsplit(url).scheme not in ('http', 'https') or not urlsplit(url).netloc:
            continue
        # A refreshed retrieval of the same URL is a distinct source receipt.
        identity = (url, item.get('sha256'), item.get('retrieved_at'))
        if identity in seen:
            continue
        seen.add(identity)
        result.append({key: item.get(key) for key in ('url', 'source_date', 'retrieved_at', 'sha256')})
    return result


def _window(raw):
    if not isinstance(raw, dict):
        return {'label': '时间待核实', 'start': None, 'end': None, 'precision': 'unknown'}
    start, end = raw.get('start'), raw.get('end')
    if start is None or end is None:
        return {'label': raw.get('label', '时间待核实'), 'start': None, 'end': None, 'precision': 'unknown'}
    if date.fromisoformat(start) > date.fromisoformat(end):
        raise ValueError('Inverted readout window')
    label = raw.get('label', '')
    precision = raw.get('precision')
    if precision is None:
        span = (date.fromisoformat(end) - date.fromisoformat(start)).days
        precision = 'day' if span == 0 else 'year' if span > 300 else 'half_year' if span > 150 else 'quarter' if span > 60 else 'month' if span >= 27 else 'unknown'
    if precision not in ('half_year', 'quarter', 'month', 'day', 'year', 'unknown'):
        raise ValueError('Invalid readout precision')
    return {'label': label, 'start': start, 'end': end, 'precision': precision}


def _calendar_status(window, today, result_status):
    if result_status == 'published':
        return 'published'
    if not window['start'] or not window['end']:
        return 'unknown'
    if window['end'] < today:
        return 'past_window'
    if window['start'] > today:
        return 'future_window'
    return 'within_window'


def _target_definition(value):
    # Original AVANZAR records contain one object; other records contain lists.
    # Normalize the presentation shape without modifying the frozen source.
    items = [value] if isinstance(value, dict) else value
    if not isinstance(items, list) or not items or any(not isinstance(item, dict) or not isinstance(item.get('measure'), str) for item in items):
        raise ValueError('Invalid target definition')
    return deepcopy(items)


def _forecast(raw, created_at=None):
    probability = _probability(raw.get('probability'))
    status = raw.get('status', 'abstain')
    if status != 'predicted' and probability is not None:
        raise ValueError('Non-prediction contains probability')
    if status == 'predicted' and probability is None:
        raise ValueError('Prediction missing probability')
    return {key: deepcopy(raw.get(key, [] if key in ('assumptions', 'key_unknowns', 'evidence_ids') else ''))
            for key in ('reasoning', 'assumptions', 'key_unknowns', 'evidence_ids')} | {
        'status': status, 'probability': probability, 'created_at_utc': created_at,
        'kind': 'uncalibrated_llm_research_estimate',
    }


def _prospective_runs(release):
    """Bind display provenance to the exact reviewed provider artifacts."""
    bound = {item['path']: item['sha256'] for item in release['artifact_manifest']}
    base = 'predome_data/continuous_v4/prospective/'
    def load(path):
        raw = (ROOT / path).read_bytes()
        if hashlib.sha256(raw).hexdigest() != bound.get(path):
            raise ValueError('Forecast provenance artifact hash mismatch')
        return json.loads(raw)
    review = load(base + 'reviews/repair_llm_review.json')
    runs = {}
    for checked in review['models']:
        path = base + checked['output_file']
        envelope = load(path)
        run = envelope['record']
        if (checked['remaining_major_findings'] != 0 or checked['output_sha256'] != bound[path]
                or sha(run) != envelope['sha256'] or run['status'] != 'completed'
                or run['model_requested'] != checked['model']):
            raise ValueError('Forecast provenance review mismatch')
        runs[checked['model']] = run
    return runs


def _prospective_forecast(case_id, model, prediction, runs, locked_at):
    run = runs[model]
    if [f for f in run['forecasts'] if f['case_id'] == case_id] != [prediction]:
        raise ValueError('Forecast differs from reviewed provider output')
    generated = run['completed_at_utc']
    if datetime.fromisoformat(generated.replace('Z', '+00:00')) > datetime.fromisoformat(locked_at.replace('Z', '+00:00')):
        raise ValueError('Forecast generation cannot follow lock')
    return _forecast(prediction, generated) | {
        'generated_at_utc': generated, 'model_version': run['model_returned'],
        'system_prompt_sha256': run.get('system_prompt_sha256'),
        'source_run_record_sha256': sha(run),
    }


def _load_updates():
    if not UPDATES.exists():
        return {}, None, None
    contents = UPDATES.read_bytes()
    updates = json.loads(contents)
    if not isinstance(updates, dict) or not isinstance(updates.get('trials'), list):
        raise ValueError('Invalid evidence update document')
    indexed = {}
    for item in updates['trials']:
        nct = item.get('nct_id')
        if not isinstance(nct, str) or nct in indexed:
            raise ValueError('Invalid or duplicate evidence update')
        status = item.get('result_status', 'not_found_in_checked_sources')
        if status not in ('published', 'not_found_in_checked_sources', 'unverified'):
            raise ValueError('Invalid evidence update result status')
        if status == 'published' and not _sources(item.get('sources', [])):
            raise ValueError('Published status requires source')
        indexed[nct] = item
    return indexed, updates.get('checked_at_utc'), hashlib.sha256(contents).hexdigest()


def build_dashboard(now=None):
    """Return a presentation snapshot; raises on any underlying integrity failure."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError('Dashboard time must include timezone')
    now = now.astimezone(timezone.utc)
    continuous, expanded = read_continuous(), read_expanded()
    if not continuous.get('integrity_verified') or not expanded.get('integrity_verified'):
        raise ValueError('Dashboard requires verified source releases')
    c, h = continuous['record'], expanded['record']
    prospective = c['prospective']
    prospective_runs = _prospective_runs(c)
    packet_path = 'predome_data/continuous_v4/prospective/candidate_packets.json'
    if packet_path not in {item['path'] for item in c['artifact_manifest']}:
        raise ValueError('Input packet is not bound to release')
    packets = {item['nct_id']: item for item in json.loads((ROOT / packet_path).read_text())['candidates']}
    updates, updated_at, update_hash = _load_updates()
    groups = {}
    for case in prospective['cases']:
        nct = case['nct_id']
        update = updates.get(nct, {})
        if nct not in groups:
            window = _window(update.get('window', case['readout_window_bounds']))
            status = update.get('result_status', 'not_found_in_checked_sources')
            groups[nct] = {
                'nct_id': nct, 'trial_name': case['trial_name'], 'asset': case['asset'], 'company': case['company'],
                'indication': packets[nct]['indication'],
                'inputs': {key: deepcopy(packets[nct].get(key)) for key in ('design', 'conditions', 'arms', 'eligibility')},
                'window': window, 'original_window': _window(case['readout_window_bounds']),
                'calendar_status': _calendar_status(window, now.date().isoformat(), status),
                'result_status': status,
                'checked_at': update.get('checked_at_utc') or updated_at or prospective['evidence_as_of_utc'],
                'status_note': update.get('note', '仅代表归档来源核查范围内未发现目标结果；并非全网未公布证明。'),
                'sources': _sources(case.get('sources', []) + update.get('sources', [])),
                'outcome_update': deepcopy(update.get('outcome')),
                'targets': [],
            }
        groups[nct]['targets'].append({
            'case_id': case['case_id'], 'target_definition': _target_definition(case['target_definition']),
            'success_rule': case['target_success_rule'],
            'frozen_at_utc': prospective['frozen_at_utc'], 'evidence_as_of_utc': prospective['evidence_as_of_utc'],
            'record_sha256': sha(prospective),
            'forecasts': {model: _prospective_forecast(case['case_id'], model, prediction, prospective_runs, prospective['frozen_at_utc']) for model, prediction in case['forecasts'].items()},
            'unknowns': deepcopy(case.get('unknowns', [])),
            'prior_evidence_tiers': deepcopy(case.get('prior_evidence_tiers', [])),
            'statistical_assurance': deepcopy(case.get('statistical_assurance')),
            'probabilities_uncalibrated': True,
            'prospective_adjudication_status': 'not_adjudicated',
        })
    upcoming = sorted(groups.values(), key=lambda item: (item['window']['end'] or '9999-12-31', item['trial_name']))
    rows = {row['nct_id']: row for row in h['dataset_rows']}
    benchmark = h['benchmark']
    calls = {run['record']['model_requested']: run['record'] for run in h['llm_runs']}
    historical = []
    # This order is the archived case mapping defined by predome_expanded.py
    # and predome_pilot_llm.compare; do not sort until after the join.
    for index, prediction in enumerate(benchmark['test_predictions']):
        row = rows[prediction['nct_id']]
        case_id = f'case-{index:03d}'
        forecasts = {}
        for model, call in calls.items():
            matching = [item for item in call['forecasts'] if item['case_id'] == case_id]
            if len(matching) != 1:
                raise ValueError('Historical case mapping is incomplete')
            forecasts[model] = _forecast(matching[0], call['completed_at_utc']) | {'model_version': call.get('model_returned')}
        label = row['label']
        if type(label) is not int or label not in (0, 1) or prediction['label'] != label:
            raise ValueError('Historical outcome label mismatch')
        historical.append({
            'nct_id': row['nct_id'], 'trial_name': row['trial_name'], 'asset': row['asset_group'],
            'scope': 'retrospective_replay', 'prospective_eligible': False,
            'features_as_of': row['features_as_of'], 'features': deepcopy(row['features']),
            'model_created_at_utc': benchmark['created_at_utc'], 'result_date': row['first_result_at'],
            'actual_label': label, 'actual_result': 'met' if label == 1 else 'not_met',
            'endpoint': row['label_target'], 'label_source': deepcopy(row['label_source']),
            'feature_source': deepcopy(row['feature_source']),
            'forecasts': forecasts,
            'statistical_models': {model: _probability(p) for model, p in prediction['probabilities'].items()},
            'case_id': case_id, 'review_status': row.get('review_status'),
            'human_review_count': row.get('human_review_count', 0),
            'review_notes': row.get('review_notes', ''),
        })
    historical.sort(key=lambda item: (item['result_date'], item['trial_name']), reverse=True)
    # One common answered subset, same denominator for both models. These are
    # descriptive historical replay scores, never prospective validation.
    models = list(calls)
    common = [row for row in historical if all(row['forecasts'][m]['probability'] is not None for m in models)]
    replay_metrics = {}
    for model in models:
        answered = [row for row in historical if row['forecasts'][model]['probability'] is not None]
        replay_metrics[model] = {
            'total': len(historical), 'answered': len(answered), 'common_n': len(common),
            'common_correct_at_0_5': sum((row['forecasts'][model]['probability'] >= .5) == bool(row['actual_label']) for row in common),
            'common_brier': sum((row['forecasts'][model]['probability'] - row['actual_label']) ** 2 for row in common) / len(common) if common else None,
            'scope': 'retrospective_replay_only',
        }
    return {
        'schema_version': 'emeris.dashboard.v1', 'as_of_utc': now.isoformat(),
        'data_checked_at': updated_at or prospective['evidence_as_of_utc'],
        'summary': {
            'upcoming_trial_count': sum(row['result_status'] != 'published' for row in upcoming),
            'tracked_trial_count': len(upcoming), 'upcoming_target_count': sum(len(row['targets']) for row in upcoming if row['result_status'] != 'published'),
            'tracked_target_count': sum(len(row['targets']) for row in upcoming),
            'historical_trial_count': len(historical), 'prospective_evaluated_n': 0,
            'clinical_prediction_validated': False,
            'scope': '连续队列冻结的7项试验/10个终点假设与v3历史8例回放；其他研究档案保留在研究工作台。',
        },
        'upcoming': upcoming, 'historical': historical, 'replay_metrics': replay_metrics,
        'provenance': {
            'continuous_release_sha256': continuous['sha256'], 'expanded_release_sha256': expanded['sha256'],
            'prospective_record_sha256': sha(prospective), 'updates_sha256': update_hash,
            'integrity_verified': True, 'independent_timestamp_verified': False,
            'continuous_release_created_at': c['created_at_utc'], 'historical_release_created_at': h['created_at_utc'],
            'data_checked_at_is_not_live_search': True,
            'historical_memory_contamination_excluded': False,
            'forecast_method': '冻结的DeepSeek独立研究估计；概率未校准，未进行模型集成。',
        },
    }
