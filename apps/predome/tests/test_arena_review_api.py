"""Real cookie/SQLite/source review contracts on isolated fixtures, never people."""
from copy import deepcopy
import hashlib
import json
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from emeris_live import arena_review_api as api, intake, main, official_intake
from emeris_live.arena_review_auth import enroll_reviewer
from emeris_live.arena_store import result_target_binding, sha
from test_arena_core import registered, spec


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv('EMERIS_MODE', 'local')
    monkeypatch.setenv('EMERIS_ALLOWED_HOSTS', 'testserver,research.example')
    monkeypatch.setenv('EMERIS_REVIEWERS', '')
    monkeypatch.setenv('EMERIS_ARENA_LAB_USERS', 'jake')
    app = main.create_app(tmp_path / 'isolated-runtime')
    store = app.state.store
    ledger, target, _ = registered(store.path)
    other, slots = spec()
    other.update(id='target-other', nct_id='NCT00000002')
    for slot in slots:
        slot['target_id'] = other['id']
    ledger.register('other-season', [other], slots, {})
    with store.connect() as db:
        for name in ('one', 'two', 'jake'):
            db.execute('INSERT INTO users VALUES(?,?,?)', ('owner-' + name, name, 'unused-isolated-hash'))
            db.execute('INSERT INTO sessions VALUES(?,?,?,?)',
                       (main.digest('token-' + name), 'owner-' + name, 'csrf-' + name, time.time() + 3600))
    grants = {name: enroll_reviewer(ledger, 'owner-' + name, 'person-' + name, [target['id']],
                                  'a' * 64, 'Isolated operator identity reference') for name in ('one', 'two')}
    intake.initialize(store)
    quote = target['nct_id'] + ' prespecified primary analysis: the isolated PFS endpoint was met.'
    raw = ('<html><head><meta property="article:published_time" content="2025-01-01T09:00:00Z"></head>'
           '<body><p>' + quote + '</p></body></html>').encode()
    url = 'https://www.roche.com/media/releases/isolated-outcome'
    # The real source validator also reads tracked trial identity configuration.
    # Keep that input isolated rather than borrowing local frozen dashboard data.
    source_root = tmp_path / 'isolated-source-configuration'
    (source_root / 'dashboard_data').mkdir(parents=True)
    source_config = source_root / 'dashboard_data/evidence_updates.json'
    source_config.write_text(json.dumps({'trials': [{'nct_id': target['nct_id'],
        'trial': 'Isolated trial', 'drug': 'Isolated drug', 'sources': [{'url': url}]}]}))
    monkeypatch.setattr(official_intake, 'ROOT', source_root)
    structured = {**official_intake.extract(raw), 'trial_name': 'Isolated trial',
                  'drug': 'Isolated drug', 'publication_date_basis': 'document_metadata'}
    cid, _ = intake.save_candidate(store, target['nct_id'], 'official_announcement_candidate', url, raw,
                                    structured, hash_scope='source_response_bytes')
    client = TestClient(app)
    client.cookies.set('emeris_session', 'token-one')
    body = {'enrollment_sha256': grants['one']['event_hash'], 'target_binding_sha256': sha(result_target_binding(target)),
            'candidate_id': cid, 'candidate_sha256': hashlib.sha256(raw).hexdigest(), 'label': 'success',
            'first_public_at': '2025-01-01T09:00:00Z', 'source_publication_date': '2025-01-01',
            'source_quote': quote, 'review_note': 'I checked the isolated exact endpoint and original source.',
            'confirmations': {k: True for k in api.CONFIRMATIONS},
            'supersedes_result_sha256': None, 'supersedes_review_sha256': None}
    return SimpleNamespace(app=app, client=client, store=store, ledger=ledger, target=target, other=other,
                           grants=grants, cid=cid, raw=raw, quote=quote, body=body, source_config=source_config,
                           route='/api/arena/review/targets/' + target['id'])


def submit(w, body=None, **headers):
    return w.client.post(w.route + '/reviews', json=w.body if body is None else body,
                         headers={'Origin': 'http://testserver', 'X-CSRF-Token': 'csrf-one', **headers})


def test_enrolled_session_views_only_frozen_question_sources_and_own_pointer(workspace):
    w = workspace
    before = w.ledger.events()
    access = w.client.get('/api/arena/review/access')
    assert access.status_code == 200 and access.json()['csrf'] == 'csrf-one'
    assert access.json()['independent_human_review_verified'] is False
    assert w.client.get('/arena/lab/review').status_code == 200
    assert w.client.get('/static/arena-review.html').status_code == 200
    assert w.client.get('/arena/lab').status_code == 403  # Review enrollment grants no debugging access.
    targets = w.client.get('/api/arena/review/targets').json()['targets']
    assert [t['id'] for t in targets] == [w.target['id']]
    sheet = w.client.get(w.route).json()
    assert sheet['target_binding_sha256'] == sha(result_target_binding(w.target))
    assert sheet['own_review_sha256'] is None and sheet['supersedes_result_sha256'] is None
    assert sheet['candidates'][0]['reviewable'] is True
    assert not any(key in json.dumps(sheet) for key in ('probability', 'reviewer_authentication', 'review_note', 'model_id'))
    inspected = w.client.get(w.route + '/sources/' + w.cid + '/inspect')
    assert inspected.json()['source_publication_date'] == '2025-01-01'
    assert w.quote in inspected.json()['text']
    original = w.client.get(w.route + '/sources/' + w.cid)
    assert original.content == w.raw and 'attachment' in original.headers['content-disposition']
    assert w.ledger.events() == before


def test_anonymous_and_unenrolled_debug_user_cannot_review(workspace):
    w = workspace
    w.client.cookies.clear()
    for path in ('/api/arena/review/access', w.route, '/arena/lab/review', '/static/arena-review.html'):
        assert w.client.get(path).status_code == 401
    page=w.client.get('/arena/lab/review')
    assert page.headers['content-type'].startswith('text/html') and 'https://testserver/data' in page.text
    w.client.cookies.set('emeris_session', 'token-jake')
    assert w.client.get('/api/arena/review/access').status_code == 403
    assert w.client.get('/arena/lab/review').status_code == 403
    assert submit(w, **{'X-CSRF-Token': 'csrf-jake'}).status_code == 403


def test_review_scope_is_checked_before_source_and_peer_target_reads(workspace):
    w = workspace
    forbidden = '/api/arena/review/targets/' + w.other['id']
    assert w.client.get(forbidden).status_code == 403
    assert w.client.get(forbidden + '/sources/' + w.cid).status_code == 403
    assert w.client.get(forbidden + '/sources/' + w.cid + '/inspect').status_code == 403
    assert w.client.get(w.route + '/sources/not-a-candidate').status_code == 404


@pytest.mark.parametrize('changes', [{'account_id': 'owner-two'}, {'reviewer_id': 'forged'},
    {'principal_id': 'person-two'}, {'reviewer_kind': 'human'}, {'calibrated': True},
    {'label': 'not-a-label'}, {'first_public_at': '2025-01-01T09:00:00'},
    {'first_public_at': '2099-01-01T00:00:00Z'}, {'first_public_at':'2025-01-01T23:59:00Z'}, {'candidate_sha256': 'b' * 64},
    {'target_binding_sha256': 'b' * 64}, {'source_quote': 'An invented long excerpt absent from the original bytes.'}])
def test_client_identity_injection_bad_time_or_source_never_appends(workspace, changes):
    w = workspace
    before = w.ledger.events()
    response = submit(w, {**w.body, **changes})
    assert response.status_code in (409, 422), response.text
    assert w.ledger.events() == before


def test_csrf_origin_and_confirmation_fail_closed(workspace):
    w = workspace
    before = w.ledger.events()
    assert submit(w, **{'X-CSRF-Token': 'wrong'}).status_code == 403
    assert submit(w, **{'Origin': 'https://unrelated.example'}).status_code == 403
    for value in (False, 'true'):
        body = deepcopy(w.body)
        body['confirmations']['source_inspected'] = value
        assert submit(w, body).status_code == 422
    assert w.ledger.events() == before


def test_missing_official_configuration_still_fails_closed(workspace):
    w = workspace
    before = w.ledger.events()
    w.source_config.unlink()
    assert submit(w).status_code == 422
    assert w.ledger.events() == before


def test_two_session_owners_record_distinct_source_notes_without_public_identity(workspace):
    w = workspace
    response = submit(w)
    assert response.status_code == 200, response.text
    assert response.json()['status'] == 'announced_pending_adjudication'
    assert submit(w).status_code == 409
    own = w.client.get(w.route).json()['own_review_sha256']
    assert own is not None
    w.client.cookies.set('emeris_session', 'token-two')
    sheet = w.client.get(w.route).json()
    assert sheet['own_review_sha256'] is None and 'review_note' not in json.dumps(sheet)
    body = {**w.body, 'enrollment_sha256': w.grants['two']['event_hash'],
            'review_note': 'A second isolated source reader checked the same source facts.'}
    response = submit(w, body, **{'X-CSRF-Token': 'csrf-two'})
    assert response.status_code == 200 and response.json()['status'] == 'adjudicated', response.text
    assert response.json()['independent_human_review_verified'] is False
    votes = [e['payload'] for e in w.ledger.events() if e['kind'] == 'result_review']
    assert votes[0]['result_sha256'] == votes[1]['result_sha256']
    assert votes[0]['review_context']['source_publication_date'] == '2025-01-01'
    assert votes[0]['reviewer_authentication']['account_id'] == 'owner-one'
    from emeris_live.arena_public import result_view
    public = result_view(w.ledger.snapshot()['targets'][0])
    assert 'Session-authenticated' in public['verification_note']
    assert not any(v in json.dumps(public) for v in ('owner-one', 'person-one', w.quote))


def test_account_deletion_retains_immutable_review_and_session(workspace):
    w = workspace
    assert submit(w).status_code == 200
    before = w.ledger.events()
    response = w.client.delete('/api/account', headers={'Origin': 'http://testserver', 'X-CSRF-Token': 'csrf-one'})
    assert response.status_code == 409
    assert w.client.get('/api/arena/review/access').status_code == 200
    assert w.ledger.events() == before


def test_logout_during_source_parse_is_rechecked_before_append(workspace, monkeypatch):
    w = workspace
    before = w.ledger.events()
    verify = api.official_outcome_source
    def during_parse(*args, **kwargs):
        checked = verify(*args, **kwargs)
        with w.store.connect() as db:
            db.execute('DELETE FROM sessions WHERE owner=?', ('owner-one',))
        return checked
    monkeypatch.setattr(api, 'official_outcome_source', during_parse)
    assert submit(w).status_code == 401
    assert w.ledger.events() == before


def test_revocation_during_parse_blocks_future_write_but_keeps_history(workspace, monkeypatch):
    w = workspace
    verify = api.official_outcome_source
    def during_parse(*args, **kwargs):
        checked = verify(*args, **kwargs)
        enroll_reviewer(w.ledger, 'owner-one', 'person-one', [], 'a' * 64,
                        'Revoke isolated access during parsing', active=False, supersedes=w.grants['one']['event_hash'])
        return checked
    monkeypatch.setattr(api, 'official_outcome_source', during_parse)
    assert submit(w).status_code == 403
    assert not any(e['kind'] == 'result_review' for e in w.ledger.events())


def test_registry_without_posted_results_is_not_a_result(workspace):
    w = workspace
    value = {'hasResults': False, 'protocolSection': {'identificationModule': {'nctId': w.target['nct_id']}}}
    raw = json.dumps(value).encode()
    cid, _ = intake.save_candidate(w.store, w.target['nct_id'], 'registry_update',
                                  'https://clinicaltrials.gov/api/v2/studies/' + w.target['nct_id'], raw, intake.registry_summary(value))
    candidate = next(c for c in w.client.get(w.route).json()['candidates'] if c['id'] == cid)
    assert candidate['reviewable'] is False
    before = w.ledger.events()
    assert w.client.get(w.route + '/sources/' + cid + '/inspect').status_code == 422
    body = {**w.body, 'candidate_id': cid, 'candidate_sha256': hashlib.sha256(raw).hexdigest()}
    assert submit(w, body).status_code == 422
    assert w.ledger.events() == before


def test_original_registry_results_are_inspected_and_quote_bound(workspace):
    w = workspace
    value = {'hasResults': True, 'protocolSection': {'identificationModule': {'nctId': w.target['nct_id']},
             'statusModule': {'resultsFirstPostDateStruct': {'date': '2025-01-01', 'type': 'ACTUAL'}}},
             'resultsSection': {'outcomeMeasuresModule': {'outcomeMeasures': [{'type': 'PRIMARY', 'title': w.target['endpoint'],
               'analyses': [{'paramType': 'Hazard Ratio', 'paramValue': '0.70', 'pValue': '0.001'}]}]}}}
    raw = json.dumps(value).encode()
    cid, _ = intake.save_candidate(w.store, w.target['nct_id'], 'reported_results_candidate',
        'https://clinicaltrials.gov/api/v2/studies/' + w.target['nct_id'], raw, intake.registry_summary(value))
    document = w.client.get(w.route + '/sources/' + cid + '/inspect').json()
    assert document['publication_date_basis'] == 'registry_results_post_date'
    body = {**w.body, 'candidate_id': cid, 'candidate_sha256': hashlib.sha256(raw).hexdigest(), 'source_quote': document['text']}
    assert submit(w, body).status_code == 200


def test_only_the_review_post_accepts_configured_internal_origin(workspace, monkeypatch):
    w = workspace
    monkeypatch.setenv('EMERIS_MODE', 'public')
    monkeypatch.setenv('EMERIS_PUBLIC_ORIGIN', 'https://research.example')
    app = main.create_app(w.store.path)
    client = TestClient(app, base_url='https://research.example:18443')
    client.cookies.set('emeris_session', 'token-one')
    headers = {'Origin': 'https://research.example:18443', 'X-CSRF-Token': 'csrf-one'}
    response = client.post(w.route + '/reviews', json=w.body, headers=headers)
    assert response.status_code == 200, response.text
    for path in ('/api/logout', '/api/auth/register', '/api/research/execute'):
        assert client.post(path, json={}, headers=headers).status_code == 403
    assert client.post(w.route + '/reviews', json=w.body, headers={**headers, 'Origin': 'https://research.example:18444'}).status_code == 403


def test_pdf_parser_receives_no_service_credentials(monkeypatch):
    seen = []
    def execute(*args, **kwargs):
        seen.append(kwargs['env'])
        return SimpleNamespace(stdout=json.dumps({'text': 'Bounded original PDF extraction', 'title': 'Fixture'}).encode())
    monkeypatch.setenv('APIKEY', 'isolated-not-a-key')
    monkeypatch.setenv('EPO_OPS_SECRET', 'isolated-not-a-secret')
    monkeypatch.setattr(official_intake.subprocess, 'run', execute)
    assert official_intake.extract(b'%PDF-isolated')['text']
    assert not any(k in seen[0] for k in ('APIKEY', 'EPO_OPS_SECRET', 'PYTHONPATH'))
