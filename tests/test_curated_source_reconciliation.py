"""Synthetic transaction tests for curated source-name proposal reconciliation."""
import hashlib
import json
import sqlite3

import pytest

from test_china_expansion_v2_integration import setup  # Reuse explicitly synthetic source captures.
from vbt import market_expansion as expansion
from vbt.china_expansion_review import row_digest
from vbt.issuer_discovery import discover
from vbt.market_coverage import refresh_candidates
from vbt.sponsor_review import init_review_schema, review


def proposal(market, mapping, *, pid='fixture-proposal', run_id='fixture-resolution', alias=None):
    refresh_candidates(market, discover(market.rows('listings')))
    init_review_schema(market)
    with market.db() as db:
        candidate_hash = db.execute('SELECT content_hash FROM issuer_candidates WHERE id=?', (mapping['id'],)).fetchone()[0]
        stamp = '2026-10-17T00:00:00+00:00'
        db.execute('''INSERT INTO sponsor_resolution_runs
                   (id,candidate_id,candidate_hash,query,started,finished,status,page_count,proposal_count,observation_count,truncated,error)
                   VALUES (?,?,?,?,?,?,'suggested',1,1,1,0,'')''',
                   (run_id, mapping['id'], candidate_hash, 'synthetic therapeutics', stamp, stamp))
        db.execute('''INSERT INTO sponsor_proposals
                   (id,candidate_id,candidate_hash,alias,normalized_alias,match_type,evidence_nct,source_url,run_id,created,last_seen,status)
                   VALUES (?,?,?,?,?,'normalized_exact',?,?,?,?,?,'pending')''',
                   (pid, mapping['id'], candidate_hash, alias or mapping['aliases'][0], 'synthetic sponsor',
                    json.dumps(['NCT00000001']), 'https://clinicaltrials.gov/study/NCT00000001', run_id, stamp, stamp))
    return candidate_hash


def status(market, pid='fixture-proposal'):
    with market.db() as db:
        return db.execute('SELECT status FROM sponsor_proposals WHERE id=?', (pid,)).fetchone()[0]


def test_curated_path_accepts_with_mapping_and_typed_nonhuman_evidence_atomically(setup):
    market, mapping, _, _ = setup
    candidate_hash = proposal(market, mapping)
    result = expansion.expand(market)
    assert result['added'] == [mapping['id']] and status(market) == 'accepted'
    assert not result['pending_proposal_conflicts'] and not result['blocked_evidence']
    item = result['curated_reconciliations'][0]
    assert item['company'] == mapping['id'] and item['reviewer_type'] == 'curated_official_source_review'
    with market.db() as db:
        typed = dict(db.execute('SELECT * FROM sponsor_review_evidence WHERE review_id=?', (item['review_id'],)).fetchone())
        original = dict(db.execute('SELECT * FROM sponsor_proposal_reviews WHERE id=?', (item['review_id'],)).fetchone())
        source = dict(db.execute('SELECT * FROM mapping_source_reviews WHERE id=?', (item['source_review_id'],)).fetchone())
        reconciliation = dict(db.execute('SELECT * FROM curated_source_reconciliations WHERE id=?', (item['id'],)).fetchone())
    evidence = json.loads(typed['evidence_json'])
    assert evidence['candidate_hash'] == evidence['proposal_candidate_hash'] == candidate_hash
    assert evidence['proposal_id'] == original['proposal_id'] == 'fixture-proposal'
    assert evidence['source_review_id'] == source['id']
    assert evidence['source_review_sha256'] == hashlib.sha256(source['payload'].encode()).hexdigest()
    assert evidence['curated_row_sha256'] == row_digest(mapping)
    assert evidence['proof_captures'] and all(c['reference'] and len(c['sha256']) == 64 for c in evidence['proof_captures'])
    assert evidence['original_resolution']['status'] == 'suggested' and evidence['original_resolution']['truncated'] == 0
    assert evidence['human_sign_off'] is False and evidence['drug_ownership_verified'] is False
    assert evidence['scientific_review'] == 'pending' and evidence['full_pipeline_verified'] is False
    assert typed['reviewer_type'] == 'curated_official_source_review'
    assert typed['evidence_sha256'] == reconciliation['evidence_sha256'] == hashlib.sha256(typed['evidence_json'].encode()).hexdigest()
    assert original['decision'] == 'accepted' and 'Human and scientific sign-off' in original['reason']


def test_reconciliation_is_idempotent_and_never_changes_public_reviewer_type_allowlist(setup):
    market, mapping, _, _ = setup
    proposal(market, mapping)
    expansion.expand(market)
    second = expansion.expand(market)
    assert not second['added'] and not second['curated_reconciliations']
    assert second['unchanged'] == [mapping['id']]
    with market.db() as db:
        assert db.execute('SELECT count(*) FROM sponsor_proposal_reviews').fetchone()[0] == 1
        assert db.execute('SELECT count(*) FROM curated_source_reconciliations').fetchone()[0] == 1
    with pytest.raises(ValueError, match='Unknown reviewer type'):
        review(market, 'fixture-proposal', 'accepted', 'Synthetic caller', 'Unsupported public caller type',
               reviewer_type='curated_official_source_review', evidence={'status': 'verified_source_name'})
    assert status(market) == 'accepted'


def test_curated_reconciliation_ledger_rejects_updates_and_deletes(setup):
    market, mapping, _, _ = setup
    proposal(market, mapping)
    expansion.expand(market)
    for mutation in ("UPDATE curated_source_reconciliations SET evidence_json='{}'",
                     'DELETE FROM curated_source_reconciliations'):
        with pytest.raises(sqlite3.IntegrityError,match='append only'):
            with market.db() as db:db.execute(mutation)
    with market.db() as db:
        assert db.execute('SELECT count(*) FROM curated_source_reconciliations').fetchone()[0] == 1


@pytest.mark.parametrize('tamper', ['missing', 'modified'])
def test_missing_or_modified_raw_never_accepts_pending_proposal(setup, tamper):
    market, mapping, workspace, _ = setup
    proposal(market, mapping)
    file = workspace / mapping['evidence'][1]['capture_reference']
    if tamper == 'missing':
        file.unlink()
    else:
        file.write_bytes(b'modified synthetic raw')
    result = expansion.expand(market)
    assert result['blocked_evidence'] and not result['curated_reconciliations']
    assert status(market) == 'pending' and not market.rows('companies')
    with market.db() as db:
        assert db.execute('SELECT count(*) FROM sponsor_proposal_reviews').fetchone()[0] == 0


def test_unmatched_pending_alias_remains_an_explicit_conflict(setup):
    market, mapping, _, _ = setup
    proposal(market, mapping, alias='Another Unrelated Sponsor')
    result = expansion.expand(market)
    assert not result['added'] and not result['curated_reconciliations']
    assert result['pending_proposal_conflicts'][0]['conflicts'][0]['code'] == 'alias_not_curated_exact'
    assert status(market) == 'pending'


def test_normalized_exact_punctuation_variants_do_not_add_new_collection_aliases(setup):
    market, mapping, _, _ = setup
    proposal(market, mapping, alias='SYNTHETIC SPONSOR CO LTD')
    result = expansion.expand(market)
    assert result['added'] == [mapping['id']] and status(market) == 'accepted'
    assert json.loads(market.rows('companies')[0]['aliases']) == mapping['aliases']


@pytest.mark.parametrize('change', ['proposal_hash', 'resolution_hash', 'truncated', 'ambiguous', 'partial', 'unfinished'])
def test_changed_or_incomplete_original_resolution_never_reconciles(setup, change):
    market, mapping, _, _ = setup
    proposal(market, mapping)
    with market.db() as db:
        if change == 'proposal_hash':
            db.execute('UPDATE sponsor_proposals SET candidate_hash=?', ('f' * 64,))
        elif change == 'resolution_hash':
            db.execute('UPDATE sponsor_resolution_runs SET candidate_hash=?', ('f' * 64,))
        elif change == 'truncated':
            db.execute('UPDATE sponsor_resolution_runs SET truncated=1')
        elif change == 'unfinished':
            db.execute('UPDATE sponsor_resolution_runs SET finished=NULL')
        else:
            db.execute('UPDATE sponsor_resolution_runs SET status=?', (change,))
    result = expansion.expand(market)
    assert result['pending_proposal_conflicts'] and not result['curated_reconciliations']
    assert status(market) == 'pending' and not market.rows('companies')


def test_candidate_hash_change_between_evidence_and_transaction_is_fenced(setup, monkeypatch):
    market, mapping, _, _ = setup
    proposal(market, mapping)
    original = expansion.review_mapping
    def changed_after_review(*args, **kwargs):
        verified = original(*args, **kwargs)
        with market.db() as db:
            db.execute('UPDATE issuer_candidates SET content_hash=?', ('f' * 64,))
        return verified
    monkeypatch.setattr(expansion, 'review_mapping', changed_after_review)
    result = expansion.expand(market)
    assert result['blocked_evidence'][0]['errors'][0]['code'] == 'current_candidate_changed'
    assert status(market) == 'pending' and not market.rows('companies')


def test_unverified_proposal_nct_reference_is_not_promoted(setup):
    market, mapping, _, _ = setup
    proposal(market, mapping)
    with market.db() as db:
        db.execute("UPDATE sponsor_proposals SET source_url='https://clinicaltrials.gov/study/NCT00000002',evidence_nct='[\"NCT00000002\"]'")
    result = expansion.expand(market)
    assert result['pending_proposal_conflicts'][0]['conflicts'][0]['code'] == 'proposal_registry_proof_mismatch'
    assert status(market) == 'pending'


def test_only_matching_pending_proposal_is_reconciled(setup):
    market, mapping, _, _ = setup
    proposal(market, mapping)
    proposal(market, mapping, pid='unmatched-proposal', run_id='unmatched-run', alias='Unrelated Other Sponsor')
    result = expansion.expand(market)
    assert result['added'] == [mapping['id']]
    assert status(market) == 'accepted' and status(market, 'unmatched-proposal') == 'pending'
    assert result['pending_proposal_conflicts'][0]['proposal_ids'] == ['unmatched-proposal']
    assert len(result['curated_reconciliations']) == 1


def test_database_failure_rolls_back_mapping_and_all_review_writes(setup):
    market, mapping, _, _ = setup
    proposal(market, mapping)
    with market.db() as db:
        db.execute('''CREATE TABLE curated_source_reconciliations
                   (id TEXT PRIMARY KEY,proposal_id TEXT UNIQUE,review_id TEXT UNIQUE,company TEXT,
                    source_review_id TEXT,evidence_sha256 TEXT,created TEXT,evidence_json TEXT)''')
        db.execute('''CREATE TRIGGER fixture_abort_curated BEFORE INSERT ON curated_source_reconciliations
                    BEGIN SELECT RAISE(ABORT,'Synthetic reconciliation storage failure'); END''')
    with pytest.raises(sqlite3.IntegrityError, match='Synthetic reconciliation'):
        expansion.expand(market)
    assert status(market) == 'pending' and not market.rows('companies')
    with market.db() as db:
        assert db.execute('SELECT count(*) FROM sponsor_proposal_reviews').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM sponsor_review_evidence').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM mapping_source_reviews').fetchone()[0] == 0


def test_existing_different_mapping_is_never_overwritten_or_accepted(setup):
    market, mapping, _, _ = setup
    proposal(market, mapping)
    market.add_company('HK', '09999', 'Synthetic old mapping', ['Old Other Sponsor'], 'Synthetic existing mapping')
    result = expansion.expand(market)
    assert result['conflicts'] == [mapping['id']] and status(market) == 'pending'
    assert json.loads(market.rows('companies')[0]['aliases']) == ['Old Other Sponsor']


def test_raw_removed_after_outer_validation_is_rechecked_inside_transaction(setup, monkeypatch):
    market, mapping, workspace, _ = setup
    proposal(market, mapping)
    original = expansion.review_mapping
    def remove_after_review(*args, **kwargs):
        verified = original(*args, **kwargs)
        (workspace / mapping['evidence'][1]['capture_reference']).unlink()
        return verified
    monkeypatch.setattr(expansion, 'review_mapping', remove_after_review)
    result = expansion.expand(market)
    assert result['blocked_evidence'][0]['errors'][0]['code'] == 'capture_missing'
    assert not result['source_name_verified'] and not result['curated_reconciliations']
    assert status(market) == 'pending' and not market.rows('companies')


def test_modified_existing_source_review_is_retained_and_reported_as_blocked(setup):
    market, mapping, _, _ = setup
    proposal(market, mapping)
    expansion.expand(market)
    with market.db() as db:
        db.execute("UPDATE mapping_source_reviews SET payload='[]'")
    result = expansion.expand(market)
    assert result['blocked_evidence'][0]['errors'][0]['code'] == 'source_review_ledger_mismatch'
    assert not result['source_name_verified'] and not result['curated_reconciliations']
    with market.db() as db:
        assert db.execute('SELECT count(*) FROM sponsor_proposal_reviews').fetchone()[0] == 1
        assert db.execute('SELECT payload FROM mapping_source_reviews').fetchone()[0] == '[]'
