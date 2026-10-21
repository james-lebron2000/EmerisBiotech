"""Fixed two-model descriptive ensemble and real report construction with HTTP stubs."""
from copy import deepcopy
from datetime import datetime
import hashlib
import json
import pytest
from emeris_live import prediction_ensemble as ensemble, prediction_service as svc

NOW='2026-10-21T00:00:00+00:00'


def request(method='llm',models=None):
    fields={k:{'value':value,'origin':'user','confirmed':True} for k,value in {
        'indication':'research condition','drug':'research drug','population':'ITT population',
        'primary_endpoint':'PFS','comparator':'active comparator'}.items()}
    payload={'profile':{'fields':fields},'target':{'endpoint':'PFS','population':'ITT population',
        'comparison':'drug versus active comparator','success_definition':'Prespecified PFS primary test favors treatment at adjusted alpha 0.05'},
        'data_cutoff':'2026-01-01','method':method,'models':list(ensemble.MEMBERS) if models is None and method=='llm' else models or [],
        'allow_external_processing':method=='llm','confirm_fields_reviewed':True,
        'evidence':[{'id':'prior-trial','text':'Public prior study with randomized efficacy evidence and limitations.','availability_date':'2025-12-01'}]}
    if method=='scenario':
        payload['scenario']={'endpoint':'PFS','prior_hr_median':.8,'prior_log_hr_sd':.2,'event_count':300,
            'allocation':.5,'alpha':.05,'transport_sd':.1,'clinical_hr_threshold':.8,'acknowledged_conditional_only':True}
    return svc.PredictionRequest(**payload)


def bound():
    req=request();packet=svc.canonical_packet(req)
    prompt=hashlib.sha256(svc.SYSTEM_PROMPT.encode()).hexdigest()
    requests={m:svc.digest(svc.model_request(m,packet)) for m in ensemble.MEMBERS}
    forecasts={m:{'model_version':m,'status':'predicted','probability':p,
        'target_definition':req.target.success_definition,'input_sha256':svc.digest(packet),
        'prompt_sha256':prompt,'request_sha256':requests[m],'reasoning':'Bound research fixture'}
        for m,p in zip(ensemble.MEMBERS,(.2,.8))}
    return dict(method='llm',forecasts=forecasts,packet=packet,prompt_sha256=prompt,
                request_sha256_by_model=requests,generated_at_utc=NOW)


def test_fixed_mean_provenance_determinism_and_no_mutation():
    data=bound();before=deepcopy(data)
    first=ensemble.build_ensemble(**data);second=ensemble.build_ensemble(**data)
    assert data==before and first==second
    assert first['status']=='available' and first['probability']==.5
    assert first['model_range']=={'min':.2,'max':.8,'spread':.8-.2}
    assert first['policy_sha256']==svc.digest(ensemble.POLICY)
    assert first['input_sha256']==svc.digest(data['packet'])
    assert first['target_sha256']==svc.digest(data['packet']['target'])
    assert [m['model'] for m in first['members']]==list(ensemble.MEMBERS)
    for m in first['members']:
        assert m['weight']==.5 and m['component_sha256']==svc.digest(data['forecasts'][m['model']])
    assert first['generated_at_utc']==NOW and first['data_cutoff']=='2026-01-01'
    assert first['clinical_prediction_validated'] is False
    assert first['uncertainty_kind']=='model_range_not_confidence_interval'
    assert first['evaluation_scope']=='descriptive_only_excluded_from_model_performance'


@pytest.mark.parametrize('p',[True,False,None,-.1,1.1,float('nan'),float('inf'),'0.7'])
def test_invalid_member_probability_cannot_be_averaged(p):
    data=bound();data['forecasts']['deepseek-flash']['probability']=p
    output=ensemble.build_ensemble(**data)
    assert output['status']=='unavailable' and output['probability'] is None and output['model_range'] is None
    assert 'deepseek-flash:invalid_probability' in output['reason_codes']
    json.dumps(output,allow_nan=False)


@pytest.mark.parametrize('status',['abstain','failed','unavailable'])
def test_no_partial_average_when_other_member_not_predicted(status):
    data=bound();data['forecasts']['deepseek-v4-pro'].update(status=status,probability=None)
    output=ensemble.build_ensemble(**data)
    assert output['probability'] is None and output['status']=='unavailable'
    assert 'deepseek-v4-pro:member_not_predicted' in output['reason_codes']


def test_missing_fixed_member_never_uses_other_models():
    data=bound();data['forecasts']['other-model']=data['forecasts'].pop('deepseek-v4-pro')
    output=ensemble.build_ensemble(**data)
    assert output['probability'] is None and 'deepseek-v4-pro:missing_member' in output['reason_codes']


@pytest.mark.parametrize('field,value,reason',[
    ('input_sha256','c'*64,'input_binding_mismatch'),('prompt_sha256','c'*64,'prompt_binding_mismatch'),
    ('request_sha256','c'*64,'request_binding_mismatch'),('request_sha256','invalid','request_binding_mismatch'),
    ('target_definition','Other successful endpoint','target_mismatch'),('model_version','','missing_model_version')])
def test_binding_mismatch_rejects_ensemble(field,value,reason):
    data=bound();data['forecasts']['deepseek-flash'][field]=value
    output=ensemble.build_ensemble(**data)
    assert output['status']=='unavailable' and output['probability'] is None
    assert 'deepseek-flash:'+reason in output['reason_codes']


def test_matching_member_hashes_must_also_match_current_packet_and_prompt():
    data=bound()
    for c in data['forecasts'].values():c['input_sha256']='c'*64
    assert ensemble.build_ensemble(**data)['status']=='unavailable'
    data=bound();data['packet']['target']['population']='Different population, same endpoint'
    assert ensemble.build_ensemble(**data)['status']=='unavailable'
    data=bound();data['prompt_sha256']='d'*64
    assert ensemble.build_ensemble(**data)['status']=='unavailable'


@pytest.mark.parametrize('method',['scenario','evidence_review'])
def test_non_llm_never_combines_model_outputs_or_assurance(method):
    data=bound();data['method']=method
    output=ensemble.build_ensemble(**data)
    assert output['status']=='not_applicable' and output['probability'] is None and output['members']==[]


@pytest.mark.parametrize('stamp',['2026-01-01T00:00:00','2026-01-01T08:00:00+08:00',None,'bad-date'])
def test_generation_timestamp_is_explicit_utc(stamp):
    data=bound();data['generated_at_utc']=stamp
    output=ensemble.build_ensemble(**data)
    assert output['status']=='unavailable' and 'invalid_generation_timestamp' in output['reason_codes']


def install_provider(monkeypatch,abstain=False):
    calls=[]
    monkeypatch.setenv('EMERIS_ENABLE_USER_LLM','true');monkeypatch.setenv('DEEPSEEK_API_KEY','test-only-never-sent')
    monkeypatch.setattr(svc,'utcnow',lambda:NOW)
    class Client:
        def __init__(self,**kwargs):assert kwargs['follow_redirects'] is False
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def post(self,url,**kwargs):
            body=kwargs['json'];calls.append(deepcopy(body));model=body['model']
            packet=json.loads(body['messages'][1]['content'])
            no=abstain and model=='deepseek-v4-pro'
            value={'status':'abstain' if no else 'predicted','probability':None if no else .2 if model=='deepseek-flash' else .8,
                'target_definition':packet['target']['success_definition'],'reasoning':'Explicit source-bounded research judgment',
                'evidence_ids':['prior-trial'],'key_drivers':[],'risk_factors':[],'assumptions':[],'key_unknowns':[]}
            raw={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(value)}}],'model':model,'id':'fixture-'+model}
            return svc.httpx.Response(200,json=raw,request=svc.httpx.Request('POST',url))
    monkeypatch.setattr(svc.httpx,'Client',Client)
    return calls


@pytest.mark.parametrize('abstain',[False,True])
def test_actual_create_report_with_mocked_http_success_or_abstention(monkeypatch,abstain):
    calls=install_provider(monkeypatch,abstain)
    req=request();report=svc.create_report(req);output=report['ensemble']
    assert len(calls)==2 and set(report['forecasts'])==set(ensemble.MEMBERS)
    assert report['pipeline_version']=='predome-user-trial-1.1.5'
    assert output['generated_at_utc']==report['completed_at_utc']==NOW
    assert datetime.fromisoformat(report['created_at_utc'])<=datetime.fromisoformat(output['generated_at_utc'])
    assert report['ensemble_probability']==output['probability'] and report['ensemble_reason']==output['reason']
    for body in calls:
        f=report['forecasts'][body['model']]
        assert f['request_sha256']==svc.digest(body)
        assert f['input_sha256']==svc.digest(svc.canonical_packet(req))
        assert body['messages'][0]['content']==svc.SYSTEM_PROMPT
    assert output['status']==('unavailable' if abstain else 'available')
    assert output['probability']==(None if abstain else .5)
    assert 'ensemble' not in report['forecasts']


def test_single_selected_model_does_not_become_ensemble(monkeypatch):
    calls=install_provider(monkeypatch)
    report=svc.create_report(request(models=['deepseek-flash']))
    assert len(calls)==1 and report['ensemble_probability'] is None
    assert 'deepseek-v4-pro:missing_member' in report['ensemble']['reason_codes']


@pytest.mark.parametrize('method',['scenario','evidence_review'])
def test_actual_non_llm_report_marks_ensemble_not_applicable(method):
    report=svc.create_report(request(method))
    assert report['ensemble']['status']=='not_applicable' and report['ensemble_probability'] is None
    assert report['forecasts']=={}


def test_capabilities_publish_policy_without_enabling_unconfigured_provider(monkeypatch):
    monkeypatch.delenv('EMERIS_ENABLE_USER_LLM',raising=False)
    monkeypatch.delenv('DEEPSEEK_API_KEY',raising=False)
    capabilities=svc.capabilities();policy=capabilities['ensemble_policy']
    assert capabilities['llm_available'] is False
    assert policy['policy_sha256']==ensemble.POLICY_SHA256
    assert policy['members']==list(ensemble.MEMBERS) and policy['requires_all_members'] is True
    report=svc.create_report(request())
    assert report['ensemble']['status']=='unavailable' and report['ensemble_probability'] is None


def test_actual_provider_transport_failure_does_not_average_survivor(monkeypatch):
    calls=install_provider(monkeypatch)
    original=svc.httpx.Client.post
    def post(self,url,**kwargs):
        if kwargs['json']['model']=='deepseek-v4-pro':
            raise svc.httpx.ReadTimeout('fixture transport failure')
        return original(self,url,**kwargs)
    monkeypatch.setattr(svc.httpx.Client,'post',post)
    report=svc.create_report(request())
    assert len(calls)==1
    assert report['forecasts']['deepseek-v4-pro']['status']=='failed'
    assert report['forecasts']['deepseek-flash']['probability']==.2
    assert report['ensemble_probability'] is None and report['ensemble']['status']=='unavailable'


def test_probability_boundary_zero_and_one_are_valid():
    data=bound()
    for c,p in zip(data['forecasts'].values(),(0,1)):c['probability']=p
    result=ensemble.build_ensemble(**data)
    assert result['status']=='available' and result['probability']==.5
    assert result['model_range']=={'min':0,'max':1,'spread':1}


def test_third_model_is_explicitly_rejected_not_silently_ignored():
    data=bound();data['forecasts']['future-third-model']=deepcopy(data['forecasts']['deepseek-flash'])
    result=ensemble.build_ensemble(**data)
    assert result['status']=='unavailable' and result['probability'] is None
    assert 'unexpected_ensemble_member' in result['reason_codes']


def test_provider_returned_model_is_separate_from_requested_version(monkeypatch):
    install_provider(monkeypatch)
    report=svc.create_report(request())
    for m in report['ensemble']['members']:
        assert m['model_version']==m['model']
        assert m['returned_model']==report['forecasts'][m['model']]['returned_model']


def test_target_whitespace_normalized_before_provider_and_report_binding(monkeypatch):
    calls=install_provider(monkeypatch)
    original=request();payload=original.model_dump(mode='json')
    payload['target']={key:' \n\t'+value+'\t\n ' for key,value in payload['target'].items()}
    normalized=svc.PredictionRequest(**payload)
    assert normalized.target.model_dump()==original.target.model_dump()
    report=svc.create_report(normalized)
    assert report['target']==original.target.model_dump()
    assert report['ensemble']['status']=='available'
    assert report['ensemble']['target_sha256']==svc.digest(original.target.model_dump())
    for body in calls:
        packet=json.loads(body['messages'][1]['content'])
        assert packet['target']==original.target.model_dump()
        assert svc.digest(packet)==report['ensemble']['input_sha256']


@pytest.mark.parametrize('field',['endpoint','population','comparison','success_definition'])
def test_target_all_whitespace_is_rejected(field):
    from pydantic import ValidationError
    payload=request().model_dump(mode='json');payload['target'][field]=' \n\t '
    with pytest.raises(ValidationError):svc.PredictionRequest(**payload)
