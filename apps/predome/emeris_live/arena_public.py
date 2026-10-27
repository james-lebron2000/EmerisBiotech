"""Clinical-only projection. No calibrated probabilities or internal run metadata."""
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from . import arena_api
from .arena_store import ArenaLedger, result_target_binding
from .arena_result_assurance import result_assurance
from .arena_public_contract import credential_like, public_url, validate_current_readout, public_consensus
from .arena_evidence_locations import validate_locations

ROOT = Path(__file__).resolve().parents[1]
router = APIRouter()
VERSION = 'arena-clinical-public-1.4'
POLICY = {
    'calibration_status': 'not_established',
    'probability': None, 'confidence': None,
    'direction_rule': 'Direction only: persisted valid model value >0.5 = Success; <0.5 = Failure; exactly 0.5, abstention, failed/missing output or blocked input = Uncertain. This is not a calibrated clinical success probability.',
    'consensus_rule': 'Each assigned model slot counts once for direction counts and majority; missing or failed outputs count as Uncertain. A class needs more than half of all assigned slots; otherwise consensus is Uncertain. Agreement uses only returned predicted or explicit abstention judgments, including Uncertain, and requires at least two judgments; technical failures and unexecuted slots do not contribute. It is not accuracy or confidence.',
    'comparison_scope': 'Same-evidence clinical-only assignments, separately for each exact registered target. Historical records remain separate.',
    'clinical_prediction_validated': False,
}


def text(value, limit=2000):
    return value[:limit] if isinstance(value, str) and value.strip() else None


def source_view(value, snapshot_urls=None):
    result = {'id': text(value.get('id') or value.get('source_id'), 200),
            'title': text(value.get('title') or value.get('id') or value.get('source_id'), 300),
            'source_url': public_url(value.get('source_url') or value.get('url')),
            'source_snapshot_url': None if value.get('archive_only_source') else (snapshot_urls or {}).get(value.get('source_sha256')),
            'publication_date': text(value.get('public_date') or value.get('availability_date'), 80),
            'kind': text(value.get('kind'), 80),
            'page': value.get('page') if type(value.get('page')) is int else None}
    # These are recorded card metadata, not proof that a model read an original
    # document. Invalid or credential-like locations must not block old records.
    for key in ('page_references', 'source_anchor', 'document_version'):
        if key not in value or isinstance(value[key], str) and credential_like(value[key]):
            continue
        try:
            result.update(validate_locations({key: value[key]}))
        except ValueError:
            continue
    return result


def read_frozen(store):
    path = Path(store.path) / 'arena.sqlite3'
    if path.is_symlink(): raise ValueError('Invalid ledger path')
    if not path.exists(): return {'targets': [], 'runs': [], 'cohorts': []}
    if not path.is_file(): raise ValueError('Invalid ledger path')
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        events = ArenaLedger._verified_events(None, db)
    class FrozenEvents:
        def events(self): return events
    frozen = FrozenEvents()
    frozen.path = path
    return ArenaLedger.snapshot(frozen)



def read_archived(store):
    """Read the existing public clinical archives without schema initialization.

    Existing integrity decoders run against one query-only SQLite snapshot.
    Missing, never-initialized tables mean no recorded public outcomes; partial
    schemas fail closed rather than dropping some established result records.
    A source-only installation has none of the four legacy material roots. It
    can serve the separate Arena ledger without manufacturing a legacy release.
    Existing database integrity/schema checks still run in this case; recorded
    archive imports require their source material rather than silently vanishing.
    """
    from . import product as product_module
    from .platform_store import PlatformStore
    legacy_roots = ('predome_data', 'dashboard_data', 'trial_data', 'public_data')
    has_materials = any((ROOT / name).exists() or (ROOT / name).is_symlink()
                        for name in legacy_roots)
    product = product_module.build_product(None) if has_materials else None
    path = Path(store.path) / 'workbench.sqlite3'
    if path.is_symlink(): raise ValueError('Invalid archive path')
    if not path.exists():
        return combined_archives(store, product) if product is not None else ([], [], {})
    if not path.is_file(): raise ValueError('Invalid archive path')
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        class ReadOnlyStore:
            path = store.path
            unpack = staticmethod(store.unpack)
            @contextmanager
            def connect(self):
                yield db
        view_store = ReadOnlyStore()
        tables = {row['name'] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        required = {'platform_prediction_snapshots', 'platform_actual_outcomes',
                    'platform_outcome_verifications', 'platform_evaluation_results'}
        if tables & required:
            if not required <= tables: raise ValueError('Incomplete public outcome schema')
            ledger = object.__new__(PlatformStore)
            ledger.store = view_store
            snapshots = ledger.list_public_snapshots()
            outcomes = product_module._published_outcomes(ledger, snapshots)
            if product is None:
                if any(item['record'].get('provenance') == 'archived_record_import'
                       or item['record'].get('archive_provenance') is not None
                       for item in snapshots):
                    raise ValueError('Recorded archive imports require legacy source material')
            else:
                product_module._bind_results(product, snapshots, outcomes)
        # Read the existing independently approved calendar overlay, if present.
        overlay_tables = {'readout_update_proposals', 'readout_update_decisions'}
        if tables & overlay_tables:
            if not overlay_tables <= tables: raise ValueError('Incomplete calendar schema')
            from .readout_updates import approved_records
            newest = {}
            for record in approved_records(db, view_store.unpack):
                proposal = record['proposal']
                # A target-scoped approval cannot silently cover every legacy
                # question that happens to share its registry identifier.
                if proposal.get('arena_target_id') or proposal['nct_id'] in newest:
                    continue
                newest[proposal['nct_id']] = proposal
            trials = [] if product is None else product['upcoming'] + product.get('resolved_trials', [])
            for trial in trials:
                update = newest.get(trial['nct_id'])
                if update:
                    trial['window'] = {k: update[k] for k in ('start', 'end', 'precision', 'label')}
                    trial['window']['source_url'] = update['source']['url']
    return combined_archives(store, product) if product is not None else ([], [], {})


def current_readouts(store, frozen):
    """Read exact-target display updates without initializing or changing state."""
    path = Path(store.path) / 'workbench.sqlite3'
    if path.is_symlink(): raise ValueError('Invalid calendar path')
    if not path.exists(): return {}
    if not path.is_file(): raise ValueError('Invalid calendar path')
    from .arena_watch import current_targets
    from .platform_store import digest
    from .readout_updates import approved_records
    targets = {target['id']: target for target in current_targets(frozen)}
    result = {}
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        tables = {row['name'] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not tables & {'readout_update_proposals', 'readout_update_decisions'}:
            return {}
        for record in approved_records(db, store.unpack):
            proposal, review = record['proposal'], record['review']
            ident = proposal.get('arena_target_id')
            target = targets.get(ident)
            if target is None or ident in result:
                continue
            binding = result_target_binding(target)
            if (proposal.get('arena_target_binding') != binding
                    or proposal.get('arena_target_binding_sha256') != digest(binding)
                    or proposal['nct_id'] != target['nct_id']
                    or proposal.get('applicability') != 'exact_registered_target'):
                raise ValueError('Calendar update does not bind the registered target')
            source = proposal['source']
            value = {key: proposal[key] for key in ('label', 'start', 'end', 'precision', 'source_granularity')}
            value.update(source_url=source['url'], source_title='Approved schedule update',
                source_note='Reviewed display schedule; original frozen forecast inputs are unchanged.',
                published_at=source['published_at'], retrieved_at=source['retrieved_at'],
                approved_at=review['created_at'], proposal_id=proposal['id'],
                proposal_sha256=record['proposal_sha256'], decision_id=review['id'],
                decision_sha256=record['decision_sha256'], source_sha256=source['sha256'],
                hash_scope=source['hash_scope'], original_prediction_unchanged=True,
                timestamp_attestation='server_clock_not_independent_timestamp')
            result[ident] = validate_current_readout(value)
    return result



def verified_historical_source(source):
    """Require exact locally captured public bytes; never infer a missing source."""
    if not isinstance(source, dict) or not public_url(source.get('url')): return False
    name, digest = source.get('path'), source.get('sha256')
    if not isinstance(name, str) or not re.fullmatch(r'[a-f0-9]{64}', str(digest)): return False
    path = ROOT / name
    try:
        return (not path.is_symlink() and path.is_file() and path.resolve().is_relative_to(ROOT.resolve())
                and 0 < path.stat().st_size <= 32 * 1024 * 1024
                and hashlib.sha256(path.read_bytes()).hexdigest() == digest)
    except OSError:
        return False


def historical_replays(product):
    """Project only verified, persisted historical LLM cases, never baseline fits.

    build_product(None) already verifies the frozen releases and exact case/model
    join. The additional checks below reject incomplete/conflicting historical
    labels and source bytes instead of turning unknown records into failures.
    """
    targets, runs, evidence = [], [], {}
    note = ('Historical source-curated research label, checked by AI agents; '
            'no independent human adjudication is claimed. Model execution followed '
            'public results. This is a retrospective replay, not a prospective hit.')
    for case in product.get('historical', []):
        detail = case.get('result_details') or {}
        label = case.get('actual_label')
        case_id = case.get('case_id')
        original = case.get('label_source')
        if (case.get('scope') != 'retrospective_replay' or type(label) is not int or label not in (0, 1)
                or not re.fullmatch(r'case-\d{3}', str(case_id))
                or case.get('actual_result') != ('met' if label else 'not_met')
                or not all(detail.get(k) == case.get(k) for k in ('case_id', 'nct_id', 'endpoint'))
                or type(detail.get('original_label')) is not int or detail['original_label'] != label
                or detail.get('original_topline_date') != case.get('result_date')
                or detail.get('original_label_source') != original
                or not verified_historical_source(original)):
            continue
        try:
            timestamp(case.get('result_date'), date_only=True)
        except (ValueError, TypeError):
            continue
        ident = 'history-replay-' + case_id
        def source_record(source, suffix, kind, title):
            if not verified_historical_source(source): return None
            source_id = ident + '-' + suffix
            evidence[source_id] = {'id': source_id, 'source_url': source['url'],
                'source_sha256': source['sha256'], 'public_date': source.get('published_at'),
                'kind': kind, 'title': title, 'archive_only_source': True}
            return source_id
        label_id = source_record(original, 'result', 'historical_public_result', case['trial_name'] + ' original public result')
        numeric_id = source_record(detail.get('source'), 'numeric-result', 'historical_numeric_result', case['trial_name'] + ' numeric result publication')
        design_id = source_record(case.get('feature_source'), 'design', 'historical_design', case['trial_name'] + ' archived design data')
        target = {'id': ident, 'nct_id': case['nct_id'], 'trial_name': case['trial_name'],
            'drug': case.get('asset'), 'sponsor': None, 'indication': detail.get('population'),
            'endpoint': case['endpoint'], 'population': detail.get('population'),
            'comparison': detail.get('comparison'), 'success_definition': None,
            'analysis_timepoint': None, 'scope': 'archived_research', 'record_kind': 'historical_replay',
            'status': 'adjudicated', 'information_cutoff_utc': case.get('features_as_of'),
            'verification_note': note,
            'public_source_ids': [i for i in (label_id, numeric_id, design_id) if i],
            'readout': {'label': case['result_date'], 'start': case['result_date'], 'end': case['result_date'],
                'precision': 'day', 'source_url': original['url'], 'source_title': 'Archived first public result'},
            'result': {'label': 'success' if label else 'failure', 'first_public_at': case['result_date'],
                'source_url': original['url'], 'summary': case.get('review_notes'), 'verification_note': note,
                'numeric_source_url': detail['source']['url'] if numeric_id else None,
                'effect_size': detail.get('effect_size') if numeric_id else None,
                'confidence_interval': detail.get('confidence_interval') if numeric_id else None,
                'p_value': detail.get('p_value') if numeric_id else None}}
        own = []
        for model, forecast in (case.get('forecasts') or {}).items():
            if not isinstance(forecast, dict) or forecast.get('status') not in ('predicted', 'abstain', 'abstained', 'failed'):
                continue
            generated = forecast.get('created_at_utc')
            try: timestamp(generated)
            except (ValueError, TypeError): generated = None
            # A design source can be resolved; absent prior-ID mappings stay
            # missing rather than citing the later outcome as model input.
            cited = [design_id] if design_id and 'design' in forecast.get('evidence_ids', []) else []
            own.append({'id': ident + '-' + model, 'target_id': ident, 'model_id': model,
                'requested_model': model, 'model_version': forecast.get('model_version'),
                'track': 'archived', 'condition': 'clinical_only', 'scope': 'archived_research',
                'status': 'abstained' if forecast['status'] == 'abstain' else forecast['status'],
                'probability': forecast.get('probability'), 'reasoning': forecast.get('reasoning'),
                'generated_at_utc': generated, 'locked_at_utc': None, 'evidence_ids': cited})
        if own:
            targets.append(target); runs.extend(own)
    return targets, runs, evidence


def combined_archives(store, product):
    targets, runs, evidence = arena_api.archived_views(store, product=product)
    history_targets, history_runs, history_evidence = historical_replays(product)
    return targets + history_targets, runs + history_runs, {**evidence, **history_evidence}


def registry_fields(target, sources, verified_assets=None):
    empty = {'phase': None, 'design': None, 'registry_status': None}
    try:
        manifest_path = ROOT / 'arena_data/season_acquisition_manifest.json'
        manifest = json.loads(manifest_path.read_bytes()) if manifest_path.is_file() else {}
        assets = manifest.get('assets', []) if manifest.get('schema_version') == 'arena-public-acquisition-v1' else []
        for source in sources:
            if source.get('kind') != 'trial_registry':
                continue
            imported_raw = (verified_assets or {}).get(source.get('source_sha256'))
            name = source.get('source_path', '')
            if imported_raw is not None:
                raw = imported_raw
                if (not isinstance(raw, bytes) or not 0 < len(raw) <= 10 * 1024 * 1024
                        or hashlib.sha256(raw).hexdigest() != source.get('source_sha256')):
                    continue
            else:
                if not re.fullmatch(r'arena_data/source_snapshots/[A-Za-z0-9][A-Za-z0-9_.-]{0,160}\.json', name):
                    continue
                matches = [a for a in assets if a.get('path') == name and a.get('sha256') == source.get('source_sha256')]
                if len(matches) != 1: continue
                path = ROOT / name
                if path.is_symlink() or not path.resolve().is_relative_to(ROOT.resolve()) or not path.is_file(): continue
                if not 0 < path.stat().st_size <= 10 * 1024 * 1024: continue
                raw = path.read_bytes()
                if len(raw) != matches[0].get('bytes') or hashlib.sha256(raw).hexdigest() != source.get('source_sha256'): continue
            protocol = json.loads(raw).get('protocolSection', {})
            if protocol.get('identificationModule', {}).get('nctId') != target.get('nct_id'): continue
            design = protocol.get('designModule', {})
            info = design.get('designInfo', {})
            enrollment = design.get('enrollmentInfo', {})
            phases = [p for p in design.get('phases', []) if isinstance(p, str)]
            return {'phase': ' / '.join(phases) or None,
                    'design': {'allocation': text(info.get('allocation'), 80),
                        'intervention_model': text(info.get('interventionModel'), 80),
                        'masking': text(info.get('maskingInfo', {}).get('masking'), 80),
                        'primary_purpose': text(info.get('primaryPurpose'), 80),
                        'enrollment': {'count': enrollment.get('count') if type(enrollment.get('count')) is int and enrollment['count'] >= 0 else None,
                                       'type': text(enrollment.get('type'), 40)}} if design else None,
                    'registry_status': text(protocol.get('statusModule', {}).get('overallStatus'), 80)}
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        pass
    return empty


def clinical_value(value):
    if isinstance(value, str): return text(value, 500)
    if type(value) in (int, float) and math.isfinite(value): return value
    if isinstance(value, list) and len(value) <= 3:
        return [clinical_value(v) if not isinstance(v, (list, dict)) else None for v in value]
    if isinstance(value, dict):
        return {k: clinical_value(value[k]) for k in ('measure', 'value', 'estimate', 'unit', 'lower', 'upper', 'level')
                if k in value and not isinstance(value[k], (list, dict))}
    return None


def result_view(target):
    result = target.get('result')
    if not isinstance(result, dict): return None
    raw_label = result.get('label')
    label = raw_label.casefold() if isinstance(raw_label, str) else ('success' if type(raw_label) is int and raw_label == 1 else 'failure' if type(raw_label) is int and raw_label == 0 else None)
    if label == 'not_adjudicable': label = 'inconclusive'
    if label not in ('success', 'failure', 'mixed', 'inconclusive'): label = None
    status = target.get('status')
    assurance = result_assurance(target)
    provenance=result.get('review_provenance')
    note=text(result.get('verification_note'),1000)
    if isinstance(provenance,dict):
        basis=provenance.get('basis')
        scope={'ai_only':'AI source reviews; no human review is recorded.',
            'human_only':'Operator-declared human reviews; identities and qualifications are not independently authenticated.',
            'mixed':'Mixed declared review types; not independent clinical validation.',
            'unspecified_only':'Reviewer types were not specified; human review is unverified.',
            'legacy_unspecified':'Legacy review identities have no recorded AI/human type; human review is unverified.'}.get(basis,
                'Review provenance is incomplete; human review is unverified.')
        if provenance.get('identity_assurance')=='authenticated_account_operator_enrolled':
            scope='Session-authenticated accounts linked to operator-enrolled principals; clinical credentials and scientific independence remain unverified.'
        note=('Conflicting reviews remain unresolved. ' if provenance.get('typed_conflict') else '')+scope
        if status!='adjudicated':
            label=None
            note+=' Awaiting agreement against the exact registered target; no match score is assigned.'
    if status == 'adjudicated' and assurance['status'] == 'agreement_pending_independent_review':
        status = 'agreement_pending_independent_review'
        label = None
        note = (note or '') + ' Source agreement is recorded; independent scientific review has not been accepted. No prediction match or formal score is assigned.'
    if assurance['status'] == 'accepted':
        note = 'Documented independent source review was accepted by the responsible operator. This is not automated verification of professional credentials or clinical predictive validity.'
    return {'status': status if status in ('pending', 'announced', 'adjudicated', 'agreement_pending_independent_review') else 'pending',
            'review_status':assurance['status'], 'scoring_eligible':assurance['scoring_eligible'],
            'label': label, 'first_public_at': text(result.get('first_public_at') or result.get('published_at'), 80),
            'source_url': public_url(result.get('source_url')),
            'summary': text(result.get('summary') or result.get('endpoint_match_note'), 1500),
            'effect_size': clinical_value(result.get('effect_size')),
            'confidence_interval': clinical_value(result.get('confidence_interval')),
            'p_value': clinical_value(result.get('p_value')),
            'numeric_source_url': public_url(result.get('numeric_source_url')),
            'verification_note': note}


def direction(run):
    blocked = any(isinstance(v, dict) and v.get('severity') == 'blocking' for v in run.get('input_evidence_issues', []))
    value = run.get('probability')
    if (blocked or run.get('status') != 'predicted' or type(value) not in (int, float)
            or not math.isfinite(value) or not 0 <= value <= 1 or value == .5):
        return 'Uncertain'
    return 'Success' if value > .5 else 'Failure'


def timestamp(value, *, date_only=False):
    if date_only and isinstance(value, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
    if not isinstance(value, str): raise ValueError('Missing timestamp')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None: raise ValueError('Timezone required')
    return parsed


def match_result(run, target, predicted):
    if target.get('record_kind') == 'historical_replay': return None
    result = result_view(target)
    if (not result or not result['scoring_eligible'] or result['status'] != 'adjudicated' or result['label'] not in ('success', 'failure')
            or predicted == 'Uncertain' or run.get('submission_on_time') is False): return None
    if run.get('scope') != 'archived_research' and run.get('submission_on_time') is not True: return None
    try:
        generated = timestamp(run.get('generated_at_utc'))
        locked = timestamp(run.get('locked_at_utc'))
        first_public = timestamp(result['first_public_at'], date_only=True)
        cutoff = timestamp(target.get('information_cutoff_utc') or target.get('data_cutoff'))
        if not cutoff <= generated <= locked < first_public: return None
        if target.get('submission_deadline_utc') and locked > timestamp(target['submission_deadline_utc']): return None
        proof = run.get('timestamp_proof') or {}
        if proof.get('status') == 'attested' and timestamp(proof.get('generated_at_utc')) >= first_public: return None
    except (ValueError, TypeError): return None
    return predicted.casefold() == result['label']


def brief_reason(run):
    if any(isinstance(v, dict) and v.get('severity') == 'blocking' for v in run.get('input_evidence_issues', [])):
        return '输入证据需要更正，暂不作公开方向判断。'
    if run.get('status') not in ('predicted', 'abstained', 'abstain'):
        return {'withdrawn': '执行前撤回，未产生预测。', 'pending': '等待生成研究判断。'}.get(run.get('status'), '本次未产生可用研究判断。')
    raw = run.get('reasoning') or run.get('reason')
    # Check the original summary before truncating it. Do not echo part of an
    # unsafe response or change the immutable stored result to make it public.
    if credential_like(raw): return None
    value = text(raw, 1600)
    if not value: return None
    # Omit numerical self-assessments as whole clauses; do not convert them
    # into invented calibrated values. Clinical HR/OR/CI and p values in other
    # clauses remain intact. This also handles "slightly below 50%" and values
    # written before the word probability, not just "probability = 0.5".
    parts = re.split(r'([。；;\n]|(?<=[.!?])\s+)', value)
    for index in range(0, len(parts), 2):
        clause = parts[index]
        if (re.search(r'概率|置信度|成功机会|成功機會|probability|likelihood|chance|confidence(?!\s+interval)|p\s*\(\s*success\s*\)', clause, re.I)
                and re.search(r'\d|五五[开開]', clause)):
            parts[index] = '未建立概率校准'
    return ''.join(parts)[:800]



def run_view(run, target, sources, snapshot_urls=None):
    predicted = direction(run)
    match = match_result(run, target, predicted)
    by_id = {s.get('id'): s for s in sources}
    refs = [source_view(by_id[i], snapshot_urls) for i in run.get('evidence_ids', []) if i in by_id]
    if not refs: refs = [source_view(s, snapshot_urls) for s in run.get('supporting_evidence', []) if isinstance(s, dict)]
    fields = {key: text(run.get(key), 300) for key in
              ('id', 'target_id', 'cohort_id', 'model_id', 'requested_model', 'scope', 'track', 'condition',
               'generated_at_utc', 'locked_at_utc')}
    version = run.get('returned_model') or run.get('model_version') or run.get('requested_model')
    reason = brief_reason(run)
    fields.update(model_version=text(version, 200), status=text(run.get('status'), 80),
        predicted_outcome=predicted, probability=None, confidence=None, calibration_status='not_established',
        brief_reason=reason, reason=reason, sources=refs, match=match,
        match_status='historical_replay' if target.get('record_kind') == 'historical_replay' else 'matched' if match is True else 'mismatched' if match is False else 'not_evaluable')
    return fields


def consensus(runs):
    return public_consensus(runs)


def target_view(target, sources, snapshot_urls=None, verified_assets=None):
    result = {key: text(target.get(key), 4000 if key in ('success_definition', 'endpoint', 'population', 'comparison') else 500)
              for key in ('id', 'cohort_id', 'trial_name', 'nct_id', 'drug', 'sponsor', 'indication',
                          'endpoint', 'population', 'comparison', 'success_definition', 'analysis_timepoint',
                          'scope', 'registered_at', 'execution_status', 'information_cutoff_utc', 'record_kind', 'verification_note')}
    result['status'] = target.get('status') if target.get('status') in ('pending', 'announced', 'adjudicated') else 'pending'
    result['registry_url'] = 'https://clinicaltrials.gov/study/' + target['nct_id'] if re.fullmatch(r'NCT\d{8}', str(target.get('nct_id', ''))) else None
    result.update(registry_fields(target, sources, verified_assets))
    window = target.get('readout') if isinstance(target.get('readout'), dict) else {}
    result['readout'] = {key: (public_url(window.get(key)) if key == 'source_url' else text(window.get(key), 1000))
                         for key in ('label', 'start', 'end', 'precision', 'source_url', 'source_title', 'source_granularity', 'source_note')}
    result['result'] = result_view(target)
    if result['result'] is not None:
        result['status'] = result['result']['status']
    result['sources'] = [source_view(s, snapshot_urls) for s in sources]
    return result


def build_public(store):
    frozen = read_frozen(store)
    calendar = current_readouts(store, frozen)
    old_targets, old_runs, old_evidence = read_archived(store)
    raw_runs = [r for r in frozen['runs'] if r.get('track') == 'same_evidence' and r.get('condition') == 'clinical_only'] + [r for r in old_runs if r.get('track') == 'archived' and r.get('condition') == 'clinical_only' and r.get('scope') == 'archived_research']
    target_ids = {r['target_id'] for r in raw_runs}
    targets, runs, verified_assets, snapshot_urls = [], [], {}, {}
    checked_sources = set()
    for target in frozen['targets'] + old_targets:
        if target['id'] not in target_ids: continue
        own = [r for r in raw_runs if r['target_id'] == target['id']]
        sources = {}
        for source_id in target.get('public_source_ids', []):
            if source_id in old_evidence:
                source = old_evidence[source_id]
                sources[(source_id, source.get('source_sha256'))] = source
        for run in own:
            for source in run.get('packet', {}).get('evidence', []):
                sources[(source['id'], source.get('source_sha256'))] = source
            for ident in run.get('evidence_ids', []):
                if run.get('scope') == 'archived_research' and ident in old_evidence:
                    source = old_evidence[ident]
                    sources[(ident, source.get('source_sha256'))] = source
        source_list = list(sources.values())
        for source in source_list:
            digest = source.get('source_sha256')
            if source.get('archive_only_source') or not re.fullmatch(r'[a-f0-9]{64}', str(digest)) or digest in checked_sources:
                continue
            checked_sources.add(digest)
            try:
                _, raw = arena_api._source_asset(digest, data_dir=store.path, events=frozen.get('events', []))
            except HTTPException as exc:
                if exc.status_code != 404:
                    raise
                continue
            verified_assets[digest] = raw
            snapshot_urls[digest] = '/api/arena/sources/' + digest
        public_target = target_view(target, source_list, snapshot_urls, verified_assets)
        if target['id'] in calendar:
            public_target['current_readout'] = calendar[target['id']]
        own_public = [run_view(run, target, run.get('packet', {}).get('evidence', []) or source_list, snapshot_urls) for run in own]
        public_target['consensus'] = consensus(own_public)
        targets.append(public_target); runs.extend(own_public)
    used_cohorts = {t.get('cohort_id') for t in targets}
    cohorts = [{'id': text(c.get('id'), 200), 'label': text(c.get('name') or c.get('label') or c.get('id'), 300),
                'kind': text(c.get('protocol', {}).get('season_kind'), 100),
                'registered_at': text(c.get('registered_at'), 80),
                'execution_status': text(c.get('execution_status'), 80)}
               for c in frozen['cohorts'] if c['id'] in used_cohorts]
    formal = [c for c in cohorts if c['kind'] == 'registered_12_project_season'
              and c['execution_status'] not in ('withdrawn_before_execution', 'withdrawn', 'superseded')]
    current = max(formal, key=lambda c: c.get('registered_at') or '')['id'] if formal else None
    versions = {}
    for run in runs: versions.setdefault(run['model_id'], set()).add(run['model_version'])
    models = [{'id': ident, 'name': ident, 'versions': sorted(v for v in values if v)} for ident, values in sorted(versions.items())]
    return {'version': VERSION, 'models': models, 'targets': targets, 'runs': runs, 'cohorts': cohorts,
            'current_cohort_id': current, 'public_policy': dict(POLICY)}


@router.get('/api/arena/public')
def overview(request: Request):
    try:
        return build_public(request.app.state.store)
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
        raise HTTPException(503, '临床展示记录暂不可用。') from None


@router.get('/api/arena/public/targets/{target_id}')
def target_detail(target_id: str, request: Request):
    return detail_from_view(overview(request), target_id)


def detail_from_view(view, target_id):
    """One sanitized snapshot supplies both its detail DTO and export scope."""
    target = next((t for t in view['targets'] if t['id'] == target_id), None)
    if target is None: raise HTTPException(404, '未找到该临床研究记录。')
    return {'version': VERSION, 'target': target, 'runs': [r for r in view['runs'] if r['target_id'] == target_id],
            'consensus': target['consensus'], 'sources': target['sources'], 'evidence': target['sources'],
            'result': target['result'], 'public_policy': dict(POLICY)}


@router.get('/api/arena/public/targets/{target_id}/record')
def record(target_id: str, request: Request):
    return JSONResponse(target_detail(target_id, request), headers={
        'Content-Disposition': 'attachment; filename=predome-clinical-record.json',
        'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})


@router.get('/api/arena/public/targets/{target_id}/brief/download')
def clinical_brief(target_id: str, request: Request):
    from .arena_public_brief import build_brief
    view = overview(request)
    selected = detail_from_view(view, target_id)
    try:
        cohort = next((c for c in view['cohorts'] if c['id'] == selected['target'].get('cohort_id')), {})
        raw, manifest = build_brief(selected, cohort_kind=cohort.get('kind'))
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
        raise HTTPException(503, '公开临床简报暂不可用；不会以示例结果替代。') from None
    return Response(raw, media_type='application/zip', headers={
        'Content-Disposition': 'attachment; filename=predome-clinical-brief-'+manifest['nct_id']+'.zip',
        'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})
