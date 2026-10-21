"""Immutable feature-free 50% reference; never a fitted historical success rate."""
import hashlib
import json
from datetime import datetime,timezone

POLICY_VERSION='fixed-0.5-no-features.v1'
BASELINE_KEY='fixed_0_5'
POLICY={'policy_version':POLICY_VERSION,'algorithm':'constant_binary_event_probability',
        'probability':0.5,'inputs_used':[],'label_fitting':False,
        'interpretation':'feature_free_50_percent_reference_not_historical_base_rate',
        'cutoff_semantics':'aligned_to_forecast_boundary_no_features_or_outcomes_used'}


def canonical(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
def sha(value):return hashlib.sha256(canonical(value).encode()).hexdigest()
POLICY_SHA256=sha(POLICY)
RECORD_FIELDS=set(POLICY)|{'schema_version','baseline_id','baseline_key','snapshot_id','snapshot_sha256','prediction_id','trial_id','target_sha256','policy_sha256','recorded_at','effective_evidence_cutoff','source_forecast_cutoff','source_forecast_locked_at','registration_origin','timestamp_attestation'}


def current_time():
    from .platform_store import now
    return now()


def stamp(value):
    if not isinstance(value,str):raise ValueError('Baseline timestamp missing')
    parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
    if parsed.tzinfo is None:
        if len(value)==10:return parsed.replace(tzinfo=timezone.utc)
        raise ValueError('Baseline timestamp timezone missing')
    return parsed.astimezone(timezone.utc)


def initialize(store):
    with store.connect() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS platform_prospective_baselines(id TEXT PRIMARY KEY,snapshot_id TEXT NOT NULL,policy_version TEXT NOT NULL,sha256 TEXT NOT NULL,payload BLOB NOT NULL,created TEXT NOT NULL,UNIQUE(snapshot_id,policy_version));
        CREATE INDEX IF NOT EXISTS prospective_baselines_order ON platform_prospective_baselines(created DESC,id DESC);
        ''')
        for action in ('UPDATE','DELETE'):
            db.execute(f"CREATE TRIGGER IF NOT EXISTS prospective_baselines_no_{action.lower()} BEFORE {action} ON platform_prospective_baselines BEGIN SELECT RAISE(ABORT,'append-only prospective baseline'); END")


def _decode(store,row):
    record=store.unpack(row['payload'])
    if not isinstance(record,dict) or set(record)!=RECORD_FIELDS:raise ValueError('Prospective baseline record schema conflict')
    if sha(record)!=row['sha256']:raise ValueError('Prospective baseline integrity failed')
    if record.get('schema_version')!='predome.prospective_baseline.v1' or record.get('registration_origin') not in ('new_public_snapshot_lock','explicit_public_snapshot_migration') or record.get('timestamp_attestation')!='server_clock_not_independent_timestamp':
        raise ValueError('Prospective baseline provenance schema conflict')
    if (record.get('baseline_id'),record.get('snapshot_id'),record.get('policy_version'),record.get('recorded_at'))!=(row['id'],row['snapshot_id'],row['policy_version'],row['created']):
        raise ValueError('Prospective baseline index binding failed')
    if sha({key:record.get(key) for key in POLICY})!=POLICY_SHA256:raise ValueError('Prospective baseline canonical policy conflict')
    if record['baseline_id']!='pb_'+sha({'snapshot_sha256':record['snapshot_sha256'],'policy_sha256':POLICY_SHA256})[:24]:raise ValueError('Prospective baseline deterministic identity conflict')
    if (record.get('policy_version')!=POLICY_VERSION or record.get('policy_sha256')!=POLICY_SHA256
            or record.get('probability')!=.5 or type(record.get('probability')) not in (int,float)
            or record.get('inputs_used')!=[] or record.get('label_fitting') is not False
            or record.get('baseline_key')!=BASELINE_KEY):
        raise ValueError('Prospective baseline policy conflict')
    return record


def _binding(record,snapshot,snapshot_sha):
    if snapshot.get('published') is not True:raise ValueError('Private snapshots cannot enter the public baseline registry')
    expected={'snapshot_id':snapshot['snapshot_id'],'snapshot_sha256':snapshot_sha,
              'prediction_id':snapshot['prediction_id'],'trial_id':snapshot['trial_id'],
              'target_sha256':sha(snapshot['target']),'source_forecast_cutoff':snapshot['data_cutoff'],
              'effective_evidence_cutoff':snapshot['data_cutoff'],'source_forecast_locked_at':snapshot['locked_at']}
    if any(record.get(key)!=value for key,value in expected.items()):raise ValueError('Prospective baseline snapshot/complete-target binding conflict')
    if stamp(record['recorded_at'])<stamp(snapshot['locked_at']):raise ValueError('Baseline registration predates its snapshot lock')
    if stamp(record['effective_evidence_cutoff'])>stamp(record['recorded_at']):raise ValueError('Baseline evidence cutoff exceeds registration time')


def register_snapshot(store,db,snapshot,snapshot_sha,registration_origin):
    """Transaction-owned writer with server time; no caller-supplied backdating."""
    if snapshot.get('published') is not True:raise ValueError('Private snapshots cannot enter the public baseline registry')
    if sha(snapshot)!=snapshot_sha:raise ValueError('Snapshot integrity failed before baseline registration')
    if registration_origin not in ('new_public_snapshot_lock','explicit_public_snapshot_migration'):raise ValueError('Unknown baseline registration origin')
    existing=db.execute('SELECT * FROM platform_prospective_baselines WHERE snapshot_id=? AND policy_version=?',(snapshot['snapshot_id'],POLICY_VERSION)).fetchone()
    if existing:
        record=_decode(store,existing);_binding(record,snapshot,snapshot_sha)
        return record|{'record_sha256':existing['sha256'],'created':False}
    recorded=current_time()
    record={'schema_version':'predome.prospective_baseline.v1','baseline_id':'pb_'+sha({'snapshot_sha256':snapshot_sha,'policy_sha256':POLICY_SHA256})[:24],
            'baseline_key':BASELINE_KEY,'snapshot_id':snapshot['snapshot_id'],'snapshot_sha256':snapshot_sha,
            'prediction_id':snapshot['prediction_id'],'trial_id':snapshot['trial_id'],'target_sha256':sha(snapshot['target']),
            **POLICY,'policy_sha256':POLICY_SHA256,'recorded_at':recorded,
            'effective_evidence_cutoff':snapshot['data_cutoff'],'source_forecast_cutoff':snapshot['data_cutoff'],'source_forecast_locked_at':snapshot['locked_at'],
            'registration_origin':registration_origin,'timestamp_attestation':'server_clock_not_independent_timestamp'}
    _binding(record,snapshot,snapshot_sha)
    h=sha(record)
    db.execute('INSERT INTO platform_prospective_baselines VALUES(?,?,?,?,?,?)',(record['baseline_id'],record['snapshot_id'],POLICY_VERSION,h,store.pack(record),recorded))
    return record|{'record_sha256':h,'created':True}


def register_existing_public_baselines(store):
    """Explicit migration: current registration times, unchanged source snapshots."""
    from .platform_store import PlatformStore
    PlatformStore(store)
    records=[]
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        for row in db.execute('SELECT * FROM platform_prediction_snapshots WHERE published=1 ORDER BY created,id'):
            snapshot=store.unpack(row['payload'])
            records.append(register_snapshot(store,db,snapshot,row['sha256'],'explicit_public_snapshot_migration'))
    return {'policy_version':POLICY_VERSION,'policy_sha256':POLICY_SHA256,'registered':sum(r['created'] for r in records),
            'existing':sum(not r['created'] for r in records),'records':records,
            'source_snapshots_changed':False,'registration_time_policy':'actual_server_time_never_backfilled'}


def audit_for_snapshot(store,db,snapshot,snapshot_sha,evaluation=None):
    if snapshot.get('published') is not True:
        return {'baseline_key':BASELINE_KEY,'eligible':False,'exclusion_reasons':['private_snapshot_not_in_public_baseline_registry'],'state':'not_eligible'}
    row=db.execute('SELECT * FROM platform_prospective_baselines WHERE snapshot_id=? AND policy_version=?',(snapshot['snapshot_id'],POLICY_VERSION)).fetchone()
    if not row:return {'baseline_key':BASELINE_KEY,'eligible':False,'exclusion_reasons':['baseline_not_registered'],'state':'not_eligible'}
    record=_decode(store,row);_binding(record,snapshot,snapshot_sha)
    reasons=[]
    if not evaluation or evaluation.get('verification_status')!='approved':reasons.append('awaiting_independent_verified_readout')
    else:
        if (evaluation.get('snapshot_id'),evaluation.get('prediction_id'))!=(snapshot['snapshot_id'],snapshot['prediction_id']):
            raise ValueError('Baseline evaluation target binding failed')
        if stamp(record['recorded_at'])>=stamp(evaluation['readout_at']):reasons.append('baseline_not_registered_before_readout')
        if evaluation.get('eligible_for_prospective') is not True:reasons.append('prediction_not_eligible_for_prospective_comparison')
    state='awaiting_verified_readout' if 'awaiting_independent_verified_readout' in reasons else 'not_eligible' if reasons else 'timing_eligible_after_verified_readout'
    return record|{'record_sha256':row['sha256'],'eligible':not reasons,'exclusion_reasons':reasons,'state':state,'integrity_verified':True}


def _view(store,db,row):
    _decode(store,row)
    snapshot=store.unpack(row['snapshot_payload'])
    if sha(snapshot)!=row['snapshot_sha']:raise ValueError('Baseline source snapshot integrity failed')
    evaluation_row=db.execute('SELECT * FROM platform_evaluation_results WHERE snapshot_id=? ORDER BY created,rowid LIMIT 1',(row['snapshot_id'],)).fetchone()
    evaluation=None
    if evaluation_row:
        evaluation=store.unpack(evaluation_row['payload'])
        if sha(evaluation)!=evaluation_row['sha256']:raise ValueError('Baseline result verification integrity failed')
    return audit_for_snapshot(store,db,snapshot,row['snapshot_sha'],evaluation)|{'target':snapshot['target']}


def list_baselines(store,limit=20,offset=0):
    from .platform_store import page_sql
    suffix,args=page_sql(limit,offset)
    with store.connect() as db:
        rows=db.execute('SELECT b.*,s.payload AS snapshot_payload,s.sha256 AS snapshot_sha FROM platform_prospective_baselines b JOIN platform_prediction_snapshots s ON s.id=b.snapshot_id WHERE s.published=1 ORDER BY b.created DESC,b.id DESC'+suffix,args)
        return [_view(store,db,row) for row in rows]


def get_baseline(store,baseline_id):
    with store.connect() as db:
        row=db.execute('SELECT b.*,s.payload AS snapshot_payload,s.sha256 AS snapshot_sha FROM platform_prospective_baselines b JOIN platform_prediction_snapshots s ON s.id=b.snapshot_id WHERE s.published=1 AND b.id=?',(baseline_id,)).fetchone()
        if not row:raise KeyError(baseline_id)
        return _view(store,db,row)


def summary(store):
    result={'public_locked_snapshot_count':0,'registered_snapshot_count':0,'missing_registration_count':0,
            'awaiting_verified_readout_count':0,'pre_readout_registered_verified_count':0,
            'late_registered_verified_count':0,'other_ineligible_verified_count':0,
            'policy_version':POLICY_VERSION,'policy_sha256':POLICY_SHA256,'scope':'public_snapshot_targets_not_model_rows',
            'comparison_note':'Per-model primary-version and common-case eligibility is determined separately in analytics.'}
    with store.connect() as db:
        result['public_locked_snapshot_count']=db.execute('SELECT COUNT(*) FROM platform_prediction_snapshots WHERE published=1').fetchone()[0]
        rows=db.execute('SELECT b.*,s.payload AS snapshot_payload,s.sha256 AS snapshot_sha FROM platform_prospective_baselines b JOIN platform_prediction_snapshots s ON s.id=b.snapshot_id WHERE s.published=1')
        for row in rows:
            item=_view(store,db,row);result['registered_snapshot_count']+=1
            key='awaiting_verified_readout_count' if item['state']=='awaiting_verified_readout' else 'late_registered_verified_count' if 'baseline_not_registered_before_readout' in item['exclusion_reasons'] else 'pre_readout_registered_verified_count' if item['eligible'] else 'other_ineligible_verified_count'
            result[key]+=1
    result['missing_registration_count']=result['public_locked_snapshot_count']-result['registered_snapshot_count']
    return result
