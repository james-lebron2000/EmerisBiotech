#!/usr/bin/env python3
"""Bounded, isolated Docker release QA. Default mode only prepares a manifest.

Run only after the coordinating agent explicitly freezes source. No production
volumes, credentials, listeners, containers or image deletion are involved.
"""
from __future__ import annotations

import argparse
import ast
import base64
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit, urlencode
import uuid


MAX_GROWTH = 1536 * 1024**2
MIN_FREE = 1536 * 1024**2
PYTHON = '/app/.venv/bin/python'
OFFLINE = ('ascentage_annual_pipeline_2025', 'carsgen_annual_pipeline_2024')
CONTROL_FILES = ('pyproject.toml', 'uv.lock', 'deploy/cloud/Dockerfile',
                 'deploy/cloud/Caddyfile', 'deploy/cloud/compose.yaml', '.dockerignore')


def stamp():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def source_files(repo):
    result = {}
    for path in sorted((repo / 'src').rglob('*')):
        if '__pycache__' in path.parts or path.suffix in ('.pyc', '.pyo'):
            continue
        if path.is_symlink():
            raise ValueError('Linked source payload cannot enter the build context: ' + str(path))
        if not path.is_file():
            continue
        if path.suffix not in ('.py', '.json', '.css', '.js', '.html', '.svg', '.txt') or path.stat().st_size > 2_000_000:
            raise ValueError('Unexpected source payload; refuse build context: ' + str(path))
        result[path.relative_to(repo).as_posix()] = sha(path.read_bytes())
    if not result or sum((repo / p).stat().st_size for p in result) > 10_000_000:
        raise ValueError('Source context is empty or exceeds 10 MB')
    return result


class QA:
    def __init__(self, repo, folder, freeze_label):
        self.repo, self.folder = repo, folder
        self.prefix = 'emeris-release-qa-' + uuid.uuid4().hex[:10]
        self.image = 'emeris-biotech:qa-release-20261018-' + self.prefix.rsplit('-', 1)[-1]
        self.containers, self.volumes, self.networks = [], [], []
        self.baseline_free = shutil.disk_usage(repo).free
        self.report = {'started_at': stamp(), 'result': 'prepared', 'scope': 'Isolated synthetic-only local container acceptance; not a public/customer deployment',
                       'freeze_label': freeze_label, 'resource_prefix': self.prefix, 'checks': [], 'cleanup': [],
                       'harness_sha256': sha(Path(__file__).read_bytes()),
                       'source_commit': self.command(['git', 'rev-parse', 'HEAD']).stdout.strip(),
                       'source_status_porcelain': self.command(['git', 'status', '--porcelain', '--', 'src']).stdout,
                       'source_files': source_files(repo), 'lock_sha256': sha((repo / 'uv.lock').read_bytes()),
                       'pyproject_sha256': sha((repo / 'pyproject.toml').read_bytes()),
                       'dockerfile_sha256': sha((repo / 'deploy/cloud/Dockerfile').read_bytes()),
                       'caddyfile_sha256': sha((repo / 'deploy/cloud/Caddyfile').read_bytes()),
                       'control_files': {name: sha((repo/name).read_bytes()) for name in CONTROL_FILES},
                       'disk_free_before': self.baseline_free, 'max_added_disk_bytes': MAX_GROWTH}
        self.report['source_tree_sha256'] = sha(json.dumps(self.report['source_files'], sort_keys=True).encode())
        tree = ast.parse((repo/'src/vbt/disclosures.py').read_text())
        configured = next(ast.literal_eval(node.value) for node in tree.body if isinstance(node,ast.Assign)
                          and any(isinstance(target,ast.Name) and target.id=='SOURCES' for target in node.targets))
        self.report['required_offline_sources_missing'] = [name for name in OFFLINE if name not in configured
              or configured[name].get('intake_mode')!='offline_reviewed_bundle']
        self.report['docker_version'] = json.loads(self.docker('version', '--format', '{{json .}}').stdout)
        self.report['unrelated_containers_before'] = self.docker('ps', '--format', '{{.ID}} {{.Names}}').stdout.splitlines()

    def command(self, args, *, input=None, check=True, timeout=60, env=None):
        result = subprocess.run(args, input=input, text=True, capture_output=True, cwd=self.repo, timeout=timeout, env=env)
        if check and result.returncode:
            raise RuntimeError(args[0] + ' ' + args[1] + ': ' + result.stderr[-2500:])
        return result

    def docker(self, *args, **kwargs):
        return self.command(['docker', *args], **kwargs)

    def run_container(self, suffix, *args, detach=False, ephemeral=False, **kwargs):
        name = self.prefix + '-' + suffix
        if self.docker('container', 'inspect', name, check=False).returncode == 0:
            raise RuntimeError('Refuse to reuse a preexisting QA container name: ' + name)
        # Record before daemon mutation: a CLI failure may still leave a container.
        self.containers.append(name)
        return self.docker('run', '--pull=never', *(['-d'] if detach else []), *(['--rm'] if ephemeral else []),
                           '--name', name, '--label', 'emeris.qa.run=' + self.prefix, *args, **kwargs)

    def inputs_match(self):
        return (source_files(self.repo) == self.report['source_files']
                and all(sha((self.repo/name).read_bytes()) == expected
                        for name, expected in self.report['control_files'].items()))

    def check(self, name, condition, detail=None):
        if not condition:
            raise AssertionError(name + ': ' + str(detail))
        self.report['checks'].append({'name': name, 'detail': detail})
        print(json.dumps({'check': name, 'status': 'passed'}, ensure_ascii=False), flush=True)

    def disk_gate(self):
        free = shutil.disk_usage(self.repo).free
        growth = max(0, self.baseline_free - free)
        self.report['peak_observed_added_disk_bytes'] = max(self.report.get('peak_observed_added_disk_bytes', 0), growth)
        self.report['lowest_observed_free_bytes'] = min(self.report.get('lowest_observed_free_bytes', free), free)
        if growth > MAX_GROWTH or free < MIN_FREE:
            raise RuntimeError('QA disk budget exceeded: free=' + str(free) + '; baseline=' + str(self.baseline_free))

    def bounded_command(self, args, filename, timeout, env=None):
        """Monitor disk while a build/pull runs; record actual cache/network errors."""
        self.disk_gate()
        log = self.folder/filename
        started = time.monotonic()
        failure = None
        with log.open('w') as out:
            process = subprocess.Popen(args, stdout=out, stderr=subprocess.STDOUT, cwd=self.repo, env=env, start_new_session=True)
            try:
                while process.poll() is None:
                    self.disk_gate()
                    if time.monotonic()-started > timeout:
                        raise TimeoutError('Bounded command exceeded '+str(timeout)+' seconds')
                    time.sleep(.25)
            except BaseException as error:
                failure = error
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait(timeout=10)
        self.report.setdefault('bounded_commands', []).append({'argv':args, 'returncode':process.returncode,
            'elapsed_seconds':round(time.monotonic()-started,3), 'log':str(log), 'failure':repr(failure) if failure else None})
        if failure:
            raise failure
        if process.returncode:
            raise RuntimeError('Bounded command failed; '+str(log)+': '+log.read_text()[-4000:])
        self.disk_gate()

    def build(self, cache_image):
        if not self.report['freeze_label']:
            raise ValueError('--run requires an explicit source-freeze label')
        self.disk_gate()
        self.check('required_offline_sources_are_configured', not self.report['required_offline_sources_missing'], self.report['required_offline_sources_missing'])
        self.check('frozen_inputs_unchanged_before_build', self.inputs_match())
        dependency_hashes = json.loads(self.run_container('cache-probe', '--network', 'none', '--read-only', cache_image, PYTHON, '-c',
            "import pathlib,hashlib,json;print(json.dumps({n:hashlib.sha256(pathlib.Path('/app',n).read_bytes()).hexdigest() for n in ('uv.lock','pyproject.toml')}))", ephemeral=True).stdout)
        self.check('cached_dependency_inputs_match', dependency_hashes == {'uv.lock': self.report['lock_sha256'], 'pyproject.toml': self.report['pyproject_sha256']}, dependency_hashes)
        context = self.folder / 'build-context'
        context.mkdir()
        for name in ['pyproject.toml', 'uv.lock', *self.report['source_files']]:
            target = context / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.repo / name, target)
        self.check('build_context_whitelist', {p.relative_to(context).as_posix() for p in context.rglob('*') if p.is_file()} == {'pyproject.toml', 'uv.lock', *self.report['source_files']})
        expected = {**self.report['source_files'], 'pyproject.toml': self.report['pyproject_sha256'], 'uv.lock': self.report['lock_sha256']}
        self.check('build_context_exact_frozen_bytes', all(sha((context/name).read_bytes()) == digest for name, digest in expected.items()))
        control = self.folder/'build-control.Dockerfile'
        control.write_bytes((self.repo/'deploy/cloud/Dockerfile').read_bytes())
        self.check('build_control_exact_frozen_dockerfile', sha(control.read_bytes()) == self.report['dockerfile_sha256'])
        self.report['cached_base_images'] = {}
        for reference in re.findall(r'^FROM\s+([^\s]+)', control.read_text(), re.M|re.I):
            if '$' in reference:
                raise ValueError('Dynamic base-image references require a separately reviewed cache gate')
            existing = self.docker('image','inspect',reference,check=False)
            if existing.returncode:
                raise RuntimeError('Required base image is not cached; implicit registry pulls are forbidden: '+reference)
            self.report['cached_base_images'][reference] = json.loads(existing.stdout)[0]['Id']
        self.check('all_build_bases_already_cached', bool(self.report['cached_base_images']), self.report['cached_base_images'])
        env = {**os.environ, 'DOCKER_BUILDKIT': '0'}
        args = ['docker', 'build', '--network', 'none', '--pull=false', '--force-rm', '--cache-from', cache_image,
                '--label', 'emeris.qa.run=' + self.prefix,
                '-t', self.image, '-f', str(control), str(context)]
        self.bounded_command(args, 'build.log', 240, env)
        self.report['image_tag'] = self.image
        self.report['image_id'] = self.docker('image', 'inspect', self.image, '--format', '{{.Id}}').stdout.strip()
        hashes = json.loads(self.run_container('image-probe', '--network', 'none', '--read-only', self.image, PYTHON, '-c',
            "from pathlib import Path;import hashlib,json;r=Path('/app');print(json.dumps({p.relative_to(r).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in (r/'src').rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix not in ('.pyc','.pyo')}))", ephemeral=True).stdout)
        self.check('all_packaged_source_hashes_match', hashes == self.report['source_files'], {'files': len(hashes)})

    def mounts(self):
        return ['--mount', 'type=volume,source=' + self.volumes[0] + ',target=/workspace',
                '--mount', 'type=volume,source=' + self.volumes[1] + ',target=/state']

    @staticmethod
    def hardened():
        return ['--read-only', '--tmpfs', '/tmp:size=64m', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true', '--memory', '512m', '--pids-limit', '128']

    def python(self, container, code):
        return self.docker('exec', '-i', container, PYTHON, '-', input=code).stdout

    def create(self):
        for suffix in ('workspace', 'state'):
            name = self.prefix + '-' + suffix
            if self.docker('volume','inspect',name,check=False).returncode==0:
                raise RuntimeError('Refuse a preexisting QA volume: '+name)
            self.volumes.append(name)
            self.docker('volume', 'create', '--label', 'emeris.qa.run=' + self.prefix, name)
        for suffix, internal in [('backend', True), ('edge', False)]:
            name = self.prefix + '-' + suffix
            if self.docker('network','inspect',name,check=False).returncode==0:
                raise RuntimeError('Refuse a preexisting QA network: '+name)
            self.networks.append(name)
            self.docker('network', 'create', *(['--internal'] if internal else []), '--label', 'emeris.qa.run=' + self.prefix, name)
        seed = r'''from pathlib import Path
import os,json,time,hashlib
from vbt.market import Market
from vbt.market_jobs import enqueue
from vbt.io import now
from vbt import disclosures
m=Market(Path('/workspace'),Path('/state'));m.schedule(False)
m.add_company('US','TEST','SYNTHETIC QA ONLY',['Synthetic Sponsor'],'Temporary synthetic engineering fixture only')
jid=enqueue(m,'company','US:TEST')
offline=[]
for sid in ('ascentage_annual_pipeline_2025','carsgen_annual_pipeline_2024'):
 r=disclosures.ingest(m,sid);offline.append({'source':sid,'status':r['status'],'count':r['count'],'error':r['error']})
 assert r['status']=='parse_blocked' and r['count']==0 and r['sha256'] is None
assert not disclosures.observations(m)
due=enqueue(m,'disclosure','ascentage_annual_pipeline_2025')
with m.db() as db:
 db.execute("UPDATE collection_jobs SET status='running',attempts=1,owner_pid=999999,heartbeat=0,next_at=? WHERE id=?",(time.time()+86400,jid))
 d={'title':'SYNTHETIC QA ONLY','sponsor':'Synthetic Sponsor','interventions':[{'name':'SYNTHETIC DRUG'}],'conditions':['Synthetic condition'],'phases':['PHASE3'],'status':'RECRUITING','primary_completion':{},'source_updated':'2026-10-18','source_url':'https://example.invalid/synthetic-only'}
 raw=json.dumps(d,sort_keys=True);db.execute('INSERT INTO trials VALUES (?,?,?,?,?)',('US:TEST','NCT00000001',raw,hashlib.sha256(raw.encode()).hexdigest(),now()))
Path('/workspace/synthetic-qa-only.txt').write_text('Temporary synthetic acceptance only')
Path('/state/synthetic-qa-only.txt').write_text('Temporary synthetic acceptance only')
print(json.dumps({'uid':os.getuid(),'gid':os.getgid(),'workspace_writable':os.access('/workspace',os.W_OK),'state_writable':os.access('/state',os.W_OK),'stale_job':jid,'offline_job':due,'offline_intakes':offline}))
'''
        seeded = json.loads(self.run_container('seed', '-i', '--network', 'none', *self.hardened(), *self.mounts(), self.image, PYTHON, '-', input=seed, ephemeral=True).stdout)
        self.report['seed'] = seeded
        self.check('uid10001_and_persistent_volumes_writable', seeded['uid'] == seeded['gid'] == 10001 and seeded['workspace_writable'] and seeded['state_writable'])
        self.check('missing_frozen_raw_fails_closed', all(x['status'] == 'parse_blocked' and x['count'] == 0 for x in seeded['offline_intakes']), seeded['offline_intakes'])

    def wait(self, predicate, label, seconds=20):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(.2)
        raise RuntimeError(label + ' did not become ready within ' + str(seconds) + ' seconds')

    def snapshot(self, container):
        return json.loads(self.python(container, "from pathlib import Path\nfrom vbt.market import Market\nimport json\nm=Market(Path('/workspace'),Path('/state'))\nwith m.db() as db:\n print(json.dumps({'jobs':[dict(r) for r in db.execute('SELECT id,kind,status,attempts,next_at,error FROM collection_jobs ORDER BY id')],'runs':db.execute('SELECT count(*) FROM runs').fetchone()[0],'observations':db.execute('SELECT count(*) FROM disclosure_observations').fetchone()[0],'enabled':m.setting('enabled')}))"))

    def runtime(self, origin):
        app, worker = self.prefix + '-app', self.prefix + '-worker'
        self.app = app
        self.run_container('app', '--network', self.networks[0], '--network-alias', 'app',
                           *self.hardened(), *self.mounts(), '-e', 'PUBLIC_ORIGIN=' + origin, self.image, detach=True)
        self.wait(lambda: 'backend ready' in self.docker('logs', app).stdout, 'Cloud backend')
        info = json.loads(self.docker('inspect', app).stdout)[0]
        self.check('app_uid_read_only_no_direct_published_ports', info['Config']['User'] == '10001:10001' and info['HostConfig']['ReadonlyRootfs'] and not info['HostConfig']['PortBindings'])
        self.check('app_only_internal_backend_network', set(info['NetworkSettings']['Networks']) == {self.networks[0]})
        self.check('backend_network_is_internal', json.loads(self.docker('network','inspect',self.networks[0]).stdout)[0]['Internal'] is True)
        self.run_container('worker', '--network', 'none', *self.hardened(), *self.mounts(), self.image, PYTHON, '-m', 'vbt.market_worker', detach=True)
        self.wait(lambda: 'Market collector ready' in self.docker('logs', worker).stdout, 'Worker')
        worker_info = json.loads(self.docker('inspect', worker).stdout)[0]
        self.check('worker_network_none_read_only_uid10001', worker_info['HostConfig']['NetworkMode'] == 'none'
                   and worker_info['HostConfig']['ReadonlyRootfs'] and worker_info['Config']['User'] == '10001:10001')
        interfaces = json.loads(self.python(worker, "import socket,json;print(json.dumps([name for _,name in socket.if_nameindex()]))"))
        self.check('worker_has_only_loopback_interface', interfaces == ['lo'], interfaces)
        self.wait(lambda: any(j['id'] == self.report['seed']['offline_job'] and j['status'] == 'parse_blocked' for j in self.snapshot(app)['jobs']), 'Offline worker execution')
        before = self.snapshot(app)
        stale = next(row for row in before['jobs'] if row['id'] == self.report['seed']['stale_job'])
        self.check('actual_worker_recovers_seeded_dead_owner', stale['status'] == 'queued' and stale['error'] == 'Interrupted worker recovered')
        self.check('worker_offline_attempt_did_not_fetch_real_data', before['runs'] == before['observations'] == 0 and before['enabled'] == '0', before)
        self.docker('stop', '--time', '20', worker, timeout=30)
        stopped = json.loads(self.docker('inspect', worker).stdout)[0]['State']
        self.check('actual_sigterm_worker_exit_zero', stopped['ExitCode'] == 0 and not stopped['OOMKilled'], stopped)
        self.docker('start', worker)
        self.wait(lambda: self.docker('logs', worker).stdout.count('Market collector ready') >= 2, 'Worker restart')
        self.check('queue_and_no_collection_state_persist_restart', self.snapshot(app) == before)
        readonly = json.loads(self.python(app, "import json\ntry:\n open('/app/qa-forbidden-write','w').write('x');blocked=False\nexcept OSError:blocked=True\nprint(json.dumps({'read_only_write_blocked':blocked}))"))
        self.check('container_rootfs_rejects_write', readonly['read_only_write_blocked'])
        return app

    def backend_http(self, origin):
        host = urlsplit(origin).netloc
        code = r'''import urllib.request,urllib.error,json,re,http.client
from urllib.parse import urlencode
HOST=HOST_VALUE;ORIGIN=ORIGIN_VALUE
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
def req(path,method='GET',headers=None,data=None):
 r=urllib.request.Request('http://127.0.0.1:8080'+path,method=method,data=data,headers={'Host':HOST,**(headers or {})})
 try:
  with opener.open(r,timeout=8) as v:return v.status,v.read().decode()
 except urllib.error.HTTPError as v:return v.code,v.read().decode()
pages={p:req(p)[0] for p in ('/','/market','/coverage','/mapping','/disclosures','/assets?company=US%3ATEST')}
status,body=req('/market');token=re.search(r'name="token" value="([^"]+)"',body).group(1)
missing=http.client.HTTPConnection('127.0.0.1',8080,timeout=8)
missing.putrequest('GET','/market',skip_host=True);missing.endheaders();missing_status=missing.getresponse().status;missing.close()
result={'pages':pages,'wrong_host':req('/market',headers={'Host':'wrong.invalid'})[0],
 'missing_host':missing_status,
 'missing_origin':req('/market/export',method='POST',data=urlencode({'token':token}).encode())[0],
 'wrong_csrf':req('/market/export',method='POST',headers={'Origin':ORIGIN},data=b'token=wrong')[0],
 'wrong_origin':req('/market/export',method='POST',headers={'Origin':'https://wrong.invalid'},data=urlencode({'token':token}).encode())[0]}
print(json.dumps(result))
'''.replace('HOST_VALUE', repr(host)).replace('ORIGIN_VALUE', repr(origin))
        result = json.loads(self.python(self.app, code))
        self.check('backend_http_routes', all(value == 200 for value in result['pages'].values()), result['pages'])
        self.check('backend_host_csrf_origin_rejected', all(result[key] == 403 for key in ('wrong_host', 'missing_host', 'missing_origin', 'wrong_csrf', 'wrong_origin')), result)

    def gateway(self, origin, port):
        image = 'caddy:2.10.2'
        if self.docker('image', 'inspect', image, check=False).returncode:
            self.disk_gate()
            self.bounded_command(['docker','pull',image], 'gateway-pull.log', 150)
            self.disk_gate()
        self.report['gateway_image_id'] = self.docker('image', 'inspect', image, '--format', '{{.Id}}').stdout.strip()
        certs = self.folder / 'gateway'
        certs.mkdir(mode=0o755)
        def openssl(*args):
            return self.command(['openssl', *args], timeout=30)
        openssl('req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', str(certs/'ca.key'), '-out', str(certs/'ca.crt'), '-days', '2', '-subj', '/CN=Emeris disposable QA CA',
                '-addext','basicConstraints=critical,CA:TRUE','-addext','keyUsage=critical,keyCertSign,cRLSign')
        openssl('req', '-new', '-newkey', 'rsa:2048', '-nodes', '-keyout', str(certs/'server.key'), '-out', str(certs/'server.csr'), '-subj', '/CN=localhost')
        (certs/'extensions').write_text('subjectAltName=DNS:localhost,IP:127.0.0.1\nextendedKeyUsage=serverAuth\nbasicConstraints=CA:FALSE\n')
        openssl('x509', '-req', '-in', str(certs/'server.csr'), '-CA', str(certs/'ca.crt'), '-CAkey', str(certs/'ca.key'), '-CAcreateserial', '-out', str(certs/'server.crt'), '-days', '2', '-extfile', str(certs/'extensions'))
        password = secrets.token_urlsafe(24)
        hashed = self.run_container('password-hash', '--network', 'none', '--read-only', image,
                                    'caddy', 'hash-password', '--plaintext', password, ephemeral=True).stdout.strip()
        (certs/'access.caddy').write_text('tls /qa/server.crt /qa/server.key\nbasic_auth {\n qa-reviewer ' + hashed + '\n}\n')
        self.check('frozen_gateway_config_unchanged', self.inputs_match())
        (certs/'production.Caddyfile').write_bytes((self.repo/'deploy/cloud/Caddyfile').read_bytes())
        (certs/'QA.Caddyfile').write_text('{\n admin off\n auto_https disable_redirects\n}\nimport /qa/production.Caddyfile\n')
        for path in certs.iterdir():
            path.chmod(0o644 if path.name != 'ca.key' else 0o600)
        name = self.prefix + '-gateway'
        args = ['--user', '10001:10001', '--network', self.networks[1],
                *self.hardened(), '--cap-add', 'NET_BIND_SERVICE',
                '--tmpfs', '/data:uid=10001,gid=10001,size=32m', '--tmpfs', '/config:uid=10001,gid=10001,size=16m',
                '--mount', 'type=bind,source='+str(certs)+',target=/qa,readonly',
                '--mount', 'type=bind,source='+str(certs/'access.caddy')+',target=/run/secrets/access_config,readonly',
                '-e', 'DOMAIN=localhost:' + str(port), '-p', '127.0.0.1:' + str(port) + ':' + str(port),
                image, 'caddy', 'run', '--config', '/qa/QA.Caddyfile', '--adapter', 'caddyfile']
        self.run_container('gateway', *args, detach=True)
        self.docker('network', 'connect', self.networks[0], name)
        context = ssl.create_default_context(cafile=str(certs/'ca.crt'))
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=context))
        auth = 'Basic ' + base64.b64encode(('qa-reviewer:' + password).encode()).decode()
        def request(path='/', *, authorization=None, data=None, headers=None):
            h = {**(headers or {})}
            if authorization:
                h['Authorization'] = authorization
            req = urllib.request.Request(origin+path, data=data, headers=h)
            try:
                with opener.open(req, timeout=8) as value:
                    return value.status, dict(value.headers), value.read()
            except urllib.error.HTTPError as value:
                return value.code, dict(value.headers), value.read()
        def ready():
            try:
                return request()[0] == 401
            except OSError:
                return False
        self.wait(ready, 'HTTPS gateway', seconds=25)
        ordinary_trust = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=ssl.create_default_context()))
        try:
            ordinary_trust.open(origin, timeout=8)
            rejects_unknown_ca = False
        except urllib.error.URLError as error:
            rejects_unknown_ca = isinstance(error.reason, ssl.SSLCertVerificationError)
        self.check('qa_certificate_rejected_without_explicit_ca', rejects_unknown_ca)
        unauthorized = request()
        self.check('https_gateway_unauthenticated_401', unauthorized[0] == 401 and 'Basic' in {k.lower():v for k,v in unauthorized[1].items()}.get('www-authenticate',''))
        wrong = 'Basic ' + base64.b64encode(b'qa-reviewer:incorrect-qa-password').decode()
        self.check('https_gateway_wrong_credentials_401', request(authorization=wrong)[0] == 401)
        self.check('https_gateway_attachments_also_require_auth', request('/market/export-file?id='+'0'*32+'&file=manifest.json')[0] == 401)
        code, _, body = request('/market', authorization=auth)
        self.check('https_gateway_authenticated_200', code == 200)
        token = re.search(rb'name="token" value="([^"]+)"', body).group(1).decode()
        data = ('token=' + token).encode()
        self.check('https_gateway_wrong_origin_403', request('/market/export', authorization=auth, data=data, headers={'Origin':'https://wrong.invalid'})[0] == 403)
        self.check('https_gateway_wrong_csrf_403', request('/market/export', authorization=auth, data=b'token=wrong', headers={'Origin':origin})[0] == 403)
        note = urlencode({'token':token, 'kind':'note', 'author':'Synthetic QA only',
                          'body':'Temporary authenticated engineering acceptance note; not a research finding'}).encode()
        self.check('https_gateway_valid_origin_write_accepted', request('/note', authorization=auth, data=note, headers={'Origin':origin})[0] == 200)
        self.docker('stop', '--time', '10', self.app, timeout=20)
        self.docker('start', self.app)
        self.wait(lambda: self.docker('logs', self.app).stdout.count('backend ready') >= 2, 'App restart')
        persistent = json.loads(self.python(self.app, "import sqlite3,json\nwith sqlite3.connect('/state/workbench.sqlite3') as db:print(json.dumps({'notes':db.execute(\"SELECT count(*) FROM entries WHERE author='Synthetic QA only'\").fetchone()[0]}))"))
        self.check('authenticated_note_survives_app_restart', persistent['notes'] == 1)
        markers = json.loads(self.python(self.app, "from pathlib import Path;import json;print(json.dumps({n:Path(n,'synthetic-qa-only.txt').read_text() for n in ('/workspace','/state')}))"))
        self.check('both_volume_markers_survive_app_restart', all(value == 'Temporary synthetic acceptance only' for value in markers.values()), markers)
        self.report['gateway_tls'] = {'verification': 'ssl.create_default_context(cafile=temporary_qa_ca)',
                                      'ca_sha256': sha((certs/'ca.crt').read_bytes()), 'system_trust_modified': False,
                                      'credentials': 'disposable generated QA only; plaintext not retained in report',
                                      'production_caddyfile_exact_copy': True,
                                      'qa_global_options': 'admin off; auto_https disable_redirects; temporary tls cert in access import'}
        info = json.loads(self.docker('inspect', name).stdout)[0]
        capabilities = {value.removeprefix('CAP_') for value in info['HostConfig'].get('CapAdd', [])}
        self.check('gateway_only_net_bind_capability_with_no_new_privileges',
                   capabilities == {'NET_BIND_SERVICE'} and info['HostConfig'].get('CapDrop') == ['ALL']
                   and 'no-new-privileges:true' in info['HostConfig'].get('SecurityOpt', []),
                   {'added': sorted(capabilities), 'dropped': info['HostConfig'].get('CapDrop')})
        bindings = info['HostConfig']['PortBindings']
        self.check('gateway_published_loopback_only', bindings[str(port)+'/tcp'] == [{'HostIp':'127.0.0.1', 'HostPort':str(port)}])

    def cleanup(self):
        def safe(*args):
            try:
                return self.docker(*args, check=False)
            except Exception as error:
                return subprocess.CompletedProcess(args, 1, '', repr(error))
        # Cleanup is best effort per owned resource; one error must not suppress
        # the remaining attempts or the final failure report.
        for kind, names, command in [('container', self.containers, ['rm', '-f']), ('network', self.networks, ['network', 'rm']), ('volume', self.volumes, ['volume', 'rm'])]:
            for name in reversed(names):
                inspection = safe(kind, 'inspect', name)
                if inspection.returncode:
                    missing = 'No such' in inspection.stderr or 'not found' in inspection.stderr.lower()
                    self.report['cleanup'].append({'kind':kind,'name':name,'returncode':0 if missing else 1,
                                                  'result':'already_removed' if missing else 'inspection_failed', 'error':inspection.stderr[-300:]})
                    continue
                try:
                    obj = json.loads(inspection.stdout)[0]
                    labels = obj.get('Config',{}).get('Labels',{}) if kind=='container' else obj.get('Labels',{})
                    owned = isinstance(labels,dict) and labels.get('emeris.qa.run')==self.prefix and name.startswith(self.prefix+'-')
                except (ValueError,TypeError,KeyError,IndexError):
                    owned = False
                if not owned:
                    self.report['cleanup'].append({'kind':kind,'name':name,'returncode':1,'error':'Ownership proof differs; resource retained'})
                    continue
                if kind=='container':
                    logs = safe('logs','--tail','80',name)
                    self.report.setdefault('logs',{})[name] = (logs.stdout+logs.stderr)[-12000:]
                result = safe(*command, name)
                self.report['cleanup'].append({'kind': kind, 'name': name, 'returncode': result.returncode, 'error': result.stderr[-300:]})
        # Delete only this run's generated QA private keys/password-hash config.
        certs = self.folder/'gateway'
        if certs.is_dir():
            for name in ('ca.key', 'server.key', 'access.caddy'):
                path = certs/name
                try:
                    path.unlink(missing_ok=True)
                    self.report['cleanup'].append({'kind':'qa_secret_file', 'name':str(path), 'removed':not path.exists()})
                except OSError as error:
                    self.report['cleanup'].append({'kind':'qa_secret_file', 'name':str(path), 'returncode':1,'error':repr(error)})
        inventory = safe('ps', '--format', '{{.ID}} {{.Names}}')
        self.report['unrelated_containers_after'] = inventory.stdout.splitlines()
        try:
            self.check('unrelated_running_containers_unchanged', inventory.returncode == 0
                       and sorted(self.report['unrelated_containers_after']) == sorted(self.report['unrelated_containers_before']),
                       {'before':self.report['unrelated_containers_before'], 'after':self.report['unrelated_containers_after'],
                        'inventory_returncode':inventory.returncode})
        except Exception as error:
            self.report.update(result='failed', container_inventory_error=repr(error))
        self.report['disk_free_after'] = shutil.disk_usage(self.repo).free
        try:
            self.disk_gate()
        except Exception as error:
            # Always retain the final failure and cleanup report, even if disk
            # pressure appears during runtime rather than a monitored build.
            self.report.update(result='failed', disk_budget_error=repr(error))
        try:
            self.report['source_after_qa_matches'] = self.inputs_match()
        except Exception as error:
            self.report['source_after_qa_matches'] = False
            self.report['source_validation_error'] = repr(error)
        if not self.report['source_after_qa_matches']:
            self.report.update(result='failed',error='Source or release-control bytes changed during acceptance')
        if any(row.get('returncode', 0) != 0 for row in self.report['cleanup']):
            self.report.update(result='failed', cleanup_error='Some dedicated QA resources were not removed; see cleanup log')
        self.report['finished_at'] = stamp()
        (self.folder/'acceptance.json').write_text(json.dumps(self.report, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--run', action='store_true', help='Build/run only AFTER source freeze')
    parser.add_argument('--freeze-label', default='')
    parser.add_argument('--cache-image', default='emeris-biotech:qa-market-20261017')
    parser.add_argument('--skip-gateway', action='store_true')
    parser.add_argument('--report-dir', type=Path)
    args = parser.parse_args()
    folder = args.report_dir or Path('/tmp')/('emeris-release-qa-'+uuid.uuid4().hex[:10])
    folder.mkdir(mode=0o700, parents=True, exist_ok=False)
    qa = QA(args.repo.resolve(), folder.resolve(), args.freeze_label)
    if not args.run:
        (folder/'prepare.json').write_text(json.dumps(qa.report, ensure_ascii=False, indent=2))
        print(json.dumps({'result':'prepared_only_no_build', 'source_files':len(qa.report['source_files']),
                          'source_tree_sha256':qa.report['source_tree_sha256'], 'evidence':str(folder/'prepare.json')}, ensure_ascii=False))
        return 0
    if not args.freeze_label:
        parser.error('--run requires --freeze-label after explicit coordinating-agent source freeze')
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    origin = 'https://localhost:' + str(port)
    try:
        qa.disk_gate()
        qa.build(args.cache_image)
        qa.disk_gate()
        qa.create()
        qa.disk_gate()
        qa.runtime(origin)
        qa.disk_gate()
        qa.backend_http(origin)
        qa.disk_gate()
        if args.skip_gateway:
            qa.report['gateway_status'] = 'not_tested_explicit_skip'
        else:
            qa.gateway(origin, port)
        qa.disk_gate()
        qa.check('source_and_controls_did_not_change_during_acceptance', qa.inputs_match())
        qa.report['result'] = 'partial' if args.skip_gateway else 'passed'
    except BaseException as exc:
        qa.report.update(result='failed', error=repr(exc))
    finally:
        qa.cleanup()
    print(json.dumps({'result':qa.report['result'], 'checks':len(qa.report['checks']), 'evidence':str(folder/'acceptance.json'), 'error':qa.report.get('error')}, ensure_ascii=False))
    return 0 if qa.report['result'] in ('passed','partial') else 1


if __name__ == '__main__':
    raise SystemExit(main())
