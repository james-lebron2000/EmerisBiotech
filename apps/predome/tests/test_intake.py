"""Public ingestion keeps result discovery separate from efficacy adjudication."""
import json
import pytest
from emeris_live.store import Store
from emeris_live.intake import run_intake,list_candidates,jobs,registry_summary

def test_registry_results_preserve_statistics_without_success_label(tmp_path):
    store=Store(tmp_path/'store')
    study={'hasResults':True,'protocolSection':{'identificationModule':{'nctId':'NCT12345678'},'statusModule':{'primaryCompletionDateStruct':{'date':'2026-01-01'},'resultsFirstPostDateStruct':{'date':'2026-06-01'}}},'resultsSection':{'outcomeMeasuresModule':{'outcomeMeasures':[{'type':'PRIMARY','title':'Test endpoint','analyses':[{'pValue':'0.03','paramType':'Hazard Ratio','paramValue':'0.7'}]}]}}}
    def fetcher(url):
        if 'clinicaltrials' in url:return json.dumps(study).encode()
        return b'{"resultList":{"result":[]}}'
    report=run_intake(store,['NCT12345678'],fetcher,discover=False)
    assert report['auto_approved']==0 and report['sources_checked']==2
    r=list_candidates(store)[0]
    assert r['structured']['label'] is None and r['structured']['readout_date'] is None
    assert r['structured']['reported_outcome_measures'][0]['analyses'][0]['pValue']=='0.03'
    assert r['kind']=='reported_results_candidate'
    extracted=report['result_suggestions']['records']
    assert len(extracted)==1 and extracted[0]['candidate_id']==r['id']
    assert extracted[0]['primary_measure_count']==1 and extracted[0]['matched_prediction_count']==0
    assert len(extracted[0]['digest'])==64 and report['result_suggestions']['writes_actual_outcomes'] is False
    second=run_intake(store,['NCT12345678'],fetcher,discover=False)
    assert second['new_candidates']==0 and len(list_candidates(store))==1
    assert second['result_suggestions']['records'][0]['digest']==extracted[0]['digest']
    assert len(jobs(store))==2

def test_failed_source_not_negative_and_identity_mismatch_is_failure(tmp_path):
    store=Store(tmp_path/'store')
    def fetcher(url):
        if 'clinicaltrials' in url:return b'{"protocolSection":{"identificationModule":{"nctId":"NCT99999999"}}}'
        raise TimeoutError('Do not log any secret-bearing exception text')
    result=run_intake(store,['NCT12345678'],fetcher,discover=False)
    assert result['status']=='failed' and len(result['failures'])==2
    assert list_candidates(store)==[]
    assert 'secret-bearing' not in json.dumps(result)

def test_pubmed_search_candidate_retains_specific_publication(tmp_path):
    store=Store(tmp_path/'store')
    def fetcher(url):
        if 'clinicaltrials' in url:return b'{"protocolSection":{"identificationModule":{"nctId":"NCT12345678"}}}'
        return json.dumps({'resultList':{'result':[{'id':'123456','pmid':'123456','title':'NCT12345678 test abstract','firstPublicationDate':'2026-01-01'}]}}).encode()
    run_intake(store,['NCT12345678'],fetcher,discover=False)
    r=next(r for r in list_candidates(store) if r['kind']=='pubmed_literature_candidate')
    assert r['structured']['articles'][0]['publication_url']=='https://pubmed.ncbi.nlm.nih.gov/123456/'
    assert r['structured']['label'] is None and r['review_status']=='needs_review'

def test_invalid_id_never_triggers_network(tmp_path):
    with pytest.raises(ValueError):run_intake(Store(tmp_path/'store'),['https://localhost'],lambda _:pytest.fail('must not fetch'))
