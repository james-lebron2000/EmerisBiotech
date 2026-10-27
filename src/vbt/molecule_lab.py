"""Patient-molecule paired synthetic assays; no clinical efficacy inference."""
import copy
import math
from statistics import mean
from .patient_lab import canonical, digest

MODEL_VERSION = 'paired-assay-log-tail-v1'
MOLECULES = {
    'fluorouracil': {'label': 'Fluorouracil', 'identity_source': 'https://www.cancer.gov/publications/dictionaries/cancer-drug/def/fluorouracil'},
    'oxaliplatin': {'label': 'Oxaliplatin', 'identity_source': 'https://www.cancer.gov/publications/dictionaries/cancer-drug/def/oxaliplatin'},
}
# Deliberately authored synthetic measurements, not public data or clinical outcomes.
FIXTURE_VALUES = {
    'P001': {'fluorouracil': [100, 82, 56, 37, 29], 'oxaliplatin': [100, 90, 76, 68, 64]},
    'P002': {'fluorouracil': [100, 95, 89, 86, 85], 'oxaliplatin': [100, 75, 44, 27, 20]},
    'P003': {'fluorouracil': [100, 88, 71, 60, 55]},
}
DAYS = (0, 1, 3, 5, 7)


def patient_snapshot(patient_id):
    if patient_id not in FIXTURE_VALUES:
        raise ValueError('Unknown synthetic patient')
    assays = {}
    for molecule, values in FIXTURE_VALUES[patient_id].items():
        rows = []
        for i, (day, value) in enumerate(zip(DAYS, values)):
            vehicle = [1000 + 160 * day] * 3
            # Matched replicate pairs; variation is synthetic technical spread only.
            drug = [round(vehicle[r] * (value + (r - 1) * (0 if day == 0 else 2)) / 100, 6) for r in range(3)]
            rows.append({'day': day, 'available_day': day, 'vehicle_counts': vehicle, 'drug_counts': drug,
                         'source_id': f'synthetic-{patient_id}-{molecule}-d{day}'})
        assays[molecule] = {'patient_id': patient_id, 'sample_id': patient_id + '-T01', 'molecule_id': molecule,
                            'assay_id': f'{patient_id}-{molecule}-v1', 'source_kind': 'synthetic',
                            'exposure': 'Fixed synthetic exposure X1; no clinical dose equivalence',
                            'rows': rows}
    return {'patient_id': patient_id, 'source_kind': 'synthetic', 'fixture_version': 'paired-assays-v1',
            'sample_id': patient_id + '-T01', 'clinical': {'Diagnosis': 'Colorectal cancer (synthetic)',
                'Age / ECOG': {'P001': '58 / 1', 'P002': '64 / 0', 'P003': '61 / unavailable'}[patient_id],
                'Imaging / organ function': 'Unavailable; not assumed normal'},
            'molecular': {'DNA / RNA / protein': 'Unavailable', 'Single-cell / spatial': 'Unavailable',
                          'Functional assay': 'Synthetic matched drug / vehicle viable-cell counts'},
            'assays': assays}


def estimate_assay(patient, molecule_id, cutoff_day):
    """Interpolate measured means; extrapolate three days using the last log slope.

    Raw matched counts are normalized to vehicle at each time and baseline. The
    terminal fraction is an assay endpoint, never a resistant-cell fraction.
    """
    if molecule_id not in MOLECULES or type(cutoff_day) is not int or cutoff_day not in (3, 5, 7):
        raise ValueError('Unsupported molecule or data cutoff')
    assay = patient['assays'].get(molecule_id)
    if assay is None:
        return {'status': 'not_estimable', 'reason': 'No paired assay for this patient and molecule.', 'trajectory': []}
    if (assay['patient_id'] != patient['patient_id'] or assay['sample_id'] != patient['sample_id']
            or assay['molecule_id'] != molecule_id or assay['source_kind'] != 'synthetic'):
        raise ValueError('Patient, sample or molecule mapping mismatch')
    rows = copy.deepcopy([r for r in assay['rows'] if r['available_day'] <= cutoff_day and r['day'] <= cutoff_day])
    if len(rows) < 3 or rows[0]['day'] != 0 or rows[-1]['day'] != cutoff_day:
        return {'status': 'not_estimable', 'reason': 'Baseline and at least two paired post-exposure timepoints required.', 'trajectory': []}
    if [r['day'] for r in rows] != sorted(set(r['day'] for r in rows)):
        raise ValueError('Assay timepoints must be unique and ordered')
    for row in rows:
        counts = row['vehicle_counts'] + row['drug_counts']
        if (len(row['vehicle_counts']) != 3 or len(row['drug_counts']) != 3
                or any(type(x) not in (int, float) or not math.isfinite(x) or x <= 0 for x in counts)):
            raise ValueError('Three positive finite matched technical replicates required')
    baseline = [d / v for d, v in zip(rows[0]['drug_counts'], rows[0]['vehicle_counts'])]
    measured = []
    for row in rows:
        values = [100 * d / v / b for d, v, b in zip(row['drug_counts'], row['vehicle_counts'], baseline)]
        measured.append({'day': row['day'], 'mean_percent': round(mean(values), 6),
                         'replicate_min': round(min(values), 6), 'replicate_max': round(max(values), 6),
                         'source_id': row['source_id']})
    last, previous = measured[-1], measured[-2]
    rate = math.log(last['mean_percent'] / previous['mean_percent']) / (last['day'] - previous['day'])
    trajectory = []
    for day in range(cutoff_day + 4):
        if day <= cutoff_day:
            right = next(i for i, r in enumerate(measured) if r['day'] >= day)
            a, b = measured[max(0, right - 1)], measured[right]
            weight = 0 if a['day'] == b['day'] else (day - a['day']) / (b['day'] - a['day'])
            value = a['mean_percent'] + weight * (b['mean_percent'] - a['mean_percent'])
            kind = 'measured' if day in [r['day'] for r in measured] else 'interpolated'
        else:
            value = last['mean_percent'] * math.exp(rate * (day - cutoff_day))
            kind = 'extrapolated'
        trajectory.append({'day': day, 'viability_percent': round(value, 6), 'kind': kind})
    return {'status': 'estimable', 'assay': {**{k: v for k, v in assay.items() if k != 'rows'}, 'rows': rows},
            'measured': measured, 'terminal_viability_percent': last['mean_percent'],
            'terminal_day': cutoff_day, 'terminal_log_rate_per_day': rate,
            'trajectory': trajectory, 'resistant_cell_fraction': None,
            'resistance_status': 'Unresolved: viable-cell counts do not identify resistant cells.',
            'uncertainty': 'Technical replicate min-max only; no predictive confidence interval.'}


def simulate_molecule(patient_id, molecule_id, cutoff_day=7):
    patient = patient_snapshot(patient_id)
    # Remove future observations before hashing or exporting. No temporal leakage.
    for assay in patient['assays'].values():
        assay['rows'] = [r for r in assay['rows'] if r['available_day'] <= cutoff_day and r['day'] <= cutoff_day]
    response = estimate_assay(patient, molecule_id, cutoff_day)
    inputs = {'patient': patient, 'molecule_id': molecule_id, 'cutoff_day': cutoff_day, 'model_version': MODEL_VERSION}
    return {'schema_version': 'patient-molecule-v1', **inputs, 'molecule': MOLECULES[molecule_id],
            'input_sha256': digest(inputs), 'response': response, 'source_kind': 'synthetic',
            'clinical_outcomes': {k: None for k in ('objective_response', 'tumor_burden', 'pfs', 'os', 'toxicity')},
            'clinical_use': False, 'scientific_review': 'not_performed',
            'limits': ['All patient-linked measurements are authored synthetic fixtures.',
                       'Named molecules identify assay conditions; simulated values are not drug efficacy evidence.',
                       'Exposure X1 has no human dose or pharmacokinetic equivalence.',
                       'A three-day log-tail extrapolation is an unvalidated assay forecast.',
                       'Clinical and molecular context is displayed but not fitted as predictive features.',
                       'No drug ranking, treatment recommendation or survival inference.']}


def public_bundle():
    templates = {}
    for patient_id in FIXTURE_VALUES:
        templates[patient_id] = {}
        for molecule_id in MOLECULES:
            templates[patient_id][molecule_id] = {}
            for cutoff in (3, 5, 7):
                payload = simulate_molecule(patient_id, molecule_id, cutoff)
                templates[patient_id][molecule_id][str(cutoff)] = {'canonical_result': canonical(payload), 'sha256': digest(payload)}
    return {'schema': 'patient-molecule-public-v1', 'locale': 'en', 'molecules': MOLECULES,
            'patients': list(FIXTURE_VALUES), 'templates': templates, 'clinical_use': False}
