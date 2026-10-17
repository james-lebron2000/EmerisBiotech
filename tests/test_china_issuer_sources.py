"""A/HK expansion seeds preserve observed identity and reject false attribution."""
import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from vbt.china_issuer_sources import matches_listing, source_gaps, source_mappings


def test_observed_mapping_contract_and_primary_evidence():
    rows = source_mappings()
    assert len(rows) == 23
    assert len({r['id'] for r in rows}) == len(rows)
    assert len({r['issuer_group'] for r in rows}) == 21
    assert {r['market'] for r in rows} == {'A', 'HK'}
    for row in rows:
        assert row['id'] == row['market'] + ':' + row['ticker']
        assert row['review_status'] == 'pending'
        assert row['group'] == row['issuer_group']
        assert row['legal_name'] and row['official_url'].startswith('https://')
        assert len(row['provenance']) <= 2000 and 'ownership' in row['provenance']
        observed = [e for e in row['evidence'] if e['kind'] == 'registry_lead_sponsor']
        assert {e['sponsor'] for e in observed} == set(row['aliases'])
        assert any(e['kind'] == 'official_identity_source' for e in row['evidence'])
        assert any(e['kind'] == 'exchange_listing_observation' for e in row['evidence'])
        for evidence in observed:
            assert re.fullmatch(r'NCT\d{8}', evidence['nct_id'])
            assert evidence['url'] == 'https://clinicaltrials.gov/study/' + evidence['nct_id']
            assert re.fullmatch(r'[a-f0-9]{64}', evidence['sha256'])
            assert re.fullmatch(r'[a-f0-9]{64}', evidence['query_sha256'])
            assert evidence['query_exact_lead_examples'] and evidence['query_total'] > 0
            params = parse_qs(urlsplit(evidence['query_url']).query)
            assert params['query.spons'] == ['"' + evidence['sponsor'] + '"']


def test_current_listing_match_requires_key_and_observed_name():
    for row in source_mappings():
        listing = {'market': row['market'], 'ticker': row['ticker'], 'name': row['listing_names'][0]}
        assert matches_listing(row, listing)
        assert matches_listing(row, dict(listing, ticker=row['ticker'].lstrip('0') or '0'))
        assert not matches_listing(row, dict(listing, name='Unrelated Pharmaceutical Issuer'))
        assert not matches_listing(row, dict(listing, ticker='9999999'))
        assert not matches_listing(row, dict(listing, market='US'))
        assert not matches_listing(row, dict(listing, name=''))
        assert not matches_listing(row, dict(listing, name=None))


def test_hk_short_name_change_not_guessed_from_old_translations():
    row = next(r for r in source_mappings() if r['id'] == 'HK:01177')
    assert matches_listing(row, {'market': 'HK', 'ticker': '1177', 'name': 'SBP GROUP'})
    assert not matches_listing(row, {'market': 'HK', 'ticker': '1177', 'name': 'SINO BIOPHARM'})
    assert not matches_listing(row, {'market': 'HK', 'ticker': '1177', 'name': 'UNRELATED SBP GROUP'})


def test_cross_listing_group_does_not_conflate_security_keys():
    fosun = [r for r in source_mappings() if r['issuer_group'] == 'fosun']
    assert {r['id'] for r in fosun} == {'A:600196', 'HK:02196'}
    assert fosun[0]['aliases'] == fosun[1]['aliases']
    assert not matches_listing(fosun[0], {'market': fosun[1]['market'], 'ticker': fosun[1]['ticker'], 'name': fosun[1]['name']})


@pytest.mark.parametrize('ticker', ['../1177', '01177A', '1177.0', '-1177', '1/177'])
def test_malformed_tickers_never_normalize_into_valid_mapping(ticker):
    row = next(r for r in source_mappings() if r['id'] == 'HK:01177')
    assert not matches_listing(row, {'market': 'HK', 'ticker': ticker, 'name': 'SBP GROUP'})


def test_independent_copies_and_unresolved_gaps():
    mappings = source_mappings()
    mappings[0]['aliases'].append('Unrelated Sponsor')
    mappings[0]['evidence'][0]['listing_name'] = 'Unrelated'
    assert 'Unrelated Sponsor' not in source_mappings()[0]['aliases']
    assert source_mappings()[0]['evidence'][0]['listing_name'] != 'Unrelated'
    gaps = source_gaps()
    assert {g['id'] for g in gaps} == {'HK:00867', 'A:002019'}
    assert not ({g['id'] for g in gaps} & {r['id'] for r in source_mappings()})
    gaps[0]['reason'] = 'mutated'
    assert source_gaps()[0]['reason'] != 'mutated'


def test_reviewable_json_export_matches_offline_configuration():
    path = Path(__file__).resolve().parents[1] / 'examples/china_expansion_2026-10-17.json'
    payload = json.loads(path.read_text())
    assert payload['mappings'] == source_mappings()
    assert payload['gaps'] == source_gaps()
    assert 'not a complete' in payload['scope']
