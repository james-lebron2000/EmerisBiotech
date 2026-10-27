"""Actual operator command over synthetic HTTP and real isolated PDF parsing.

No source-audit originals, production state, model, database or external HTTP
are used. Transport fixtures replace only the registry/CDN boundary.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
from urllib.error import HTTPError

import pytest

from emeris_live import trial_documents as documents
from test_trial_documents import pdf_bytes, transport


APP = Path(__file__).resolve().parents[1]
NCT = 'NCT01234567'
REGISTRY = 'https://clinicaltrials.gov/api/v2/studies/' + NCT
PREFIX = 'https://cdn.clinicaltrials.gov/large-docs/67/' + NCT + '/'
PROTOCOL, SAP = 'Prot_fixture.pdf', 'SAP_fixture.pdf'
CANARY = 'SYNTHETIC_UNPUBLISHED_HTTP_DETAIL'


def cli_module():
    spec = importlib.util.spec_from_file_location('actual_trial_documents_cli',
        APP / 'scripts/acquire_trial_documents.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def registry_bytes(originals):
    rows = [{'filename': name, 'hasProtocol': name == PROTOCOL,
             'hasSap': name == SAP, 'hasIcf': False, 'size': len(raw)}
            for name, raw in originals.items()]
    return json.dumps({'protocolSection': {'identificationModule': {'nctId': NCT}},
        'documentSection': {'largeDocumentModule': {'largeDocs': rows}}},
        indent=1).encode() + b'\n'


def saved_tree(directory):
    return {path.relative_to(directory).as_posix(): path.read_bytes()
            for path in directory.rglob('*') if path.is_file()}


@pytest.fixture(autouse=True)
def no_external_effects(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('No real network, model or database is authorized')
    monkeypatch.setattr(documents, 'build_opener', forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(sqlite3, 'connect', forbidden)


def test_real_cli_extract_flag_requires_explicit_names_before_output_or_transport(tmp_path):
    output = tmp_path / 'must-not-exist'
    process = subprocess.run([sys.executable, '-B', str(APP / 'scripts/acquire_trial_documents.py'),
        '--nct', NCT, '--output-dir', str(output), '--extract'],
        cwd=tmp_path, capture_output=True, text=True, timeout=20,
        env={'PATH': os.defpath, 'PYTHONDONTWRITEBYTECODE': '1'})
    assert process.returncode == 2
    assert '--extract requires at least one explicitly selected --document' in process.stderr
    assert process.stdout == '' and not output.exists()


def test_actual_main_inventory_only_returns_complete_without_pdf_or_extraction(tmp_path, monkeypatch, capsys):
    cli = cli_module()
    raw = registry_bytes({PROTOCOL: b'%PDF-1.7\nnot requested'})
    calls = transport(monkeypatch, {REGISTRY: raw})
    output = tmp_path / 'inventory-only'
    assert cli.main(['--nct', NCT, '--output-dir', str(output)]) == 0
    summary = json.loads(capsys.readouterr().out)
    manifest = json.loads((output / 'manifest.json').read_bytes())
    assert summary['status'] == manifest['status'] == 'complete'
    assert summary['extraction_status'] == 'not_requested' and summary['acquired_documents'] == 0
    assert [call['url'] for call in calls] == [REGISTRY]
    assert (output / 'registry.json').read_bytes() == raw
    assert set(saved_tree(output)) == {'registry.json', 'inventory.json', 'manifest.json'}
    assert manifest['extraction_performed'] is False
    assert summary['model_calls'] == summary['ledger_writes'] == summary['human_scientific_reviews'] == 0


def test_actual_main_extracts_named_originals_with_page_counts_and_separate_manifests(tmp_path, monkeypatch, capsys):
    cli = cli_module()
    originals = {PROTOCOL: pdf_bytes(['SYNTHETIC protocol population', 'SYNTHETIC protocol endpoint']),
                 SAP: pdf_bytes(['SYNTHETIC SAP estimand'])}
    registry = registry_bytes(originals)
    calls = transport(monkeypatch, {REGISTRY: registry,
        **{PREFIX + name: raw for name, raw in originals.items()}})
    output = tmp_path / 'selected'
    args = ['--nct', NCT, '--output-dir', str(output), '--document', PROTOCOL,
            '--document', SAP, '--extract']
    assert cli.main(args) == 0
    summary = json.loads(capsys.readouterr().out)
    acquisition_bytes = (output / 'manifest.json').read_bytes()
    acquisition = json.loads(acquisition_bytes)
    extraction = json.loads((output / 'extraction-manifest.json').read_bytes())
    assert summary['status'] == acquisition['status'] == 'complete'
    assert summary['extraction_status'] == extraction['status'] == 'completed'
    assert summary['acquired_documents'] == 2
    assert acquisition['extraction_performed'] is False
    assert extraction['acquisition_manifest_sha256'] == hashlib.sha256(acquisition_bytes).hexdigest()
    assert [call['url'] for call in calls] == [REGISTRY, PREFIX + PROTOCOL, PREFIX + SAP]
    for record, expected_count in zip(extraction['documents'], (2, 1)):
        raw = originals[record['filename']]
        source = next(row['source'] for row in acquisition['documents'] if row['filename'] == record['filename'])
        pages_bytes = (output / record['pages_json_path']).read_bytes()
        pages = json.loads(pages_bytes)
        assert record['status'] == pages['status'] == 'complete'
        assert record['total_pages'] == record['extracted_pages'] == pages['page_count'] == expected_count
        assert [page['page_number'] for page in pages['pages']] == list(range(1, expected_count + 1))
        assert record['pages_json_sha256'] == hashlib.sha256(pages_bytes).hexdigest()
        assert record['source_sha256'] == pages['source_sha256'] == source['sha256'] == hashlib.sha256(raw).hexdigest()
        assert pages['registry_snapshot_sha256'] == hashlib.sha256(registry).hexdigest()
        assert pages['acquisition_source'] == source
        assert (output / ('documents/' + record['filename'])).read_bytes() == raw
    assert extraction['model_calls'] == extraction['ledger_writes'] == extraction['human_scientific_reviews'] == 0
    before = saved_tree(output)
    with pytest.raises(SystemExit) as caught:
        cli.main(args)
    assert caught.value.code == 1
    assert saved_tree(output) == before and len(calls) == 3


@pytest.mark.parametrize('kind', ['blank_page', 'corrupt_pdf'])
def test_acquired_pdf_is_not_fabricated_as_complete_extraction(tmp_path, monkeypatch, capsys, kind):
    cli = cli_module()
    raw = pdf_bytes([None]) if kind == 'blank_page' else b'%PDF-1.7\nSYNTHETIC corrupt PDF'
    calls = transport(monkeypatch, {REGISTRY: registry_bytes({PROTOCOL: raw}), PREFIX + PROTOCOL: raw})
    output = tmp_path / kind
    result = cli.main(['--nct', NCT, '--output-dir', str(output), '--document', PROTOCOL, '--extract'])
    summary = json.loads(capsys.readouterr().out)
    acquisition = json.loads((output / 'manifest.json').read_bytes())
    extraction = json.loads((output / 'extraction-manifest.json').read_bytes())
    row = extraction['documents'][0]
    pages = json.loads((output / row['pages_json_path']).read_bytes())
    assert result == 1 and len(calls) == 2
    assert summary['status'] == acquisition['status'] == 'complete'
    assert summary['extraction_status'] == extraction['status'] == 'incomplete'
    assert row['status'] == pages['status'] == ('partial' if kind == 'blank_page' else 'failed')
    assert row['total_pages'] == pages['page_count'] == (1 if kind == 'blank_page' else None)
    assert row['extracted_pages'] == len(pages['pages'])
    assert (output / 'documents' / PROTOCOL).read_bytes() == raw


@pytest.mark.parametrize('failure_at', ['registry', 'document'])
def test_failed_acquisition_has_incomplete_extraction_without_invented_pages(tmp_path, monkeypatch, capsys, failure_at):
    cli = cli_module()
    responses = {REGISTRY: registry_bytes({PROTOCOL: b'%PDF-1.7\nnot returned'})}
    failed_url = REGISTRY if failure_at == 'registry' else PREFIX + PROTOCOL
    responses[failed_url] = HTTPError(failed_url, 503, CANARY, {}, None)
    calls = transport(monkeypatch, responses)
    output = tmp_path / failure_at
    assert cli.main(['--nct', NCT, '--output-dir', str(output), '--document', PROTOCOL, '--extract']) == 1
    summary = json.loads(capsys.readouterr().out)
    acquisition_bytes = (output / 'manifest.json').read_bytes()
    acquisition = json.loads(acquisition_bytes)
    extraction = json.loads((output / 'extraction-manifest.json').read_bytes())
    assert summary['status'] == acquisition['status'] == ('failed' if failure_at == 'registry' else 'partial')
    assert summary['extraction_status'] == extraction['status'] == 'incomplete'
    assert summary['acquired_documents'] == 0 and len(calls) == (1 if failure_at == 'registry' else 2)
    failed = acquisition['registry'] if failure_at == 'registry' else acquisition['documents'][0]
    assert failed['error_http_status'] == 503 and failed['error_category'] == 'http_error'
    assert failed['source']['sha256'] is failed['source']['available_at'] is None
    assert extraction['acquisition_manifest_sha256'] == hashlib.sha256(acquisition_bytes).hexdigest()
    assert extraction['documents'] == [{'filename': PROTOCOL, 'status': 'not_acquired',
        'pages_json_path': None, 'pages_json_sha256': None}]
    assert not list((output / 'document-extractions').iterdir())
    assert CANARY not in json.dumps(saved_tree(output), default=lambda value: value.decode())


@pytest.mark.parametrize('original', ['document', 'registry'])
@pytest.mark.parametrize('fault', ['changed_bytes', 'missing'])
def test_extract_acquired_rechecks_all_originals_before_parsing(tmp_path, monkeypatch, original, fault):
    cli = cli_module()
    raw = pdf_bytes(['SYNTHETIC integrity boundary'])
    transport(monkeypatch, {REGISTRY: registry_bytes({PROTOCOL: raw}), PREFIX + PROTOCOL: raw})
    output = tmp_path / (original + '-' + fault)
    manifest = documents.acquire_documents(NCT, output, [PROTOCOL])
    acquisition_before = (output / 'manifest.json').read_bytes()
    path = output / ('documents/' + PROTOCOL if original == 'document' else 'registry.json')
    if fault == 'changed_bytes':
        data = path.read_bytes()
        path.write_bytes(bytes([data[0] ^ 1]) + data[1:])
    else:
        path.unlink()
    calls = []
    def no_parse(*args, **kwargs):
        calls.append(True)
        raise AssertionError('A changed original must be rejected before PDF parsing')
    monkeypatch.setattr(cli, 'extract_pdf_pages', no_parse)
    with pytest.raises((ValueError, OSError)):
        cli.extract_acquired(manifest, output)
    assert calls == [] and not (output / 'extraction-manifest.json').exists()
    assert (output / 'manifest.json').read_bytes() == acquisition_before


def test_changed_acquisition_manifest_is_rejected_without_extraction_output(tmp_path, monkeypatch):
    cli = cli_module()
    raw = pdf_bytes(['SYNTHETIC manifest identity'])
    transport(monkeypatch, {REGISTRY: registry_bytes({PROTOCOL: raw}), PREFIX + PROTOCOL: raw})
    output = tmp_path / 'manifest-changed'
    manifest = documents.acquire_documents(NCT, output, [PROTOCOL])
    saved = json.loads((output / 'manifest.json').read_bytes())
    saved['nct_id'] = 'NCT87654321'
    changed = json.dumps(saved).encode()
    (output / 'manifest.json').write_bytes(changed)
    with pytest.raises(ValueError, match='Acquisition manifest changed'):
        cli.extract_acquired(manifest, output)
    assert not (output / 'document-extractions').exists()
    assert not (output / 'extraction-manifest.json').exists()
    assert (output / 'manifest.json').read_bytes() == changed


def test_extraction_preserves_noncanonical_manifest_bytes_and_never_overwrites_results(tmp_path, monkeypatch):
    cli = cli_module()
    raw = pdf_bytes(['SYNTHETIC immutable extraction'])
    transport(monkeypatch, {REGISTRY: registry_bytes({PROTOCOL: raw}), PREFIX + PROTOCOL: raw})
    output = tmp_path / 'immutable'
    manifest = documents.acquire_documents(NCT, output, [PROTOCOL])
    original_bytes = json.dumps(manifest, indent=3).encode() + b'\n\n'
    (output / 'manifest.json').write_bytes(original_bytes)
    report = cli.extract_acquired(manifest, output)
    assert report['status'] == 'completed'
    assert report['acquisition_manifest_sha256'] == hashlib.sha256(original_bytes).hexdigest()
    assert (output / 'manifest.json').read_bytes() == original_bytes
    before = saved_tree(output)
    with pytest.raises(FileExistsError):
        cli.extract_acquired(manifest, output)
    assert saved_tree(output) == before


@pytest.mark.parametrize('original', ['registry', 'document'])
def test_extraction_rejects_symlinked_originals_without_following_external_bytes(tmp_path, monkeypatch, original):
    cli = cli_module()
    raw = pdf_bytes(['SYNTHETIC path boundary'])
    transport(monkeypatch, {REGISTRY: registry_bytes({PROTOCOL: raw}), PREFIX + PROTOCOL: raw})
    output = tmp_path / ('linked-' + original)
    manifest = documents.acquire_documents(NCT, output, [PROTOCOL])
    source = output / ('registry.json' if original == 'registry' else 'documents/' + PROTOCOL)
    outside = tmp_path / ('external-' + original)
    outside.write_bytes(source.read_bytes())
    source.unlink()
    source.symlink_to(outside)
    with pytest.raises(ValueError):
        cli.extract_acquired(manifest, output)
    assert source.is_symlink() and outside.read_bytes()
    assert not (output / 'extraction-manifest.json').exists()
