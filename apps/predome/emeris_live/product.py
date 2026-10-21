"""User-facing clinical product views over integrity-verified frozen research data."""
from copy import deepcopy
from datetime import datetime,timedelta
import json
import hashlib
from pathlib import Path
from .dashboard import build_dashboard
ROOT=Path(__file__).resolve().parents[1]

def _published_outcomes(ledger, snapshots):
    """Join only approved public results to their exact immutable target.

    The ledger's internal evaluation rows contain private input metadata and
    reviewer diagnostics. Product responses deliberately expose a smaller view.
    """
    by_id={s['record']['snapshot_id']:s['record'] for s in snapshots}
    allowed=('outcome_id','snapshot_id','prediction_id','trial_id','target','label',
             'result_date','readout_date','effect_size','confidence_interval','p_value',
             'verification_status','verified_at','snapshot_sha256','outcome_sha256',
             'archive_provenance','forecasts')
    result=[]
    for row in ledger.public_outcome_records():
        snapshot=by_id.get(row.get('snapshot_id'))
        if (not snapshot or row.get('verification_status')!='approved'
                or row.get('trial_id')!=snapshot['trial_id']
                or row.get('target')!=snapshot['target']
                or type(row.get('label')) is not int or row['label'] not in (0,1)):
            continue
        item={key:deepcopy(row.get(key)) for key in allowed}
        item['source']={key:row.get('source',{}).get(key) for key in ('url','published_at','sha256')}
        for key in ('excerpt','excerpt_sha256','publication_date_basis'):
            if key in row.get('source',{}):item['source'][key]=row['source'][key]
        item['assessments']=[{key:a.get(key) for key in
                             ('model','model_version','probability','scope','eligible_for_prospective','prediction_status','coverage_eligible','score_eligible','selection_policy')}
                            for a in row.get('assessments',[])]
        item['actual_label']=row['label']
        item['eligible_for_prospective']=any(a.get('eligible_for_prospective') for a in item['assessments'])
        result.append(item)
    return result

def _bind_results(result, snapshots, outcomes):
    """An approval resolves one exact target, never every endpoint in a trial."""
    by_snapshot={r['snapshot_id']:r for r in outcomes}
    archive={}
    for envelope in snapshots:
        record=envelope['record']
        origin=record.get('archive_provenance') or {}
        if record.get('provenance')=='archived_record_import':
            key=(record['trial_id'],origin.get('case_id'),origin.get('original_record_sha256'))
            archive[key]=record
    for trial in result['upcoming']:
        verified=0
        for target in trial['targets']:
            key=(trial['nct_id'],target['case_id'],target['record_sha256'])
            snapshot=archive.get(key)
            actual=by_snapshot.get(snapshot['snapshot_id']) if snapshot else None
            target.update({'actual_outcome':actual,'verification_status':'approved' if actual else 'not_verified',
                           'verified_at':actual['verified_at'] if actual else None,
                           'prediction_status':'Verified' if actual else 'Awaiting Readout',
                           'public_snapshot_id':snapshot['snapshot_id'] if snapshot else None})
            if actual:verified+=1
        trial['verified_target_count']=verified
        trial['unresolved_target_count']=len(trial['targets'])-verified
        trial['prediction_status']=('Verified' if verified==len(trial['targets']) else
                                    'Partially Verified' if verified else 'Awaiting Readout')
        if verified:
            trial['result_status']='verified' if verified==len(trial['targets']) else 'partially_verified'
    result['resolved_trials']=[t for t in result['upcoming'] if t['unresolved_target_count']==0]
    result['upcoming']=[t for t in result['upcoming'] if t['unresolved_target_count']>0]

def area(indication):
    value=indication.lower()
    if any(x in value for x in ('cancer','nsclc','oncology','tumor')):return 'Oncology'
    if any(x in value for x in ('diabetes','metabolic')):return 'Metabolic Diseases'
    if any(x in value for x in ('sclerosis','neurolog','alzheimer')):return 'Neurology'
    if any(x in value for x in ('immune','lupus','nephropathy','eosinophilic')):return 'Immunology'
    return 'Others'

def attach_historical_details(result):
    path=ROOT/'dashboard_data/historical_result_details.json'
    raw=path.read_bytes();payload=json.loads(raw)
    rows={(r['case_id'],r['nct_id']):r for r in payload['records']}
    if len(rows)!=len(payload['records']):raise ValueError('Duplicate historical result details')
    def check(source):
        local=(ROOT/source['path']).resolve()
        if not local.is_relative_to(ROOT.resolve()) or hashlib.sha256(local.read_bytes()).hexdigest()!=source['sha256']:
            raise ValueError('Historical result source integrity failed')
        if source.get('publication_date_evidence'):check(source['publication_date_evidence'])
    for h in result['historical']:
        detail=rows.get((h['case_id'],h['nct_id']))
        if not detail or detail['endpoint']!=h['endpoint']:raise ValueError('Historical result target mismatch')
        check(detail['source'])
        for endpoint in detail.get('endpoints',[]):check(endpoint['source'])
        h['result_details']=deepcopy(detail)
        # Detailed paper dates never replace first topline or model execution dates.
    result['provenance']['historical_result_details_sha256']=hashlib.sha256(raw).hexdigest()
    result['provenance']['historical_details_review']='agent_source_checked_not_independent_human_review; correction-pending values withheld'


def attach_drug_annotations(result):
    raw=(ROOT/'dashboard_data/drug_annotations.json').read_bytes()
    annotations=json.loads(raw)['records'];index={r['nct_id']:r for r in annotations}
    if len(index)!=len(annotations):raise ValueError('Duplicate drug annotation')
    for trial in result['upcoming']:
        item=index.get(trial['nct_id'])
        if not item or item['asset'].casefold()!=trial['asset'].casefold():raise ValueError('Drug annotation identity mismatch')
        for source in item['sources']+[item['trial_drug_linkage']]:
            path=(ROOT/source['path']).resolve()
            if not path.is_relative_to(ROOT.resolve()) or hashlib.sha256(path.read_bytes()).hexdigest()!=source['sha256']:
                raise ValueError('Drug annotation source integrity failed')
        trial['registry_intervention_type']=trial['modality']
        trial['modality']=item['drug_modality'];trial['modality_zh']=item['drug_modality_zh']
        trial['modality_provenance']='Source-supported indexed drug annotation; not the entire combination regimen.'
        trial['molecular_target']=item['molecular_target'];trial['molecular_target_zh']=item['molecular_target_zh']
        trial['target_provenance']=item['review_status'];trial['drug_annotation']=deepcopy(item)
    result['provenance']['drug_annotations_sha256']=hashlib.sha256(raw).hexdigest()


def apply_readout_updates(result,store):
    from .readout_updates import approved_public_updates
    updates=approved_public_updates(store)
    result['readout_updates']=[]
    for trial in result['upcoming']:
        update=updates.get(trial['nct_id'])
        if not update:continue
        trial['previous_display_window']=deepcopy(trial['window'])
        trial['window']={key:update[key] for key in ('start','end','precision','label')}
        trial['window']['source_url']=update['source_url']
        trial['readout_date_kind']=update['date_kind']
        trial['official_announced_readout_date']=update['start'] if update['date_kind']=='official_exact_date' else None
        trial['readout_update']=update
        today=result['as_of_utc'][:10]
        trial['calendar_status']='past_window' if update['end']<today else 'future_window' if update['start']>today else 'within_window'
        trial['checked_at']=update['provenance']['approved_at']
        trial['status_note']='当前日历时间来自独立审核通过的公开来源更新；原预测输入、概率和锁定时间保持不变。'
        trial['sources'].append(update['source'])
        result['readout_updates'].append(update)
    result['summary']['reviewed_readout_update_count']=len(result['readout_updates'])


def build_product(store=None, now=None):
    result=build_dashboard(now)
    attach_historical_details(result)
    packets=json.loads((ROOT/'predome_data/continuous_v4/prospective/candidate_packets.json').read_text())['candidates']
    packets={p['nct_id']:p for p in packets}
    for trial in result['upcoming']:
        packet=packets[trial['nct_id']]
        raw=json.loads((ROOT/packet['registry_source']['path']).read_text())['protocolSection']
        interventions=raw.get('armsInterventionsModule',{}).get('interventions',[])
        types=sorted(set(i.get('type','UNKNOWN') for i in interventions))
        trial.update({
            'therapeutic_area':area(trial['indication']),
            'classification_provenance':'System keyword grouping of recorded indication; not independently adjudicated.',
            'modality':' / '.join(types) or 'Unknown',
            'modality_provenance':'ClinicalTrials.gov intervention type; molecular modality not inferred.',
            'molecular_target':None,'target_provenance':'Not extracted; do not infer from drug name.',
            'phase':trial['inputs']['design'].get('phases',[]),
            'trial_design':trial['inputs']['design'].get('designInfo',{}),
            'registry_url':f'https://clinicaltrials.gov/study/{trial["nct_id"]}',
            'registry_summary':raw.get('descriptionModule',{}).get('briefSummary'),
            'registered_at':raw.get('statusModule',{}).get('studyFirstSubmitDate'),
            'primary_completion_date':packet['registry_status'].get('primaryCompletionDateStruct'),
            'study_completion_date':packet['registry_status'].get('completionDateStruct'),
            'readout_date_kind':'estimated_window',
            'official_announced_readout_date':None,
            'prediction_status':'Awaiting Readout',
            'detail_url':'/trial/'+trial['nct_id'],
        })
        for target in trial['targets']:
            target['pipeline_version']='continuous-v4-frozen-20261019'
            target['model_training_cutoff']='Unknown; provider pretraining leakage cannot be excluded.'
            ps=[p['probability'] for p in target['forecasts'].values() if p['probability'] is not None]
            target['model_disagreement']=max(ps)-min(ps) if len(ps)>1 else None
            target['uncertainty_kind']='between-model spread; not a confidence interval; probabilities not calibrated'
    attach_drug_annotations(result)
    if store is not None:apply_readout_updates(result,store)
    today=datetime.fromisoformat(result['as_of_utc'])
    end=(today+timedelta(days=90)).date().isoformat()
    result['summary']['upcoming_90day_trials']=sum(t['window']['start']<=end and t['window']['end']>=today.date().isoformat() for t in result['upcoming'])
    result['summary']['locked_model_predictions']=sum(len(tg['forecasts']) for t in result['upcoming'] for tg in t['targets'])
    result['summary']['archive_locked_model_predictions']=result['summary']['locked_model_predictions']
    result['summary']['locked_count_definition']='Archived plus explicitly public user model judgments, including abstentions; not independent trial count.'
    result['summary']['public_user_locked_predictions']=0
    result['public_predictions']=[]
    result['public_verified_outcomes']=[]
    if store is not None:
        from .platform_store import PlatformStore
        ledger=PlatformStore(store)
        result['public_predictions']=ledger.list_public_snapshots()
        result['summary']['public_user_locked_predictions']=sum(
            x['record'].get('provenance')!='archived_record_import' for x in result['public_predictions'])
        result['summary']['locked_model_predictions']+=sum(len(x['record'].get('forecasts',{})) for x in result['public_predictions'] if x['record'].get('provenance')!='archived_record_import')
        result['public_verified_outcomes']=_published_outcomes(ledger,result['public_predictions'])
        rows=ledger.evaluation_rows(public_only=True)
        result['summary']['prospective_evaluated_n']=len({r['trial_id'] for r in rows if r.get('score_eligible')})
        result['summary']['prospective_evaluated_target_count']=len({
            r['snapshot_id'] for r in rows if r.get('score_eligible')})
    _bind_results(result,result['public_predictions'],result['public_verified_outcomes'])
    result['summary']['verified_trial_count']=len({r['trial_id'] for r in result['public_verified_outcomes']})
    result['summary']['verified_target_count']=len({
        (r['trial_id'],json.dumps(r['target'],sort_keys=True)) for r in result['public_verified_outcomes']})
    result['summary']['fully_verified_curated_trial_count']=len(result['resolved_trials'])
    result['summary']['upcoming_trial_count']=len(result['upcoming'])
    result['summary']['upcoming_target_count']=sum(t['unresolved_target_count'] for t in result['upcoming'])
    result['summary']['upcoming_90day_trials']=sum(
        t['window']['start']<=end and t['window']['end']>=today.date().isoformat() for t in result['upcoming'])
    result['summary']['verified_count_definition']='Public independently approved exact-target outcomes; not necessarily eligible for prospective performance. One trial may contain unresolved targets.'
    result['summary']['calendar_count_definition']='Curated trials with source-backed readout windows and at least one unresolved target. Public user predictions without a source-backed window are listed separately.'
    result['taxonomy_note']='Therapeutic area is a display grouping. Drug modality and molecular target are source-backed annotations for the indexed asset, not the full combination regimen; source checks are not independent human adjudication.'
    return result

def trial_detail(nct,store=None):
    product=build_product(store)
    pending=next((t for t in product['upcoming']+product['resolved_trials'] if t['nct_id']==nct),None)
    historical=[r for r in product['historical'] if r['nct_id']==nct]
    published=[r for r in product['public_predictions'] if r['record']['trial_id']==nct]
    outcomes=[r for r in product['public_verified_outcomes'] if r['trial_id']==nct]
    if not pending and not historical and not published:return None
    provenance=deepcopy(product['provenance']) if pending or historical else {
        'kind':'user_published_prediction','integrity_verified':all(r.get('integrity_verified') for r in published),
        'input_source':'User-submitted input; only explicitly published target and forecast summaries are shown.',
        'curated_archive_equivalent':False,'clinical_prediction_validated':False,
        'independent_timestamp_verified':False,
        'input_validation':'Snapshot integrity does not establish registry accuracy, evidence completeness, or clinical validity.'}
    return {'nct_id':nct,'upcoming':pending,'historical':historical,'provenance':provenance,
            'record_kind':'curated_archive' if pending or historical else 'user_published_prediction',
            'public_predictions':published,'public_verified_outcomes':outcomes,
            'comparables':{'status':'not_evaluated','records':[], 'note':'No validated comparable-trial matching has been run for this archived record.'},
            'timeline_policy':'Source dates are preserved; missing lock/readout/verification events are not invented.'}
