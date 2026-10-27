"""120 authored mCRC scenarios with real regimen names and uncalibrated kinetics.

This is a separate illustrative model, NOT an assay-to-clinical conversion.
Clinical regimen references constrain the catalogue, never the simulated effect sizes.
"""
import csv
import io
import math
import random
from statistics import median
from .patient_lab import canonical, digest

VERSION = 'synthetic-regimen-cohort-120-v1'
HORIZON = 72
SOURCES = {
    'asco': {'title': 'ASCO mCRC guideline (2022)', 'url': 'https://ascopubs.org/doi/10.1200/JCO.22.01690'},
    'esmo': {'title': 'ESMO mCRC guideline (2026)', 'url': 'https://www.annalsofoncology.org/article/S0923-7534(26)00108-0/abstract'},
    'nci': {'title': 'NCI Colon Cancer Treatment PDQ', 'url': 'https://www.cancer.gov/types/colorectal/hp/colon-treatment-pdq'},
    'fda': {'title': 'FDA FTD/TPI + bevacizumab approval (2023)', 'url': 'https://www.fda.gov/drugs/drug-approvals-and-databases/fda-approves-trifluridine-and-tipiracil-bevacizumab-previously-treated-metastatic-colorectal-cancer'},
}
REGIMENS = {
    'folfox_bev': {'label': 'FOLFOX + bevacizumab', 'components': ['fluorouracil', 'leucovorin', 'oxaliplatin', 'bevacizumab'], 'source_ids': ['asco', 'esmo']},
    'folfiri_bev': {'label': 'FOLFIRI + bevacizumab', 'components': ['fluorouracil', 'leucovorin', 'irinotecan', 'bevacizumab'], 'source_ids': ['asco', 'esmo']},
    'folfiri_afl': {'label': 'FOLFIRI + ziv-aflibercept', 'components': ['fluorouracil', 'leucovorin', 'irinotecan', 'ziv-aflibercept'], 'source_ids': ['nci'], 'eligibility': 'After an oxaliplatin-containing regimen.'},
    'ftdtpi_bev': {'label': 'Trifluridine/tipiracil + bevacizumab', 'components': ['trifluridine', 'tipiracil', 'bevacizumab'], 'source_ids': ['fda'], 'eligibility': 'After fluoropyrimidine, oxaliplatin, irinotecan and anti-VEGF exposure; RAS-mutant cohort.'},
}
PATHWAYS = {
    'A': {'label': 'Oxaliplatin first', 'regimens': ['folfox_bev', 'folfiri_afl', 'ftdtpi_bev']},
    'B': {'label': 'Irinotecan first', 'regimens': ['folfiri_bev', 'folfox_bev', 'ftdtpi_bev']},
}


def patients():
    rng = random.Random(20261028)
    rows = []
    for number in range(1, 121):
        growth = round(rng.uniform(.012, .035), 6)
        # All coefficients below are authored scenario inputs, not measured biomarkers.
        coefficients = {k: round(rng.uniform(.035, .14), 6) for k in ('oxaliplatin', 'irinotecan')}
        coefficients['later_line'] = round(rng.uniform(.018, .065), 6)
        coefficients['escape_per_week'] = round(rng.uniform(.035, .10), 6)
        rows.append({'patient_id': f'P{number:03}', 'source_kind': 'synthetic',
                     'diagnosis': 'Initially unresectable metastatic colorectal cancer',
                     'molecular_scope': {'mmr': 'pMMR', 'msi': 'MSS', 'ras': 'mutant', 'braf_v600e': 'wild-type'},
                     'age': rng.randint(40, 74), 'ecog': rng.choice([0, 1]),
                     'metastatic_sites': rng.choice([['liver'], ['liver', 'lung'], ['peritoneum', 'liver']]),
                     'baseline_index': 100, 'pretreatment_index': [{'week': w, 'index': round(100*math.exp(growth*w), 6)} for w in (-8, -4, 0)],
                     'scenario_coefficients': coefficients, 'coefficient_origin': 'Authored synthetic inputs; no clinical or assay calibration.'})
    return rows


def trajectory(patient, pathway):
    if pathway not in PATHWAYS:
        raise ValueError('Unsupported pathway')
    pretreatment = patient['pretreatment_index']
    growth = math.log(pretreatment[-1]['index']/pretreatment[0]['index'])/8
    c = patient['scenario_coefficients']
    line, started, burden, nadir = 1, 0, 100., 100.
    rows, events = [], []
    for week in range(HORIZON+1):
        if week:
            elapsed = week-1-started
            drug = ('oxaliplatin' if PATHWAYS[pathway]['regimens'][line-1]=='folfox_bev' else 'irinotecan') if line<3 else 'later_line'
            # Same coefficients in both pathways; no pathway-specific efficacy bonus.
            effect = c[drug]*math.exp(-c['escape_per_week']*elapsed)
            burden *= math.exp(growth-effect)
            nadir = min(nadir, burden)
            # Explicit toy review rule, not RECIST or a recommendation to switch.
            if week%8==0 and line<3 and burden >= nadir*1.3 and burden-nadir>=15:
                line += 1
                started, nadir = week, burden
                events.append({'week': week, 'line': line, 'regimen_id': PATHWAYS[pathway]['regimens'][line-1], 'reason': 'Illustrative 8-week review rule'})
        rows.append({'week': week, 'burden_index': round(burden, 6), 'line': line,
                     'regimen_id': PATHWAYS[pathway]['regimens'][line-1]})
    return {'trajectory': rows, 'switches': events}


def regimen_cohort_result():
    inputs = patients()
    rows = [{'patient': p, 'pathways': {k: trajectory(p, k) for k in PATHWAYS}} for p in inputs]
    summaries = {}
    for key in PATHWAYS:
        summaries[key] = []
        for week in range(HORIZON+1):
            points = [r['pathways'][key]['trajectory'][week] for r in rows]
            summaries[key].append({'week': week, 'n': len(points), 'median_index': round(median(p['burden_index'] for p in points), 6),
                                   'line_counts': {str(line): sum(p['line']==line for p in points) for line in (1, 2, 3)}})
    return {'schema_version': 'regimen-cohort-v1', 'model_version': VERSION, 'source_kind': 'synthetic',
            'clinical_use': False, 'scientific_review': 'not_performed', 'total_patients': 120, 'horizon_weeks': HORIZON,
            'patients': rows, 'pathways': PATHWAYS, 'regimens': REGIMENS, 'sources': SOURCES, 'source_checked': '2026-10-27',
            'summaries': summaries, 'input_sha256': digest({'version': VERSION, 'patients': inputs, 'pathways': PATHWAYS}),
            'design': 'The same 120 authored patients are simulated under both sequential pathways. No randomization or causal treatment effect is estimated.',
            'endpoint': 'Illustrative burden index, baseline 100. Not lesion measurements, RECIST, response probability, PFS or OS.',
            'switch_rule': 'At an 8-week review, switch 1L to 2L or 2L to 3L when index rises at least 30% and 15 index units above the current-line nadir. This toy rule is not RECIST. No further line decision is modeled after 3L.',
            'clinical_outcomes': {'objective_response': None, 'pfs': None, 'os': None, 'toxicity': None},
            'limits': ['Regimen names and sequencing have clinical references; every trajectory and effect coefficient is fictional and uncalibrated.',
                       'Scope: fit adults, initially unresectable mCRC, MSS/pMMR, RAS mutant, BRAF V600E wild-type; assume no anti-VEGF contraindication.',
                       'Age and ECOG describe the fixture; they are not fitted treatment modifiers. All effect coefficients are visible in patient details.',
                       'No dosing, PK/PD bridge, maintenance, surgery, treatment interruption, adverse-event model or mortality model.',
                       'Later-line options and real-world eligibility require individual clinical review. This catalogue is not exhaustive.',
                       'Animated intermediate values use linear display interpolation between frozen weekly outputs.']}


def public_regimen_bundle():
    result = regimen_cohort_result()
    return {'schema': 'regimen-cohort-public-v1', 'locale': 'en', 'clinical_use': False,
            'canonical_result': canonical(result), 'sha256': digest(result)}


def regimen_csv(result):
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=['patient_id', 'pathway', 'week', 'line', 'regimen', 'burden_index', 'source_kind', 'model_version'])
    writer.writeheader()
    for row in result['patients']:
        for key, response in row['pathways'].items():
            for p in response['trajectory']:
                writer.writerow({'patient_id': row['patient']['patient_id'], 'pathway': key, 'week': p['week'], 'line': p['line'],
                                 'regimen': result['regimens'][p['regimen_id']]['label'], 'burden_index': p['burden_index'],
                                 'source_kind': 'synthetic', 'model_version': result['model_version']})
    return stream.getvalue()


def public_cohort_overview():
    """Separate presentation summary of all 120 patients; kinetics stay unchanged."""
    from .cohort_lab import percentile
    result = regimen_cohort_result()
    groups = {}
    for key in PATHWAYS:
        groups[key] = []
        for week in range(HORIZON + 1):
            values = [r['pathways'][key]['trajectory'][week]['burden_index'] for r in result['patients']]
            groups[key].append({**result['summaries'][key][week],
                                'q25': round(percentile(values, .25), 6),
                                'q75': round(percentile(values, .75), 6),
                                'below_baseline': sum(v < 100 for v in values)})
    payload = {'schema': 'regimen-cohort-overview-v2', 'source_result_sha256': digest(result),
               'total_patients': result['total_patients'], 'horizon_weeks': HORIZON,
               'displayed_patient_ids': [r['patient']['patient_id'] for r in result['patients'][:20]],
               'selection': 'Fixed patient IDs P001-P020.',
               'summary_definition': 'All 120 patients per pathway; median and inclusive 25th/75th patient percentiles, not confidence intervals.',
               'groups': groups}
    payload['comparison'] = pathway_comparison(result)
    return {'canonical_overview': canonical(payload), 'sha256': digest(payload)}


COMPARISON_STRATA = ('all', 'oxaliplatin', 'irinotecan', 'similar')


def comparison_stratum(patient):
    """Partition on authored baseline inputs, never on observed outcome ranking."""
    c = patient['scenario_coefficients']
    ratio = c['oxaliplatin'] / c['irinotecan']
    return 'oxaliplatin' if ratio >= 1.35 else 'irinotecan' if ratio <= 1/1.35 else 'similar'


def pathway_comparison(result):
    """Descriptive paired contrasts of frozen outputs; no efficacy estimate."""
    from .cohort_lab import percentile
    strata = {}
    for name in COMPARISON_STRATA:
        rows = [r for r in result['patients'] if name == 'all' or comparison_stratum(r['patient']) == name]
        if not rows:
            raise ValueError('Comparison stratum is empty')
        groups = {key: [] for key in PATHWAYS}
        pairs = []
        for week in range(HORIZON + 1):
            for key in PATHWAYS:
                points = [r['pathways'][key]['trajectory'][week] for r in rows]
                values = [p['burden_index'] for p in points]
                groups[key].append({'week': week, 'n': len(rows), 'median_index': round(median(values),6),
                    'q25': round(percentile(values,.25),6), 'q75': round(percentile(values,.75),6),
                    'below_baseline': sum(v<100 for v in values),
                    'line_counts': {str(line):sum(p['line']==line for p in points) for line in (1,2,3)}})
            gaps = [r['pathways']['A']['trajectory'][week]['burden_index'] - r['pathways']['B']['trajectory'][week]['burden_index'] for r in rows]
            pairs.append({'week': week, 'n': len(rows), 'median_a_minus_b': round(median(gaps),6),
                          'a_lower': sum(d < -5 for d in gaps), 'b_lower': sum(d > 5 for d in gaps),
                          'within_5': sum(abs(d)<=5 for d in gaps)})
        strata[name] = {'n': len(rows), 'patient_ids': [r['patient']['patient_id'] for r in rows],
                        'groups': groups, 'paired': pairs}
    return {'definition': 'Paired difference = A index minus B index for each patient; negative means lower in A. Not a causal or clinical efficacy estimate.',
            'grouping': 'Baseline authored coefficient ratio oxaliplatin/irinotecan >=1.35, <=1/1.35, or between. Not measured sensitivity, not outcome-selected.',
            'tolerance': 'Within 5 index units is a display band, not clinical equivalence or a significance test.',
            'strata': strata}
