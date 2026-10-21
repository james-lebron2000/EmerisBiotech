"""Time-aware public-outcome supervised baseline. Never trains on forecast targets.
Usage: python predome_training.py --data records.json --output NEW_DIRECTORY
Requires sklearn only for an actual eligible supervised run.
"""
import argparse,json,math,hashlib
from pathlib import Path
from datetime import datetime,date
NUMERIC=['planned_enrollment','phase','primary_endpoint_count','randomized','placebo_controlled','prior_randomized_evidence','prior_effect_standard_error']
CATEGORICAL=['therapeutic_area','endpoint_family','modality','comparator_type']
def eligible(records):
 accepted=[];rejected=[]
 for r in records:
  reason=[]
  if r.get('review_status')!='double_reviewed':reason.append('not_double_reviewed')
  if type(r.get('label')) is not int or r.get('label') not in [0,1]:reason.append('no_adjudicated_primary_label')
  if not r.get('features_as_of') or not r.get('first_result_at'):reason.append('missing_preoutcome_snapshot')
  elif r['features_as_of']>=r['first_result_at']:reason.append('feature_outcome_leakage')
  if r.get('rights_status')!='approved_for_this_training':reason.append('rights_not_confirmed')
  if r.get('pre_result_snapshot_verified') is not True:reason.append('pre_result_snapshot_not_verified')
  if not isinstance(r.get('features'),dict):reason.append('no_feature_record')
  elif set(r['features'])-set(NUMERIC+CATEGORICAL):reason.append('unapproved_feature_fields')
  if not r.get('asset_group') or not r.get('nct_id'):reason.append('missing_identity_group')
  (rejected if reason else accepted).append({'record_id':r.get('record_id'),'reasons':reason} if reason else r)
 return accepted,rejected

def train(records,out):
 out=Path(out);out.mkdir(parents=True,exist_ok=False);a,rejected=eligible(records)
 # Calendar split is fixed before model fitting; no random split across projects.
 groups={};conflicts=[]
 for r in a:groups.setdefault(r['asset_group'],set()).add('train' if r['first_result_at']<'2023-01-01' else 'validation' if r['first_result_at']<'2024-01-01' else 'test' if r['first_result_at']<'2026-01-01' else 'future')
 a2=[]
 for r in a:
  if len(groups[r['asset_group']])>1:conflicts.append(r['record_id'])
  elif next(iter(groups[r['asset_group']]))!='future':a2.append(r)
 partitions={s:[r for r in a2 if next(iter(groups[r['asset_group']]))==s] for s in ['train','validation','test']}
 audit={'eligible_before_group_split':len(a),'rejected':rejected,'cross_period_asset_groups_excluded':conflicts,'counts':{s:len(v) for s,v in partitions.items()},'training_completed':False,'clinical_prediction_validated':False}
 # Operational minimums for this baseline, not a proof of statistical sufficiency.
 if any(len(partitions[s])<n or len({r['label'] for r in partitions[s]})<2 for s,n in [('train',200),('validation',50),('test',50)]):
  audit['status']='blocked_insufficient_adjudicated_temporal_data';(out/'audit.json').write_text(json.dumps(audit,indent=2,ensure_ascii=False));return audit
 from sklearn.compose import ColumnTransformer
 from sklearn.pipeline import Pipeline
 from sklearn.impute import SimpleImputer
 from sklearn.preprocessing import StandardScaler,OneHotEncoder
 from sklearn.linear_model import LogisticRegression
 from sklearn.metrics import brier_score_loss,log_loss,roc_auc_score,average_precision_score
 import numpy as np,pickle
 def xy(rows):
  x=[[r['features'].get(f,float('nan')) for f in NUMERIC]+[r['features'].get(f,'unknown') for f in CATEGORICAL] for r in rows]
  return np.array(x,dtype=object),np.array([r['label'] for r in rows])
 prep=ColumnTransformer([('numeric',Pipeline([('impute',SimpleImputer(strategy='median')),('scale',StandardScaler())]),list(range(len(NUMERIC)))),('category',OneHotEncoder(handle_unknown='ignore'),list(range(len(NUMERIC),len(NUMERIC+CATEGORICAL))))])
 model=Pipeline([('features',prep),('logistic',LogisticRegression(C=1.,max_iter=2000,random_state=20261016))]);xt,yt=xy(partitions['train']);xv,yv=xy(partitions['validation']);xe,ye=xy(partitions['test']);model.fit(xt,yt)
 pv=model.predict_proba(xv)[:,1];cal=LogisticRegression(C=1e6,random_state=20261016).fit(np.log(np.clip(pv,1e-6,1-1e-6)/np.clip(1-pv,1e-6,1)).reshape(-1,1),yv)
 pe=model.predict_proba(xe)[:,1];pc=cal.predict_proba(np.log(np.clip(pe,1e-6,1-1e-6)/np.clip(1-pe,1e-6,1)).reshape(-1,1))[:,1];base=np.full(len(ye),yt.mean())
 def score(p):return {'brier':brier_score_loss(ye,p),'log_loss':log_loss(ye,p),'auroc':roc_auc_score(ye,p),'pr_auc':average_precision_score(ye,p)}
 audit.update(status='trained_research_baseline',training_completed=True,train_prevalence=float(yt.mean()),test_metrics={'base_rate':score(base),'raw_logistic':score(pe),'calibrated_logistic':score(pc)},scope='2010-2022 training;2023 calibration;2024-2025 heldout;cross-period assets excluded; no clinical validation claim from one test')
 (out/'model.pkl').write_bytes(pickle.dumps({'model':model,'calibrator':cal,'feature_contract':[NUMERIC,CATEGORICAL]}))
 (out/'audit.json').write_text(json.dumps(audit,indent=2,ensure_ascii=False));return audit
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--data',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();r=train(json.loads(a.data.read_text()),a.output);print(json.dumps(r,ensure_ascii=False))
