import csv
import io
import math
import json
from copy import deepcopy
import pytest
from vbt.immunotherapy_cohort import immunotherapy_result, eligibility, trajectory, public_immune_bundle, immune_csv
from vbt.patient_lab import canonical, digest
from vbt.patient_exports import verify_export


@pytest.fixture(scope='module')
def result():
    return immunotherapy_result()


def test_complete_independent_deterministic_cohort(result):
    assert result == immunotherapy_result()
    assert result['total_patients'] == len(result['patients']) == 120
    assert len({r['patient']['patient_id'] for r in result['patients']}) == 120
    assert not result['clinical_use'] and result['scientific_review'] == 'not_performed'
    assert all(v is None for v in result['clinical_outcomes'].values())
    assert result['patients'][0]['patient']['patient_id'] == 'I001'
    for row in result['patients']:
        assert row['patient']['molecular_scope'] == {'msi': 'MSI-H', 'mmr': 'dMMR'}
        for key, response in row['pathways'].items():
            ps=response['trajectory']
            assert len(ps)==73 and ps[0]['burden_index']==100
            assert all(p['week']==w and math.isfinite(p['burden_index']) and p['burden_index']>0 for w,p in enumerate(ps))
            assert all(p['line']==1 for p in ps) and response['switches']==[]
            assert row['eligibility'][key]['status']=='eligible'
    for mode in ('early','delayed','low_effect','escape'):
        assert sum(r['patient']['input_pattern']==mode for r in result['patients'])==30


def test_input_dependence_symmetry_and_heterogeneity(result):
    p=deepcopy(result['patients'][0]['patient'])
    for field in ('effect_per_week','activation_delay_weeks','escape_week'):
        p['scenario_coefficients'][field]['B']=p['scenario_coefficients'][field]['A']
    a=trajectory(p,'A')['trajectory'];b=trajectory(p,'B')['trajectory']
    assert [v['burden_index'] for v in a]==[v['burden_index'] for v in b]
    p['scenario_coefficients']['effect_per_week']['A']*=1.5
    assert trajectory(p,'A')['trajectory'][24]['burden_index']<a[24]['burden_index']
    # Independent one-step equation from pretreatment and authored coefficients.
    r=result['patients'][0];p=r['patient'];c=p['scenario_coefficients']
    growth=math.log(100/p['pretreatment_index'][0]['index'])/8
    effect=c['effect_per_week']['A']/(1+math.exp(-(.5-c['activation_delay_weeks']['A'])/.9))
    assert r['pathways']['A']['trajectory'][1]['burden_index']==round(100*math.exp(growth-effect),6)
    early=[r for r in result['patients'] if r['patient']['input_pattern']=='early']
    delayed=[r for r in result['patients'] if r['patient']['input_pattern']=='delayed']
    low=[r for r in result['patients'] if r['patient']['input_pattern']=='low_effect']
    escape=[r for r in result['patients'] if r['patient']['input_pattern']=='escape']
    assert all(r['pathways']['A']['trajectory'][8]['burden_index']<100 for r in early)
    assert all(r['pathways']['A']['trajectory'][4]['burden_index']>100 for r in delayed)
    assert all(r['pathways']['A']['trajectory'][72]['burden_index']>100 for r in low)
    assert any(r['pathways']['A']['trajectory'][72]['display_status']=='rebound' for r in escape)
    gaps=[r['pathways']['A']['trajectory'][24]['burden_index']-r['pathways']['B']['trajectory'][24]['burden_index'] for r in result['patients']]
    assert any(g < -5 for g in gaps) and any(g > 5 for g in gaps)


@pytest.mark.parametrize('regimen',['pembrolizumab','nivo_ipi'])
def test_eligibility_fails_closed(result,regimen):
    p=deepcopy(result['patients'][0]['patient'])
    assert eligibility(p,regimen)['status']=='eligible'
    p['molecular_scope']={'msi':'MSS','mmr':'pMMR'}
    assert eligibility(p,regimen)['status']=='not_eligible'
    with pytest.raises(ValueError):trajectory(p,'A')
    p['molecular_scope']={}
    assert eligibility(p,regimen)['status']=='insufficient_data'
    p=deepcopy(result['patients'][0]['patient']);p['immune_suitability']=None
    assert eligibility(p,regimen)['status']=='insufficient_data'
    p=deepcopy(result['patients'][0]['patient']);p['prior_ici']=True
    assert eligibility(p,regimen)['status']=='insufficient_data'
    p=deepcopy(result['patients'][0]['patient']);p['molecular_scope']['mmr']='pMMR'
    assert eligibility(p,regimen)['status']=='insufficient_data'
    p=deepcopy(result['patients'][0]['patient']);p['age']=None
    assert eligibility(p,regimen)['status']=='insufficient_data'
    with pytest.raises(ValueError):eligibility(p,'unknown')


def envelope(result):
    return {'run_id':'8c7e4366-e077-4bf5-8a31-8471241e5a90','created_at':'2026-10-28T02:00:00Z',
            'canonical_template':canonical(result),'template_sha256':digest(result),'result':result,
            'view':{'week':24.375,'highlight_patient':'I120','pattern':'all'}}


def test_export_reproduction_csv_and_tampering(result):
    bundle=public_immune_bundle()
    assert bundle['canonical_result']==canonical(result) and bundle['sha256']==digest(result)
    assert verify_export(envelope(result))==result
    rows=list(csv.DictReader(io.StringIO(immune_csv(result))))
    assert len(rows)==17520
    for row in rows:
        p=result['patients'][int(row['patient_id'][1:])-1]['pathways'][row['pathway']]['trajectory'][int(row['week'])]
        assert float(row['burden_index'])==p['burden_index']
    bad=deepcopy(result);bad['patients'][0]['pathways']['A']['trajectory'][3]['burden_index']+=1
    with pytest.raises(ValueError):verify_export(envelope(bad))


@pytest.mark.parametrize('change',[{'week':True},{'week':float('nan')},{'week':73},{'highlight_patient':'P001'},{'pattern':'responders'}])
def test_invalid_view_rejected(result,change):
    record=envelope(result);record['view'].update(change)
    with pytest.raises(ValueError):verify_export(record)
