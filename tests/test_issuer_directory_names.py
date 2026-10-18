"""Synthetic engineering checks; names never certify pipelines or ownership."""
import hashlib
import io
import json
import sqlite3
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

import pytest

from vbt import issuer_directory_names as names
from vbt.market import Market
from vbt.market_coverage import refresh_candidates
from vbt.market_sources import URLS


def sse(source='SH', count=2, **changes):
    rows = []
    for i in range(count):
        row = dict(A_STOCK_CODE=f'{600001+i if source == "SH" else 688001+i:06}',
                   COMPANY_ABBR=f'合成医药{i}', FULL_NAME=f'合成医药{i}股份有限公司',
                   FULL_NAME_IN_ENGLISH=f'Synthetic Pharma {i} Co., Ltd.', CSRC_CODE_DESC='制造业')
        row.update(changes)
        rows.append(row)
    return json.dumps({'result': rows, 'pageHelp': {'total': count}}, ensure_ascii=False).encode()


def xlsx(count=2, *, missing_header=False):
    header = ['A股代码', 'A股简称', '公司全称', '英文名称', '公司网址', '所属行业']
    if missing_header:
        header[3] = 'English short name'
    rows = [header] + [[f'{i+1:06}', f'合成医药{i}', f'合成医药{i}股份有限公司',
                       f'Synthetic Pharma {i} Co., Ltd.', 'www.example.test', 'C 制造业'] for i in range(count)]
    xml = '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
    for number, row in enumerate(rows, 1):
        xml += f'<row r="{number}">' + ''.join(
            f'<c r="{chr(65+i)}{number}" t="inlineStr"><is><t>{escape(value)}</t></is></c>'
            for i, value in enumerate(row)) + '</row>'
    xml += '</sheetData></worksheet>'
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('xl/worksheets/sheet1.xml', xml)
    return buffer.getvalue()


def seed(market, source='SH', raw=None):
    raw = raw if raw is not None else xlsx() if source == 'SZ' else sse(source)
    rid = market.refresh_universe(source, fetch=lambda url: raw)
    assert next(r for r in market.rows('runs') if r['id'] == rid)['status'] == 'succeeded'
    rows = [r for r in market.rows('listings') if r['source'] == source]
    refresh_candidates(market, [dict(market='A', ticker=r['ticker'], name=r['name'],
        classification='candidate', source=source, reason='Synthetic fixture only',
        evidence=[{'kind': 'listing_metadata', 'source': source, 'name': r['name']}]) for r in rows])
    return rid


def candidates(market):
    with market.db() as db:
        return [dict(r) for r in db.execute('SELECT * FROM issuer_candidates ORDER BY id')]


@pytest.fixture
def market(tmp_path):
    return Market(tmp_path / 'workspace', tmp_path / 'state')


@pytest.mark.parametrize('source', names.SOURCES)
def test_named_fields_enrich_and_reverified_evidence_are_query_seeds_only(market, source):
    rid = seed(market, source)
    before = {key: market.rows(key) for key in ('listings', 'companies', 'trials')}
    result = names.enrich(market, source)
    assert result['status'] == 'succeeded' and result['added'] == 2
    assert names.enrich(market, source)['unchanged'] == 2
    rows = candidates(market)
    assert names.query_names(market, rows) == {r['id']: f'Synthetic Pharma {i} Co., Ltd.' for i, r in enumerate(rows)}
    evidence = names.query_evidence(market, rows[0])
    assert evidence['source_run_id'] == rid and evidence['source'] == source
    assert evidence['candidate_hash'] == rows[0]['content_hash']
    assert evidence['receipt_id'] == evidence['ledger_id'] and len(evidence['raw_sha256']) == 64
    assert evidence['registry_alias_proven'] is False and evidence['human_sign_off'] is False
    assert evidence['drug_ownership_verified'] is False
    assert names.query_name(market, rows[0]) == evidence['english_name']
    assert {key: market.rows(key) for key in before} == before


def test_query_before_enrich_never_creates_schema(market):
    seed(market)
    with market.db() as db:
        before = list(db.iterdump())
    assert names.query_names(market, candidates(market)) == {}
    assert names.query_evidence(market, candidates(market)[0]) is None
    with market.db() as db:
        assert list(db.iterdump()) == before


@pytest.mark.parametrize('field,value', [('url', 'https://evil.test/directory'), ('sha256', '0'*64),
    ('bytes', 0), ('bytes', True), ('method', 'POST'), ('status', 302), ('retrieved_at', None),
    ('retrieved_at', '2026-10-17T01:00:00')])
def test_bad_capture_metadata_blocks_source_and_preserves_old_receipts(market, field, value):
    rid = seed(market)
    names.enrich(market, 'SH')
    path = market.root / 'captures' / rid / '0.json'
    metadata = json.loads(path.read_text()); metadata[field] = value; path.write_text(json.dumps(metadata))
    assert names.query_name(market, candidates(market)[0]) is None
    assert names.enrich(market, 'SH')['status'] == 'blocked'
    with market.db() as db:
        assert db.execute('SELECT count(*) FROM issuer_directory_name_receipts').fetchone()[0] == 2


def test_raw_tamper_and_truncation_reject_without_publishing_new_names(market):
    rid = seed(market)
    names.enrich(market, 'SH')
    path = market.root / 'captures' / rid / '0.bin'
    path.write_bytes(path.read_bytes()[:-1])
    assert names.query_names(market, candidates(market)) == {}
    assert names.enrich(market, 'SH')['added'] == 0


@pytest.mark.parametrize('body', [b'[]', b'null'])
def test_changed_json_envelope_is_blocked_not_a_query_exception(market, body):
    rid = seed(market); names.enrich(market, 'SH')
    path = market.root / 'captures' / rid / '0.bin'; path.write_bytes(body)
    meta_path = path.with_suffix('.json'); meta = json.loads(meta_path.read_text())
    meta.update(sha256=hashlib.sha256(body).hexdigest(), bytes=len(body)); meta_path.write_text(json.dumps(meta))
    assert names.query_names(market, candidates(market)) == {}
    assert names.enrich(market, 'SH')['status'] == 'blocked'


@pytest.mark.parametrize('sql', [
    "UPDATE universe_snapshots SET rows_sha256='bad'", "UPDATE universe_snapshots SET count=count+1",
    "UPDATE runs SET status='running'", "UPDATE runs SET count=count+1",
    "UPDATE runs SET company='A:600001'", "UPDATE runs SET source='STAR'",
    "UPDATE listings SET name='Different issuer'", "UPDATE listings SET industry='Changed'",
])
def test_snapshot_current_directory_and_run_all_rechecked(market, sql):
    seed(market); names.enrich(market, 'SH')
    with market.db() as db:
        db.execute(sql)
    assert names.query_names(market, candidates(market)) == {}
    assert names.enrich(market, 'SH')['status'] == 'blocked'


def test_candidate_version_change_requires_new_receipt_without_overwriting_history(market):
    seed(market); names.enrich(market, 'SH')
    old = candidates(market)[0]
    payload = json.loads(old['payload_json']); payload['reason'] = 'Changed independent classification rationale'
    refresh_candidates(market, [payload])
    current = candidates(market)[0]
    assert current['content_hash'] != old['content_hash']
    assert names.query_evidence(market, old) is None
    assert names.query_evidence(market, current) is None
    assert names.enrich(market, 'SH')['added'] == 1
    assert names.query_name(market, current) == 'Synthetic Pharma 0 Co., Ltd.'
    with market.db() as db:
        assert db.execute('SELECT count(*) FROM issuer_directory_name_receipts').fetchone()[0] == 3


def test_new_snapshot_requires_new_receipt_even_when_name_is_unchanged(market):
    seed(market); names.enrich(market, 'SH')
    old = names.query_evidence(market, candidates(market)[0])
    seed(market)
    assert names.query_name(market, candidates(market)[0]) is None
    assert names.enrich(market, 'SH')['added'] == 2
    fresh = names.query_evidence(market, candidates(market)[0])
    assert fresh['receipt_id'] != old['receipt_id'] and fresh['source_run_id'] != old['source_run_id']


LEGAL_QUERY_CASES = [
    ('Gan & Lee Pharmaceuticals.', 'gan and lee pharmaceuticals'),
    ('Hunan Warrant Pharmaceutical Co., Ltd.', 'hunan warrant pharmaceutical'),
    ('PKU HealthCare Corp., Ltd.', 'pku healthcare'),
    ('C.Q. Pharmaceutical Holding Co., Ltd.', 'c q pharmaceutical holding'),
    ('KPC PHARMACEUTICALS, INC.', 'kpc pharmaceuticals'),
    ('Apt Medical Inc.', 'apt medical'),
]


def registry(alias):
    return json.dumps({'studies': [{'protocolSection': {
        'identificationModule': {'nctId': 'NCT00000001'},
        'sponsorCollaboratorsModule': {'leadSponsor': {'name': alias}}}}]}).encode()


@pytest.mark.parametrize('english,query', LEGAL_QUERY_CASES)
def test_verified_legal_queries_do_not_apply_short_brand_or_security_rules(market, english, query):
    from vbt.sponsor_resolution import candidate_query_context, query_name_from_listing
    seed(market, raw=sse(count=1, FULL_NAME_IN_ENGLISH=english))
    candidate = candidates(market)[0]
    assert candidate_query_context(market, candidate)['query'] is None
    # The existing unverified-name path stays conservative.
    assert query_name_from_listing(english, 'A') != query
    assert names.enrich(market, 'SH')['added'] == 1
    context = candidate_query_context(market, candidate)
    assert context['query'] == query and context['name_original'] == english
    assert context['origin'] == 'verified_exchange_english_name'
    assert context['directory_evidence']['candidate_hash'] == candidate['content_hash']
    assert market.count('companies') == market.count('trials') == 0


def test_new_verified_name_reopens_old_blocked_attempt_and_freezes_original(market):
    from vbt.sponsor_resolution import resolve, proposals
    from vbt.market_traversal import readiness, enqueue_traversal
    english, query = LEGAL_QUERY_CASES[0]
    seed(market, raw=sse(count=1, FULL_NAME_IN_ENGLISH=english))
    candidate = candidates(market)[0]
    blocked = resolve(market, candidate['id'], fetch=lambda url: pytest.fail('No verified name must not fetch'))
    assert blocked['status'] == 'blocked' and blocked['query'] is None
    assert readiness(market, 'A')['counts']['needs_name'] == 1
    names.enrich(market, 'SH')
    assert readiness(market, 'A')['counts']['ready'] == 1
    assert enqueue_traversal(market, market_filter='A')['candidate_ids'] == [candidate['id']]
    result = resolve(market, candidate['id'], fetch=lambda url: registry(english))
    assert result['status'] == 'suggested'
    frozen = json.loads(result['query_evidence_json'])
    assert result['query'] == frozen['query'] == query
    assert frozen['name_original'] == english
    assert frozen['directory_evidence']['receipt_id'] == names.query_evidence(market, candidate)['receipt_id']
    assert not proposals(market)[0]['query_input_stale']
    assert market.count('companies') == market.count('trials') == 0


@pytest.mark.parametrize('change', ['english_name', 'raw_tamper', 'missing_receipt'])
def test_old_suggestion_cannot_be_accepted_when_current_verified_input_is_lost_or_changed(market, change):
    from vbt.sponsor_resolution import resolve, proposals
    from vbt.sponsor_review import review
    from vbt.market_traversal import readiness
    rid = seed(market, raw=sse(count=1, FULL_NAME_IN_ENGLISH=LEGAL_QUERY_CASES[0][0]))
    names.enrich(market, 'SH')
    candidate = candidates(market)[0]
    resolve(market, candidate['id'], fetch=lambda url: registry(LEGAL_QUERY_CASES[0][0]))
    proposal_id = proposals(market)[0]['id']
    if change == 'raw_tamper':
        (market.root / 'captures' / rid / '0.bin').write_bytes(b'tampered')
    else:
        seed(market, raw=sse(count=1, FULL_NAME_IN_ENGLISH='Updated Pharma Co., Ltd.' if change == 'english_name' else LEGAL_QUERY_CASES[0][0]))
        assert candidates(market)[0]['content_hash'] == candidate['content_hash']
        if change == 'english_name':
            names.enrich(market, 'SH')
            assert readiness(market, 'A')['counts']['ready'] == 1
    row = proposals(market)[0]
    assert row['query_input_stale'] and not row['candidate_changed']
    with pytest.raises(ValueError, match='Lookup input changed|Current verified query unavailable'):
        review(market, proposal_id, 'accepted', 'Synthetic reviewer', 'Synthetic explicit identity review only')
    assert market.count('companies') == 0
    with market.db() as db:
        assert db.execute('SELECT count(*) FROM sponsor_proposal_reviews').fetchone()[0] == 0
    # Staleness blocks acceptance; an explicit rejection remains possible.
    review(market, proposal_id, 'rejected', 'Synthetic reviewer', 'Synthetic source evidence is no longer usable')
    assert market.count('companies') == 0


def test_same_normalized_query_in_new_source_receipt_does_not_invalidate_pending_review(market):
    from vbt.sponsor_resolution import resolve, proposals
    from vbt.sponsor_review import review
    seed(market, raw=sse(count=1, FULL_NAME_IN_ENGLISH=LEGAL_QUERY_CASES[0][0]))
    names.enrich(market, 'SH')
    candidate = candidates(market)[0]
    result = resolve(market, candidate['id'], fetch=lambda url: registry(LEGAL_QUERY_CASES[0][0]))
    old = json.loads(result['query_evidence_json'])['directory_evidence']['receipt_id']
    seed(market, raw=sse(count=1, FULL_NAME_IN_ENGLISH='GAN & LEE PHARMACEUTICALS CO., LTD.'))
    names.enrich(market, 'SH')
    assert names.query_evidence(market, candidate)['receipt_id'] != old
    row = proposals(market)[0]
    assert not row['query_input_stale']
    review(market, row['id'], 'accepted', 'Synthetic reviewer', 'Synthetic explicit legal-entity review only')
    assert market.count('companies') == 1 and market.count('trials') == 0
    with market.db() as db:
        assert db.execute('SELECT reviewer_type FROM sponsor_review_evidence').fetchone()[0] == 'human_declared'


def test_source_change_during_registry_fetch_preserves_observation_but_publishes_no_suggestion(market):
    from vbt.sponsor_resolution import resolve, proposals
    rid = seed(market, raw=sse(count=1, FULL_NAME_IN_ENGLISH=LEGAL_QUERY_CASES[0][0]))
    names.enrich(market, 'SH')
    candidate = candidates(market)[0]
    def fetch(url):
        (market.root / 'captures' / rid / '0.bin').write_bytes(b'tampered while awaiting registry')
        return registry(LEGAL_QUERY_CASES[0][0])
    result = resolve(market, candidate['id'], fetch=fetch)
    assert result['status'] == 'blocked' and 'Lookup input changed' in result['error']
    assert (market.root / 'captures' / result['run_id'] / '0.bin').is_file()
    assert proposals(market) == [] and market.count('companies') == 0
    with market.db() as db:
        assert db.execute('SELECT count(*) FROM sponsor_resolution_observations').fetchone()[0] == 1


def test_batch_context_and_proposal_rechecks_replay_each_source_only_once(market, monkeypatch):
    from vbt.sponsor_resolution import candidate_query_contexts, resolve, proposals
    seed(market)
    names.enrich(market, 'SH')
    for row in candidates(market):
        english = names.query_name(market, row)
        resolve(market, row['id'], fetch=lambda url, alias=english: registry(alias))
    original = names._source
    calls = []
    def source(*args):
        calls.append(args[2])
        return original(*args)
    monkeypatch.setattr(names, '_source', source)
    assert len(candidate_query_contexts(market, candidates(market))) == 2
    assert calls == ['SH']
    calls.clear()
    assert len(proposals(market)) == 2 and calls == ['SH']


def test_transaction_query_uses_callers_uncommitted_snapshot_and_never_commits(market):
    seed(market); names.enrich(market, 'SH')
    candidate = candidates(market)[0]
    with market.db() as db:
        db.execute('BEGIN IMMEDIATE')
        assert names.query_evidence(market, candidate, db=db)
        db.execute("UPDATE issuer_candidates SET content_hash=? WHERE id=?", ('0'*64, candidate['id']))
        assert names.query_evidence(market, candidate, db=db) is None
        assert db.in_transaction
        db.rollback()
    assert names.query_evidence(market, candidate)


@pytest.mark.parametrize('table', ['issuer_directory_name_receipts', 'issuer_directory_name_audits'])
def test_insert_or_replace_cannot_overwrite_append_only_records_with_default_sqlite_triggers(market, table):
    seed(market); names.enrich(market, 'SH')
    with market.db() as db:
        db.execute('PRAGMA recursive_triggers=OFF')
        assert db.execute('PRAGMA recursive_triggers').fetchone()[0] == 0
        row = dict(db.execute('SELECT * FROM ' + table + ' LIMIT 1').fetchone())
        forged = {**row, 'payload_json': '{"forged":true}'}
        sql = 'INSERT OR REPLACE INTO ' + table + ' VALUES (' + ','.join('?' for _ in row) + ')'
        with pytest.raises(sqlite3.IntegrityError, match='append-only'):
            db.execute(sql, list(forged.values()))
        assert dict(db.execute('SELECT * FROM ' + table + ' WHERE id=?', (row['id'],)).fetchone()) == row
    assert names.enrich(market, 'SH')['unchanged'] == 2


def test_review_guard_revalidates_name_inside_the_same_write_transaction(market):
    from vbt.sponsor_resolution import resolve, proposals
    from vbt.sponsor_review import init_review_schema, review_in_transaction
    seed(market, raw=sse(count=1, FULL_NAME_IN_ENGLISH=LEGAL_QUERY_CASES[0][0]))
    names.enrich(market, 'SH')
    resolve(market, candidates(market)[0]['id'], fetch=lambda url: registry(LEGAL_QUERY_CASES[0][0]))
    proposal_id = proposals(market)[0]['id']
    init_review_schema(market)
    with pytest.raises(ValueError, match='Current verified query unavailable'):
        with market.db() as db:
            db.execute('BEGIN IMMEDIATE')
            def guard(conn):
                conn.execute("UPDATE universe_snapshots SET rows_sha256='changed inside the acceptance transaction'")
            review_in_transaction(market, db, proposal_id, 'accepted', 'Synthetic reviewer',
                'Synthetic acceptance must see its own transaction changes', guard=guard)
    assert market.count('companies') == 0 and not proposals(market)[0]['query_input_stale']
    with market.db() as db:
        assert db.execute('SELECT count(*) FROM sponsor_proposal_reviews').fetchone()[0] == 0


def test_directory_names_cli_reports_blocked_receipts_as_failure(market, capsys):
    from vbt.cli import main
    seed(market)
    args = ['--workspace', str(market.root.parent.parent), '--state-dir', str(market.state),
            'market', 'directory-names', 'SH']
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'succeeded'
    with market.db() as db:
        db.execute("UPDATE universe_snapshots SET rows_sha256='invalid'")
    assert main(args) == 2
    assert json.loads(capsys.readouterr().out)['status'] == 'blocked'


@pytest.mark.parametrize('change',['original_name','proof_receipt','proof_sha','nonfinite'])
def test_frozen_name_context_tamper_is_stale_even_when_query_still_matches(market,change):
    from vbt.sponsor_resolution import resolve,proposals
    from vbt.sponsor_review import review
    seed(market,raw=sse(count=1,FULL_NAME_IN_ENGLISH=LEGAL_QUERY_CASES[0][0]))
    names.enrich(market,'SH')
    result=resolve(market,candidates(market)[0]['id'],fetch=lambda url:registry(LEGAL_QUERY_CASES[0][0]))
    frozen=json.loads(result['query_evidence_json'])
    if change=='original_name':frozen['name_original']='Other Pharma Co., Ltd.'
    elif change=='proof_receipt':frozen['directory_evidence']['receipt_id']='0'*64
    elif change=='proof_sha':frozen['directory_evidence']['raw_sha256']='0'*64
    else:frozen['directory_evidence']['raw_bytes']=float('nan')
    with market.db() as db:
        db.execute('UPDATE sponsor_resolution_runs SET query_evidence_json=? WHERE id=?',(json.dumps(frozen),result['run_id']))
    row=proposals(market)[0]
    assert row['query_input_stale'] and 'Frozen query evidence' in row['stale_reason']
    with pytest.raises(ValueError,match='Frozen query evidence'):
        review(market,row['id'],'accepted','Synthetic reviewer','Synthetic frozen name input integrity test')
    assert market.count('companies')==0


def test_scheduler_bootstraps_english_only_candidate_and_finishes_with_stable_hash_receipt(market):
    from vbt.market_discovery_pipeline import tick
    raw=sse(count=1,COMPANY_ABBR='合成创新',FULL_NAME='合成创新股份有限公司',
        FULL_NAME_IN_ENGLISH='Synthetic Pharmaceutical Innovation Co., Ltd.')
    market.refresh_universe('SH',fetch=lambda url:raw)
    with market.db() as db:
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='issuer_candidates'").fetchone()
    first=tick(market)
    current=candidates(market)[0]
    assert current['name']=='合成创新' and current['classification']=='candidate'
    payload=json.loads(current['payload_json'])
    assert any(e['kind']=='issuer_directory_english_name_screening' for e in payload['evidence'])
    assert names.query_evidence(market,current)['candidate_hash']==current['content_hash']
    assert first['directory_name_enrichment']['status']=='succeeded'
    assert len(first['resolution_jobs'])==1 and market.count('companies')==market.count('trials')==0
    with market.db() as db:
        audits=db.execute('SELECT count(*) FROM issuer_directory_name_audits').fetchone()[0]
    assert not tick(market)['expanded']
    with market.db() as db:
        assert db.execute('SELECT count(*) FROM issuer_directory_name_audits').fetchone()[0]==audits
        db.execute("UPDATE listings SET observed='2000-01-01T00:00:00+00:00'")
    assert tick(market)['expanded']
    assert candidates(market)[0]['content_hash']==current['content_hash']
    assert names.query_evidence(market,candidates(market)[0])


def test_caller_payload_cannot_translate_or_impersonate_an_issuer(market):
    seed(market); names.enrich(market, 'SH')
    row = candidates(market)[0]
    fake = {**row, 'english_name': 'Invented Translated Name', 'name': 'Spoofed display'}
    assert names.query_name(market, fake) == 'Synthetic Pharma 0 Co., Ltd.'
    assert names.query_name(market, {**row, 'content_hash': '0'*64}) is None
    assert names.query_name(market, {'id': row['id'], 'english_name': fake['english_name']}) is None
    assert names.query_names(market, [row, {**row, 'content_hash': '0'*64}, row]) == {}


@pytest.mark.parametrize('english', ['', '-', None, '中文猜测名', '<script>fake</script>', 'Fake\nPharma'])
def test_invalid_english_field_stays_unknown_per_security(market, english):
    seed(market, raw=sse(FULL_NAME_IN_ENGLISH=english))
    result = names.enrich(market, 'SH')
    assert result['added'] == 0 and len(result['sources'][0]['unresolved']) == 2
    assert names.query_names(market, candidates(market)) == {}


def test_missing_named_sse_field_does_not_substitute_short_name(market):
    data = json.loads(sse()); del data['result'][0]['FULL_NAME_IN_ENGLISH']
    seed(market, raw=json.dumps(data).encode())
    assert names.enrich(market, 'SH')['status'] == 'blocked'


def test_missing_sz_named_header_blocks_instead_of_using_short_name(market):
    seed(market, 'SZ', xlsx(missing_header=True))
    assert names.enrich(market, 'SZ')['status'] == 'blocked'


@pytest.mark.parametrize('location', ['run', 'raw', 'metadata', 'captures'])
def test_symlink_archive_and_namespace_are_rejected(market, tmp_path, location):
    rid = seed(market); names.enrich(market, 'SH')
    folder = market.root / 'captures' / rid
    target = folder if location == 'run' else folder / ('0.bin' if location == 'raw' else '0.json')
    if location == 'captures':
        target = market.root / 'captures'
    moved = tmp_path / ('moved-' + location)
    target.rename(moved); target.symlink_to(moved, target_is_directory=moved.is_dir())
    assert names.query_names(market, candidates(market)) == {}
    assert names.enrich(market, 'SH')['status'] == 'blocked'


def test_receipt_and_audit_tables_are_append_only(market):
    seed(market); names.enrich(market, 'SH')
    for table in ('issuer_directory_name_receipts', 'issuer_directory_name_audits'):
        for operation in ('DELETE FROM ' + table, 'UPDATE ' + table + " SET id='changed'"):
            with market.db() as db, pytest.raises(sqlite3.IntegrityError, match='append-only'):
                db.execute(operation)


def test_forged_ledger_payload_fails_reconstruction_even_with_fresh_sha(market):
    seed(market); names.enrich(market, 'SH')
    with market.db() as db:
        db.execute('DROP TRIGGER directory_names_no_update')
        rows = db.execute('SELECT * FROM issuer_directory_name_receipts').fetchall()
        for row in rows:
            payload = json.loads(row['payload_json']); payload['english_name'] = 'Fabricated Name Co., Ltd.'
            serial = names._serial(payload); digest = hashlib.sha256(serial.encode()).hexdigest()
            db.execute('UPDATE issuer_directory_name_receipts SET id=?,payload_json=?,payload_sha256=? WHERE id=?',
                       (digest, serial, digest, row['id']))
    assert names.query_names(market, candidates(market)) == {}


def test_corrupted_existing_id_cannot_be_reported_as_unchanged_success(market):
    seed(market); names.enrich(market, 'SH')
    with market.db() as db:
        db.execute('DROP TRIGGER directory_names_no_update')
        db.execute("UPDATE issuer_directory_name_receipts SET payload_json='{}'")
    result = names.enrich(market, 'SH')
    assert result['status'] == 'blocked' and result['unchanged'] == 0
    assert names.query_names(market, candidates(market)) == {}


@pytest.mark.parametrize('broken', ['<worksheet', '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row><c r="A1"/><c r="A1"/></row></sheetData></worksheet>'])
def test_malformed_or_duplicate_cell_worksheet_fails_closed(broken):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('xl/worksheets/sheet1.xml', broken)
    with pytest.raises(names._SOURCE_ERRORS):
        names._extract('SZ', buffer.getvalue())


def test_batch_replays_each_source_once_and_is_read_only(market, monkeypatch):
    for source in names.SOURCES:
        seed(market, source)
    assert names.enrich(market)['added'] == 6
    real = names._source; calls = []
    def counted(m, db, source):
        calls.append(source); return real(m, db, source)
    monkeypatch.setattr(names, '_source', counted)
    with market.db() as db:
        before = list(db.iterdump())
    assert len(names.query_names(market, candidates(market))) == 6
    assert sorted(calls) == sorted(names.SOURCES)
    with market.db() as db:
        assert list(db.iterdump()) == before


def test_duplicate_security_records_block_even_if_snapshot_counts_agree(market):
    raw = sse(A_STOCK_CODE='600001')
    # shared universe publication replaces duplicate keys, so the current count
    # check already fails; the name parser also rejects duplicates independently.
    with pytest.raises(ValueError, match='Duplicate security'):
        names._extract('SH', raw)


def test_unsupported_source_and_external_state_rejected(market):
    with pytest.raises(ValueError, match='Unsupported'):
        names.enrich(market, 'HK')
    market.state = Path('/Volumes/NotLocal')
    with pytest.raises(ValueError, match='local'):
        names.enrich(market, 'SH')
