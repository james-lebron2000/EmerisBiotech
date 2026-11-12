"""Port split fails closed and rolls back only its own Nginx site."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import pytest

spec=importlib.util.spec_from_file_location('arena_frontend_config',Path(__file__).parents[1]/'scripts/configure_arena_frontends.py')
ports=importlib.util.module_from_spec(spec);spec.loader.exec_module(ports)


@pytest.fixture
def staged(tmp_path,monkeypatch):
    site=tmp_path/'site';site.write_bytes(b'original existing public site')
    monkeypatch.setattr(ports,'SITE',site);monkeypatch.setattr(ports,'BACKUP',tmp_path/'backup')
    monkeypatch.setattr(ports,'RECEIPT',tmp_path/'receipt')
    monkeypatch.setattr(ports.urllib.request,'urlopen',lambda *a,**kw:io.BytesIO(b'{"version":"1.2.1"}'))
    monkeypatch.setattr(ports.subprocess,'check_output',lambda *a,**kw:'')
    calls=[];monkeypatch.setattr(ports.subprocess,'run',lambda args,**kw:calls.append(args))
    return site,hashlib.sha256(site.read_bytes()).hexdigest(),calls


def test_changed_existing_site_is_never_overwritten(staged):
    site,expected,calls=staged;site.write_bytes(b'concurrent operator modification')
    with pytest.raises(ValueError,match='changed'):ports.remote(expected)
    assert site.read_bytes()==b'concurrent operator modification'
    assert not ports.BACKUP.exists() and calls==[]


def test_port_collision_does_not_replace_other_service(staged,monkeypatch):
    site,expected,calls=staged
    monkeypatch.setattr(ports.subprocess,'check_output',lambda *a,**kw:'LISTEN 0 511 0.0.0.0:18443 0.0.0.0:*\n')
    with pytest.raises(ValueError,match='already used'):ports.remote(expected)
    assert hashlib.sha256(site.read_bytes()).hexdigest()==expected and not ports.BACKUP.exists()
    assert calls==[]


def test_invalid_nginx_configuration_restores_old_bytes(staged,monkeypatch):
    site,expected,calls=staged
    def run(args,**kw):
        calls.append(args)
        if len(calls)==1:raise subprocess.CalledProcessError(1,args)
    monkeypatch.setattr(ports.subprocess,'run',run)
    with pytest.raises(subprocess.CalledProcessError):ports.remote(expected)
    assert hashlib.sha256(site.read_bytes()).hexdigest()==expected
    assert ports.BACKUP.read_bytes()==site.read_bytes() and not ports.RECEIPT.exists()
    assert calls==[['nginx','-t'],['nginx','-t'],['systemctl','reload','nginx']]


def test_success_keeps_backup_and_pins_public_private_routes(staged):
    site,expected,calls=staged;old=site.read_bytes();ports.remote(expected)
    assert ports.BACKUP.read_bytes()==old and site.read_bytes()==ports.render()
    receipt=json.loads(ports.RECEIPT.read_text())
    assert receipt['previous_config_sha256']==expected
    assert receipt['internal_origin']=='https://8.148.145.28:18443'
    assert receipt['unrelated_sites_modified'] is False
    assert calls==[['nginx','-t'],['systemctl','reload','nginx']]
    public,private=site.read_text().split('    listen 18443 ssl http2;',1)
    assert 'location ^~ /api/arena/ { return 404; }' in public
    assert 'location ^~ /api/arena/public/' in public
    assert 'location = /arena/lab { return 404; }' in public
    assert 'auth_request /_lab_auth;' in private and 'limit_except GET HEAD' in private
    assert 'proxy_set_header Cookie $http_cookie;' in private
    for path in ('/api/product','/api/dashboard','/api/predome','/api/forecasts','/api/analytics',
                 '/api/platform/snapshots','/api/platform/evaluations',
                 '/api/platform/public-outcomes','/api/platform/baselines'):
        assert 'location ^~ '+path+' { return 404; }' in public
    for path in ('/api/intake/status','/openapi.json','/static/predome.html','/static/trials.html'):
        assert 'location = '+path+' { return 404; }' in public
    assert 'location = /predome { return 302 https://8.148.145.28:18443/predome; }' in public
    # The existing authenticated upload, mapping and prediction workbenches
    # continue through the general proxy rather than being denied wholesale.
    for path in ('/api/state','/api/upload','/api/datasets','/api/public',
                 '/api/predict','/api/platform/predictions','/api/research'):
        assert 'location ^~ '+path+' { return 404; }' not in public


def test_review_access_is_a_narrow_internal_exception_not_global_write_access():
    public,private=ports.upgrade_review_routes(ports.render()).decode().split('    listen 18443 ssl http2;',1)
    for name in ('arena-review.html','arena-review.js','arena-review.css'):
        assert 'location = /static/'+name+' { return 404; }' in public
    assert 'location ^~ /arena/lab/ { return 404; }' in public
    assert 'location ^~ /api/arena/ { return 404; }' in public
    auth=private.split('location = /_review_auth {',1)[1].split('}',1)[0]
    assert 'internal;' in auth and '/api/arena/review/access;' in auth
    assert 'proxy_pass_request_body off;' in auth and 'proxy_set_header Cookie $http_cookie;' in auth
    route=private.split('location ^~ /api/arena/review/ {',1)[1].split('proxy_pass',1)[0]
    assert 'auth_request /_review_auth;' in route and 'limit_except GET HEAD POST { deny all; }' in route
    for prefix in ('location ^~ /api/arena/ {','location /api/ {','location = /api/arena {'):
        block=private.split(prefix,1)[1].split('proxy_pass',1)[0]
        assert 'auth_request /_lab_auth;' in block
        assert 'limit_except GET HEAD { deny all; }' in block
        assert 'GET HEAD POST' not in block


def test_upgrade_preserves_public_proof_aliases_and_every_existing_line():
    import difflib
    import sys
    sys.path.insert(0,str(Path(__file__).parents[1]/'scripts'))
    import publish_public_proof as publication
    baseline=ports.render()
    assert publication.digest(baseline)==publication.EXPECTED_SITE
    published=publication.render(baseline)
    upgraded=ports.upgrade_review_routes(published)
    for name in publication.proof.PUBLIC_FILES:
        assert ('alias '+str(publication.DEST/name)+';').encode() in upgraded
    changes=difflib.SequenceMatcher(None,published.splitlines(keepends=True),upgraded.splitlines(keepends=True)).get_opcodes()
    assert all(change[0] in ('equal','insert') for change in changes)
    with pytest.raises(ValueError,match='baseline'):
        ports.upgrade_review_routes(upgraded)


@pytest.mark.parametrize('change',[b'    listen 18443 ssl http2;\n',b'    location = /_lab_auth {\n',
                                 b'    location ^~ /api/arena/ { return 404; }\n'])
def test_incomplete_or_changed_review_upgrade_boundaries_are_rejected(change):
    with pytest.raises(ValueError):
        ports.upgrade_review_routes(ports.render().replace(change,b'',1))


def test_wrong_app_version_does_not_touch_server_site(staged,monkeypatch):
    site,expected,calls=staged
    monkeypatch.setattr(ports.urllib.request,'urlopen',lambda *a,**kw:io.BytesIO(b'{"version":"1.2.0"}'))
    with pytest.raises(ValueError,match='before frontend split'):ports.remote(expected)
    assert hashlib.sha256(site.read_bytes()).hexdigest()==expected and not ports.BACKUP.exists()
