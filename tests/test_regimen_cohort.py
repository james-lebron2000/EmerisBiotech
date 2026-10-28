import csv
import io
import json
import math
from statistics import median
import pytest
from vbt.regimen_cohort import (patients, trajectory, regimen_cohort_result, public_regimen_bundle,
                               regimen_csv, PATHWAYS, REGIMENS, VERSION)
from vbt.patient_lab import canonical, digest
from vbt.patient_exports import verify_export


@pytest.fixture(scope='module')
def result():
    return regimen_cohort_result()


def test_complete_paired_120_and_clinical_boundary(result):
    assert result['total_patients']==len(result['patients'])==120
    assert len({r['patient']['patient_id'] for r in result['patients']})==120
    assert result['patients'][-1]['patient']['patient_id']=='P120'
    assert result['model_version']==VERSION and not result['clinical_use']
    assert all(v is None for v in result['clinical_outcomes'].values())
    assert result==regimen_cohort_result()
    for row in result['patients']:
        assert row['patient']['source_kind']=='synthetic'
        assert row['patient']['molecular_scope']=={'mmr':'pMMR','msi':'MSS','ras':'mutant','braf_v600e':'wild-type'}
        for key,response in row['pathways'].items():
            points=response['trajectory']
            assert len(points)==73 and points[0]['burden_index']==100
            assert all(math.isfinite(p['burden_index']) and p['burden_index']>0 for p in points)
            assert [p['week'] for p in points]==list(range(73))
            assert all(p['regimen_id']==PATHWAYS[key]['regimens'][p['line']-1] for p in points)
            assert all(e['week']%8==0 for e in response['switches'])
            assert [e['line'] for e in response['switches']] in ([],[2],[2,3])
            for event in response['switches']:
                prior=[p['burden_index'] for p in points[:event['week']] if p['line']==event['line']-1]
                event_index=points[event['week']]['burden_index']
                assert event_index>=min(prior)*1.3-1e-5 and event_index-min(prior)>=15-1e-5
                if event['line']==3:
                    prior={p['regimen_id'] for p in points[:event['week']]}
                    assert 'folfox_bev' in prior and prior.intersection({'folfiri_bev','folfiri_afl'})


def test_patient_level_summaries_independently_match(result):
    for key in PATHWAYS:
        for week in (0,8,24,48,72):
            points=[r['pathways'][key]['trajectory'][week] for r in result['patients']]
            s=result['summaries'][key][week]
            assert s['n']==120
            assert s['median_index']==round(median(p['burden_index'] for p in points),6)
            assert sum(s['line_counts'].values())==120
            assert s['line_counts']=={str(line):sum(p['line']==line for p in points) for line in (1,2,3)}
    assert result['summaries']['A'][-1]['line_counts']['3']>0
    assert result['summaries']['A'][-1]['line_counts']['1']>0


def test_kinetics_depend_on_patient_inputs_without_pathway_bonus():
    patient=patients()[0]
    patient['scenario_coefficients']['irinotecan']=patient['scenario_coefficients']['oxaliplatin']
    assert trajectory(patient,'A')['trajectory']==[dict(p,regimen_id=PATHWAYS['A']['regimens'][p['line']-1]) for p in trajectory(patient,'B')['trajectory']]
    faster=patients()[0]
    baseline=trajectory(faster,'A')['trajectory'][8]['burden_index']
    faster['scenario_coefficients']['oxaliplatin']*=2
    assert trajectory(faster,'A')['trajectory'][8]['burden_index']<baseline
    with pytest.raises(ValueError):trajectory(patient,'unknown')


def envelope(result):
    return {'run_id':'8c7e4366-e077-4bf5-8a31-8471241e5a90','created_at':'2026-10-27T05:00:00Z',
            'canonical_template':canonical(result),'template_sha256':digest(result),'result':result,
            'view':{'week':31.375,'highlight_patient':'P120','order':'id'}}


def test_export_round_trip_csv_and_checksum(result):
    bundle=public_regimen_bundle()
    assert bundle['canonical_result']==canonical(result) and bundle['sha256']==digest(result)
    assert verify_export(envelope(result))==result
    rows=list(csv.DictReader(io.StringIO(regimen_csv(result))))
    assert len(rows)==120*2*73
    for row in rows:
        point=result['patients'][int(row['patient_id'][1:])-1]['pathways'][row['pathway']]['trajectory'][int(row['week'])]
        assert float(row['burden_index'])==point['burden_index']
        assert row['regimen']==REGIMENS[point['regimen_id']]['label']
    assert not any('\u3400'<=c<='\u9fff' for c in json.dumps(bundle,ensure_ascii=False))


@pytest.mark.parametrize('change',[{'week':True},{'week':float('nan')},{'week':73},{'highlight_patient':'P121'},{'order':'efficacy'}])
def test_invalid_export_view_rejected(result,change):
    record=envelope(result);record['view'].update(change)
    with pytest.raises(ValueError):verify_export(record)


def test_overview_uses_all_120_while_display_selection_stays_fixed(result):
    from statistics import quantiles
    from vbt.regimen_cohort import public_cohort_overview
    envelope=public_cohort_overview()
    overview=json.loads(envelope['canonical_overview'])
    assert digest(overview)==envelope['sha256']
    assert overview['source_result_sha256']==digest(result)
    assert overview['total_patients']==120
    assert overview['displayed_patient_ids']==[f'P{i:03}' for i in range(1,21)]
    for key in PATHWAYS:
        for week in range(73):
            values=[row['pathways'][key]['trajectory'][week]['burden_index'] for row in result['patients']]
            q25,_,q75=quantiles(values,n=4,method='inclusive')
            point=overview['groups'][key][week]
            assert point['n']==120 and point['median_index']==round(median(values),6)
            # Independent algorithms may straddle a binary rounding tie at the sixth decimal.
            assert point['q25']==pytest.approx(q25,rel=0,abs=0.50001e-6)
            assert point['q75']==pytest.approx(q75,rel=0,abs=0.50001e-6)
            assert point['below_baseline']==sum(v<100 for v in values)
        ten=[row['pathways'][key]['trajectory'][72]['burden_index'] for row in result['patients'][:10]]
        assert overview['groups'][key][72]['median_index']!=round(median(ten),6)


def test_paired_comparison_strata_are_input_defined_and_exhaustive(result):
    from vbt.regimen_cohort import public_cohort_overview, comparison_stratum
    overview=json.loads(public_cohort_overview()['canonical_overview'])
    strata=overview['comparison']['strata']
    assert overview['schema']=='regimen-cohort-overview-v2'
    ids=[pid for k in ('oxaliplatin','irinotecan','similar') for pid in strata[k]['patient_ids']]
    assert len(ids)==len(set(ids))==120
    for key,group in strata.items():
        selected=[row for row in result['patients'] if row['patient']['patient_id'] in group['patient_ids']]
        assert group['n']==len(selected)
        if key!='all':assert all(comparison_stratum(row['patient'])==key for row in selected)
        for week in (0,12,24,48,72):
            gaps=[row['pathways']['A']['trajectory'][week]['burden_index']-row['pathways']['B']['trajectory'][week]['burden_index'] for row in selected]
            paired=group['paired'][week]
            assert paired['median_a_minus_b']==round(median(gaps),6)
            assert paired['a_lower']==sum(v < -5 for v in gaps)
            assert paired['b_lower']==sum(v > 5 for v in gaps)
            assert paired['within_5']==sum(-5<=v<=5 for v in gaps)
            assert paired['a_lower']+paired['b_lower']+paired['within_5']==group['n']
            for path in PATHWAYS:
                values=[r['pathways'][path]['trajectory'][week]['burden_index'] for r in selected]
                assert group['groups'][path][week]['median_index']==round(median(values),6)
    # Opposing input-defined effects exist without changing the frozen model or choosing outcomes.
    assert strata['oxaliplatin']['paired'][24]['median_a_minus_b'] < -20
    assert strata['irinotecan']['paired'][24]['median_a_minus_b'] > 20
    assert abs(strata['all']['paired'][24]['median_a_minus_b']) < 5
    record=envelope(result);record['view']['comparison_stratum']='oxaliplatin'
    assert verify_export(record)==result
    record['view']['comparison_stratum']='invented'
    with pytest.raises(ValueError):verify_export(record)
