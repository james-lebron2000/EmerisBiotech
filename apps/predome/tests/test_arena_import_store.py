"""Actual sealed bundle imports into temporary host ledgers; no provider calls."""
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from emeris_live import arena_imports
from emeris_live import arena_store
from emeris_live.arena_result_assurance import result_assurance
from emeris_live.arena_store import ArenaLedger, result_target_binding
from test_arena_core import registered, finish
from test_arena_imports import bundle_factory, signed


def install(ledger, bundle):
    return ledger.import_sealed_cohort(bundle['path'], bundle['payload']['bundle_sha256'])


def test_one_host_append_preserves_source_events_original_times_and_failure(tmp_path, bundle_factory):
    ledger, _, native_ids = registered(tmp_path / 'host')
    finish(ledger, native_ids[0])
    before = ledger.events()
    bundle = bundle_factory(model_ids=('glm-5.2', 'kimi-k3'), include_patents=True,
        statuses={('kimi-k3', 'clinical_only'): 'failed'})
    original = deepcopy(bundle['events'])
    receipt = install(ledger, bundle)
    after = ledger.events()
    assert after[:-1] == before and len(after) == len(before) + 1
    assert after[-1]['kind'] == arena_imports.IMPORT_KIND
    assert after[-1]['payload']['source_events'] == original
    assert receipt['appended'] is True and receipt['provider_calls'] == 0
    view = ledger.snapshot()
    own = [r for r in view['runs'] if r['id'] in bundle['run_ids']]
    assert len(own) == 4 and sum(r['status'] == 'failed' for r in own) == 1
    failed = next(r for r in own if r['status'] == 'failed')
    assert failed['probability'] is None and failed['error_code'] == 'invalid_json'
    for run in own:
        completion = next(e for e in original if e['kind'] == 'run_completed' and e['entity_id'] == run['id'])
        assert run['generated_at_utc'] == run['locked_at_utc'] == completion['created_at']
        assert run['completion_event_seq'] == completion['seq']
        assert run['completion_event_epoch'] == bundle['payload']['source_ledger_sha256']
        assert run['record_origin']['synced_at_utc'] == receipt['synced_at_utc']
        assert run['record_origin']['source_event_count'] == len(original)
        assert run['timestamp_proof']['url'] is None
        assert run['timestamp_proof']['manifest_sha256'] == bundle['payload']['proofs']['predictions']['manifest_sha256']
    assert sum(len(r.get('citation_reviews', [])) for r in own) == 3
    assert all(r.get('reviewer_kind') == 'ai' for run in own for r in run.get('citation_reviews', []))
    target = next(t for t in view['targets'] if t['id'] == bundle['target_id'])
    assert target['result'] is None and target['status'] == 'pending'
    assert view['events'] == after and view['ledger_sha256'] == after[-1]['event_hash']


def test_repeat_import_is_idempotent_not_a_new_prediction(tmp_path, bundle_factory):
    ledger = ArenaLedger(tmp_path / 'host'); bundle = bundle_factory()
    first = install(ledger, bundle); before = ledger.events()
    second = install(ledger, bundle)
    assert second == {**first, 'appended': False}
    assert ledger.events() == before


def test_original_tsa_epoch_cannot_be_replaced_by_earlier_host_tsa(tmp_path, bundle_factory):
    ledger, _, ids = registered(tmp_path / 'host')
    finish(ledger, ids[0])
    with ledger.connect() as db:
        for index in range(30):
            ledger._append(db, 'engineering_marker', 'marker-' + str(index), {})
    before = ledger.events(); at = datetime.now(timezone.utc).isoformat()
    ledger.attest({'purpose': 'predictions', 'ledger_sha256': before[-1]['event_hash'],
        'covered_event_count': len(before), 'generated_at_utc': at, 'verified_at_utc': at,
        'manifest_sha256': 'a' * 64, 'timestamp_response_sha256': 'b' * 64, 'signature_verified': True,
        'verification_method': 'openssl_ts_verify', 'tsa': 'synthetic-host-proof',
        'public_url': 'https://example.org/host-proof'})
    bundle = bundle_factory(); install(ledger, bundle)
    view = ledger.snapshot()
    native = next(r for r in view['runs'] if r['id'] == ids[0])
    imported = next(r for r in view['runs'] if r['id'] in bundle['run_ids'])
    assert native['timestamp_proof']['manifest_sha256'] == 'a' * 64
    assert imported['completion_event_seq'] < len(before)
    assert imported['timestamp_proof']['manifest_sha256'] != 'a' * 64
    assert imported['timestamp_proof']['generated_at_utc'] == bundle['payload']['proofs']['predictions']['generated_at_utc']


@pytest.mark.parametrize('action', ['start', 'finish', 'withdraw'])
def test_sealed_runs_cannot_be_reexecuted_replaced_or_withdrawn(tmp_path, bundle_factory, action):
    ledger = ArenaLedger(tmp_path / 'host'); bundle = bundle_factory(); install(ledger, bundle)
    before = ledger.events()
    with pytest.raises(ValueError, match='imported|Imported'):
        if action == 'start':
            ledger.start(bundle['run_ids'][0])
        elif action == 'finish':
            ledger.finish(bundle['run_ids'][0], {'status': 'failed', 'probability': None})
        else:
            ledger.withdraw_unstarted_cohort(bundle['payload']['source_cohort_id'], 'Cannot erase a completed imported cohort')
    assert ledger.events() == before


def test_later_source_issue_and_ai_correction_preserve_original_outputs(tmp_path, bundle_factory):
    ledger = ArenaLedger(tmp_path / 'host'); bundle = bundle_factory(); install(ledger, bundle)
    original = deepcopy(bundle['events'])
    review = next(e for e in original if e['kind'] == 'citation_review')
    assessment = deepcopy(review['payload']['assessment'])
    assessment['pairs'][0]['note'] += ' Additional engineering source check.'
    kwargs = {k: review['payload'][k] for k in ('reviewer_id', 'reviewer_kind')}
    with pytest.raises(ValueError, match='latest review'):
        ledger.record_citation_review(review['entity_id'], assessment, **kwargs)
    ledger.record_citation_review(review['entity_id'], assessment,
        supersedes=review['payload']['review_sha256'], **kwargs)
    evidence = next(e['payload']['packet']['evidence'][0] for e in original if e['kind'] == 'slot_registered')
    ledger.record_evidence_issue(evidence['id'], evidence['source_sha256'],
        note='Synthetic later source discrepancy remains visible.', reference_url='https://example.org/correction')
    view = ledger.snapshot(); run = next(r for r in view['runs'] if r['id'] == review['entity_id'])
    assert run['probability'] == .68 and len(run['citation_reviews']) == 2
    assert run['input_evidence_issues'][0]['severity'] == 'blocking'
    assert ledger.events()[0]['payload']['source_events'] == original


def test_later_operator_result_requires_exact_imported_target_and_stays_unaccepted(tmp_path, bundle_factory):
    ledger = ArenaLedger(tmp_path / 'host'); bundle = bundle_factory(); install(ledger, bundle)
    target = next(e['payload'] for e in bundle['events'] if e['kind'] == 'target_registered')
    result = {'label': 'mixed', 'source_url': 'https://example.org/actual-synthetic-result',
        'source_sha256': 'a' * 64, 'first_public_at': datetime.now(timezone.utc).isoformat(),
        'target_binding': result_target_binding(target), 'endpoint_match_note': 'Synthetic exact-target fixture only.'}
    for reviewer in ('ai-a', 'ai-b'):
        ledger.record_result(target['id'], result, reviewer_id=reviewer, reviewer_kind='ai',
            review_note='Synthetic AI-only source comparison; no scientific credentials.')
    projected = next(t for t in ledger.snapshot()['targets'] if t['id'] == target['id'])
    assert projected['result']['label'] == 'mixed'
    assert result_assurance(projected)['status'] == 'agreement_pending_independent_review'
    assert result_assurance(projected)['scoring_eligible'] is False
    assert projected['result']['review_provenance']['review_counts']['human'] == 0


@pytest.mark.parametrize('corruption', ['missing', 'changed'])
def test_stored_original_archive_must_remain_available_and_unchanged(tmp_path, bundle_factory, corruption):
    ledger = ArenaLedger(tmp_path / 'host'); bundle = bundle_factory(); install(ledger, bundle)
    archive = ledger.path.parent / arena_imports.DIRECTORY / (bundle['payload']['bundle_sha256'] + '.tar.gz')
    if corruption == 'missing':
        archive.unlink()
    else:
        archive.write_bytes(b'changed archive')
    with pytest.raises((OSError, ValueError)):
        ledger.snapshot()
    assert len(ledger.events()) == 1


def test_incorrect_transport_hash_leaves_host_events_and_archive_directory_untouched(tmp_path, bundle_factory):
    ledger, _, _ = registered(tmp_path / 'host'); bundle = bundle_factory(); before = ledger.events()
    with pytest.raises(ValueError):
        ledger.import_sealed_cohort(bundle['path'], '0' * 64)
    assert ledger.events() == before
    assert not (ledger.path.parent / arena_imports.DIRECTORY).exists()


def test_native_registration_cannot_reuse_imported_target_identity(tmp_path, bundle_factory):
    ledger = ArenaLedger(tmp_path / 'host'); bundle = bundle_factory(); install(ledger, bundle)
    target = next(e['payload'] for e in bundle['events'] if e['kind'] == 'target_registered')
    slot = next(e['payload'] for e in bundle['events'] if e['kind'] == 'slot_registered')
    before = ledger.events()
    with pytest.raises(ValueError, match='Target ID already frozen'):
        ledger.register('new-native-cohort', [target], [slot], {'maximum_slots': 1})
    assert ledger.events() == before


def test_later_host_event_collision_rolls_back_instead_of_poisoning_projection(tmp_path, bundle_factory, monkeypatch):
    ledger = ArenaLedger(tmp_path / 'host'); bundle = bundle_factory(); install(ledger, bundle)
    collision = next(e['id'] for e in bundle['events'] if e['kind'] == 'citation_review')
    before = ledger.events()
    monkeypatch.setattr(arena_store.uuid, 'uuid4', lambda: SimpleNamespace(hex=collision))
    source = next(e['payload']['packet']['evidence'][0] for e in bundle['events'] if e['kind'] == 'slot_registered')
    with pytest.raises(ValueError, match='host event ID collides'):
        ledger.record_evidence_issue(source['id'], source['source_sha256'],
            note='Synthetic source discrepancy must remain an atomic rejected write.',
            reference_url='https://example.org/fixture-collision')
    assert ledger.events() == before
    assert len(ledger.snapshot()['runs']) == 1
