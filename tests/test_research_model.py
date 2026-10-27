"""Transport fixtures are synthetic engineering checks, never model evidence."""
import copy
import io
import json
import socket
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from urllib.parse import urlencode

import pytest
from vbt.programs import Programs, Conflict, canonical
from vbt.ai_research import DOMAINS, packet, proposals, ingest
from vbt.research_model import configuration, generate, MAX_RESPONSE
from vbt.workbench import Workbench, handler, page
from test_programs import PROFILE, add

KEY = 'SYNTHETIC-CREDENTIAL-CANARY-ONLY'
HIDDEN = 'HIDDEN-REASONING-CANARY-MUST-NOT-PERSIST'


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch):
    import os
    for name in list(os.environ):
        if name.startswith('VBT_RESEARCH_'):
            monkeypatch.delenv(name)


@pytest.fixture
def ready(tmp_path, monkeypatch):
    monkeypatch.setenv('VBT_RESEARCH_MODEL_URL', 'https://models.example.invalid/v1/chat/completions')
    monkeypatch.setenv('VBT_RESEARCH_MODEL', 'configured-engineering-model')
    monkeypatch.setenv('VBT_RESEARCH_MODEL_API_KEY', KEY)
    store = Programs(tmp_path/'state'); pid = store.create(PROFILE, 'engineering author'); ref = add(store, pid)
    return store, pid, ref


def draft(store, pid, ref):
    return dict(packet_hash=packet(store,pid)['packet_hash'],summary='Synthetic transport fixture; not a real scientific proposal',
        risks=[dict(domain=k,status='unknown',reason='Unknown in synthetic engineering fixture',references=[]) for k in DOMAINS],
        tasks=[dict(id='manual',tool='manual_research',question='Check original source mappings explicitly',
            rationale='Missing mappings must remain unknown in this fixture',next_action='Obtain actual source mapping from a real researcher',
            success_criterion='Mapping can be independently verified',stop_criterion='No original mapping; retain missing state',references=[ref])])


def envelope(proposal):
    return dict(model='returned-engineering-model',usage=dict(prompt_tokens=100,completion_tokens=50,total_tokens=150),
                choices=[dict(finish_reason='stop',message=dict(role='assistant',content=canonical(proposal),reasoning_content=HIDDEN))])


def transport(monkeypatch, response=None, status=200, error=None, during=None, raw=None, content_type='application/json'):
    calls=[]; reads=[]
    class Response:
        def __init__(self):
            self.status=status
            self.stream=io.BytesIO(raw if raw is not None else canonical(response).encode())
        def getheader(self, name, default=''):return content_type
        def read1(self, n):reads.append(n);return self.stream.read(n)
    class Connection:
        sock=None
        def __init__(self,host,port,timeout):
            assert host=='models.example.invalid' and timeout==60
        def request(self, method, path, body, headers):
            calls.append(dict(method=method,path=path,body=json.loads(body),headers=headers))
            if during:during()
            if error:raise error
        def getresponse(self):return Response()
        def close(self):pass
    monkeypatch.setattr('vbt.research_model.http.client.HTTPSConnection',Connection)
    return calls,reads


def run(ready, **kw):
    store,pid,ref=ready;state=configuration()[1]
    return generate(store,pid,'engineering operator',store.get(pid)['revision'],packet(store,pid)['packet_hash'],state['config_hash'],True,**kw)


def test_actual_adapter_success_is_atomic_with_draft_and_never_executes(ready,monkeypatch):
    store,pid,ref=ready;before=packet(store,pid)
    calls,_=transport(monkeypatch,envelope(draft(store,pid,ref)))
    receipt=run(ready)
    assert receipt['status']=='succeeded' and receipt['scientific_review']=='pending'
    assert receipt['configured_model']=='configured-engineering-model' and receipt['returned_model']=='returned-engineering-model'
    assert receipt['usage']['complete_and_consistent'] and receipt['estimated_cost'] is None and receipt['actual_billed_cost'] is None
    events=store.events(pid);assert [e['kind'] for e in events[-2:]]==['ai_model_request','ai_model_receipt']
    assert len(calls)==1 and calls[0]['headers']['Authorization']=='Bearer '+KEY
    body=calls[0]['body'];assert body['store'] is False and body['stream'] is False
    assert body['max_completion_tokens']==8192 and body['response_format']=={'type':'json_object'}
    assert json.loads(body['messages'][1]['content'])==before
    assert proposals(store,pid)[-1]['origin']['kind']=='model_service' and not proposals(store,pid)[-1]['stale']
    saved=store.export(pid).decode();assert KEY not in saved and HIDDEN not in saved
    assert packet(store,pid)==before and not store.get(pid)['experiments'] and not store.get(pid)['decisions']


@pytest.mark.parametrize('status',[302,401,403,429,500,503])
def test_http_errors_never_follow_redirect_read_error_body_or_retry(ready,monkeypatch,status):
    calls,reads=transport(monkeypatch,status=status,raw=(KEY+' error body').encode())
    receipt=run(ready)
    assert len(calls)==1 and not reads
    assert receipt['error']=='http_error' and receipt['http_status']==status
    assert receipt['usage']['total_tokens'] is None and receipt['actual_billed_cost'] is None
    assert receipt['draft'] is None and receipt['status']=='failed'


@pytest.mark.parametrize('error',[socket.timeout('private secret '+KEY),OSError('Authorization: '+KEY)])
def test_transport_failure_is_fixed_code_and_unknown_usage(ready,monkeypatch,error):
    store,pid,ref=ready;calls,_=transport(monkeypatch,error=error)
    r=run(ready);assert r['status']=='failed' and r['http_status'] is None
    assert r['error'] in ('transport_timeout','transport_error') and len(calls)==1
    assert r['usage']['prompt_tokens'] is None and KEY not in store.export(pid).decode()


@pytest.mark.parametrize('failure',['bad_envelope','duplicate','nonfinite','fenced','wrong_refs','extra_field','truncated','tools','wrong_top','echo','oversized','bad_content_type'])
def test_strict_output_validation_preserves_failure_not_a_draft(ready,monkeypatch,failure):
    store,pid,ref=ready;proposal=draft(store,pid,ref);response=envelope(proposal);raw=None;ctype='application/json'
    if failure=='bad_envelope':raw=b'{broken'
    elif failure=='duplicate':raw=b'{"choices":[],"choices":[]}'
    elif failure=='nonfinite':raw=b'{"choices":NaN}'
    elif failure=='fenced':response['choices'][0]['message']['content']='```json\n'+canonical(proposal)+'\n```'
    elif failure=='wrong_refs':proposal['tasks'][0]['references']=['fabricated'];response=envelope(proposal)
    elif failure=='extra_field':proposal['tasks'][0]['command']='shell canary';response=envelope(proposal)
    elif failure=='truncated':response['choices'][0]['finish_reason']='length'
    elif failure=='tools':response['choices'][0]['message']['tool_calls']=[{'name':'execute'}]
    elif failure=='wrong_top':response['choices'][0]['message']['content']='[]'
    elif failure=='echo':response['model']=KEY
    elif failure=='oversized':raw=b'x'*(MAX_RESPONSE+2)
    else:ctype='text/html; canary='+KEY
    calls,_=transport(monkeypatch,response,raw=raw,content_type=ctype)
    r=run(ready)
    assert len(calls)==1 and r['status']=='failed' and r['draft'] is None and not proposals(store,pid)
    assert KEY not in store.export(pid).decode() and HIDDEN not in store.export(pid).decode()
    if failure in ('wrong_refs','extra_field','fenced','truncated','tools','wrong_top'):
        assert r['usage']['total_tokens']==150
    if failure=='oversized':assert r['response_sha256'] is None and r['response_complete'] is False


def test_context_change_during_request_keeps_usage_but_not_current_draft(ready,monkeypatch):
    store,pid,ref=ready;calls,_=transport(monkeypatch,envelope(draft(store,pid,ref)),during=lambda:add(store,pid))
    r=run(ready)
    assert r['status']=='stale' and r['draft'] is None and r['usage']['total_tokens']==150
    assert len(calls)==1 and not proposals(store,pid)


def test_receipt_storage_conflict_does_not_repeat_provider(ready,monkeypatch):
    store,pid,ref=ready;calls,_=transport(monkeypatch,envelope(draft(store,pid,ref)))
    original=store._append;conflicts=[]
    def append(pid,kind,payload,author,expected):
        if kind=='ai_model_receipt' and not conflicts:
            conflicts.append(True);raise Conflict('synthetic concurrent writer')
        return original(pid,kind,payload,author,expected)
    monkeypatch.setattr(store,'_append',append)
    assert run(ready)['status']=='succeeded' and len(calls)==1


@pytest.mark.parametrize('change',['no_choice','stale_packet','stale_revision','changed_target'])
def test_preflight_rejects_without_any_provider_or_request(ready,monkeypatch,change):
    store,pid,ref=ready;calls,_=transport(monkeypatch,envelope(draft(store,pid,ref)));revision=store.get(pid)['revision']
    packet_hash=packet(store,pid)['packet_hash'];config_hash=configuration()[1]['config_hash'];choice=True
    if change=='no_choice':choice=False
    elif change=='stale_packet':packet_hash='old'
    elif change=='stale_revision':add(store,pid)
    else:monkeypatch.setenv('VBT_RESEARCH_MODEL','different-model')
    with pytest.raises(ValueError):generate(store,pid,'operator',revision,packet_hash,config_hash,choice)
    assert not calls and not [e for e in store.events(pid) if e['kind']=='ai_model_request']


def test_not_configured_is_unavailable_with_honest_no_network_receipt(tmp_path,monkeypatch):
    store=Programs(tmp_path/'state');pid=store.create(PROFILE,'engineering operator');add(store,pid)
    calls,_=transport(monkeypatch,raw=b'never read')
    config,state=configuration();assert config is None and state['status']=='not_configured'
    r=generate(store,pid,'operator',store.get(pid)['revision'],packet(store,pid)['packet_hash'],None,True)
    assert r['status']=='failed' and r['error']=='not_configured' and not r['transport_attempted'] and not calls
    app=Workbench(tmp_path/'workspace',store.state);body=page(app,'/programs',{'id':pid})
    assert '模型生成不可用' in body and 'name="send_context"' not in body


@pytest.mark.parametrize('url',['http://models.invalid/v1/chat/completions','https://name:password@models.invalid/v1/chat/completions','https://models.invalid/v1/chat/completions?key=private','https://models.invalid/v1/chat/completions#private','https://models.invalid/wrong'])
def test_operator_endpoint_validation_never_echoes_raw_invalid_config(ready,monkeypatch,url):
    monkeypatch.setenv('VBT_RESEARCH_MODEL_URL',url)
    config,state=configuration()
    assert config is None and state==dict(status='invalid_config',config_hash=None,config=None)


def test_cost_is_separate_estimate_and_unknown_usage_stays_unknown(ready,monkeypatch):
    store,pid,ref=ready
    monkeypatch.setenv('VBT_RESEARCH_INPUT_USD_PER_MTOK','2');monkeypatch.setenv('VBT_RESEARCH_OUTPUT_USD_PER_MTOK','4')
    response=envelope(draft(store,pid,ref));response['usage']['prompt_tokens_details']={'cached_tokens':80}
    response['usage']['completion_tokens_details']={'reasoning_tokens':20}
    transport(monkeypatch,response)
    r=run(ready);assert r['estimated_cost']['amount']=='0.0004' and r['actual_billed_cost'] is None
    response.pop('model');response['usage']={'prompt_tokens':True,'completion_tokens':50}
    transport(monkeypatch,response)
    r=run(ready);assert r['returned_model'] is None and r['usage']['prompt_tokens'] is None and r['estimated_cost'] is None


def test_crash_retains_unknown_request_without_synthesized_success(ready,monkeypatch,tmp_path):
    store,pid,ref=ready
    def crash(*args):raise KeyboardInterrupt()
    monkeypatch.setattr('vbt.research_model.call_once',crash)
    with pytest.raises(KeyboardInterrupt):run(ready)
    assert store.events(pid)[-1]['kind']=='ai_model_request' and not proposals(store,pid)
    body=page(Workbench(tmp_path/'workspace',store.state),'/programs',{'id':pid})
    assert '状态未知' in body and '不会自动重试' in body


def test_model_origin_cannot_be_spoofed_by_import(ready):
    store,pid,ref=ready
    with pytest.raises(ValueError,match='如实记录'):
        ingest(store,pid,draft(store,pid,ref),'operator',store.get(pid)['revision'],dict(kind='model_service',model_label='forged'))


@pytest.mark.parametrize('name,value',[
    ('VBT_RESEARCH_TIMEOUT_SECONDS','0'),('VBT_RESEARCH_MAX_OUTPUT_TOKENS','999999'),
    ('VBT_RESEARCH_TOKEN_PARAMETER','made_up'),('VBT_RESEARCH_JSON_MODE','repair'),
    ('VBT_RESEARCH_INPUT_USD_PER_MTOK','NaN'),('VBT_RESEARCH_INPUT_USD_PER_MTOK','2'),
    ('VBT_RESEARCH_MODEL_API_KEY','header\ninjection'),('VBT_RESEARCH_MODEL',KEY)])
def test_invalid_host_config_exposes_only_fixed_status(ready,monkeypatch,name,value):
    monkeypatch.setenv(name,value)
    config,status=configuration()
    assert config is None and status['status']=='invalid_config' and KEY not in canonical(status)


def test_missing_credential_and_large_context_do_not_contact_provider(ready,monkeypatch):
    store,pid,ref=ready;calls,_=transport(monkeypatch,envelope(draft(store,pid,ref)))
    monkeypatch.delenv('VBT_RESEARCH_MODEL_API_KEY')
    assert configuration()[1]['status']=='missing_credential'
    assert run(ready)['error']=='missing_credential' and not calls
    monkeypatch.setenv('VBT_RESEARCH_MODEL_API_KEY',KEY)
    context=packet(store,pid);context['large_untrusted_source']='x'*(256*1024)
    monkeypatch.setattr('vbt.research_model.packet',lambda *_:context)
    before=len(store.events(pid))
    with pytest.raises(ValueError,match='256KB'):run(ready)
    assert len(store.events(pid))==before and not calls


def test_partial_read_failure_keeps_observed_bytes_and_no_complete_hash(ready,monkeypatch):
    calls,_=transport(monkeypatch,raw=b'ignored')
    # Replace only the response's reader; the actual adapter handles transport.
    import vbt.research_model as model
    original=model.http.client.HTTPSConnection
    class Partial(original):
        def getresponse(self):
            response=super().getresponse();count=[]
            def read(n):
                if count:raise OSError('private partial body '+KEY)
                count.append(True);return b'{"incomplete":'
            response.read1=read;return response
    monkeypatch.setattr(model.http.client,'HTTPSConnection',Partial)
    r=run(ready)
    assert len(calls)==1 and r['error']=='transport_error' and r['http_status']==200
    assert r['observed_response_bytes']==len(b'{"incomplete":') and not r['response_complete'] and r['response_sha256'] is None


def test_json_escaped_credentials_are_not_persisted(ready,monkeypatch):
    store,pid,ref=ready;proposal=draft(store,pid,ref)
    proposal['summary']='Synthetic visible credential echo '+KEY
    visible=canonical(proposal).replace(KEY,'\\u0053'+KEY[1:])
    response=envelope(proposal);response['choices'][0]['message']['content']=visible
    raw=canonical(response).encode();assert KEY.encode() not in raw
    transport(monkeypatch,raw=raw)
    r=run(ready)
    assert r['error']=='credential_echo_rejected' and KEY not in store.export(pid).decode() and not proposals(store,pid)


def test_short_http_body_is_not_certified_complete_even_if_prefix_is_valid_json(ready,monkeypatch):
    store,pid,ref=ready;calls,_=transport(monkeypatch,envelope(draft(store,pid,ref)))
    import vbt.research_model as model
    original=model.http.client.HTTPSConnection
    class ShortBody(original):
        def getresponse(self):
            response=super().getresponse();response.length=5
            return response
    monkeypatch.setattr(model.http.client,'HTTPSConnection',ShortBody)
    r=run(ready)
    assert r['error']=='response_incomplete' and not r['response_complete'] and r['response_sha256'] is None
    assert len(calls)==1 and not proposals(store,pid)


def test_http_explicit_send_and_csrf_use_actual_adapter(ready,monkeypatch,tmp_path):
    store,pid,ref=ready;calls,_=transport(monkeypatch,envelope(draft(store,pid,ref)))
    app=Workbench(tmp_path/'workspace',store.state)
    body=page(app,'/programs',{'id':pid})
    assert 'models.example.invalid' in body and '完整冻结上下文' in body and KEY not in body
    server=ThreadingHTTPServer(('127.0.0.1',0),handler(app));threading.Thread(target=server.serve_forever,daemon=True).start()
    base=f'http://127.0.0.1:{server.server_port}'
    try:
        data=dict(operation='ai_generate',id=pid,revision=store.get(pid)['revision'],author='HTTP engineering operator',
                  packet_hash=packet(store,pid)['packet_hash'],config_hash=configuration()[1]['config_hash'],send_context='yes',token='bad')
        req=lambda:urllib.request.Request(base+'/program/action',data=urlencode(data).encode(),headers={'Origin':base})
        with pytest.raises(urllib.error.HTTPError) as exc:urllib.request.urlopen(req())
        assert exc.value.code==403 and not calls
        data['token']=app.token
        assert urllib.request.urlopen(req()).status==200
        assert len(calls)==1 and proposals(store,pid)[-1]['origin']['kind']=='model_service'
        assert '提供方返回模型' in page(app,'/programs',{'id':pid})
    finally:server.shutdown();server.server_close()
