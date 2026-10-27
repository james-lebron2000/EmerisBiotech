"""Operator-to-adapter-to-ledger integration; transport is isolated, never paid."""
from copy import deepcopy
import hashlib,json,sys
import importlib.util
import io
from pathlib import Path
import zipfile
import httpx
import pytest
from emeris_live.arena_store import ArenaLedger
from emeris_live import arena_agents,arena_providers
from emeris_live.arena_attempt_diagnostics import new_diagnostics,sanitize_diagnostics
from emeris_live import arena_public
from emeris_live.arena_public_brief import build_brief
from test_arena_core import spec,admin_module
from test_arena_agents import execute as execute_agent,packet as agent_packet,models as agent_models,budget as agent_budget,spec as agent_spec


def test_registered_multimodel_system_executes_once_with_exact_receipt(tmp_path,monkeypatch):
    admin=admin_module();config=tmp_path/'models.json'
    config.write_text(json.dumps({'models':[{'id':ident,'provider':'openai','model':ident+'-fixture',
      'api_key_env':'ARENA_ISOLATED_KEY','max_output_tokens':8192,'timeout_seconds':120} for ident in ('A','B.2')]}))
    monkeypatch.setenv('EMERIS_ARENA_MODELS_FILE',str(config));monkeypatch.setenv('EMERIS_ENABLE_ARENA_LLM','true')
    monkeypatch.setenv('ARENA_ISOLATED_KEY','isolated-test-fixture')
    target,slots=spec();packet=slots[0]['packet'];calls=[]
    review={'findings':[{'evidence_id':'e1','kind':'limitation','statement':'Only an isolated fixture.'}],'key_unknowns':['Clinical effect unknown.']}
    output={'status':'abstain','probability':None,'target_definition':target['success_definition'],
        'reasoning':'Isolated test only.','evidence_ids':['e1'],'supporting_evidence_ids':[],
        'opposing_evidence_ids':['e1'],'key_assumptions':[],'key_unknowns':['Clinical effect unknown.']}
    original=httpx.Client
    def transport(request):
        data=json.loads(request.content);calls.append(data)
        return httpx.Response(200,json={'model':data['model'],'choices':[{'finish_reason':'stop',
            'message':{'content':json.dumps(review if len(calls)==1 else output)}}],
            'usage':{'prompt_tokens':100,'completion_tokens':50}})
    monkeypatch.setattr(arena_providers.httpx,'Client',lambda **kw:original(transport=httpx.MockTransport(transport),**kw))
    budget={'max_requests':2,'max_output_tokens':8192,'timeout_seconds':120,'max_input_chars':6000,
        'budget_policy':'token_only_unpriced','max_total_output_tokens':12288,'max_total_timeout_seconds':240,
        'stage_budgets':{'evidence_review':{'max_output_tokens':4096,'timeout_seconds':120},
                         'forecast':{'max_output_tokens':8192,'timeout_seconds':120}}}
    agent_spec=arena_agents.make_agent_spec('agent-fixture','A','B.2',tier='two-call-stage-v2')
    data={'cohort_id':'open-test','targets':[target],'slots':[{'target_id':target['id'],
        'model_id':agent_spec['agent_id'],'track':'open_agent','condition':'clinical_only',
        'packet':packet,'agent_spec':agent_spec}], 'protocol':{'execution_budget':budget}}
    file=tmp_path/'spec.json';file.write_text(json.dumps(data));policy=tmp_path/'budget.json';policy.write_text(json.dumps(budget))
    runtime=tmp_path/'runtime'
    monkeypatch.setattr(sys,'argv',['arena_admin','--data-dir',str(runtime),'register',str(file)]);admin.main()
    ledger=ArenaLedger(runtime);row=ledger.snapshot()['runs'][0]
    assert row['agent_contract']['components'][1]['id']=='B.2'
    assert row['runner_implementation_sha256']==hashlib.sha256(Path(admin.__file__).read_bytes()).hexdigest()
    changed=dict(row,agent_spec={**row['agent_spec'],'max_tool_calls':2})
    with pytest.raises(ValueError):admin.verify_registered_model(changed,{},budget)
    assert calls==[]
    monkeypatch.setattr(sys,'argv',['arena_admin','--data-dir',str(runtime),'execute','--cohort','open-test',
        '--max-calls','1','--budget-file',str(policy),'--model','agent-fixture'])
    admin.main();run=ledger.snapshot()['runs'][0]
    assert len(calls)==2 and [r['max_completion_tokens'] for r in calls]==[4096,8192]
    assert run['status']=='abstained' and run['probability'] is None
    assert run['provider']=='multi_model_agent' and run['contract_complete'] is True
    assert run['agent_trace']['stages'][1]['returned_model']=='B.2-fixture'
    assert run['cost_usd'] is None and run['cost_amount'] is None
    receipt=json.loads((runtime/run['receipt_path']).read_text())['result']
    assert receipt['agent_trace']==run['agent_trace'] and receipt['attempts']==run['attempts']
    assert all(sanitize_diagnostics(a['diagnostics'])==a['diagnostics'] for a in run['attempts'])
    assert all(a['diagnostics']['http_status']==200 for a in run['attempts'])
    admin.main();assert len(calls)==2


CANARY='PRIVATE-DIAGNOSTIC-CANARY-NEVER-PERSIST'


def store_agent_result(tmp_path,value):
    """Real new ledger/receipt for a transport-free production Agent execution."""
    target,_=spec();packet=agent_packet()
    target.update(packet['target'],information_cutoff_utc=packet['data_cutoff'])
    contract=arena_agents.freeze_agent_contract(agent_spec(),packet,agent_budget(),agent_models())
    slot={'target_id':target['id'],'model_id':contract['model_id'],'requested_model':contract['requested_model'],
        'provider':contract['provider'],'provider_config_snapshot':{'config_sha256':contract['config_sha256']},
        'track':'open_agent','condition':'clinical_only','packet':packet,
        'agent_spec':contract['agent_spec'],'agent_contract':contract,
        **{k:contract[k] for k in ('workflow_version','expected_provider_input_sha256','expected_prompt_sha256')}}
    ledger=ArenaLedger(tmp_path);ident=ledger.register('diagnostic-agent',[target],[slot],{})[0]
    started=ledger.start(ident)
    returned=admin_module().finish_with_receipt(ledger,tmp_path,ident,started,value)
    run=ledger.snapshot()['runs'][0]
    raw=(tmp_path/run['receipt_path']).read_bytes()
    document=json.loads(raw)
    assert document['schema_version']=='arena-result-receipt-v1'
    assert hashlib.sha256(raw).hexdigest()==run['receipt_sha256']
    return ledger,returned,run,document['result']


def test_agent_diagnostics_survive_trace_receipt_accounting_and_contract_rejection(tmp_path):
    diagnostic=new_diagnostics(True)
    diagnostic.update(http_status=200,content_type_class='json',body_read={'state':'complete','observed_bytes':123})
    def attach(value):value['attempts'][0]['diagnostics']=deepcopy(diagnostic)
    value,calls=execute_agent(change_review=attach,change_forecast=attach)
    assert len(calls)==2 and value['status']=='predicted'
    assert arena_agents.VERSION=='arena-agents-1.1.0'
    assert all(a['diagnostics']==diagnostic for a in value['attempts'])
    assert all(s['attempts'][0]['diagnostics']==diagnostic for s in value['agent_trace']['stages'])
    original=deepcopy(value)
    _,_,run,saved=store_agent_result(tmp_path/'accepted',value)
    assert value==original
    assert run['attempts']==saved['attempts']==value['attempts']
    assert run['agent_trace']==saved['agent_trace']==value['agent_trace']
    assert run['agent_trace_sha256']==arena_providers.digest(run['agent_trace'])
    assert run['cost_amount']==saved['cost_amount']==.04
    assert run['usage']['input_tokens']==200 and run['cost_usd'] is None
    rejected=deepcopy(value);rejected['evidence_ids']=['invented']
    ledger,returned,run,saved=store_agent_result(tmp_path/'rejected',rejected)
    assert returned['error_code']==run['error_code']=='contract_rejected'
    assert run['status']=='failed' and run['probability'] is None
    assert run['attempts']==saved['attempts']==value['attempts']
    assert run['cost_amount']==.04 and run['usage']['input_tokens']==200
    assert saved['agent_trace']==value['agent_trace']
    assert len([e for e in ledger.events() if e['kind']=='run_completed'])==1
    assert len(calls)==2


@pytest.mark.parametrize('poison',[CANARY,{'schema_version':CANARY},
    {**new_diagnostics(),'raw_error':{'Authorization':CANARY}},
    {**new_diagnostics(),'http_status':CANARY},
    {**new_diagnostics(),'json_error':{'kind':'syntax_error','line':1,'column':1,'position':0,'snippet':CANARY}}])
def test_invalid_diagnostics_are_null_at_both_agent_and_admin_boundaries(tmp_path,poison):
    def attach(value):value['attempts'][0]['diagnostics']=deepcopy(poison)
    value,calls=execute_agent(change_review=attach,change_forecast=attach)
    assert value['status']=='predicted' and value['probability']==.61 and len(calls)==2
    assert all(a['diagnostics'] is None for a in value['attempts'])
    assert all(s['attempts'][0]['diagnostics'] is None for s in value['agent_trace']['stages'])
    # Challenge the admin boundary independently after the Agent already sanitized.
    for attempt in value['attempts']:attempt['diagnostics']=deepcopy(poison)
    for stage in value['agent_trace']['stages']:stage['attempts'][0]['diagnostics']=deepcopy(poison)
    original=deepcopy(value)
    ledger,returned,run,saved=store_agent_result(tmp_path,value)
    assert value==original
    assert run['status']=='predicted' and returned['probability']==.61
    assert run['cost_amount']==.04 and run['usage']['input_tokens']==200
    assert all(a['diagnostics'] is None for a in run['attempts'])
    assert all(s['attempts'][0]['diagnostics'] is None for s in run['agent_trace']['stages'])
    assert run['attempts']==saved['attempts'] and run['agent_trace']==saved['agent_trace']
    assert CANARY not in json.dumps(ledger.events())+json.dumps(saved)


def test_missing_historical_diagnostics_stay_missing_and_old_agent_cannot_execute(tmp_path,monkeypatch):
    value,calls=execute_agent()
    assert len(calls)==2 and all('diagnostics' not in a for a in value['attempts'])
    ledger,_,run,saved=store_agent_result(tmp_path,value)
    assert all('diagnostics' not in a for a in saved['attempts'])
    assert all('diagnostics' not in s['attempts'][0] for s in run['agent_trace']['stages'])
    old=agent_spec();old['version']='arena-agents-1.0.0'
    before=ledger.events()
    with pytest.raises(arena_agents.AgentContractError,match='invalid_agent_spec'):
        arena_agents.freeze_agent_contract(old,agent_packet(),agent_budget(),agent_models())
    assert ledger.events()==before and ledger.snapshot()['runs'][0]==run
    # A synthetic historical spec remains readable; current execution rejects it.
    target,slots=spec();legacy_slot={**slots[0],'track':'open_agent','agent_spec':old}
    legacy=ArenaLedger(tmp_path/'old-read-only');legacy.register('old-agent',[target],[legacy_slot],{})
    old_run=legacy.snapshot()['runs'][0];before=legacy.events()
    admin=admin_module();monkeypatch.setattr(admin,'list_models',agent_models)
    with pytest.raises(arena_agents.AgentContractError,match='invalid_agent_spec'):
        admin.verify_registered_model(old_run,{},agent_budget())
    assert legacy.events()==before and legacy.snapshot()['runs'][0]==old_run


@pytest.mark.parametrize('track',['same_evidence','open_agent'])
def test_helper_only_change_rejects_registered_execution_before_start(tmp_path,monkeypatch,track):
    admin=admin_module();models=[{k:v for k,v in m.items() if k!='api_key'} for m in agent_models()]
    monkeypatch.setattr(admin,'list_models',lambda:deepcopy(models))
    provider_shadow=tmp_path/'arena_providers.py';helper_shadow=tmp_path/'arena_attempt_diagnostics.py'
    provider_shadow.write_bytes(Path(arena_providers.__file__).read_bytes())
    helper_shadow.write_bytes(Path(arena_providers.__file__).with_name(helper_shadow.name).read_bytes())
    monkeypatch.setattr(arena_providers,'__file__',str(provider_shadow))
    target,slots=spec();slot=slots[0];slot.pop('workflow_version');slot['track']=track
    slot['model_id']='reviewer'
    if track=='open_agent':
        slot['agent_spec']=agent_spec();slot['model_id']=slot['agent_spec']['agent_id']
    budget=agent_budget()
    source=tmp_path/'spec.json';source.write_text(json.dumps({'cohort_id':'helper-frozen','targets':[target],
        'slots':[slot],'protocol':{'execution_budget':budget}}))
    policy=tmp_path/'budget.json';policy.write_text(json.dumps(budget));runtime=tmp_path/'runtime'
    monkeypatch.setattr(sys,'argv',['arena_admin','--data-dir',str(runtime),'register',str(source)]);admin.main()
    ledger=ArenaLedger(runtime);before=ledger.events();run=ledger.snapshot()['runs'][0]
    previous_hash=arena_providers.provider_implementation_sha256()
    helper_shadow.write_bytes(helper_shadow.read_bytes()+b'\n# isolated helper-only contract change\n')
    assert arena_providers.provider_implementation_sha256()!=previous_hash
    calls=[]
    def forbidden(*args,**kwargs):calls.append(True);raise AssertionError('No dispatch before frozen-contract verification')
    monkeypatch.setattr(admin,'forecast',forbidden);monkeypatch.setattr(arena_agents,'run_agent',forbidden)
    monkeypatch.setattr(sys,'argv',['arena_admin','--data-dir',str(runtime),'execute','--cohort','helper-frozen',
        '--max-calls','1','--budget-file',str(policy)])
    with pytest.raises(ValueError,match='changed since registration'):admin.main()
    assert calls==[] and ledger.events()==before
    assert ledger.snapshot()['runs'][0]==run and run['status']=='pending' and not run.get('started_at')


def test_broad_runner_failure_remains_once_unknown_and_has_no_invented_attempt(tmp_path,monkeypatch):
    admin=admin_module();models=[{k:v for k,v in m.items() if k!='api_key'} for m in agent_models()]
    monkeypatch.setattr(admin,'list_models',lambda:deepcopy(models))
    target,slots=spec();slot=slots[0];slot.pop('workflow_version');slot['model_id']='reviewer'
    budget=agent_budget();source=tmp_path/'spec.json';policy=tmp_path/'budget.json';runtime=tmp_path/'runtime'
    source.write_text(json.dumps({'cohort_id':'runner-failure','targets':[target],'slots':[slot],
        'protocol':{'execution_budget':budget}}));policy.write_text(json.dumps(budget))
    monkeypatch.setattr(sys,'argv',['arena_admin','--data-dir',str(runtime),'register',str(source)]);admin.main()
    calls=[]
    def failed(*args,**kwargs):calls.append(True);raise RuntimeError(CANARY)
    monkeypatch.setattr(admin,'forecast',failed)
    monkeypatch.setattr(sys,'argv',['arena_admin','--data-dir',str(runtime),'execute','--cohort','runner-failure',
        '--max-calls','1','--budget-file',str(policy)])
    admin.main();ledger=ArenaLedger(runtime);run=ledger.snapshot()['runs'][0]
    raw=(runtime/run['receipt_path']).read_text();saved=json.loads(raw)['result']
    assert run['status']=='failed' and run['error_code']=='runner_exception' and run['probability'] is None
    assert run['billing_status']=='unknown_after_runner_exception' and run['cost_usd'] is None
    assert run['attempts']==saved['attempts']==[] and CANARY not in raw+json.dumps(ledger.events())
    assert 'diagnostics' not in saved
    before=ledger.events();admin.main()
    assert calls==[True] and ledger.events()==before


def test_private_diagnostics_do_not_enter_public_projection_brief_or_campaign(tmp_path):
    admin=admin_module();target,slots=spec()
    slots=[{**deepcopy(slots[0]),'model_id':name,'requested_model':name+'-fixed'} for name in ('A','B','C','D')]
    ledger=ArenaLedger(tmp_path);ids=ledger.register('public-diagnostics',[target],slots,{'season_kind':'registered_12_project_season'})
    started=ledger.start(ids[0]);diagnostic=new_diagnostics(False)
    admin.finish_with_receipt(ledger,tmp_path,ids[0],started,{'status':'failed','probability':None,
        'error_code':'provider_http_error','attempts':[{'stage':'forecast','diagnostics':diagnostic}]})
    snapshot=ledger.snapshot();raw_target=snapshot['targets'][0]
    runs=[arena_public.run_view(run,raw_target,[]) for run in snapshot['runs']]
    public_target=arena_public.target_view(raw_target,[]);summary=arena_public.consensus(runs)
    public_target['consensus']=summary
    record={'version':arena_public.VERSION,'target':public_target,'runs':runs,'consensus':summary,
        'sources':[],'evidence':[],'result':None,'public_policy':dict(arena_public.POLICY)}
    brief,_=build_brief(record)
    module_spec=importlib.util.spec_from_file_location('diagnostic_public_campaign',Path(__file__).parents[1]/'scripts/export_public_campaign.py')
    export=importlib.util.module_from_spec(module_spec);module_spec.loader.exec_module(export)
    campaign,_=export.bundle({'current_cohort_id':'public-diagnostics','targets':[public_target],'runs':runs},lambda _:deepcopy(record))
    public_bytes=json.dumps(record).encode()
    for archive in (brief,campaign):
        with zipfile.ZipFile(io.BytesIO(archive)) as zipped:public_bytes+=b''.join(zipped.read(name) for name in zipped.namelist())
    assert b'"diagnostics"' not in public_bytes and b'arena-attempt-diagnostics-1' not in public_bytes
    assert ledger.snapshot()['runs'][0]['attempts'][0]['diagnostics']==diagnostic
