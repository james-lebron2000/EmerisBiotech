"""Leakage/target guard regression; synthetic cases are never efficacy evidence."""
import json
import pytest
import continuous_audit as audit
import continuous_model as model
from continuous_ensemble import classification,guard_oof,learn,target70
from continuous_time import time_split,result_interval

def row(n,date,asset,eligible=True,label=0):
    return {'nct_id':n,'decisive_primary_at':date,'asset_group':asset,'investigational_components':[asset],
            'eligible':eligible,'label':label}

def test_unknown_later_asset_purges_training_and_seen_components():
    rows=[row('old','2021-02-02','shared'),row('unknown','2025-02-02','shared',False,None),
          row('new','2025-03-01','combo')]
    rows[-1]['investigational_components']=['seen-core','another-core']
    groups,purged=model.partition(rows,set(),{'seen-core'})
    assert not groups['train']
    assert purged[0]['nct_id']=='old'
    assert groups['seen_replay'][0]['nct_id']=='new'
    assert len(groups['excluded'])==2

def test_date_interval_does_not_invent_a_day_or_cross_boundary():
    r={'decisive_primary_at':None,'decisive_primary_interval':{'lower':'2022-09-01','upper_exclusive':'2022-10-01','precision':'month'}}
    assert time_split(r)=='train'
    assert r['decisive_primary_at'] is None
    r['decisive_primary_interval']['upper_exclusive']='2023-01-02'
    assert time_split(r)=='excluded'
    with pytest.raises(ValueError):result_interval({'decisive_primary_at':'2022-09-01','decisive_primary_interval':r['decisive_primary_interval']})

def test_majority_trap_is_not_70_success():
    rows=[row(str(i),'2025-01-01','asset'+str(i),label=int(i<80)) for i in range(100)]
    result=target70(rows,[.8]*100,.5)
    assert result['all_assessable_test']['accuracy']==.8
    assert result['all_assessable_test']['balanced_accuracy']==.5
    assert not result['point_target_met']
    assert not result['stable70_lower_bound_passed']

def test_perfect_tiny_correlated_cohort_is_not_stable70():
    rows=[row(str(i),'2025-01-01','one-asset',label=i%2) for i in range(10)]
    r=target70(rows,[float(i%2) for i in range(10)],.5)
    assert r['point_target_met']
    assert not r['stable70_lower_bound_passed']
    assert r['asset_count']==1

def test_one_class_balanced_accuracy_unknown_not_zero():
    assert classification([1,1],[.7,.8])['balanced_accuracy'] is None

def test_oof_guard_and_convex_recipe():
    assert guard_oof([row('x','2022-01-01','a')])
    rows=[row(str(i),'2022-01-01','a'+str(i//2),label=i%2) for i in range(60)]
    for r in rows:r['probabilities']={'signal':.9 if r['label'] else .1,'bad':.1 if r['label'] else .9}
    recipe=learn(rows,['signal','bad'])
    assert recipe['status']=='fitted'
    assert recipe['weights']['signal']>.99
    assert sum(recipe['weights'].values())==pytest.approx(1)
    assert recipe['meta_fit_is_not_validation']
    assert recipe['threshold_from'].startswith('same training-only OOF')

def test_calibration_guards_not_relaxed():
    rows=[row(str(i),'2023-01-01','a'+str(i),label=i%2) for i in range(48)]
    assert 'calibration_n_below_50' in model.check_calibration(rows,[i/100 for i in range(48)])
    rows.extend([row('48','2023-01-01','a48',label=0),row('49','2023-01-01','a49',label=1)])
    assert not model.check_calibration(rows,[i/100 for i in range(50)])
    assert 'complete_separation' in model.check_calibration(rows,[float(r['label']) for r in rows])

def test_fit_rejects_missing_source_audit_before_creating_output(tmp_path):
    out=tmp_path/'run'
    with pytest.raises(ValueError,match='Source audit'):model.fit([],{'nct_ids':[],'asset_groups':[]},out)
    assert not out.exists()

@pytest.fixture
def source_case(tmp_path,monkeypatch):
    monkeypatch.setattr(audit,'ROOT',tmp_path);monkeypatch.setattr(audit,'DATA',tmp_path)
    for name in audit.CONTRACTS:(tmp_path/name).write_text('{}')
    source=tmp_path/'source.json';source.write_text('{}')
    desc={'path':'source.json','sha256':audit.digest(source),'published_at':'2020-01-01'}
    old={'nct_id':'NCT00000001','features_as_of':'2020-01-01','feature_sources':{k:desc for k in ['studies','designs','design_outcomes','conditions','interventions','sponsors']},'primary_endpoints_raw':[{}],'conditions_raw':[],'enrollment_type_raw':'Actual'}
    universe=tmp_path/'universe.json';universe.write_text(json.dumps({'records':[old]}))
    from continuous_dataset import features
    r=row(old['nct_id'],'2021-01-01','asset');r.update(features_as_of='2020-01-01',baseline_record_sha256=audit.canonical(old),historical_target_match='matched',label_status='source_adjudicated_primary',target_success_rule='one primary under specified alpha',source_neutral_id='neutral-1',source_sha256='a'*64,features=features(old),asset_mapping_status='independently_reviewed_investigational_core')
    seen=tmp_path/'seen.json';seen.write_text('{"nct_ids":[],"asset_groups":[]}')
    receipt={'dataset_sha256':audit.canonical([r]),'contract_hashes':audit.contract_hashes(),'status':'source_clock_checks_passed','universe':{'path':'universe.json','sha256':audit.digest(universe)},'seen':{'path':'seen.json','sha256':audit.digest(seen)},'scaffold':desc,'feature_extractor_sha256':audit.digest(__import__('continuous_dataset').__file__)}
    return r,receipt

def test_source_guard_rechecks_sources_and_actual_enrollment(source_case,tmp_path):
    r,receipt=source_case
    assert audit.validate([r],receipt)['eligible_n']==1
    r['features']['planned_enrollment']=100;receipt['dataset_sha256']=audit.canonical([r])
    with pytest.raises(ValueError,match='Actual enrollment'):audit.validate([r],receipt)
    r['features']['planned_enrollment']=None;receipt['dataset_sha256']=audit.canonical([r])
    (tmp_path/'source.json').write_text('{"changed":true}')
    with pytest.raises(ValueError,match='Source hash'):audit.validate([r],receipt)

def test_source_guard_rejects_prior_at_target_or_after_baseline(source_case,tmp_path):
    r,receipt=source_case
    p=tmp_path/'prior.json';p.write_text('{}')
    r['selected_prior_record']={'randomized':True,'prior_nct_id':'NCT99999999','source':{'local_path':'prior.json','sha256':audit.digest(p),'publication_date':'2020-01-02'}}
    selection={'target_nct_id':r['nct_id'],'record':r['selected_prior_record']};r['prior_selection_sha256']=audit.canonical(selection)
    selections=tmp_path/'priors.json';selections.write_text(json.dumps({'selections':[selection]}));receipt['prior_selections']={'path':'priors.json','sha256':audit.digest(selections)}
    receipt['dataset_sha256']=audit.canonical([r])
    with pytest.raises(ValueError,match='after baseline'):audit.validate([r],receipt)

@pytest.mark.parametrize('test_n',[0,4])
def test_end_to_end_freezes_before_test_label_parse(tmp_path,monkeypatch,test_n):
    """Exercise preprocessing, asset OOF, recipe, sealing, scoring in one run.
    Tiny trees shorten this engineering test; these are not research results.
    """
    import hashlib
    from pathlib import Path
    monkeypatch.setattr(audit,'validate',lambda rows,receipt,seen=None:{'status':'synthetic_test_only'})
    p=model.protocol()
    for name,spec in p['models'].items():
        if 'extra_trees' in name:spec['n_estimators']=2
    monkeypatch.setattr(model,'protocol',lambda:p)
    rows=[]
    for split,n,date in [('train',60,'2022-01-01'),('cal',50,'2023-01-01'),('test',test_n,'2025-01-01')]:
        for i in range(n):
            r=row(split+str(i),date,split+'asset'+str(i//2),label=None if split=='test' else i%2)
            r['features']={'planned_enrollment':100+(i*17)%101,'phase':3,'primary_endpoint_count':1+(i%3),
                           'randomized':1,'placebo_controlled':i%2,'therapeutic_area':'oncology',
                           'endpoint_family':'survival','modality':None,'comparator_type':'active'}
            rows.append(r)
    out=tmp_path/'run';seal=tmp_path/'sealed.json';commit=tmp_path/'commit.json'
    seal.write_text(json.dumps({'labels':[{'nct_id':'test'+str(i),'label':i%2} for i in range(test_n)]}))
    commit.write_text(json.dumps({'labels_file_sha256':hashlib.sha256(seal.read_bytes()).hexdigest(),'test_ncts':['test'+str(i) for i in range(test_n)]}))
    read=Path.read_text;opened=[]
    def checked_read(path,*args,**kwargs):
        if path==seal:
            assert (out/'models.pkl').exists()
            frozen=json.loads(read(out/'fit_freeze.json'))
            assert frozen['primary_candidate']=='asset_OOF_convex_ensemble'
            assert frozen['ensemble_recipe']['threshold_from'].startswith('same training-only OOF')
            opened.append(True)
        return read(path,*args,**kwargs)
    monkeypatch.setattr(Path,'read_text',checked_read)
    report=model.fit(rows,{'nct_ids':[],'asset_groups':[]},out,seal,commit,{'seen':{'path':'synthetic-seen-unit-only','sha256':'synthetic'}})
    assert report['status']==('retrospective_heldout_complete' if test_n else 'trained_no_eligible_heldout')
    assert len(opened)==(1 if test_n else 0)
    assert report['ensemble_training']['status']=='fitted'
    assert report['counts']['test']['n']==test_n
    if test_n:
        assert report['metrics']['asset_OOF_convex_ensemble']['n']==test_n
        assert report['performance_goal']['prediction_coverage_on_assessable_test']==1
        assert not report['performance_goal']['stable70_lower_bound_passed']
    else:assert not report['metrics']
    assert all(r['label'] is None for r in rows if r['nct_id'].startswith('test'))

def test_seal_guard_cannot_be_disabled_by_python_optimization(tmp_path):
    import subprocess,sys
    script="""
import sys,json
from pathlib import Path
import continuous_audit,continuous_model
continuous_audit.validate=lambda *args:{'status':'synthetic'}
p=Path(sys.argv[1]);seal=p/'seal.json';seal.write_text('{"labels":[]}')
commit=p/'commit.json';commit.write_text(json.dumps({'labels_file_sha256':'incorrect','test_ncts':['t']}))
r={'nct_id':'t','eligible':True,'decisive_primary_at':'2025-01-01','asset_group':'a','label':None}
try:continuous_model.fit([r],{'nct_ids':[],'asset_groups':[]},p/'run',seal,commit,{})
except ValueError as e:
    if 'seal hash mismatch' not in str(e):raise
    print('optimization_guard_active')
else:raise RuntimeError('Seal guard disabled')
"""
    result=subprocess.run([sys.executable,'-O','-c',script,str(tmp_path)],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    assert 'optimization_guard_active' in result.stdout
