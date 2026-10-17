"""Synthetic contract tests; no live source calls or investment validation."""
import hashlib
import json
import sqlite3
import time

import pytest

from vbt.identity_collection import (
    DIRECTORY_URLS, NCT_API, collect, eligible_proposals, events, fetch, init_schema,
    runs, sources, validate_url,
)
from vbt.market import Market
from vbt.market_coverage import refresh_candidates
from vbt.sponsor_resolution import proposals, resolve


@pytest.fixture
def case(tmp_path):
    market = Market(tmp_path / 'workspace', tmp_path / 'state')
    refresh_candidates(market, [{'market': 'US', 'ticker': 'SYN',
        'name': 'Syntheticnova Therapeutics, Inc. - Common Stock',
        'classification': 'candidate', 'reason': 'Synthetic fixture only',
        'source': 'Synthetic fixture', 'evidence': [{'kind': 'synthetic'}]}])
    registry = {'protocolSection': {'identificationModule': {'nctId': 'NCT00000001'},
        'sponsorCollaboratorsModule': {'leadSponsor': {'name': 'Syntheticnova Therapeutics, Inc.'}}}}
    resolve(market, 'US:SYN', fetch=lambda url: json.dumps({'studies': [registry]}).encode())
    proposal = proposals(market)[0]
    directory = b'Symbol|Security Name|Test Issue|ETF\nSYN|Syntheticnova Therapeutics, Inc. - Common Stock|N|N\n'
    other = b'ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol\nSYN|Syntheticnova Therapeutics, Inc. Common Stock|N|SYN|N|100|N|SYN\n'
    return market, proposal, {DIRECTORY_URLS[0]: directory, DIRECTORY_URLS[1]: other,
                              NCT_API + 'NCT00000001': json.dumps(registry).encode()}


def test_atomic_success_links_two_archives_typed_review_and_no_ownership(case):
    m, p, documents = case
    calls, starts = [], []
    result = collect(m, p['id'], fetch=lambda url: (calls.append(url) or documents[url]), on_start=starts.append)
    assert result['status'] == 'succeeded' and result['company'] == 'US:SYN' and result['review_id']
    assert calls == [DIRECTORY_URLS[0], NCT_API + 'NCT00000001'] and starts == [result['run_id']]
    assert m.count('companies') == 1 and m.count('trials') == 0
    with m.db() as db:
        row = db.execute('SELECT * FROM sponsor_review_evidence WHERE review_id=?', (result['review_id'],)).fetchone()
        assert row['reviewer_type'] == 'automated_source_review'
        proof = json.loads(row['evidence_json'])
        assert not proof['human_sign_off'] and not proof['drug_ownership_verified']
        assert row['evidence_sha256'] == result['evidence_sha256']
    folder = m.root / 'identity_review' / result['run_id']
    assert (folder / 'directory-0.txt').read_bytes() == documents[DIRECTORY_URLS[0]]
    assert (folder / 'registry.json').read_bytes() == documents[NCT_API + 'NCT00000001']
    assert hashlib.sha256((folder / 'evidence.json').read_bytes()).hexdigest() == result['evidence_sha256']
    assert [e['event'] for e in events(m, result['run_id'])] == ['running', 'captured', 'captured', 'succeeded']
    with m.db() as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE identity_collection_events SET event='wrong'")


def test_otherlisted_uses_act_symbol_not_nasdaq_symbol(case):
    m, p, docs = case
    docs[DIRECTORY_URLS[0]] = b'Symbol|Security Name|Test Issue|ETF\nUNRELATED|Unrelated Company|N|N\n'
    docs[DIRECTORY_URLS[1]] = docs[DIRECTORY_URLS[1]].replace(b'|N|SYN\n', b'|N|DIFFERENT\n')
    calls = []
    result = collect(m, p['id'], fetch=lambda url: (calls.append(url) or docs[url]))
    assert result['status'] == 'succeeded'
    assert calls[:2] == list(DIRECTORY_URLS)
    proof = json.loads((m.root / 'identity_review' / result['run_id'] / 'evidence.json').read_text())
    assert proof['official_source_url'] == DIRECTORY_URLS[1]


def test_otherlisted_wrong_act_symbol_cannot_match_nasdaq_symbol(case):
    m, p, docs = case
    docs[DIRECTORY_URLS[0]] = b'Symbol|Security Name|Test Issue|ETF\n'
    docs[DIRECTORY_URLS[1]] = docs[DIRECTORY_URLS[1]].replace(b'\nSYN|', b'\nOTHER|')
    result = collect(m, p['id'], fetch=docs.__getitem__)
    assert result['status'] == 'failed' and 'absent' in result['error']
    assert m.count('companies') == 0


@pytest.mark.parametrize('change', ['wrong_name', 'duplicate', 'etf', 'test', 'bad_header'])
def test_directory_failure_is_not_bypassed_with_other_directory(case, change):
    m, p, docs = case
    raw = docs[DIRECTORY_URLS[0]]
    if change == 'wrong_name': raw = raw.replace(b'Syntheticnova', b'Unrelatednova')
    if change == 'duplicate': raw += raw.split(b'\n')[1] + b'\n'
    if change == 'etf': raw = raw.replace(b'|N|N\n', b'|N|Y\n')
    if change == 'test': raw = raw.replace(b'|N|N\n', b'|Y|N\n')
    if change == 'bad_header': raw = raw.replace(b'Security Name', b'Changed Name')
    docs[DIRECTORY_URLS[0]] = raw
    calls = []
    result = collect(m, p['id'], fetch=lambda url: (calls.append(url) or docs[url]))
    assert result['status'] == 'failed' and calls == [DIRECTORY_URLS[0]]
    assert m.count('companies') == 0 and proposals(m)[0]['status'] == 'pending'


@pytest.mark.parametrize('change', ['sponsor', 'nct', 'html'])
def test_registry_raw_schema_checked_even_when_capture_hash_matches(case, change):
    m, p, docs = case
    url = NCT_API + 'NCT00000001'
    if change == 'sponsor': docs[url] = docs[url].replace(b'Syntheticnova', b'Unrelatednova')
    elif change == 'nct': docs[url] = docs[url].replace(b'NCT00000001', b'NCT99999999')
    else: docs[url] = b'<html>HTTP access challenge</html>'
    result = collect(m, p['id'], fetch=docs.__getitem__)
    assert result['status'] == 'failed' and m.count('companies') == 0
    assert (m.root / 'identity_review' / result['run_id'] / 'registry.json').read_bytes() == docs[url]


def test_idempotent_success_never_refetches_or_duplicates_review(case):
    m, p, docs = case
    first = collect(m, p['id'], fetch=docs.__getitem__)
    second = collect(m, p['id'], fetch=lambda url: pytest.fail('Successful identity is idempotent'))
    assert first == second and len(runs(m)) == 1
    with m.db() as db:
        assert db.execute('SELECT count(*) FROM sponsor_proposal_reviews').fetchone()[0] == 1


def test_tampered_success_cannot_be_reused_as_valid_completion(case):
    m, p, docs = case
    first = collect(m, p['id'], fetch=docs.__getitem__)
    (m.root / 'identity_review' / first['run_id'] / 'registry.json').write_text('tampered')
    second = collect(m, p['id'], fetch=lambda url: pytest.fail('Handled proposal must not reconfigure'))
    assert second['status'] == 'blocked' and second['run_id'] != first['run_id']
    assert m.count('companies') == 1


@pytest.mark.parametrize('stage', ['before', 'after_directory', 'after_registry', 'inside_review'])
def test_cancellation_never_publishes_and_keeps_capture_history(case, monkeypatch, stage):
    import vbt.identity_collection as module
    m, p, docs = case
    cancelled = [stage == 'before']
    calls = []
    def request(url):
        calls.append(url)
        if (stage == 'after_directory' and len(calls) == 1) or (stage == 'after_registry' and len(calls) == 2):
            cancelled[0] = True
        return docs[url]
    if stage == 'inside_review':
        original = module.review_in_transaction
        def review(*args, **kwargs):
            result = original(*args, **kwargs)
            cancelled[0] = True
            return result
        monkeypatch.setattr(module, 'review_in_transaction', review)
    result = collect(m, p['id'], fetch=request, cancelled=lambda: cancelled[0])
    assert result['status'] == 'cancelled' and m.count('companies') == 0
    assert proposals(m)[0]['status'] == 'pending'
    with m.db() as db:
        assert db.execute('SELECT count(*) FROM sponsor_proposal_reviews').fetchone()[0] == 0
    assert len(calls) == {'before': 0, 'after_directory': 1, 'after_registry': 2, 'inside_review': 2}[stage]


def test_owner_loss_after_capture_fails_closed_without_deleting_new_owner(case):
    m, p, docs = case
    key = 'identity:' + p['id']
    def request(url):
        if url.startswith(NCT_API):
            with m.db() as db:
                db.execute('UPDATE leases SET owner=? WHERE key=?', ('new-owner', key))
        return docs[url]
    result = collect(m, p['id'], fetch=request)
    assert result['status'] == 'superseded' and m.count('companies') == 0
    with m.db() as db:
        assert db.execute('SELECT owner FROM leases WHERE key=?', (key,)).fetchone()[0] == 'new-owner'


def test_candidate_change_during_fetch_is_not_published(case):
    m, p, docs = case
    def request(url):
        if url.startswith(NCT_API):
            with m.db() as db: db.execute("UPDATE issuer_candidates SET content_hash='changed'")
        return docs[url]
    result = collect(m, p['id'], fetch=request)
    assert result['status'] == 'failed' and 'Candidate changed' in result['error']
    assert m.count('companies') == 0


@pytest.mark.parametrize('classification', ['uncertain', 'excluded', 'unclassified', 'drug_developer_candidate'])
def test_uncertain_classification_requires_human_review(case, classification):
    m, p, docs = case
    with m.db() as db: db.execute('UPDATE issuer_candidates SET classification=?', (classification,))
    assert eligible_proposals(m) == []
    result = collect(m, p['id'], fetch=lambda url: pytest.fail('Ineligible candidate must not fetch'))
    assert result['status'] == 'blocked' and 'human' in result['error']
    assert m.count('companies') == 0


@pytest.mark.parametrize('field,value', [('match_type', 'distinctive_token'), ('candidate_hash', 'old'),
    ('source_url', 'https://evil.example/NCT00000001'), ('evidence_nct', '["NCT1"]')])
def test_unsafe_proposal_is_blocked_before_network(case, field, value):
    m, p, docs = case
    with m.db() as db: db.execute('UPDATE sponsor_proposals SET ' + field + '=?', (value,))
    result = collect(m, p['id'], fetch=lambda url: pytest.fail('Unsafe proposal must not fetch'))
    assert result['status'] == 'blocked' and m.count('companies') == 0


def test_recovery_interrupts_lost_running_and_new_attempt_retries(case):
    m, p, docs = case
    init_schema(m)
    with m.db() as db:
        db.execute('''INSERT INTO identity_collection_runs
          (id,proposal_id,candidate_id,candidate_hash,started,status,lease_owner)
          VALUES (?,?,?,?,?,'running','lost-owner')''', ('a' * 32, p['id'], p['candidate_id'], p['candidate_hash'], '2026-10-17T00:00:00+00:00'))
    old = runs(m)[0]
    assert old['status'] == 'interrupted' and old['recovered']
    result = collect(m, p['id'], fetch=docs.__getitem__)
    assert result['status'] == 'succeeded' and result['run_id'] != old['id']
    assert {row['status'] for row in runs(m)} == {'interrupted', 'succeeded'}


def test_recovery_respects_live_exact_owner(case):
    m, p, docs = case
    init_schema(m)
    assert m.claim('identity:' + p['id'])
    owner = m.lease_tokens()['identity:' + p['id']]
    with m.db() as db:
        db.execute('''INSERT INTO identity_collection_runs
          (id,proposal_id,candidate_id,candidate_hash,started,status,lease_owner)
          VALUES (?,?,?,?,?,'running',?)''', ('b' * 32, p['id'], p['candidate_id'], p['candidate_hash'], '2026-10-17T00:00:00+00:00', owner))
    assert runs(m)[0]['status'] == 'running'
    result = collect(m, p['id'], fetch=lambda url: pytest.fail('Active owner must not be bypassed'))
    assert result['status'] == 'busy'


def test_recovery_only_completes_accepted_review_with_exact_archived_proof(case):
    m, p, docs = case
    first = collect(m, p['id'], fetch=docs.__getitem__)
    with m.db() as db:
        db.execute("UPDATE identity_collection_runs SET status='running',finished=NULL WHERE id=?", (first['id'],))
    recovered = runs(m)[0]
    assert recovered['status'] == 'succeeded' and recovered['recovered'] and recovered['review_id'] == first['review_id']
    assert events(m, first['id'])[-1]['event'] == 'recovered_succeeded'


def test_run_and_proposal_source_pagination_is_bounded(case):
    m, p, docs = case
    assert sources(m)[0]['id'] == p['id'] and eligible_proposals(m)[0]['candidate_id'] == 'US:SYN'
    assert eligible_proposals(m, offset=1) == []
    with pytest.raises(ValueError): eligible_proposals(m, limit=501)
    with pytest.raises(ValueError): runs(m, limit=0)
    with pytest.raises(ValueError): collect(m, '../../escape')


@pytest.mark.parametrize('url', ['http://clinicaltrials.gov/api/v2/studies/NCT00000001',
    NCT_API + 'NCT00000001?query=unexpected', NCT_API + 'NCT1',
    'https://user:password@clinicaltrials.gov/api/v2/studies/NCT00000001',
    DIRECTORY_URLS[0] + '?redirect=bad', 'https://www.nasdaqtrader.com/arbitrary'])
def test_fetch_allowlist_rejects_before_subprocess(url, monkeypatch):
    monkeypatch.setattr('subprocess.run', lambda *a, **kw: pytest.fail('Rejected URL made a request'))
    with pytest.raises(ValueError): fetch(url)


def test_real_fetch_contract_uses_tls_no_redirects_and_strict_http(monkeypatch):
    calls = []
    class Result:
        returncode = 0
        stdout = b'raw-body\n200'
        stderr = b''
    def request(args, **kwargs):
        calls.append((args, kwargs));return Result()
    monkeypatch.setattr('subprocess.run', request)
    assert fetch(DIRECTORY_URLS[0]) == b'raw-body'
    args, kwargs = calls[0]
    assert args[1] == '--disable' and '--location' not in args and '--insecure' not in args
    assert args[args.index('--max-redirs') + 1] == '0'
    assert 'OPENAI_API_KEY' not in kwargs['env'] and kwargs['timeout'] == 100
    Result.stdout = b'access challenge\n302'
    with pytest.raises(ValueError): fetch(DIRECTORY_URLS[0])
