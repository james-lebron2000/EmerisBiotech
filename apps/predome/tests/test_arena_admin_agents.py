"""Operator-to-adapter-to-ledger integration; transport is isolated, never paid."""
import hashlib,json,sys
from pathlib import Path
import httpx
import pytest
from emeris_live.arena_store import ArenaLedger
from emeris_live import arena_agents,arena_providers
from test_arena_core import spec,admin_module


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
    admin.main();assert len(calls)==2
