"""Clinical Brief uses the actual public producer and isolated native/import ledgers."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
from io import BytesIO
import json
from types import SimpleNamespace
import zipfile

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from emeris_live import arena_public as public, arena_public_brief as brief
from emeris_live.arena_store import ArenaLedger, result_target_binding
from arena_scientific_fixture import scientifically_accept
from test_arena_core import finish, spec
from test_arena_import_public import integrated
from test_arena_imports import bundle_factory, signed


class Document(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.tags, self.text, self.links = [], [], []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.tags.append((tag, attrs))
        if tag == 'a':
            self.links.append(attrs.get('href'))

    def handle_data(self, data):
        self.text.append(data)


@pytest.fixture
def native(tmp_path, monkeypatch):
    root = tmp_path / 'native-runtime'
    ledger = ArenaLedger(root)
    target, slots = spec()
    target.update(scope='prospective_challenge', trial_name='Synthetic clinical question',
                  drug='Synthetic study drug', readout={'label': 'Official readout time unconfirmed'})
    ids = ledger.register('brief-native-formal', [target], slots,
                          {'season_kind': 'registered_12_project_season'})
    finish(ledger, ids[0], .7, returned_model='A-fixed')
    ledger.start(ids[1])
    ledger.finish(ids[1], {'status': 'failed', 'probability': None, 'error_code': 'invalid_json'})
    monkeypatch.setattr(public, 'read_archived', lambda store: ([], [], {}))
    app = FastAPI()
    app.state.store = SimpleNamespace(path=root)
    app.include_router(public.router)
    return SimpleNamespace(ledger=ledger, target=target, ids=ids, client=TestClient(app))


def unpack(response):
    assert response.status_code == 200, response.text
    assert response.headers['content-type'] == 'application/zip'
    assert 'attachment;' in response.headers['content-disposition']
    assert response.headers['cache-control'] == 'no-store'
    with zipfile.ZipFile(BytesIO(response.content)) as bundle:
        assert set(bundle.namelist()) == {'clinical-brief.html', 'clinical-brief.md', 'clinical-record.json', 'manifest.json'}
        files = {name: bundle.read(name) for name in bundle.namelist()}
    manifest = json.loads(files['manifest.json'])
    assert set(manifest['files']) == set(files) - {'manifest.json'}
    for name, value in manifest['files'].items():
        assert value == {'sha256': hashlib.sha256(files[name]).hexdigest(), 'bytes': len(files[name])}
    assert manifest['schema_version'] == brief.SCHEMA_VERSION
    assert manifest['provider_calls'] == manifest['ledger_writes'] == 0
    assert manifest['independent_timestamp_attestation'] is False
    assert manifest['clinical_prediction_validated'] is False
    return files, manifest


def test_native_api_brief_uses_one_public_snapshot_preserves_json_times_and_failure(native, monkeypatch):
    calls = []
    original = public.overview
    def observed(request):
        calls.append(True)
        return original(request)
    monkeypatch.setattr(public, 'overview', observed)
    before = native.ledger.events()
    base = '/api/arena/public/targets/' + native.target['id']
    files, manifest = unpack(native.client.get(base + '/brief/download'))
    assert calls == [True]
    record = native.client.get(base + '/record').json()
    assert files['clinical-record.json'] == brief.canonical(record)
    assert manifest['record_kind'] == 'formal_season'
    assert manifest['comparison_scope'] == 'same_evidence_clinical_only'
    assert manifest['assigned_records'] == 2
    assert {r['status'] for r in record['runs']} == {'predicted', 'failed'}
    assert all(r['probability'] is None and r['confidence'] is None for r in record['runs'])
    original_times = {r['id']: r['locked_at_utc'] for r in record['runs']}
    assert {r['id']: r['locked_at_utc'] for r in manifest['original_model_times']} == original_times
    assert all(time < manifest['exported_at_utc'] for time in original_times.values())
    assert manifest['export_time_scope'] == 'public_brief_export_only'
    report = files['clinical-brief.md'].decode()
    assert 'Formal season clinical question' in report and 'Awaiting results' in report
    assert 'failed' in report and 'Not yet evaluable' in report and 'Uncertain' in report
    assert 'Direction counts: Success 1 · Failure 0 · Uncertain 1' in report
    assert 'does not independently prove those earlier times' in report
    doc = Document(files['clinical-brief.html'].decode())
    assert sum(tag == 'section' for tag, _ in doc.tags) == 3
    assert not any(tag in ('script', 'img', 'iframe', 'form', 'link', 'video', 'audio') for tag, _ in doc.tags)
    assert not any(key.startswith('on') for _, attrs in doc.tags for key in attrs)
    assert all(url.startswith('https://') for url in doc.links)
    assert any(tag == 'meta' and attrs.get('http-equiv') == 'Content-Security-Policy' for tag, attrs in doc.tags)
    assert native.ledger.events() == before


def test_imported_pilot_brief_is_public_two_controls_and_not_the_four_slot_internal_record(integrated):
    sample, client = integrated.sample, integrated.client
    base = '/api/arena/public/targets/' + sample['target_id']
    files, manifest = unpack(client.get(base + '/brief/download'))
    record = json.loads(files['clinical-record.json'])
    assert record == client.get(base + '/record').json()
    assert manifest['record_kind'] == 'independent_research_pilot'
    assert manifest['assigned_records'] == len(record['runs']) == 2
    assert all(r['track'] == 'same_evidence' and r['condition'] == 'clinical_only' for r in record['runs'])
    report = files['clinical-brief.md'].decode()
    assert 'Independent research pilot' in report and 'Separate from the formal season' in report
    assert 'Randomized · Parallel group · Double masked' in report
    assert 'Current formal clinical question' not in report
    for raw in files.values():
        text = raw.decode()
        for private in ('source_events', 'record_origin', 'provider_config_snapshot', 'source_path',
                        'openai_compatible', 'expected_prompt_sha256', 'token_usage', 'sync_arena_records'):
            assert private not in text
    assert integrated.ledger.events() == integrated.before


@pytest.mark.parametrize('label,accepted', [('success', False), ('success', True), ('mixed', True), ('inconclusive', True)])
def test_real_outcome_review_states_preserve_nonbinary_and_unaccepted_comparisons(native, label, accepted):
    result = {'target_binding': result_target_binding(native.target), 'label': label,
        'source_url': 'https://example.org/isolated-result', 'source_sha256': 'b' * 64,
        'first_public_at': datetime.now(timezone.utc).isoformat(), 'endpoint_match_note': 'Synthetic exact primary target result'}
    for reviewer in ('synthetic-ai-one', 'synthetic-ai-two'):
        native.ledger.record_result(native.target['id'], result, reviewer_id=reviewer, reviewer_kind='ai',
                                    review_note='Synthetic source inspection, not a real human review.')
    if accepted:
        scientifically_accept(native.ledger, native.target)
    before = native.ledger.events()
    files, _ = unpack(native.client.get('/api/arena/public/targets/' + native.target['id'] + '/brief/download'))
    record = json.loads(files['clinical-record.json'])
    report = files['clinical-brief.md'].decode()
    if not accepted:
        assert 'Reported result awaiting independent scientific review' in report
        assert record['result']['label'] is None and all(r['match'] is None for r in record['runs'])
    else:
        assert 'Actual outcome: ' + label in report
        if label in ('mixed', 'inconclusive'):
            assert all(r['match'] is None for r in record['runs'])
            assert 'do not produce a right-or-wrong score' in report
        else:
            assert next(r for r in record['runs'] if r['status'] == 'predicted')['match'] is True
            assert '| Matched |' in report
    assert native.ledger.events() == before


def test_historical_scope_is_not_relabelled_as_formal_or_prospective(native):
    record = native.client.get('/api/arena/public/targets/' + native.target['id'] + '/record').json()
    record['target'].update(record_kind='historical_replay', scope='archived_research')
    for run in record['runs']:
        run.update(scope='archived_research', track='archived', match=None, match_status='historical_replay')
    raw, manifest = brief.build_brief(record, cohort_kind='registered_12_project_season')
    with zipfile.ZipFile(BytesIO(raw)) as bundle:
        report = bundle.read('clinical-brief.md').decode()
    assert manifest['record_kind'] == 'historical_replay'
    assert manifest['comparison_scope'] == 'archived_historical_clinical_only'
    assert 'Historical replay / archived research record' in report
    assert 'Historical record; no prospective score' in report
    assert 'Formal season clinical question' not in report


def test_actual_registration_withdrawn_before_calls_is_not_a_current_formal_brief(native):
    target, slots = spec()
    target.update(id='withdrawn-brief-target', scope='prospective_challenge', trial_name='Withdrawn synthetic question')
    for slot in slots:
        slot['target_id'] = target['id']
    native.ledger.register('withdrawn-brief-cohort', [target], slots,
                           {'season_kind': 'registered_12_project_season'})
    native.ledger.withdraw_unstarted_cohort('withdrawn-brief-cohort', 'Synthetic source correction before any model execution')
    before = native.ledger.events()
    files, manifest = unpack(native.client.get('/api/arena/public/targets/' + target['id'] + '/brief/download'))
    assert manifest['record_kind'] == 'withdrawn_registration'
    record = json.loads(files['clinical-record.json'])
    assert record['target']['execution_status'] == 'withdrawn_before_execution'
    assert all(r['status'] == 'withdrawn' and r['generated_at_utc'] is None for r in record['runs'])
    report = files['clinical-brief.md'].decode()
    assert 'Preserved withdrawn registration' in report
    assert 'no prediction was generated' in report
    assert 'Formal season clinical question' not in report
    assert native.ledger.events() == before


def test_untrusted_clinical_text_is_escaped_in_offline_html_and_markdown(native):
    record = native.client.get('/api/arena/public/targets/' + native.target['id'] + '/record').json()
    record['target']['trial_name'] = '<img src=x onerror=alert(1)> [bad](javascript:bad)'
    raw, _ = brief.build_brief(record)
    with zipfile.ZipFile(BytesIO(raw)) as bundle:
        html = bundle.read('clinical-brief.html').decode()
        markdown = bundle.read('clinical-brief.md').decode()
    document = Document(html)
    assert not any(tag in ('script', 'img') for tag, _ in document.tags)
    assert '&lt;img' in html and '&lt;img' in markdown
    assert '\\[bad\\]' in markdown and '\\(javascript:bad\\)' in markdown


@pytest.mark.parametrize('fault', ['internal_field', 'nested_internal', 'probability', 'patent_arm', 'premature_hit', 'unsafe_source'])
def test_brief_export_boundary_rejects_nonpublic_or_incomparable_objects(native, fault):
    record = native.client.get('/api/arena/public/targets/' + native.target['id'] + '/record').json()
    if fault == 'internal_field':
        record['runs'][0]['reasoning'] = 'Internal reasoning must not be exported'
    elif fault == 'nested_internal':
        record['target']['trial_name'] = {'private_internal_field': 'Not a public scalar'}
    elif fault == 'probability':
        record['runs'][0]['probability'] = .78
    elif fault == 'patent_arm':
        record['runs'][0]['condition'] = 'with_patents'
    elif fault == 'premature_hit':
        record['runs'][0].update(match=True, match_status='matched')
    else:
        record['sources'][0]['source_url'] = 'https://example.org/source?api_key=private-value'
    with pytest.raises(ValueError):
        brief.build_brief(record)


def test_missing_target_and_unavailable_records_keep_clear_http_states(native, monkeypatch):
    assert native.client.get('/api/arena/public/targets/missing/brief/download').status_code == 404
    def unavailable(store):
        raise ValueError('Isolated source failure')
    monkeypatch.setattr(public, 'read_frozen', unavailable)
    response = native.client.get('/api/arena/public/targets/' + native.target['id'] + '/brief/download')
    assert response.status_code == 503
    assert 'application/zip' not in response.headers['content-type']
