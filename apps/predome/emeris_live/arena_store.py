"""Append-only public challenge ledger, isolated from existing sealed forecasts.

The operator CLI and session-bound internal source reviews mutate this ledger.
Public HTTP routes are read-only.
Registration precedes execution, and a response cannot replace a prior response.
"""
from contextlib import contextmanager
from datetime import date, datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import uuid
from urllib.parse import urlsplit

VERSION = 'open-forecast-1.0'
TRACKS = ('same_evidence', 'shared_agent', 'open_agent', 'patent_ablation')
CONDITIONS = ('clinical_only', 'with_patents')
TERMINAL = ('predicted', 'abstained', 'failed', 'unavailable', 'timed_out')
HASH = re.compile(r'[a-f0-9]{64}')
RESULT_LABELS = ('success','failure','mixed','inconclusive','not_adjudicable')
REVIEW_KINDS = ('ai','human','unspecified')
AUTHENTICATED_REVIEW_PREFIX = 'account-principal-'
RESULT_BINDING_FIELDS = ('id','nct_id','endpoint','population','comparison','success_definition','analysis_timepoint')


def result_target_binding(target):
    return {key:target[key] for key in RESULT_BINDING_FIELDS}


def result_review_provenance(votes, *, authenticated=None):
    """Account enrollment and scientific independence are different assurances."""
    if authenticated is None:
        authenticated = bool(votes) and all(v.get('reviewer_authentication') for v in votes)
    excluded = sum(bool(v.get('reviewer_authentication')) != authenticated for v in votes)
    votes = [v for v in votes if bool(v.get('reviewer_authentication')) == authenticated]
    counts={kind:sum(v.get('reviewer_kind','unspecified')==kind for v in votes) for kind in REVIEW_KINDS}
    present=[kind for kind,count in counts.items() if count]
    basis=present[0]+'_only' if len(present)==1 else 'mixed' if present else 'unspecified'
    typed={v['result_sha256'] for v in votes if v.get('reviewer_kind','unspecified')!='unspecified'}
    return {'policy_version':'account-principal-review-1' if authenticated else 'explicit-review-kind-1',
        'basis':basis,'review_counts':counts,'excluded_other_assurance_reviews':excluded,
        'typed_conflict':len(typed)>1,'identity_assurance':'authenticated_account_operator_enrolled'
            if authenticated else 'operator_declared_not_independently_authenticated',
        'distinct_authenticated_principals':len({v['reviewer_authentication']['principal_id'] for v in votes}) if authenticated else 0,
        'scientific_credentials_verified':False,
        'independent_human_review_verified':False}


def validate_result_review_input(result,reviewer_id,reviewer_kind='unspecified',review_note=None):
    if (not isinstance(reviewer_id,str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}',reviewer_id)
            or reviewer_kind not in REVIEW_KINDS):raise ValueError('Reviewer identity required')
    if not isinstance(result,dict) or result.get('label') not in RESULT_LABELS:raise ValueError('Explicit outcome label required')
    if not https(result.get('source_url')) or not HASH.fullmatch(str(result.get('source_sha256',''))):
        raise ValueError('Outcome source and original content hash required')
    if instant(result.get('first_public_at'))>datetime.now(timezone.utc):raise ValueError('A future announcement cannot be adjudicated')
    if not result.get('endpoint_match_note'):raise ValueError('Exact target correspondence required')
    if reviewer_kind!='unspecified':
        if not isinstance(review_note,str) or not review_note.strip() or len(review_note)>4000:
            raise ValueError('Typed reviews require an individual source-check note')
        if set(result)-{'label','source_url','source_sha256','first_public_at','endpoint_match_note',
                'target_binding','effect_size','confidence_interval','p_value','summary'}:
            raise ValueError('Typed outcome contains unknown or reserved provenance fields')
    raw=canonical({'result':result,'review_note':review_note})
    if len(raw)>100000 or re.search(r'(?<![A-Za-z])sk-[A-Za-z0-9_-]{24,}|-----BEGIN .*PRIVATE KEY-----',raw):
        raise ValueError('Oversized or credential-like review content rejected')
    return reviewer_id.casefold()
FROZEN_RUN_FIELDS = {'id', 'target_id', 'cohort_id', 'track', 'condition', 'packet', 'scope',
    'registered_at', 'started_at', 'locked_at_utc', 'generated_at_utc', 'timestamp_proof',
    'execution_state', 'provider_config_snapshot', 'provider_implementation_sha256',
    'expected_provider_input_sha256', 'expected_prompt_sha256', 'execution_budget_sha256',
    'agent_spec', 'agent_contract', 'runner_implementation_sha256', 'citation_reviews', 'citation_support_reviews',
    'input_evidence_issues', 'withdrawn_before_execution', 'withdrawn_at', 'record_origin', 'completion_event_epoch'}


def now():
    return datetime.now(timezone.utc).isoformat()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def sha(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def instant(value):
    value = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if value.tzinfo is None:
        raise ValueError('Timezone-aware timestamp required')
    return value.astimezone(timezone.utc)


def https(value):
    u = urlsplit(str(value))
    return u.scheme == 'https' and bool(u.hostname) and not u.username and not u.password


def validate_target(target):
    required = ('id', 'project_id', 'nct_id', 'endpoint', 'population', 'comparison', 'success_definition',
                'analysis_timepoint', 'information_cutoff_utc', 'submission_deadline_utc')
    if any(not isinstance(target.get(k), str) or not target[k].strip() for k in required):
        raise ValueError('Exact target, project, cutoff and deadline required')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', target['id']) or not re.fullmatch(r'NCT\d{8}', target['nct_id']):
        raise ValueError('Invalid target or trial identifier')
    cutoff, deadline = instant(target['information_cutoff_utc']), instant(target['submission_deadline_utc'])
    if cutoff > datetime.now(timezone.utc) or cutoff >= deadline or deadline <= datetime.now(timezone.utc):
        raise ValueError('Cutoff must be in the past and precede deadline')
    screen = target.get('readout_screen', {})
    if screen.get('status') != 'no_decisive_public_result_found' or not screen.get('sources'):
        raise ValueError('Documented current readout screening required before prospective enrollment')
    checked = instant(screen.get('checked_at_utc'))
    if checked < cutoff or checked > datetime.now(timezone.utc) or (datetime.now(timezone.utc) - checked).total_seconds() > 86400:
        raise ValueError('Readout screening must follow cutoff and be refreshed within 24 hours')
    if any(not https(s.get('url')) for s in screen['sources']):
        raise ValueError('Readout screening sources require HTTPS URLs')
    if target.get('result') is not None:
        raise ValueError('Known outcomes cannot enter a prospective cohort')


def validate_packet(packet, target, condition):
    if packet.get('target') != {k: target[k] for k in ('endpoint', 'population', 'comparison', 'success_definition')}:
        raise ValueError('Packet target mismatch')
    if packet.get('data_cutoff') != target['information_cutoff_utc']:
        raise ValueError('Packet cutoff mismatch')
    evidence = packet.get('evidence')
    if not isinstance(evidence, list) or not evidence or len(evidence) > 100:
        raise ValueError('A bounded evidence packet is required')
    ids, patent_count = set(), 0
    for e in evidence:
        if not isinstance(e, dict) or e.get('eligible_for_forecast') is False:
            raise ValueError('Evidence is not eligible for forecasting')
        if e.get('kind') in ('target_result', 'trial_result', 'adjudicated_result') or e.get('is_target_result') is True:
            raise ValueError('Target outcome evidence cannot enter a prospective packet')
        if not isinstance(e.get('id'), str) or not e['id'] or e['id'] in ids:
            raise ValueError('Unique evidence identifiers required')
        ids.add(e['id'])
        if not isinstance(e.get('text'), str) or not e['text'].strip() or len(e['text']) > 30000:
            raise ValueError('Bounded evidence text required')
        if not https(e.get('source_url')):
            raise ValueError('Evidence source URL required')
        if not re.fullmatch(r'[a-f0-9]{64}', str(e.get('source_sha256', ''))):
            raise ValueError('Original source hash required')
        if instant(e.get('available_at')) > instant(packet['data_cutoff']):
            raise ValueError('Evidence is later than cutoff')
        if e.get('kind') == 'patent':
            patent_count += 1
            if (e.get('mapping_status') != 'verified' or e.get('review_status') != 'verified'
                    or type(e.get('page')) is not int or e['page'] < 1 or not e.get('review_receipt')):
                raise ValueError('Patent identity, page and evidence review required')
            if e.get('section_type') not in ('reported_experiment', 'mechanism_statement', 'claim', 'proposed_experiment'):
                raise ValueError('Patent evidence type required')
    if condition == 'clinical_only' and patent_count:
        raise ValueError('Clinical-only arm contains patents')
    if condition == 'with_patents' and not patent_count:
        raise ValueError('Patent arm requires reviewed patent evidence')
    if len(canonical(packet)) > 250000:
        raise ValueError('Evidence packet is too large')


class ArenaLedger:
    def __init__(self, data_dir):
        self.path = Path(data_dir) / 'arena.sqlite3'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS arena_events(
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                id TEXT UNIQUE NOT NULL, kind TEXT NOT NULL, entity_id TEXT NOT NULL,
                created_at TEXT NOT NULL, payload TEXT NOT NULL,
                previous_hash TEXT NOT NULL, event_hash TEXT UNIQUE NOT NULL);
            CREATE TRIGGER IF NOT EXISTS arena_no_update BEFORE UPDATE ON arena_events
                BEGIN SELECT RAISE(ABORT, 'Arena ledger is append only'); END;
            CREATE TRIGGER IF NOT EXISTS arena_no_delete BEFORE DELETE ON arena_events
                BEGIN SELECT RAISE(ABORT, 'Arena ledger is append only'); END;
            CREATE INDEX IF NOT EXISTS arena_entity ON arena_events(entity_id,seq);
            ''')
        self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA journal_mode=WAL')
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _append(self, db, kind, entity_id, payload):
        from .arena_imports import IMPORT_KIND
        last = db.execute('SELECT event_hash FROM arena_events ORDER BY seq DESC LIMIT 1').fetchone()
        ident = uuid.uuid4().hex
        for event in db.execute('SELECT payload FROM arena_events WHERE kind=?', (IMPORT_KIND,)):
            if any(item['id'] == ident for item in json.loads(event['payload'])['source_events']):
                raise ValueError('A new host event ID collides with an imported source event')
        row = dict(id=ident, kind=kind, entity_id=entity_id, created_at=now(),
                   payload=payload, previous_hash=last['event_hash'] if last else '0' * 64)
        row['event_hash'] = sha(row)
        db.execute('INSERT INTO arena_events(id,kind,entity_id,created_at,payload,previous_hash,event_hash) VALUES(?,?,?,?,?,?,?)',
                   (row['id'], kind, entity_id, row['created_at'], canonical(payload), row['previous_hash'], row['event_hash']))
        return row

    def events(self):
        with self.connect() as db:
            return self._verified_events(db)

    def import_sealed_cohort(self, path, expected_sha256):
        """Append one verified source epoch; never import permissions or results."""
        from .arena_imports import IMPORT_KIND, imported_streams, stage_bundle, verify_bundle
        payload = verify_bundle(path, expected_sha256)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            events = self._verified_events(db)
            streams = imported_streams(events)
            prior = next((event for event, value in streams if value['bundle_sha256'] == expected_sha256), None)
            if prior is not None:
                if canonical(prior['payload']) != canonical(payload):
                    raise ValueError('The same import digest cannot identify different source records')
                stage_bundle(self.path.parent, path, payload)
                event, appended = prior, False
            else:
                candidate = {'id': uuid.uuid4().hex, 'kind': IMPORT_KIND,
                    'entity_id': payload['source_cohort_id'], 'payload': payload, 'created_at': now()}
                imported_streams([*events, candidate])
                stage_bundle(self.path.parent, path, payload)
                event = self._append(db, IMPORT_KIND, payload['source_cohort_id'], payload)
                imported_streams([*events, event])
                appended = True
        return {'schema_version': 'predome-sealed-cohort-import-receipt-1',
            'appended': appended, 'bundle_sha256': expected_sha256,
            'source_cohort_id': payload['source_cohort_id'], 'source_ledger_sha256': payload['source_ledger_sha256'],
            'source_event_count': len(payload['source_events']), 'host_import_event_sha256': event['event_hash'],
            'synced_at_utc': event['created_at'], 'original_predictions_preserved': True,
            'provider_calls': 0, 'results_imported': 0, 'human_reviews_imported': 0,
            'clinical_prediction_validated': False}

    def _verified_events(self, db):
        rows = [dict(r) for r in db.execute('SELECT * FROM arena_events ORDER BY seq')]
        previous = '0' * 64
        for row in rows:
            row['payload'] = json.loads(row['payload'])
            check = {k: v for k, v in row.items() if k not in ('seq', 'event_hash')}
            if row['previous_hash'] != previous or sha(check) != row['event_hash']:
                raise ValueError('Challenge ledger integrity check failed')
            previous = row['event_hash']
        return rows

    def register(self, cohort_id, targets, slots, protocol):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', cohort_id):
            raise ValueError('Invalid cohort identifier')
        by_target = {t['id']: t for t in targets}
        if not by_target or len(by_target) != len(targets) or not slots:
            raise ValueError('Unique targets and assigned slots required')
        for target in targets:
            validate_target(target)
        model_slots = set()
        for slot in slots:
            if slot.get('track') not in TRACKS or slot.get('condition') not in CONDITIONS:
                raise ValueError('Unknown comparison track or evidence condition')
            if slot.get('target_id') not in by_target or not slot.get('model_id'):
                raise ValueError('Unbound slot')
            if not isinstance(slot.get('requested_model'), str) or not slot['requested_model'].strip():
                raise ValueError('An explicit requested model version must be frozen')
            key = (slot['target_id'], slot['model_id'], slot['track'], slot['condition'])
            if key in model_slots:
                raise ValueError('Duplicate assigned slot')
            model_slots.add(key)
            validate_packet(slot['packet'], by_target[slot['target_id']], slot['condition'])
        # Controlled model comparisons must share the exact input packet.
        groups = {}
        for slot in slots:
            if slot['track'] in ('same_evidence', 'shared_agent'):
                key = (slot['target_id'], slot['track'], slot['condition'])
                groups.setdefault(key, set()).add(sha(slot['packet']))
        if any(len(hashes) != 1 for hashes in groups.values()):
            raise ValueError('Controlled comparisons require identical input packets')
        workflow_groups = {}
        for slot in slots:
            if slot['track'] in ('same_evidence', 'shared_agent'):
                key = (slot['target_id'], slot['track'], slot['condition'])
                workflow_groups.setdefault(key, set()).add(slot.get('workflow_version', VERSION))
        if any(len(versions) != 1 for versions in workflow_groups.values()):
            raise ValueError('Controlled comparisons require identical workflow versions')
        maximum = protocol.get('maximum_slots', len(slots))
        if type(maximum) is not int or maximum < len(slots):
            raise ValueError('Assigned slots exceed the registered budget maximum')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            events = self._verified_events(db)
            from .arena_imports import imported_streams
            imported = imported_streams(events)
            known = {e['entity_id'] for e in events} | {e['entity_id'] for _, value in imported for e in value['source_events']}
            if cohort_id in known:
                raise ValueError('Cohort already registered')
            for target in targets:
                if target['id'] == cohort_id or target['id'] in known:
                    raise ValueError('Target ID already frozen; use a new cohort-specific ID')
            self._append(db, 'cohort_registered', cohort_id, {'protocol': protocol, 'target_ids': sorted(by_target),
                                                           'slot_count': len(slots), 'clinical_prediction_validated': False})
            for target in targets:
                self._append(db, 'target_registered', target['id'], {**target, 'cohort_id': cohort_id})
            slot_ids = []
            for slot in slots:
                ident = 'run-' + uuid.uuid4().hex
                if ident in known:
                    raise ValueError('Generated run identity collides with an existing record')
                self._append(db, 'slot_registered', ident, {**slot, 'cohort_id': cohort_id,
                    'input_sha256': sha(slot['packet']), 'workflow_version': slot.get('workflow_version', VERSION),
                    'scope': 'prospective_challenge'})
                slot_ids.append(ident)
        return slot_ids

    def start(self, run_id):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            events = self._verified_events(db)
            from .arena_imports import imported_streams
            if any(e['kind'] == 'slot_registered' and e['entity_id'] == run_id
                   for _, value in imported_streams(events) for e in value['source_events']):
                raise ValueError('Imported runs are sealed; execution cannot be repeated')
            rows = db.execute('SELECT kind,payload FROM arena_events WHERE entity_id=? ORDER BY seq', (run_id,)).fetchall()
            if not rows or rows[0]['kind'] != 'slot_registered' or any(r['kind'] in ('run_started', 'run_completed') for r in rows):
                raise ValueError('Run is missing or has already started; never silently repeat a billed request')
            slot = json.loads(rows[0]['payload'])
            if db.execute("SELECT 1 FROM arena_events WHERE kind='cohort_withdrawn' AND entity_id=?",(slot['cohort_id'],)).fetchone():
                raise ValueError('Cohort was withdrawn before execution')
            sources = {(e['id'], e['source_sha256']) for e in slot['packet']['evidence']}
            issues = (json.loads(row['payload']) for row in db.execute(
                "SELECT payload FROM arena_events WHERE kind='evidence_issue'"))
            if any(issue['severity'] == 'blocking' and
                   (issue['evidence_id'], issue['source_sha256']) in sources for issue in issues):
                raise ValueError('A frozen source version has a blocking evidence issue')
            target = db.execute("SELECT payload FROM arena_events WHERE kind='target_registered' AND entity_id=?", (slot['target_id'],)).fetchone()
            target = json.loads(target['payload'])
            if db.execute("SELECT 1 FROM arena_events WHERE entity_id=? AND kind IN ('result_review','result_adjudicated')", (slot['target_id'],)).fetchone():
                raise ValueError('A public result has been recorded; prospective execution is closed')
            validate_target(target)
            if datetime.now(timezone.utc) >= instant(target['submission_deadline_utc']):
                raise ValueError('Submission deadline has passed')
            self._append(db, 'run_started', run_id, {'input_sha256': slot['input_sha256'],
                'execution_budget_sha256': slot.get('execution_budget_sha256')})
        return slot

    def finish(self, run_id, result):
        result = dict(result)
        if FROZEN_RUN_FIELDS.intersection(result):
            raise ValueError('A result cannot overwrite frozen run fields')
        if 'input_sha256' in result:
            result['provider_input_sha256'] = result.pop('input_sha256')
        if result.get('status') == 'abstain':
            result['status'] = 'abstained'
        status, p = result.get('status'), result.get('probability')
        if status not in TERMINAL:
            raise ValueError('Invalid terminal run status')
        if status == 'predicted':
            if type(p) not in (int, float) or not math.isfinite(p) or not 0 <= p <= 1:
                raise ValueError('Finite probability in [0,1] required')
        elif p is not None:
            raise ValueError('A missing prediction must retain a null probability')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            events = self._verified_events(db)
            from .arena_imports import imported_streams
            if any(e['kind'] == 'slot_registered' and e['entity_id'] == run_id
                   for _, value in imported_streams(events) for e in value['source_events']):
                raise ValueError('Imported runs are sealed; completion cannot be replaced')
            rows = db.execute('SELECT kind,payload FROM arena_events WHERE entity_id=? ORDER BY seq', (run_id,)).fetchall()
            kinds = [r['kind'] for r in rows]
            if 'run_started' not in kinds or 'run_completed' in kinds:
                raise ValueError('A started run can be completed exactly once')
            slot = json.loads(rows[0]['payload'])
            if result.get('model_id',slot['model_id']) != slot['model_id'] or result.get('requested_model',slot.get('requested_model')) != slot.get('requested_model'):
                raise ValueError('Provider model identity differs from registered slot')
            if result.get('workflow_version', slot['workflow_version']) != slot['workflow_version']:
                raise ValueError('Provider workflow differs from registered slot')
            expected_config = slot.get('provider_config_snapshot', {}).get('config_sha256')
            if expected_config and result.get('config_sha256') != expected_config:
                raise ValueError('Provider configuration differs from registered slot')
            if result.get('provider', slot.get('provider')) != slot.get('provider'):
                raise ValueError('Provider differs from registered slot')
            complete = status in ('predicted', 'abstained')
            expected_input = slot.get('expected_provider_input_sha256')
            if expected_input and (complete or result.get('provider_input_sha256') is not None) and result.get('provider_input_sha256') != expected_input:
                raise ValueError('Provider input differs from registered slot')
            # A failed review may stop before the forecast request. Its observed
            # prompt sequence is partial, but its billed attempt must survive.
            if complete and slot.get('expected_prompt_sha256') and result.get('prompt_sha256') != slot['expected_prompt_sha256']:
                raise ValueError('Provider prompt differs from registered slot')
            result['contract_complete'] = complete
            evidence_ids = {e['id'] for e in slot['packet']['evidence']}
            cited = result.get('evidence_ids', [])
            if not isinstance(cited, list) or any(e not in evidence_ids for e in cited) or (status == 'predicted' and not cited):
                raise ValueError('Prediction must reference its frozen evidence')
            for field in ('supporting_evidence_ids', 'opposing_evidence_ids'):
                values = result.get(field, [])
                if not isinstance(values, list) or any(e not in cited for e in values):
                    raise ValueError('Support and opposition must cite frozen cited evidence')
            target = json.loads(db.execute("SELECT payload FROM arena_events WHERE kind='target_registered' AND entity_id=?", (slot['target_id'],)).fetchone()['payload'])
            result['submission_on_time'] = datetime.now(timezone.utc) <= instant(target['submission_deadline_utc'])
            result['output_sha256'] = sha(result)
            event = self._append(db, 'run_completed', run_id, result)
        return event

    def record_result(self, target_id, result, *, reviewer_id, supersedes=None,
                      reviewer_kind='unspecified', review_note=None, review_supersedes=None):
        """Legacy operator declarations never impersonate account-bound reviews."""
        if isinstance(reviewer_id, str) and reviewer_id.casefold().startswith(AUTHENTICATED_REVIEW_PREFIX):
            raise ValueError('Authenticated principal identities are reserved for the session-bound review service')
        return self._record_result(target_id, result, reviewer_id=reviewer_id, supersedes=supersedes,
            reviewer_kind=reviewer_kind, review_note=review_note, review_supersedes=review_supersedes)

    def record_authenticated_result(self, target_id, result, *, account_id, enrollment_sha256,
                                    review_note, review_context, supersedes=None, review_supersedes=None):
        """Trusted HTTP service supplies an authenticated session owner, never a body ID.

        Enrollment is rechecked under the same write lock as the review. This is
        server-account assurance, not a person's signature or clinical credentials.
        """
        return self._record_result(target_id, result, reviewer_id=None, reviewer_kind='human',
            review_note=review_note, supersedes=supersedes, review_supersedes=review_supersedes,
            authenticated_account_id=account_id, enrollment_sha256=enrollment_sha256,
            review_context=review_context)

    def _record_result(self, target_id, result, *, reviewer_id, supersedes=None,
                       reviewer_kind='unspecified', review_note=None, review_supersedes=None,
                       authenticated_account_id=None, enrollment_sha256=None, review_context=None):
        """Two distinct reviewer identities must confirm the identical source-bound result."""
        authenticated = authenticated_account_id is not None
        if not authenticated:
            reviewer_id=validate_result_review_input(result,reviewer_id,reviewer_kind,review_note)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            events=self._verified_events(db)
            authentication=None
            if authenticated:
                from .arena_review_auth import resolve_enrollment
                grant=resolve_enrollment(events,authenticated_account_id,target_id,enrollment_sha256)
                reviewer_id=validate_result_review_input(result,AUTHENTICATED_REVIEW_PREFIX+sha(grant['principal_id']),'human',review_note)
                if (not isinstance(review_context,dict) or set(review_context)!={'candidate_id','source_sha256','excerpt','source_publication_date','publication_date_basis','confirmations'}
                        or review_context.get('source_sha256')!=result.get('source_sha256')
                        or not isinstance(review_context.get('candidate_id'),str) or not 1<=len(review_context['candidate_id'])<=160
                        or not isinstance(review_context.get('excerpt'),str) or not 20<=len(review_context['excerpt'])<=6000
                        or not isinstance(review_context.get('source_publication_date'),str)
                        or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}',review_context['source_publication_date'])
                        or review_context.get('publication_date_basis') not in ('document_metadata','reviewer_provided_not_independently_verified','registry_results_post_date')
                        or review_context.get('confirmations')!={k:True for k in ('exact_trial','primary_endpoint','prespecified_analysis','first_public_release','source_inspected')}):
                    raise ValueError('Source-bound individual review confirmations required')
                try:
                    source_day=date.fromisoformat(review_context['source_publication_date'])
                except ValueError:
                    raise ValueError('A valid source publication date is required') from None
                if source_day>datetime.now(timezone.utc).date():
                    raise ValueError('Source publication date cannot be in the future')
                if re.search(r'(?<![A-Za-z])sk-[A-Za-z0-9_-]{24,}|-----BEGIN .*PRIVATE KEY-----',canonical(review_context)):
                    raise ValueError('Credential-like review content rejected')
                authentication={k:grant[k] for k in ('account_id','principal_id','identity_evidence_sha256','identity_assurance')}
                authentication['enrollment_event_sha256']=grant['event_hash']
            from .arena_records import registered_targets
            target = registered_targets(events).get(target_id)
            if target is None:
                raise ValueError('Unknown target')
            expected=result_target_binding(target)
            if ('target_binding' in result or reviewer_kind!='unspecified') and result.get('target_binding')!=expected:
                raise ValueError('Outcome must bind the exact registered trial, population, comparator and analysis')
            previous_events=[e for e in events if e['kind']=='result_adjudicated' and e['entity_id']==target_id]
            round_start=previous_events[-1]['seq'] if previous_events else 0
            votes=[]
            for row in db.execute("SELECT payload,event_hash FROM arena_events WHERE kind='result_review' AND entity_id=? AND seq>? ORDER BY seq",(target_id,round_start)):
                vote=json.loads(row['payload']);vote.setdefault('vote_sha256',row['event_hash']);votes.append(vote)
            active={v['reviewer_id'].casefold():v for v in votes if v.get('supersedes')==supersedes
                and bool(v.get('reviewer_authentication'))==authenticated}
            prior=active.get(reviewer_id)
            if ((prior and review_supersedes!=prior['vote_sha256']) or (not prior and review_supersedes is not None)):
                raise ValueError('Reviewer already voted; a correction must identify the latest own review')
            if (prior and prior.get('reviewer_kind','unspecified')!='unspecified'
                    and reviewer_kind!=prior['reviewer_kind']):
                raise ValueError('A declared reviewer kind cannot change within an outcome version')
            previous = [e['payload'] for e in previous_events]
            if previous and supersedes != previous[-1]['result_sha256']:
                raise ValueError('Correction must identify the previous adjudicated result')
            if not previous and supersedes is not None:
                raise ValueError('No prior adjudication to correct')
            content_hash = sha(result)
            vote = {'reviewer_id': reviewer_id, 'reviewer_kind':reviewer_kind,'review_note':review_note,
                'result': result, 'result_sha256': content_hash, 'supersedes': supersedes,'review_supersedes':review_supersedes}
            if authenticated:
                vote.update(reviewer_authentication=authentication,review_context=review_context)
            vote['vote_sha256']=sha(vote)
            self._append(db, 'result_review', target_id, vote)
            active[reviewer_id]=vote
            typed=reviewer_kind!='unspecified'
            eligible=[v for v in active.values() if (v.get('reviewer_kind','unspecified')!='unspecified')==typed
                and bool(v.get('reviewer_authentication'))==authenticated]
            matching=[v for v in eligible if v['result_sha256']==content_hash]
            conflict=typed and len({v['result_sha256'] for v in eligible})>1
            if len(matching)>=2 and not conflict:
                self._append(db, 'result_adjudicated', target_id, {'result': result, 'result_sha256': content_hash,
                    'reviewer_ids': sorted(v['reviewer_id'] for v in matching), 'supersedes': supersedes,
                    'review_provenance':result_review_provenance(matching)})
                return 'adjudicated'
        return 'announced_pending_adjudication'

    def withdraw_unstarted_cohort(self, cohort_id, reason):
        """Close a preregistration before ANY component call; retain every slot."""
        if not isinstance(reason,str) or not 10<=len(reason.strip())<=3000:
            raise ValueError('A concrete withdrawal reason is required')
        reason = reason.strip()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE');events=self._verified_events(db)
            from .arena_imports import imported_streams
            if any(value['source_cohort_id'] == cohort_id for _, value in imported_streams(events)):
                raise ValueError('An executed imported cohort cannot be withdrawn')
            if not any(e['kind']=='cohort_registered' and e['entity_id']==cohort_id for e in events):
                raise ValueError('Unknown cohort')
            if any(e['kind']=='cohort_withdrawn' and e['entity_id']==cohort_id for e in events):
                raise ValueError('Cohort already withdrawn')
            ids={e['entity_id'] for e in events if e['kind']=='slot_registered' and e['payload']['cohort_id']==cohort_id}
            if any(e['kind'] in ('run_started','run_completed') and e['entity_id'] in ids for e in events):
                raise ValueError('An executed cohort cannot be withdrawn')
            return self._append(db,'cohort_withdrawn',cohort_id,{'reason':reason,'assigned_slots':len(ids),
                'model_calls':0,'clinical_prediction_validated':False})

    def record_evidence_issue(self, evidence_id, source_sha256, *, note, reference_url, severity='blocking'):
        """Record discovery without replacing source bytes or previous forecasts."""
        if (not isinstance(evidence_id,str) or not evidence_id or not HASH.fullmatch(str(source_sha256))
                or severity not in ('blocking','warning') or not https(reference_url)
                or not isinstance(note,str) or not 10<=len(note.strip())<=3000):
            raise ValueError('Source-bound issue, severity and public discovery reference required')
        note = note.strip()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE');events=self._verified_events(db)
            from .arena_imports import imported_streams
            records = [e for _, value in imported_streams(events) for e in value['source_events']] + events
            if not any(e['kind']=='slot_registered' and any(v['id']==evidence_id and v['source_sha256']==source_sha256
                for v in e['payload']['packet']['evidence']) for e in records):
                raise ValueError('Unknown frozen source version')
            if any(e['kind']=='evidence_issue' and e['payload']['evidence_id']==evidence_id
                and e['payload']['source_sha256']==source_sha256 and e['payload']['note']==note
                and e['payload']['severity']==severity for e in events):
                raise ValueError('This evidence issue is already recorded')
            return self._append(db,'evidence_issue','source-'+sha([evidence_id,source_sha256])[:40],
                {'evidence_id':evidence_id,'source_sha256':source_sha256,'note':note,
                 'reference_url':reference_url,'severity':severity,'clinical_prediction_validated':False})

    def record_citation_review(self, run_id, assessment, *, reviewer_id, reviewer_kind, supersedes=None):
        """Append source-bound assessments; a declared AI role is never a human review."""
        from .arena_citations import validate_review
        if (not isinstance(reviewer_id,str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}',reviewer_id)
                or reviewer_kind not in ('ai','human')):
            raise ValueError('Explicit reviewer identity and AI/human kind required')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            events=self._verified_events(db)
            from .arena_imports import imported_streams
            records = [e for _, value in imported_streams(events) for e in value['source_events']] + events
            assigned=next((e for e in records if e['kind']=='slot_registered' and e['entity_id']==run_id),None)
            completed=next((e for e in records if e['kind']=='run_completed' and e['entity_id']==run_id),None)
            if not assigned or not completed:
                raise ValueError('A completed registered run is required')
            run={**assigned['payload'],**completed['payload']}
            clean=validate_review(run,assessment)
            prior=[e['payload'] for e in records if e['kind']=='citation_review' and e['entity_id']==run_id
                   and e['payload']['reviewer_id']==reviewer_id and e['payload']['reviewer_kind']==reviewer_kind]
            if (prior and supersedes!=prior[-1]['review_sha256']) or (not prior and supersedes is not None):
                raise ValueError('Correction must identify the latest review by this reviewer')
            payload={'assessment':clean,'reviewer_id':reviewer_id,'reviewer_kind':reviewer_kind,
                     'supersedes':supersedes}
            payload['review_sha256']=sha(payload)
            if prior and clean==prior[-1]['assessment']:
                raise ValueError('Identical review must not be counted twice')
            return self._append(db,'citation_review',run_id,payload)

    def attest(self, receipt):
        """Append an operator-verified RFC3161 receipt; no public mutation route."""
        receipt = dict(receipt)
        if receipt.get('purpose') not in ('registration', 'predictions'):
            raise ValueError('Explicit attestation purpose required')
        if (receipt.get('signature_verified') is not True or receipt.get('verification_method') != 'openssl_ts_verify'
                or not isinstance(receipt.get('tsa'), str) or not https(receipt.get('public_url'))):
            raise ValueError('Verified operator timestamp receipt required')
        for field in ('ledger_sha256', 'timestamp_response_sha256', 'manifest_sha256'):
            if not HASH.fullmatch(str(receipt.get(field, ''))):
                raise ValueError('Timestamp receipt hashes required')
        for field in ('authority_cert_sha256', 'ca_cert_sha256'):
            if receipt.get(field) is not None and not HASH.fullmatch(str(receipt[field])):
                raise ValueError('Invalid timestamp authority certificate hash')
        generated, verified = instant(receipt.get('generated_at_utc')), instant(receipt.get('verified_at_utc'))
        if generated > verified or verified > datetime.now(timezone.utc):
            raise ValueError('Timestamp generation and verification cannot be in the future')
        count = receipt.get('covered_event_count')
        if type(count) is not int or count < 1:
            raise ValueError('Timestamp covered event count required')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            events = self._verified_events(db)
            if count > len(events) or events[count - 1]['event_hash'] != receipt['ledger_sha256']:
                raise ValueError('Timestamp receipt does not bind the claimed ledger prefix')
            if instant(events[count - 1]['created_at']) > generated:
                raise ValueError('Timestamp predates its covered ledger prefix')
            if receipt['purpose'] == 'predictions' and not any(e['kind'] == 'run_completed' for e in events[:count]):
                raise ValueError('Prediction attestation must cover completed predictions')
            if any(e['kind'] == 'timestamp_attested' and e['payload'].get('timestamp_response_sha256') == receipt['timestamp_response_sha256'] for e in events):
                raise ValueError('Timestamp receipt already appended')
            return self._append(db, 'timestamp_attested', 'attestation-' + uuid.uuid4().hex, receipt)

    def snapshot(self):
        return project_events(self.events(), getattr(self, 'path', None))


def project_events(events, ledger_path=None):
    """Fold source epochs independently, then apply later host facts in order."""
    from .arena_imports import imported_streams, verify_stored_bundle
    imports = {event['id']: payload for event, payload in imported_streams(events)}
    targets, runs, cohorts = {}, {}, []
    result_reviews={}
    for event in events:
        kind, ident, p = event['kind'], event['entity_id'], event['payload']
        if event['id'] in imports:
            payload = imports[event['id']]
            if ledger_path is not None:
                verify_stored_bundle(Path(ledger_path).parent, payload)
            source = project_events(payload['source_events'])
            origin = {'kind': 'sealed_cohort_import', 'bundle_sha256': payload['bundle_sha256'],
                'source_ledger_sha256': payload['source_ledger_sha256'],
                'source_event_count': len(payload['source_events']),
                'synced_at_utc': event['created_at'], 'host_event_sha256': event['event_hash'],
                'registration_proof': payload['proofs']['registration'],
                'prediction_proof': payload['proofs']['predictions']}
            for cohort in source['cohorts']:
                cohorts.append({**cohort, 'record_origin': origin})
            for target in source['targets']:
                targets[target['id']] = {**target, 'record_origin': origin}
            for run in source['runs']:
                run['record_origin'] = origin
                run['completion_event_epoch'] = payload['source_ledger_sha256']
                if run['timestamp_proof'].get('status') == 'attested':
                    run['timestamp_proof'] = {**run['timestamp_proof'], 'url': None,
                        'verification_method': 'openssl_ts_verify_data_and_query_nonce',
                        'proof_scope': 'original_source_event_prefix',
                        'originals_access': 'authorized_internal_only',
                        'source_ledger_sha256': payload['source_ledger_sha256']}
                runs[run['id']] = run
        elif kind == 'cohort_registered':
            cohorts.append({'id': ident, **p, 'registered_at': event['created_at'], 'event_hash': event['event_hash']})
        elif kind == 'target_registered':
            targets[ident] = {**p, 'status': 'pending', 'registered_at': event['created_at']}
        elif kind == 'slot_registered':
            runs[ident] = {**p, 'id': ident, 'status': 'pending', 'probability': None,
                          'registered_at': event['created_at'], 'timestamp_proof': {'status': 'not_attested', 'url': None}}
        elif kind == 'run_started':
            runs[ident]['started_at'] = event['created_at']
            runs[ident]['execution_state'] = 'running'
        elif kind == 'run_completed':
            runs[ident].update(p, generated_at_utc=event['created_at'], locked_at_utc=event['created_at'], execution_state='terminal')
            runs[ident]['completion_event_seq'] = event['seq']
        elif kind == 'citation_review':
            runs[ident].setdefault('citation_reviews',[]).append({**p,'reviewed_at':event['created_at']})
        elif kind == 'result_review':
            key=(ident,p.get('supersedes'))
            result_reviews.setdefault(key,{})[(p['reviewer_id'].casefold(),bool(p.get('reviewer_authentication')))]=p
            provenance=result_review_provenance(list(result_reviews[key].values()),authenticated=bool(p.get('reviewer_authentication')))
            if targets[ident]['status']!='adjudicated' or p.get('supersedes'):
                if targets[ident]['status']=='adjudicated':targets[ident]['previous_adjudicated_result']=targets[ident].get('result')
                targets[ident].update(status='announced',result={**p['result'],
                    'verification_status':'conflicting_reviews' if provenance['typed_conflict'] else 'pending_second_review',
                    'review_provenance':provenance})
        elif kind == 'result_adjudicated':
            # Equal result bytes can recur after corrections. A new round
            # cannot revive reviews from an earlier adjudication event.
            result_reviews={key:value for key,value in result_reviews.items() if key[0]!=ident}
            targets[ident]['result_adjudication_event_sha256']=event['event_hash']
            targets[ident].update(status='adjudicated', result={**p['result'], 'verification_status': 'adjudicated',
                'result_sha256': p['result_sha256'], 'verified_at': event['created_at'],
                'review_provenance':p.get('review_provenance',{'basis':'legacy_unspecified',
                    'review_counts':{'ai':0,'human':0,'unspecified':len(p.get('reviewer_ids',[]))},
                    'identity_assurance':'not_recorded','independent_human_review_verified':False})})
    for event in events:
        if event['kind']=='cohort_withdrawn':
            for cohort in cohorts:
                if cohort['id']==event['entity_id']:
                    cohort.update(execution_status='withdrawn_before_execution',withdrawal=event['payload'])
            for target in targets.values():
                if target['cohort_id']==event['entity_id']:
                    target.update(execution_status='withdrawn_before_execution',withdrawal=event['payload'])
            for run in runs.values():
                if run['cohort_id']==event['entity_id']:
                    run.update(status='withdrawn',probability=None,withdrawn_before_execution=True,
                        execution_state='withdrawn',reason=event['payload']['reason'],withdrawn_at=event['created_at'])
        if event['kind']=='evidence_issue':
            issue=event['payload']
            for run in runs.values():
                if any(e['id']==issue['evidence_id'] and e['source_sha256']==issue['source_sha256'] for e in run['packet']['evidence']):
                    run.setdefault('input_evidence_issues',[]).append({**issue,'recorded_at':event['created_at'],'event_hash':event['event_hash']})
    for event in events:
        receipt = event['payload']
        if event['kind'] != 'timestamp_attested' or receipt.get('purpose') != 'predictions':
            continue
        for run in runs.values():
            if run.get('record_origin'):
                continue
            if run.get('completion_event_seq', float('inf')) <= receipt['covered_event_count']:
                prior = run['timestamp_proof'].get('generated_at_utc')
                if prior and instant(prior) <= instant(receipt['generated_at_utc']):
                    continue
                run['timestamp_proof'] = {'status': 'attested', 'url': receipt['public_url'],
                    'generated_at_utc': receipt['generated_at_utc'], 'ledger_sha256': receipt['ledger_sha256'],
                    'covered_event_count': receipt['covered_event_count'], 'tsa': receipt['tsa'],
                    'signature_verified': True, 'verification_method': receipt['verification_method'],
                    'manifest_sha256': receipt['manifest_sha256'],
                    'trust_note': 'Depends on the third-party TSA certificate authority; not legal notarization.'}
    from .arena_result_assurance import project_result_assurance
    project_result_assurance(events,targets,Path(ledger_path).parent if ledger_path is not None else None)
    from .arena_citations import summaries
    for run in runs.values():
        run['citation_support_reviews']=summaries(run.get('citation_reviews',[]))
    return {'targets': list(targets.values()), 'runs': list(runs.values()), 'cohorts': cohorts,
            'events': events, 'ledger_sha256': events[-1]['event_hash'] if events else None}
