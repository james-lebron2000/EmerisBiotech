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


def test_wrong_app_version_does_not_touch_server_site(staged,monkeypatch):
    site,expected,calls=staged
    monkeypatch.setattr(ports.urllib.request,'urlopen',lambda *a,**kw:io.BytesIO(b'{"version":"1.2.0"}'))
    with pytest.raises(ValueError,match='before frontend split'):ports.remote(expected)
    assert hashlib.sha256(site.read_bytes()).hexdigest()==expected and not ports.BACKUP.exists()
