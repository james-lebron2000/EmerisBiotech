"""One real temporary ledger drives public, score and offline assurance gates."""
from copy import deepcopy
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from emeris_live import arena_public as public
from emeris_live.arena_api import _target_review_view
from emeris_live.arena_metrics import build_metrics, patent_pairs
from emeris_live.arena_result_assurance import result_assurance, revoke_scientific_adjudication
from emeris_live.arena_store import result_target_binding
from arena_scientific_fixture import scientific_acceptance_document, scientifically_accept
from test_arena_core import registered, finish


@pytest.fixture
def learning_module():
    spec = importlib.util.spec_from_file_location('scientific_consumer_learning',
        Path(__file__).parents[1] / 'scripts/arena_learning_report.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def agreed_ledger(tmp_path, kind='ai', label='success'):
    ledger, target, ids = registered(tmp_path / 'isolated-runtime')
    for ident, model, probability in zip(ids, ('A', 'B'), (.8, .2)):
        finish(ledger, ident, probability, returned_model=model + '-fixed')
    result = {'target_binding': result_target_binding(target), 'label': label,
        'source_url': 'https://example.org/isolated-source', 'source_sha256': 'b' * 64,
        'first_public_at': datetime.now(timezone.utc).isoformat(),
        'endpoint_match_note': 'Synthetic source agreement for the exact primary hypothesis.'}
    for reviewer in ('isolated-one', 'isolated-two'):
        ledger.record_result(target['id'], result, reviewer_id=reviewer, reviewer_kind=kind,
            review_note='Synthetic source inspection; no real scientific reviewer exists.')
    return ledger, target


def public_client(ledger, monkeypatch):
    # The unrelated historical archive is absent. The new Arena ledger and its
    # private original-document validation run through the actual read-only API.
    monkeypatch.setattr(public, 'read_archived', lambda store: ([], [], {}))
    app = FastAPI()
    app.state.store = SimpleNamespace(path=ledger.path.parent)
    app.include_router(public.router)
    return TestClient(app)


def assert_unqualified(ledger, learning, client, target_id):
    snapshot = learning.read_snapshot(ledger.path)
    target = snapshot['targets'][0]
    assert result_assurance(target)['scoring_eligible'] is False
    internal = _target_review_view(target)
    assert internal['result']['scoring_eligible'] is False
    assert internal['result']['review_status'] == 'agreement_pending_independent_review'
    metrics = build_metrics(snapshot)
    assert metrics['assigned'] == metrics['answered'] == 2
    assert metrics['common_tasks'] == 0
    assert all(row['brier'] is None for row in metrics['by_model'])
    assert metrics['paired_differences'][0]['delta_brier_a_minus_b'] is None
    report = learning.build_report(snapshot)
    assert report['outcome_losses'] == []
    assert not any(p['kind'] == 'evidence_hypothesis_review' for p in report['version_proposals'])
    response = client.get('/api/arena/public/targets/' + target_id)
    assert response.status_code == 200, response.text
    view = response.json()
    assert view['target']['status'] == view['result']['status'] == 'agreement_pending_independent_review'
    assert view['result']['label'] is None and view['result']['scoring_eligible'] is False
    assert view['result']['source_url']
    assert all(r['match'] is None and r['match_status'] == 'not_evaluable' for r in view['runs'])
    return snapshot


@pytest.mark.parametrize('kind', ['ai', 'human', 'unspecified', 'account_only'])
def test_source_agreement_does_not_issue_formal_hits_scores_or_outcome_errors(tmp_path, monkeypatch, learning_module, kind):
    ledger, target = agreed_ledger(tmp_path, 'ai' if kind == 'account_only' else kind)
    if kind == 'account_only':
        scientific_acceptance_document(ledger, target)  # Prepare only; no acceptance.
    before = ledger.events()
    snapshot = assert_unqualified(ledger, learning_module, public_client(ledger, monkeypatch), target['id'])
    assert snapshot['targets'][0]['status'] == 'adjudicated'  # Original agreement remains.
    assert ledger.events() == before


def test_documented_acceptance_enables_consumers_and_revocation_removes_all_credit(tmp_path, monkeypatch, learning_module):
    ledger, target = agreed_ledger(tmp_path)
    previous = ledger.events()
    acceptance = scientifically_accept(ledger, target)
    assert ledger.events()[:len(previous)] == previous
    snapshot = learning_module.read_snapshot(ledger.path)
    assert result_assurance(snapshot['targets'][0])['scoring_eligible'] is True
    internal = _target_review_view(snapshot['targets'][0])
    assert internal['result']['review_status'] == 'accepted'
    assert internal['result']['scoring_eligible'] is True
    assert build_metrics(snapshot)['common_tasks'] == 1
    assert len(learning_module.build_report(snapshot)['outcome_losses']) == 2
    client = public_client(ledger, monkeypatch)
    response = client.get('/api/arena/public/targets/' + target['id'])
    assert response.status_code == 200
    view = response.json()
    assert view['result']['review_status'] == 'accepted'
    assert view['result']['scoring_eligible'] is True
    assert [r['match'] for r in view['runs']] == [True, False]
    private = {'scientific_adjudication_acceptance', 'qualification_evidence', 'independence_statement',
        'principal_id', 'account_id', 'acceptor', 'scientific_role', 'acceptance_event_sha256'}
    def keys(value):
        if isinstance(value, dict):
            return set(value).union(*(keys(v) for v in value.values()))
        if isinstance(value, list):
            return set().union(*(keys(v) for v in value))
        return set()
    assert not private & keys(view)
    assert str(ledger.path.parent) not in response.text and 'PRIVATE SYNTHETIC' not in response.text
    revoke_scientific_adjudication(ledger, target['id'], 'Synthetic acceptance operator',
        'Synthetic independent acceptance is explicitly revoked for this test.', supersedes=acceptance['event_hash'])
    assert_unqualified(ledger, learning_module, client, target['id'])


def test_missing_private_original_rejects_new_api_and_offline_reads(tmp_path, monkeypatch, learning_module):
    ledger, target = agreed_ledger(tmp_path)
    scientifically_accept(ledger, target)
    before = ledger.events()
    assert build_metrics(ledger.snapshot())['common_tasks'] == 1
    next((ledger.path.parent / 'scientific_review_evidence').glob('*.bin')).unlink()
    assert_unqualified(ledger, learning_module, public_client(ledger, monkeypatch), target['id'])
    assert ledger.events() == before


def test_json_flags_cannot_self_issue_scientific_acceptance(tmp_path):
    ledger, target = agreed_ledger(tmp_path)
    scientifically_accept(ledger, target)
    snapshot = ledger.snapshot()
    clone = json.loads(json.dumps(snapshot))
    clone['targets'][0]['result']['review_provenance'].update(
        scientific_credentials_verified=True, independent_human_review_verified=True)
    assert build_metrics(snapshot)['common_tasks'] == 1
    assert build_metrics(clone)['common_tasks'] == 0
    assert public.result_view(clone['targets'][0])['label'] is None


@pytest.mark.parametrize('label', ['mixed', 'inconclusive', 'not_adjudicable'])
def test_accepted_nonbinary_outcomes_are_visible_without_binary_credit(tmp_path, label, learning_module):
    ledger, target = agreed_ledger(tmp_path, label=label)
    scientifically_accept(ledger, target)
    snapshot = ledger.snapshot()
    value = public.result_view(snapshot['targets'][0])
    assert value['status'] == 'adjudicated' and value['review_status'] == 'accepted'
    assert value['label'] == ('inconclusive' if label == 'not_adjudicable' else label)
    assert value['scoring_eligible'] is False
    assert build_metrics(snapshot)['common_tasks'] == 0
    assert learning_module.build_report(snapshot)['outcome_losses'] == []


def test_patent_delta_waits_for_the_same_scientific_acceptance(tmp_path):
    ledger, target = agreed_ledger(tmp_path)
    snapshot = ledger.snapshot()
    control = snapshot['runs'][0]
    treated = deepcopy(control)
    treated.update(id='synthetic-patent-condition', track='patent_ablation', condition='with_patents', probability=.9)
    treated['packet']['evidence'].append({'kind': 'patent', 'id': 'synthetic-patent'})
    snapshot['runs'] = [control, treated]
    assert patent_pairs(snapshot)[0]['probability_change'] == pytest.approx(.1)
    assert patent_pairs(snapshot)[0]['delta_brier_with_minus_without'] is None
    scientifically_accept(ledger, target)
    snapshot['targets'] = ledger.snapshot()['targets']
    assert patent_pairs(snapshot)[0]['delta_brier_with_minus_without'] == pytest.approx(-.03)
