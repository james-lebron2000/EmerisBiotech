"""Arena workspace access uses isolated cookie/allowlist fixtures; no providers."""
import time

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from emeris_live import arena_api, arena_lab, main, platform_api


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv('EMERIS_MODE', 'local')
    monkeypatch.setenv('EMERIS_ALLOWED_HOSTS', 'testserver')
    monkeypatch.delenv('EMERIS_PUBLIC_ORIGIN', raising=False)
    monkeypatch.setenv('EMERIS_REVIEWERS', '')
    monkeypatch.setenv('EMERIS_ARENA_LAB_USERS', '  Jake , ')
    source = tmp_path / 'isolated-source'
    static = source / 'static'
    static.mkdir(parents=True)
    for filename, text in {
        'arena-public.html': '<main>Public clinical comparison fixture</main>',
        'arena-lab.html': '<main>Reviewer-only internal fixture</main>',
        'index.html': '<main>Existing login fixture</main>',
        'dashboard.html': '<main>Original calendar fixture</main>',
        'arena.css': 'body { color: black; }',
    }.items():
        (static / filename).write_text(text)
    monkeypatch.setattr(main, 'ROOT', source)
    monkeypatch.setattr(arena_lab, 'ROOT', source)
    calls = []
    def overview(request, **kwargs):
        calls.append(('overview', kwargs))
        return {'scope': 'existing-public-audit-view', 'models': [], 'runs': [], **kwargs}
    def target_detail(target_id, request):
        calls.append(('target', target_id))
        if target_id == 'missing':
            raise HTTPException(404, 'Fixture target missing')
        return {'target': {'id': target_id}, 'runs': [], 'evidence': []}
    monkeypatch.setattr(arena_api, 'overview', overview)
    monkeypatch.setattr(arena_api, 'target_detail', target_detail)
    app = main.create_app(tmp_path / 'isolated-runtime')
    client = TestClient(app)
    def login(name='jake', expires=None):
        # These fixtures exercise the existing digest(cookie) session lookup,
        # without inventing another token/auth endpoint or reading real accounts.
        token = 'isolated-session-for-' + name
        owner = 'isolated-owner-' + name
        with app.state.store.connect() as db:
            db.execute('INSERT INTO users VALUES(?,?,?)', (owner, name, 'unused-fixture-password-hash'))
            db.execute('INSERT INTO sessions VALUES(?,?,?,?)',
                       (main.digest(token), owner, 'isolated-csrf', expires or time.time() + 3600))
        client.cookies.set('emeris_session', token)
        return token
    return client, app, calls, login


def test_public_arena_uses_clinical_page_without_internal_access(workspace):
    client, _, calls, _ = workspace
    response = client.get('/arena')
    assert response.status_code == 200
    assert 'Public clinical comparison fixture' in response.text
    assert 'Reviewer-only internal fixture' not in response.text
    assert calls == []
    assert client.get('/data').status_code == 200


@pytest.mark.parametrize('url', ['/arena/lab', '/static/arena-lab.html', '/arena/lab/calendar'])
def test_anonymous_lab_entry_is_safe_login_shell(workspace, url):
    client, _, calls, _ = workspace
    response = client.get(url)
    assert response.status_code == 401
    assert response.headers['content-type'].startswith('text/html')
    assert '登录内部研究工作台' in response.text
    assert 'href="/data"' in response.text and 'href="/arena"' in response.text
    assert '网页不会触发付费调用' in response.text
    assert '公开预测、来源与审计下载仍保持公开' in response.text
    assert 'Reviewer-only internal fixture' not in response.text
    assert '没有账号可在 /data 注册后登录' in response.text
    assert 'jake' not in response.text.casefold()
    assert calls == []
    assert response.headers['cache-control'] == 'no-store'
    assert "script-src 'self'" in response.headers['content-security-policy']


@pytest.mark.parametrize('url', ['/api/arena/lab/overview', '/api/arena/lab/targets/target-one', '/api/arena/lab/access'])
def test_anonymous_lab_api_is_401_without_reading_records(workspace, url):
    client, _, calls, _ = workspace
    response = client.get(url)
    assert response.status_code == 401
    assert response.json() == {'detail': '请先登录。'}
    assert calls == []


def test_signed_in_nonreviewer_is_403_for_page_and_apis(workspace):
    client, _, calls, login = workspace
    login('ordinary-user')
    for url in ('/arena/lab', '/static/arena-lab.html', '/arena/lab/calendar', '/api/arena/lab/overview', '/api/arena/lab/targets/target-one', '/api/arena/lab/access'):
        response = client.get(url)
        assert response.status_code == 403
        assert 'Reviewer-only internal fixture' not in response.text
    assert '当前账号没有内部工作台权限' in client.get('/arena/lab').text
    assert calls == []


def test_lab_cookie_serves_internal_page_and_forwards_scope_exactly(workspace):
    client, _, calls, login = workspace
    login()
    for url in ('/arena/lab', '/static/arena-lab.html'):
        response = client.get(url)
        assert response.status_code == 200
        assert 'Reviewer-only internal fixture' in response.text
        assert response.headers['cache-control'] == 'no-store'
    response = client.get('/api/arena/lab/overview?cohort_id=frozen-cohort&track=open_agent&condition=with_patents')
    assert response.status_code == 200
    assert response.json()['cohort_id'] == 'frozen-cohort'
    assert calls == [('overview', {'cohort_id': 'frozen-cohort', 'track': 'open_agent', 'condition': 'with_patents'})]
    response = client.get('/api/arena/lab/targets/target-one')
    assert response.status_code == 200
    assert response.json()['target']['id'] == 'target-one'
    assert calls[-1] == ('target', 'target-one')
    assert client.get('/api/arena/lab/targets/missing').status_code == 404
    assert client.get('/api/platform/capabilities').json()['reviewer'] is False


def test_expired_session_and_unconfigured_allowlist_fail_closed(workspace, monkeypatch):
    client, _, calls, login = workspace
    login(expires=time.time() - 60)
    assert client.get('/api/arena/lab/overview').status_code == 401
    login('another-reviewer')
    monkeypatch.setenv('EMERIS_ARENA_LAB_USERS', '')
    assert client.get('/api/arena/lab/overview').status_code == 403
    monkeypatch.setenv('EMERIS_ARENA_LAB_USERS', '  ANOTHER-REVIEWER  ')
    assert client.get('/api/arena/lab/overview').status_code == 200
    monkeypatch.setenv('EMERIS_ARENA_LAB_USERS', '')
    assert client.get('/api/arena/lab/overview').status_code == 403
    assert len(calls) == 1


def test_headers_and_query_cannot_claim_reviewer_identity(workspace):
    client, _, calls, _ = workspace
    response = client.get('/api/arena/lab/overview?reviewer=jake',
                          headers={'X-Reviewer': 'jake', 'Authorization': 'Bearer isolated-fake'})
    assert response.status_code == 401 and calls == []


def test_existing_registration_normalizes_jake_without_global_reviewer_access(workspace):
    client, app, _, _ = workspace
    response = client.post('/api/auth/register', headers={'Origin': 'http://testserver'},
                           json={'name': 'Jake', 'password': 'isolated-fixture-password-123'})
    assert response.status_code == 200 and response.json()['name'] == 'jake'
    with app.state.store.connect() as db:
        assert [row['name'] for row in db.execute('SELECT name FROM users')] == ['jake']
    assert client.get('/arena/lab').status_code == 200
    assert client.get('/api/arena/lab/overview').status_code == 200
    assert client.get('/api/platform/capabilities').json()['reviewer'] is False


def test_shared_session_gate_runs_before_any_builder(workspace, monkeypatch):
    client, _, calls, login = workspace
    login()
    def denied(request):
        raise HTTPException(403, 'Isolated shared session rejection')
    monkeypatch.setattr(platform_api, 'actor', denied)
    assert client.get('/api/arena/lab/overview').status_code == 403
    assert client.get('/api/arena/lab/targets/target-one').status_code == 403
    assert client.get('/arena/lab').status_code == 403
    assert calls == []


def test_existing_reviewer_access_remains_compatible(workspace, monkeypatch):
    client, _, _, login = workspace
    monkeypatch.setenv('EMERIS_ARENA_LAB_USERS', '')
    monkeypatch.setenv('EMERIS_REVIEWERS', 'legacy-reviewer')
    login('legacy-reviewer')
    assert client.get('/arena/lab').status_code == 200
    assert client.get('/api/arena/lab/overview').status_code == 200
    assert client.get('/api/platform/capabilities').json()['reviewer'] is True


def test_no_internal_mutation_routes_or_billable_controls(workspace):
    client, app, calls, login = workspace
    login()
    paths = [route for route in app.routes if route.path.startswith('/api/arena/lab')]
    assert len(paths) == 3
    assert all(route.methods == {'GET'} for route in paths)
    for method in ('post', 'put', 'patch', 'delete'):
        for url in ('/api/arena/lab/overview', '/api/arena/lab/targets/target-one', '/api/arena/lab/access'):
            response = getattr(client, method)(url, headers={'Origin': 'http://testserver'})
            assert response.status_code == 405
    assert calls == []


def test_existing_public_audit_download_stays_anonymous(workspace):
    client, _, _, _ = workspace
    response = client.get('/api/arena/ledger')
    assert response.status_code == 200
    assert 'attachment;' in response.headers['content-disposition']
    assert response.json()['events'] == []
    # These public endpoints still exist; unknown records get 404, not auth gates.
    assert client.get('/api/arena/runs/unknown/packet').status_code == 404
    assert client.get('/api/arena/sources/' + 'a' * 64).status_code == 404


@pytest.mark.parametrize('origin,expected', [
    ('https://8.148.145.28', 'https://8.148.145.28/data'),
    ('https://8.148.145.28/', 'https://8.148.145.28/data'),
    ('http://8.148.145.28', '/data'),
    ('https://user:password@example.org', '/data'),
    ('https://example.org/path', '/data'),
    ('https://example.org?next=/private', '/data'),
    ('https://example.org:not-a-port', '/data'),
])
def test_separate_port_login_uses_validated_public_https_origin(workspace, monkeypatch, origin, expected):
    client, _, calls, _ = workspace
    monkeypatch.setenv('EMERIS_PUBLIC_ORIGIN', origin)
    response = client.get('/arena/lab')
    assert response.status_code == 401
    assert f'href="{expected}"' in response.text
    assert 'href="/arena/lab"' in response.text
    assert 'jake' not in response.text.casefold()
    assert 'user:password' not in response.text
    assert calls == []


def test_public_root_and_authorized_calendar_and_access_probe(workspace):
    client, _, calls, login = workspace
    assert 'Public clinical comparison fixture' in client.get('/').text
    login()
    response = client.get('/api/arena/lab/access')
    assert response.status_code == 204 and response.content == b''
    response = client.get('/arena/lab/calendar')
    assert response.status_code == 200 and 'Original calendar fixture' in response.text
    assert response.headers['cache-control'] == 'no-store'
    assert calls == []
