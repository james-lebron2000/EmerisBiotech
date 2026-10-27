"""Exact-target calendar updates use real temporary stores, never live reviews."""
from copy import deepcopy
import json
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from emeris_live import arena_public as public, intake, readout_updates
from emeris_live.arena_public_contract import validate_current_readout
from emeris_live.arena_store import ArenaLedger
from emeris_live.platform_store import digest
from emeris_live.store import Store
from test_arena_core import finish, spec


@pytest.fixture
def native_calendar(tmp_path, monkeypatch):
    store = Store(tmp_path / 'synthetic-calendar')
    ledger = ArenaLedger(store.path)
    target, slots = spec()
    target.update(trial_name='Synthetic exact clinical question', drug='Synthetic study drug',
                  readout={'label': 'H2 2026 recorded guidance', 'start': '2026-07-01',
                           'end': '2026-12-31', 'precision': 'half_year',
                           'source_url': 'https://example.org/original-guidance'})
    ids = ledger.register('synthetic-calendar-season', [target], slots,
                          {'season_kind': 'registered_12_project_season'})
    finish(ledger, ids[0], .7, returned_model='A-fixed')
    ledger.start(ids[1])
    ledger.finish(ids[1], {'status': 'failed', 'probability': None, 'error_code': 'invalid_json'})
    intake.initialize(store)
    quote = 'The NCT00000001 primary readout is expected in 2027. Synthetic fixture source only.'
    cid, _ = intake.save_candidate(store, target['nct_id'], 'official_announcement_candidate',
        'https://example.org/new-synthetic-guidance', quote.encode(),
        {'text': quote, 'published_at': '2020-01-01', 'content_type': 'text/plain'},
        hash_scope='source_response_bytes')
    updates = readout_updates.ReadoutUpdateStore(store)
    proposal = {'nct_id': target['nct_id'], 'candidate_id': cid,
                'date_kind': 'estimated_window', 'start': '2027-01-01', 'end': '2027-12-31',
                'precision': 'year', 'label': '2027 synthetic approved guidance',
                'quoted_evidence': quote, 'reason': 'Synthetic source matches this registered analysis.',
                'arena_target_id': target['id'], 'source_granularity': 'named_trial',
                'applicability': 'exact_registered_target'}
    monkeypatch.setattr(public, 'read_archived', lambda _: ([], [], {}))
    app = FastAPI(); app.state.store = store; app.include_router(public.router)
    return SimpleNamespace(store=store, ledger=ledger, target=target, updates=updates,
                           proposal=proposal, client=TestClient(app))


def approve(value, proposal=None):
    candidate = value.updates.propose('synthetic-submitter', proposal or value.proposal)
    confirmations = {key: True for key in readout_updates.CONFIRMATIONS}
    confirmations['exact_target_applicability_confirmed'] = True
    value.updates.review('synthetic-independent-account', candidate['id'], 'approved',
                         'Synthetic isolated source and exact target were reviewed.', confirmations)
    return candidate['id']


def test_approved_display_is_separate_and_does_not_change_forecasts_or_scores(native_calendar, monkeypatch):
    value = native_calendar
    before = value.ledger.events()
    original = value.client.get('/api/arena/public/targets/' + value.target['id']).json()
    assert 'current_readout' not in original['target']
    approve(value)
    # Public reads must not call the schema-writing Store interface.
    with monkeypatch.context() as patch:
        def forbidden(*args, **kwargs):
            raise AssertionError('Public calendar GET must not initialize or write a store')
        patch.setattr(value.store, 'connect', forbidden)
        selected = value.client.get('/api/arena/public/targets/' + value.target['id']).json()
        record = value.client.get('/api/arena/public/targets/' + value.target['id'] + '/record').json()
        overview = value.client.get('/api/arena/public').json()
        download = value.client.get('/api/arena/public/targets/' + value.target['id'] + '/brief/download')
    update = selected['target']['current_readout']
    assert validate_current_readout(update) == update
    assert update['start'] == '2027-01-01' and update['original_prediction_unchanged'] is True
    assert update['timestamp_attestation'] == 'server_clock_not_independent_timestamp'
    assert selected['target']['readout'] == original['target']['readout']
    assert selected['runs'] == original['runs'] and selected['consensus'] == original['consensus']
    assert selected['result'] is None and selected['target']['status'] == 'pending'
    assert record == selected and overview['targets'][0] == selected['target']
    assert download.status_code == 200
    assert value.ledger.events() == before
    encoded = json.dumps(selected)
    for private in ('synthetic-submitter', 'synthetic-independent-account', 'quoted_evidence',
                    'confirmations', 'arena_target_binding', 'candidate_id'):
        assert private not in encoded


def test_pending_rejected_and_legacy_nct_approvals_do_not_cover_formal_target(native_calendar):
    value = native_calendar
    pending = value.updates.propose('synthetic-submitter', value.proposal)
    assert public.current_readouts(value.store, value.ledger.snapshot()) == {}
    value.updates.review('synthetic-independent-account', pending['id'], 'rejected',
                         'Synthetic source still requires target-specific verification.')
    assert public.current_readouts(value.store, value.ledger.snapshot()) == {}
    legacy = {key: item for key, item in value.proposal.items()
              if key not in ('arena_target_id', 'source_granularity', 'applicability')}
    approve(value, legacy)
    assert public.current_readouts(value.store, value.ledger.snapshot()) == {}


def test_latest_approval_preserves_prior_records_and_can_withdraw_a_date(native_calendar):
    value = native_calendar
    first = approve(value)
    original = deepcopy(value.updates.get(first))
    quote = 'The NCT00000001 previously estimated 2027 readout timing is withdrawn; no new window is confirmed. Synthetic fixture.'
    cid, _ = intake.save_candidate(value.store, value.target['nct_id'], 'official_announcement_candidate',
        'https://example.org/withdrawn-synthetic-guidance', quote.encode(),
        {'text': quote, 'published_at': '2020-01-02', 'content_type': 'text/plain'},
        hash_scope='source_response_bytes')
    unknown = value.proposal | {'candidate_id': cid, 'quoted_evidence': quote,
        'date_kind': 'readout_time_unconfirmed', 'start': None,
        'end': None, 'precision': 'unknown', 'label': 'Synthetic future readout time unconfirmed'}
    approve(value, unknown)
    update = public.current_readouts(value.store, value.ledger.snapshot())[value.target['id']]
    assert update['precision'] == 'unknown' and update['start'] is update['end'] is None
    assert value.updates.get(first) == original


def test_changed_exact_target_binding_fails_closed(native_calendar):
    value = native_calendar
    ident = approve(value)
    with value.store.connect() as db:
        db.execute('DROP TRIGGER readout_update_proposals_no_update')
        db.execute('DROP TRIGGER readout_update_decisions_no_update')
        row = db.execute('SELECT * FROM readout_update_proposals WHERE id=?', (ident,)).fetchone()
        proposal = value.store.unpack(row['payload'])
        proposal['arena_target_binding']['endpoint'] = 'A different analysis'
        proposal['arena_target_binding_sha256'] = digest(proposal['arena_target_binding'])
        proposal_hash = digest(proposal)
        db.execute('UPDATE readout_update_proposals SET payload=?,sha256=? WHERE id=?',
                   (value.store.pack(proposal), proposal_hash, ident))
        row = db.execute('SELECT * FROM readout_update_decisions WHERE proposal_id=?', (ident,)).fetchone()
        review = value.store.unpack(row['payload']); review['proposal_sha256'] = proposal_hash
        db.execute('UPDATE readout_update_decisions SET payload=?,sha256=? WHERE id=?',
                   (value.store.pack(review), digest(review), row['id']))
    response = value.client.get('/api/arena/public')
    assert response.status_code == 503
    assert 'different analysis' not in response.text


def test_missing_calendar_schema_is_not_created_and_partial_schema_is_rejected(tmp_path):
    store = Store(tmp_path / 'synthetic-empty')
    frozen = {'targets': [], 'cohorts': [], 'runs': []}
    with store.connect() as db:
        before = db.execute('SELECT sql FROM sqlite_master ORDER BY name').fetchall()
    assert public.current_readouts(store, frozen) == {}
    with store.connect() as db:
        assert db.execute('SELECT sql FROM sqlite_master ORDER BY name').fetchall() == before
        db.execute('CREATE TABLE readout_update_proposals(id TEXT)')
    with pytest.raises(ValueError, match='[Ii]ncomplete'):
        public.current_readouts(store, frozen)


def test_no_runtime_directory_is_created_for_public_calendar_read(tmp_path):
    path = tmp_path / 'absent-synthetic-runtime'
    assert public.current_readouts(SimpleNamespace(path=path), {'targets': [], 'runs': [], 'cohorts': []}) == {}
    assert not path.exists()


@pytest.mark.parametrize('changes', [
    {'private_reason': 'internal review text'},
    {'original_prediction_unchanged': False},
    {'source_granularity': 'drug_program'},
    {'source_sha256': 'x' * 64},
    {'proposal_id': 'not-a-persisted-proposal'},
    {'decision_sha256': 'a' * 63},
    {'source_url': 'https://name:secret@example.org/source'},
    {'source_url': '/api/arena/sources/' + 'a' * 64},
    {'source_note': 'sk-' + 'X' * 32},
    {'hash_scope': 'guessed_original_pdf_hash'},
    {'approved_at': '2020-01-01T00:00:00'},
    {'approved_at': '2019-01-01T00:00:00Z'},
    {'precision': 'year', 'start': '2027-02-01'},
    {'end': '2027-02-30'},
    {'precision': 'unknown'},
    {'precision': 'quarter'},
    {'precision': 'day'},
    {'start': None},
])
def test_shared_public_contract_rejects_unsafe_or_inconsistent_updates(native_calendar, changes):
    approve(native_calendar)
    update = public.current_readouts(native_calendar.store, native_calendar.ledger.snapshot())[native_calendar.target['id']]
    with pytest.raises(ValueError) as error:
        validate_current_readout(update | changes)
    assert 'X' * 32 not in str(error.value)


def test_source_range_is_preserved_without_fake_quarter_or_year_precision(native_calendar):
    approve(native_calendar, native_calendar.proposal | {'start': '2027-11-12', 'end': '2028-02-19',
        'precision': 'range', 'label': 'Synthetic explicitly reported cross-year range'})
    update = public.current_readouts(native_calendar.store, native_calendar.ledger.snapshot())[native_calendar.target['id']]
    assert update['precision'] == 'range'
    assert (update['start'], update['end']) == ('2027-11-12', '2028-02-19')
