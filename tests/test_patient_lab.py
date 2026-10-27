"""Behavior and state boundaries of the synthetic-only patient workflow."""
import copy
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic import ValidationError
from vbt.patient_lab import PatientLab, ScenarioRequest, digest, result, simulate, snapshot


def test_synthetic_inputs_and_unavailable_clinical_endpoints():
    data = result(ScenarioRequest())
    assert data['patient']['patient_id'] == 'P001'
    assert data['patient']['source_kind'] == 'synthetic'
    assert data['patient']['observations'] == snapshot()['observations']
    assert len(data['patient']['observations']) == 1
    assert not data['clinical_use'] and data['scientific_review'] == 'not_performed'
    assert data['patient_sha256'] == digest(data['patient'])
    for forecast in data['forecasts'].values():
        assert len(forecast['trajectory']) == 25
        assert forecast['trajectory'][0]['burden_index'] == 100
        assert all(forecast[k] is None for k in ('pfs', 'os', 'clinical_response', 'toxicity', 'quality_of_life'))
        for point in forecast['trajectory']:
            assert 0 <= point['scenario_low'] <= point['burden_index'] <= point['scenario_high']
            assert 0 <= point['resistant_fraction'] <= 1


@pytest.mark.parametrize('values', [
    {'patient_id': 'real-patient'}, {'source_kind': 'real'}, {'resistance': 'nan'},
    {'dose': 100}, {'patient': {}}, {'resistance': .2}, {'clinical_use': True},
])
def test_unsupported_patient_or_unreviewed_parameters_rejected(tmp_path, values):
    store = PatientLab(tmp_path)
    with pytest.raises(ValidationError):
        store.run(values)
    assert not store.history()


def test_assumption_changes_scenarios_but_not_survival_illustration():
    low, high = result(ScenarioRequest()), result(ScenarioRequest(resistance='high'))
    assert low['forecasts']['a']['trajectory'][12]['burden_index'] < high['forecasts']['a']['trajectory'][12]['burden_index']
    assert low['input_sha256'] != high['input_sha256']
    assert low['patient_sha256'] == high['patient_sha256']
    assert low['forecasts']['a'] != low['forecasts']['b']
    assert low['survival_illustration'] == high['survival_illustration']
    changed = snapshot()
    changed['patient_id'] = 'different'
    with pytest.raises(ValueError):
        simulate(changed, 'low')


def test_followup_versions_restart_and_no_future_inputs(tmp_path):
    store = PatientLab(tmp_path)
    original = store.run({'resistance': 'high'})
    frozen = copy.deepcopy(original)
    updated = store.followup(original['run_id'])
    assert updated['result']['parent_id'] == original['run_id']
    assert updated['result']['patient']['cutoff_week'] == 6
    assert [r['week'] for r in updated['result']['patient']['observations']] == [0, 6]
    assert updated['result']['forecasts']['a']['trajectory'][0]['burden_index'] == 82
    assert updated['result']['resistance_assumption'] == 'high'
    assert updated['result']['input_sha256'] != original['result']['input_sha256']
    assert store.get(original['run_id']) == frozen
    assert PatientLab(tmp_path).get(updated['run_id']) == updated
    final = store.followup(updated['run_id'])
    assert final['result']['patient']['cutoff_week'] == 12
    assert final['result']['forecasts']['b']['trajectory'][0]['burden_index'] == 69
    with pytest.raises(ValueError, match='No further'):
        store.followup(final['run_id'])
    assert len(store.history()) == 3


def test_duplicate_followup_returns_same_child_even_concurrently(tmp_path):
    store = PatientLab(tmp_path)
    original = store.run({})
    with ThreadPoolExecutor(max_workers=2) as executor:
        children = list(executor.map(store.followup, [original['run_id']] * 2))
    assert children[0] == children[1]
    assert len(store.history()) == 2


def test_integrity_error_is_not_an_empty_verified_history(tmp_path):
    store = PatientLab(tmp_path)
    run = store.run({})
    with store.db() as db:
        data = json.loads(db.execute('SELECT payload FROM scenarios').fetchone()[0])
        data['clinical_use'] = True
        db.execute('UPDATE scenarios SET payload=?', (json.dumps(data),))
    with pytest.raises(ValueError, match='integrity'):
        store.get(run['run_id'])
    with pytest.raises(ValueError, match='integrity'):
        store.history()


@pytest.mark.parametrize('run_id', ['../../x', '', '<script>', 'a' * 32, None])
def test_missing_and_invalid_runs_fail_closed(tmp_path, run_id):
    with pytest.raises(ValueError):
        PatientLab(tmp_path).get(run_id)
