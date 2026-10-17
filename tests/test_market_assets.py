"""Synthetic observations test attribution boundaries; no real asset claims."""
import hashlib
import json

import pytest

from vbt.market import Market
from vbt.market_assets import build_assets


@pytest.fixture
def market(tmp_path):
    return Market(tmp_path/'workspace', tmp_path/'state')


def put_trial(market, cid, nct, interventions, updated='2018-04-01', observed='2026-10-17T01:00:00+00:00', phases=None):
    data = dict(interventions=interventions, phases=phases or ['PHASE2'], conditions=['Synthetic indication'],
                source_updated=updated, source_url='https://clinicaltrials.gov/study/'+nct)
    serial = json.dumps(data)
    with market.db() as db:
        db.execute('INSERT OR REPLACE INTO trials VALUES (?,?,?,?,?)', (cid,nct,serial,hashlib.sha256(serial.encode()).hexdigest(),observed))


def drug(name, roles=None):
    value = {'name': name, 'type': 'DRUG'}
    if roles is not None:
        value['arm_roles'] = roles
    return value


def run(market, rid, cid, day, status='succeeded'):
    stamp = f'2026-10-{day:02}T01:00:00+00:00'
    with market.db() as db:
        db.execute('INSERT INTO runs VALUES (?,?,?,?,?,?,?,?)', (rid,'ClinicalTrials.gov',cid,stamp,stamp,status,1,''))


def lineage(market,cid,nct,rid):
    with market.db() as db:
        db.execute('INSERT INTO trial_lineage VALUES (?,?,?,?,?)', (cid,nct,'2026-10-01','2026-10-02',rid))


def test_exact_name_normalization_preserves_company_and_distinct_codes(market):
    put_trial(market,'US:ONE','NCT00000001',[drug('ＡＢＣ  １２３'),drug('ABC-123'),drug('ABC124')])
    put_trial(market,'US:ONE','NCT00000002',[drug(' abc\t123 ')])
    put_trial(market,'HK:00001','NCT00000003',[drug('ABC 123')])
    assets = build_assets(market)
    assert len(assets) == 4
    one = next(a for a in assets if a['company']=='US:ONE' and a['normalized_name']=='abc 123')
    assert one['trials'] == ['NCT00000001','NCT00000002']
    other = next(a for a in assets if a['company']=='HK:00001')
    assert one['id'] != other['id']
    assert one['id'] == next(a['id'] for a in build_assets(market,'US:ONE') if a['normalized_name']=='abc 123')
    assert {a['company'] for a in build_assets(market,'US:ONE')} == {'US:ONE'}
    assert build_assets(market,"US:ONE' OR 1=1 --") == []


def test_comparators_and_combination_therapy_never_establish_ownership(market):
    put_trial(market,'US:ONE','NCT00000001',[
        drug('Candidate',['EXPERIMENTAL']), drug('Effective comparator',['ACTIVE_COMPARATOR']),
        drug('Standard chemotherapy',['EXPERIMENTAL']),drug('Unknown role'),
        drug('Placebo',['PLACEBO_COMPARATOR']),drug('Saline',['PLACEBO_COMPARATOR']),
        drug('Normal saline',['ACTIVE_COMPARATOR']), drug('Experimental saline',['EXPERIMENTAL']),
    ])
    assets = {a['drug_name']: a for a in build_assets(market)}
    assert 'Placebo' not in assets and 'Saline' not in assets
    assert assets['Candidate']['role_status'] == 'experimental_arm'
    assert assets['Standard chemotherapy']['role_status'] == 'experimental_arm'
    assert assets['Effective comparator']['role_status'] == 'comparator_only'
    assert assets['Normal saline']['role_status'] == 'comparator_only'
    assert assets['Experimental saline']['role_status'] == 'experimental_arm'
    assert assets['Unknown role']['role_status'] == 'unresolved'
    assert all(a['ownership']=='unverified' for a in assets.values())


def test_mixed_roles_retain_each_trial_role(market):
    put_trial(market,'US:ONE','NCT00000001',[drug('Candidate',['ACTIVE_COMPARATOR'])])
    put_trial(market,'US:ONE','NCT00000002',[drug('Candidate',['EXPERIMENTAL'])], phases=['PHASE3'])
    asset = build_assets(market)[0]
    assert asset['role_status'] == 'experimental_arm'
    assert {s['role_status'] for s in asset['sources']} == {'experimental_arm','comparator_only'}
    assert asset['phases'] == ['PHASE2','PHASE3']
    assert [source['phases'] for source in asset['sources']] == [['PHASE2'], ['PHASE3']]
    assert all(source['conditions']==['Synthetic indication'] for source in asset['sources'])
    assert asset['ownership'] == 'unverified'


def test_missing_role_does_not_upgrade_comparator_to_asset(market):
    put_trial(market,'US:ONE','NCT00000001',[drug('Candidate',['ACTIVE_COMPARATOR'])])
    put_trial(market,'US:ONE','NCT00000002',[drug('Candidate')])
    assert build_assets(market)[0]['role_status']=='unresolved'


def test_old_source_date_is_not_replaced_by_new_collection_time(market):
    put_trial(market,'US:ONE','NCT00000001',[drug('Candidate')])
    asset = build_assets(market)[0]
    assert asset['source_updated']=='2018-04-01'
    assert asset['observed']=='2026-10-17T01:00:00+00:00'
    assert asset['freshness']=='unknown'
    assert asset['sources'][0]['data_sha256']
    assert asset['sources'][0]['url'].endswith('NCT00000001')


def test_retained_observations_are_not_reported_as_termination(market):
    run(market,'old','US:ONE',1)
    run(market,'new','US:ONE',2)
    run(market,'failed','US:ONE',3,'failed')
    put_trial(market,'US:ONE','NCT00000001',[drug('Old candidate')])
    put_trial(market,'US:ONE','NCT00000002',[drug('Current candidate')])
    lineage(market,'US:ONE','NCT00000001','old')
    lineage(market,'US:ONE','NCT00000002','new')
    assets = {a['drug_name']: a for a in build_assets(market)}
    old = assets['Old candidate']
    assert old['freshness']=='not_seen_in_latest'
    assert old['retained_trials']==['NCT00000001']
    assert old['sources'][0]['latest_run']=='new'
    assert old['sources'][0]['latest_attempt_status']=='failed'
    assert 'terminated' not in json.dumps(old).lower()
    assert assets['Current candidate']['freshness']=='seen_in_latest'


def test_mixed_freshness_is_preserved_per_source(market):
    run(market,'old','US:ONE',1)
    run(market,'new','US:ONE',2)
    for number, rid in [(1,'old'),(2,'new')]:
        nct=f'NCT{number:08}'
        put_trial(market,'US:ONE',nct,[drug('Candidate')])
        lineage(market,'US:ONE',nct,rid)
    asset=build_assets(market)[0]
    assert asset['freshness']=='seen_in_latest'
    assert asset['retained_trials']==['NCT00000001']
    assert {s['freshness'] for s in asset['sources']}=={'seen_in_latest','not_seen_in_latest'}


def test_legacy_without_lineage_remains_unknown_and_view_is_read_only(market):
    put_trial(market,'US:ONE','NCT00000001',[drug('Candidate')])
    with market.db() as db:
        db.execute('DROP TABLE trial_lineage')
        before = list(db.iterdump())
    asset = build_assets(market)[0]
    assert asset['freshness']=='unknown' and asset['sources'][0]['last_run'] is None
    with market.db() as db:
        assert list(db.iterdump()) == before


def test_corrupt_record_is_visible_failure(market):
    with market.db() as db:
        db.execute('INSERT INTO trials VALUES (?,?,?,?,?)',('US:ONE','NCT00000001','{','bad','2026-10-17'))
    with pytest.raises(ValueError, match='US:ONE / NCT00000001'):
        build_assets(market)
