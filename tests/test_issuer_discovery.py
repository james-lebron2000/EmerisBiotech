"""Discovery must expose uncertainty and resist false issuer attribution."""
import re
from copy import deepcopy

import pytest

from vbt.issuer_discovery import discover, initial_mappings, seed_matches_listing, unsupported_listings


def listing(name,ticker='TEST',market='US',**extra):
 return {'market':market,'ticker':ticker,'name':name,'industry':'','source':'Synthetic test fixture','observed':'2026-10-17T00:00:00+00:00',**extra}


def test_seed_contract_cross_market_evidence_and_entity_grouping():
 rows=initial_mappings()
 assert len(rows)>=30 and len({r['id'] for r in rows})==len(rows)
 assert {r['market'] for r in rows}=={'US','A','HK'}
 assert len({r['issuer_group'] for r in rows})<len(rows)
 for row in rows:
  assert row['review_status']=='pending'
  assert 1<=len(row['aliases'])<=10 and len(row['provenance'])<=2000
  assert row['source'].startswith('https://')
  registry=[e for e in row['evidence'] if e['kind']=='registry_lead_sponsor']
  assert {e['sponsor'] for e in registry}==set(row['aliases'])
  assert all(re.fullmatch(r'NCT\d{8}',e['nct_id']) for e in registry)
  assert all(e['url']=='https://clinicaltrials.gov/study/'+e['nct_id'] for e in registry)
  assert all(e.get('availability')=='not_checked_by_platform' and 'observed' not in e for e in row['evidence'] if e['kind']=='company_primary_source')
  assert 'ownership' in row['provenance']


def test_seed_copies_cannot_mutate_later_discovery():
 one=initial_mappings();one[0]['aliases'].append('Unrelated Sponsor')
 one[0]['evidence'][0]['url']='https://example.invalid/'
 assert 'Unrelated Sponsor' not in initial_mappings()[0]['aliases']
 assert initial_mappings()[0]['evidence'][0]['url']!='https://example.invalid/'


def test_listing_name_reuse_does_not_install_old_sponsor():
 row=discover([listing('Unrelated Therapeutics',ticker='PFE')])[0]
 assert row['classification']=='uncertain'
 assert 'suggested_aliases' not in row
 assert any(e['kind']=='seed_identity_conflict' for e in row['evidence'])
 assert not seed_matches_listing(initial_mappings()[0],listing('Unrelated',ticker='PFE'))


def test_curated_classification_remains_pending_and_records_both_sources():
 row=discover([listing('Pfizer Inc.',ticker='PFE',review_status='approved')])[0]
 assert row['classification']=='drug_developer' and row['review_status']=='pending'
 assert row['suggested_aliases']==['Pfizer']
 assert {e['kind'] for e in row['evidence']}=={'listing_metadata','company_primary_source','registry_lead_sponsor'}


def test_all_listings_retained_and_classified_with_reasons():
 inputs=[listing('Synthetic Therapeutics',ticker='DRUG'),listing('Synthetic Biotechnology',ticker='BIO'),listing('Synthetic Retail',ticker='OTHER'),listing('Synthetic',ticker='600001',market='A',industry='C27 医药制造业')]
 rows=discover(inputs)
 assert len(rows)==4
 by_id={r['id']:r for r in rows}
 assert by_id['US:DRUG']['classification']=='candidate'
 assert by_id['US:BIO']['classification']=='uncertain'
 assert by_id['US:OTHER']['classification']=='unclassified'
 assert by_id['A:600001']['classification']=='candidate'
 assert all(r['reason'] and r['evidence'] and r['review_status']=='pending' for r in rows)
 assert all('suggested_aliases' not in r for r in rows)


@pytest.mark.parametrize('name',[
 'Synthetic Biotech ETF','Synthetic Pharma Fund','Synthetic Acquisition Corp.',
 'Synthetic Pharma Warrants','Synthetic Medical Devices','Synthetic CDMO',
 'IQVIA Holdings Inc.','Medpace Holdings Inc.','Charles River Laboratories',
 '药明康德','康龙化成','泰格医药','合成药房','合成医疗器械',
])
def test_non_developer_signals_excluded_not_installed(name):
 row=discover([listing(name)])[0]
 assert row['classification']=='excluded'
 assert 'suggested_aliases' not in row


def test_ads_security_is_not_excluded_by_instrument_filter():
 row=discover([listing('Zai Lab Limited - American Depositary Shares',ticker='ZLAB')])[0]
 assert row['classification']=='drug_developer'


def test_duplicate_connector_rows_do_not_inflate_issuers_and_are_deterministic():
 a=listing('Pfizer Inc.',ticker='PFE',source='USNASDAQ')
 b=listing('Pfizer Inc.',ticker='PFE',source='US')
 original=deepcopy([a,b])
 rows=discover([a,b])
 assert len(rows)==1 and len([e for e in rows[0]['evidence'] if e['kind']=='listing_metadata'])==2
 assert rows==discover([b,a]) and [a,b]==original


def test_multiple_listings_share_group_but_keep_market_coverage():
 rows=discover([listing('君实生物',ticker='688180',market='A'),listing('JUNSHI BIO-U',ticker='1877',market='HK')])
 assert {r['id'] for r in rows}=={'A:688180','HK:01877'}
 assert len({r['issuer_group'] for r in rows})==1
 assert all(r['classification']=='drug_developer' for r in rows)


def test_verified_exchange_short_name_change_has_explicit_identity_evidence():
 row=discover([listing('SKB BIO',ticker='6990',market='HK')])[0]
 assert row['classification']=='drug_developer'
 assert any(e['kind']=='exchange_identity_notice' for e in row['evidence'])
 assert row['suggested_aliases']==['Sichuan Kelun-Biotech Biopharmaceutical Co., Ltd.']


@pytest.mark.parametrize('row',[
 listing('X',market='EU'),listing('X',ticker='../outside'),listing('X',ticker='1234567',market='A'),listing('X',ticker='ABC',market='HK'),listing('',ticker='TEST'),
])
def test_malformed_listing_fails_closed(row):
 with pytest.raises(ValueError):discover([row],strict=True)
 assert discover([row])==[]
 assert len(unsupported_listings([row]))==1


def test_unsupported_exchange_symbols_quarantined_without_losing_valid_companies():
 rows=[listing('Pfizer preferred',ticker='PFE$A'),listing('Pfizer Inc.',ticker='PFE'),listing('Synthetic Preferred',ticker='SYN/A')]
 assert [r['id'] for r in discover(rows)]==['US:PFE']
 quarantined=unsupported_listings(rows)
 assert {r['ticker'] for r in quarantined}=={'PFE$A','SYN/A'}
 assert all(r['reason'] for r in quarantined)


def test_empty_universe_does_not_invent_seed_listings():
 assert discover([])==[]
