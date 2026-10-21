"""Fixed-hyperparameter development replay. The historical eight were seen previously."""
import json, pickle, hashlib
from pathlib import Path
from datetime import datetime,timezone
from predome_pilot import ROOT,NUMERIC,CATEGORICAL,audit_rows,partition,metrics,paired_bootstrap,sha,write_new
from predome_prior import DATA

def contract():
    return {p.name:json.loads(p.read_text()) for p in [DATA/'protocol.json',DATA/'protocol_amendment_01.json',DATA/'protocol_amendment_02.json',DATA/'protocol_amendment_03.json']}

def fit(rows, output):
    import numpy as np, sklearn
    from sklearn.compose import ColumnTransformer
    from sklearn.pipeline import Pipeline
    from sklearn.impute import SimpleImputer
    from sklearn.preprocessing import StandardScaler,OneHotEncoder
    from sklearn.linear_model import LogisticRegression
    from sklearn.ensemble import ExtraTreesClassifier
    output=Path(output); output.mkdir(parents=True,exist_ok=False)
    policy=contract(); protocol=policy['protocol.json']; accepted,rejected=audit_rows(rows)
    clock_excluded=[r['nct_id'] for r in accepted if r['features_as_of']>r['forecast_as_of']]
    accepted=[r for r in accepted if r['features_as_of']<=r['forecast_as_of']]
    train,test,purged=partition(accepted)
    assert {r['nct_id'] for r in test}==set(protocol['test_policy']['locked_replay_ncts'])
    assert len(train)>=18 and len({r['label'] for r in train})==2
    assert not {r['asset_group'] for r in train}&{r['asset_group'] for r in test}
    prior_num=protocol['features_prior_numeric']; prior_cat=protocol['features_prior_categorical']
    def xy(rs,aug):
        nums=NUMERIC+(prior_num if aug else []); cats=CATEGORICAL+(prior_cat if aug else [])
        data=[]
        for r in rs:
            f=r['features']|r['prior_features']; data.append([np.nan if f.get(k) is None else f[k] for k in nums]+[f.get(k) or 'unknown' for k in cats])
        return np.array(data,dtype=object),np.array([r['label'] for r in rs]),nums,cats
    def predict(train_rows,eval_rows):
        yt=np.array([r['label'] for r in train_rows]); pooled=float((yt.sum()+.5)/(len(yt)+1))
        pred={'beta_pooled':[pooled]*len(eval_rows),'beta_area':[],'constant_0_5':[.5]*len(eval_rows)}; models={}
        for r in eval_rows:
            local=[t['label'] for t in train_rows if t['features']['therapeutic_area']==r['features']['therapeutic_area']]
            pred['beta_area'].append((sum(local)+8*pooled)/(len(local)+8))
        for name,spec in protocol['models'].items():
            aug=name.startswith('prior_'); xt,_,nums,cats=xy(train_rows,aug); xe,_,_,_=xy(eval_rows,aug)
            prep=ColumnTransformer([('numeric',Pipeline([('impute',SimpleImputer(strategy='median',keep_empty_features=True)),('scale',StandardScaler())]),list(range(len(nums)))),('category',OneHotEncoder(handle_unknown='ignore'),list(range(len(nums),len(nums)+len(cats))))])
            estimator=LogisticRegression(**spec,random_state=20261017) if 'logistic' in name else ExtraTreesClassifier(**spec,random_state=20261017)
            model=Pipeline([('features',prep),('model',estimator)]).fit(xt,yt)
            assert set(model.named_steps['model'].classes_)=={0,1}
            pred[name]=model.predict_proba(xe)[:,1].tolist(); models[name]=model
        return pred,models
    predictions,models=predict(train,test)
    oof=[];folds=[]
    for asset in sorted({r['asset_group'] for r in train}):
        sub=[r for r in train if r['asset_group']!=asset]; held=[r for r in train if r['asset_group']==asset]
        assert not {r['asset_group'] for r in sub}&{r['asset_group'] for r in held}
        if len({r['label'] for r in sub})!=2:
            folds.append({'held_asset':asset,'status':'abstain_training_single_class'});continue
        ps,_=predict(sub,held)
        folds.append({'held_asset':asset,'training_ncts':[r['nct_id'] for r in sub],'held_ncts':[r['nct_id'] for r in held],'status':'completed','preprocessing_fit_within_fold':True})
        for i,r in enumerate(held): oof.append({'nct_id':r['nct_id'],'asset_group':asset,'label':r['label'],'probabilities':{k:v[i] for k,v in ps.items()}})
    report={'id':protocol['id'],'created_at_utc':datetime.now(timezone.utc).isoformat(),'status':'development_replay_complete','validation_kind':'reused_seen_test_set_not_independent_validation','new_independent_test':False,'exploratory_training_completed':True,'formal_training_completed':False,'clinical_prediction_validated':False,'protocol_sha256':sha(policy),'dataset_sha256':sha(rows),'accepted':len(accepted),'rejected':rejected,'clock_excluded':clock_excluded,'counts':{'candidate_rows':len(rows),'train':len(train),'test':len(test),'train_assets':len({r['asset_group'] for r in train}),'test_assets':len({r['asset_group'] for r in test})},'training_ncts':[r['nct_id'] for r in train],'purged_train_asset_overlap':purged,'label_counts':{k:{'success':sum(r['label'] for r in rs),'failure':len(rs)-sum(r['label'] for r in rs)} for k,rs in [('train',train),('test',test)]},'prior_coverage':{k:{'eligible_numeric':sum(r['prior_features']['prior_benefit_z'] is not None for r in rs),'total':len(rs),'A':sum(r['prior_features']['prior_tier']=='A' for r in rs),'B':sum(r['prior_features']['prior_tier']=='B' for r in rs),'assurance_answered':0} for k,rs in [('train',train),('test',test)]},'metrics':{k:metrics([r['label'] for r in test],p) for k,p in predictions.items()},'uncertainty':paired_bootstrap(test,predictions),'test_predictions':[{'nct_id':r['nct_id'],'asset_group':r['asset_group'],'label':r['label'],'probabilities':{k:p[i] for k,p in predictions.items()}} for i,r in enumerate(test)],'training_cv':{'scheme':'leave_one_asset_group_out','temporal_forward_validation':False,'interpretation':'Grouped internal development CV; folds may train on later pre2023 trials. Not a temporal forward test.','used_for_model_selection':False,'n_oof':len(oof),'total':len(train),'folds':folds,'predictions':oof,'metrics':{k:metrics([r['label'] for r in oof],[r['probabilities'][k] for r in oof]) for k in predictions}},'sklearn_version':sklearn.__version__,'limitations':['Reuses the eight previously seen test cases: development replay, not independent validation.','Six added failures were outcome-selected; failure-enriched convenience sample, not an industry base-rate estimate.','No probability calibration set; probabilities uncalibrated.','Only 18 training trials and few asset groups; wide, conditional bootstrap intervals omit training/source uncertainty.','B-tier prior RCT effects are indirect; standardized z does not remove comparator/population/endpoint differences.','No A-tier assurance; all assurance results abstain.','Retrospective cutoff is anchored seven days before a known result; readout timing was not prospectively predicted.','Historical LLM pretraining contamination cannot be excluded.','Agent source review is not human double adjudication; formal200/50/50 gate remains unmet.']}
    blob=pickle.dumps({'models':models,'protocol_sha256':sha(policy),'dataset_sha256':sha(rows)})
    (output/'models.pkl').write_bytes(blob);report['model_sha256']=hashlib.sha256(blob).hexdigest()
    cases=[]
    for i,r in enumerate(test):
        prior=r.get('selected_prior_record'); evidence=None
        if prior:
            app=next(a for a in prior['applicability'] if a['target_nct_id']==r['nct_id'])
            evidence={'evidence_id':'prior-%03d'%i,'metric':prior['effect_measure'],'effect':prior['effect_estimate'],'confidence_interval':prior['confidence_interval'],'confidence_level':prior['confidence_level'],'se_approx':r['selected_prior_math']['derived_se'],'benefit_z':r['prior_features']['prior_benefit_z'],'tier':r['prior_features']['prior_tier'],'differences':app['differences'],'endpoint':prior['endpoint'],'randomized':True,'sample_sizes':prior.get('sample_sizes')}
        cases.append({'case_id':'case-%03d'%i,'features':r['features'],'prior_features':r['prior_features'],'prior_evidence':evidence})
    write_new(output/'llm_inputs.json',{'protocol_sha256':sha(policy),'task':'Estimate primary-analysis success using ONLY supplied anonymized pre-cutoff design and indirect RCT evidence; no target identity, labels or peer predictions. Uncalibrated historical replay; abstain when insufficient.','cases':cases})
    write_new(output/'report.json',report);return report

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--data',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    r=fit(json.loads(a.data.read_text()),a.output);print(json.dumps({k:r[k] for k in ['status','counts','label_counts','prior_coverage','metrics','training_cv']},ensure_ascii=False))
