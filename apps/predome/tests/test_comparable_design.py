import hashlib,json
from emeris_live import prediction_service as svc

def test_current_registry_design_and_enrollment_are_not_effect_evidence(monkeypatch):
    studies=[{'protocolSection':{'identificationModule':{'nctId':'NCT01234567','briefTitle':'Current trial'}}},{'protocolSection':{'identificationModule':{'nctId':'NCT12345678','briefTitle':'Comparable record'},'conditionsModule':{'conditions':['NSCLC']},'sponsorCollaboratorsModule':{'leadSponsor':{'name':'Sponsor'}},'designModule':{'phases':['PHASE3'],'enrollmentInfo':{'count':320,'type':'ACTUAL'},'designInfo':{'allocation':'RANDOMIZED','interventionModel':'PARALLEL','primaryPurpose':'TREATMENT','maskingInfo':{'masking':'DOUBLE'}}},'outcomesModule':{'primaryOutcomes':[{'measure':'PFS','timeFrame':'24 months'}]},'statusModule':{'lastUpdatePostDateStruct':{'date':'2026-01-01'}}},'resultsSection':{'outcomeMeasuresModule':{'must_not_enter':'actual result probability'}}},{'protocolSection':{'identificationModule':{'nctId':'NCT87654321'}}}]
    raw=json.dumps({'studies':studies,'totalCount':3}).encode();seen={}
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def raise_for_status(self):pass
        def iter_bytes(self):yield raw[:35];yield raw[35:]
    class Client:
        def __init__(self,**kwargs):assert kwargs['follow_redirects'] is False
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def stream(self,method,url,params,headers):seen.update(method=method,url=url,params=params,headers=headers);return Response()
    monkeypatch.setattr(svc.httpx,'Client',Client)
    result=svc.retrieve_comparables('NSCLC','NCT01234567')
    assert seen['url']=='https://clinicaltrials.gov/api/v2/studies' and seen['params']['query.cond']=='NSCLC'
    assert result['response_sha256']==hashlib.sha256(raw).hexdigest()
    assert [r['nct_id'] for r in result['records']]==['NCT12345678','NCT87654321']
    found,missing=result['records'];assert found['enrollment']=={'count':320,'type':'ACTUAL'}
    assert found['study_design']==studies[1]['protocolSection']['designModule']['designInfo']
    assert found['primary_endpoints'][0]['timeFrame']=='24 months'
    assert missing['enrollment']=={} and missing['study_design']=={}
    assert missing['sponsor'] is None and missing['primary_endpoints']==[]
    assert all(r['use']=='context_only_not_model_input_or_effect_transfer' for r in result['records'])
    assert 'actual result probability' not in json.dumps(result) and 'resultsSection' not in json.dumps(result)
    assert result['current_registry_only'] is True
