"""Real sealed test bundles through public and authenticated application routes.

The authority, forecasts and sources are synthetic fixtures. Original source
events, signatures, private storage, sessions and public projections are real
implementations; these checks establish no clinical prediction performance.
"""
from io import BytesIO
import json
import time
from types import SimpleNamespace
import zipfile

import pytest
from fastapi.testclient import TestClient

from emeris_live import arena_api, arena_lab, arena_public, main
from emeris_live.arena_store import ArenaLedger
from test_arena_core import spec
from test_arena_imports import bundle_factory, signed


@pytest.fixture
def integrated(tmp_path, monkeypatch, bundle_factory):
    monkeypatch.setenv('EMERIS_MODE', 'local')
    monkeypatch.setenv('EMERIS_ALLOWED_HOSTS', 'testserver')
    monkeypatch.setenv('EMERIS_REVIEWERS', '')
    monkeypatch.setenv('EMERIS_ARENA_LAB_USERS', 'Jake')
    monkeypatch.setattr(arena_api, 'archived_views', lambda store: ([], [], {}))
    monkeypatch.setattr(arena_api, 'read_catalog', lambda: {'items': [], 'connectors': []})
    monkeypatch.setattr(arena_api, 'list_models', lambda: [
        {'id': name, 'available': False} for name in ('glm-5.2', 'kimi-k3')])
    monkeypatch.setattr(arena_public, 'read_archived', lambda store: ([], [], {}))
    app = main.create_app(tmp_path / 'host-runtime')
    ledger = ArenaLedger(app.state.store.path)
    formal, slots = spec()
    formal.update(scope='prospective_challenge', trial_name='Existing formal trial')
    ledger.register('existing-formal-season', [formal], slots,
                    {'season_kind': 'registered_12_project_season'})
    sample = bundle_factory(model_ids=('glm-5.2', 'kimi-k3'), include_patents=True,
                            cohort_id='independent-pair-test',
                            statuses={('kimi-k3', 'clinical_only'): 'failed'})
    receipt = ledger.import_sealed_cohort(sample['path'], sample['payload']['bundle_sha256'])
    return SimpleNamespace(client=TestClient(app), app=app, ledger=ledger, sample=sample,
                           receipt=receipt, formal=formal, before=ledger.events())


def login(context, name):
    token, owner = 'isolated-session-' + name, 'isolated-owner-' + name
    with context.app.state.store.connect() as db:
        db.execute('INSERT INTO users VALUES(?,?,?)', (owner, name.lower(), 'unused-fixture-hash'))
        db.execute('INSERT INTO sessions VALUES(?,?,?,?)',
                   (main.digest(token), owner, 'isolated-csrf', time.time() + 3600))
    context.client.cookies.set('emeris_session', token)


def test_imported_public_record_keeps_two_controls_original_time_and_formal_scope(integrated):
    c, sample = integrated.client, integrated.sample
    overview = c.get('/api/arena/public')
    assert overview.status_code == 200
    value = overview.json()
    assert value['version'] == 'arena-clinical-public-1.4'
    assert value['current_cohort_id'] == 'existing-formal-season'
    pilot = next(v for v in value['cohorts'] if v['id'] == sample['payload']['source_cohort_id'])
    assert pilot['kind'] == 'registered_patent_pair_pilot'
    response = c.get('/api/arena/public/targets/' + sample['target_id'])
    assert response.status_code == 200
    record = response.json()
    assert len(record['runs']) == 2
    assert all(r['track'] == 'same_evidence' and r['condition'] == 'clinical_only' for r in record['runs'])
    assert {r['model_id'] for r in record['runs']} == {'glm-5.2', 'kimi-k3'}
    assert {r['status'] for r in record['runs']} == {'predicted', 'failed'}
    assert all(r['probability'] is None and r['confidence'] is None and r['match'] is None
               and r['match_status'] == 'not_evaluable' for r in record['runs'])
    assert record['consensus']['assigned'] == 2
    assert record['consensus']['counts'] == {'Success': 1, 'Failure': 0, 'Uncertain': 1}
    assert record['consensus']['predicted_outcome'] == 'Uncertain'
    assert record['target']['status'] == 'pending' and record['result'] is None
    original = {e['entity_id']: e for e in sample['events'] if e['kind'] == 'run_completed'}
    for row in record['runs']:
        assert row['generated_at_utc'] == row['locked_at_utc'] == original[row['id']]['created_at']
        assert row['generated_at_utc'] < integrated.receipt['synced_at_utc']
    registered = next(e for e in sample['events'] if e['kind'] == 'target_registered')
    assert record['target']['registered_at'] == registered['created_at']
    assert c.get('/api/arena/public/targets/' + sample['target_id'] + '/record').json() == record
    serialized = json.dumps(record)
    for internal in ('record_origin', 'provider_config_snapshot', 'source_path', 'source_events',
                     'openai_compatible', 'manifest.json', 'synced_at_utc', 'token_usage'):
        assert internal not in serialized
    assert integrated.ledger.events() == integrated.before


def test_imported_sources_are_real_public_hash_bound_assets_and_supply_registry_fields(integrated):
    sample, c = integrated.sample, integrated.client
    record = c.get('/api/arena/public/targets/' + sample['target_id']).json()
    target = record['target']
    assert target['phase'] == 'PHASE3'
    assert target['design']['allocation'] == 'RANDOMIZED'
    assert target['design']['masking'] == 'DOUBLE'
    assert target['registry_status'] == 'ACTIVE_NOT_RECRUITING'
    asset = sample['payload']['assets'][0]
    source = next(s for s in record['sources'] if s['kind'] == 'trial_registry')
    assert source['source_snapshot_url'] == '/api/arena/sources/' + asset['sha256']
    response = c.get(source['source_snapshot_url'])
    assert response.status_code == 200 and response.content == sample['asset_raw']
    assert response.headers['content-type'] == 'application/octet-stream'
    assert response.headers['x-content-type-options'] == 'nosniff'
    assert 'attachment;' in response.headers['content-disposition']
    assert c.get('/api/arena/sources/' + '0' * 64).status_code == 404
    # A valid registry source for another NCT cannot fill this trial's facts.
    wrong = {**target, 'nct_id': 'NCT00000000'}
    assert arena_public.registry_fields(wrong, [{'kind': 'trial_registry', 'source_sha256': asset['sha256']}],
                                        {asset['sha256']: sample['asset_raw']}) == {
                                            'phase': None, 'design': None, 'registry_status': None}
    assert integrated.ledger.events() == integrated.before


def test_imported_internal_routes_require_existing_team_session(integrated):
    c, sample = integrated.client, integrated.sample
    prefix = '/api/arena/lab/imports/' + sample['payload']['bundle_sha256']
    routes = ['/api/arena', '/api/arena/ledger',
              '/api/arena/targets/' + sample['target_id'],
              '/api/arena/targets/' + sample['target_id'] + '/report',
              '/api/arena/runs/' + sample['run_ids'][0] + '/packet',
              prefix + '/proofs/registration/download', prefix + '/proofs/predictions/download']
    for route in routes:
        assert c.get(route).status_code == 401, route
    login(integrated, 'ordinary')
    for route in routes:
        assert c.get(route).status_code == 403, route
    login(integrated, 'Jake')
    overview = c.get('/api/arena')
    assert overview.status_code == 200
    assert overview.json()['metrics']['cohort_id'] == 'existing-formal-season'
    internal = c.get('/api/arena/targets/' + sample['target_id'])
    assert internal.status_code == 200 and len(internal.json()['runs']) == 4
    runs = internal.json()['runs']
    assert len([r for r in runs if r['condition'] == 'with_patents']) == 2
    assert len([r for r in runs if r['condition'] == 'clinical_only']) == 2
    assert all(s['source_snapshot_url'] == '/api/arena/sources/' + s['source_sha256']
               for s in internal.json()['evidence'])
    assert all(r['record_origin']['synced_at_utc'] == integrated.receipt['synced_at_utc'] for r in runs)
    assert all(r['timestamp_proof']['url'] == prefix + '/proofs/predictions/download' for r in runs)
    assert all(r['record_origin']['proof_downloads']['registration'] == prefix + '/proofs/registration/download'
               for r in runs)
    assert sum(len(r.get('citation_support_reviews', [])) for r in runs) == 3
    assert all(v['reviewer_kind'] == 'ai' for r in runs for v in r.get('citation_support_reviews', []))
    assert all(r['record_origin']['prediction_proof']['generated_at_utc'] < integrated.receipt['synced_at_utc']
               for r in runs)
    assert c.get('/api/arena/ledger').json()['events'] == integrated.before
    assert c.get('/api/arena/runs/' + sample['run_ids'][0] + '/packet').status_code == 200
    assert c.get(prefix + '/proofs/not-a-purpose/download').status_code == 404
    assert integrated.ledger.events() == integrated.before


@pytest.mark.parametrize('purpose', ['registration', 'predictions'])
def test_private_proof_download_preserves_all_signed_bytes_and_adds_separate_import_receipt(integrated, purpose):
    c, sample = integrated.client, integrated.sample
    login(integrated, 'Jake')
    route = '/api/arena/lab/imports/' + sample['payload']['bundle_sha256'] + '/proofs/' + purpose + '/download'
    response = c.get(route)
    assert response.status_code == 200 and response.headers['content-type'] == 'application/zip'
    assert response.headers['cache-control'] == 'no-store'
    with zipfile.ZipFile(BytesIO(response.content)) as bundle:
        names = {'manifest.json', 'manifest.tsq', 'manifest.tsr', 'tsa.crt', 'cacert.pem', 'verification.txt', 'receipt.json'}
        assert set(bundle.namelist()) == names | {'import-receipt.json'}
        for name in names:
            assert bundle.read(name) == sample['files']['proofs/' + purpose + '/' + name]
        receipt = json.loads(bundle.read('import-receipt.json'))
    assert receipt['host_event_sha256'] == integrated.receipt['host_import_event_sha256']
    assert receipt['synced_at_utc'] == integrated.receipt['synced_at_utc']
    assert receipt['source_ledger_sha256'] == sample['payload']['source_ledger_sha256']
    assert receipt['original_proof'] == sample['payload']['proofs'][purpose]
    assert receipt['original_proof']['generated_at_utc'] != receipt['synced_at_utc']
    assert c.get('/api/arena/attestations/' + purpose + '-source/download').status_code == 404
    assert integrated.ledger.events() == integrated.before


def test_tampered_staged_original_is_not_downloaded_or_silently_used(integrated):
    sample, c = integrated.sample, integrated.client
    archive = integrated.app.state.store.path / 'arena_imports' / (sample['payload']['bundle_sha256'] + '.tar.gz')
    archive.write_bytes(b'tampered isolated import archive')
    source = sample['payload']['assets'][0]['sha256']
    assert c.get('/api/arena/sources/' + source).status_code == 404
    assert c.get('/api/arena/public/targets/' + sample['target_id']).status_code == 503
    login(integrated, 'Jake')
    assert c.get('/api/arena/lab/imports/' + sample['payload']['bundle_sha256']
                 + '/proofs/predictions/download').status_code == 404
    assert integrated.ledger.events() == integrated.before
