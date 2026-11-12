"""Real isolated CLI subprocesses; no runtime initialization, providers or human credentials."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from arena_scientific_fixture import scientific_acceptance_document
from emeris_live.arena_result_assurance import result_assurance
from test_arena_core import registered
from test_arena_result_reviews import result

SCRIPT = Path(__file__).parents[1] / 'scripts/arena_scientific_acceptance.py'


@pytest.fixture
def prepared(tmp_path):
    ledger, target, _ = registered(tmp_path / 'runtime')
    body = result(target)
    for reviewer in ('one', 'two'):
        ledger.record_result(target['id'], body, reviewer_id=reviewer)
    document = scientific_acceptance_document(ledger, target, tmp_path)
    path = tmp_path / 'private-document.json'
    path.write_text(json.dumps(document))
    return ledger, target, document, path


def cli(tmp_path, *arguments):
    env = {'PATH': os.defpath, 'LANG': 'C.UTF-8', 'PYTHONDONTWRITEBYTECODE': '1',
           'EMERIS_ENABLE_ARENA_LLM': 'false', 'EMERIS_ENABLE_USER_LLM': 'false'}
    return subprocess.run([sys.executable, '-B', str(SCRIPT), *map(str, arguments)], cwd=tmp_path,
                          env=env, capture_output=True, text=True, timeout=20)


def test_cli_accept_list_revoke_with_private_originals_and_no_sensitive_output(tmp_path, prepared):
    ledger, target, document, path = prepared
    # Relative file references are resolved from the document, not the subprocess cwd.
    for review in document['reviews']:
        for key in ('qualification_evidence_file', 'independence_statement_file'):
            review[key] = os.path.relpath(review[key], path.parent)
    document['source_evidence_file'] = os.path.relpath(document['source_evidence_file'], path.parent)
    path.write_text(json.dumps(document))
    before = ledger.events()
    response = cli(tmp_path, 'accept', '--data-dir', ledger.path.parent, '--document', path)
    assert response.returncode == 0, response.stderr
    accepted = json.loads(response.stdout)
    assert accepted['status'] == 'scientific_acceptance_recorded'
    assert result_assurance(ledger.snapshot()['targets'][0])['scoring_eligible'] is True
    assert ledger.events()[:len(before)] == before
    listing = cli(tmp_path, '--data-dir', ledger.path.parent, 'list')
    assert listing.returncode == 0 and len(json.loads(listing.stdout)['events']) == 1
    assert not any(token in response.stdout + listing.stdout for token in
                   (str(tmp_path), 'PRIVATE SYNTHETIC', document['acceptor'], 'principal', 'qualification_check_note'))
    revoked = cli(tmp_path, 'revoke', '--data-dir', ledger.path.parent, '--target', target['id'],
                  '--acceptor', 'Synthetic responsible operator', '--note', 'Synthetic withdrawal for an isolated test',
                  '--supersedes', accepted['event_sha256'])
    assert revoked.returncode == 0, revoked.stderr
    assert result_assurance(ledger.snapshot()['targets'][0])['scoring_eligible'] is False


def test_prepare_is_read_only_and_exclusive(tmp_path, prepared):
    ledger, target, _, _ = prepared
    before = ledger.events()
    output = tmp_path / 'worksheet.json'
    response = cli(tmp_path, 'prepare', '--data-dir', ledger.path.parent, '--target', target['id'], '--output', output)
    assert response.returncode == 0, response.stderr
    template = json.loads(output.read_bytes())
    assert template['acceptor'] is None and template['source_evidence_file'] is None
    assert all(value is False for row in template['reviews'] for value in row['confirmations'].values())
    assert output.stat().st_mode & 0o777 == 0o600 and ledger.events() == before
    assert cli(tmp_path, 'prepare', '--data-dir', ledger.path.parent, '--target', target['id'], '--output', output).returncode != 0
    assert json.loads(output.read_bytes()) == template


@pytest.mark.parametrize('fault', ['missing_runtime', 'duplicate_json', 'unknown_fields', 'source_mismatch', 'symlink', 'fifo'])
def test_cli_failures_are_closed_and_do_not_echo_private_inputs(tmp_path, prepared, fault):
    ledger, _, document, path = prepared
    before = ledger.events()
    data = ledger.path.parent
    if fault == 'missing_runtime':
        data = tmp_path / 'not-created-runtime'
    elif fault == 'duplicate_json':
        path.write_text('{"acceptor":"PRIVATE INPUT","acceptor":"private duplicate"}')
    elif fault == 'unknown_fields':
        document['scientific_credentials_verified'] = True
        path.write_text(json.dumps(document))
    elif fault == 'source_mismatch':
        Path(document['source_evidence_file']).write_bytes(b'PRIVATE WRONG SOURCE')
    elif fault == 'symlink':
        link = tmp_path / 'document-link'
        link.symlink_to(path)
        path = link
    else:
        path = tmp_path / 'document-fifo'
        os.mkfifo(path)
    response = cli(tmp_path, 'accept', '--data-dir', data, '--document', path)
    assert response.returncode != 0 and response.stdout == ''
    assert 'operation rejected' in response.stderr
    assert str(tmp_path) not in response.stderr and 'PRIVATE' not in response.stderr
    assert ledger.events() == before
    if fault == 'missing_runtime':
        assert not data.exists()
