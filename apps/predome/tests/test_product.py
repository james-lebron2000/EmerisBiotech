"""Public product projections must preserve target identity and privacy."""
from copy import deepcopy
from datetime import datetime, timezone
import json

from emeris_live import product, platform_store
from emeris_live.store import Store

NOW=datetime(2026,10,21,tzinfo=timezone.utc)


def archived_fixture():
    dashboard=product.build_product(now=NOW)
    trial=next(t for t in dashboard['upcoming'] if len(t['targets'])==4)
    snapshots=[]
    for target in trial['targets']:
        record={'snapshot_id':'snapshot-'+target['case_id'],'prediction_id':'prediction-'+target['case_id'],
                'trial_id':trial['nct_id'],'provenance':'archived_record_import',
                'target':{'endpoint':target['target_definition'][0]['measure'],
                          'population':'registered population','comparison':'registered arms',
                          'success_definition':target['success_rule']},
                'archive_provenance':{'case_id':target['case_id'],'original_record_sha256':target['record_sha256']},
                'forecasts':{'model':{'status':'predicted','probability':.7}}}
        snapshots.append({'record':record,'sha256':'b'*64,'integrity_verified':True})
    return trial,snapshots


def outcome(snapshot):
    record=snapshot['record']
    return {'outcome_id':'outcome-'+record['snapshot_id'],'snapshot_id':record['snapshot_id'],
            'prediction_id':record['prediction_id'],'trial_id':record['trial_id'],'target':record['target'],
            'label':1,'result_date':'2026-10-20','readout_date':'2026-10-20',
            'verification_status':'approved','verified_at':'2026-10-21T00:00:00+00:00',
            'effect_size':{'measure':'HR','value':.7},'confidence_interval':[.6,.9],'p_value':.01,
            'source':{'url':'https://example.org/results','published_at':'2026-10-20','sha256':'c'*64,
                      'private_note':'SECRET SOURCE NOTE'},
            'archive_provenance':record['archive_provenance'],'forecasts':record['forecasts'],
            'assessments':[{'model':'model','scope':'prospective','eligible_for_prospective':True,
                            'reasons':['SECRET REASON'],'sponsor':'SECRET INPUT'}],
            'reviewer_id':'SECRET REVIEWER','reason':'SECRET REVIEW REASON'}


def fake_ledger(monkeypatch,snapshots,outcomes):
    from emeris_live import readout_updates
    monkeypatch.setattr(readout_updates,'approved_public_updates',lambda store:{})
    class Ledger:
        def __init__(self,store):pass
        def list_public_snapshots(self):return deepcopy(snapshots)
        def public_outcome_records(self):return deepcopy(outcomes)
        def evaluation_rows(self,public_only=True):
            return [{'trial_id':r['trial_id'],'snapshot_id':r['snapshot_id'],'eligible_for_prospective':True,
                     'asset':'SECRET PRIVATE INPUT'} for r in outcomes if r['verification_status']=='approved']
    monkeypatch.setattr(platform_store,'PlatformStore',Ledger)


def test_no_ledger_preserves_seven_trials_ten_targets():
    data=product.build_product(now=NOW)
    assert len(data['upcoming'])==7
    assert data['summary']['upcoming_target_count']==10
    assert data['public_verified_outcomes']==[]
    assert data['resolved_trials']==[]
    assert data['summary']['verified_trial_count']==0


def test_one_of_four_approved_does_not_remove_trial(monkeypatch):
    trial,snapshots=archived_fixture()
    fake_ledger(monkeypatch,snapshots,[outcome(snapshots[0])])
    data=product.build_product(object(),now=NOW)
    updated=next(t for t in data['upcoming'] if t['nct_id']==trial['nct_id'])
    assert updated['prediction_status']=='Partially Verified'
    assert updated['verified_target_count']==1 and updated['unresolved_target_count']==3
    assert updated['targets'][0]['actual_outcome']['p_value']==.01
    assert all(t['actual_outcome'] is None for t in updated['targets'][1:])
    assert data['summary']['upcoming_trial_count']==7
    assert data['summary']['upcoming_target_count']==9
    assert data['summary']['verified_trial_count']==1
    assert data['summary']['verified_target_count']==1
    assert data['summary']['public_user_locked_predictions']==0
    assert data['summary']['locked_model_predictions']==20
    assert 'SECRET' not in json.dumps(data)
    assert 'evaluation_rows' not in data


def test_all_four_approved_moves_to_resolved_but_keeps_detail(monkeypatch):
    trial,snapshots=archived_fixture()
    fake_ledger(monkeypatch,snapshots,[outcome(s) for s in snapshots])
    data=product.build_product(object(),now=NOW)
    assert data['summary']['upcoming_trial_count']==6
    assert data['summary']['upcoming_target_count']==6
    assert data['summary']['verified_trial_count']==1
    assert data['summary']['verified_target_count']==4
    assert data['summary']['fully_verified_curated_trial_count']==1
    assert len(data['resolved_trials'])==1
    detail=product.trial_detail(trial['nct_id'],object())
    assert detail['upcoming']['prediction_status']=='Verified'
    assert len(detail['public_verified_outcomes'])==4


def test_wrong_target_trial_private_or_pending_never_joins(monkeypatch):
    trial,snapshots=archived_fixture()
    rows=[]
    for field,value in [('target',{'endpoint':'a different endpoint'}),('trial_id','NCT00000000'),
                        ('snapshot_id','private-snapshot'),('verification_status','pending_review')]:
        row=deepcopy(outcome(snapshots[0]));row[field]=value;rows.append(row)
    fake_ledger(monkeypatch,snapshots,rows)
    data=product.build_product(object(),now=NOW)
    assert data['public_verified_outcomes']==[]
    updated=next(t for t in data['upcoming'] if t['nct_id']==trial['nct_id'])
    assert updated['verified_target_count']==0


def test_wrong_archive_checksum_cannot_resolve_curated_target(monkeypatch):
    trial,snapshots=archived_fixture()
    snapshots[0]['record']['archive_provenance']['original_record_sha256']='wrong-archive'
    fake_ledger(monkeypatch,snapshots,[outcome(snapshots[0])])
    data=product.build_product(object(),now=NOW)
    updated=next(t for t in data['upcoming'] if t['nct_id']==trial['nct_id'])
    assert updated['verified_target_count']==0


def test_new_public_nct_has_detail_without_private_fields_or_archive_claim(tmp_path,monkeypatch):
    monkeypatch.setattr(platform_store,'now',lambda:'2026-10-21T00:00:00+00:00')
    store=Store(tmp_path/'product-store')
    ledger=platform_store.PlatformStore(store)
    target={'endpoint':'Overall survival','population':'All randomized participants',
            'comparison':'drug versus placebo','success_definition':'Prespecified primary analysis statistically significant'}
    run=ledger.create_run('PRIVATE OWNER',{'nct_id':'NCT01234567','trial_name':'PRIVATE NAME',
        'drug':'PRIVATE DRUG','protocol_text':'PRIVATE PROTOCOL'},{
        'forecast_kind':'uncalibrated_llm_research','forecasts':{'model':{'status':'predicted','probability':.7,
        'reasoning':'PRIVATE MODEL REASONING'}}},'model-v1','pipeline-v1','2026-10-20',
        evidence=[{'url':'https://clinicaltrials.gov/study/NCT01234567','availability_date':'2026-10-19'}],target=target)
    assert product.trial_detail('NCT01234567',store) is None
    ledger.lock('PRIVATE OWNER',run['id'],publish=True)
    detail=product.trial_detail('NCT01234567',store)
    assert detail['record_kind']=='user_published_prediction'
    assert detail['upcoming'] is None and detail['historical']==[]
    assert detail['provenance']['curated_archive_equivalent'] is False
    assert len(detail['public_predictions'])==1
    assert 'PRIVATE' not in json.dumps(detail)
    data=product.build_product(store,now=NOW)
    assert data['summary']['public_user_locked_predictions']==1
    assert data['summary']['locked_model_predictions']==21
    assert data['summary']['archive_locked_model_predictions']==20
    assert data['summary']['upcoming_trial_count']==7  # No invented readout window.


def test_private_lock_has_no_public_trial_detail(tmp_path,monkeypatch):
    monkeypatch.setattr(platform_store,'now',lambda:'2026-10-21T00:00:00+00:00')
    store=Store(tmp_path/'private-product-store')
    ledger=platform_store.PlatformStore(store)
    run=ledger.create_run('owner',{'nct_id':'NCT01234567'},{
        'forecast_kind':'uncalibrated_llm_research','forecasts':{'model':{'status':'predicted','probability':.7}}},
        'v1','p1','2026-10-20',target={'endpoint':'OS','population':'ITT','comparison':'A vs B','success_definition':'Significance'})
    ledger.lock('owner',run['id'],publish=False)
    assert product.trial_detail('NCT01234567',store) is None


def test_real_archive_import_review_and_product_join(tmp_path,monkeypatch):
    """Synthetic adjudication in an isolated DB, never a real trial result."""
    monkeypatch.setattr(platform_store,'now',lambda:'2026-10-21T00:00:00+00:00')
    store=Store(tmp_path/'synthetic-adjudication')
    platform_store.import_legacy_archive(store)
    ledger=platform_store.PlatformStore(store)
    public=ledger.list_public_snapshots()
    snapshot=next(s for s in public if s['record']['archive_provenance']['case_id']=='forward-002')
    rec=snapshot['record']
    target=rec['target']
    submitted=ledger.submit_outcome('test-source-submitter',rec['snapshot_id'],{
        'trial_id':rec['trial_id'],'endpoint':target['endpoint'],'population':target['population'],
        'comparison':target['comparison'],'label':1,'readout_date':'2026-10-20',
        'source':{'url':'https://synthetic-test.example/result','published_at':'2026-10-20','sha256':'a'*64},
        'effect_size':{'measure':'HR','value':.7},'confidence_interval':[.6,.9],'p_value':.01,
        'extraction_status':'complete','conflicts':[]},allow_other_owner=True)
    ledger.verify_outcome('test-independent-reviewer',submitted['id'],'approved',
        'Synthetic isolated test, not a real result',dict.fromkeys((
            'endpoint_match','population_match','comparison_match','source_checked',
            'prespecified_analysis','earliest_result_checked','evidence_cutoff_checked'),True))
    data=product.build_product(store,now=NOW)
    trial=next(t for t in data['upcoming'] if t['nct_id']==rec['trial_id'])
    assert len(public)==10
    assert data['summary']['public_user_locked_predictions']==0
    assert data['summary']['locked_model_predictions']==20
    assert trial['verified_target_count']==1 and trial['unresolved_target_count']==3
    target=next(t for t in trial['targets'] if t['case_id']=='forward-002')
    assert target['actual_outcome']['source']['url']=='https://synthetic-test.example/result'
    assert target['actual_outcome']['confidence_interval']==[.6,.9]
    assert len(data['public_verified_outcomes'])==1
    text=json.dumps(data)
    assert 'test-independent-reviewer' not in text
    assert 'test-source-submitter' not in text
    assert 'Synthetic isolated test' not in text


def test_historical_numeric_overlay_keeps_frozen_labels_and_dates():
    data=product.build_product(now=NOW)
    base={r['nct_id']:r for r in product.build_dashboard(NOW)['historical']}
    for r in data['historical']:
        assert r['actual_label']==base[r['nct_id']]['actual_label']
        assert r['result_date']==base[r['nct_id']]['result_date']
        assert r['forecasts']==base[r['nct_id']]['forecasts']
    key=next(r for r in data['historical'] if r['trial_name']=='KEYNOTE-789')
    assert key['actual_label']==0 and len(key['result_details']['endpoints'])==2
    assert [e['p_value'] for e in key['result_details']['endpoints']]==[.0122,.0362]
    assert all(e['prespecified_significance_threshold']['value']==.0117 for e in key['result_details']['endpoints'])
    ev=[r for r in data['historical'] if r['trial_name'].startswith('evolution')]
    assert len(ev)==2 and all(r['result_details']['effect_size'] is None for r in ev)


def test_modified_detail_source_fails_closed(tmp_path,monkeypatch):
    import shutil,pytest
    shutil.copytree(product.ROOT/'dashboard_data',tmp_path/'dashboard_data')
    monkeypatch.setattr(product,'ROOT',tmp_path)
    details=json.loads((tmp_path/'dashboard_data/historical_result_details.json').read_text())
    (tmp_path/details['records'][0]['source']['path']).write_text('tampered')
    with pytest.raises(ValueError,match='integrity'):product.attach_historical_details(product.build_dashboard(NOW))
