"""Independent, authored MSI-H/dMMR scenario; no clinical response calibration."""
import math
import random
from .patient_lab import canonical, digest
from .regimen_cohort import HORIZON, regimen_csv

VERSION = 'synthetic-msih-immunotherapy-120-v1'
REGIMENS = {
    'pembrolizumab': {'label': 'Pembrolizumab', 'components': ['pembrolizumab'], 'source_ids': ['fda_pembro']},
    'nivo_ipi': {'label': 'Nivolumab + ipilimumab', 'components': ['nivolumab', 'ipilimumab'], 'source_ids': ['fda_nivo_ipi']},
}
SOURCES = {
    'fda_pembro': {'title': 'FDA: first-line pembrolizumab in MSI-H/dMMR CRC (2020)', 'url': 'https://www.fda.gov/drugs/drug-approvals-and-databases/fda-approves-pembrolizumab-first-line-treatment-msi-hdmmr-colorectal-cancer'},
    'fda_nivo_ipi': {'title': 'FDA: nivolumab + ipilimumab in MSI-H/dMMR CRC (2025)', 'url': 'https://www.fda.gov/drugs/resources-information-approved-drugs/fda-approves-nivolumab-ipilimumab-unresectable-or-metastatic-msi-h-or-dmmr-colorectal-cancer'},
}
PATHWAYS = {'A': {'label': 'PD-1 monotherapy', 'regimens': ['pembrolizumab']},
            'B': {'label': 'Dual checkpoint blockade', 'regimens': ['nivo_ipi']}}


def eligibility(patient, regimen):
    """Catalogue scope only. Unknowns never imply individual clinical suitability."""
    if regimen not in REGIMENS:
        raise ValueError('Unsupported immune regimen')
    if patient.get('disease_setting') != 'unresectable_or_metastatic_crc':
        return {'status': 'insufficient_data', 'reason': 'Unresectable or metastatic CRC setting required.'}
    if type(patient.get('age')) is not int or patient['age'] < 18:
        return {'status': 'insufficient_data', 'reason': 'This authored cohort requires documented adult status.'}
    m = patient.get('molecular_scope', {})
    if (m.get('msi') == 'MSI-H' and m.get('mmr') == 'pMMR') or (m.get('msi') == 'MSS' and m.get('mmr') == 'dMMR'):
        return {'status': 'insufficient_data', 'reason': 'Discordant MSI/MMR results require resolution.'}
    if m.get('msi') != 'MSI-H' and m.get('mmr') != 'dMMR':
        if m.get('msi') == 'MSS' and m.get('mmr') == 'pMMR':
            return {'status': 'not_eligible', 'reason': 'Outside this MSI-H/dMMR immune-treatment catalogue; no MSS trajectory generated.'}
        return {'status': 'insufficient_data', 'reason': 'MSI/MMR confirmation is missing.'}
    if patient.get('prior_ici') is not False or patient.get('treatment_line') != 1:
        return {'status': 'insufficient_data', 'reason': 'This scenario requires first-line, checkpoint-inhibitor-naive patients.'}
    if patient.get('immune_suitability') != 'assumed_for_scenario':
        return {'status': 'insufficient_data', 'reason': 'Individual immune-treatment suitability is unresolved.'}
    return {'status': 'eligible', 'reason': 'Within the US-labelled molecular/disease scope; clinical suitability assumed for this synthetic first-line scenario.'}


def patients():
    rng = random.Random(2026102802)
    rows = []
    for n in range(1, 121):
        # Balanced authored input patterns ensure demonstrable heterogeneity, not prevalence.
        mode = ('early', 'delayed', 'low_effect', 'escape')[(n-1) % 4]
        growth = round(rng.uniform(.012, .027), 6)
        effects = {k: round(rng.uniform(.045, .10) if mode != 'low_effect' else rng.uniform(.002, .009), 6) for k in PATHWAYS}
        delays = {k: round(rng.uniform(8, 15) if mode == 'delayed' else rng.uniform(0, 3), 6) for k in PATHWAYS}
        escape = {k: rng.randint(20, 38) if mode == 'escape' else None for k in PATHWAYS}
        rows.append({'patient_id': f'I{n:03}', 'source_kind': 'synthetic', 'age': rng.randint(40, 78),
                     'ecog': rng.choice([0, 1]), 'metastatic_sites': rng.choice([['liver'], ['lung'], ['peritoneum'], ['liver', 'lung']]),
                     'disease_setting': 'unresectable_or_metastatic_crc',
                     'molecular_scope': {'msi': 'MSI-H', 'mmr': 'dMMR'}, 'treatment_line': 1,
                     'prior_ici': False, 'immune_suitability': 'assumed_for_scenario', 'input_pattern': mode,
                     'pretreatment_index': [{'week': w, 'index': round(100*math.exp(growth*w), 6)} for w in (-8, -4, 0)],
                     'scenario_coefficients': {'effect_per_week': effects, 'activation_delay_weeks': delays,
                                               'escape_week': escape, 'escape_rate': round(rng.uniform(.07, .14), 6)},
                     'coefficient_origin': 'Authored synthetic inputs. Patterns and frequencies are not clinical biomarkers or prevalence estimates.'})
    return rows


def trajectory(patient, pathway):
    if pathway not in PATHWAYS:
        raise ValueError('Unsupported immune pathway')
    regimen = PATHWAYS[pathway]['regimens'][0]
    if eligibility(patient, regimen)['status'] != 'eligible':
        raise ValueError('Cannot simulate an ineligible or unresolved patient')
    growth = math.log(patient['pretreatment_index'][-1]['index']/patient['pretreatment_index'][0]['index'])/8
    c, burden, nadir = patient['scenario_coefficients'], 100., 100.
    rows = []
    for week in range(HORIZON+1):
        if week:
            t = week-.5
            activation = 1/(1+math.exp(-(t-c['activation_delay_weeks'][pathway])/.9))
            escape = c['escape_week'][pathway]
            persistence = math.exp(-c['escape_rate']*max(0, t-escape)) if escape is not None else 1
            effect = c['effect_per_week'][pathway]*activation*persistence
            burden *= math.exp(growth-effect)
            nadir = min(nadir, burden)
        # Descriptive burden bands only: never RECIST or clinical progression.
        status = 'rebound' if burden >= nadir*1.3 and burden-nadir >= 15 else 'below_baseline' if burden < 95 else 'above_baseline' if burden > 105 else 'near_baseline'
        rows.append({'week': week, 'burden_index': round(burden, 6), 'line': 1,
                     'regimen_id': regimen, 'display_status': status})
    return {'trajectory': rows, 'switches': []}


def immunotherapy_result():
    inputs = patients()
    rows = [{'patient': p, 'eligibility': {k: eligibility(p, PATHWAYS[k]['regimens'][0]) for k in PATHWAYS},
             'pathways': {k: trajectory(p, k) for k in PATHWAYS}} for p in inputs]
    return {'schema_version': 'immune-cohort-v1', 'model_version': VERSION, 'source_kind': 'synthetic',
            'clinical_use': False, 'scientific_review': 'not_performed', 'total_patients': 120,
            'horizon_weeks': HORIZON, 'patients': rows, 'pathways': PATHWAYS, 'regimens': REGIMENS,
            'sources': SOURCES, 'source_checked': '2026-10-28', 'regulatory_scope': 'US FDA; not a jurisdiction-independent prescribing catalogue.',
            'input_sha256': digest({'version': VERSION, 'patients': inputs, 'pathways': PATHWAYS}),
            'endpoint': 'Authored burden index, baseline 100; not RECIST, response probability, PFS, OS or toxicity.',
            'design': 'Same 120 synthetic patients under two first-line immune scenarios. No causal efficacy or clinical ranking. Independent cohort from the MSS chemotherapy scene.',
            'clinical_outcomes': {'objective_response': None, 'pfs': None, 'os': None, 'toxicity': None},
            'limits': ['Regulatory sources constrain names and population only, never effect sizes. No head-to-head pembrolizumab versus nivolumab/ipilimumab estimate is imported.',
                       'Four equally represented authored input patterns demonstrate early activation, delayed activation, low effect and effect escape; frequencies are not clinical prevalence.',
                       'Age, ECOG, metastatic sites and MSI/MMR are descriptors or eligibility gates, not fitted response modifiers. Immune activation and escape are fictional latent inputs.',
                       'No pseudoprogression diagnosis, dose schedule, immune toxicity, treatment discontinuation, subsequent treatment, survival or uncertainty calibration is modeled.',
                       'Curves are hypothetical continued-exposure trajectories, including after rebound. A rebound flag is not a treatment recommendation.',
                       'Intermediate values interpolate frozen weekly outputs. These two counterfactual scenarios cannot both be observed in a real patient.']}


def public_immune_bundle():
    result = immunotherapy_result()
    return {'schema': 'immune-cohort-public-v1', 'clinical_use': False,
            'canonical_result': canonical(result), 'sha256': digest(result)}


def immune_csv(result):
    return regimen_csv(result)
