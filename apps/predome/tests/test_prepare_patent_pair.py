"""Real preparation, catalog review gates and temporary registration; no paid calls."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest

from emeris_live import arena_providers, patent_evidence
from emeris_live.arena_store import ArenaLedger, canonical, sha
from test_arena_core import admin_module, spec as clinical_fixture
from test_patent_identity import build_root  # Production builder + public receipt, synthetic OCR inventory.

ROOT = Path(__file__).parents[1]
MODULE = importlib.util.spec_from_file_location('prepare_patent_pair', ROOT / 'scripts/prepare_patent_pair.py')
pair = importlib.util.module_from_spec(MODULE)
MODULE.loader.exec_module(pair)


@pytest.fixture
def proposal(build_root):
    root, builder = build_root
    builder.build_catalog(root)
    target, slots = clinical_fixture()
    target['drug'] = 'retatrutide'
    target['information_cutoff_utc'] = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    for slot in slots:
        slot['workflow_version'] = pair.WORKFLOW
        slot['packet']['data_cutoff'] = target['information_cutoff_utc']
    budget = {'budget_policy': 'token_only_unpriced', 'max_requests': 1,
              'max_output_tokens': 8192, 'max_input_chars': 30000, 'timeout_seconds': 120,
              'max_total_output_tokens': 8192, 'max_total_timeout_seconds': 120}
    return {'cohort_id': 'clinical-input', 'targets': [target], 'slots': slots,
            'protocol': {'execution_budget': budget, 'clinical_prediction_validated': False}}, root


def prepare(proposal):
    original, root = proposal
    return pair.prepare(original, drug='retatrutide', patent_root=root, cohort_id='new-patent-pair')


def rewrite_catalog(root, mutate):
    path = root / 'catalog.json'
    catalog = json.loads(path.read_text())
    mutate(catalog['items'][0])
    catalog['catalog_sha256'] = patent_evidence.canonical_sha256({k: v for k, v in catalog.items() if k != 'catalog_sha256'})
    path.write_text(json.dumps(catalog))


def test_real_review_card_generates_exact_pairs_and_preserves_every_input(proposal, monkeypatch):
    original, root = proposal
    before = deepcopy(original)
    files = {p.relative_to(root): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}
    monkeypatch.setattr(patent_evidence, '_http', lambda *a, **k: pytest.fail('Network forbidden'))
    monkeypatch.setattr(arena_providers, 'forecast', lambda *a, **k: pytest.fail('Model call forbidden'))
    result, pack = prepare(proposal)
    assert len(result['slots']) == 4
    assert pack['status'] == 'ready'
    assert result['cohort_id'] != original['cohort_id']
    assert result['targets'][0]['id'] != original['targets'][0]['id']
    assert result['targets'][0]['project_id'] == original['targets'][0]['project_id']
    assert result['targets'][0]['information_cutoff_utc'] == original['targets'][0]['information_cutoff_utc']
    assert result['targets'][0]['readout_screen'] == original['targets'][0]['readout_screen']
    assert result['protocol']['execution_budget'] == original['protocol']['execution_budget']
    assert result['protocol']['maximum_provider_requests'] == 4
    assert result['protocol']['season_kind'] == 'registered_patent_pair_pilot'
    for model in ('A', 'B'):
        arms = {s['condition']: s for s in result['slots'] if s['model_id'] == model}
        control, treated = arms['clinical_only'], arms['with_patents']
        assert control['packet'] == original['slots'][0]['packet']
        assert control['requested_model'] == treated['requested_model']
        assert control['workflow_version'] == treated['workflow_version'] == pair.WORKFLOW
        evidence = treated['packet']['evidence'][-1]
        assert evidence['example_id'] == '12'
        assert evidence['human_review_count'] == 0 and evidence['research_use_only'] is True
        assert evidence['clinical_prediction_validated'] is False
        assert evidence['same_base_model_correlation_disclosed'] is True
        assert evidence['review_receipt'] == pack['items'][0]['research_review']['receipt_sha256']
        text = json.loads(evidence['text'])
        card = text['scientific_experiment_card']
        assert card == pack['items'][0]['experiment_cards'][0]
        assert card['result']['groups'][2]['sem_nM'] == 1.41
        assert card['dose']['example_range']['minimum_unit'] == 'fM'
        assert card['source_discrepancies'][1]['status'] == 'unresolved_source_relationship'
        assert 'assay_conditions' in card and 'missing_fields' in card
        assert text['source_review_disclosure']['human_review_count'] == 0
        projected = arena_providers._packet(treated['packet'])['evidence'][-1]
        assert projected['text'] == evidence['text']
        assert projected['review_receipt'] == evidence['review_receipt']
    assert original == before
    assert files == {p.relative_to(root): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}
    assert not list(root.rglob('arena.sqlite3'))


def test_output_is_registerable_through_real_operator_cli(proposal, tmp_path, monkeypatch):
    result, pack = prepare(proposal)
    output = tmp_path / 'proposal'
    pair.write_proposal(output, result, pack)
    config = tmp_path / 'models.json'
    config.write_text(json.dumps({'models': [{'id': model, 'provider': 'openai', 'model': model + '-fixed',
        'api_key_env': 'ISOLATED_UNUSED_KEY', 'max_output_tokens': 8192, 'timeout_seconds': 120} for model in ('A', 'B')]}))
    monkeypatch.setenv('EMERIS_ARENA_MODELS_FILE', str(config))
    monkeypatch.setenv('EMERIS_ENABLE_ARENA_LLM', 'false')
    monkeypatch.setattr(arena_providers, 'forecast', lambda *a, **k: pytest.fail('Model call forbidden'))
    admin = admin_module()
    runtime = tmp_path / 'isolated-runtime'
    monkeypatch.setattr(sys, 'argv', ['arena_admin', '--data-dir', str(runtime), 'register', str(output / 'spec.json')])
    admin.main()
    snapshot = ArenaLedger(runtime).snapshot()
    assert len(snapshot['runs']) == 4
    assert len({r['id'] for r in snapshot['runs']}) == 4
    assert {r['scope'] for r in snapshot['runs']} == {'prospective_challenge'}
    assert all(r['status'] == 'pending' and not r.get('started_at') for r in snapshot['runs'])
    for run in snapshot['runs']:
        admin.verify_registered_model(run, next(m for m in admin.list_models() if m['id'] == run['model_id']), result['protocol']['execution_budget'])


@pytest.mark.parametrize('fault', ['multiple_targets', 'empty_slots', 'mixed_arm', 'shared_workflow',
    'changed_clinical', 'duplicate_model', 'wrong_target', 'frozen_slot', 'wrong_drug',
    'old_workflow', 'research_scope', 'missing_budget', 'small_input_budget'])
def test_invalid_clinical_preparation_fails_without_outputs(proposal, fault):
    original, root = proposal
    if fault == 'multiple_targets': original['targets'].append(deepcopy(original['targets'][0]))
    elif fault == 'empty_slots': original['slots'] = []
    elif fault == 'mixed_arm': original['slots'][0]['condition'] = 'with_patents'
    elif fault == 'shared_workflow': original['slots'][0]['track'] = 'shared_agent'
    elif fault == 'changed_clinical': original['slots'][1]['packet']['evidence'][0]['text'] += ' Changed.'
    elif fault == 'duplicate_model': original['slots'].append(deepcopy(original['slots'][0]))
    elif fault == 'wrong_target': original['slots'][0]['target_id'] = 'not-target'
    elif fault == 'frozen_slot': original['slots'][0]['input_sha256'] = 'a' * 64
    elif fault == 'wrong_drug': original['targets'][0]['drug'] = 'different drug'
    elif fault == 'old_workflow': original['slots'][0]['workflow_version'] = 'old-version:single_pass'
    elif fault == 'research_scope': original['protocol']['scope'] = 'research_only'
    elif fault == 'missing_budget': original['protocol']['execution_budget'].pop('timeout_seconds')
    elif fault == 'small_input_budget': original['protocol']['execution_budget']['max_input_chars'] = 512
    with pytest.raises(ValueError): prepare(proposal)
    assert not list(root.rglob('arena.sqlite3'))


@pytest.mark.parametrize('fault', ['stale_screen', 'known_result', 'deadline_passed', 'future_source', 'cutoff_changed'])
def test_existing_prospective_runtime_gates_are_not_relaxed(proposal, fault):
    original, _ = proposal
    target = original['targets'][0]
    if fault == 'stale_screen': target['readout_screen']['checked_at_utc'] = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    elif fault == 'known_result': target['result'] = {'label': 'success'}
    elif fault == 'deadline_passed': target['submission_deadline_utc'] = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    elif fault == 'future_source':
        for slot in original['slots']: slot['packet']['evidence'][0]['available_at'] = target['submission_deadline_utc']
    elif fault == 'cutoff_changed': target['information_cutoff_utc'] = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    with pytest.raises(ValueError): prepare(proposal)


@pytest.mark.parametrize('fault', ['unreviewed', 'changed_number', 'one_ai', 'human_claim', 'wrong_identity', 'late_review', 'old_cutoff', 'missing_receipt'])
def test_real_catalog_and_receipt_gates_reject_unqualified_patent_cards(proposal, fault):
    original, root = proposal
    def mutate(item):
        if fault == 'unreviewed': item['experiment_cards'][0]['review']['status'] = 'machine_extracted'
        elif fault == 'changed_number': item['experiment_cards'][0]['result']['groups'][2]['sem_nM'] = 141
        elif fault == 'one_ai': item['research_review']['reviewers'].pop()
        elif fault == 'human_claim': item['research_review']['human_review_count'] = 2
        elif fault == 'wrong_identity': item['research_review']['identity_analysis']['candidate']['residues'][0] = 'D-Tyr'
        elif fault == 'late_review': item['research_review']['reviewed_at'] = original['targets'][0]['submission_deadline_utc']
        elif fault == 'missing_receipt': item['experiment_cards'][0]['review'].pop('receipt_sha256')
    if fault == 'old_cutoff':
        cutoff = '2026-10-23T00:00:00+00:00'
        original['targets'][0]['information_cutoff_utc'] = cutoff
        for slot in original['slots']:
            slot['packet']['data_cutoff'] = cutoff
            slot['packet']['evidence'][0]['available_at'] = '2026-10-22T00:00:00+00:00'
    else: rewrite_catalog(root, mutate)
    with pytest.raises(ValueError, match='No qualifying'): prepare(proposal)


@pytest.mark.parametrize('fault', ['missing_arm', 'duplicate_arm', 'changed_clinical', 'changed_model_version', 'changed_workflow'])
def test_pair_preflight_catches_post_preparation_contract_changes(proposal, fault):
    result, _ = prepare(proposal)
    if fault == 'missing_arm': result['slots'].pop()
    elif fault == 'duplicate_arm': result['slots'].append(deepcopy(result['slots'][0]))
    elif fault == 'changed_clinical': result['slots'][1]['packet']['evidence'][0]['text'] += ' Changed clinical fact.'
    elif fault == 'changed_model_version': result['slots'][1]['requested_model'] = 'another-version'
    elif fault == 'changed_workflow': result['slots'][1]['workflow_version'] = 'changed-workflow'
    with pytest.raises(ValueError): pair.validate_pairs(result)


def test_source_and_output_files_are_exclusive_hash_bound_and_private(proposal, tmp_path):
    result, pack = prepare(proposal)
    output = tmp_path / 'prepared'
    manifest = pair.write_proposal(output, result, pack, input_file_sha256='b' * 64)
    assert set(p.name for p in output.iterdir()) == {'spec.json', 'budget.json', 'patent_packet.json', 'manifest.json'}
    assert manifest['registration_performed'] is False and manifest['model_calls_performed'] is False
    assert manifest['input_file_sha256'] == 'b' * 64
    for name, source in manifest['files'].items():
        assert hashlib.sha256((output / name).read_bytes()).hexdigest() == source['sha256']
        assert (output / name).stat().st_mode & 0o777 == 0o600
    before = {p.name: p.read_bytes() for p in output.iterdir()}
    with pytest.raises(FileExistsError): pair.write_proposal(output, result, pack)
    assert before == {p.name: p.read_bytes() for p in output.iterdir()}


@pytest.mark.parametrize('fault', ['pack_mutated', 'other_pack', 'card_text_changed'])
def test_output_refuses_mismatched_or_changed_scientific_bundle_before_creating_directory(proposal, tmp_path, fault):
    result, pack = prepare(proposal)
    if fault == 'pack_mutated': pack['items'][0]['experiment_cards'][0]['result']['groups'][2]['sem_nM'] = 141
    elif fault == 'other_pack':
        pack['drug'] = 'another drug'
        pack['packet_sha256'] = patent_evidence.canonical_sha256({k: v for k, v in pack.items() if k != 'packet_sha256'})
    elif fault == 'card_text_changed':
        for slot in result['slots']:
            if slot['condition'] == 'with_patents': slot['packet']['evidence'][-1]['text'] += ' Unbound changed content.'
    output = tmp_path / 'must-not-exist'
    with pytest.raises(ValueError): pair.write_proposal(output, result, pack)
    assert not output.exists()


@pytest.mark.parametrize('fault', ['unknown', 'stage_list', 'unknown_policy', 'two_currency_caps', 'invalid_amount'])
def test_invalid_execution_budget_cannot_prepare_an_unusable_run(proposal, fault):
    budget = proposal[0]['protocol']['execution_budget']
    if fault == 'unknown': budget['unrecognized_policy'] = True
    elif fault == 'stage_list': budget['stage_budgets'] = ['forecast']
    elif fault == 'unknown_policy': budget['budget_policy'] = 'not-real'
    elif fault == 'two_currency_caps': budget.update(max_cost_usd=1, max_cost_amount=1)
    elif fault == 'invalid_amount': budget.update(max_cost_amount=True, cost_currency='CNY')
    with pytest.raises(ValueError): prepare(proposal)


def test_main_reads_actual_file_and_never_calls_or_registers(proposal, tmp_path, monkeypatch, capsys):
    original, root = proposal
    source = tmp_path / 'clinical.json'
    source.write_text(json.dumps(original))
    before = source.read_bytes()
    output = tmp_path / 'new-proposal'
    monkeypatch.setattr(sys, 'argv', ['prepare_patent_pair', '--clinical-spec', str(source),
        '--drug', 'retatrutide', '--patent-root', str(root), '--cohort-id', 'cli-new-pair', '--output-dir', str(output)])
    pair.main()
    status = json.loads(capsys.readouterr().out)
    assert status['assigned_slots'] == 4 and status['model_calls_performed'] is False
    manifest = json.loads((output / 'manifest.json').read_text())
    assert manifest['input_file_sha256'] == hashlib.sha256(before).hexdigest()
    assert source.read_bytes() == before
    assert not list(tmp_path.rglob('arena.sqlite3'))


@pytest.mark.parametrize('raw', ['{"targets":[],"targets":[]}', '{"x":NaN}'])
def test_duplicate_or_nonfinite_source_json_rejected(tmp_path, raw):
    source = tmp_path / 'bad.json'; source.write_text(raw)
    with pytest.raises(ValueError): pair.read_spec(source)
