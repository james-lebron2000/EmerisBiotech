"""Isolated deployment safety checks; no SSH, systemd or production files."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import tarfile
from types import SimpleNamespace

import pytest

SPEC = importlib.util.spec_from_file_location('arena_deployment', Path(__file__).parents[1] / 'scripts/deploy_arena.py')
deploy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(deploy)


def digest(value):
    return hashlib.sha256(value).hexdigest()


def archive(tmp_path, members, commit='a' * 40):
    database = members.pop('arena-initial.sqlite3', b'test-ledger')
    value = {'git_commit': commit, 'files': {name: digest(raw) for name, raw in members.items()},
             'arena_database_sha256': digest(database), 'arena_ledger_sha256': 'b' * 64}
    members = {**members, 'OVERLAY.json': json.dumps(value).encode(), 'arena-initial.sqlite3': database}
    target = tmp_path / 'overlay.tar.gz'
    with tarfile.open(target, 'w:gz') as tar:
        for name, raw in members.items():
            info = tarfile.TarInfo(name); info.size = len(raw)
            tar.addfile(info, io.BytesIO(raw))
    return target, deploy.sha(target), commit


@pytest.mark.parametrize('name', ['', '../static/x.js', '/static/x.js', 'static//x.js', 'static/./x.js',
                                 'static/../../x.js', 'static\\x.js', 'static/x.env', 'scripts/key.pem',
                                 'arena_data/x.sqlite3', 'arena_data/.secret', 'emeris_live/x.pyc', 'predome_data/x.json'])
def test_unsafe_paths_rejected(name):
    assert not deploy.allowed(name)


def test_public_regular_files_and_secret_detection():
    assert deploy.allowed('static/arena.js')
    assert deploy.allowed('patent_data/.gitignore')
    with pytest.raises(ValueError, match='Credential-like'):
        deploy.public_bytes('source.json', b'{"key":"' + b'sk-' + b'x' * 30 + b'"}')


def test_exclusive_import_cannot_overwrite_file_or_dangling_link(tmp_path):
    target = tmp_path / 'arena.sqlite3'
    deploy.exclusive_write(target, b'old')
    with pytest.raises(FileExistsError):
        deploy.exclusive_write(target, b'new')
    assert target.read_bytes() == b'old'
    target.unlink(); target.symlink_to(tmp_path / 'nonexistent')
    with pytest.raises(FileExistsError):
        deploy.exclusive_write(target, b'new')
    assert not (tmp_path / 'nonexistent').exists()


def make_workbench(path):
    with sqlite3.connect(path) as db:
        for table in deploy.SEALED_TABLES:
            db.execute('CREATE TABLE ' + table + ' (id INTEGER PRIMARY KEY, value TEXT)')
            db.execute('INSERT INTO ' + table + ' VALUES (1,"immutable")')


def test_sealed_record_hashes_detect_equal_count_modification(tmp_path):
    path = tmp_path / 'workbench.sqlite3'; make_workbench(path)
    before = deploy.sealed_rows(path)
    with sqlite3.connect(path) as db:
        db.execute('INSERT INTO platform_prediction_snapshots VALUES (2,"new legitimate record")')
    deploy.check_sealed_rows(before, deploy.sealed_rows(path))
    with sqlite3.connect(path) as db:
        db.execute('UPDATE platform_prediction_snapshots SET value="modified" WHERE id=1')
    with pytest.raises(ValueError, match='sealed clinical rows changed'):
        deploy.check_sealed_rows(before, deploy.sealed_rows(path))


def test_initial_database_rejects_wrong_database(tmp_path):
    path = tmp_path / 'workbench.sqlite3'; make_workbench(path)
    with pytest.raises(ValueError, match='isolated Arena ledger'):
        deploy.check_initial_database(path)


def test_tar_blocks_traversal_before_any_deploy(tmp_path):
    args = archive(tmp_path, {'static/../../escape.py': b'bad'})
    with pytest.raises(ValueError, match='Unsafe release'):
        deploy.load_overlay(*args)
    assert not (tmp_path / 'escape.py').exists()


def test_tar_pin_and_member_hash_checked(tmp_path):
    args = archive(tmp_path, {'static/arena.js': b'public'})
    manifest, content, database = deploy.load_overlay(*args)
    assert content == {'static/arena.js': b'public'}
    with pytest.raises(ValueError, match='hash mismatch'):
        deploy.load_overlay(args[0], 'f' * 64, args[2])
    with pytest.raises(ValueError, match='Unexpected release content'):
        deploy.load_overlay(args[0], args[1], 'e' * 40)


@pytest.fixture
def isolated_server(tmp_path, monkeypatch):
    home = tmp_path / 'app'; base = home / 'releases/base'; base.mkdir(parents=True)
    data = tmp_path / 'data'; data.mkdir()
    current = home / 'current'; current.symlink_to(base)
    sealed = base / 'predome_data/frozen.json'; sealed.parent.mkdir(); sealed.write_text('{"frozen":true}')
    manifest = {'version': '1.1.8', 'ui_revision': 'editorial-01', 'files': [
        {'path': 'predome_data/frozen.json', 'sha256': deploy.sha(sealed), 'bytes': sealed.stat().st_size}]}
    (base / 'RELEASE_MANIFEST.json').write_text(json.dumps(manifest))
    env = tmp_path / 'operator.env'; env.write_text('SEM_API_KEY=not-an-actual-secret\n'); env.chmod(0o600)
    dropin = tmp_path / 'service.d/arena.conf'
    monkeypatch.setattr(deploy, 'BASE', base); monkeypatch.setattr(deploy, 'TARGET', home / 'releases/new')
    monkeypatch.setattr(deploy, 'CURRENT', current); monkeypatch.setattr(deploy, 'DATA', data)
    monkeypatch.setattr(deploy, 'ENV_FILE', env); monkeypatch.setattr(deploy, 'DROPIN', dropin)
    # The test user is unprivileged; only the synthetic environment file's owner
    # is modeled as root. Production preflight still requires actual uid 0.
    path_stat = Path.stat
    def fake_stat(self, *args, **kwargs):
        result = path_stat(self, *args, **kwargs)
        if self == env:
            return SimpleNamespace(st_mode=result.st_mode, st_uid=0)
        return result
    monkeypatch.setattr(Path, 'stat', fake_stat)
    make_workbench(data / 'workbench.sqlite3')
    source = tmp_path / 'initial.sqlite3'
    with sqlite3.connect(source) as db:
        db.execute('CREATE TABLE arena_events(seq INTEGER PRIMARY KEY AUTOINCREMENT, payload TEXT)')
        db.execute('INSERT INTO arena_events(payload) VALUES ("public fixture")')
    args = archive(tmp_path, {'static/arena.html': b'<main>New page</main>', 'arena-initial.sqlite3': source.read_bytes(),
                             'scripts/deploy_arena.py': Path(deploy.__file__).read_bytes()})
    monkeypatch.setattr(deploy, 'verify_release', lambda *a: {'counts': {}, 'health': {'version': 'test'}})
    monkeypatch.setattr(deploy.subprocess, 'run', lambda *a, **k: subprocess.CompletedProcess(a[0], 0))
    monkeypatch.setattr(deploy.shutil, 'chown', lambda *a, **k: None)
    return SimpleNamespace(args=args, home=home, base=base, data=data, env=env, current=current, dropin=dropin,
                           frozen_bytes=sealed.read_bytes(), manifest_bytes=(base / 'RELEASE_MANIFEST.json').read_bytes())


def test_missing_configuration_has_no_data_or_release_side_effects(isolated_server, monkeypatch):
    server = isolated_server; server.env.unlink()
    monkeypatch.setattr(deploy, 'systemctl', lambda *a: pytest.fail('Must not invoke systemd'))
    with pytest.raises(ValueError, match='regular file'):
        deploy.remote(*server.args)
    assert server.current.resolve() == server.base
    assert not deploy.TARGET.exists()
    assert not (server.data / 'arena.sqlite3').exists()
    assert not (server.home / 'backups').exists()


@pytest.mark.parametrize('suffix', ['', '-wal', '-shm', '-journal'])
def test_existing_ledger_or_sidecars_block_first_import(isolated_server, suffix):
    server = isolated_server; existing = server.data / ('arena.sqlite3' + suffix); existing.write_bytes(b'existing')
    with pytest.raises(ValueError, match='must never be overwritten'):
        deploy.remote(*server.args)
    assert existing.read_bytes() == b'existing'
    assert not deploy.TARGET.exists()


def test_failed_service_switch_restores_base_preserves_ledgers(isolated_server, monkeypatch):
    server = isolated_server; commands = []
    old_rows = deploy.sealed_rows(server.data / 'workbench.sqlite3')
    def systemctl(*args):
        commands.append(args)
        if args == ('restart', 'emeris-live') and server.current.resolve() == deploy.TARGET:
            raise subprocess.CalledProcessError(1, ['systemctl', *args])
    monkeypatch.setattr(deploy, 'systemctl', systemctl)
    with pytest.raises(subprocess.CalledProcessError):
        deploy.remote(*server.args)
    assert server.current.resolve() == server.base
    assert not server.dropin.exists()
    assert (server.data / 'arena.sqlite3').exists()  # preserved for investigation
    assert (server.home / 'backups/openforecast01/workbench.sqlite3').exists()
    assert (server.base / 'RELEASE_MANIFEST.json').read_bytes() == server.manifest_bytes
    assert (server.base / 'predome_data/frozen.json').read_bytes() == server.frozen_bytes
    deploy.check_sealed_rows(old_rows, deploy.sealed_rows(server.data / 'workbench.sqlite3'))
    assert all(args in (('daemon-reload',), ('restart', 'emeris-live')) for args in commands)
    failure = json.loads((server.home / 'openforecast01-failure.json').read_text())
    assert failure['status'] == 'deployment_failed'
    assert failure['imported_ledger_preserved'] is True


def test_success_retains_base_manifest_and_only_restarts_app(isolated_server, monkeypatch):
    server = isolated_server; commands = []
    monkeypatch.setattr(deploy, 'systemctl', lambda *args: commands.append(args))
    response = io.BytesIO(json.dumps({'ledger_sha256': 'b' * 64, 'runs': []}).encode())
    monkeypatch.setattr(deploy.urllib.request, 'urlopen', lambda *a, **k: response)
    deploy.remote(*server.args)
    assert server.current.resolve() == deploy.TARGET
    assert commands == [('daemon-reload',), ('restart', 'emeris-live')]
    assert (server.base / 'RELEASE_MANIFEST.json').read_bytes() == server.manifest_bytes
    assert (deploy.TARGET / 'predome_data/frozen.json').read_bytes() == server.frozen_bytes
    assert json.loads((deploy.TARGET / 'RELEASE_MANIFEST.json').read_text())['ui_revision'] == 'editorial-01'


def test_patent_assets_ignore_unlisted_secrets_and_validate_anchor(tmp_path, monkeypatch):
    monkeypatch.setattr(deploy, 'ROOT', tmp_path)
    raw = b'%PDF-fixture'; ident = 'WO202600001A1'
    folder = tmp_path / 'patent_data/raw'; folder.mkdir(parents=True)
    (folder / (ident + '.pdf')).write_bytes(raw)
    (folder / 'operator.env').write_text('sensitive material must not be bundled')
    catalog = {'items': [{'publication_number': ident, 'raw_sha256': digest(raw), 'page_references': []}]}
    result = deploy.patent_assets({'patent_data/catalog.json': json.dumps(catalog).encode()})
    assert set(result) == {'patent_data/raw/' + ident + '.pdf'}
    (folder / (ident + '.pdf')).write_bytes(b'%PDF-tampered')
    with pytest.raises(ValueError, match='differs from committed catalog'):
        deploy.patent_assets({'patent_data/catalog.json': json.dumps(catalog).encode()})


def test_review_assets_require_committed_receipt_and_exact_hashes(tmp_path, monkeypatch):
    monkeypatch.setattr(deploy, 'ROOT', tmp_path)
    ident = 'WO202600001A1'; pdf = b'%PDF-fixture'; png = b'\x89PNG\r\n\x1a\nfixture'
    folder = tmp_path / 'patent_data'; (folder / 'raw').mkdir(parents=True); (folder / 'receipts/review').mkdir(parents=True)
    (folder / 'raw' / (ident + '.pdf')).write_bytes(pdf)
    (folder / 'receipts/review/page.png').write_bytes(png)
    (folder / 'receipts/review/unlisted.png').write_bytes(b'unlisted data excluded')
    html = b'<p>Official public identity source fixture</p>'
    source = {'source_id': 'nci', 'url': 'https://www.cancer.gov/publications/dictionaries/cancer-drug/def/datopotamab-deruxtecan',
              'raw_sha256': digest(html), 'retrieved_at': '2026-10-22T00:00:00Z'}
    (folder / 'receipts/review/nci_dato.html').write_bytes(html)
    (folder / 'receipts/review/nci_dato.receipt.json').write_text(json.dumps({
        'source_url': source['url'], 'raw_sha256': source['raw_sha256'], 'retrieved_at': source['retrieved_at'], 'bytes': len(html)}))
    review = {'page_checks': [{'preview_relative_path': 'receipts/review/page.png', 'preview_sha256': digest(png)}],
              'drug_mapping': {'sources': [source]}}
    catalog = {'items': [{'publication_number': ident, 'raw_sha256': digest(pdf), 'research_review': review}]}
    files = {'patent_data/catalog.json': json.dumps(catalog).encode(),
             'patent_data/sources/' + ident + '.identity-review.json': json.dumps(review).encode()}
    assets = deploy.patent_assets(files)
    assert len(assets) == 4
    assert not any('unlisted' in name for name in assets)
    (folder / 'receipts/review/nci_dato.html').write_bytes(b'changed')
    with pytest.raises(ValueError, match='snapshot differs'):
        deploy.patent_assets(files)


def test_committed_overlay_rejects_deletions(monkeypatch):
    monkeypatch.setattr(deploy.subprocess, 'check_output', lambda *a, **k: 'D\tapps/predome/static/old.html\n')
    with pytest.raises(ValueError, match='removed or changed-type'):
        deploy.committed_overlay('a' * 40)


def test_staged_validation_failure_does_not_import_database(isolated_server, monkeypatch):
    server = isolated_server
    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, ['isolated-stage-validation'])
    monkeypatch.setattr(deploy.subprocess, 'run', fail)
    monkeypatch.setattr(deploy, 'systemctl', lambda *args: pytest.fail('No service changes before validation'))
    with pytest.raises(subprocess.CalledProcessError):
        deploy.remote(*server.args)
    assert server.current.resolve() == server.base
    assert not (server.data / 'arena.sqlite3').exists()
    assert not server.dropin.exists()
    assert (server.base / 'RELEASE_MANIFEST.json').read_bytes() == server.manifest_bytes


def proof_files(identifier='pilot-registration-20261022'):
    prefix = 'arena_data/attestations/' + identifier + '/'
    certificate = b'-----BEGIN CERTIFICATE-----\nQUJD\n-----END CERTIFICATE-----\n'
    values = {'manifest.json': b'{"public":"snapshot"}', 'manifest.tsq': b'query',
              'manifest.tsr': b'response', 'tsa.crt': certificate, 'cacert.pem': certificate,
              'verification.txt': b'Verification: OK'}
    hashes = {name: digest(raw) for name, raw in values.items()}
    receipt = {'id': identifier, 'files': hashes, 'manifest_sha256': hashes['manifest.json'],
               'timestamp_response_sha256': hashes['manifest.tsr'], 'authority_cert_sha256': hashes['tsa.crt'],
               'ca_cert_sha256': hashes['cacert.pem']}
    values['receipt.json'] = json.dumps(receipt).encode()
    return {prefix + name: raw for name, raw in values.items()}


@pytest.mark.parametrize('name', ['scripts/cacert.pem', 'arena_data/cacert.pem', 'arena_data/attestations/key.pem',
    'arena_data/attestations/proof/other.pem', 'arena_data/attestations/proof/private.key',
    'arena_data/attestations/proof/extra.json', 'arena_data/attestations/proof/nested/tsa.crt',
    'arena_data/attestations/not_valid/cacert.pem'])
def test_attestation_exception_does_not_allow_general_certificates_or_files(name):
    assert not deploy.allowed(name)


def test_exact_attestation_files_and_all_receipt_hashes_are_validated():
    values = proof_files()
    assert all(deploy.allowed(name) for name in values)
    assert len(deploy.validate_attestations(values)) == 1
    values['arena_data/attestations/pilot-registration-20261022/manifest.tsr'] = b'changed'
    with pytest.raises(ValueError, match='receipt hash mismatch'):
        deploy.validate_attestations(values)


def test_missing_attestation_certificate_is_not_a_partial_release():
    values = proof_files(); values.pop('arena_data/attestations/pilot-registration-20261022/cacert.pem')
    with pytest.raises(ValueError, match='Incomplete attestation'):
        deploy.validate_attestations(values)


@pytest.mark.parametrize('kind', ['', 'RSA ', 'EC ', 'DSA ', 'OPENSSH ', 'ENCRYPTED '])
def test_private_key_disguised_as_attestation_ca_is_rejected(kind):
    values = proof_files()
    values['arena_data/attestations/pilot-registration-20261022/cacert.pem'] = ('-----BEGIN '+kind+'PRIVATE KEY-----\nfixture').encode()
    with pytest.raises(ValueError, match='Credential-like'):
        deploy.validate_attestations(values)


def test_committed_overlay_contains_two_complete_attestations(tmp_path, monkeypatch):
    values = {**proof_files(), **proof_files('pilot-predictions-20261022'),
              'scripts/deploy_arena.py': Path(deploy.__file__).read_bytes(), 'patent_data/catalog.json': b'{"items":[]}'}
    for name, raw in values.items():
        target = tmp_path / name; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(raw)
    monkeypatch.setattr(deploy, 'ROOT', tmp_path)
    monkeypatch.setattr(deploy, 'patent_assets', lambda files: {})
    def git(args, **kwargs):
        if args[1] == 'diff':
            return ''.join('A\tapps/predome/' + name + '\n' for name in values)
        return values[args[2].split(':apps/predome/')[1]]
    monkeypatch.setattr(deploy.subprocess, 'check_output', git)
    result = deploy.committed_overlay('a' * 40)
    proofs = {name: raw for name, raw in result.items() if '/attestations/' in name}
    assert len(proofs) == 14
    assert set(deploy.validate_attestations(proofs)) == {'pilot-registration-20261022', 'pilot-predictions-20261022'}
