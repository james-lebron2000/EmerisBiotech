"""Synthetic independent-review gates; never evidence of real issuer coverage."""
import copy
import hashlib
import json
from pathlib import Path
from urllib.parse import urlencode

import pytest

from vbt import china_expansion_review as audit
from vbt import market_expansion as expansion
from vbt.china_issuer_sources_v2 import source_mappings
from vbt.market import Market


@pytest.fixture
def setup(tmp_path, monkeypatch):
    workspace = tmp_path / 'workspace'
    market = Market(workspace, tmp_path / 'state')
    folder = workspace / audit.CAPTURE_PREFIX
    folder.mkdir(parents=True)
    listing = {'id': 'synthetic:09999', 'market': 'HK', 'exchange': 'SYNTHETIC', 'ticker': '09999',
               'name': 'SYNTHETIC THERAPEUTICS', 'industry': '', 'source': 'SYNTHETIC', 'observed': '2026-10-17T00:00:00+00:00'}
    with market.db() as db:
        db.execute('INSERT INTO listings VALUES (?,?,?,?,?,?,?,?)', tuple(listing[k] for k in
                   ('id', 'market', 'exchange', 'ticker', 'name', 'industry', 'source', 'observed')))
    alias, nct = 'Synthetic Sponsor Co., Ltd.', 'NCT00000001'
    study = {'protocolSection': {'identificationModule': {'nctId': nct},
                                'sponsorCollaboratorsModule': {'leadSponsor': {'name': alias}}}}
    def captured(name, value):
        raw = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode()
        file = folder / name
        file.write_bytes(raw)
        return str(file.relative_to(workspace)), hashlib.sha256(raw).hexdigest(), len(raw)
    snap, snap_hash, _ = captured('current-listings.json', [listing])
    official, official_hash, size = captured('synthetic-official.html', b'<h1>Synthetic fixture only, not a real issuer</h1>')
    single, single_hash, _ = captured('synthetic-study.json', study)
    query, query_hash, _ = captured('synthetic-exact.json', {'studies': [study], 'totalCount': 1})
    row = {'id': 'HK:09999', 'market': 'HK', 'ticker': '09999', 'name': listing['name'],
           'listing_names': [listing['name']], 'legal_name': alias, 'aliases': [alias],
           'issuer_group': 'synthetic-group', 'provenance': 'Synthetic fixture, source-name evidence only',
           'review_status': 'pending', 'human_sign_off': False, 'drug_ownership_verified': False,
           'relationship_to_issuer': 'issuer_legal_name',
           'evidence': [
               {'kind': 'exchange_listing_observation', 'snapshot_reference': snap, 'snapshot_sha256': snap_hash},
               {'kind': 'official_identity_source', 'url': 'https://fixture.example.test/',
                'capture_reference': official, 'sha256': official_hash, 'bytes': size},
               {'kind': 'registry_lead_sponsor', 'url': 'https://clinicaltrials.gov/study/' + nct,
                'nct_id': nct, 'sponsor': alias,
                'api_url': 'https://clinicaltrials.gov/api/v2/studies/' + nct,
                'capture_reference': single, 'sha256': single_hash,
                'query_url': 'https://clinicaltrials.gov/api/v2/studies?' + urlencode({'query.spons': '"' + alias + '"'}),
                'query_capture_reference': query, 'query_sha256': query_hash,
                'query_total': 1, 'query_exact_lead_examples': [nct]},
           ]}
    def reviewed(value):
        monkeypatch.setattr(audit, '_REVIEWED_ROWS', {value['id']: {
            'row_sha256': audit.row_digest(value), 'relationship_to_issuer': value['relationship_to_issuer'],
            'note': 'Independent synthetic test receipt; not real evidence.'}})
    reviewed(row)
    monkeypatch.setattr(expansion, 'initial_mappings', lambda: [])
    monkeypatch.setattr(expansion, 'source_mappings', lambda: [])
    monkeypatch.setattr(expansion, 'source_mappings_v2', lambda: [row])
    return market, row, workspace, reviewed


def test_verified_capture_batch_installs_once_and_keeps_boundaries(setup):
    market, row, _, _ = setup
    result = expansion.expand(market)
    assert result['added'] == [row['id']] and result['blocked_evidence'] == []
    assert result['source_name_verified'] == [row['id']]
    assert expansion.expand(market)['unchanged'] == [row['id']]
    with market.db() as db:
        assert db.execute('SELECT count(*) FROM mapping_source_reviews').fetchone()[0] == 1
        payload = json.loads(db.execute('SELECT payload FROM mapping_evidence').fetchone()[0])
    assert payload['review_status'] == 'pending' and payload['human_sign_off'] is False
    review = payload['source_name_review']
    assert review['status'] == 'verified_source_name'
    assert review['scientific_review'] == 'pending' and review['human_sign_off'] is False
    assert review['drug_ownership_verified'] is False and review['full_pipeline_verified'] is False
    assert market.rows('trials') == []


@pytest.mark.parametrize('role', ['official_identity_source', 'registry_lead_sponsor', 'exchange_listing_observation'])
def test_missing_raw_evidence_blocks_cloud_configuration_only_deployments(setup, role):
    market, row, workspace, _ = setup
    entry = next(e for e in row['evidence'] if e['kind'] == role)
    reference = entry.get('capture_reference', entry.get('snapshot_reference'))
    (workspace / reference).unlink()
    result = expansion.expand(market)
    assert result['added'] == [] and market.rows('companies') == []
    blocked = result['blocked_evidence'][0]
    assert blocked['errors'][0]['code'] == 'capture_missing'
    assert blocked['errors'][0]['reference'] == reference


def test_raw_hash_tamper_rejected_even_when_configuration_is_unchanged(setup):
    market, row, workspace, _ = setup
    entry = row['evidence'][1]
    (workspace / entry['capture_reference']).write_bytes(b'tampered')
    result = expansion.expand(market)
    assert not result['added'] and result['blocked_evidence'][0]['errors'][0]['code'] == 'capture_hash_mismatch'


def test_modified_alias_or_review_boundary_requires_another_independent_review(setup):
    market, row, workspace, _ = setup
    changed = copy.deepcopy(row)
    changed['aliases'] = ['Unreviewed subsidiary']
    result = audit.review_mapping(changed, workspace, market.rows('listings'))
    assert result['errors'][0]['code'] == 'configuration_not_independently_reviewed'
    changed = copy.deepcopy(row)
    changed['human_sign_off'] = True
    assert audit.review_mapping(changed, workspace, market.rows('listings'))['status'] == 'blocked'


def test_current_code_reuse_never_installs_historical_identity(setup):
    market, row, workspace, _ = setup
    with market.db() as db:
        db.execute("UPDATE listings SET name='UNRELATED NEW ISSUER'")
    result = expansion.expand(market)
    assert row['id'] in result['missing_listing'] and not market.rows('companies')
    review = audit.review_mapping(row, workspace, market.rows('listings'))
    assert review['errors'][0]['code'] == 'current_listing_mismatch'


def test_exact_lead_is_required_not_a_collaborator(setup):
    market, row, workspace, approve = setup
    entry = row['evidence'][2]
    path = workspace / entry['capture_reference']
    parsed = json.loads(path.read_bytes())
    parsed['protocolSection']['sponsorCollaboratorsModule']['leadSponsor']['name'] = 'Unrelated hospital'
    raw = json.dumps(parsed).encode()
    path.write_bytes(raw)
    entry['sha256'] = hashlib.sha256(raw).hexdigest()
    approve(row)
    result = expansion.expand(market)
    assert not result['added']
    assert result['blocked_evidence'][0]['errors'][0]['code'] == 'registry_exact_lead_mismatch'


def test_missing_exact_lead_in_bounded_query_blocks_installation(setup):
    market, row, workspace, approve = setup
    entry = row['evidence'][2]
    path = workspace / entry['query_capture_reference']
    raw = json.dumps({'studies': [], 'totalCount': 1}).encode()
    path.write_bytes(raw)
    entry['query_sha256'] = hashlib.sha256(raw).hexdigest()
    approve(row)
    result = expansion.expand(market)
    assert result['blocked_evidence'][0]['errors'][0]['code'] == 'registry_query_evidence_mismatch'
    assert not market.rows('companies')


def test_path_escape_or_symlink_cannot_read_other_local_files(setup, tmp_path):
    market, row, workspace, approve = setup
    row['evidence'][1]['capture_reference'] = '../outside.html'
    approve(row)
    assert expansion.expand(market)['blocked_evidence'][0]['errors'][0]['code'] == 'capture_path_escape'
    entry = row['evidence'][1]
    outside = tmp_path / 'outside.html'
    outside.write_bytes(b'outside')
    link = workspace / audit.CAPTURE_PREFIX / 'linked.html'
    link.symlink_to(outside)
    entry.update(capture_reference=str(link.relative_to(workspace)), sha256=hashlib.sha256(b'outside').hexdigest(), bytes=7)
    approve(row)
    assert expansion.expand(market)['blocked_evidence'][0]['errors'][0]['code'] == 'capture_path_escape'


def test_existing_mapping_is_not_overwritten_by_new_source_names(setup):
    market, row, _, _ = setup
    market.add_company('HK', '09999', row['name'], ['Existing different alias'], 'Synthetic previous mapping')
    result = expansion.expand(market)
    assert result['conflicts'] == [row['id']]
    assert json.loads(market.rows('companies')[0]['aliases']) == ['Existing different alias']


def test_pending_proposal_is_actionable_conflict_not_missing_evidence(setup):
    market, row, _, _ = setup
    with market.db() as db:
        db.execute('CREATE TABLE sponsor_proposals (id TEXT,candidate_id TEXT,alias TEXT,status TEXT)')
        db.execute('INSERT INTO sponsor_proposals VALUES (?,?,?,?)', ('synthetic-pending', row['id'], row['aliases'][0], 'pending'))
    result = expansion.expand(market)
    assert not result['added'] and result['blocked_evidence'] == []
    assert result['source_name_verified'] == [row['id']]
    assert result['pending_proposal_conflicts'][0]['proposal_ids'] == ['synthetic-pending']
    with market.db() as db:
        assert db.execute('SELECT status FROM sponsor_proposals').fetchone()[0] == 'pending'
        assert db.execute('SELECT status FROM mapping_source_reviews').fetchone()[0] == 'verified_source_name'


def test_public_row_configuration_matches_independently_frozen_receipts():
    rows = source_mappings()
    assert len(rows) == 25 and len({r['id'] for r in rows}) == 25
    assert {r['id'] for r in rows} == set(audit._REVIEWED_ROWS)
    assert all(audit.row_digest(r) == audit._REVIEWED_ROWS[r['id']]['row_sha256'] for r in rows)
    by_id = {r['id']: r for r in rows}
    assert by_id['HK:02171']['relationship_to_issuer'] == 'consolidated_affiliated_entity'
    assert by_id['HK:09939']['aliases'] == ['Suzhou Kintor Pharmaceutical Inc,']
    assert by_id['HK:02493']['issuer_group'] == by_id['A:688062']['issuer_group']
    assert all(r['review_status'] == 'pending' and r['human_sign_off'] is False and r['drug_ownership_verified'] is False for r in rows)
