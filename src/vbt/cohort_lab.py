"""A fixed synthetic paired-exposure cohort, not a randomized clinical trial."""
import math
import random
from statistics import median
from .molecule_lab import (FIXTURE_VALUES, MOLECULES, MODEL_VERSION,
                           patient_snapshot, assay_snapshot, estimate_assay)
from .patient_lab import canonical, digest

COHORT_VERSION = 'synthetic-paired-cohort-24-v1'
SEED = 20261027


def cohort_patients():
    """Frozen, deterministic authoring recipe; seed is not model uncertainty."""
    patients = [patient_snapshot(patient_id) for patient_id in FIXTURE_VALUES]
    rng = random.Random(SEED)
    for number in range(4, 25):
        values = {}
        for molecule in MOLECULES:
            if number == 14 and molecule == 'fluorouracil':
                continue
            # Deliberately varied decline / persistence / rebound patterns.
            rate = rng.uniform(.035, .34)
            rebound = rng.uniform(1, 5) if number % 6 == 0 else 0
            values[molecule] = [round(100 * math.exp(-rate * day) + rebound * max(0, day - 3) ** 1.25, 3)
                                for day in (0, 1, 3, 5, 7)]
        patients.append(assay_snapshot(f'P{number:03}', values, 'Unavailable'))
    return patients


def percentile(values, probability):
    """Linear interpolation at (n-1)*p; no sampling uncertainty implied."""
    if not values:
        return None
    ordered = sorted(values)
    index = (len(values) - 1) * probability
    low = math.floor(index)
    high = math.ceil(index)
    return ordered[low] + (ordered[high] - ordered[low]) * (index - low)


def summarize(rows, molecule, day, patient_ids):
    values = [row['responses'][molecule]['trajectory'][day]['viability_percent']
              for row in rows if row['patient']['patient_id'] in patient_ids
              and row['responses'][molecule]['status'] == 'estimable']
    return {'day': day, 'n': len(values),
            'median': round(median(values), 6) if values else None,
            'q25': round(percentile(values, .25), 6) if values else None,
            'q75': round(percentile(values, .75), 6) if values else None}


def cohort_result(cutoff_day=7):
    if type(cutoff_day) is not int or cutoff_day not in (3, 5, 7):
        raise ValueError('Unsupported cohort cutoff')
    rows = []
    for patient in cohort_patients():
        for assay in patient['assays'].values():
            assay['rows'] = [r for r in assay['rows'] if r['day'] <= cutoff_day and r['available_day'] <= cutoff_day]
        rows.append({'patient': patient, 'responses': {m: estimate_assay(patient, m, cutoff_day) for m in MOLECULES}})
    paired = [r['patient']['patient_id'] for r in rows if all(v['status'] == 'estimable' for v in r['responses'].values())]
    all_ids = [r['patient']['patient_id'] for r in rows]
    summaries = {}
    for population, ids in (('paired', paired), ('available', all_ids)):
        summaries[population] = {m: [summarize(rows, m, day, ids) for day in range(cutoff_day + 4)] for m in MOLECULES}
    payload = {'schema_version': 'patient-cohort-v1', 'cohort_version': COHORT_VERSION,
               'model_version': MODEL_VERSION, 'fixture_seed': SEED, 'cutoff_day': cutoff_day,
               'patients': rows, 'total_patients': len(rows), 'paired_patient_ids': paired,
               'summaries': summaries, 'molecules': MOLECULES,
               'design': 'Same synthetic patients evaluated under both fixed assay exposures; not randomized arms.',
               'source_kind': 'synthetic', 'clinical_use': False, 'scientific_review': 'not_performed',
               'summary_definition': 'Median and linearly interpolated 25th–75th patient percentiles; not confidence intervals.',
               'clinical_outcomes': {'objective_response': None, 'pfs': None, 'os': None, 'toxicity': None},
               'limits': ['Authored synthetic assays only; no clinical trial outcomes.',
                          'Group summaries default to the same complete-pair patients for both molecules.',
                          'Available-case summaries have molecule-specific denominators and cannot establish a treatment effect.',
                          'Days after cutoff are unvalidated three-day assay extrapolations, not clinical follow-up.',
                          'Technical replicates are never counted as additional patients.',
                          'No combination regimens, randomization, clinical efficacy or survival inference.']}
    payload['input_sha256'] = digest({'patients': [r['patient'] for r in rows],
                                      'cohort_version': COHORT_VERSION, 'cutoff_day': cutoff_day,
                                      'model_version': MODEL_VERSION})
    return payload


def public_cohort_bundle():
    templates = {}
    for cutoff in (3, 5, 7):
        payload = cohort_result(cutoff)
        templates[str(cutoff)] = {'canonical_result': canonical(payload), 'sha256': digest(payload)}
    return {'schema': 'patient-cohort-public-v1', 'locale': 'en', 'clinical_use': False, 'templates': templates}
