import json
import re
from statistics import median
import pytest
from vbt.cohort_lab import cohort_result, public_cohort_bundle, percentile
from vbt.molecule_lab import simulate_molecule
from vbt.patient_lab import canonical, digest


def test_cohort_same_patient_denominators_missing_never_zero():
    r=cohort_result()
    assert len(r['patients']) == r['total_patients'] == 24
    assert len({p['patient']['patient_id'] for p in r['patients']}) == 24
    assert len(r['paired_patient_ids']) == 22
    assert 'P003' not in r['paired_patient_ids'] and 'P014' not in r['paired_patient_ids']
    for molecule in r['molecules']:
        assert all(s['n']==22 for s in r['summaries']['paired'][molecule])
        assert all(s['n']==23 for s in r['summaries']['available'][molecule])
    assert r['patients'][2]['responses']['oxaliplatin']['trajectory']==[]
    assert r['clinical_outcomes']=={'objective_response':None,'pfs':None,'os':None,'toxicity':None}
    assert not r['clinical_use']


def test_cohort_individual_results_match_existing_engine():
    for cutoff in (3,5,7):
        cohort=cohort_result(cutoff)
        for row in cohort['patients'][:3]:
            for molecule,response in row['responses'].items():
                assert response==simulate_molecule(row['patient']['patient_id'],molecule,cutoff)['response']


def test_patient_is_independent_summary_unit_and_percentiles_match():
    r=cohort_result()
    paired=set(r['paired_patient_ids'])
    for molecule in r['molecules']:
        for day in range(11):
            values=[p['responses'][molecule]['trajectory'][day]['viability_percent'] for p in r['patients'] if p['patient']['patient_id'] in paired]
            s=r['summaries']['paired'][molecule][day]
            assert s['n']==len(values)
            assert s['median']==round(median(values),6)
            assert s['q25']==round(percentile(values,.25),6)
            assert s['q75']==round(percentile(values,.75),6)
    assert percentile([10,20,30,40],.25)==17.5
    assert percentile([],.25) is None


def test_cohort_frozen_deterministic_cutoff_and_short_extrapolation():
    first=cohort_result(3)
    assert first==cohort_result(3)
    assert first['input_sha256']!=cohort_result(7)['input_sha256']
    for row in first['patients']:
        for assay in row['patient']['assays'].values():
            assert all(r['day']<=3 and r['available_day']<=3 for r in assay['rows'])
        for response in row['responses'].values():
            if response['status']=='estimable':
                assert len(response['trajectory'])==7
                assert response['trajectory'][-1]['kind']=='extrapolated'
                assert response['trajectory'][0]['viability_percent']==100


@pytest.mark.parametrize('cutoff',[0,4,8,True,'7'])
def test_bad_cutoffs_fail(cutoff):
    with pytest.raises(ValueError):cohort_result(cutoff)


def test_bundle_checksums_english_and_no_second_browser_model():
    bundle=public_cohort_bundle()
    for cutoff,template in bundle['templates'].items():
        r=cohort_result(int(cutoff))
        assert template=={'canonical_result':canonical(r),'sha256':digest(r)}
    assert not re.search(r'[\u3400-\u9fff]',json.dumps(bundle,ensure_ascii=False))
