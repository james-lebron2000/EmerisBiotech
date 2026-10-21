"""Synthetic registry fixtures in temporary stores; no clinical adjudication."""
from copy import deepcopy
import hashlib
import json
import pytest
from fastapi import FastAPI,HTTPException
from fastapi.testclient import TestClient
from emeris_live import intake,platform_store
from emeris_live.store import Store
from emeris_live.platform_store import PlatformStore,digest
from emeris_live.result_suggestions import suggest_candidate,build_suggestions,PIPELINE_VERSION
from emeris_live.intake_api import router

NCT='NCT01234567'
URL='https://clinicaltrials.gov/api/v2/studies/'+NCT
TARGET={'endpoint':'Predicted primary target','population':'PREDICTED POPULATION MUST NOT BECOME ACTUAL',
        'comparison':'PREDICTED COMPARATOR MUST NOT BECOME ACTUAL','success_definition':'Prespecified multiplicity-adjusted primary analysis'}


def registry():
    analysis={'groupIds':['OG000','OG001'],'statisticalMethod':'Cochran-Mantel-Haenszel',
              'paramType':'Risk Difference','paramValue':'16.3','ciPctValue':'95','ciNumSides':'TWO_SIDED',
              'ciLowerLimit':'6.3','ciUpperLimit':'26.3','pValue':'<0.001','pValueComment':'Nominal p-value.',
              'nonInferiorityType':'SUPERIORITY'}
    first={'type':'PRIMARY','title':'SYNTHETIC endpoint A at week 52','populationDescription':'Reported full analysis set',
           'timeFrame':'Baseline; Week 52','paramType':'COUNT_OF_PARTICIPANTS','unitOfMeasure':'Participants',
           'groups':[{'id':'OG000','title':'Drug A','description':'First measure intervention'},
                     {'id':'OG001','title':'Placebo A','description':'First measure comparator'}],
           'analyses':[analysis,analysis|{'pValue':'0.2','paramType':'Odds Ratio','paramValue':'1.1','statisticalMethod':'Logistic Regression'}]}
    second={'type':'PRIMARY','title':'SYNTHETIC endpoint B','timeFrame':'Week 24','paramType':'MEAN','unitOfMeasure':'Score',
            'groups':[{'id':'OG000','title':'Different local arm B'}],
            'analyses':[{'groupIds':['OG000','OG001'],'paramType':'Mean Difference','paramValue':'-2.1','pValue':'0.9'}]}
    return {'hasResults':True,'protocolSection':{'identificationModule':{'nctId':NCT},
            'statusModule':{'resultsFirstPostDateStruct':{'date':'2026-10-20','type':'ACTUAL'}},
            'outcomesModule':{'primaryOutcomes':[{'measure':'Registered endpoint A','timeFrame':'Week 52'}, {'measure':'Registered endpoint B','timeFrame':'Week 24'}]}},
            'resultsSection':{'outcomeMeasuresModule':{'outcomeMeasures':[first,{'type':'SECONDARY','title':'Must not be selected'},second]}}}


def make_run(ledger,owner='owner',published=True,nct=NCT):
    record=ledger.create_run(owner,{'nct_id':nct,'protocol_text':'PRIVATE PROTOCOL'},
        {'forecast_kind':'uncalibrated_llm_research','forecasts':{'test-model':{'status':'predicted','probability':.7}}},
        model_version='test-v1',pipeline_version='test',data_cutoff='2026-10-18T00:00:00+00:00',target=TARGET,
        evidence=[{'url':URL,'availability_date':'2026-10-17'}])
    return ledger.lock(owner,record['id'],publish=published)


@pytest.fixture
def setup(tmp_path,monkeypatch):
    monkeypatch.setattr(platform_store,'now',lambda:'2026-10-19T12:00:00+00:00')
    monkeypatch.setattr(intake,'now',lambda:'2026-10-21T12:00:00+00:00')
    store=Store(tmp_path/'synthetic-only');intake.initialize(store);ledger=PlatformStore(store)
    public=make_run(ledger);private=make_run(ledger,owner='private-owner',published=False)
    make_run(ledger,owner='other-trial',nct='NCT99999999')
    raw=json.dumps(registry(),ensure_ascii=False).encode()
    cid,_=intake.save_candidate(store,NCT,'reported_results_candidate',URL,raw,
        {'reported_outcome_measures':[{'type':'PRIMARY','title':'FORGED MUTABLE SUMMARY','analyses':[{'pValue':'0'}]}]},hash_scope='source_response_bytes')
    return store,ledger,cid,public,private


def test_extracts_all_primary_analyses_with_raw_strings_local_groups_and_audit_pointers(setup):
    store,ledger,cid,public,private=setup
    result=suggest_candidate(store,cid)
    assert result['pipeline_version']==PIPELINE_VERSION
    assert result['primary_measure_count']==2 and result['pairing_count_total']==3
    assert len(result['suggestions'])==3 and len(result['matched_predictions'])==1
    assert result['matched_predictions'][0]['snapshot_id']==public['record']['snapshot_id']
    assert result['registered_primary_outcomes'][0]['measure']=='Registered endpoint A'
    assert result['source']['sha256']==hashlib.sha256(json.dumps(registry(),ensure_ascii=False).encode()).hexdigest()
    for original,suggestion in zip([registry()['resultsSection']['outcomeMeasuresModule']['outcomeMeasures'][0]['analyses'][0],
                                    registry()['resultsSection']['outcomeMeasuresModule']['outcomeMeasures'][0]['analyses'][1],
                                    registry()['resultsSection']['outcomeMeasuresModule']['outcomeMeasures'][2]['analyses'][0]],result['suggestions']):
        assert suggestion['reported']['analysis']==original
        assert suggestion['suggested_outcome']['label'] is None
        assert suggestion['suggested_outcome']['p_value'] is None
        assert suggestion['suggested_outcome']['comparison'] is None
        assert suggestion['suggested_outcome']['readout_date'] is None
        assert suggestion['pairing']['target_match']=='unverified'
        assert suggestion['pairing']['predicted_target']==TARGET
        assert 'prespecified_alpha_unknown' in suggestion['warnings']
        assert suggestion['auto_verified'] is False
        assert suggestion['digest']==digest({k:v for k,v in suggestion.items() if k not in ('digest','suggestion_id')})
    first,alternate,second=result['suggestions']
    assert first['reported']['paramType']=='COUNT_OF_PARTICIPANTS'
    assert first['reported']['analysis']['paramType']=='Risk Difference'
    assert first['suggested_outcome']['p_value_raw']=='<0.001'
    assert first['suggested_outcome']['population']=='Reported full analysis set'
    assert first['suggested_outcome']['confidence_interval']['ciLowerLimit']=='6.3'
    assert 'p_value_inequality_preserved_not_exact' in first['warnings']
    assert 'nominal_p_value_not_primary_success' in first['warnings']
    assert 'multiple_analyses_no_automatic_selection' in first['warnings']
    assert first['reported']['analysis_pointer']=='/resultsSection/outcomeMeasuresModule/outcomeMeasures/0/analyses/0'
    assert second['reported']['analysis_groups'][0]['title']=='Different local arm B'
    assert second['reported']['analysis_groups'][1]['title'] is None
    assert 'unresolved_or_ambiguous_measure_local_group_id' in second['warnings']
    assert second['suggested_outcome']['population'] is None
    assert 'reported_analysis_population_missing' in second['warnings']
    assert second['reported']['measure_pointer'].endswith('/2')
    assert 'FORGED MUTABLE SUMMARY' not in json.dumps(result)
    assert 'PRIVATE PROTOCOL' not in json.dumps(result)
    assert private['record']['snapshot_id'] not in json.dumps(result)
    assert result==suggest_candidate(store,cid)
    assert result['digest']==digest({k:v for k,v in result.items() if k!='digest'})


def test_service_never_reads_private_snapshot_and_does_not_write_outcomes(setup,monkeypatch):
    store,ledger,cid,public,private=setup
    with store.connect() as db:
        private_blob=db.execute('SELECT payload FROM platform_prediction_snapshots WHERE id=?',(private['record']['id'],)).fetchone()[0]
        before={table:[tuple(row) for row in db.execute('SELECT * FROM '+table)] for table in
                ('platform_prediction_snapshots','platform_prediction_runs','platform_actual_outcomes','platform_outcome_verifications','intake_links')}
    unpack=store.unpack
    def guarded(value):
        assert value!=private_blob,'Private snapshot was decrypted'
        return unpack(value)
    monkeypatch.setattr(store,'unpack',guarded)
    suggest_candidate(store,cid)
    with store.connect() as db:
        after={table:[tuple(row) for row in db.execute('SELECT * FROM '+table)] for table in before}
    assert before==after


def pure_inputs(setup,change=None):
    store,ledger,cid,public,private=setup
    raw=registry()
    if change:change(raw)
    payload=json.dumps(raw).encode()
    candidate=intake.get_candidate(store,cid)
    candidate['source']['sha256']=hashlib.sha256(payload).hexdigest()
    return candidate,payload,ledger.list_public_snapshots()


def test_absent_analysis_and_absent_public_match_remain_reviewable_unknowns(setup):
    def change(raw):raw['resultsSection']['outcomeMeasuresModule']['outcomeMeasures'][0]['analyses']=[]
    args=pure_inputs(setup,change)
    result=build_suggestions(*args)
    assert result['pairing_count']==2
    first=result['suggestions'][0]
    assert first['reported']['analysis'] is None
    assert first['reported']['analysis_pointer'] is None
    assert first['suggested_outcome']['label'] is None
    assert 'no_reported_statistical_analysis' in first['warnings']
    unmatched=build_suggestions(args[0],args[1],[])
    assert unmatched['suggestions']==[] and unmatched['primary_measure_count']==2
    assert 'no_public_locked_prediction_match' in unmatched['warnings']


def test_limit_is_explicit_and_all_primary_raw_results_are_retained(setup):
    args=pure_inputs(setup)
    result=build_suggestions(*args,max_suggestions=2)
    assert result['truncated'] is True
    assert result['pairing_count_total']==3 and result['pairing_count']==2
    assert result['suggestion_limit']==2
    assert len(result['primary_measures'])==2
    assert result['primary_measures'][1]['raw']==registry()['resultsSection']['outcomeMeasuresModule']['outcomeMeasures'][2]
    assert 'suggestion_limit_reached_review_all_raw_primary_results' in result['warnings']
    many=[]
    for i in range(80):
        envelope=deepcopy(args[2][0]);envelope['record']['trial_id']=NCT
        envelope['record']['snapshot_id']='ps_'+str(i);envelope['record']['prediction_id']='pr_'+str(i)
        many.append(envelope)
    capped=build_suggestions(args[0],args[1],many)
    assert capped['pairing_count']==200 and capped['pairing_count_total']==240 and capped['truncated']
    with pytest.raises(ValueError):build_suggestions(*args,max_suggestions=201)


@pytest.mark.parametrize('mutate',[
    lambda r:r.update(protocolSection=[]),
    lambda r:r['protocolSection'].update(identificationModule=[]),
    lambda r:r['protocolSection']['identificationModule'].update(nctId='NCT99999999'),
    lambda r:r.update(resultsSection=[]),
    lambda r:r['resultsSection'].update(outcomeMeasuresModule=[]),
    lambda r:r['resultsSection']['outcomeMeasuresModule'].update(outcomeMeasures='malformed'),
    lambda r:r['resultsSection']['outcomeMeasuresModule']['outcomeMeasures'][0].update(analyses={}),
    lambda r:r['resultsSection']['outcomeMeasuresModule']['outcomeMeasures'][0]['analyses'][0].update(groupIds=[{}]),
])
def test_malformed_raw_schema_fails_closed_as_value_error(setup,mutate):
    with pytest.raises(ValueError):build_suggestions(*pure_inputs(setup,mutate))


def test_untrusted_url_kind_and_hash_are_not_accepted(setup):
    candidate,raw,snapshots=pure_inputs(setup)
    for url in ('http://clinicaltrials.gov/api/v2/studies/'+NCT,'https://attacker.example/api/v2/studies/'+NCT,
                'https://clinicaltrials.gov/api/v2/studies/NCT99999999'):
        bad=deepcopy(candidate);bad['source']['url']=url
        with pytest.raises(ValueError):build_suggestions(bad,raw,snapshots)
    with pytest.raises(ValueError):build_suggestions(candidate,raw+b' ',snapshots)
    with pytest.raises(ValueError):build_suggestions(candidate|{'kind':'registry_update'},raw,snapshots)
    forged=deepcopy(snapshots[0]);forged['record'].pop('public_view')
    assert not build_suggestions(candidate,raw,[forged])['matched_predictions']


def test_reviewer_api_auth_invalid_capture_and_no_actual_write(setup,monkeypatch):
    store,ledger,cid,public,private=setup
    monkeypatch.setenv('EMERIS_REVIEWERS','reviewer')
    app=FastAPI();app.state.store=store
    def actor(request):
        user=request.headers.get('x-user')
        if not user:raise HTTPException(401)
        return {'owner':user,'name':user}
    app.state.platform_user=actor;app.include_router(router);client=TestClient(app)
    path='/api/intake/candidates/'+cid+'/suggestions';headers={'x-user':'reviewer'}
    assert client.get(path).status_code==401
    assert client.get(path,headers={'x-user':'ordinary'}).status_code==403
    response=client.get(path,headers=headers)
    assert response.status_code==200
    assert all(row['suggested_outcome']['label'] is None for row in response.json()['suggestions'])
    assert client.get('/api/intake/candidates/missing/suggestions',headers=headers).status_code==404
    bad,_=intake.save_candidate(store,NCT,'reported_results_candidate',URL,b'not-json',{},hash_scope='source_response_bytes')
    assert client.get('/api/intake/candidates/'+bad+'/suggestions',headers=headers).status_code==422
    with store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM platform_actual_outcomes').fetchone()[0]==0
        assert db.execute('SELECT COUNT(*) FROM platform_outcome_verifications').fetchone()[0]==0


def test_byte_budget_stops_before_cross_product_and_preserves_oversized_source_record(setup):
    def long_analysis(raw):
        raw['resultsSection']['outcomeMeasuresModule']['outcomeMeasures'][0]['analyses'][0]['pValueComment']='SYNTHETIC '+('x'*12000)
    candidate,raw,snapshots=pure_inputs(setup,long_analysis)
    result=build_suggestions(candidate,raw,snapshots,max_suggestion_bytes=10000)
    assert result['suggestions']==[]
    assert result['pairing_count_total']==3 and result['truncated'] is True
    assert result['suggestion_bytes']==0 and result['suggestion_byte_limit']==10000
    assert 'suggestion_byte_limit_reached_review_raw_primary_results' in result['warnings']
    assert len(result['primary_measures'][0]['raw']['analyses'][0]['pValueComment'])>12000
    normal=pure_inputs(setup)
    initial=build_suggestions(*normal)
    first_size=len(platform_store.canonical(initial['suggestions'][0]).encode())
    limited=build_suggestions(*normal,max_suggestion_bytes=first_size)
    assert limited['pairing_count']==1 and limited['pairing_count_total']==3 and limited['truncated']
    assert limited['suggestion_bytes']==first_size
    assert limited['suggestion_bytes']==sum(len(platform_store.canonical(row).encode()) for row in limited['suggestions'])
    with pytest.raises(ValueError):build_suggestions(*normal,max_suggestion_bytes=2*1024*1024+1)
