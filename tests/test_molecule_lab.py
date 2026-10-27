import json
from pathlib import Path
import pytest
from vbt.molecule_lab import patient_snapshot, simulate_molecule, estimate_assay, public_bundle
from vbt.patient_lab import canonical, digest


def test_paired_responses_differ_by_patient_and_molecule():
    expected = {('P001','fluorouracil'):29, ('P001','oxaliplatin'):64,
                ('P002','fluorouracil'):85, ('P002','oxaliplatin'):20}
    for (patient, molecule), value in expected.items():
        r = simulate_molecule(patient,molecule)
        assert r['response']['terminal_viability_percent'] == value
        assert r['response']['resistant_cell_fraction'] is None
        assert all(v is None for v in r['clinical_outcomes'].values())
        assert not r['clinical_use']
        assert 'resistance_assumption' not in canonical(r)
        assert r['response']['trajectory'][-1]['day'] == 10
        assert r['response']['trajectory'][-1]['kind'] == 'extrapolated'


def test_missing_pair_abstains_without_default_or_cross_patient_transfer():
    r = simulate_molecule('P003','oxaliplatin')
    assert r['response']['status'] == 'not_estimable'
    assert r['response']['trajectory'] == []
    assert 'terminal_viability_percent' not in r['response']


def test_measurement_changes_change_automatic_estimate():
    patient = patient_snapshot('P001')
    first = estimate_assay(patient,'fluorouracil',7)
    row = patient['assays']['fluorouracil']['rows'][-1]
    row['drug_counts'] = [v * 2 for v in row['drug_counts']]
    second = estimate_assay(patient,'fluorouracil',7)
    assert second['terminal_viability_percent'] == 2 * first['terminal_viability_percent']
    assert second['trajectory'][-1]['viability_percent'] > first['trajectory'][-1]['viability_percent']


def test_cutoff_filters_future_from_estimate_and_export():
    patient = patient_snapshot('P001')
    first = estimate_assay(patient,'fluorouracil',3)
    patient['assays']['fluorouracil']['rows'][-1]['drug_counts'] = [999999] * 3
    assert first == estimate_assay(patient,'fluorouracil',3)
    early = simulate_molecule('P001','fluorouracil',3)
    assert all(row['day'] <= 3 for assay in early['patient']['assays'].values() for row in assay['rows'])
    assert early['input_sha256'] != simulate_molecule('P001','fluorouracil',7)['input_sha256']


@pytest.mark.parametrize('bad',[0,-1,float('nan'),float('inf'),True])
def test_invalid_counts_fail_closed(bad):
    patient = patient_snapshot('P001')
    patient['assays']['fluorouracil']['rows'][0]['vehicle_counts'][0] = bad
    with pytest.raises(ValueError): estimate_assay(patient,'fluorouracil',7)


def test_patient_sample_molecule_mapping_cannot_cross():
    patient = patient_snapshot('P001')
    patient['assays']['fluorouracil']['patient_id']='P002'
    with pytest.raises(ValueError): estimate_assay(patient,'fluorouracil',7)
    with pytest.raises(ValueError): simulate_molecule('P404','fluorouracil')
    with pytest.raises(ValueError): simulate_molecule('P001','unknown')
    with pytest.raises(ValueError): simulate_molecule('P001','fluorouracil',4)


def test_templates_and_build(tmp_path):
    import subprocess
    import sys
    root = Path(__file__).resolve().parents[1]
    subprocess.run([sys.executable,str(root/'scripts/build_patient_site.py'),'--out',str(tmp_path)],check=True)
    bundle=json.loads((tmp_path/'molecule-lab/scenarios.json').read_text())
    assert bundle == public_bundle()
    for patient,molecules in bundle['templates'].items():
        for molecule,cutoffs in molecules.items():
            for cutoff,t in cutoffs.items():
                r=simulate_molecule(patient,molecule,int(cutoff))
                assert t == {'canonical_result':canonical(r),'sha256':digest(r)}
    assert 'id="resistance"' not in (tmp_path/'molecule-lab/index.html').read_text()


@pytest.mark.parametrize('mutation',[
    lambda p:p.update(source_kind='real'),
    lambda p:p.pop('sample_id'),
    lambda p:p.update(assays=[]),
    lambda p:p['assays']['fluorouracil'].update(source_kind='real'),
    lambda p:p['assays']['fluorouracil'].update(exposure=''),
    lambda p:p['assays']['fluorouracil'].pop('assay_id'),
    lambda p:p['assays']['fluorouracil']['rows'][1].update(day=True),
    lambda p:p['assays']['fluorouracil']['rows'][1].update(available_day=0),
    lambda p:p['assays']['fluorouracil']['rows'][1].update(available_day=1.0),
    lambda p:p['assays']['fluorouracil']['rows'][1].update(source_id=''),
    lambda p:p['assays']['fluorouracil']['rows'][1].update(source_id='synthetic-P001-fluorouracil-d0'),
    lambda p:p['assays']['fluorouracil']['rows'][1].update(drug_counts='100,200,300'),
    lambda p:p['assays']['fluorouracil']['rows'][1].pop('vehicle_counts'),
])
def test_declared_patient_scope_and_measurement_contract_fail_closed(mutation):
    patient=patient_snapshot('P001')
    mutation(patient)
    with pytest.raises(ValueError):estimate_assay(patient,'fluorouracil',7)


def test_availability_lag_cannot_include_unavailable_measurement():
    patient=patient_snapshot('P001')
    patient['assays']['fluorouracil']['rows'][-1]['available_day']=8
    r=estimate_assay(patient,'fluorouracil',7)
    assert r['status']=='not_estimable'
