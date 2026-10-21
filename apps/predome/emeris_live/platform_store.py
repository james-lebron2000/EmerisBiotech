"""Encrypted, append-only prediction snapshots and independently reviewed outcomes.

Timestamps are server timestamps, not an external timestamp attestation. No result
is inferred from a missing announcement. Scientific labels need explicit review.
"""
from datetime import datetime, timezone
import hashlib
import json
import math
import re
import secrets
from urllib.parse import urlsplit
from .evaluation_metadata import preview_metadata, published_metadata, evaluation_fields, CLASSIFICATION_OPTIONS


def now():
    return datetime.now(timezone.utc).isoformat()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def instant(value):
    if not isinstance(value, str):
        raise ValueError('A dated source or cutoff is required')
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('Timestamp timezone required')
    return result.astimezone(timezone.utc)


def valid_url(value):
    if not isinstance(value, str):
        return False
    parsed = urlsplit(value)
    return parsed.scheme == 'https' and bool(parsed.netloc) and not parsed.username and not parsed.password


def target_key(target):
    keys = ('endpoint', 'population', 'comparison', 'success_definition')
    return {k: str(target.get(k, '')).strip() for k in keys}


def evidence_date(source):
    return next((source.get(k) for k in ('availability_date', 'available_date', 'evidence_availability_date', 'source_date', 'published_at') if source.get(k)), None)


def probabilities(outputs):
    values = outputs.get('forecasts', {})
    if not isinstance(values, dict):
        raise ValueError('Forecasts must be keyed by model')
    for model, value in values.items():
        if not isinstance(model, str) or not isinstance(value, dict):
            raise ValueError('Malformed model forecast')
        p, status = value.get('probability'), value.get('status')
        if status not in ('predicted', 'abstain', 'unavailable', 'failed'):
            raise ValueError('Invalid model output status')
        if status == 'predicted':
            if isinstance(p, bool) or not isinstance(p, (int, float)) or not math.isfinite(p) or not 0 <= p <= 1:
                raise ValueError('Predicted probability must be finite and between zero and one')
        elif p is not None:
            raise ValueError('A non-prediction must not contain a probability')
    return values



def validate_ensemble(record, boundary, verify_current_request=False):
    """Validate optional new reports without ever retrofitting old snapshots.

    Current prompt/request reconstruction is only a creation-time gate. Later
    locks check the frozen packet/component bindings; software upgrades must not
    reinterpret an existing report using a newly changed prompt.
    """
    outputs=record['outputs']
    if 'ensemble' not in outputs:return
    ensemble=outputs['ensemble']
    from .prediction_ensemble import build_ensemble, MEMBERS
    if not isinstance(ensemble,dict):raise ValueError('Malformed ensemble')
    p=ensemble.get('probability')
    if p is not None and (type(p) not in (int,float) or not math.isfinite(p) or not 0<=p<=1):
        raise ValueError('Ensemble probability must be numeric or null')
    packet={key:outputs.get(key) for key in ('profile','target','evidence','data_cutoff')}
    if (packet['target']!=record['target'] or packet['data_cutoff']!=record['data_cutoff']
            or not isinstance(packet['profile'],dict) or not isinstance(packet['evidence'],list)):
        raise ValueError('Ensemble report target, cutoff or packet differs from saved prediction')
    if record['input_snapshot'].get('profile')!=packet['profile']:
        raise ValueError('Ensemble profile differs from saved input')
    generated=ensemble.get('generated_at_utc')
    if instant(generated)>instant(boundary) or instant(generated)<instant(record['data_cutoff']):
        raise ValueError('Ensemble generation must precede snapshot lock and follow cutoff')
    members=ensemble.get('members')
    if not isinstance(members,list) or any(not isinstance(m,dict) for m in members):
        raise ValueError('Malformed ensemble members')
    if any(m.get('probability') is not None and (type(m['probability']) not in (int,float) or not math.isfinite(m['probability']) or not 0<=m['probability']<=1) for m in members):
        raise ValueError('Ensemble member probability must be numeric or null')
    if verify_current_request:
        from .prediction_service import model_request, SYSTEM_PROMPT
        prompt_hash=hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest()
        requests={model:digest(model_request(model,packet)) for model in MEMBERS}
    else:
        # Expected bindings are frozen in the original member outputs. Available
        # ensembles must still have a common prompt and canonical input.
        forecasts=outputs.get('forecasts',{})
        prompts={f.get('prompt_sha256') for f in forecasts.values() if isinstance(f,dict) and f.get('prompt_sha256')}
        prompt_hash=next(iter(prompts)) if len(prompts)==1 else None
        requests={model:forecasts.get(model,{}).get('request_sha256') for model in MEMBERS}
        # Unavailable reasons depend on creation-time missing hashes. There is
        # no average to authorize; retain the original frozen diagnostic codes.
        if ensemble.get('status')!='available':
            if ensemble.get('probability') is not None:raise ValueError('Unavailable ensemble must have null probability')
            if ensemble.get('target_sha256')!=digest(packet['target']) or ensemble.get('input_sha256')!=digest(packet):
                raise ValueError('Ensemble packet hash mismatch')
            return
    expected=build_ensemble(method=outputs.get('method'),forecasts=outputs.get('forecasts'),packet=packet,
        prompt_sha256=prompt_hash,request_sha256_by_model=requests,generated_at_utc=generated)
    keys=('status','probability','policy_version','policy_sha256','target_sha256','input_sha256','data_cutoff',
          'generated_at_utc','clinical_prediction_validated','evaluation_scope','uncertainty_kind','model_range','reason_codes')
    if any(ensemble.get(key)!=expected.get(key) for key in keys):raise ValueError('Ensemble policy, value or binding mismatch')
    member_keys=('model','model_version','returned_model','status','probability','weight','input_sha256','request_sha256','prompt_sha256','component_sha256')
    projected=[{key:m.get(key) for key in member_keys} for m in members]
    if projected!=expected['members']:raise ValueError('Ensemble component binding mismatch')


def public_ensemble(value):
    """Safe projection: no free-text provider explanations or nested evidence."""
    keys=('status','probability','policy_version','policy_sha256','target_sha256','input_sha256','data_cutoff','generated_at_utc')
    member_keys=('model','model_version','returned_model','status','probability','weight','input_sha256','request_sha256','prompt_sha256','component_sha256')
    reasons={'available':'两个指定模型按预先固定的 50% / 50% 权重汇总；仅作描述性展示，未经临床校准。',
             'unavailable':'双模型输入或成员校验未全部通过，不计算部分成员平均值。',
             'not_applicable':'当前方法不适用双模型描述性集成。'}
    result={key:value.get(key) for key in keys}
    result.update(members=[{key:m.get(key) for key in member_keys} for m in value.get('members',[])],
        reason=reasons.get(value.get('status'),'集成状态不可用。'),clinical_prediction_validated=False,
        evaluation_scope='descriptive_only_excluded_from_model_performance',uncertainty_kind='model_range_not_confidence_interval')
    ps=[m['probability'] for m in result['members'] if type(m.get('probability')) in (int,float)]
    result['model_range']={'min':min(ps),'max':max(ps),'spread':max(ps)-min(ps)} if value.get('status')=='available' and len(ps)==2 else None
    return result

def public_result_source(source):
    """Approved source excerpt only; omit unrelated notes and full source text."""
    result={k:source.get(k) for k in ('url','published_at','sha256')}
    excerpt=source.get('excerpt')
    basis=source.get('publication_date_basis')
    if (source.get('candidate_kind')=='official_announcement_candidate'
            and source.get('quote_validation')=='continuous_normalized_excerpt_reextracted_from_verified_source_bytes'
            and isinstance(excerpt,str) and 20<=len(excerpt)<=6000
            and source.get('excerpt_sha256')==hashlib.sha256(excerpt.encode()).hexdigest()
            and basis in ('document_metadata','reviewer_provided_not_independently_verified')):
        result.update(excerpt=excerpt,excerpt_sha256=source['excerpt_sha256'],publication_date_basis=basis)
    return result

def page_sql(limit=None, offset=0):
    """None is an explicit complete internal scan; HTTP callers always bound it."""
    if type(offset) is not int or offset < 0:
        raise ValueError('Offset must be a nonnegative integer')
    if limit is None:
        if offset:raise ValueError('Offset requires a page limit')
        return '', ()
    # One extra row is permitted for API has_more without a second count scan.
    if type(limit) is not int or not 1 <= limit <= 101:
        raise ValueError('Page limit must be between 1 and 101')
    return ' LIMIT ? OFFSET ?', (limit, offset)


class PlatformStore:
    def __init__(self, store):
        self.store = store
        with store.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS platform_trials(id TEXT PRIMARY KEY,owner TEXT NOT NULL,payload BLOB NOT NULL,created TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS platform_trial_versions(id TEXT PRIMARY KEY,trial_id TEXT NOT NULL,owner TEXT NOT NULL,sha256 TEXT NOT NULL,payload BLOB NOT NULL,created TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS platform_model_versions(id TEXT PRIMARY KEY,sha256 TEXT NOT NULL,payload BLOB NOT NULL,created TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS platform_prediction_runs(id TEXT PRIMARY KEY,trial_id TEXT NOT NULL,trial_version_id TEXT NOT NULL,owner TEXT NOT NULL,model_version_id TEXT NOT NULL,parent_id TEXT,sha256 TEXT NOT NULL,payload BLOB NOT NULL,created TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS platform_prediction_evidence(id TEXT PRIMARY KEY,prediction_id TEXT NOT NULL,owner TEXT NOT NULL,sha256 TEXT NOT NULL,payload BLOB NOT NULL);
            CREATE TABLE IF NOT EXISTS platform_prediction_snapshots(id TEXT PRIMARY KEY,prediction_id TEXT NOT NULL UNIQUE,trial_id TEXT NOT NULL,owner TEXT NOT NULL,published INTEGER NOT NULL,sha256 TEXT NOT NULL UNIQUE,previous_sha256 TEXT,payload BLOB NOT NULL,created TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS platform_actual_outcomes(id TEXT PRIMARY KEY,snapshot_id TEXT NOT NULL,submitted_by TEXT NOT NULL,sha256 TEXT NOT NULL,payload BLOB NOT NULL,created TEXT NOT NULL,UNIQUE(snapshot_id,sha256));
            CREATE TABLE IF NOT EXISTS platform_outcome_verifications(id TEXT PRIMARY KEY,outcome_id TEXT NOT NULL,reviewer TEXT NOT NULL,decision TEXT NOT NULL,sha256 TEXT NOT NULL,payload BLOB NOT NULL,created TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS platform_evaluation_results(id TEXT PRIMARY KEY,verification_id TEXT NOT NULL,snapshot_id TEXT NOT NULL,sha256 TEXT NOT NULL,payload BLOB NOT NULL,created TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS platform_readout_events(id TEXT PRIMARY KEY,trial_id TEXT NOT NULL,outcome_id TEXT NOT NULL,sha256 TEXT NOT NULL,payload BLOB NOT NULL,created TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS platform_runs_owner_order ON platform_prediction_runs(owner,created DESC,id DESC);
            CREATE INDEX IF NOT EXISTS platform_snapshots_primary_order ON platform_prediction_snapshots(published,created);
            CREATE INDEX IF NOT EXISTS platform_snapshots_public_order ON platform_prediction_snapshots(published,created DESC,id DESC);
            CREATE INDEX IF NOT EXISTS platform_outcomes_snapshot_order ON platform_actual_outcomes(snapshot_id,created DESC,id DESC);
            CREATE INDEX IF NOT EXISTS platform_outcomes_order ON platform_actual_outcomes(created DESC,id DESC);
            CREATE INDEX IF NOT EXISTS platform_verifications_outcome_order ON platform_outcome_verifications(outcome_id,created);
            CREATE INDEX IF NOT EXISTS platform_verifications_decision_order ON platform_outcome_verifications(decision,created DESC,id DESC);
            CREATE INDEX IF NOT EXISTS platform_evaluations_order ON platform_evaluation_results(created);

            CREATE INDEX IF NOT EXISTS platform_runs_owner ON platform_prediction_runs(owner,created);
            CREATE INDEX IF NOT EXISTS platform_snapshot_owner ON platform_prediction_snapshots(owner,created);
            CREATE INDEX IF NOT EXISTS platform_outcome_snapshot ON platform_actual_outcomes(snapshot_id,created);
            ''')
            for table in ('trials', 'trial_versions', 'model_versions', 'prediction_runs', 'prediction_evidence', 'prediction_snapshots', 'actual_outcomes', 'outcome_verifications', 'evaluation_results', 'readout_events'):
                name = 'platform_' + table
                for action in ('UPDATE', 'DELETE'):
                    db.execute(f"CREATE TRIGGER IF NOT EXISTS {name}_no_{action.lower()} BEFORE {action} ON {name} BEGIN SELECT RAISE(ABORT,'append-only clinical ledger'); END")

        from .prospective_baselines import initialize
        initialize(self.store)

    def _decode(self, row):
        content = self.store.unpack(row['payload'])
        if 'sha256' in row.keys() and digest(content) != row['sha256']:
            raise ValueError('Clinical ledger integrity check failed')
        return content

    def create_run(self, owner, input_snapshot, outputs, model_version, pipeline_version, data_cutoff,
                   evidence=None, target=None, parent_prediction_id=None, training_data_cutoff=None, generated_at=None):
        if not isinstance(owner, str) or not owner or not isinstance(input_snapshot, dict) or not isinstance(outputs, dict):
            raise ValueError('Prediction owner, structured input and output required')
        generated_at = generated_at or now()
        generated = instant(generated_at)
        if generated > instant(now()) or instant(data_cutoff) > generated:
            raise ValueError('Evidence cutoff cannot follow prediction generation')
        if training_data_cutoff and instant(training_data_cutoff) > generated:
            raise ValueError('Training cutoff cannot follow prediction generation')
        if not model_version or not pipeline_version:
            raise ValueError('Model and pipeline versions required')
        forecasts = probabilities(outputs)
        evidence = evidence or []
        if not isinstance(evidence, list) or any(not isinstance(x, dict) for x in evidence):
            raise ValueError('Evidence must be a list of source records')
        for item in evidence:
            dated = evidence_date(item)
            if dated and instant(dated) > instant(data_cutoff):
                raise ValueError('Evidence published after prediction cutoff')
        # No caller is permitted to backdate a lock. Generation time can be the
        # trusted provider completion time, but is never accepted by HTTP input.
        run_id = 'pr_' + secrets.token_hex(12)
        raw_trial = str(input_snapshot.get('nct_id') or input_snapshot.get('trial_id') or input_snapshot.get('trial_name') or run_id)
        trial_id = 'tr_' + digest({'owner': owner, 'identity': raw_trial})[:24]
        tv_id = 'tv_' + secrets.token_hex(12)
        version_record = {'model_version': model_version, 'pipeline_version': pipeline_version,
                          'training_data_cutoff': training_data_cutoff, 'training_cutoff_status': 'known' if training_data_cutoff else 'unknown'}
        mv_id = 'mv_' + digest(version_record)[:24]
        record = {'schema_version': 'predome.run.v1', 'id': run_id, 'prediction_id': run_id, 'trial_id': raw_trial,
                  'internal_trial_id': trial_id, 'trial_version_id': tv_id, 'model_version_id': mv_id,
                  'status': 'draft', 'created_at': generated_at, 'prediction_timestamp': generated_at,
                  'input_snapshot': input_snapshot, 'outputs': outputs, 'model_version': model_version,
                  'pipeline_version': pipeline_version, 'data_cutoff': data_cutoff, 'evidence': evidence,
                  'target': target_key(target or {}), 'parent_prediction_id': parent_prediction_id,
                  'model_training_data_cutoff': training_data_cutoff, 'pretraining_contamination_excluded': False,
                  'clinical_prediction_validated': False}
        validate_ensemble(record, generated_at, verify_current_request=True)
        canonical(record)
        if len(canonical(record).encode()) > 2*1024*1024:
            raise ValueError('Prediction record exceeds 2 MiB')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT count(*) FROM platform_prediction_runs WHERE owner=?', (owner,)).fetchone()[0] >= 300:
                raise ValueError('Account prediction storage limit reached')
            if parent_prediction_id:
                parent = db.execute('SELECT * FROM platform_prediction_runs WHERE id=? AND owner=?', (parent_prediction_id,owner)).fetchone()
                if not parent:
                    raise KeyError('Prediction parent unavailable')
                if parent['trial_id'] != trial_id:
                    raise ValueError('Prediction versions must refer to the same trial')
                self._decode(parent)
            trial = {'trial_id': raw_trial, 'created_at': generated_at}
            db.execute('INSERT OR IGNORE INTO platform_trials VALUES(?,?,?,?)', (trial_id,owner,self.store.pack(trial),generated_at))
            db.execute('INSERT INTO platform_trial_versions VALUES(?,?,?,?,?,?)', (tv_id,trial_id,owner,digest(input_snapshot),self.store.pack(input_snapshot),generated_at))
            existing = db.execute('SELECT * FROM platform_model_versions WHERE id=?', (mv_id,)).fetchone()
            if existing:
                if self._decode(existing) != version_record:raise ValueError('Model version conflict')
            else:
                db.execute('INSERT INTO platform_model_versions VALUES(?,?,?,?)',(mv_id,digest(version_record),self.store.pack(version_record),generated_at))
            db.execute('INSERT INTO platform_prediction_runs VALUES(?,?,?,?,?,?,?,?,?)',
                       (run_id,trial_id,tv_id,owner,mv_id,parent_prediction_id,digest(record),self.store.pack(record),generated_at))
            for item in evidence:
                db.execute('INSERT INTO platform_prediction_evidence VALUES(?,?,?,?,?)',
                           ('pe_'+secrets.token_hex(12),run_id,owner,digest(item),self.store.pack(item)))
        return record | {'sha256':digest(record)}

    def get_run(self, owner, run_id):
        with self.store.connect() as db:
            row = db.execute('SELECT * FROM platform_prediction_runs WHERE id=? AND owner=?',(run_id,owner)).fetchone()
            if not row:raise KeyError('Prediction not found')
            record = self._decode(row)
            lock = db.execute('SELECT id,created,published FROM platform_prediction_snapshots WHERE prediction_id=?',(run_id,)).fetchone()
            return record | {'sha256':row['sha256'], 'status':'locked' if lock else 'draft',
                             'snapshot_id':lock['id'] if lock else None, 'locked_at':lock['created'] if lock else None,
                             'published':bool(lock['published']) if lock else False}

    def list_runs(self, owner, limit=20, offset=0):
        """Owner-only summaries. Full private reports use get_run individually."""
        suffix, args = page_sql(limit, offset)
        with self.store.connect() as db:
            rows = db.execute('SELECT r.*,s.id AS snapshot_id,s.created AS locked_at,s.published AS published FROM platform_prediction_runs r LEFT JOIN platform_prediction_snapshots s ON s.prediction_id=r.id WHERE r.owner=? ORDER BY r.created DESC,r.id DESC'+suffix,(owner,)+args)
            result=[]
            for row in rows:
                record=self._decode(row)
                result.append({k:record.get(k) for k in ('id','prediction_id','trial_id','created_at','prediction_timestamp','model_version','pipeline_version','data_cutoff','parent_prediction_id')} | {
                    'target':{'endpoint':record.get('target',{}).get('endpoint','')[:1000]},
                    'sha256':row['sha256'],'status':'locked' if row['snapshot_id'] else 'draft',
                    'snapshot_id':row['snapshot_id'],'locked_at':row['locked_at'],
                    'published':bool(row['published']),'summary_only':True,
                    'detail_url':'/api/platform/predictions/'+row['id']})
            return result

    def evaluation_metadata_preview(self, owner, run_id, evaluation_classification=None):
        run=self.get_run(owner,run_id)
        frozen=None
        if run.get('snapshot_id'):
            frozen=published_metadata(self.get_snapshot(run['snapshot_id'],owner=owner)['record'])
        return {'prediction_id':run_id,'metadata':preview_metadata(run['input_snapshot'],evaluation_classification),
                'classification_options':{k:list(v) for k,v in CLASSIFICATION_OPTIONS.items()},
                'locked':run['status']=='locked','metadata_already_published':frozen is not None,'frozen_metadata':frozen,
                'notice':'Optional public evaluation fields copied from saved input; classifications are user-selected, not medically validated. Other private input stays private.'}

    def lock(self, owner, run_id, publish=False, publish_evaluation_metadata=False,
             evaluation_classification=None, confirm_evaluation_metadata=False, confirm_public=False):
        if publish_evaluation_metadata and not (publish and confirm_public and confirm_evaluation_metadata):
            raise ValueError('Public evaluation metadata requires separate explicit public and metadata consent')
        if not publish_evaluation_metadata and (evaluation_classification is not None or confirm_evaluation_metadata):
            raise ValueError('Evaluation classification requires explicit metadata publication consent')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM platform_prediction_runs WHERE id=? AND owner=?',(run_id,owner)).fetchone()
            if not row:raise KeyError('Prediction not found')
            record = self._decode(row)
            metadata=preview_metadata(record['input_snapshot'],evaluation_classification) if publish_evaluation_metadata else None
            old = db.execute('SELECT * FROM platform_prediction_snapshots WHERE prediction_id=?',(run_id,)).fetchone()
            if old:
                if bool(old['published']) != bool(publish):raise ValueError('Locked visibility is immutable; create a new version')
                frozen=self._decode(old)
                if bool(frozen.get('evaluation_metadata_published'))!=bool(publish_evaluation_metadata) or frozen.get('evaluation_metadata')!=metadata:
                    raise ValueError('Locked evaluation metadata is immutable; create a new version')
                return {'record':frozen,'sha256':old['sha256'],'integrity_verified':True}
            if not all(target_key(record['target']).values()):
                raise ValueError('Endpoint, population, comparison and prespecified success definition required before locking')
            forecasts = probabilities(record['outputs'])
            if record['outputs'].get('forecast_kind') == 'conditional_scenario' or not any(x['status']=='predicted' for x in forecasts.values()):
                raise ValueError('No actual endpoint prediction available to lock; retain this research report as a draft')
            locked_at = now()
            validate_ensemble(record, locked_at)
            prior = db.execute('SELECT sha256 FROM platform_prediction_snapshots WHERE owner=? ORDER BY created DESC,rowid DESC LIMIT 1',(owner,)).fetchone()
            snapshot_id = 'ps_'+secrets.token_hex(12)
            record = dict(record)
            record.update({'schema_version':'predome.snapshot.v1','id':snapshot_id,'snapshot_id':snapshot_id,
                           'status':'locked','locked_at':locked_at,'published':bool(publish),
                           'previous_snapshot_sha256':prior['sha256'] if prior else None,
                           'timestamp_attestation':'server_clock_not_independent_timestamp',
                           'prospective_eligibility_status':'awaiting_independent_outcome_verification'})
            if publish_evaluation_metadata:
                record['evaluation_metadata_published']=True
                record['evaluation_metadata']=metadata
                record['evaluation_metadata_consent']={'confirmed_at':locked_at,'scope':'asset_phase_modality_design_and_user_evaluation_classification','independent_medical_validation':False}
            sha = digest(record)
            db.execute('INSERT INTO platform_prediction_snapshots VALUES(?,?,?,?,?,?,?,?,?)',
                       (snapshot_id,run_id,row['trial_id'],owner,int(bool(publish)),sha,record['previous_snapshot_sha256'],self.store.pack(record),locked_at))
            if publish:
                from .prospective_baselines import register_snapshot
                register_snapshot(self.store,db,record,sha,'new_public_snapshot_lock')
            return {'record':record,'sha256':sha,'integrity_verified':True}

    def _snapshot(self, db, snapshot_id):
        row=db.execute('SELECT * FROM platform_prediction_snapshots WHERE id=?',(snapshot_id,)).fetchone()
        if not row:raise KeyError('Snapshot not found')
        return row,self._decode(row)

    @staticmethod
    def public_record(record, sha):
        # Publishing exposes the explicitly confirmed target and forecast only.
        # Never return uploaded protocol text, arbitrary profile fields, or LLM
        # reasoning that might reproduce confidential source paragraphs.
        return {'record':{key:record[key] for key in ('schema_version','id','snapshot_id','prediction_id','trial_id','status','created_at','prediction_timestamp','model_version','pipeline_version','data_cutoff','target','parent_prediction_id','model_training_data_cutoff','locked_at','timestamp_attestation','clinical_prediction_validated')} | {
            'forecasts':{m:{'status':f['status'],'probability':f.get('probability'),'model_version':f.get('model_version') or record['model_version'],'generated_at_utc':f.get('generated_at_utc') or record['prediction_timestamp']} for m,f in record['outputs']['forecasts'].items()},
            'evidence_sources':[{'url':x.get('url') or x.get('source_url'),'availability_date':evidence_date(x)} for x in record['evidence'] if valid_url(x.get('url') or x.get('source_url'))],
            'provenance':record.get('provenance','generated_prediction'),
            'archive_provenance':record.get('archive_provenance'),'imported_at':record.get('imported_at'),
            'input_snapshot_sha256':digest(record['input_snapshot']),'public_view':'explicitly_published_summary_not_full_private_input'} | ({'evaluation_metadata':published_metadata(record)} if published_metadata(record) is not None else {}) | ({'ensemble':public_ensemble(record['outputs']['ensemble'])} if 'ensemble' in record['outputs'] else {}),
            'sha256':sha,'integrity_verified':True,'public_view_hash_scope':'sha256_is_of_full_private_snapshot; redacted_summary_is_not_hash_reproducible'}

    def get_snapshot(self, snapshot_id, owner=None, reviewer=False):
        with self.store.connect() as db:
            row,record=self._snapshot(db,snapshot_id)
            if reviewer or row['owner']==owner:return {'record':record,'sha256':row['sha256'],'integrity_verified':True}
            if not row['published']:raise KeyError('Snapshot not found')
            return self.public_record(record,row['sha256'])

    def list_public_snapshots(self, limit=None, offset=0):
        # Internal consumers deliberately get the complete redacted collection.
        # API supplies a bounded page. Never retain all encrypted payloads.
        suffix,args=page_sql(limit,offset)
        with self.store.connect() as db:
            rows=db.execute('SELECT * FROM platform_prediction_snapshots WHERE published=1 ORDER BY created DESC,id DESC'+suffix,args)
            return [self.public_record(self._decode(row),row['sha256']) for row in rows]

    def submit_outcome(self, owner, snapshot_id, outcome, allow_other_owner=False):
        if not isinstance(outcome,dict):raise ValueError('Structured outcome required')
        label=outcome.get('label')
        if label is not None and (type(label) is not int or label not in (0,1)):raise ValueError('Outcome label must be 0, 1, or null')
        if len(canonical(outcome).encode())>256*1024:raise ValueError('Outcome record too large')
        p = outcome.get('p_value')
        if p is not None and (isinstance(p,bool) or not isinstance(p,(int,float)) or not math.isfinite(p) or not 0<=p<=1):
            raise ValueError('P value must be between zero and one, or unknown')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            snap,record=self._snapshot(db,snapshot_id)
            if snap['owner']!=owner and not allow_other_owner:raise KeyError('Snapshot not found')
            sha=digest(outcome)
            prior=db.execute('SELECT * FROM platform_actual_outcomes WHERE snapshot_id=? AND sha256=?',(snapshot_id,sha)).fetchone()
            if prior:return {'id':prior['id'],'snapshot_id':snapshot_id,'outcome':self._decode(prior),'status':'submitted'}
            oid='oc_'+secrets.token_hex(12);created=now()
            db.execute('INSERT INTO platform_actual_outcomes VALUES(?,?,?,?,?,?)',(oid,snapshot_id,owner,sha,self.store.pack(outcome),created))
            event={'outcome_id':oid,'snapshot_id':snapshot_id,'source':outcome.get('source'),'readout_date':outcome.get('readout_date'),'verification_status':'pending'}
            db.execute('INSERT INTO platform_readout_events VALUES(?,?,?,?,?,?)',('re_'+secrets.token_hex(12),snap['trial_id'],oid,digest(event),self.store.pack(event),created))
            return {'id':oid,'snapshot_id':snapshot_id,'outcome':outcome,'status':'pending_review','created_at':created}

    def list_outcomes(self, owner=None, reviewer=False, limit=None, offset=0):
        suffix,page_args=page_sql(limit,offset)
        with self.store.connect() as db:
            sql='SELECT o.*,s.owner AS prediction_owner FROM platform_actual_outcomes o JOIN platform_prediction_snapshots s ON o.snapshot_id=s.id'
            args=()
            if not reviewer:sql+=' WHERE s.owner=?';args=(owner,)
            rows=db.execute(sql+' ORDER BY o.created DESC,o.id DESC'+suffix,args+page_args)
            result=[]
            for row in rows:
                reviews=[self._decode(r) for r in db.execute('SELECT * FROM platform_outcome_verifications WHERE outcome_id=? ORDER BY created,rowid',(row['id'],))]
                result.append({'id':row['id'],'snapshot_id':row['snapshot_id'],'submitted_by':row['submitted_by'],
                               'created_at':row['created'],'outcome':self._decode(row),'status':reviews[-1]['decision'] if reviews else 'pending_review',
                               'reviews':reviews})
            return result

    def _approval_errors(self, snapshot, outcome, confirmations):
        errors=[]
        if type(outcome.get('label')) is not int or outcome.get('label') not in (0,1):errors.append('definitive_binary_result_missing')
        if outcome.get('extraction_status')!='complete':errors.append('extraction_incomplete')
        if outcome.get('conflicts'):errors.append('source_conflict_unresolved')
        for field in ('endpoint','population','comparison'):
            if str(outcome.get(field,'')).strip()!=snapshot['target'][field]:errors.append(field+'_mismatch')
        if str(outcome.get('trial_id',''))!=snapshot['trial_id']:errors.append('trial_mismatch')
        for field in ('endpoint_match','population_match','comparison_match','source_checked','prespecified_analysis','earliest_result_checked','evidence_cutoff_checked'):
            if confirmations.get(field) is not True:errors.append('review_confirmation_missing:'+field)
        source=outcome.get('source') or {}
        if not valid_url(source.get('url')):errors.append('https_primary_source_required')
        try:
            published=instant(source.get('published_at'));readout=instant(outcome.get('readout_date'))
            if published>instant(now()) or readout>instant(now()):errors.append('future_result_date')
            if published<readout:errors.append('source_precedes_claimed_readout')
        except (ValueError,TypeError):errors.append('result_dates_missing_or_invalid')
        return errors

    def verify_outcome(self, reviewer_id, outcome_id, decision, reason, confirmations=None):
        if decision not in ('approved','rejected','needs_review'):raise ValueError('Invalid review decision')
        if not isinstance(reason,str) or len(reason.strip())<10:raise ValueError('Review rationale must be at least 10 characters')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM platform_actual_outcomes WHERE id=?',(outcome_id,)).fetchone()
            if not row:raise KeyError('Outcome not found')
            snap,snapshot=self._snapshot(db,row['snapshot_id']);outcome=self._decode(row)
            if reviewer_id in (row['submitted_by'],snap['owner']):raise ValueError('Reviewer must be independent of submitter and prediction owner')
            old=db.execute("SELECT id FROM platform_outcome_verifications WHERE outcome_id=? AND decision IN ('approved','rejected')",(outcome_id,)).fetchone()
            if old:raise ValueError('Final review is immutable; submit a new corrected outcome record')
            if decision=='approved':
                errors=self._approval_errors(snapshot,outcome,confirmations or {})
                if errors:raise ValueError('Approval blocked: '+', '.join(errors))
                # Competing sources for the same snapshot cannot silently replace
                # an existing accepted label. A new corrected prediction/target
                # needs a distinct record and explicit adjudication policy.
                accepted=db.execute("SELECT o.id FROM platform_actual_outcomes o JOIN platform_outcome_verifications v ON v.outcome_id=o.id WHERE o.snapshot_id=? AND v.decision='approved'",(snap['id'],)).fetchone()
                if accepted:raise ValueError('Snapshot already has an approved outcome; conflicting reports require adjudication')
            created=now();vid='ov_'+secrets.token_hex(12)
            verification={'id':vid,'outcome_id':outcome_id,'snapshot_id':snap['id'],'reviewer_id':reviewer_id,'decision':decision,'reason':reason.strip(),'confirmations':confirmations or {},'created_at':created,'independent_reviewer':True}
            db.execute('INSERT INTO platform_outcome_verifications VALUES(?,?,?,?,?,?,?)',(vid,outcome_id,reviewer_id,decision,digest(verification),self.store.pack(verification),created))
            if decision=='approved':
                evaluation=self._evaluate(snapshot,outcome,verification,bool(snap['published']))
                db.execute('INSERT INTO platform_evaluation_results VALUES(?,?,?,?,?,?)',('ev_'+secrets.token_hex(12),vid,snap['id'],digest(evaluation),self.store.pack(evaluation),created))
            return verification

    def _evaluate(self, snapshot, outcome, verification, published):
        reasons=[]
        if instant(snapshot['locked_at'])>=instant(outcome['readout_date']):reasons.append('lock_not_before_readout')
        if instant(snapshot['data_cutoff'])>instant(snapshot['created_at']):reasons.append('invalid_evidence_cutoff')
        if not snapshot['evidence']:reasons.append('no_source_evidence')
        for source in snapshot['evidence']:
            available=evidence_date(source)
            if not available:reasons.append('evidence_availability_unknown')
            elif instant(available)>instant(snapshot['data_cutoff']):reasons.append('post_cutoff_evidence')
        if snapshot['outputs'].get('forecast_kind') not in ('uncalibrated_llm_research','validated_model'):
            reasons.append('not_a_supported_endpoint_forecast')
        if not published:reasons.append('private_snapshot_not_public_performance')
        kind='prospective' if not reasons else ('retrospective' if 'lock_not_before_readout' in reasons else 'not_evaluable')
        # Training cutoff remains explicitly unknown where unavailable. An
        # actual pre-readout lock, unlike retrospective replay, establishes the
        # temporal boundary; it does not prove model calibration or validity.
        return {'snapshot_id':snapshot['snapshot_id'],'prediction_id':snapshot['prediction_id'],
                'scope':kind,'eligible_for_prospective':not reasons,'reasons':sorted(set(reasons)),
                'actual_label':outcome['label'],'readout_at':outcome['readout_date'],
                'verified_at':verification['created_at'],'verification_status':'approved','target_match':True,
                'source_url':outcome['source']['url'],'source_sha256':outcome['source'].get('sha256'),
                'published':published,'calibration_validated':False,'model_training_data_cutoff':snapshot['model_training_data_cutoff']}

    def public_outcome_records(self, limit=None, offset=0):
        """Only approved outcomes of explicitly public locks; no private notes."""
        suffix,args=page_sql(limit,offset)
        assessments = {}
        for item in self.iter_evaluation_rows(public_only=True):
            safe={k:item.get(k) for k in ('model','model_version','probability','scope','eligible_for_prospective','prospective_eligible','actual_label','locked_at','readout_at','verified_at','primary_prediction_id','prediction_status','coverage_eligible','score_eligible','selection_policy')}
            assessments.setdefault(item['snapshot_id'],[]).append(safe)
        with self.store.connect() as db:
            rows = db.execute("SELECT o.*,s.payload AS snapshot_payload,s.sha256 AS snapshot_sha,v.payload AS verification_payload,v.sha256 AS verification_sha FROM platform_actual_outcomes o JOIN platform_prediction_snapshots s ON o.snapshot_id=s.id JOIN platform_outcome_verifications v ON v.outcome_id=o.id WHERE s.published=1 AND v.decision='approved' ORDER BY v.created DESC,v.id DESC"+suffix,args)
            result = []
            for row in rows:
                outcome = self._decode(row)
                snap = self._decode({'payload':row['snapshot_payload'],'sha256':row['snapshot_sha']})
                verification = self._decode({'payload':row['verification_payload'],'sha256':row['verification_sha']})
                source = outcome['source']
                result.append({'outcome_id':row['id'],'snapshot_id':row['snapshot_id'],'prediction_id':snap['prediction_id'],
                    'trial_id':snap['trial_id'],'target':snap['target'],'label':outcome['label'],
                    'result_date':outcome['readout_date'],'readout_date':outcome['readout_date'],'effect_size':outcome.get('effect_size'),
                    'confidence_interval':outcome.get('confidence_interval'),'p_value':outcome.get('p_value'),
                    'source':public_result_source(source),
                    'verification_status':'approved','verified_at':verification['created_at'],
                    'snapshot_sha256':row['snapshot_sha'],'outcome_sha256':row['sha256'],
                    'archive_provenance':snap.get('archive_provenance'),
                    'forecasts':self.public_record(snap,row['snapshot_sha'])['record']['forecasts'],
                    'assessments':assessments.get(row['snapshot_id'],[])})
            return result

    def evaluation_rows(self, public_only=True):
        """Complete scientific denominator; never apply UI pagination here."""
        return list(self.iter_evaluation_rows(public_only=public_only))

    def iter_evaluation_rows(self, public_only=True):
        """Stream verified records after selecting primary locks over ALL versions."""
        with self.store.connect() as db:
            rows=db.execute('SELECT e.*,s.payload AS snapshot_payload,s.sha256 AS snapshot_sha FROM platform_evaluation_results e JOIN platform_prediction_snapshots s ON e.snapshot_id=s.id'+(' WHERE s.published=1' if public_only else '')+' ORDER BY e.created,e.rowid')
            primary = {}
            all_locked = db.execute('SELECT * FROM platform_prediction_snapshots WHERE published=1 ORDER BY created,rowid')
            for locked in all_locked:
                original = self._decode(locked)
                if original['outputs'].get('forecast_kind') not in ('uncalibrated_llm_research','validated_model'):continue
                for model, forecast in probabilities(original['outputs']).items():
                    # First public request wins even when this member abstained or failed.
                    # Later releases cannot replace an inconvenient first result.
                    key=(original['trial_id'],digest(original['target']),model)
                    primary.setdefault(key,original['prediction_id'])
            for row in rows:
                evaluation=self._decode(row);snap=self.store.unpack(row['snapshot_payload'])
                if digest(snap)!=row['snapshot_sha']:raise ValueError('Snapshot integrity failed')
                metadata=evaluation_fields(snap)
                for model,f in probabilities(snap['outputs']).items():
                    item=evaluation | {'trial_id':snap['trial_id'],'asset':metadata['asset'],
                        'endpoint':snap['target']['endpoint'],'target':snap['target'],'probability':f['probability'],
                        'label':evaluation['actual_label'],'model':model,'model_version':f.get('model_version') or snap['model_version'],
                        'prediction_status':f['status'],
                        'selection_policy':'first_public_locked_request_per_trial_full_target_model_across_versions',
                        'locked_at':snap['locked_at'],'generated_at':snap['created_at'],'evidence_cutoff':snap['data_cutoff'],
                        'prospective_eligible':evaluation['eligible_for_prospective'],
                        'features':{key:metadata.get(key) for key in ('phase','therapeutic_area','modality','randomized','sponsor_category')},
                        'phase':metadata.get('phase'),'therapeutic_area':metadata.get('therapeutic_area'),'modality':metadata.get('modality'),
                        'sponsor':None,'sponsor_category':metadata.get('sponsor_category'),'design':metadata.get('design'),'readout_date':evaluation['readout_at'],
                        'evaluation_metadata_provenance':metadata['evaluation_metadata_provenance'],'clinical_classification_validated':False,
                        'prediction_horizon_days':(instant(evaluation['readout_at'])-instant(snap['locked_at'])).days,
                        'baseline_probabilities':{},'provenance':snap.get('provenance','generated_prediction'),
                        'archive_provenance':snap.get('archive_provenance'),'imported_at':snap.get('imported_at')}
                    # Selection includes unreviewed and out-of-page locks.
                    key=(item['trial_id'],digest(item['target']),item['model'])
                    item['primary_prediction_id']=primary.get(key)
                    if item['eligible_for_prospective'] and primary.get(key)!=item['prediction_id']:
                        item['eligible_for_prospective']=False;item['prospective_eligible']=False
                        item['scope']='not_evaluable';item['reasons']=item['reasons']+['later_version_not_primary']
                    item['coverage_eligible']=item['eligible_for_prospective']
                    item['score_eligible']=item['coverage_eligible'] and f['status']=='predicted'
                    from .prospective_baselines import audit_for_snapshot, BASELINE_KEY
                    try:audit=audit_for_snapshot(self.store,db,snap,row['snapshot_sha'],item)
                    except ValueError:
                        audit={'baseline_key':BASELINE_KEY,'eligible':False,'integrity_verified':False,
                               'exclusion_reasons':['baseline_integrity_or_binding_failed'],'state':'not_eligible'}
                    item['snapshot_sha256']=row['snapshot_sha']
                    item['baseline_audits']=[audit]
                    item['baseline_probabilities']={BASELINE_KEY:audit['probability']} if audit.get('eligible') else {}
                    yield item


def evaluation_rows(store, public_only=True):
    return PlatformStore(store).evaluation_rows(public_only=public_only)


def import_legacy_archive(store):
    """Explicit migration of verified published research records, never a new run.

    No caller-supplied timestamps or paths are accepted. The fixed release and
    its complete artifact closure are checked before writing anything. Imported
    wrapper IDs are deterministic; repeats retain the original imported_at and
    wrapper hash. Existing source files are read only.
    """
    from pathlib import Path
    from .continuous_api import read_continuous
    root = Path(__file__).resolve().parents[1]
    release = read_continuous()
    if not release.get('integrity_verified'):
        raise ValueError('Legacy import requires full source verification')
    continuous = release['record']; archive = continuous['prospective']
    archive_sha = digest(archive)
    bound = {item['path']:item['sha256'] for item in continuous['artifact_manifest']}

    def load_bound(path):
        if path not in bound:raise ValueError('Legacy artifact missing from verified closure')
        raw = (root/path).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=bound[path]:raise ValueError('Legacy source changed during import')
        return json.loads(raw)

    base='predome_data/continuous_v4/prospective/'
    original_envelope=load_bound(base+'frozen_forecasts_v1.json')
    if original_envelope['sha256']!=archive_sha or original_envelope['record']!=archive:
        raise ValueError('Embedded and original forecast archives disagree')
    packets={item['nct_id']:item for item in load_bound(base+'candidate_packets.json')['candidates']}
    priors=load_bound(base+'prior_effect_records.json')['records']
    review=load_bound(base+'reviews/repair_llm_review.json')
    model_runs={}
    for checked in review['models']:
        if checked['remaining_major_findings']!=0:raise ValueError('Legacy model output was not cleared for publication')
        path=base+checked['output_file']
        envelope=load_bound(path);run=envelope['record']
        if digest(run)!=envelope['sha256'] or run['status']!='completed':raise ValueError('Invalid archived provider run')
        if bound[path]!=checked['output_sha256']:raise ValueError('Legacy model review hash mismatch')
        model_runs[checked['model']]=(run,path,bound[path])
    archived_owner='archived-public-release'
    ledger=PlatformStore(store);imported_at=now();inserted=0;existing=0;result=[]
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        for case in archive['cases']:
            packet=packets[case['nct_id']]
            suffix=digest({'archive':archive_sha,'case_id':case['case_id'],'adapter':'legacy-import-v1'})[:24]
            run_id='pr_archive_'+suffix;snapshot_id='ps_archive_'+suffix
            present=db.execute('SELECT * FROM platform_prediction_snapshots WHERE id=?',(snapshot_id,)).fetchone()
            if present:
                old=ledger._decode(present)
                if old.get('archive_provenance',{}).get('original_record_sha256')!=archive_sha or old.get('archive_provenance',{}).get('case_id')!=case['case_id']:
                    raise ValueError('Legacy wrapper conflicts with source archive')
                if {m:{'status':f['status'],'probability':f.get('probability')} for m,f in old['outputs']['forecasts'].items()}!={m:{'status':f['status'],'probability':f.get('probability')} for m,f in case['forecasts'].items()}:
                    raise ValueError('Imported probability differs from original')
                result.append({'case_id':case['case_id'],'nct_id':case['nct_id'],'snapshot_id':snapshot_id,'wrapper_sha256':present['sha256'],'imported_at':old['imported_at']})
                existing+=1;continue
            definitions=case['target_definition'] if isinstance(case['target_definition'],list) else [case['target_definition']]
            # These are literal source bindings, not newly inferred ITT/PP,
            # dose, comparison, or statistical-analysis-population assertions.
            target={'endpoint':' ; '.join(x['measure'] for x in definitions),
                    'population':canonical({'registered_eligibility':packet['eligibility'],'original_target_definition':case['target_definition']}),
                    'comparison':canonical({'registered_arms':packet['arms']}),
                    'success_definition':case['target_success_rule']}
            outputs={'forecast_kind':'uncalibrated_llm_research','forecasts':{},'clinical_prediction_validated':False,
                     'archived_statistical_assurance':case.get('statistical_assurance'),
                     'original_target_definition':case['target_definition']}
            generated=[];models={}
            for model,forecast in case['forecasts'].items():
                actual,path,file_sha=model_runs[model]
                matches=[x for x in actual['forecasts'] if x['case_id']==case['case_id']]
                if matches!=[forecast]:raise ValueError('Frozen forecast does not match reviewed provider output')
                completed=actual['completed_at_utc'];generated.append(completed)
                if instant(completed)>instant(archive['frozen_at_utc']):raise ValueError('Legacy lock predates provider completion')
                outputs['forecasts'][model]=dict(forecast)|{'generated_at_utc':completed,'model_version':actual['model_returned'],
                    'source_run_record_sha256':digest(actual),'source_run_file_sha256':file_sha,
                    'system_prompt_sha256':actual.get('system_prompt_sha256'),'request_sha256':actual.get('request_sha256')}
                models[model]={'model_returned':actual['model_returned'],'generated_at_utc':completed,
                               'source_path':path,'source_file_sha256':file_sha,'system_prompt_sha256':actual.get('system_prompt_sha256')}
            probabilities(outputs)
            prediction_timestamp=max(generated,key=instant)
            input_snapshot={'nct_id':case['nct_id'],'trial_name':case['trial_name'],'drug':case['asset'],'sponsor':case['company'],
                            'indication':packet['indication'],'phase':';'.join(packet['design'].get('phases',[])),
                            'trial_design':packet['design'],'randomized':packet['design'].get('designInfo',{}).get('allocation')=='RANDOMIZED',
                            'original_target_definition':case['target_definition'],'eligibility':packet['eligibility'],'arms':packet['arms'],
                            'original_input_packet':packet,'registry_source':packet['registry_source']}
            evidence=[]
            sources=case['sources']+[p['source'] for p in priors if case['nct_id'] in p.get('target_nct_ids',[])]
            seen=set()
            for source in sources:
                source=dict(source)
                if source['path'] not in bound or bound[source['path']]!=source['sha256']:
                    raise ValueError('Legacy evidence source not bound to verified release')
                key=(source.get('url'),source['sha256'])
                if key in seen:continue
                seen.add(key)
                if source.get('source_date'):
                    source['availability_date']=source['source_date'];source['availability_basis']='archived_publication_date'
                elif source.get('retrieved_at'):
                    source['availability_date']=source['retrieved_at'];source['availability_basis']='observed_public_retrieval_not_publication_date'
                if source.get('availability_date') and instant(source['availability_date'])>instant(archive['evidence_as_of_utc']):
                    raise ValueError('Legacy evidence availability exceeds archived cutoff')
                evidence.append(source)
            trial_id='tr_'+digest({'owner':archived_owner,'identity':case['nct_id']})[:24]
            tv_id='tv_archive_'+suffix
            model_record={'model_version':'archived-reviewed-R2','pipeline_version':'continuous-v4/repair-v1',
                          'training_data_cutoff':None,'training_cutoff_status':'not_disclosed_in_original_archive','models':models}
            mv_id='mv_'+digest(model_record)[:24]
            provenance={'provenance':'archived_record_import','case_id':case['case_id'],'original_record_id':archive['id'],
                        'original_record_sha256':archive_sha,'original_record_file_sha256':bound[base+'frozen_forecasts_v1.json'],
                        'original_release_sha256':release['sha256'],'original_source_packet_sha256':archive['source_packet_sha256'],
                        'original_locked_at':archive['frozen_at_utc'],'original_evidence_cutoff':archive['evidence_as_of_utc'],
                        'generation_timestamp_basis':'latest_actual_archived_model_completion; per_model_timestamps_preserved',
                        'target_binding':'literal_original_definition_registered_eligibility_and_arms; no_inferred_analysis_population_or_pair',
                        'source_archive_path':base+'frozen_forecasts_v1.json','independent_timestamp_verified':False,
                        'imported_at_is_not_prediction_or_lock':True}
            record={'schema_version':'predome.run.v1','id':run_id,'prediction_id':run_id,'trial_id':case['nct_id'],
                    'internal_trial_id':trial_id,'trial_version_id':tv_id,'model_version_id':mv_id,'status':'draft',
                    'created_at':prediction_timestamp,'prediction_timestamp':prediction_timestamp,'input_snapshot':input_snapshot,
                    'outputs':outputs,'model_version':'archived-reviewed-R2','pipeline_version':'continuous-v4/repair-v1',
                    'data_cutoff':archive['evidence_as_of_utc'],'evidence':evidence,'target':target,'parent_prediction_id':None,
                    'model_training_data_cutoff':None,'pretraining_contamination_excluded':False,'clinical_prediction_validated':False,
                    'provenance':'archived_record_import','archive_provenance':provenance,'imported_at':imported_at}
            snapshot=record|{'schema_version':'predome.snapshot.v1','id':snapshot_id,'snapshot_id':snapshot_id,'status':'locked',
                             'locked_at':archive['frozen_at_utc'],'published':True,'previous_snapshot_sha256':None,
                             'timestamp_attestation':'original_archive_server_clock_and_sha; import_not_new_lock; not_independent_timestamp',
                             'prospective_eligibility_status':'awaiting_independent_outcome_verification'}
            db.execute('INSERT OR IGNORE INTO platform_trials VALUES(?,?,?,?)',(trial_id,archived_owner,store.pack({'trial_id':case['nct_id'],'provenance':'archived_record_import'}),imported_at))
            db.execute('INSERT INTO platform_trial_versions VALUES(?,?,?,?,?,?)',(tv_id,trial_id,archived_owner,digest(input_snapshot),store.pack(input_snapshot),imported_at))
            if not db.execute('SELECT id FROM platform_model_versions WHERE id=?',(mv_id,)).fetchone():
                db.execute('INSERT INTO platform_model_versions VALUES(?,?,?,?)',(mv_id,digest(model_record),store.pack(model_record),imported_at))
            db.execute('INSERT INTO platform_prediction_runs VALUES(?,?,?,?,?,?,?,?,?)',(run_id,trial_id,tv_id,archived_owner,mv_id,None,digest(record),store.pack(record),prediction_timestamp))
            for i,item in enumerate(evidence):
                db.execute('INSERT INTO platform_prediction_evidence VALUES(?,?,?,?,?)',('pe_archive_'+suffix+'_'+str(i),run_id,archived_owner,digest(item),store.pack(item)))
            wrapper_sha=digest(snapshot)
            db.execute('INSERT INTO platform_prediction_snapshots VALUES(?,?,?,?,?,?,?,?,?)',
                       (snapshot_id,run_id,trial_id,archived_owner,1,wrapper_sha,None,store.pack(snapshot),archive['frozen_at_utc']))
            result.append({'case_id':case['case_id'],'nct_id':case['nct_id'],'snapshot_id':snapshot_id,'wrapper_sha256':wrapper_sha,'imported_at':imported_at});inserted+=1
    return {'status':'imported' if inserted else 'already_imported','inserted':inserted,'existing':existing,
            'source_record_sha256':archive_sha,'source_release_sha256':release['sha256'],'source_files_modified':False,
            'records':result,'timestamp_attestation':'preserved_archive_times_not_new_predictions'}
