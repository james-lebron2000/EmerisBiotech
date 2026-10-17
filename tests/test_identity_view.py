"""Independent identity archive/UI review using synthetic real-collector runs."""
import hashlib
import json
import shutil
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest

from vbt.identity_collection import DIRECTORY_URLS, NCT_API, collect
from vbt.identity_view import evidence_file, render
from vbt.market import Market
from vbt.market_coverage import refresh_candidates
from vbt.sponsor_resolution import proposals, resolve
from vbt.workbench import Workbench, handler


@pytest.fixture
def case(tmp_path):
    m = Market(tmp_path / 'workspace', tmp_path / 'state')
    refresh_candidates(m, [{'market': 'US', 'ticker': 'SYN',
        'name': 'Syntheticnova Therapeutics Inc. - Common Stock', 'classification': 'candidate',
        'reason': 'Independent synthetic UI verification fixture only', 'source': 'Synthetic fixture',
        'evidence': [{'kind': 'synthetic'}]}])
    record = {'protocolSection': {'identificationModule': {'nctId': 'NCT00000001'},
        'sponsorCollaboratorsModule': {'leadSponsor': {'name': 'Syntheticnova Therapeutics Inc.'}}},
        'synthetic_note': '<script>window.syntheticEvidenceShouldNotExecute=true</script>'}
    registry = json.dumps(record).encode()
    resolve(m, 'US:SYN', fetch=lambda url: json.dumps({'studies': [record]}).encode())
    p = proposals(m)[0]
    # A real original BOM must survive the HTTP download byte for byte.
    exchange = b'\xef\xbb\xbfSymbol|Security Name|Test Issue|ETF|Synthetic Note\nSYN|Syntheticnova Therapeutics Inc. - Common Stock|N|N|<img src=x onerror=alert(1)>\n'
    docs = {DIRECTORY_URLS[0]: exchange, NCT_API + 'NCT00000001': registry}
    result = collect(m, p['id'], fetch=docs.__getitem__)
    assert result['status'] == 'succeeded'
    folder = m.root / 'identity_review' / result['run_id']
    return SimpleNamespace(m=m, p=p, result=result, folder=folder,
                           proof_path=folder / 'evidence.json', exchange=exchange, registry=registry)


def proof(case):
    return json.loads(case.proof_path.read_bytes())


def rewrite_proof(case, value):
    """Fault-inject a ledger-consistent malformed/old snapshot, not fresh proof."""
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    case.proof_path.write_bytes(raw)
    with case.m.db() as db:
        db.execute('UPDATE identity_collection_runs SET evidence_sha256=? WHERE id=?',
                   (hashlib.sha256(raw).hexdigest(), case.result['run_id']))
    return raw


def manifest(case, key):
    path = case.folder / ('exchange_manifest.json' if key == 'exchange' else 'capture_manifest.json')
    value = json.loads(path.read_bytes())
    return path, value if key == 'exchange' else value[0]


def rewrite_manifest(case, key, value):
    path, _ = manifest(case, key)
    path.write_text(json.dumps(value if key == 'exchange' else [value]))


@pytest.fixture
def http(case):
    app = Workbench(case.m.root.parent.parent, case.m.state)
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler(app))
    thread = threading.Thread(target=server.serve_forever, daemon=True);thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    try:
        yield base
    finally:
        server.shutdown();server.server_close();thread.join(timeout=2)


@pytest.mark.parametrize('kind', ['proof', 'exchange', 'registry'])
def test_real_collector_archive_bytes_are_readable(case, kind):
    raw, mime = evidence_file(case.m, case.result['run_id'], kind)
    expected = case.proof_path.read_bytes() if kind == 'proof' else getattr(case, kind)
    assert raw == expected
    assert mime == ('text/plain; charset=utf-8' if kind == 'exchange' else 'application/json; charset=utf-8')


@pytest.mark.parametrize('filename', ['evidence.json', 'directory-0.txt', 'registry.json'])
def test_any_source_tamper_blocks_even_the_proof_download(case, filename):
    (case.folder / filename).write_bytes(b'tampered')
    with pytest.raises(ValueError): evidence_file(case.m, case.result['run_id'], 'proof')


@pytest.mark.parametrize('key,field,value', [
    ('exchange', 'url', DIRECTORY_URLS[1]), ('registry', 'url', NCT_API + 'NCT99999999'),
    ('exchange', 'sha256', 'b' * 64), ('registry', 'sha256', 'b' * 64),
    ('exchange', 'bytes', 1), ('registry', 'bytes', True),
    ('exchange', 'status', 202), ('registry', 'status', 403),
    ('exchange', 'fetched_at', '2000-01-01T00:00:00+00:00'),
    ('registry', 'fetched_at', '2000-01-01T00:00:00+00:00'),
    ('exchange', 'kind', 'registry'), ('registry', 'kind', 'exchange'),
    ('registry', 'ticker', 'OTHER'),
])
def test_manifest_metadata_tamper_is_detected_without_changing_proof_hash(case, key, field, value):
    _, record = manifest(case, key);record[field] = value;rewrite_manifest(case, key, record)
    with pytest.raises(ValueError): evidence_file(case.m, case.result['run_id'], 'proof')


@pytest.mark.parametrize('key,field', [('exchange', 'url'), ('registry', 'path'),
    ('exchange', 'bytes'), ('registry', 'sha256'), ('exchange', 'status'),
    ('exchange', 'fetched_at'), ('registry', 'fetched_at'), ('registry', 'ticker'), ('registry', 'kind')])
def test_manifest_missing_required_fields_fail_closed(case, key, field):
    _, record = manifest(case, key);record.pop(field);rewrite_manifest(case, key, record)
    with pytest.raises(ValueError): evidence_file(case.m, case.result['run_id'], 'proof')


@pytest.mark.parametrize('key', ['exchange', 'registry'])
def test_matching_missing_timestamps_in_proof_and_manifest_are_not_valid(case, key):
    document = proof(case);document.pop(key + '_fetched_at');rewrite_proof(case, document)
    _, record = manifest(case, key);record.pop('fetched_at');rewrite_manifest(case, key, record)
    with pytest.raises(ValueError): evidence_file(case.m, case.result['run_id'], 'proof')


@pytest.mark.parametrize('key', ['exchange', 'registry'])
def test_matching_invalid_timestamp_strings_do_not_prove_provenance(case, key):
    document = proof(case);document[key + '_fetched_at'] = 'not-a-timestamp';rewrite_proof(case, document)
    _, record = manifest(case, key);record['fetched_at'] = 'not-a-timestamp';rewrite_manifest(case, key, record)
    with pytest.raises(ValueError): evidence_file(case.m, case.result['run_id'], 'proof')


def test_missing_ticker_in_both_proof_and_manifest_is_rejected(case):
    document = proof(case);document.pop('ticker');rewrite_proof(case, document)
    _, record = manifest(case, 'registry');record.pop('ticker');rewrite_manifest(case, 'registry', record)
    with pytest.raises(ValueError): evidence_file(case.m, case.result['run_id'], 'proof')


def test_matching_wrong_ticker_cannot_rebind_a_run_to_another_security(case):
    document = proof(case);document['ticker'] = 'OTHER';rewrite_proof(case, document)
    _, record = manifest(case, 'registry');record['ticker'] = 'OTHER';rewrite_manifest(case, 'registry', record)
    with pytest.raises(ValueError): evidence_file(case.m, case.result['run_id'], 'proof')


@pytest.mark.parametrize('key,value', [('collector_run_id', 'f' * 32), ('proposal_id', 'f' * 32),
    ('candidate_id', 'US:OTHER'), ('candidate_hash', 'incorrect-hash'), ('scope', 'drug_ownership_verified'),
    ('status', 'pending'), ('human_sign_off', True), ('drug_ownership_verified', True),
    ('reviewer_type', 'human_declared')])
def test_ledger_consistent_proof_still_requires_correct_run_binding(case, key, value):
    document = proof(case);document[key] = value;rewrite_proof(case, document)
    with pytest.raises(ValueError): evidence_file(case.m, case.result['run_id'], 'proof')


@pytest.mark.parametrize('key', ['collector_run_id', 'proposal_id', 'candidate_id', 'candidate_hash',
    'scope', 'status', 'human_sign_off', 'drug_ownership_verified', 'reviewer_type',
    'exchange_sha256', 'registry_sha256'])
def test_missing_proof_binding_or_source_hash_is_rejected(case, key):
    document = proof(case);document.pop(key);rewrite_proof(case, document)
    with pytest.raises(ValueError): evidence_file(case.m, case.result['run_id'], 'proof')


@pytest.mark.parametrize('value', [None, '', 'not-a-hash', 'A' * 64, 'b' * 63])
def test_missing_or_malformed_trusted_proof_hash_is_rejected(case, value):
    with case.m.db() as db:
        db.execute('UPDATE identity_collection_runs SET evidence_sha256=? WHERE id=?', (value, case.result['run_id']))
    with pytest.raises(ValueError): evidence_file(case.m, case.result['run_id'], 'proof')


@pytest.mark.parametrize('target', ['proof', 'exchange_manifest', 'registry_manifest', 'exchange_source', 'registry_source'])
def test_paths_must_stay_inside_the_recorded_run(case, target):
    prefix = 'artifacts/market/identity_review/' + case.result['run_id'] + '/'
    escape = prefix + '../outside.json'
    if target == 'proof':
        with case.m.db() as db:
            db.execute('UPDATE identity_collection_runs SET evidence_path=? WHERE id=?', (escape, case.result['run_id']))
    elif target.endswith('manifest'):
        document = proof(case)
        document['exchange_capture_manifest' if target.startswith('exchange') else 'evidence_capture_manifest'] = escape
        rewrite_proof(case, document)
    else:
        key = target.split('_')[0]
        _, record = manifest(case, key);record['path'] = escape;rewrite_manifest(case, key, record)
    with pytest.raises(ValueError): evidence_file(case.m, case.result['run_id'], 'proof')


def test_absolute_proof_path_cannot_escape_prefix_validation(case):
    with case.m.db() as db:
        db.execute('UPDATE identity_collection_runs SET evidence_path=? WHERE id=?', (str(case.proof_path), case.result['run_id']))
    with pytest.raises(ValueError): evidence_file(case.m, case.result['run_id'], 'proof')


def test_source_symlink_to_another_run_is_rejected_even_for_identical_bytes(case):
    other = case.folder.parent / ('f' * 32);other.mkdir()
    target = other / 'registry.json';target.write_bytes(case.registry)
    path = case.folder / 'registry.json';path.unlink();path.symlink_to(target)
    with pytest.raises(ValueError): evidence_file(case.m, case.result['run_id'], 'registry')


def test_whole_run_folder_symlink_cannot_alias_another_run(case):
    other = case.folder.parent / ('f' * 32)
    shutil.copytree(case.folder, other)
    shutil.rmtree(case.folder);case.folder.symlink_to(other, target_is_directory=True)
    with pytest.raises(ValueError): evidence_file(case.m, case.result['run_id'], 'proof')


@pytest.mark.parametrize('filename,value', [('exchange_manifest.json', []), ('capture_manifest.json', {}),
    ('capture_manifest.json', []), ('capture_manifest.json', [{}, {}])])
def test_wrong_manifest_containers_are_rejected(case, filename, value):
    (case.folder / filename).write_text(json.dumps(value))
    with pytest.raises(ValueError): evidence_file(case.m, case.result['run_id'], 'proof')


def test_historical_proof_remains_readable_without_reapproving_current_identity(case):
    old = '2000-01-01T00:00:00+00:00'
    document = proof(case)
    for field in ('checked_at', 'exchange_fetched_at', 'registry_fetched_at'): document[field] = old
    rewrite_proof(case, document)
    for key in ('exchange', 'registry'):
        _, record = manifest(case, key);record['fetched_at'] = old;rewrite_manifest(case, key, record)
    with case.m.db() as db:
        db.execute('UPDATE issuer_candidates SET content_hash=?,classification=?', ('new-current-version', 'uncertain'))
        db.execute('UPDATE identity_collection_runs SET started=?,finished=? WHERE id=?', (old, old, case.result['run_id']))
        before = db.execute('SELECT count(*) FROM sponsor_proposal_reviews').fetchone()[0]
    for kind in ('proof', 'exchange', 'registry'):
        assert evidence_file(case.m, case.result['run_id'], kind)[0]
    with case.m.db() as db:
        assert db.execute('SELECT count(*) FROM sponsor_proposal_reviews').fetchone()[0] == before
        assert db.execute('SELECT content_hash FROM issuer_candidates').fetchone()[0] == 'new-current-version'


@pytest.mark.parametrize('run_id,kind', [('f' * 32, 'proof'), ('../escape', 'proof'), ('', 'proof'),
    ('valid', 'proof'), (None, 'proof'), ('f' * 32, '../source.html')])
def test_unknown_run_and_invalid_request_are_rejected(case, run_id, kind):
    with pytest.raises(ValueError): evidence_file(case.m, run_id, kind)


def test_empty_market_has_no_attachments_or_implicit_identity_schema(tmp_path):
    m = Market(tmp_path / 'workspace', tmp_path / 'state')
    with pytest.raises(ValueError): evidence_file(m, 'f' * 32, 'proof')
    with m.db() as db:
        assert db.execute("SELECT 1 FROM sqlite_master WHERE name='identity_collection_runs'").fetchone() is None


def test_failed_run_does_not_offer_archives_as_completed_proof(case):
    with case.m.db() as db:
        db.execute("UPDATE identity_collection_runs SET status='failed' WHERE id=?", (case.result['run_id'],))
    with pytest.raises(ValueError): evidence_file(case.m, case.result['run_id'], 'proof')


def test_http_returns_original_bom_and_safe_json_text_types(case, http):
    for kind, expected in [('proof', case.proof_path.read_bytes()), ('exchange', case.exchange), ('registry', case.registry)]:
        url = http + '/market/identity-evidence?' + urlencode({'id': case.result['run_id'], 'file': kind})
        with urllib.request.urlopen(url) as response:
            raw = response.read()
            assert response.status == 200 and raw == expected
            assert int(response.headers['Content-Length']) == len(expected)
            assert response.headers['X-Content-Type-Options'] == 'nosniff'
            assert response.headers['Cache-Control'] == 'no-store'
            assert "sandbox;" in response.headers['Content-Security-Policy']
            assert "default-src 'none'" in response.headers['Content-Security-Policy']
            assert response.headers.get_content_type() == ('text/plain' if kind == 'exchange' else 'application/json')
    assert case.exchange.startswith(b'\xef\xbb\xbf') and b'<script>' in case.registry


def test_http_bad_request_and_tamper_fail_closed(case, http):
    for query in ({}, {'id': case.result['run_id'], 'file': '../outside.html'}, {'id': 'f' * 32, 'file': 'proof'}):
        with pytest.raises(urllib.error.HTTPError) as ex:
            urllib.request.urlopen(http + '/market/identity-evidence?' + urlencode(query))
        assert ex.value.code == 404
    (case.folder / 'registry.json').write_bytes(b'tampered')
    with pytest.raises(urllib.error.HTTPError) as ex:
        urllib.request.urlopen(http + '/market/identity-evidence?' + urlencode({'id': case.result['run_id'], 'file': 'proof'}))
    assert ex.value.code == 404
    assert b'tampered' not in ex.value.read()


def test_http_host_guard_blocks_archive_delivery(case, http):
    url = http + '/market/identity-evidence?' + urlencode({'id': case.result['run_id'], 'file': 'registry'})
    with pytest.raises(urllib.error.HTTPError) as ex:
        urllib.request.urlopen(urllib.request.Request(url, headers={'Host': 'evil.example'}))
    assert ex.value.code == 403


def test_render_escapes_history_and_events_and_explains_scope(case):
    with case.m.db() as db:
        db.execute('UPDATE identity_collection_runs SET error=? WHERE id=?', ('<img src=x onerror=alert(1)>', case.result['run_id']))
        db.execute('INSERT INTO identity_collection_events VALUES (?,?,?,?,?)',
                   ('synthetic-event', case.result['run_id'], '2000-01-01T00:00:00+00:00', '<script>unsafe</script>', '<img src=x>'))
    text = render(SimpleNamespace(token='synthetic-token'), case.m, {'identity_run': case.result['run_id']})
    assert '来源名称核对完成' in text and '非人工签署' in text and '完整管线验收或科学审批' in text
    assert '<script>unsafe</script>' not in text and '&lt;script&gt;unsafe&lt;/script&gt;' in text
    assert '<img src=x' not in text and '&lt;img src=x' in text
    assert '/market/identity-evidence?' in text


def test_render_empty_history_is_explicitly_unobserved(tmp_path):
    m = Market(tmp_path / 'workspace', tmp_path / 'state')
    text = render(SimpleNamespace(token='synthetic-token'), m, {})
    assert '尚无自动来源核对运行记录' in text and '排队数量不能当作核对完成数量' in text


def test_archive_namespace_symlink_cannot_redirect_outside_market_root(case, tmp_path):
    namespace = case.folder.parent
    outside = tmp_path / 'outside-market-archive'
    shutil.move(str(namespace), str(outside))
    namespace.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError): evidence_file(case.m, case.result['run_id'], 'proof')
