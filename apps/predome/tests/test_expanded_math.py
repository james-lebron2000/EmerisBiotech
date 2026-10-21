import math
import pytest
from predome_prior import effect_features,validate_prior
from predome_expanded import contract

def effect(metric='HR',level=.90):
    return {'effect_measure':metric,'effect_estimate':.58,'confidence_interval':[.41,.81],'confidence_level':level,'lower_effect_favors_experimental':True}

def test_actual_confidence_level_changes_se():
    a,b=effect_features(effect()),effect_features(effect(level=.95))
    assert a['critical_value']==pytest.approx(1.64485362695)
    assert b['derived_se']/a['derived_se']==pytest.approx(.8392267,rel=1e-5)
    assert a['prior_benefit_z']>0

def test_difference_is_not_a_log_ratio():
    r={'effect_measure':'mean_difference','effect_estimate':.99,'confidence_interval':[.30,1.68],'confidence_level':.90,'lower_effect_favors_experimental':False}
    f=effect_features(r)
    assert f['prior_logratio_se'] is None
    assert f['derived_se']==pytest.approx(.69/1.64485362695)
    assert f['prior_benefit_z']==pytest.approx(.99/f['derived_se'])

def test_invalid_effect_not_imputed_as_zero():
    r=effect();r['confidence_interval']=[-.1,.8]
    with pytest.raises(ValueError):effect_features(r)
    r=effect();r['confidence_level']=.8
    with pytest.raises(ValueError):effect_features(r)

def test_contract_preserves_seen_test_and_no_calibration():
    p=contract()
    assert len(p)==4
    assert not p['protocol.json']['test_policy']['new_independent_test']
    assert len(p['protocol.json']['test_policy']['locked_replay_ncts'])==8
    assert p['protocol.json']['new_candidates_selected_on_outcome']

def test_prior_clock_target_leakage_and_tier_exclusion():
    from predome_prior import DATA
    import json
    records=json.loads((DATA/'prior_records.json').read_text())['records']
    r=next(r for r in records if r['record_id']=='canopy1_aacr2022_os')
    assert validate_prior(r,'NCT03447769','2022-08-08')[0]
    assert validate_prior(r,'NCT03447769','2021-01-01')[1]=='source_after_forecast_cutoff'
    assert validate_prior(r,r['prior_nct_id'],'2022-08-08')[1]=='same_target_result'
    r=next(r for r in records if r['record_id']=='cantos_lung_incidence_hypothesis')
    assert validate_prior(r,'NCT03626545','2021-03-02')[1]=='ineligible_tier_or_design'

def test_frozen_replay_asset_folds_and_coverage_are_honest():
    from predome_prior import DATA
    from predome_pilot import ROOT,sha,audit_rows,partition
    import json
    rows=json.loads((DATA/'dataset.json').read_text());r=json.loads((DATA/'runs/expanded-v1/report.json').read_text())
    assert sha(rows)==r['dataset_sha256']
    accepted,_=audit_rows(rows);train,test,_=partition(accepted)
    assert len(train)==18 and len(test)==8 and len(accepted)==26
    assert not {x['asset_group'] for x in train}&{x['asset_group'] for x in test}
    assert not r['new_independent_test'] and not r['formal_training_completed']
    assert len({x['nct_id'] for x in r['training_cv']['predictions']})==18
    byid={x['nct_id']:x for x in train}
    for f in r['training_cv']['folds']:
        assert not {byid[i]['asset_group'] for i in f['training_ncts']}&{byid[i]['asset_group'] for i in f['held_ncts']}
    for row in accepted:
        assert row['features_as_of']<=row['forecast_as_of']<row['first_result_at']
        if row.get('selected_prior_record'):
            assert validate_prior(row['selected_prior_record'],row['nct_id'],row['forecast_as_of'])[0]
            derived=effect_features(row['selected_prior_record'])
            assert set(row['selected_prior_math'])==set(derived)
            for key,value in derived.items():
                actual=row['selected_prior_math'][key]
                if type(value) is float: assert actual==pytest.approx(value,rel=1e-12,abs=1e-14)
                else: assert actual==value
    c=json.loads((DATA/'runs/expanded-v1/comparison.json').read_text())
    assert c['common_answered']==6 and all(v['n']==6 for v in c['metrics'].values())
    coverage=json.loads((DATA/'runs/expanded-v1/coverage_audit.json').read_text())
    assert coverage['common_subset_labels']=={'success':1,'failure':5}
    assert coverage['abstained_labels']=={'success':2,'failure':0}
