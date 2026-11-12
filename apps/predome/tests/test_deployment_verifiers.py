"""Run the historical verifiers unchanged under normal and optimized Python.

Only host paths, HTTP transport and systemctl/ps are substituted. The verifier
reads real temporary manifests, source bytes, SQLite and before-release JSON.
No production host, model service or original frozen record is accessed.
"""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from emeris_live.prediction_ensemble import MEMBERS, POLICY_SHA256


ROOT = Path(__file__).resolve().parents[1]
TABLES = ('platform_prospective_baselines', 'platform_prediction_snapshots',
          'platform_actual_outcomes', 'platform_outcome_verifications', 'intake_candidates', 'intake_jobs')
HARNESS = r'''
import importlib.util, io, json, sqlite3, sys
from pathlib import Path
from urllib.parse import urlsplit

source, fixture, mode = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
spec = importlib.util.spec_from_file_location('actual_historical_verifier', source)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
settings = json.loads((fixture / 'boundaries.json').read_text())
output = fixture / 'receipt.json'

def response(request, **kwargs):
    address = request.full_url if hasattr(request, 'full_url') else request
    parsed = urlsplit(address)
    if parsed.netloc not in ('127.0.0.1:19320', 'isolated.invalid'):
        raise RuntimeError('Unexpected HTTP boundary')
    route = parsed.path + ('?' + parsed.query if parsed.query else '')
    value = settings['http'][route]
    return io.BytesIO(value.encode() if isinstance(value, str) else json.dumps(value).encode())

module.urllib.request.urlopen = response
if mode == 'server':
    original_path, original_connect = module.Path, sqlite3.connect
    def mapped_path(value):
        return fixture / 'release' if str(value) == '/opt/emeris-live/current' else original_path(value)
    def connect(value, **kwargs):
        if value != 'file:/var/lib/emeris-live/workbench.sqlite3?mode=ro' or kwargs != {'uri': True}:
            raise RuntimeError('Unexpected database boundary')
        return original_connect((fixture / 'workbench.sqlite3').as_uri() + '?mode=ro', uri=True)
    def command(args, **kwargs):
        if args[:2] == ['systemctl', 'show'] and len(args) == 4:
            return settings['services'][args[2]]
        if args == ['ps', '-p', '1720,1852,1878', '-o', 'pid=,comm=']:
            return settings['processes']
        raise RuntimeError('Unexpected process boundary')
    module.Path = mapped_path
    module.sqlite3.connect = connect
    module.subprocess.check_output = command
    module.main(output)
else:
    module.ROOT = fixture
    module.main('https://isolated.invalid', output)
'''


@pytest.fixture(params=[False, True], ids=['normal', 'python-O'])
def optimized(request):
    return request.param


@pytest.fixture
def host(tmp_path):
    release = tmp_path / 'release'
    release.mkdir()
    (release / 'source.py').write_bytes(b'VALUE = 1\n')
    manifest = {'version': '1.1.8', 'files': [
        {'path': 'source.py', 'sha256': hashlib.sha256(b'VALUE = 1\n').hexdigest()}]}
    (release / 'RELEASE_MANIFEST.json').write_text(json.dumps(manifest))
    with sqlite3.connect(tmp_path / 'workbench.sqlite3') as db:
        for table in TABLES:
            db.execute('CREATE TABLE ' + table + ' (id TEXT)')
        db.execute('INSERT INTO platform_prediction_snapshots VALUES (?)', ('original-snapshot',))
    policy = {'policy_version': 'equal-weight-two-model.v1', 'policy_sha256': POLICY_SHA256,
              'members': list(MEMBERS), 'weights': {name: 0.5 for name in MEMBERS},
              'requires_all_members': True, 'clinical_prediction_validated': False,
              'evaluation_scope': 'descriptive_only_excluded_from_model_performance'}
    snapshots = {'records': [{'id': 'original-snapshot', 'created_at': '2026-10-21T00:00:00Z'}]}
    baselines = {'records': [{'id': 'original-baseline', 'sha256': 'a' * 64}]}
    before = tmp_path / 'verification/upgrade'
    before.mkdir(parents=True)
    for name, value in (('snapshots', snapshots), ('baselines', baselines)):
        (before / (name + '-before-ensemble-1.1.8.json')).write_text(json.dumps(value))
    return {'root': tmp_path, 'http': {
        '/api/health': {'version': '1.1.8', 'clinical_prediction_validated': False},
        '/api/predict/capabilities': {'pipeline_version': 'predome-user-trial-1.1.5',
                                      'ensemble_policy': policy, 'llm_available': False},
        '/api/platform/capabilities': {'prediction_enabled': False},
        '/api/platform/baselines': {'summary': {'registered_snapshot_count': 1}},
        '/api/platform/snapshots?limit=100': snapshots,
        '/api/platform/baselines?limit=100': baselines,
        '/api/analytics': {'summary': {'n': 0}},
        '/api/product/method': '1.1.8 等权双模型 不纳入当前单模型绩效',
    }, 'services': {name: 'ActiveState=active\nSubState=running\nResult=success\nNRestarts=0\nMemoryCurrent=1\n'
                    for name in ('emeris-live', 'emeris-intake.timer', 'emeris-intake.service')},
        'processes': '1720 node\n1852 redis-server\n1878 mysqld\n'}


def invoke(host, optimized, mode):
    fixture = host['root']
    (fixture / 'boundaries.json').write_text(json.dumps({k: v for k, v in host.items() if k != 'root'}))
    verifier = ROOT / 'deploy' / ('verify_server_state.py' if mode == 'server' else 'verify_ensemble_release.py')
    env = {key: os.environ[key] for key in ('PATH', 'HOME', 'TMPDIR', 'LANG') if key in os.environ}
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    return subprocess.run([sys.executable, *(['-O'] if optimized else []), '-c', HARNESS,
                           str(verifier), str(fixture), mode], env=env, cwd=fixture,
                          capture_output=True, text=True, timeout=20)


def refused(result, host, message):
    assert result.returncode != 0
    assert 'ValueError: ' + message in result.stderr
    assert not (host['root'] / 'receipt.json').exists()
    assert '"status": "passed"' not in result.stdout


def test_server_verifier_retains_original_receipt_and_reads_real_sqlite(host, optimized):
    original = (host['root'] / 'workbench.sqlite3').read_bytes()
    result = invoke(host, optimized, 'server')
    assert result.returncode == 0, result.stderr
    receipt = json.loads((host['root'] / 'receipt.json').read_text())
    assert receipt['status'] == 'passed'
    assert receipt['files_verified'] == 1 and receipt['sqlite_integrity'] == 'ok'
    assert receipt['counts'] == {name: int(name == 'platform_prediction_snapshots') for name in TABLES}
    assert receipt['preserved_processes'] == host['processes'].strip()
    assert receipt['health'] == host['http']['/api/health']
    assert (host['root'] / 'workbench.sqlite3').read_bytes() == original


@pytest.mark.parametrize('boundary,message', [
    ('manifest', 'Release manifest file hash differs: source.py'),
    ('version', 'Live health version differs from the release manifest'),
    ('service', 'Application service is not active'),
    ('timer', 'Intake timer is not active'),
    ('process', 'Pre-existing process missing: 1852'),
])
def test_server_verifier_refuses_changed_operational_boundary(host, optimized, boundary, message):
    if boundary == 'manifest':
        (host['root'] / 'release/source.py').write_bytes(b'changed bytes\n')
    elif boundary == 'version':
        host['http']['/api/health']['version'] = 'unexpected'
    elif boundary in ('service', 'timer'):
        host['services']['emeris-live' if boundary == 'service' else 'emeris-intake.timer'] = 'ActiveState=inactive\n'
    else:
        host['processes'] = '1720 node\n1878 mysqld\n'
    refused(invoke(host, optimized, 'server'), host, message)


def test_ensemble_verifier_retains_original_receipt_and_compares_source_records(host, optimized):
    result = invoke(host, optimized, 'ensemble')
    assert result.returncode == 0, result.stderr
    receipt = json.loads((host['root'] / 'receipt.json').read_text())
    assert receipt['status'] == 'passed' and receipt['product_contract_updated'] is True
    assert receipt['ensemble_policy'] == host['http']['/api/predict/capabilities']['ensemble_policy']
    assert receipt['preserved_records']['snapshots']['full_response_unchanged'] is True
    assert receipt['preserved_records']['baselines']['records'] == 1
    assert receipt['prospective_evaluated_count'] == 0


@pytest.mark.parametrize('boundary,message', [
    ('version', 'Health does not match the unvalidated 1.1.8 release contract'),
    ('validation', 'Health does not match the unvalidated 1.1.8 release contract'),
    ('pipeline', 'Prediction pipeline version differs from the release contract'),
    ('policy_version', 'Ensemble policy version differs from the release contract'),
    ('policy_sha256', 'Ensemble policy hash differs from the bound implementation'),
    ('members', 'Ensemble members differ from the bound implementation'),
    ('weights', 'Ensemble weights are not the required equal weights'),
    ('requires_all_members', 'Ensemble no longer requires every member'),
    ('clinical_prediction_validated', 'Ensemble clinical validation claim differs from the release contract'),
    ('evaluation_scope', 'Ensemble evaluation scope differs from the descriptive-only contract'),
    ('source_record', 'snapshots changed during deployment'),
    ('evaluated_count', 'Unexpected prospective evaluated records in the ensemble release'),
    ('method', 'Published product method differs from the ensemble release contract'),
])
def test_ensemble_verifier_refuses_changed_release_contract(host, optimized, boundary, message):
    if boundary == 'version':
        host['http']['/api/health']['version'] = 'unexpected'
    elif boundary == 'validation':
        host['http']['/api/health']['clinical_prediction_validated'] = True
    elif boundary == 'pipeline':
        host['http']['/api/predict/capabilities']['pipeline_version'] = 'unexpected'
    elif boundary == 'source_record':
        host['http']['/api/platform/snapshots?limit=100']['records'][0]['created_at'] = '2026-10-22T00:00:00Z'
    elif boundary == 'evaluated_count':
        host['http']['/api/analytics']['summary']['n'] = 1
    elif boundary == 'method':
        host['http']['/api/product/method'] = 'Unrelated published method'
    else:
        policy = host['http']['/api/predict/capabilities']['ensemble_policy']
        policy[boundary] = {'policy_version': 'unexpected', 'policy_sha256': 'b' * 64,
                            'members': list(reversed(MEMBERS)), 'weights': {name: 1 for name in MEMBERS},
                            'requires_all_members': False, 'clinical_prediction_validated': True,
                            'evaluation_scope': 'ranked'}[boundary]
    refused(invoke(host, optimized, 'ensemble'), host, message)
