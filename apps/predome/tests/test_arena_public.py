"""Public projection fixtures never call providers or alter a live ledger."""
from copy import deepcopy
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from emeris_live import arena_public as api, platform_store
from emeris_live.arena_store import ArenaLedger, instant
from emeris_live.store import Store
from test_arena_core import finish, outcome, registered, spec
from arena_scientific_fixture import scientifically_accept


def row(**changes):
    value = dict(id='run-a', target_id='t1', cohort_id='c1', model_id='kimi-k3',
        requested_model='kimi-k3', returned_model='kimi-k3-fixed', status='predicted',
        probability=.7, reasoning='Earlier efficacy evidence supports the target, but uncertainty remains.',
        track='same_evidence', condition='clinical_only', scope='prospective_challenge',
        submission_on_time=True, generated_at_utc='2026-10-21T11:00:00Z',
        locked_at_utc='2026-10-21T12:00:00Z', evidence_ids=['e1'],
        packet={'evidence':[{'id':'e1', 'source_url':'https://example.org/frozen-source',
            'source_sha256':'a'*64, 'text':'PUBLIC source excerpt', 'title':'Primary clinical source',
            'kind':'clinical_prior', 'public_date':'2026-10-19'}]})
    value.update(changes)
    return value


def target(**changes):
    value = dict(id='t1', cohort_id='c1', nct_id='NCT00000001', trial_name='Isolated trial',
        drug='Fixture drug', sponsor='Fixture sponsor', indication='Fixture population',
        endpoint='Prespecified PFS', population='ITT', comparison='A vs B',
        success_definition='Multiplicity controlled primary endpoint met', analysis_timepoint='Primary',
        information_cutoff_utc='2026-10-20T00:00:00Z', submission_deadline_utc='2026-10-22T00:00:00Z',
        status='pending', scope='prospective_challenge', readout={'label':'2026 H2'}, result=None)
    value.update(changes)
    return value


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    frozen={'targets':[target()], 'runs':[row()], 'cohorts':[{'id':'c1',
        'registered_at':'2026-10-20T00:00:00Z', 'protocol':{'season_kind':'registered_12_project_season'}}]}
    monkeypatch.setattr(api, 'read_frozen', lambda store:deepcopy(frozen))
    monkeypatch.setattr(api, 'read_archived', lambda store:([],[],{}))
    monkeypatch.setattr(api, 'ROOT', tmp_path)
    app=FastAPI();app.state.store=SimpleNamespace(path=tmp_path/'isolated-runtime')
    app.include_router(api.router)
    return TestClient(app),frozen,app


def all_keys(value):
    if isinstance(value,dict):
        for key,item in value.items():
            yield key
            yield from all_keys(item)
    elif isinstance(value,list):
        for item in value:yield from all_keys(item)


def test_allowlist_removes_internal_metadata_at_every_level(fixture):
    client,frozen,_=fixture
    extra={'prompt':'PRIVATE PROMPT','usage':{'tokens':123},'cost_usd':.12,
        'provider':'openai_compatible','provider_config_snapshot':{'api_key':'PRIVATE KEY'},
        'input_sha256':'b'*64,'attempts':[{'trace':'PRIVATE TRACE'}],
        'evaluation_rows':[{'private':'PRIVATE METRIC'}],'database_path':'PRIVATE PATH'}
    frozen['targets'][0].update(extra)
    frozen['targets'][0]['readout'].update(extra)
    frozen['runs'][0].update(extra)
    frozen['runs'][0]['packet']['evidence'][0].update(extra)
    frozen['cohorts'][0]['protocol'].update(extra)
    response=client.get('/api/arena/public')
    assert response.status_code==200
    view=response.json()
    keys=set(all_keys(view))
    assert not (keys & set(extra))
    assert not any(any(term in key for term in ('sha256','prompt','token','usage','cost','trace','config','metrics')) for key in keys)
    assert 'PRIVATE' not in json.dumps(view)
    run=view['runs'][0]
    assert run['model_id']=='kimi-k3' and run['requested_model']=='kimi-k3'
    assert run['model_version']=='kimi-k3-fixed'
    assert run['probability'] is None and run['confidence'] is None
    assert run['calibration_status']=='not_established' and run['predicted_outcome']=='Success'
    assert view['current_cohort_id']=='c1'
    assert run['sources'][0]['source_snapshot_url'] is None
    assert view['targets'][0]['phase'] is None and view['targets'][0]['design'] is None


@pytest.mark.parametrize('value,expected',[(0,'Failure'),(.49,'Failure'),(.5,'Uncertain'),(.51,'Success'),(1,'Success'),
    (None,'Uncertain'),(True,'Uncertain'),('0.8','Uncertain'),(float('nan'),'Uncertain'),(float('inf'),'Uncertain'),(-.1,'Uncertain'),(1.1,'Uncertain')])
def test_direction_never_turns_invalid_or_uncalibrated_value_into_probability(value,expected):
    result=api.run_view(row(probability=value),target(),[])
    assert result['predicted_outcome']==expected
    assert result['probability'] is None and result['confidence'] is None


@pytest.mark.parametrize('status',['failed','timed_out','pending','abstain','abstained','withdrawn','unavailable'])
def test_nonanswers_stay_uncertain_with_no_error_details(status):
    result=api.run_view(row(status=status,reasoning='PRIVATE provider stack trace with key'),target(),[])
    assert result['predicted_outcome']=='Uncertain'
    if status not in ('abstain','abstained'):assert 'PRIVATE' not in result['brief_reason']


def test_blocking_evidence_suppresses_direction_and_reason_but_warning_does_not():
    run=row(input_evidence_issues=[{'severity':'blocking','note':'PRIVATE review note'}])
    result=api.run_view(run,target(),[])
    assert result['predicted_outcome']=='Uncertain' and result['match'] is None
    assert 'PRIVATE' not in json.dumps(result)
    run['input_evidence_issues'][0]['severity']='warning'
    assert api.direction(run)=='Success'


@pytest.mark.parametrize('directions,expected,agreement',[
    (['Success','Success','Failure','Uncertain'],'Uncertain',.5),
    (['Success','Success','Success','Uncertain'],'Success',.75),
    (['Uncertain']*4,'Uncertain',1),([], 'Uncertain',None)])
def test_consensus_requires_strict_majority_of_all_assignments(directions,expected,agreement):
    result=api.consensus([{'predicted_outcome':d} for d in directions])
    assert result['predicted_outcome']==expected and result['agreement']==agreement
    assert result['assigned']==result['agreement_denominator']==len(directions)
    assert sum(result['counts'].values())==len(directions)
    assert result['average_probability'] is None and result['confidence'] is None


def test_mixed_tracks_are_not_pooled_and_withdrawal_remains_in_denominator(fixture):
    client,frozen,_=fixture
    frozen['runs'] += [row(id='r2',status='withdrawn',probability=None),
        row(id='r3',track='shared_agent',model_id='hidden-shared'),
        row(id='r4',condition='with_patents',model_id='hidden-patent'),
        row(id='r5',track='open_agent',model_id='hidden-agent')]
    view=client.get('/api/arena/public/targets/t1').json()
    assert len(view['runs'])==2 and view['consensus']['assigned']==2
    assert view['consensus']['predicted_outcome']=='Uncertain'
    assert view['consensus']['counts']=={'Success':1,'Failure':0,'Uncertain':1}
    assert 'hidden-' not in json.dumps(view)


@pytest.mark.parametrize('label,expected',[('mixed','mixed'),('inconclusive','inconclusive'),('not_adjudicable','inconclusive'),(0,'failure'),(1,'success'),(False,None),(True,None),('unknown',None)])
def test_actual_labels_remain_exact_and_nonbinary_never_match(label,expected):
    t=target(status='adjudicated',record_kind='historical_replay',
             result={'label':label,'first_public_at':'2026-10-23T00:00:00Z'})
    result=api.result_view(t)
    assert result['label']==expected
    match=api.match_result(row(),t,'Success')
    assert result['scoring_eligible'] is False and match is None


def accepted_target_and_run(tmp_path, label='success'):
    ledger, t, ids = registered(tmp_path / 'accepted')
    for ident, model in zip(ids, ('A', 'B')):
        finish(ledger, ident, returned_model=model + '-fixed')
    result = {**outcome(), 'label': label}
    for reviewer in ('synthetic-one', 'synthetic-two'):
        ledger.record_result(t['id'], result, reviewer_id=reviewer)
    scientifically_accept(ledger, t)
    state = ledger.snapshot()
    return state['targets'][0], state['runs'][0]


@pytest.mark.parametrize('label,expected', [('success', True), ('failure', False)])
def test_documented_scientific_acceptance_enables_real_snapshot_direction_match(tmp_path,label,expected):
    t, run = accepted_target_and_run(tmp_path, label)
    public = api.result_view(t)
    assert public['review_status'] == 'accepted' and public['scoring_eligible'] is True
    assert public['label'] == label and api.match_result(run, t, 'Success') is expected


@pytest.mark.parametrize('change', ['submission_false', 'submission_missing', 'late_lock',
    'before_cutoff', 'after_lock', 'after_deadline', 'naive_generation', 'late_tsa'])
def test_result_match_requires_complete_valid_preresult_timing(tmp_path,change):
    t, run = accepted_target_and_run(tmp_path)
    assert api.match_result(run, t, 'Success') is True
    if change == 'submission_false': run['submission_on_time'] = False
    elif change == 'submission_missing': run['submission_on_time'] = None
    elif change == 'late_lock': run['locked_at_utc'] = t['result']['first_public_at']
    elif change == 'before_cutoff':
        run['generated_at_utc'] = (instant(t['information_cutoff_utc']) - timedelta(seconds=1)).isoformat()
    elif change == 'after_lock':
        run['generated_at_utc'] = (instant(run['locked_at_utc']) + timedelta(seconds=1)).isoformat()
    elif change == 'after_deadline':
        run['locked_at_utc'] = (instant(t['submission_deadline_utc']) + timedelta(seconds=1)).isoformat()
    elif change == 'naive_generation':
        run['generated_at_utc'] = instant(run['generated_at_utc']).replace(tzinfo=None).isoformat()
    elif change == 'late_tsa':
        run['timestamp_proof'] = {'status': 'attested', 'generated_at_utc': t['result']['first_public_at']}
    assert api.match_result(run,t,'Success') is None


def test_announced_result_is_not_an_adjudicated_hit_and_archive_replay_is_not_hit():
    t=target(status='announced',result={'label':'success','first_public_at':'2026-10-23T00:00:00Z'})
    assert api.match_result(row(),t,'Success') is None
    t['status']='adjudicated'
    r=row(scope='archived_research',submission_on_time=None)
    assert api.match_result(r,t,'Success') is None
    t['record_kind'] = 'historical_replay'
    assert api.result_view(t)['label'] == 'success'
    assert api.match_result(r,t,'Success') is None
    t['result']['first_public_at']='2026-10-01T00:00:00Z'
    assert api.match_result(r,t,'Success') is None


def registry(tmp_path,monkeypatch):
    monkeypatch.setattr(api,'ROOT',tmp_path)
    name='arena_data/source_snapshots/isolated-registry.json'
    p=tmp_path/name;p.parent.mkdir(parents=True)
    raw=json.dumps({'protocolSection':{'identificationModule':{'nctId':'NCT00000001'},
        'designModule':{'phases':['PHASE3'],'designInfo':{'allocation':'RANDOMIZED',
            'interventionModel':'PARALLEL','maskingInfo':{'masking':'QUADRUPLE','private':'PRIVATE'},
            'primaryPurpose':'TREATMENT'},'enrollmentInfo':{'count':400,'type':'ESTIMATED'},'private':'PRIVATE'},
        'statusModule':{'overallStatus':'ACTIVE_NOT_RECRUITING'}}}).encode()
    p.write_bytes(raw)
    h=hashlib.sha256(raw).hexdigest()
    manifest={'schema_version':'arena-public-acquisition-v1','assets':[{'path':name,'sha256':h,'bytes':len(raw)}]}
    mp=tmp_path/'arena_data/season_acquisition_manifest.json';mp.write_text(json.dumps(manifest))
    return p,mp,manifest,{'id':'registry','kind':'trial_registry','source_path':name,'source_sha256':h}


def test_phase_and_design_only_from_manifest_bound_exact_frozen_registry(tmp_path,monkeypatch):
    p,mp,m,e=registry(tmp_path,monkeypatch)
    result=api.registry_fields(target(phase='INVENTED'),[e])
    assert result['phase']=='PHASE3' and result['registry_status']=='ACTIVE_NOT_RECRUITING'
    assert result['design']['masking']=='QUADRUPLE' and result['design']['enrollment']['count']==400
    assert 'PRIVATE' not in json.dumps(result)


@pytest.mark.parametrize('corruption',['bytes','sha','duplicate','nct','kind','symlink','absent','path','tamper'])
def test_missing_or_unverified_registry_never_fabricates_metadata(tmp_path,monkeypatch,corruption):
    p,mp,m,e=registry(tmp_path,monkeypatch)
    t=target(phase='UNTRUSTED',design='UNTRUSTED',registry_status='UNTRUSTED')
    if corruption=='bytes':m['assets'][0]['bytes']+=1
    elif corruption=='sha':e['source_sha256']='b'*64
    elif corruption=='duplicate':m['assets'].append(deepcopy(m['assets'][0]))
    elif corruption=='nct':t['nct_id']='NCT00000002'
    elif corruption=='kind':e['kind']='clinical_prior'
    elif corruption=='symlink':
        raw=p.read_bytes();p.unlink();outside=tmp_path/'outside.json';outside.write_bytes(raw);p.symlink_to(outside)
    elif corruption=='absent':p.unlink()
    elif corruption=='path':e['source_path']='../outside.json'
    elif corruption=='tamper':p.write_bytes(b'{}')
    mp.write_text(json.dumps(m))
    result=api.target_view(t,[e])
    assert result['phase'] is None and result['design'] is None and result['registry_status'] is None


def test_download_equals_sanitized_detail_and_no_public_mutations(fixture):
    client,_,app=fixture
    detail=client.get('/api/arena/public/targets/t1')
    record=client.get('/api/arena/public/targets/t1/record')
    assert record.json()==detail.json() and 'attachment;' in record.headers['content-disposition']
    assert record.headers['cache-control']=='no-store'
    assert client.get('/api/arena/public/targets/absent').status_code==404
    assert client.get('/api/arena/public/targets/absent/record').status_code==404
    for route in api.router.routes:
        if route.path.startswith('/api/arena/public'):assert route.methods=={'GET'}
    assert client.post('/api/arena/public',json={}).status_code==405


def test_errors_never_return_private_exception_details(fixture,monkeypatch):
    client,_,_=fixture
    def broken(store):raise ValueError('PRIVATE database path / provider key')
    monkeypatch.setattr(api,'read_frozen',broken)
    response=client.get('/api/arena/public')
    assert response.status_code==503 and 'PRIVATE' not in response.text


def test_urls_with_credentials_or_secret_query_are_not_exposed():
    for url in ('https://user:password@example.org','https://example.org?api_key=PRIVATE','http://example.org','javascript:alert(1)'):
        assert api.public_url(url) is None
    assert api.public_url('https://example.org/study?id=12')=='https://example.org/study?id=12'


def test_brief_reason_withholds_explicit_uncalibrated_percentage():
    reason=api.brief_reason(row(reasoning='成功概率为70%；PFS HR=0.63, but evidence is limited.'))
    assert '70%' not in reason and 'HR=0.63' in reason


def test_frozen_ledger_reader_never_constructs_or_writes_ledger(tmp_path,monkeypatch):
    ledger=ArenaLedger(tmp_path/'runtime');t,slots=spec();ledger.register('fixture',[t],slots,{})
    expected=ledger.snapshot()
    before=ledger.path.read_bytes()
    def forbidden(*args,**kwargs):raise AssertionError('Public GET attempted schema mutation')
    monkeypatch.setattr(ArenaLedger,'__init__',forbidden)
    assert api.read_frozen(SimpleNamespace(path=ledger.path.parent))==expected
    assert ledger.path.read_bytes()==before
    assert api.read_frozen(SimpleNamespace(path=tmp_path/'absent'))=={'targets':[],'runs':[],'cohorts':[]}
    assert not (tmp_path/'absent').exists()


def test_actual_sqlite_archive_projection_is_query_only_and_withholds_unaccepted_result(tmp_path,monkeypatch,verified_historical_product):
    """Real frozen archive inputs with synthetic adjudication in an isolated DB."""
    monkeypatch.setattr(platform_store,'now',lambda:'2026-10-21T00:00:00+00:00')
    store=Store(tmp_path/'isolated');platform_store.import_legacy_archive(store)
    ledger=platform_store.PlatformStore(store)
    snapshot=next(s['record'] for s in ledger.list_public_snapshots() if s['record']['archive_provenance']['case_id']=='forward-002')
    submitted=ledger.submit_outcome('isolated-submitter',snapshot['snapshot_id'],{
        'trial_id':snapshot['trial_id'],**{k:snapshot['target'][k] for k in ('endpoint','population','comparison')},
        'label':0,'readout_date':'2026-10-20','source':{'url':'https://synthetic-test.example/result','published_at':'2026-10-20','sha256':'a'*64},
        'effect_size':{'measure':'HR','value':1.02},'confidence_interval':[.8,1.3],'p_value':.8,
        'extraction_status':'complete','conflicts':[]},allow_other_owner=True)
    ledger.verify_outcome('isolated-reviewer',submitted['id'],'approved','Synthetic isolated test only',dict.fromkeys((
        'endpoint_match','population_match','comparison_match','source_checked','prespecified_analysis','earliest_result_checked','evidence_cutoff_checked'),True))
    dbpath=store.path/'workbench.sqlite3'
    before=dbpath.read_bytes()
    actual_connect=sqlite3.connect;observed=[]
    def readonly_connect(path,*args,**kwargs):
        assert str(path).endswith('?mode=ro') and kwargs.get('uri') is True
        connection=actual_connect(path,*args,**kwargs)
        connection.set_trace_callback(observed.append)
        return connection
    def forbidden(*args,**kwargs):raise AssertionError('Public archive read attempted initialization or writable connection')
    monkeypatch.setattr(api.sqlite3,'connect',readonly_connect)
    monkeypatch.setattr(platform_store.PlatformStore,'__init__',forbidden)
    monkeypatch.setattr(store,'connect',forbidden)
    app=FastAPI();app.state.store=store;app.include_router(api.router)
    response=TestClient(app).get('/api/arena/public')
    assert response.status_code==200,response.text
    view=response.json();t=next(t for t in view['targets'] if t['id']=='archive-forward-002')
    assert t['result']['label'] is None and t['status']=='agreement_pending_independent_review'
    assert t['result']['review_status'] == 'agreement_pending_independent_review'
    assert t['result']['scoring_eligible'] is False and all(run['match'] is None for run in view['runs'])
    assert t['result']['p_value']==.8 and t['result']['confidence_interval']==[.8,1.3]
    assert 'isolated-submitter' not in response.text and 'isolated-reviewer' not in response.text
    assert all(r['probability'] is None and r['confidence'] is None for r in view['runs'])
    assert dbpath.read_bytes()==before
    assert observed and any(x=='PRAGMA query_only=ON' for x in observed)
    assert not any(x.lstrip().split()[0].upper() in ('INSERT','UPDATE','DELETE','CREATE','ALTER','REPLACE','DROP') for x in observed)


@pytest.mark.parametrize('claim',[
    '综合判断，成功概率略低于50%', '综合给予略低于50%的主观概率',
    '综合给予中等略偏正的概率 0.58', '主观概率设于约0.40',
    '目标统计优效成功概率定于偏低区间，约0.35',
    '成功机会略高于五五开', 'A 55% probability of success',
    'Success probability is slightly below 50%', 'We estimate a 0.4 chance of success',
    'Confidence: 75%', 'P(success) = 0.45',
])
def test_short_rationale_omits_model_numeric_self_assessment_preserving_effects(claim):
    reason=api.brief_reason(row(reasoning='临床PFS HR 0.63，95% confidence interval 0.5–0.8，p=0.02。'+claim+'。需要直接结果。'))
    assert claim not in reason and '未建立概率校准' in reason
    assert 'HR 0.63' in reason and 'p=0.02' in reason


def test_source_reference_never_falls_back_to_raw_packet_body():
    value=api.source_view({'id':'source-id','text':'PRIVATE full raw JSON with debug data'})
    assert value['title']=='source-id' and 'PRIVATE' not in json.dumps(value)


def test_broken_ledger_symlink_does_not_silently_remove_assigned_denominator(tmp_path):
    path=tmp_path/'arena.sqlite3';path.symlink_to(tmp_path/'missing')
    with pytest.raises(ValueError,match='Invalid ledger'):api.read_frozen(SimpleNamespace(path=tmp_path))


@pytest.fixture(scope='module')
def verified_historical_product():
    import os
    if os.environ.get('EMERIS_SOURCE_ONLY_CI') == 'true':
        pytest.skip('Frozen historical source integration runs in the evidence-enabled acceptance suite; source-only CI intentionally omits these files')
    from emeris_live.product import build_product
    return build_product(None)


def test_eight_real_frozen_historical_cases_and_sixteen_persisted_llm_outputs(verified_historical_product):
    targets,runs,evidence=api.historical_replays(verified_historical_product)
    assert len(targets)==8 and len(runs)==16
    assert {t['trial_name'] for t in targets}=={'SERENA-6','HERCULES','GEMINI-1','GEMINI-2',
        'evolutionRMS-1','evolutionRMS-2','INAVO120','KEYNOTE-789'}
    originals={case['case_id']:case for case in verified_historical_product['historical']}
    for t in targets:
        original=originals[t['id'].removeprefix('history-replay-')]
        assert t['scope']=='archived_research' and t['record_kind']=='historical_replay'
        assert t['endpoint']==original['endpoint']
        assert t['population']==original['result_details']['population']
        assert t['comparison']==original['result_details']['comparison']
        assert t['result']['label']==('success' if original['actual_label']==1 else 'failure')
        assert t['result']['first_public_at']==original['result_date']
        assert t['result']['source_url']==original['label_source']['url']
        assert 'no independent human adjudication' in t['result']['verification_note']
        own=[r for r in runs if r['target_id']==t['id']]
        assert {r['model_id'] for r in own}==set(original['forecasts'])
        for r in own:
            f=original['forecasts'][r['model_id']]
            assert r['probability']==f['probability'] and r['generated_at_utc']==f['created_at_utc']
            assert r['model_version']==f['model_version'] and r['locked_at_utc'] is None
            assert api.timestamp(r['generated_at_utc'])>api.timestamp(t['result']['first_public_at'],date_only=True)
            public=api.run_view(r,t,[evidence[i] for i in t['public_source_ids']])
            assert public['probability'] is None and public['confidence'] is None
            assert public['match'] is None and public['match_status']=='historical_replay'
            # Results and later numeric supplements are not invented as model citations.
            assert all(s['kind']=='historical_design' for s in public['sources'])
    assert all(api.source_view(s)['source_snapshot_url'] is None for s in evidence.values())


@pytest.mark.parametrize('fault',['unknown','bool','float','result_conflict','detail_label','detail_nct','detail_endpoint','detail_date','missing_source','bad_hash','missing_forecasts'])
def test_unknown_or_unverified_historical_records_are_omitted_not_default_failures(verified_historical_product,fault):
    case=deepcopy(verified_historical_product['historical'][0])
    if fault=='unknown':case['actual_label']=None
    elif fault=='bool':case['actual_label']=True
    elif fault=='float':case['actual_label']=1.0
    elif fault=='result_conflict':case['actual_result']='unknown'
    elif fault=='detail_label':case['result_details']['original_label']=0
    elif fault=='detail_nct':case['result_details']['nct_id']='NCT00000000'
    elif fault=='detail_endpoint':case['result_details']['endpoint']='Other endpoint'
    elif fault=='detail_date':case['result_details']['original_topline_date']='2025-02-25'
    elif fault in ('missing_source','bad_hash'):
        case['label_source']['path' if fault=='missing_source' else 'sha256']='missing.json' if fault=='missing_source' else '0'*64
        case['result_details']['original_label_source']=deepcopy(case['label_source'])
    elif fault=='missing_forecasts':case['forecasts']={}
    targets,runs,_=api.historical_replays({'historical':[case]})
    assert targets==[] and runs==[]


def test_missing_numeric_supplement_does_not_invent_effect_values(verified_historical_product):
    case=deepcopy(verified_historical_product['historical'][0])
    case['result_details']['source']['sha256']='0'*64
    targets,runs,evidence=api.historical_replays({'historical':[case]})
    assert len(targets)==1 and len(runs)==2
    result=targets[0]['result']
    assert result['label']=='success' and result['source_url']==case['label_source']['url']
    assert result['numeric_source_url'] is None and result['effect_size'] is None
    assert result['confidence_interval'] is None and result['p_value'] is None
    assert all(e['kind']!='historical_numeric_result' for e in evidence.values())


def test_reported_public_projection_keeps_formal_cohort_count_and_no_accuracy_claim(fixture,monkeypatch,verified_historical_product):
    client,frozen,_=fixture
    # This integration checks real frozen sources; the general fixture otherwise
    # uses an empty source root to exercise unavailable-registry behavior.
    from emeris_live import product
    monkeypatch.setattr(api,'ROOT',product.ROOT)
    history=api.historical_replays(verified_historical_product)
    monkeypatch.setattr(api,'read_archived',lambda store:deepcopy(history))
    view=client.get('/api/arena/public').json()
    assert view['current_cohort_id']=='c1'
    assert sum(t['cohort_id']=='c1' for t in view['targets'])==1
    reported=[t for t in view['targets'] if t['id'].startswith('history-replay-')]
    assert len(reported)==8 and all(t['status']=='adjudicated' for t in reported)
    keys=set(all_keys(view))
    assert not keys & {'accuracy','brier','replay_metrics','statistical_models','human_review_count','prospective_eligible'}
    selected=reported[0]['id']
    detail=client.get('/api/arena/public/targets/'+selected).json()
    assert len(detail['runs'])==2 and detail['consensus']['assigned']==2
    assert all(r['match'] is None and r['match_status']=='historical_replay' for r in detail['runs'])
    assert client.get('/api/arena/public/targets/'+selected+'/record').json()==detail
