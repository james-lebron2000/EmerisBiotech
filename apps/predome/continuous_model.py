"""Fixed-window temporal research benchmark, separate calibration, no test tuning.
All features must precede each record's earliest qualifying annual baseline.
No outcome text enters X. Test labels are read only after model freeze.
"""
from pathlib import Path
from datetime import datetime,timezone
import json,hashlib,pickle,math
from predome_pilot import NUMERIC,CATEGORICAL,metrics,sha,write_new
from predome_prior import effect_features
from continuous_time import time_split
ROOT=Path(__file__).resolve().parent;DATA=ROOT/'predome_data/continuous_v4';SEED=20261019
PRIOR_NUM=['prior_benefit_z','prior_logratio_se','prior_target_endpoint_match','prior_population_match','prior_comparator_match','prior_time_match']
PRIOR_CAT=['prior_metric','prior_tier']

def protocol():return json.loads((DATA/'protocol.json').read_text())
def partition(rows,seen_ncts,seen_assets):
    groups={'train':[],'calibration':[],'test':[],'seen_replay':[],'excluded':[]};ordered={}
    for r in rows:
        split=time_split(r)
        if not r.get('asset_group'):split='excluded'
        if split=='test' and (r['nct_id'] in seen_ncts or r['asset_group'] in seen_assets or r.get('seen_asset') or set(r.get('investigational_components',[]))&seen_assets):split='seen_replay'
        ordered[r['nct_id']]=(r,split)
    # An investigational asset can appear in only its latest partition.
    rank={'excluded':-1,'train':0,'calibration':1,'test':2,'seen_replay':3}
    latest={}
    for r,s in ordered.values():
        if s!='excluded':latest[r['asset_group']]=max(latest.get(r['asset_group'],-1),rank[s])
    purge=[]
    for r,s in ordered.values():
        if s!='excluded' and rank[s]<latest[r['asset_group']]:purge.append({'nct_id':r['nct_id'],'asset_group':r['asset_group'],'original_split':s,'reason':'asset_overlaps_later_partition'});groups['excluded'].append(r)
        elif not r.get('eligible'):groups['excluded'].append(r)
        else:groups[s].append(r)
    return groups,purge

def check_calibration(rows,logits):
    p=protocol()['calibration'];reasons=[]
    if len(rows)<p['minimum_n']:reasons.append('calibration_n_below_'+str(p['minimum_n']))
    for y in [0,1]:
        sub=[r for r in rows if r['label']==y]
        if len(sub)<p['minimum_each_class']:reasons.append('class'+str(y)+'_n_below_'+str(p['minimum_each_class']))
        if len({r['asset_group'] for r in sub})<p['minimum_each_class_components']:reasons.append('class'+str(y)+'_assets_below_'+str(p['minimum_each_class_components']))
    if len({r['asset_group'] for r in rows})<p['minimum_asset_components']:reasons.append('calibration_assets_below_'+str(p['minimum_asset_components']))
    if len(set(logits))<2:reasons.append('constant_calibration_scores')
    a=[p for p,r in zip(logits,rows) if r['label']==0];b=[p for p,r in zip(logits,rows) if r['label']==1]
    if a and b and (max(a)<min(b) or max(b)<min(a)):reasons.append('complete_separation')
    return reasons

def bootstrap(rows,predictions):
    import numpy as np
    if not rows:return {'status':'no_test_labels'}
    rng=np.random.default_rng(SEED);groups=sorted({r['asset_group'] for r in rows});by={g:[i for i,r in enumerate(rows) if r['asset_group']==g] for g in groups};y=np.array([r['label'] for r in rows]);errs={k:[] for k in predictions};diff={k:[] for k in predictions if k!='constant_0_5'}
    for _ in range(2000):
        ix=[i for g in rng.choice(groups,len(groups),replace=True) for i in by[g]]
        e={k:float(np.mean((np.array(p)[ix]-y[ix])**2)) for k,p in predictions.items()}
        for k in errs:errs[k].append(e[k])
        for k in diff:diff[k].append(e[k]-e['constant_0_5'])
    ci=lambda a:[float(v) for v in np.quantile(a,[.025,.975])]
    return {'unit':'investigational_asset','assets':len(groups),'replicates':2000,'seed':SEED,'brier_intervals':{k:ci(v) for k,v in errs.items()},'paired_difference_vs_constant':{k:ci(v) for k,v in diff.items()},'scope':'conditional on fitted models and observed-label cohort; omits source/adjudication/training uncertainty'}

def fit(rows,seen,output,sealed_test_path=None,sealed_commitment_path=None,audit_receipt=None):
    import numpy as np,sklearn
    from sklearn.compose import ColumnTransformer
    from sklearn.pipeline import Pipeline
    from sklearn.impute import SimpleImputer
    from sklearn.preprocessing import StandardScaler,OneHotEncoder
    from sklearn.linear_model import LogisticRegression
    from sklearn.ensemble import ExtraTreesClassifier
    from continuous_audit import validate,contract_hashes,digest
    from continuous_ensemble import learn,combine,guard_oof,classification,target70
    if audit_receipt is None:raise ValueError('Source audit receipt required before model fitting')
    audit=validate(rows,audit_receipt,seen)
    p=protocol();output=Path(output);output.mkdir(parents=True,exist_ok=False)
    groups,purged=partition(rows,set(seen['nct_ids']),set(seen.get('asset_groups',[]))|set(seen.get('asset_components',[])));train,cal,test=[groups[s] for s in ['train','calibration','test']]
    counts={k:{'n':len(v),'success':sum(r.get('label')==1 for r in v),'failure':sum(r.get('label')==0 for r in v),'assets':len({r.get('asset_group') for r in v if r.get('asset_group')})} for k,v in groups.items()}
    report={'protocol_file_sha256':hashlib.sha256((DATA/'protocol.json').read_bytes()).hexdigest(),'effective_contract_hashes':contract_hashes(),'source_audit':audit,'dataset_sha256':sha(rows),'created_at_utc':datetime.now(timezone.utc).isoformat(),'counts':counts,'asset_purged':purged,'clinical_prediction_validated':False,'formal_training_completed':False,'new_test_scope':'retrospective heldout unseen-to-prior-pilot targets; source curators may know public outcomes; not prospective clinical validation','metrics':{},'calibration':{},'status':'blocked_training_guard','model_training_completed':False,'partition_ncts':{s:[r['nct_id'] for r in rs] for s,rs in groups.items()},'performance_goal':{'status':'not_evaluated','target':.70,'achieved':False}}
    if test:
        counts['test'].update(success=None,failure=None,labels_sealed=True)
        if any(r.get('label') is not None for r in test):raise ValueError('Test labels visible before freeze')
        if not sealed_test_path or not sealed_commitment_path:raise ValueError('Pre-fit steward commitment required')
        seal_path=Path(sealed_test_path);commitment=json.loads(Path(sealed_commitment_path).read_text());seal_hash=hashlib.sha256(seal_path.read_bytes()).hexdigest()
        if seal_hash!=commitment['labels_file_sha256']:raise ValueError('Steward seal hash mismatch')
        if set(commitment['test_ncts'])!={r['nct_id'] for r in test}:raise ValueError('Steward commitment target mismatch')
    else:seal_path=None;seal_hash=None
    guard=p['training_guard'];fail=[]
    if len(train)<guard['min_train_n']:fail.append('train_n')
    if any(sum(r['label']==y for r in train)<guard['min_each_class'] for y in [0,1]):fail.append('train_class_n')
    if len({r['asset_group'] for r in train})<guard['min_assets']:fail.append('train_assets')
    if fail:
        report['unmet_training_guards']=fail
        report['performance_goal'].update(reason='training_guard_failed',point_target_met=False)
        report['calibration']={name:{'status':'not_fit_no_base_model','reasons':['training_guard_failed'],'n':len(cal),'asset_disjoint_from_train_and_test':True,'asset_disjoint_scope':'eligible records with reviewed components and result dates/intervals'} for name in p['models']}
        report['limitations']=['This is a dataset/model-readiness check, not a completed model training or2021–2025 efficacy backtest.','Source-adjudicated cohort is incomplete; all977 studies are retained with pending/unknown status.','A reserved2023 calibration partition does not establish a fitted or validated calibrator.','No v4 accuracy score exists until fixed fitting and sealed heldout scoring can be completed.','Clinical prediction validated=false; human double review remains incomplete.']
        write_new(output/'report.json',report);return report
    def xy(rs,aug):
        nums=NUMERIC+(PRIOR_NUM if aug else []);cats=CATEGORICAL+(PRIOR_CAT if aug else []);data=[]
        for r in rs:
            f=r['features']|r.get('prior_features',{})
            data.append([np.nan if f.get(k) is None else f[k] for k in nums]+[f.get(k) or 'unknown' for k in cats])
        return np.array(data,dtype=object),np.array([r['label'] for r in rs]),nums,cats
    def make_model(name,spec,nums,cats):
        prep=ColumnTransformer([('numeric',Pipeline([('impute',SimpleImputer(strategy='median',keep_empty_features=True)),('scale',StandardScaler())]),list(range(len(nums)))),('category',OneHotEncoder(handle_unknown='ignore'),list(range(len(nums),len(nums)+len(cats))))])
        estimator=LogisticRegression(**spec,random_state=SEED) if 'logistic' in name else ExtraTreesClassifier(**spec,random_state=SEED,n_jobs=1)
        return Pipeline([('features',prep),('model',estimator)])
    y=[r['label'] for r in train];base=(sum(y)+.5)/(len(y)+1);majority=int(sum(y)>=len(y)/2)
    testpred={'constant_0_5':[.5]*len(test),'beta_pooled':[base]*len(test),'beta_area':[],'training_majority':[float(majority)]*len(test)};models={};calibrators={};cal_scores={}
    for r in test:
        local=[t['label'] for t in train if t['features'].get('therapeutic_area')==r['features'].get('therapeutic_area')];testpred['beta_area'].append((sum(local)+8*base)/(len(local)+8))
    for name,spec in p['models'].items():
        aug=name.startswith('prior_');xt,yt,nums,cats=xy(train,aug)
        model=make_model(name,spec,nums,cats).fit(xt,yt);models[name]=model
        testpred[name]=model.predict_proba(xy(test,aug)[0])[:,1].tolist() if test else []
        cp=model.predict_proba(xy(cal,aug)[0])[:,1] if cal else np.array([]);logits=np.log(np.clip(cp,1e-6,1-1e-6)/(1-np.clip(cp,1e-6,1-1e-6)));cal_scores[name]=cp.tolist()
        reasons=check_calibration(cal,logits.tolist());report['calibration'][name]={'status':'not_fit_guard_failed' if reasons else 'fit_on_independent_2023_only','reasons':reasons,'n':len(cal),'asset_disjoint_from_train_and_test':True,'asset_disjoint_scope':'eligible records with reviewed components and result dates/intervals','calibration_metrics':metrics([r['label'] for r in cal],cp) if cal else {}}
        if not reasons:
            c=LogisticRegression(C=1,max_iter=2000,random_state=SEED).fit(logits.reshape(-1,1),np.array([r['label'] for r in cal]));calibrators[name]=c
            if test:
                pr=np.clip(np.array(testpred[name]),1e-6,1-1e-6);testpred[name+'_platt']=c.predict_proba(np.log(pr/(1-pr)).reshape(-1,1))[:,1].tolist()
    # Leave-one-investigational-asset-out base predictions on training years only.
    # The meta weights/threshold then fit these OOF labels: meta-fit is not CV.
    reasons=guard_oof(train);oof=[];recipe={'status':'blocked_guard','reasons':reasons}
    if not reasons:
        for asset in sorted({r['asset_group'] for r in train}):
            inner=[r for r in train if r['asset_group']!=asset];outer=[r for r in train if r['asset_group']==asset]
            if len({r['label'] for r in inner})<2:
                reasons.append('leave_asset_out_training_one_class');break
            probs={}
            for name,spec in p['models'].items():
                aug=name.startswith('prior_');xx,yy,nums,cats=xy(inner,aug)
                fold=make_model(name,spec,nums,cats).fit(xx,yy)
                probs[name]=fold.predict_proba(xy(outer,aug)[0])[:,1].tolist()
            for i,r in enumerate(outer):oof.append({'nct_id':r['nct_id'],'asset_group':asset,'label':r['label'],'probabilities':{k:v[i] for k,v in probs.items()},'fold_excludes_entire_asset':True})
        recipe=learn(oof,list(p['models'])) if not reasons else {'status':'blocked_oof','reasons':reasons}
    report['ensemble_training']=recipe
    if recipe.get('status')=='fitted':
        name='asset_OOF_convex_ensemble';testpred[name]=combine(testpred,recipe)
        cp=np.array(combine(cal_scores,recipe));cal_scores[name]=cp.tolist()
        logits=np.log(np.clip(cp,1e-6,1-1e-6)/(1-np.clip(cp,1e-6,1-1e-6)))
        reasons=check_calibration(cal,logits.tolist())
        report['calibration'][name]={'status':'not_fit_guard_failed' if reasons else 'fit_on_independent_2023_only','reasons':reasons,'n':len(cal),'asset_disjoint_from_train_and_test':True,'asset_disjoint_scope':'eligible records with reviewed components and result dates/intervals','calibration_metrics':metrics([r['label'] for r in cal],cp) if cal else {}}
        if not reasons:
            c=LogisticRegression(C=1,max_iter=2000,random_state=SEED).fit(logits.reshape(-1,1),np.array([r['label'] for r in cal]));calibrators[name]=c
            if test:
                pr=np.clip(np.array(testpred[name]),1e-6,1-1e-6);testpred[name+'_platt']=c.predict_proba(np.log(pr/(1-pr)).reshape(-1,1))[:,1].tolist()
    # Test rows are scaffolds with no directions. Steward labels are read only after fit_freeze.
    if test and any(r.get('label') is not None for r in test): raise ValueError('Heldout labels must remain sealed until model freeze')
    # Freeze fitted base models/calibrators before scoring heldout labels.
    blob=pickle.dumps({'models':models,'calibrators':calibrators,'ensemble_recipe':recipe,'feature_numeric':NUMERIC,'feature_categorical':CATEGORICAL,'prior_numeric':PRIOR_NUM,'prior_categorical':PRIOR_CAT,'dataset_sha256':sha(rows),'contract_hashes':contract_hashes()})
    (output/'models.pkl').write_bytes(blob)
    write_new(output/'training_oof.json',{'rows':oof,'recipe':recipe,'not_unbiased_meta_validation':True})
    write_new(output/'fit_freeze.json',{'created_at_utc':datetime.now(timezone.utc).isoformat(),'model_sha256':hashlib.sha256(blob).hexdigest(),'model_code_hashes':{name:digest(ROOT/name) for name in ['continuous_model.py','continuous_ensemble.py','continuous_audit.py','continuous_dataset.py','continuous_time.py','predome_prior.py','predome_pilot.py','build_effective_seen.py']},'runtime':{'python':__import__('sys').version,'sklearn':sklearn.__version__,'numpy':np.__version__},'seen_inventory_source':audit_receipt['seen'],'test_ncts':[r['nct_id'] for r in test],'test_probabilities':testpred,'calibration_scores':cal_scores,'labels_not_used_in_fit_or_selection':True,'test_not_used_for_hyperparameter_selection':True,'threshold_source':'training OOF only','ensemble_recipe':recipe,'primary_candidate':'asset_OOF_convex_ensemble' if recipe.get('status')=='fitted' else None,'sealed_test_file_sha256':seal_hash,'effective_contract_hashes':contract_hashes()})
    if test:
        if not seal_path:raise ValueError('No steward labels to score frozen predictions')
        if hashlib.sha256(seal_path.read_bytes()).hexdigest()!=seal_hash:raise ValueError('Sealed labels changed after model freeze')
        sealed=json.loads(seal_path.read_text());labels={r['nct_id']:r for r in sealed['labels']}
        if len(labels)!=len(sealed['labels']):raise ValueError('Duplicate sealed label')
        if set(labels)!={r['nct_id'] for r in test}:raise ValueError('Unsealed target mismatch')
        for i,r in enumerate(test):
            label=labels[r['nct_id']]
            if type(label['label']) is not int or label['label'] not in [0,1]:raise ValueError('Invalid steward label')
            test[i]=dict(r,label=label['label'])
        counts['test'].update(success=sum(r['label'] for r in test),failure=len(test)-sum(r['label'] for r in test),labels_sealed=False)
    report.update(status='retrospective_heldout_complete' if test else 'trained_no_eligible_heldout',model_training_completed=True,model_sha256=hashlib.sha256(blob).hexdigest(),sklearn_version=sklearn.__version__,numpy_version=np.__version__,metrics={k:metrics([r['label'] for r in test],v)|classification([r['label'] for r in test],v,recipe['threshold'] if k=='asset_OOF_convex_ensemble' else .5) for k,v in testpred.items()} if test else {},uncertainty=bootstrap(test,testpred),test_predictions=[{'nct_id':r['nct_id'],'asset_group':r['asset_group'],'label':r['label'],'decisive_primary_at':r['decisive_primary_at'],'probabilities':{k:v[i] for k,v in testpred.items()}} for i,r in enumerate(test)],calibration_completed=bool(calibrators),limitations=['Annual earliest-baseline stock union is not all2021–25 MNC readouts; the fixed2022 archive failed404.','Unknown/unreported labels remain in universe; evaluated subset can still have ascertainment bias.','No retrospective LLM pretraining-memory guarantee.','Past v2/v3 seen targets/assets quarantined from unseen test.','Prior effects after each earliest baseline are excluded even if before target outcome.','Formal200/50/50 and human double adjudication remain separate gates.','OOF base scores train ensemble weights/threshold; meta-fit scores are not independent validation.','Asset disjointness is verified among eligible reviewed records; unresolved assets/dates in the wider universe can conceal additional overlaps.'])
    if test and recipe.get('status')=='fitted':
        report['performance_goal']=target70(test,testpred['asset_OOF_convex_ensemble'],recipe['threshold'])
    else:report['performance_goal'].update(reason='primary_candidate_guard_failed' if recipe.get('status')!='fitted' else 'no_eligible_test')
    write_new(output/'report.json',report);return report

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('dataset');parser.add_argument('audit');parser.add_argument('seen');parser.add_argument('output');parser.add_argument('--sealed-test');parser.add_argument('--commitment');args=parser.parse_args()
    result=fit(json.loads(Path(args.dataset).read_text()),json.loads(Path(args.seen).read_text()),args.output,args.sealed_test,args.commitment,json.loads(Path(args.audit).read_text()))
    print(json.dumps({'status':result['status'],'counts':result['counts'],'model_training_completed':result['model_training_completed'],'performance_goal':result['performance_goal']}))
