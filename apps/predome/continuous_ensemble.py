"""Training-only asset-OOF simplex ensemble and fixed-grid classification threshold.
Weights and threshold are fitted on training OOF data; meta-fit score is not CV validation.
"""
from pathlib import Path
import json,math
SEED=20261019

def guard_oof(rows):
    reasons=[]
    if len(rows)<50:reasons.append('OOF_n_below50')
    if len({r['asset_group'] for r in rows})<20:reasons.append('OOF_assets_below20')
    for y in [0,1]:
        if sum(r['label']==y for r in rows)<15:reasons.append('OOF_class'+str(y)+'_below15')
    return reasons

def learn(rows,methods):
    import numpy as np
    from scipy.optimize import minimize
    reasons=guard_oof(rows)
    result={'status':'blocked_guard' if reasons else 'not_fit','reasons':reasons,'methods':list(methods),'weights_from':'training-only cross-fitted base scores','threshold_from':'same training-only OOF, not calibration or test','n':len(rows),'assets':len({r['asset_group'] for r in rows}),'meta_fit_is_not_validation':True}
    if reasons:return result
    x=np.array([[r['probabilities'][m] for m in methods] for r in rows]);y=np.array([r['label'] for r in rows]);k=len(methods)
    objective=lambda w:float(np.mean((x@w-y)**2))
    jac=lambda w:2*x.T@(x@w-y)/len(y)
    optimization=minimize(objective,np.ones(k)/k,jac=jac,method='SLSQP',bounds=[(0,1)]*k,constraints={'type':'eq','fun':lambda w:np.sum(w)-1,'jac':lambda w:np.ones(k)},options={'ftol':1e-12,'maxiter':1000})
    if not optimization.success:result.update(status='blocked_optimizer',reasons=[str(optimization.message)]);return result
    weights=optimization.x;prob=x@weights
    def bacc(t):
        pred=prob>=t
        return float(((pred[y==1]).mean()+(~pred[y==0]).mean())/2)
    grid=[round(.05+i*.01,2) for i in range(91)];scores=[bacc(t) for t in grid];best=max(scores)
    tied=[t for t,s in zip(grid,scores) if abs(s-best)<1e-12];threshold=min(tied,key=lambda t:(abs(t-.5),-t))
    result.update(status='fitted',weights={m:float(w) for m,w in zip(methods,weights)},threshold=threshold,threshold_grid={'start':.05,'stop':.95,'step':.01,'objective':'balanced_accuracy','tie':'closest0.5 thenhigher'},meta_fit_brier=objective(weights),meta_fit_balanced_accuracy=bacc(threshold),meta_fit_score_warning='Base scores were OOF, but these meta weights and threshold were fitted on the same OOF labels. This is training fit, not unbiased ensemble CV.')
    return result

def combine(probabilities,recipe):
    if recipe.get('status')!='fitted':return None
    names=recipe['weights'];n=len(probabilities[next(iter(names))])
    if any(len(probabilities[k])!=n for k in names):raise ValueError('Prediction length mismatch')
    return [sum(recipe['weights'][k]*probabilities[k][i] for k in names) for i in range(n)]

def classification(y,p,threshold=.5):
    from sklearn.metrics import accuracy_score,balanced_accuracy_score,confusion_matrix
    import numpy as np
    a=np.array(y,dtype=int);z=np.array(p)>=threshold;tn,fp,fn,tp=confusion_matrix(a,z,labels=[0,1]).ravel()
    return {'n':len(a),'threshold':threshold,'accuracy':float(accuracy_score(a,z)),'balanced_accuracy':float(balanced_accuracy_score(a,z)) if len(set(y))==2 else None,'sensitivity':float(tp/(tp+fn)) if tp+fn else None,'specificity':float(tn/(tn+fp)) if tn+fp else None,'confusion':{'tn':int(tn),'fp':int(fp),'fn':int(fn),'tp':int(tp)}}

def target70(rows,p,threshold):
    import numpy as np
    if not rows:return {'status':'no_eligible_test','point_target_met':False,'clinical_validation':False}
    metrics=classification([r['label'] for r in rows],p,threshold);groups=sorted({r['asset_group'] for r in rows});by={g:[i for i,r in enumerate(rows) if r['asset_group']==g] for g in groups};rng=np.random.default_rng(SEED);values=[];balanced=[];invalid=0
    y=np.array([r['label'] for r in rows]);probs=np.array(p)
    for _ in range(2000):
        ix=[i for g in rng.choice(groups,len(groups),replace=True) for i in by[g]];values.append(float(np.mean((probs[ix]>=threshold)==y[ix])))
        if len(set(y[ix]))<2:invalid+=1;continue
        yy=y[ix];zz=probs[ix]>=threshold;balanced.append(float((zz[yy==1].mean()+(~zz[yy==0]).mean())/2))
    ci=[float(v) for v in np.quantile(values,[.025,.975])]
    ix=[i for i,v in enumerate(p) if v<=.30 or v>=.70];selective=classification([rows[i]['label'] for i in ix],[p[i] for i in ix],threshold) if ix else {'n':0,'accuracy':None,'balanced_accuracy':None}
    majority=float(max(sum(y==0),sum(y==1))/len(y));brier=float(np.mean((probs-y)**2));train_criteria=metrics['accuracy']>=.70 and metrics['balanced_accuracy'] is not None and metrics['balanced_accuracy']>=.70
    bci=[float(v) for v in np.quantile(balanced,[.025,.975])] if balanced else None
    point=train_criteria and metrics['accuracy']>majority and brier<.25
    return {'status':'scored_frozen_primary_candidate','primary_candidate':'asset_OOF_convex_ensemble','all_assessable_test':metrics,'prediction_coverage_on_assessable_test':1.0,'asset_bootstrap_accuracy95':ci,'asset_bootstrap_balanced_accuracy95':bci,'balanced_bootstrap_invalid_oneclass_replicates':invalid,'asset_count':len(groups),'observed_test_majority_reference_accuracy':float(majority),'test_majority_reference_is_not_a_fitted_predictor':True,'beats_observed_majority':metrics['accuracy']>majority,'brier':brier,'brier_better_than_fixed50':brier<.25,'point_accuracy_and_balanced_accuracy_target':bool(train_criteria),'point_target_met':bool(point),'stable70_lower_bound_passed':bool(point and ci[0]>=.70 and bci and bci[0]>=.70 and len(rows)>=50 and len(groups)>=20),'minimum_precision_scope':{'n':50,'assets':20,'not_a_power_or_clinical_validation_guarantee':True},'selective_subset':selective|{'coverage':len(ix)/len(rows),'fixed_gate':'p<=.30 or p>=.70','not_full_cohort_accuracy':True},'clinical_validation':False,'interpretation':'Point target requires accuracy and balanced accuracy >=.70, beating observed majority and Brier<.25. Stable evidence additionally requires both asset-CI lower bounds>=.70 and at least50tests/20assets. Ascertainment bias, independent calibration and formal human-reviewed validation remain separate.'}
