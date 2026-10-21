"""Dashboard contract tests: preserve research records and evaluation boundaries."""
from datetime import datetime, timezone
import json

import pytest

from emeris_live import dashboard

NOW = datetime(2026, 10, 21, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(scope='module')
def releases():
    return dashboard.read_continuous(), dashboard.read_expanded()


@pytest.fixture
def sources(monkeypatch, tmp_path, releases):
    continuous, expanded = releases
    monkeypatch.setattr(dashboard, 'read_continuous', lambda: continuous)
    monkeypatch.setattr(dashboard, 'read_expanded', lambda: expanded)
    monkeypatch.setattr(dashboard, 'UPDATES', tmp_path / 'evidence_updates.json')
    return continuous, expanded


def test_dashboard_keeps_frozen_probabilities_and_abstentions(sources):
    result = dashboard.build_dashboard(NOW)
    originals = {c['case_id']: c for c in sources[0]['record']['prospective']['cases']}
    observed = {t['case_id']: t for trial in result['upcoming'] for t in trial['targets']}
    assert observed.keys() == originals.keys()
    for case_id, target in observed.items():
        for model, prediction in target['forecasts'].items():
            assert prediction['probability'] == originals[case_id]['forecasts'][model]['probability']
            assert prediction['status'] == originals[case_id]['forecasts'][model]['status']
    assert sum(p['probability'] is None for t in observed.values() for p in t['forecasts'].values()) == 2
    assert result['summary']['upcoming_trial_count'] == 7
    assert result['summary']['upcoming_target_count'] == 10
    assert result['summary']['prospective_evaluated_n'] == 0
    assert result['provenance']['prospective_record_sha256'] == '4448e33ecb824a6403d9e1d96c868c3c74d68ae2fc5957c19c11ff8cf113eea6'


def test_historical_execution_date_cannot_masquerade_as_forecast_date(sources):
    result = dashboard.build_dashboard(NOW)
    assert len(result['historical']) == 8
    assert {r['actual_label'] for r in result['historical']} == {0, 1}
    original = sources[1]['record']['benchmark']['test_predictions']
    by_nct = {row['nct_id']: row for row in result['historical']}
    for index, p in enumerate(original):
        row = by_nct[p['nct_id']]
        assert row['case_id'] == f'case-{index:03d}'
        assert row['scope'] == 'retrospective_replay'
        assert row['prospective_eligible'] is False
        assert row['features_as_of'] < row['result_date'] < row['model_created_at_utc']
        assert row['label_source']['url'].startswith('https://')
        for model, f in row['forecasts'].items():
            run = next(run['record'] for run in sources[1]['record']['llm_runs'] if run['record']['model_requested'] == model)
            archived = next(r for r in run['forecasts'] if r['case_id'] == row['case_id'])
            assert f['probability'] == archived['probability']
            assert f['model_version'] == run.get('model_returned')
            assert row['result_date'] < f['created_at_utc']
    assert result['summary']['clinical_prediction_validated'] is False


def test_replay_metrics_keep_shared_denominator_and_refusals(sources):
    result = dashboard.build_dashboard(NOW)
    for model, metrics in result['replay_metrics'].items():
        assert metrics['total'] == 8
        assert metrics['answered'] == metrics['common_n'] == 6
        assert metrics['scope'] == 'retrospective_replay_only'
        original = sources[1]['record']['llm_comparison']['metrics'][model]
        assert metrics['common_brier'] == pytest.approx(original['brier'])
        assert metrics['common_correct_at_0_5'] / metrics['common_n'] == pytest.approx(original['accuracy_threshold_0_5'])


def test_window_dates_are_windows_not_fake_announcement_days(sources):
    result = dashboard.build_dashboard(NOW)
    assert all(t['window']['precision'] in ('year', 'half_year') for t in result['upcoming'])
    assert all(t['window']['start'] != t['window']['end'] for t in result['upcoming'])
    assert sum(t['calendar_status'] == 'future_window' for t in result['upcoming']) == 2
    later = dashboard.build_dashboard(datetime(2027, 7, 1, tzinfo=timezone.utc))
    assert all(t['calendar_status'] == 'past_window' for t in later['upcoming'])
    assert all(t['result_status'] == 'not_found_in_checked_sources' for t in later['upcoming'])
    assert later['data_checked_at'] == result['data_checked_at']
    assert later['as_of_utc'] != result['as_of_utc']


def test_source_update_cannot_modify_forecast_or_auto_adjudicate(sources):
    before = dashboard.build_dashboard(NOW)
    dashboard.UPDATES.write_text(json.dumps({'checked_at_utc': NOW.isoformat(), 'trials': [{
        'nct_id': 'NCT05653349', 'result_status': 'published', 'note': 'Test fixture, not evidence',
        'sources': [{'url': 'https://example.org/official-result'}],
        'forecasts': {'deepseek-flash': {'probability': 1.0}},
        'window': {'label': '2026 Q4', 'start': '2026-10-01', 'end': '2026-12-31', 'precision': 'quarter'},
        'outcome': {'label': 1, 'result_date': '2026-10-21'},
    }]}))
    after = dashboard.build_dashboard(NOW)
    old = next(r for r in before['upcoming'] if r['nct_id'] == 'NCT05653349')
    updated = next(r for r in after['upcoming'] if r['nct_id'] == 'NCT05653349')
    assert updated['targets'] == old['targets']
    assert updated['calendar_status'] == 'published'
    assert updated['window']['precision'] == 'quarter'
    assert after['summary']['upcoming_trial_count'] == 6
    assert after['summary']['prospective_evaluated_n'] == 0
    assert after['provenance']['updates_sha256']


def test_source_integrity_error_fails_closed(sources, monkeypatch):
    def broken():
        raise ValueError('Continuous artifact hash failed')
    monkeypatch.setattr(dashboard, 'read_continuous', broken)
    with pytest.raises(ValueError, match='hash failed'):
        dashboard.build_dashboard(NOW)


def test_unverified_envelope_rejected(sources, monkeypatch):
    monkeypatch.setattr(dashboard, 'read_continuous', lambda: sources[0] | {'integrity_verified': False})
    with pytest.raises(ValueError, match='verified source'):
        dashboard.build_dashboard(NOW)


@pytest.mark.parametrize('value', [True, float('nan'), float('inf'), -.1, 1.1, '0.5'])
def test_invalid_probability_rejected(value):
    with pytest.raises(ValueError):
        dashboard._probability(value)


def test_all_target_definitions_have_safe_uniform_list_shape(sources):
    result = dashboard.build_dashboard(NOW)
    originals = {c['case_id']: c for c in sources[0]['record']['prospective']['cases']}
    for trial in result['upcoming']:
        for target in trial['targets']:
            assert isinstance(target['target_definition'], list)
            assert target['target_definition']
            original = originals[target['case_id']]['target_definition']
            expected = [original] if isinstance(original, dict) else original
            assert target['target_definition'] == expected
            assert all(isinstance(item['measure'], str) for item in target['target_definition'])


def test_prospective_generation_uses_reviewed_provider_time_not_lock(sources):
    result = dashboard.build_dashboard(NOW)
    runs = dashboard._prospective_runs(sources[0]['record'])
    for trial in result['upcoming']:
        for target in trial['targets']:
            for model, forecast in target['forecasts'].items():
                run = runs[model]
                assert forecast['generated_at_utc'] == forecast['created_at_utc'] == run['completed_at_utc']
                assert forecast['created_at_utc'] < target['frozen_at_utc']
                assert forecast['model_version'] == run['model_returned']
                assert forecast['source_run_record_sha256'] == dashboard.sha(run)


def test_provider_provenance_mismatch_fails_closed(sources, monkeypatch):
    from copy import deepcopy
    original = dashboard._prospective_runs(sources[0]['record'])
    tampered = deepcopy(original)
    model = next(iter(tampered))
    tampered[model]['forecasts'][0]['probability'] = 0.123
    monkeypatch.setattr(dashboard, '_prospective_runs', lambda release: tampered)
    with pytest.raises(ValueError, match='differs from reviewed'):
        dashboard.build_dashboard(NOW)
