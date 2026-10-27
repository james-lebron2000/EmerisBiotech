"""Prepare a new prospective patent comparison; never register, call or rewrite.

The input is an explicitly prepared, single-target clinical-only specification.
Current target screening and every existing runtime evidence gate still apply.
Run ``python scripts/prepare_patent_pair.py --help`` for the offline interface.
"""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from emeris_live import arena_providers, patent_evidence
from emeris_live.arena_store import canonical, instant, sha, validate_packet, validate_target

VERSION = 'predome-prospective-patent-pair-1'
WORKFLOW = arena_providers.VERSION + ':single_pass'
MAX_INPUT_BYTES = 2 * 1024 * 1024
SLOT_FIELDS = {'target_id', 'model_id', 'track', 'condition', 'packet',
               'requested_model', 'workflow_version'}
CARD_FIELDS = {'evidence_id', 'publication_number', 'public_available_at', 'title',
               'pdf_page_1_based', 'source_sha256', 'example_id', 'section_type',
               'experiment_system', 'species_or_cell', 'dose', 'comparator',
               'endpoint', 'result', 'unit', 'missing_fields', 'source_location',
               'interpretation_limit', 'review'}


def read_spec(path):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError('Duplicate clinical specification key')
            value[key] = item
        return value
    with Path(path).open('rb') as stream:
        raw = stream.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        raise ValueError('Clinical specification exceeds the bounded file size')
    return json.loads(raw, object_pairs_hook=unique,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite clinical specification'))), hashlib.sha256(raw).hexdigest()


def _budget(value):
    if not isinstance(value, dict):
        raise ValueError('Explicit execution budget required')
    if set(value) - {'max_output_tokens', 'timeout_seconds', 'max_requests', 'max_cost_usd',
            'max_cost_amount', 'cost_currency', 'budget_policy', 'max_input_chars',
            'stage_budgets', 'max_total_output_tokens', 'max_total_timeout_seconds'}:
        raise ValueError('Unknown execution budget field')
    for key, low, high in (('max_requests', 1, 2), ('max_output_tokens', 128, 16384),
                          ('max_input_chars', 256, arena_providers.MAX_PACKET_BYTES),
                          ('timeout_seconds', 5, 300)):
        if type(value.get(key)) is not int or not low <= value[key] <= high:
            raise ValueError('Explicit bounded execution budget required: ' + key)
    if value.get('budget_policy', 'configured_rates') not in ('configured_rates', 'token_only_unpriced'):
        raise ValueError('Unsupported execution budget policy')
    stage_budget = value.get('stage_budgets')
    if stage_budget is not None and (not isinstance(stage_budget, dict) or set(stage_budget) != {'forecast'}):
        raise ValueError('Patent pairs require the same single-pass forecast budget')
    if value.get('max_cost_usd') is not None and value.get('max_cost_amount') is not None:
        raise ValueError('A monetary budget requires one explicit currency')
    amount = value.get('max_cost_usd') if value.get('max_cost_usd') is not None else value.get('max_cost_amount')
    if amount is not None and (not arena_providers._number(amount, minimum=0.000001, maximum=100)
            or (value.get('max_cost_usd') is None and value.get('cost_currency') not in ('USD', 'CNY'))):
        raise ValueError('Invalid monetary budget')
    # A conservative row-independent check of the actual production stage planner.
    arena_providers._stage_plan({'max_output_tokens': value['max_output_tokens'],
        'timeout_seconds': value['timeout_seconds']}, 'single_pass', value)


def _card_evidence(item, card, cutoff):
    if not isinstance(card, dict) or CARD_FIELDS - set(card):
        raise ValueError('Selected patent experiment card is incomplete')
    if card.get('eligible_for_forecast') is not True or item.get('eligible_for_forecast') is not True:
        raise ValueError('Selected patent experiment card is not eligible')
    if card['publication_number'] != item['publication_number'] or card['source_sha256'] != item['raw_sha256']:
        raise ValueError('Selected patent card differs from its original source')
    review = card['review']
    receipt = item.get('research_review', {})
    # Current operational publication contract requires a source-bound receipt.
    # A catalogue reviewer name alone is not fabricated into an authenticated review.
    if (not isinstance(review, dict) or review.get('status') != 'two_agent_visual_source_check'
            or review.get('receipt_sha256') != receipt.get('receipt_sha256')
            or not re.fullmatch(r'[a-f0-9]{64}', str(review.get('receipt_sha256', '')))
            or patent_evidence.research_review_errors(item, cutoff)):
        raise ValueError('Selected patent card requires its qualifying source-bound AI review')
    mapping = item['drug_mapping']
    dates = [item.get('public_available_at') or item['publication_date'],
             card['public_available_at'], mapping['reviewed_at'], receipt['reviewed_at'], review['reviewed_at']]
    dates.extend(source['public_available_at'] for source in
                 mapping.get('sources', []) + mapping.get('document_evidence', []))
    available = max(patent_evidence._instant(date) for date in dates)
    if available > instant(cutoff):
        raise ValueError('Patent source or review is later than the common cutoff')
    disclosure = {key: receipt[key] for key in ('review_type', 'reviewed_at', 'human_review_count',
        'clinical_prediction_validated', 'same_base_model_correlation_disclosed', 'reviewers')}
    text = canonical({'scientific_experiment_card': card, 'drug_mapping': mapping,
        'source_review_disclosure': disclosure,
        'research_use_only': True, 'clinical_prediction_validated': False})
    return {'id': card['evidence_id'], 'kind': 'patent', 'evidence_kind': 'patent',
        'title': card['title'], 'publication_number': item['publication_number'],
        'publication_date': item['publication_date'], 'source_url': card.get('pdf_url') or item.get('pdf_url'),
        'source_sha256': item['raw_sha256'], 'page': card['pdf_page_1_based'],
        'pdf_page_1_based': card['pdf_page_1_based'], 'example_id': card['example_id'],
        'section_type': 'reported_experiment', 'available_at': available.isoformat(),
        'mapping_status': 'verified', 'review_status': 'verified',
        'review_basis': 'two_agent_visual_source_check', 'human_review_count': 0,
        'review_receipt': receipt['receipt_sha256'], 'research_use_only': True,
        'clinical_prediction_validated': False, 'same_base_model_correlation_disclosed': True,
        'eligible_for_forecast': True, 'text': text}


def validate_pairs(spec):
    """Reject incomplete or changed arms before any registration or paid call."""
    if len(spec.get('targets', [])) != 1 or not spec.get('slots'):
        raise ValueError('Exactly one prospective clinical target and assigned models required')
    target = spec['targets'][0]
    validate_target(target)
    if any(value.get('scope', 'prospective_challenge') != 'prospective_challenge'
           for value in (spec, spec['protocol'], target)):
        raise ValueError('Research-only scope cannot enter this prospective preparer')
    budget = spec['protocol']['execution_budget']
    _budget(budget)
    by_model = {}
    for slot in spec['slots']:
        if slot.get('target_id') != target['id'] or slot.get('workflow_version') != WORKFLOW:
            raise ValueError('Paired target and single-pass workflow must be identical')
        if not isinstance(slot.get('model_id'), str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', slot['model_id']):
            raise ValueError('Explicit assigned model identity required')
        kind = (slot.get('track'), slot.get('condition'))
        if kind not in {('same_evidence', 'clinical_only'), ('patent_ablation', 'with_patents')}:
            raise ValueError('Only the clinical control and patent intervention arms are allowed')
        validate_packet(slot['packet'], target, slot['condition'])
        projected = arena_providers._packet(slot['packet'])
        if len(canonical(projected)) > budget['max_input_chars']:
            raise ValueError('Full scientific evidence exceeds the frozen input budget; no truncation is allowed')
        arms = by_model.setdefault(slot['model_id'], {})
        if slot['condition'] in arms:
            raise ValueError('Exactly one control and patent arm per model required')
        arms[slot['condition']] = slot
    clinical_hashes = set()
    for arms in by_model.values():
        if set(arms) != {'clinical_only', 'with_patents'}:
            raise ValueError('Missing arm in the patent pair')
        control, treated = arms['clinical_only'], arms['with_patents']
        if {k: v for k, v in control.items() if k not in {'track', 'condition', 'packet'}} != {
                k: v for k, v in treated.items() if k not in {'track', 'condition', 'packet'}}:
            raise ValueError('Paired model configuration differs')
        stripped = {**treated['packet'], 'evidence': [e for e in treated['packet']['evidence'] if e['kind'] != 'patent']}
        if control['packet'] != stripped:
            raise ValueError('Paired clinical evidence differs')
        clinical_hashes.add(sha(control['packet']))
    if len(clinical_hashes) != 1:
        raise ValueError('All compared models must share the same clinical evidence')


def prepare(clinical_spec, *, drug, patent_root, cohort_id):
    """Return a proposal and its frozen patent pack without creating runtime state."""
    original = deepcopy(clinical_spec)
    if (not isinstance(original, dict) or len(original.get('targets', [])) != 1
            or not isinstance(original.get('slots'), list) or not 1 <= len(original['slots']) <= 20
            or not isinstance(original.get('protocol'), dict)):
        raise ValueError('A prepared single-target clinical-only specification is required')
    if (not isinstance(cohort_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,93}', cohort_id)
            or cohort_id == original.get('cohort_id')):
        raise ValueError('A new bounded cohort identifier is required')
    if not isinstance(drug, str) or not drug.strip() or len(drug) > 200:
        raise ValueError('An explicit drug name is required')
    target = deepcopy(original['targets'][0])
    validate_target(target)
    for value in (original, original['protocol'], target):
        if value.get('scope', 'prospective_challenge') != 'prospective_challenge':
            raise ValueError('This preparer only supports eligible prospective challenges')
    if re.sub(r'[^a-z0-9]', '', str(target.get('drug', '')).lower()) != re.sub(r'[^a-z0-9]', '', drug.lower()):
        raise ValueError('The explicit drug must match the prepared clinical target drug')
    budget = deepcopy(original['protocol'].get('execution_budget'))
    _budget(budget)
    base, models = None, set()
    for slot in original['slots']:
        if (not isinstance(slot, dict) or set(slot) - SLOT_FIELDS
                or slot.get('track') != 'same_evidence' or slot.get('condition') != 'clinical_only'
                or slot.get('target_id') != target['id']):
            raise ValueError('Input slots must be unfrozen clinical-only same-evidence assignments')
        model = slot.get('model_id')
        if not isinstance(model, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', model) or model in models:
            raise ValueError('Unique explicitly assigned models required')
        if slot.get('workflow_version', WORKFLOW) != WORKFLOW:
            raise ValueError('Input workflow differs from the current single-pass runner')
        models.add(model)
        validate_packet(slot['packet'], target, 'clinical_only')
        if base is not None and base != slot['packet']:
            raise ValueError('All compared models must share the same clinical evidence')
        base = deepcopy(slot['packet'])
    cutoff = target['information_cutoff_utc']
    patent_pack = patent_evidence.freeze_evidence_pack(drug, cutoff, data_dir=patent_root)
    if patent_pack['status'] != 'ready':
        raise ValueError('No qualifying selected patent evidence at the unchanged clinical cutoff')
    patents = [_card_evidence(item, card, cutoff) for item in patent_pack['items'] for card in item['experiment_cards']]
    ids = [e['id'] for e in base['evidence']] + [e['id'] for e in patents]
    if len(ids) != len(set(ids)):
        raise ValueError('Clinical and patent evidence identifiers must be unique')
    source_target_id = target['id']
    if cohort_id + '-target' == source_target_id:
        raise ValueError('A new cohort-specific target identifier is required')
    target.update(id=cohort_id + '-target', evidence_ids=ids,
                  patent_ids=sorted({e['publication_number'] for e in patents}))
    slots = []
    for original_slot in original['slots']:
        for track, condition in (('same_evidence', 'clinical_only'), ('patent_ablation', 'with_patents')):
            slot = {**deepcopy(original_slot), 'target_id': target['id'], 'track': track,
                    'condition': condition, 'workflow_version': WORKFLOW, 'packet': deepcopy(base)}
            if condition == 'with_patents':
                slot['packet']['evidence'].extend(deepcopy(patents))
            slots.append(slot)
    protocol = {**deepcopy(original['protocol']), 'version': VERSION,
        'season_kind': 'registered_patent_pair_pilot', 'maximum_slots': len(slots),
        'maximum_provider_requests': len(slots), 'clinical_prediction_validated': False,
        'patent_pair': {'source_cohort_id': original.get('cohort_id'), 'source_target_id': source_target_id,
            'source_specification_sha256': sha(original), 'clinical_packet_sha256': sha(base),
            'patent_packet_sha256': patent_pack['packet_sha256'], 'catalog_sha256': patent_pack['catalog_sha256'],
            'unchanged_information_cutoff_utc': cutoff, 'source_review_basis': 'two_agent_visual_source_check',
            'human_review_count': 0, 'same_base_model_correlation_disclosed': True,
            'clinical_prediction_validated': False, 'first_attempt_only': True,
            'interpretation': 'Probability change is not demonstrated prediction improvement; score only matching independently accepted future outcomes.'}}
    result = {'cohort_id': cohort_id, 'targets': [target], 'slots': slots, 'protocol': protocol}
    validate_pairs(result)
    return result, patent_pack


def write_proposal(output_dir, spec, patent_pack, *, input_file_sha256=None):
    """Create one exclusive proposal directory after all validation succeeds."""
    validate_pairs(spec)
    expected_hash = patent_evidence.canonical_sha256({k: v for k, v in patent_pack.items() if k != 'packet_sha256'})
    cutoff = spec['targets'][0]['information_cutoff_utc']
    if (patent_pack.get('packet_sha256') != expected_hash or patent_pack.get('cutoff') != cutoff
            or spec['protocol'].get('patent_pair', {}).get('patent_packet_sha256') != expected_hash):
        raise ValueError('Output patent pack differs from the prepared frozen pack')
    expected_evidence = [_card_evidence(item, card, cutoff) for item in patent_pack['items'] for card in item['experiment_cards']]
    if any([e for e in slot['packet']['evidence'] if e.get('kind') == 'patent'] != expected_evidence
           for slot in spec['slots'] if slot['condition'] == 'with_patents'):
        raise ValueError('Output selected patent cards differ from the frozen source pack')
    documents = {'spec.json': spec, 'budget.json': spec['protocol']['execution_budget'],
                 'patent_packet.json': patent_pack}
    raw = {name: (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + '\n').encode()
           for name, value in documents.items()}
    manifest = {'schema_version': VERSION, 'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'cohort_id': spec['cohort_id'], 'input_file_sha256': input_file_sha256,
        'files': {name: {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)} for name, data in raw.items()},
        'registration_performed': False, 'model_calls_performed': False, 'clinical_prediction_validated': False}
    raw['manifest.json'] = (json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode()
    output = Path(output_dir)
    output.mkdir(mode=0o700)
    for name, data in raw.items():
        descriptor = os.open(output / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--clinical-spec', required=True)
    parser.add_argument('--drug', required=True)
    parser.add_argument('--patent-root', required=True)
    parser.add_argument('--cohort-id', required=True)
    parser.add_argument('--output-dir', required=True)
    args = parser.parse_args()
    original, input_hash = read_spec(args.clinical_spec)
    spec, pack = prepare(original, drug=args.drug, patent_root=args.patent_root, cohort_id=args.cohort_id)
    manifest = write_proposal(args.output_dir, spec, pack, input_file_sha256=input_hash)
    print(json.dumps({'cohort_id': spec['cohort_id'], 'assigned_slots': len(spec['slots']),
        'files': manifest['files'], 'model_calls_performed': False}, ensure_ascii=False))


if __name__ == '__main__':
    main()
