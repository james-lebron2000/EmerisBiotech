"""Append-only public challenge ledger, isolated from existing sealed forecasts.

Only the operator CLI mutates this ledger. Public HTTP routes are read-only.
Registration precedes execution, and a response cannot replace a prior response.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
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
FROZEN_RUN_FIELDS = {'id', 'target_id', 'cohort_id', 'track', 'condition', 'packet', 'scope',
    'registered_at', 'started_at', 'locked_at_utc', 'generated_at_utc', 'timestamp_proof',
    'execution_state', 'provider_config_snapshot', 'provider_implementation_sha256',
    'expected_provider_input_sha256', 'expected_prompt_sha256', 'execution_budget_sha256'}


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
        last = db.execute('SELECT event_hash FROM arena_events ORDER BY seq DESC LIMIT 1').fetchone()
        row = dict(id=uuid.uuid4().hex, kind=kind, entity_id=entity_id, created_at=now(),
                   payload=payload, previous_hash=last['event_hash'] if last else '0' * 64)
        row['event_hash'] = sha(row)
        db.execute('INSERT INTO arena_events(id,kind,entity_id,created_at,payload,previous_hash,event_hash) VALUES(?,?,?,?,?,?,?)',
                   (row['id'], kind, entity_id, row['created_at'], canonical(payload), row['previous_hash'], row['event_hash']))
        return row

    def events(self):
        with self.connect() as db:
            return self._verified_events(db)

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
            self._verified_events(db)
            if db.execute('SELECT 1 FROM arena_events WHERE entity_id=?', (cohort_id,)).fetchone():
                raise ValueError('Cohort already registered')
            for target in targets:
                if target['id'] == cohort_id or db.execute('SELECT 1 FROM arena_events WHERE entity_id=?', (target['id'],)).fetchone():
                    raise ValueError('Target ID already frozen; use a new cohort-specific ID')
            self._append(db, 'cohort_registered', cohort_id, {'protocol': protocol, 'target_ids': sorted(by_target),
                                                           'slot_count': len(slots), 'clinical_prediction_validated': False})
            for target in targets:
                self._append(db, 'target_registered', target['id'], {**target, 'cohort_id': cohort_id})
            slot_ids = []
            for slot in slots:
                ident = 'run-' + uuid.uuid4().hex
                self._append(db, 'slot_registered', ident, {**slot, 'cohort_id': cohort_id,
                    'input_sha256': sha(slot['packet']), 'workflow_version': slot.get('workflow_version', VERSION),
                    'scope': 'prospective_challenge'})
                slot_ids.append(ident)
        return slot_ids

    def start(self, run_id):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self._verified_events(db)
            rows = db.execute('SELECT kind,payload FROM arena_events WHERE entity_id=? ORDER BY seq', (run_id,)).fetchall()
            if not rows or rows[0]['kind'] != 'slot_registered' or any(r['kind'] in ('run_started', 'run_completed') for r in rows):
                raise ValueError('Run is missing or has already started; never silently repeat a billed request')
            slot = json.loads(rows[0]['payload'])
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
            self._verified_events(db)
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

    def record_result(self, target_id, result, *, reviewer_id, supersedes=None):
        """Two distinct reviewer identities must confirm the identical source-bound result."""
        if not reviewer_id or not isinstance(reviewer_id, str):
            raise ValueError('Reviewer identity required')
        if result.get('label') not in ('success', 'failure', 'not_adjudicable'):
            raise ValueError('Explicit outcome label required')
        if not https(result.get('source_url')) or not re.fullmatch(r'[a-f0-9]{64}', str(result.get('source_sha256', ''))):
            raise ValueError('Outcome source and original content hash required')
        if instant(result.get('first_public_at')) > datetime.now(timezone.utc):
            raise ValueError('A future announcement cannot be adjudicated')
        if not result.get('endpoint_match_note'):
            raise ValueError('Exact target correspondence required')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self._verified_events(db)
            if not db.execute("SELECT 1 FROM arena_events WHERE kind='target_registered' AND entity_id=?", (target_id,)).fetchone():
                raise ValueError('Unknown target')
            votes = [json.loads(r['payload']) for r in db.execute("SELECT payload FROM arena_events WHERE kind='result_review' AND entity_id=?", (target_id,))]
            if any(v['reviewer_id'] == reviewer_id and v.get('supersedes') == supersedes for v in votes):
                raise ValueError('Reviewer already voted for this outcome version')
            previous = [json.loads(r['payload']) for r in db.execute("SELECT payload FROM arena_events WHERE kind='result_adjudicated' AND entity_id=? ORDER BY seq", (target_id,))]
            if previous and supersedes != previous[-1]['result_sha256']:
                raise ValueError('Correction must identify the previous adjudicated result')
            if not previous and supersedes is not None:
                raise ValueError('No prior adjudication to correct')
            content_hash = sha(result)
            vote = {'reviewer_id': reviewer_id, 'result': result, 'result_sha256': content_hash, 'supersedes': supersedes}
            self._append(db, 'result_review', target_id, vote)
            matching = [v for v in votes if v['result_sha256'] == content_hash and v.get('supersedes') == supersedes]
            if matching:
                self._append(db, 'result_adjudicated', target_id, {'result': result, 'result_sha256': content_hash,
                    'reviewer_ids': sorted({reviewer_id, matching[0]['reviewer_id']}), 'supersedes': supersedes})
                return 'adjudicated'
        return 'announced_pending_adjudication'

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
        events = self.events()
        targets, runs, cohorts = {}, {}, []
        for event in events:
            kind, ident, p = event['kind'], event['entity_id'], event['payload']
            if kind == 'cohort_registered':
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
            elif kind == 'result_review':
                if targets[ident]['status'] != 'adjudicated':
                    targets[ident].update(status='announced', result={**p['result'], 'verification_status': 'pending_second_review'})
            elif kind == 'result_adjudicated':
                targets[ident].update(status='adjudicated', result={**p['result'], 'verification_status': 'adjudicated',
                    'result_sha256': p['result_sha256'], 'verified_at': event['created_at']})
        for event in events:
            receipt = event['payload']
            if event['kind'] != 'timestamp_attested' or receipt.get('purpose') != 'predictions':
                continue
            for run in runs.values():
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
        return {'targets': list(targets.values()), 'runs': list(runs.values()), 'cohorts': cohorts,
                'events': events, 'ledger_sha256': events[-1]['event_hash'] if events else None}
