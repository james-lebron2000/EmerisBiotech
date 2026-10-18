"""Offline engineering fixtures; legal-name keywords never verify drug assets."""
from copy import deepcopy
import hashlib
import json
import sqlite3

import pytest

from vbt import issuer_directory_names as names
from vbt import issuer_directory_screening as screening
from vbt.issuer_discovery import discover
from vbt.market import Market
from vbt.market_coverage import refresh_candidates, review_candidate


@pytest.fixture
def market(tmp_path):
    return Market(tmp_path / 'workspace', tmp_path / 'state')


def directory(english='Synthetic Pharma Co., Ltd.', *, count=1,
              chinese='合成甲一股份有限公司', short='合成甲一', industry='制造业'):
    rows = [dict(A_STOCK_CODE=str(600001+i), COMPANY_ABBR=short + str(i),
                 FULL_NAME=chinese, FULL_NAME_IN_ENGLISH=english,
                 CSRC_CODE_DESC=industry) for i in range(count)]
    return json.dumps({'result': rows, 'pageHelp': {'total': count}}, ensure_ascii=False).encode()


def current(market):
    with market.db() as db:
        return [dict(r) for r in db.execute('SELECT * FROM issuer_candidates ORDER BY id')]


def dump(market):
    with market.db() as db:
        return list(db.iterdump())


def prepare(market, english='Synthetic Pharma Co., Ltd.', *, enrich=True, **kwargs):
    raw = directory(english, **kwargs)
    run = market.refresh_universe('SH', fetch=lambda _: raw)
    assert next(r for r in market.rows('runs') if r['id'] == run)['status'] == 'succeeded'
    base = discover(market.rows('listings'))
    assert all(row['classification'] == 'unclassified' for row in base)
    refresh_candidates(market, base)
    if enrich:
        assert names.enrich(market, 'SH')['added'] == len(base)
    return base, run, raw


def test_sealed_receipt_adds_keyword_observation_without_writes_or_hash_cycle(market):
    base, _, raw = prepare(market)
    original, before = deepcopy(base), dump(market)
    old = current(market)[0]
    old_proof = names.query_evidence(market, old)
    result = screening.supplement_candidates(market, base)
    assert base == original and dump(market) == before
    row, hint = result[0], result[0]['evidence'][-1]
    assert row['classification'] == 'candidate'
    assert row['evidence'][:-1] == base[0]['evidence']
    assert hint['kind'] == 'issuer_directory_english_name_screening'
    assert hint['method_version'] == screening.VERSION
    assert hint['english_name_original'] == 'Synthetic Pharma Co., Ltd.'
    assert hint['source_locator'] == 'result[0]'
    assert hint['raw_sha256'] == hashlib.sha256(raw).hexdigest()
    assert hint['raw_bytes'] == len(raw)
    assert hint['review_status'] == hint['scientific_review'] == 'pending'
    assert hint['human_sign_off'] is hint['registry_alias_proven'] is hint['drug_ownership_verified'] is False
    assert not {'candidate_hash', 'receipt_id', 'ledger_id', 'source_run_id', 'retrieved_at'} & hint.keys()
    assert not row.get('issuer_group') and not row.get('suggested_aliases')
    assert refresh_candidates(market, result)['updated'] == 1
    new = current(market)[0]
    assert new['content_hash'] != old['content_hash']
    assert names.query_evidence(market, new) is None  # Old hash cannot authorize a new candidate.
    assert names.enrich(market, 'SH')['added'] == 1
    new_proof = names.query_evidence(market, new)
    assert new_proof['candidate_hash'] == new['content_hash']
    assert new_proof['receipt_id'] != old_proof['receipt_id']
    stable = screening.supplement_candidates(market, base)
    assert stable == result
    assert refresh_candidates(market, stable)['unchanged'] == 1
    assert current(market)[0]['content_hash'] == new['content_hash']
    assert market.count('companies') == market.count('trials') == 0


@pytest.mark.parametrize('english,expected', [
    ('Synthetic Pharmaceutical Co., Ltd.', 'candidate'),
    ('Synthetic Therapeutics Co., Ltd.', 'candidate'),
    ('Synthetic Vaccine Co., Ltd.', 'candidate'),
    ('Synthetic Biotechnology Co., Ltd.', 'uncertain'),
    ('Synthetic Biosciences Co., Ltd.', 'uncertain'),
    ('Synthetic Pharma CDMO Co., Ltd.', 'uncertain'),
    ('Synthetic Pharmaceutical Medical Devices Co., Ltd.', 'uncertain'),
    ('Synthetic Contract Research Co., Ltd.', 'uncertain'),
    ('Hunan Warrant Pharmaceutical Co., Ltd.', 'candidate'),
    ('Synthetic Industrial Co., Ltd.', 'unclassified'),
])
def test_existing_keyword_rules_keep_mixed_business_uncertain(market, english, expected):
    base, _, _ = prepare(market, english)
    result = screening.supplement_candidates(market, base)[0]
    assert result['classification'] == expected
    if expected == 'unclassified':
        assert result == base[0]
    else:
        assert result['evidence'][-1]['drug_ownership_verified'] is False


def test_chinese_full_legal_name_negative_evidence_keeps_mixed_issuer_uncertain(market):
    base, _, _ = prepare(market, chinese='合成药明康德研究股份有限公司')
    hint = screening.supplement_candidates(market, base)[0]
    assert hint['classification'] == 'uncertain'
    assert hint['evidence'][-1]['keyword_hits']['service_business'] == ['药明康德']


def test_uncertain_base_can_gain_a_pending_drug_name_clue(market):
    market.refresh_universe('SH', fetch=lambda _: directory(short='合成生物'))
    base = discover(market.rows('listings'))
    assert base[0]['classification'] == 'uncertain'
    refresh_candidates(market, base)
    names.enrich(market, 'SH')
    result = screening.supplement_candidates(market, base)[0]
    assert result['classification'] == 'candidate'
    assert result['evidence'][-1]['base_classification'] == 'uncertain'
    assert result['review_status'] == 'pending'
    assert market.count('companies') == market.count('trials') == 0


@pytest.mark.parametrize('damage', ['no_receipt', 'raw', 'url', 'run', 'directory', 'candidate_hash'])
def test_missing_stale_or_damaged_evidence_fails_closed(market, damage):
    base, run, _ = prepare(market, enrich=damage != 'no_receipt')
    if damage == 'raw':
        path = market.root / 'captures' / run / '0.bin'
        path.write_bytes(path.read_bytes()[:-1])
    elif damage == 'url':
        path = market.root / 'captures' / run / '0.json'
        metadata = json.loads(path.read_text()); metadata['url'] = 'https://example.test/forged'
        path.write_text(json.dumps(metadata))
    elif damage in ('run', 'directory', 'candidate_hash'):
        with market.db() as db:
            sql = {'run': "UPDATE runs SET status='failed' WHERE id=?",
                   'directory': "UPDATE listings SET name='Changed issuer' WHERE source=?",
                   'candidate_hash': "UPDATE issuer_candidates SET content_hash=?"}[damage]
            db.execute(sql, (run if damage == 'run' else 'SH' if damage == 'directory' else '0'*64,))
    before = dump(market)
    assert screening.supplement_candidates(market, base) == base
    assert dump(market) == before
    assert market.count('companies') == market.count('trials') == 0


def test_one_batch_read_snapshot_replays_sources_once(market, monkeypatch):
    base, _, _ = prepare(market, count=2)
    actual, calls = names.query_evidences, []
    def observed(m, candidates, *, db=None):
        calls.append(candidates)
        assert db is not None and db.in_transaction
        with pytest.raises(sqlite3.OperationalError, match='readonly'):
            db.execute('CREATE TABLE must_not_write (x)')
        return actual(m, candidates, db=db)
    monkeypatch.setattr(names, 'query_evidences', observed)
    before = dump(market)
    assert all(r['classification'] == 'candidate' for r in screening.supplement_candidates(market, base))
    assert len(calls) == 1 and len(calls[0]) == 2
    assert dump(market) == before


@pytest.mark.parametrize('decision', ['accepted', 'rejected'])
def test_refresh_keeps_declared_human_review_without_forging_new_signoff(market, decision):
    base, _, _ = prepare(market)
    review_candidate(market, base[0]['id'], decision, 'SYNTHETIC ENGINEERING REVIEWER',
                     'Synthetic classification review for preservation test only')
    with market.db() as db:
        before = [tuple(r) for r in db.execute('SELECT * FROM issuer_candidate_reviews')]
    refresh_candidates(market, screening.supplement_candidates(market, base))
    assert current(market)[0]['review_status'] == decision
    with market.db() as db:
        assert [tuple(r) for r in db.execute('SELECT * FROM issuer_candidate_reviews')] == before
    evidence = json.loads(current(market)[0]['evidence_json'])[-1]
    assert evidence['human_sign_off'] is False and evidence['review_status'] == 'pending'
    assert market.count('companies') == market.count('trials') == 0


@pytest.mark.parametrize('change', ['excluded', 'candidate', 'drug_developer', 'US', 'HK', 'seed', 'conflict'])
def test_seed_and_existing_classified_or_other_market_base_rows_are_not_reclassified(market, monkeypatch, change):
    base, _, _ = prepare(market)
    row = base[0]
    if change in ('excluded', 'candidate', 'drug_developer'):
        row['classification'] = change
    elif change in ('US', 'HK'):
        row.update(market=change, ticker='SYNTH' if change == 'US' else '01234', id=change+':SYNTH')
    elif change == 'seed':
        row['issuer_group'], row['suggested_aliases'] = 'synthetic', ['Synthetic']
    else:
        row['evidence'].append({'kind': 'seed_identity_conflict', 'expected_name': 'Other'})
    monkeypatch.setattr(names, 'query_evidences', lambda *a, **k: pytest.fail('Ineligible rows cannot consult proofs'))
    assert screening.supplement_candidates(market, base) == base


def test_read_only_bootstrap_never_creates_candidate_or_receipt_tables(market):
    run = market.refresh_universe('SH', fetch=lambda _: directory())
    assert run
    base = discover(market.rows('listings'))
    before = dump(market)
    assert screening.supplement_candidates(market, base) == base
    assert dump(market) == before
    with market.db() as db:
        assert not db.execute("SELECT name FROM sqlite_master WHERE name LIKE 'issuer_%'").fetchall()


@pytest.mark.parametrize('field,value', [('raw_bytes', True), ('raw_bytes', 0), ('source_locator', None),
    ('scope', 'human_review'), ('source_url', 'https://example.test/untrusted'), ('human_sign_off', True),
    ('candidate_hash', '0'*64), ('english_name', '')])
def test_malformed_proof_cannot_self_attest(market, monkeypatch, field, value):
    base, _, _ = prepare(market)
    proof = names.query_evidence(market, current(market)[0]); proof[field] = value
    monkeypatch.setattr(names, 'query_evidences', lambda *a, **k: {base[0]['id']: proof})
    assert screening.supplement_candidates(market, base) == base


def test_expand_uses_supplement_before_normal_refresh_and_never_installs_keyword_company(market, monkeypatch):
    from vbt import market_expansion as expansion
    base, _, _ = prepare(market)
    for method in ('initial_mappings', 'source_mappings', 'source_mappings_v2'):
        monkeypatch.setattr(expansion, method, lambda: [])
    result = expansion.expand(market)
    assert result['classification']['updated'] == 1
    assert current(market)[0]['classification'] == 'candidate'
    assert result['added'] == []
    assert market.count('companies') == market.count('trials') == 0
    names.enrich(market, 'SH')
    assert expansion.expand(market)['classification']['unchanged'] == 1


def test_fresh_same_raw_capture_changes_receipt_without_changing_screened_candidate_hash(market):
    base, _, raw = prepare(market)
    refresh_candidates(market, screening.supplement_candidates(market, base))
    names.enrich(market, 'SH')
    before = current(market)[0]
    receipt = names.query_evidence(market, before)
    market.refresh_universe('SH', fetch=lambda _: raw)
    assert names.query_evidence(market, before) is None
    assert names.enrich(market, 'SH')['added'] == 1
    assert names.query_evidence(market, before)['receipt_id'] != receipt['receipt_id']
    fresh_base = discover(market.rows('listings'))
    assert refresh_candidates(market, screening.supplement_candidates(market, fresh_base))['unchanged'] == 1
    assert current(market)[0]['content_hash'] == before['content_hash']
