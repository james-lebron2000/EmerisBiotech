"""Observed issuer associations remain bounded and reject recycled codes/names."""
import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from vbt.china_issuer_sources import source_mappings as first_expansion
from vbt.china_issuer_sources_v2 import matches_listing, source_gaps, source_mappings
from vbt.issuer_discovery import initial_mappings


def test_current_listing_key_and_name_are_both_required():
    for row in source_mappings():
        listing = {k: row[k] for k in ('market', 'ticker', 'name')}
        assert matches_listing(row, listing)
        assert matches_listing(row, dict(listing, ticker=row['ticker'].lstrip('0') or '0'))
        assert not matches_listing(row, dict(listing, name='Unrelated Biotechnology Holdings'))
        assert not matches_listing(row, dict(listing, name=''))
        assert not matches_listing(row, dict(listing, market='US'))
        assert not matches_listing(row, dict(listing, ticker='99999999'))


@pytest.mark.parametrize('ticker', ['../6660', '06660A', '6660.0', '-6660', '66/60'])
def test_malformed_security_keys_do_not_create_an_issuer_match(ticker):
    row = next(r for r in source_mappings() if r['id'] == 'HK:06660')
    assert not matches_listing(row, {'market': 'HK', 'ticker': ticker, 'name': 'AIM VACCINE'})


def test_batch_adds_observed_issuers_without_duplicate_prior_mappings():
    rows = source_mappings()
    assert len(rows) == 25
    assert sum(r['market'] == 'A' for r in rows) == 10
    assert sum(r['market'] == 'HK' for r in rows) == 15
    assert len({r['issuer_group'] for r in rows}) == 24
    old_ids = {r['id'] for r in initial_mappings() + first_expansion()}
    assert not (old_ids & {r['id'] for r in rows})
    assert len({r['id'] for r in rows}) == len(rows)
    for row in rows:
        assert row['id'] == row['market'] + ':' + row['ticker']
        assert row['listing_names'] == [row['name']]
        assert row['review_status'] == 'pending'
        assert row['human_sign_off'] is False
        assert row['drug_ownership_verified'] is False
        assert 'does not establish intervention ownership' in row['provenance']
        assert len(row['provenance']) <= 2000


def test_each_alias_has_a_specific_registry_lead_and_captured_primary_identity():
    for row in source_mappings():
        registry = [e for e in row['evidence'] if e['kind'] == 'registry_lead_sponsor']
        assert set(row['aliases']) == {e['sponsor'] for e in registry}
        primary = [e for e in row['evidence'] if e['kind'] == 'official_identity_source']
        assert len(primary) == 1 and primary[0]['url'] == row['official_url']
        for e in primary:
            assert e['url'].startswith('https://')
            assert re.fullmatch(r'[a-f0-9]{64}', e['sha256'])
            assert e['bytes'] > 0
            assert e['capture_reference'].startswith('artifacts/market/china_expansion_v2/')
        for e in registry:
            assert re.fullmatch(r'NCT\d{8}', e['nct_id'])
            assert e['nct_id'] == row['nct']
            assert e['url'] == 'https://clinicaltrials.gov/study/' + e['nct_id']
            assert re.fullmatch(r'[a-f0-9]{64}', e['sha256'])
            assert re.fullmatch(r'[a-f0-9]{64}', e['query_sha256'])
            assert e['query_total'] > 0 and e['query_exact_lead_examples']
            assert parse_qs(urlsplit(e['query_url']).query)['query.spons'] == ['"' + e['sponsor'] + '"']
            assert 'not proof of complete' in e['scope']


def test_contractual_control_is_not_falsely_reported_as_equity_ownership():
    row = next(r for r in source_mappings() if r['id'] == 'HK:02171')
    assert row['relationship_to_issuer'] == 'consolidated_affiliated_entity'
    assert 'legal equity ownership' in row['provenance']
    assert row['drug_ownership_verified'] is False


def test_a_h_crosslisting_preserves_security_keys_and_shared_group():
    rows = [r for r in source_mappings() if r['issuer_group'] == 'mabwell']
    assert {r['id'] for r in rows} == {'A:688062', 'HK:02493'}
    assert rows[0]['aliases'] == rows[1]['aliases']
    assert not matches_listing(rows[0], rows[1])
    assert not matches_listing(rows[1], rows[0])


def test_unresolved_queries_are_gaps_and_never_installation_aliases():
    gaps = source_gaps()
    assert {g['id'] for g in gaps} == {'HK:02126', 'HK:02181', 'A:688578', 'A:300765'}
    assert not ({g['id'] for g in gaps} & {r['id'] for r in source_mappings()})
    for gap in gaps:
        assert gap['status'] == 'needs_identity_review'
        assert gap['query_url'].startswith('https://clinicaltrials.gov/api/v2/studies?')
        assert re.fullmatch(r'[a-f0-9]{64}', gap['query_sha256'])
        assert 'aliases' not in gap
    cspc = next(g for g in gaps if g['id'] == 'A:300765')
    assert cspc['name'] == '石药创新'
    assert 'broader CSPC group pipeline' in cspc['reason']


def test_mutations_do_not_change_canonical_source_configuration():
    rows = source_mappings()
    rows[0]['aliases'].append('Different Sponsor')
    rows[0]['evidence'][0]['listing_name'] = 'Different Name'
    assert source_mappings()[0]['aliases'] != rows[0]['aliases']
    assert source_mappings()[0]['evidence'][0]['listing_name'] != 'Different Name'
    gaps = source_gaps()
    gaps[0]['reason'] = 'changed'
    assert source_gaps()[0]['reason'] != 'changed'


def test_json_handoff_exactly_matches_offline_configuration():
    path = Path(__file__).resolve().parents[1] / 'examples/china_expansion_v2_2026-10-17.json'
    payload = json.loads(path.read_text())
    assert payload['mappings'] == source_mappings()
    assert payload['gaps'] == source_gaps()
    assert payload['schema_version'] == 'china-issuer-expansion-2'
    assert 'not a complete' in payload['scope']
    assert 'no human sign-off' in payload['scope']
