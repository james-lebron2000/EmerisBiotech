"""Future internal diagnostics stay private; original v1 receipt bytes survive.

All records, receipts and transport/service hooks are isolated fixtures. No
provider, external HTTP, real credentials, session or production state is used.
"""
from copy import deepcopy
from io import BytesIO
import json
from types import SimpleNamespace
import zipfile

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from emeris_live import arena_public as public, arena_public_brief as brief
from emeris_live.arena_attempt_diagnostics import new_diagnostics, sanitize_diagnostics
from emeris_live.arena_store import ArenaLedger
from test_arena_core import spec
from test_public_campaign_export import export
from test_upgrade_arena import archive, server, upgrade


PRIVATE_KEYS = frozenset(('attempts', 'diagnostics', 'http_status', 'failure_stage',
    'content_type_class', 'body_read', 'json_error', 'visible_format',
    'json_mode_requested', 'private_http_detail', 'raw_response', 'usage'))
PRIVATE_CANARIES = ('SYNTHETIC-PRIVATE-HTTP-CANARY',
                    'SYNTHETIC-PRIVATE-FORMAT-CANARY')


def internal_diagnostics(*, http_failure=False):
    value = new_diagnostics(True)
    value.update(http_status=503 if http_failure else 200,
                 content_type_class='html' if http_failure else 'json',
                 failure_stage='http_response' if http_failure else 'forecast_json')
    if not http_failure:
        value['body_read'] = {'state': 'complete', 'observed_bytes': 512}
        value['json_error'] = {'kind': 'syntax_error', 'line': 2, 'column': 3, 'position': 8}
        value['visible_format'] = {'empty': False, 'starts_with_object': False,
                                  'outer_code_fence': True, 'top_level_type': None}
    result = sanitize_diagnostics(value)
    assert result is not None
    return result


def assert_public_only(value):
    if isinstance(value, dict):
        assert not PRIVATE_KEYS.intersection(value)
        for item in value.values():
            assert_public_only(item)
    elif isinstance(value, list):
        for item in value:
            assert_public_only(item)
    encoded = json.dumps(value, ensure_ascii=False)
    assert all(marker not in encoded for marker in PRIVATE_CANARIES)


@pytest.fixture
def diagnostics_runtime(tmp_path, monkeypatch):
    target, slots = spec()
    target.update(trial_name='Synthetic clinical record', drug='Synthetic study drug',
                  scope='prospective_challenge',
                  readout={'label': 'Official result announcement time unconfirmed'})
    for model in ('C', 'D'):
        row = deepcopy(slots[0])
        row.update(model_id=model, requested_model=model+'-fixed')
        slots.append(row)
    directory = tmp_path/'diagnostic-public-runtime'
    ledger = ArenaLedger(directory)
    ids = ledger.register('synthetic-diagnostic-formal', [target], slots,
                          {'season_kind': 'registered_12_project_season'})
    for index, ident in enumerate(ids):
        ledger.start(ident)
        diagnostic = internal_diagnostics(http_failure=index == 3)
        if index < 2:
            diagnostic.update(failure_stage=None, json_error=None)
            diagnostic['visible_format'].update(starts_with_object=True,
                                                outer_code_fence=False,
                                                top_level_type='object')
        ledger.finish(ident, {
            'status': 'predicted' if index == 0 else 'abstain' if index == 1 else 'failed',
            'probability': .7 if index == 0 else None,
            'returned_model': slots[index]['requested_model'],
            'evidence_ids': ['e1'] if index == 0 else [],
            'reasoning': 'Synthetic concise clinical rationale.',
            'error_code': 'invalid_json' if index == 2 else 'provider_http_error' if index == 3 else None,
            'attempts': [{'stage': 'forecast', 'diagnostics': diagnostic,
                          'private_http_detail': PRIVATE_CANARIES[0],
                          'raw_response': PRIVATE_CANARIES[1]}],
            'usage': {'input_tokens': 10, 'output_tokens': 20},
            'cost_amount': None, 'cost_usd': None,
        })
    monkeypatch.setattr(public, 'read_archived', lambda store: ([], [], {}))
    app = FastAPI()
    app.state.store = SimpleNamespace(path=directory)
    app.include_router(public.router)
    return SimpleNamespace(ledger=ledger, target=target, ids=ids, client=TestClient(app))


def test_actual_public_api_json_brief_and_campaign_omit_internal_diagnostics(diagnostics_runtime):
    sample = diagnostics_runtime
    before = sample.ledger.events()
    private = sample.ledger.snapshot()['runs']
    assert all(row['attempts'][0]['diagnostics'] is not None for row in private)
    assert PRIVATE_CANARIES[0] in json.dumps(private)
    base = '/api/arena/public/targets/'+sample.target['id']
    responses = [sample.client.get(path) for path in
                 ('/api/arena/public', base, base+'/record')]
    for response in responses:
        assert response.status_code == 200
        assert_public_only(response.json())
    index, _, record = [response.json() for response in responses]
    assert len(record['runs']) == 4
    assert [row['status'] for row in record['runs']] == ['predicted', 'abstained', 'failed', 'failed']
    assert all(row['probability'] is None and row['confidence'] is None for row in record['runs'])
    assert [row['generated_at_utc'] for row in record['runs']] == [row['generated_at_utc'] for row in private]

    downloaded = sample.client.get(base+'/brief/download')
    assert downloaded.status_code == 200
    with zipfile.ZipFile(BytesIO(downloaded.content)) as archive_file:
        assert json.loads(archive_file.read('clinical-record.json')) == record
        for name in archive_file.namelist():
            value = archive_file.read(name).decode()
            assert all(marker not in value for marker in PRIVATE_CANARIES)
            assert all('"'+key+'"' not in value for key in PRIVATE_KEYS)

    fetch_paths = []
    def fetch(path):
        fetch_paths.append(path)
        response = sample.client.get(path)
        assert response.status_code == 200
        return response.json()
    raw, manifest = export.bundle(index, fetch)
    assert manifest['provider_calls'] == manifest['ledger_writes'] == 0
    assert fetch_paths == [base+'/record']
    with zipfile.ZipFile(BytesIO(raw)) as archive_file:
        assert json.loads(archive_file.read('records/'+sample.target['id']+'.json')) == record
        for name in archive_file.namelist():
            value = archive_file.read(name).decode()
            assert all(marker not in value for marker in PRIVATE_CANARIES)
            assert all('"'+key+'"' not in value for key in PRIVATE_KEYS)
    assert sample.ledger.events() == before


@pytest.mark.parametrize('key,value', [
    ('diagnostics', {'schema_version': 'SYNTHETIC-private'}),
    ('http_status', 503),
    ('visible_format', {'outer_code_fence': True}),
])
def test_public_download_builders_reject_injected_internal_fields(diagnostics_runtime, key, value):
    sample = diagnostics_runtime
    path = '/api/arena/public/targets/'+sample.target['id']+'/record'
    record = sample.client.get(path).json()
    before = sample.ledger.events()
    record['runs'][0][key] = value
    with pytest.raises(ValueError):
        brief.build_brief(record)
    with pytest.raises(ValueError):
        export.validate_record(record, record['target'])
    assert sample.ledger.events() == before


@pytest.mark.parametrize('with_diagnostics', [False, True])
def test_legacy_receipt_reference_reader_preserves_optional_nested_metadata_bytes(tmp_path, with_diagnostics):
    relative = 'arena_receipts/'+'e'*32+'.json'
    path = tmp_path/relative
    path.parent.mkdir()
    attempt = {'stage': 'forecast', 'status': 'failed'}
    if with_diagnostics:
        attempt['diagnostics'] = internal_diagnostics(http_failure=True)
    document = {'schema_version': 'arena-result-receipt-v1', 'run_id': 'SYNTHETIC-receipt-run',
                'result': {'status': 'failed', 'probability': None, 'attempts': [attempt]}}
    # Noncanonical whitespace must survive: this is a byte-bound historical receipt.
    raw = (json.dumps(document, indent=3)+'\n\n').encode()
    path.write_bytes(raw)
    digest = upgrade.digest(raw)
    events = [{'kind': 'run_completed', 'entity_id': document['run_id'],
               'payload': json.dumps({'receipt_path': relative, 'receipt_sha256': digest})}]
    received = upgrade.referenced_receipts(events, tmp_path)
    assert received == {relative: raw}
    assert path.read_bytes() == raw and upgrade.d.sha(path) == digest
    original = json.loads(received[relative])['result']['attempts'][0]
    assert ('diagnostics' in original) is with_diagnostics


def test_actual_upgrade_receipt_identity_branch_preserves_old_and_new_v1_receipts(server, tmp_path):
    """The imported fixture mocks services/network, while real upgrade code runs."""
    old_receipts = {name: (server.data/name).read_bytes() for name in server.receipts}
    old_hashes = {name: upgrade.digest(raw) for name, raw in old_receipts.items()}
    incoming = ArenaLedger(tmp_path/'transport')
    relative = 'arena_receipts/'+'f'*32+'.json'
    ident = 'SYNTHETIC-future-diagnostic-run'
    document = {'schema_version': 'arena-result-receipt-v1', 'run_id': ident,
                'result': {'status': 'failed', 'probability': None,
                           'attempts': [{'diagnostics': internal_diagnostics(http_failure=True)}]}}
    raw = (json.dumps(document, indent=2)+'\n').encode()
    with incoming.connect() as database:
        incoming._append(database, 'run_completed', ident,
                         {'receipt_path': relative, 'receipt_sha256': upgrade.digest(raw)})
    events = upgrade.read_events(incoming.path)
    receipts = {**server.receipts, relative: raw}
    transport = tmp_path/'diagnostic-transport.sqlite3'
    upgrade.d.backup_sqlite(incoming.path, transport)
    args = archive(tmp_path, server.source, receipts, transport.read_bytes(), events)
    upgrade.remote(*args)
    assert upgrade.read_events(server.ledger.path) == events
    assert upgrade.read_events(server.ledger.path)[:len(server.original_events)] == server.original_events
    for name, old_raw in old_receipts.items():
        assert (server.data/name).read_bytes() == old_raw
        assert upgrade.d.sha(server.data/name) == old_hashes[name]
    assert (server.data/relative).read_bytes() == raw
    copied = json.loads((server.data/relative).read_bytes())
    assert copied['schema_version'] == 'arena-result-receipt-v1'
    assert copied['result']['attempts'][0]['diagnostics'] == internal_diagnostics(http_failure=True)
