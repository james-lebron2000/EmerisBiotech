"""Synthetic mission/transport fixtures; real fixed worker subprocesses."""
import json
import pytest
from test_ai_research import ready
from vbt.programs import Conflict
from vbt import research_mission as mission
from vbt import research_model as model
from vbt.ai_research import packet,DOMAINS,TOOLS
from vbt.program_backup import snapshot,restore,state_inventory


def admit(s,pid,mode='existing_evidence',**kw):
    data=dict(mode=mode,trigger_id='synthetic-request-1',goal='核对合成工程资料的来源与既有结果，不开展科学验证',
              criteria='保留真实工具观察，明确缺失项与未验证范围',max_model_calls=0 if mode=='existing_evidence' else 2,
              max_tool_attempts=4,max_seconds=60,allow_model_send='yes',config_hash=model.configuration()[1]['config_hash'])
    data.update(kw)
    return mission.create(s,pid,data,'engineering operator',s.get(pid)['revision'])


def configured(monkeypatch):
    monkeypatch.setenv('VBT_RESEARCH_MODEL_URL','https://models.example.invalid/v1/chat/completions')
    monkeypatch.setenv('VBT_RESEARCH_MODEL','synthetic-coordinator-model')
    monkeypatch.setenv('VBT_RESEARCH_MODEL_API_KEY','SYNTHETIC_SECRET_NOT_IN_MISSION')


def proposal(base,tools=None):
    ref=base['evidence'][0]['event_id']
    return dict(packet_hash=base['packet_hash'],summary='合成模型计划，只用于检查预算与反馈循环的软件行为',
                risks=[dict(domain=d,status='unknown',reason='该合成测试不能建立任何临床或资产有效性',references=[]) for d in DOMAINS],
                tasks=[dict(id=t,tool=t,question='核对合成固定来源与结果',rationale='输出需要真实工具回执与来源版本',next_action='执行主机固定只读工具并查看全部观察',success_criterion='摘要文件哈希与所报结果完整保留',stop_criterion='缺少来源或输出不合法时停止本次计划',references=[ref]) for t in (tools or TOOLS)])


def response(context,action='plan',tools=None):
    refs=[o['reference_id'] for o in context['observations'] if o['status']=='succeeded']
    return dict(mission_packet_hash=context['mission_packet_hash'],action=action,
                answer='合成模型的结构化方法检查结果，不能代替实际研究者审阅',
                findings=[dict(claim='固定工具观察保留了合成来源与所报结果，尚无临床验证',references=refs)] if refs else [],
                missing=['真实科学资料与独立人员审阅仍待落实'],proposal=proposal(context['base'],tools) if action=='plan' else None)


def transport(monkeypatch,answers):
    calls=[]
    def invoke(config,raw):
        body=json.loads(raw);context=json.loads(body['messages'][1]['content']);calls.append(context)
        choice=answers[len(calls)-1]
        candidate=choice(context) if callable(choice) else response(context,choice)
        return dict(error=None,proposal=candidate,http_status=200,returned_model='synthetic-returned-model',
                    usage=model.usage_record(dict(prompt_tokens=10,completion_tokens=10,total_tokens=20)),
                    response_sha256='synthetic-response-hash',response_complete=True,finish_reason='stop',elapsed_seconds=.01)
    monkeypatch.setattr(model,'call_once',invoke)
    return calls


def test_real_local_ticket_runs_two_workers_and_replay_does_not_dispatch(ready):
    s,pid,_,_=ready;before=packet(s,pid)['packet_hash'];mid=admit(s,pid)
    state=mission.run(s,pid,mid,'operator',s.get(pid)['revision'])
    assert state['status']=='delivered_unreviewed' and len(state['tool_requests'])==2
    assert not state['model_requests'] and all(e['payload']['status']=='succeeded' for e in state['tool_receipts'])
    assert packet(s,pid)['packet_hash']==before # observations do not invalidate the batch's own frozen basis
    rev=s.get(pid)['revision'];assert mission.run(s,pid,mid,'operator',rev)['status']=='delivered_unreviewed'
    assert s.get(pid)['revision']==rev
    p=s.get(pid);assert not p['experiments'] and not p['decisions'] and not p['diligence']
    assert not any(e['kind']=='scientific_assessment' for e in p['events'])
    assert json.loads(mission.export(s,pid,mid))['scientific_review']=='not_inferred'


def test_identical_trigger_is_idempotent_but_reused_scope_rejected(ready):
    s,pid,_,_=ready;mid=admit(s,pid);revision=s.get(pid)['revision']
    assert admit(s,pid)==mid and s.get(pid)['revision']==revision
    with pytest.raises(ValueError,match='同一触发'):admit(s,pid,goal='这是一项不同目标，不能覆盖旧的授权任务')


@pytest.mark.parametrize('change',[{'max_model_calls':1},{'max_tool_attempts':0},{'max_seconds':601},{'max_seconds':True},{'mode':'unbounded'}])
def test_invalid_offline_mandate_cannot_expand_execution(ready,change):
    s,pid,_,_=ready;before=s.get(pid)['revision']
    with pytest.raises(ValueError):admit(s,pid,**change)
    assert s.get(pid)['revision']==before


def test_missing_or_unapproved_model_does_not_create_authority(ready,monkeypatch):
    s,pid,_,_=ready
    for name in ('VBT_RESEARCH_MODEL_URL','VBT_RESEARCH_MODEL','VBT_RESEARCH_MODEL_API_KEY'):monkeypatch.delenv(name,raising=False)
    with pytest.raises(ValueError,match='配置'):admit(s,pid,mode='model_research')
    configured(monkeypatch)
    with pytest.raises(ValueError):admit(s,pid,mode='model_research',allow_model_send='no')


def test_model_coordinator_worker_observation_reviewer_loop_at_exact_call_cap(ready,monkeypatch):
    configured(monkeypatch);s,pid,_,_=ready;calls=transport(monkeypatch,['plan','deliver'])
    mid=admit(s,pid,mode='model_research');state=mission.run(s,pid,mid,'operator',s.get(pid)['revision'])
    assert state['status']=='delivered_unreviewed' and len(calls)==2
    assert [c['role'] for c in calls]==['scientific_coordinator','scientific_reviewer']
    assert not calls[0]['observations'] and len(calls[1]['observations'])==2
    assert all(o['output'] and o['status']=='succeeded' for o in calls[1]['observations'])
    assert calls[1]['previous_decision']['action']=='plan'
    assert 'SYNTHETIC_SECRET_NOT_IN_MISSION' not in s.export(pid).decode()
    assert len(state['tool_requests'])==2
    assert all(r['payload']['mission_id']==mid for r in state['tool_requests'])
    assert not s.get(pid)['decisions'] and not s.get(pid)['results']


def test_iterative_followup_uses_new_observations_and_stops_repeat_plan(ready,monkeypatch):
    configured(monkeypatch);s,pid,_,_=ready
    calls=transport(monkeypatch,[lambda c:response(c,'plan',['audit_bound_sources']),lambda c:response(c,'plan',['extract_interaction_evidence']),'deliver'])
    mid=admit(s,pid,mode='model_research',max_model_calls=3)
    result=mission.run(s,pid,mid,'operator',s.get(pid)['revision'])
    assert result['status']=='delivered_unreviewed' and [len(c['observations']) for c in calls]==[0,1,2]


def test_no_progress_plan_does_not_repeat_completed_tools(ready,monkeypatch):
    configured(monkeypatch);s,pid,_,_=ready;calls=transport(monkeypatch,['plan','plan'])
    mid=admit(s,pid,mode='model_research',max_model_calls=4);state=mission.run(s,pid,mid,'operator',s.get(pid)['revision'])
    assert state['status']=='needs_input' and len(calls)==2 and len(state['tool_requests'])==2


def test_call_budget_stops_before_third_model_request(ready,monkeypatch):
    configured(monkeypatch);s,pid,_,_=ready
    calls=transport(monkeypatch,[lambda c:response(c,'plan',['audit_bound_sources']),lambda c:response(c,'plan',['extract_interaction_evidence'])])
    mid=admit(s,pid,mode='model_research');state=mission.run(s,pid,mid,'operator',s.get(pid)['revision'])
    assert state['status']=='budget_exhausted' and len(calls)==2


def test_unknown_model_outcome_is_preserved_without_provider_retry(ready,monkeypatch):
    configured(monkeypatch);s,pid,_,_=ready;mid=admit(s,pid,mode='model_research')
    s._append(pid,'ai_model_request',dict(mission_id=mid,scope='synthetic interrupted request'),'fixture',s.get(pid)['revision'])
    monkeypatch.setattr(model,'call_once',lambda *a:pytest.fail('unknown request cannot be replayed'))
    state=mission.run(s,pid,mid,'operator',s.get(pid)['revision'])
    assert state['status']=='unknown_model_outcome' and len(state['model_requests'])==1


def test_cancel_before_start_and_during_model_call_prevents_tool_dispatch(ready,monkeypatch):
    configured(monkeypatch);s,pid,_,_=ready;mid=admit(s,pid,mode='model_research')
    def cancel(context):
        mission.stop(s,pid,mid,'operator',s.get(pid)['revision'],'合成测试操作人停止授权，不能派发后续工具')
        return response(context,'plan')
    calls=transport(monkeypatch,[cancel]);state=mission.run(s,pid,mid,'operator',s.get(pid)['revision'])
    assert state['status']=='cancelled' and not state['tool_requests'] and len(calls)==1
    rev=s.get(pid)['revision'];mission.run(s,pid,mid,'operator',rev);assert s.get(pid)['revision']==rev


def test_scope_change_and_controller_lock_prevent_duplicate_work(ready):
    s,pid,_,_=ready;mid=admit(s,pid)
    with mission.execution_lock(s,pid):
        with pytest.raises(Conflict):mission.run(s,pid,mid,'operator',s.get(pid)['revision'])
    from test_programs import add
    add(s,pid)
    assert mission.run(s,pid,mid,'operator',s.get(pid)['revision'])['status']=='scope_changed'


def test_backup_preserves_mission_and_read_only_restore_cannot_run(ready,tmp_path):
    s,pid,_,_=ready;mid=admit(s,pid);mission.run(s,pid,mid,'operator',s.get(pid)['revision'])
    snapshot(s.state,tmp_path/'snapshot','a'*40);restore(tmp_path/'snapshot',tmp_path/'restore')
    from vbt.programs import Programs
    restored=Programs(tmp_path/'restore');assert restored.export(pid)==s.export(pid)
    assert state_inventory(tmp_path/'restore')['programs'][0]['references_checked']>0
    with pytest.raises(ValueError,match='只读'):mission.run(restored,pid,mid,'operator',restored.get(pid)['revision'])


def test_interrupted_model_call_is_not_reissued_on_resume(ready,monkeypatch):
    configured(monkeypatch);s,pid,_,_=ready;mid=admit(s,pid,mode='model_research')
    def crash(*args):raise KeyboardInterrupt('synthetic model interruption')
    monkeypatch.setattr(model,'call_once',crash)
    with pytest.raises(KeyboardInterrupt):mission.run(s,pid,mid,'operator',s.get(pid)['revision'])
    monkeypatch.setattr(model,'call_once',lambda *a:pytest.fail('provider request cannot be replayed'))
    state=mission.run(s,pid,mid,'operator',s.get(pid)['revision'])
    assert state['status']=='unknown_model_outcome' and len(state['model_requests'])==1 and not state['model_receipts']


def test_interrupted_tool_is_recovered_with_kernel_lock_and_not_retried(ready,monkeypatch):
    from vbt import research_queue as queue
    s,pid,_,_=ready;mid=admit(s,pid)
    def crash(*args):raise KeyboardInterrupt('synthetic request persisted before interruption')
    monkeypatch.setattr(queue,'_worker',crash)
    with pytest.raises(KeyboardInterrupt):mission.run(s,pid,mid,'operator',s.get(pid)['revision'])
    monkeypatch.setattr(queue,'_worker',lambda *a:pytest.fail('interrupted attempt is not automatically retried'))
    state=mission.run(s,pid,mid,'operator',s.get(pid)['revision'])
    assert state['status']=='needs_input' and len(state['tool_requests'])==1
    assert state['tool_receipts'][0]['payload']['status']=='interrupted'


def test_cancel_between_recorded_request_and_child_start_does_not_launch(ready,monkeypatch):
    from vbt import research_queue as queue
    s,pid,_,_=ready;mid=admit(s,pid);original=queue._worker
    def worker(*args):
        mission.stop(s,pid,mid,'operator',s.get(pid)['revision'],'合成测试在记录请求后停止，后续子进程不得启动')
        return original(*args)
    monkeypatch.setattr(queue,'_worker',worker)
    monkeypatch.setattr(queue.subprocess,'Popen',lambda *a,**k:pytest.fail('cancelled request must not launch'))
    state=mission.run(s,pid,mid,'operator',s.get(pid)['revision'])
    assert state['status']=='cancelled' and len(state['tool_requests'])==1
    assert state['tool_receipts'][0]['payload']['error']=='cancelled_before_dispatch'


def test_insufficient_tool_budget_and_broken_sources_stop_without_false_delivery(ready):
    s,pid,_,source=ready;mid=admit(s,pid,max_tool_attempts=1)
    state=mission.run(s,pid,mid,'operator',s.get(pid)['revision'])
    assert state['status']=='budget_exhausted' and not state['tool_requests']
    mid=admit(s,pid,trigger_id='synthetic-request-broken')
    (source/'interaction_results.csv').write_bytes(b'changed synthetic data')
    state=mission.run(s,pid,mid,'operator',s.get(pid)['revision'])
    assert state['status']=='needs_input' and state['tool_receipts'][0]['payload']['status']!='succeeded'


def test_unknown_observation_reference_is_rejected_and_config_change_does_not_send(ready,monkeypatch):
    configured(monkeypatch);s,pid,_,_=ready
    def bad(context):
        result=response(context,'deliver');result['findings']=[dict(claim='合成来源不能支持这个未知观察编号的研究结论',references=['observation:unknown'])];return result
    calls=transport(monkeypatch,[bad]);mid=admit(s,pid,mode='model_research')
    state=mission.run(s,pid,mid,'operator',s.get(pid)['revision'])
    assert state['status']=='needs_input' and len(calls)==1 and state['model_receipts'][0]['payload']['error']=='mission_contract_invalid'
    mid=admit(s,pid,mode='model_research',trigger_id='synthetic-changed-config')
    monkeypatch.setenv('VBT_RESEARCH_MODEL','a-different-target')
    state=mission.run(s,pid,mid,'operator',s.get(pid)['revision'])
    assert state['status']=='needs_input' and not state['model_requests'] and len(calls)==1


def test_http_mission_admission_run_export_and_stale_revision(ready,tmp_path):
    import threading,urllib.request,urllib.error
    from urllib.parse import urlencode
    from http.server import ThreadingHTTPServer
    from vbt.workbench import Workbench,handler,page
    s,pid,_,_=ready;app=Workbench(tmp_path/'work',s.state)
    server=ThreadingHTTPServer(('127.0.0.1',0),handler(app));threading.Thread(target=server.serve_forever,daemon=True).start()
    base=f'http://127.0.0.1:{server.server_port}'
    def post(data,token=None):
        payload=dict(data,id=pid,revision=s.get(pid)['revision'],token=token or app.token,author='engineering operator')
        return urllib.request.urlopen(urllib.request.Request(base+'/program/action',data=urlencode(payload).encode(),headers={'Origin':base}))
    try:
        data=dict(operation='mission_create',mode='existing_evidence',trigger_id='http-fixture',goal='核对合成软件案例的来源和所报结果',criteria='保留成功、失败、来源与科学边界',max_model_calls=0,max_tool_attempts=4,max_seconds=60)
        with pytest.raises(urllib.error.HTTPError) as err:post(data,'bad-token')
        assert err.value.code==403
        assert post(data).status==200
        root=next(e for e in s.get(pid)['events'] if e['kind']=='research_mission')
        assert post(dict(operation='mission_run',mission_id=root['id'])).status==200
        state=json.load(urllib.request.urlopen(base+'/program/research-mission?id='+pid+'&mission='+root['id']))
        assert state['mission']['status']=='delivered_unreviewed'
        body=page(app,'/programs',{'id':pid})
        assert '持续研究任务' in body and '报告已交付，待科学审阅' in body and '未重新拟合' in body
        other=s.create(dict(s.get(pid)['events'][0]['payload']),'fixture')
        with pytest.raises(urllib.error.HTTPError):urllib.request.urlopen(base+'/program/research-mission?id='+other+'&mission='+root['id'])
    finally:server.shutdown();server.server_close()


def test_internal_provider_failure_has_renderable_receipt_and_no_retry(ready,monkeypatch,tmp_path):
    from vbt.workbench import Workbench,page
    configured(monkeypatch);s,pid,_,_=ready;mid=admit(s,pid,mode='model_research')
    def failure(*args):raise RuntimeError('SYNTHETIC_PRIVATE_TRANSPORT_TEXT_NOT_SAVED')
    monkeypatch.setattr(model,'call_once',failure)
    state=mission.run(s,pid,mid,'operator',s.get(pid)['revision'])
    assert state['status']=='needs_input' and len(state['model_requests'])==1
    assert state['model_receipts'][0]['payload']['returned_model'] is None
    body=page(Workbench(tmp_path/'work',s.state),'/programs',{'id':pid})
    assert '模型调用或草案校验失败' in body and 'SYNTHETIC_PRIVATE_TRANSPORT_TEXT_NOT_SAVED' not in body


def test_elapsed_mission_window_refuses_all_new_dispatch(ready,monkeypatch):
    from datetime import timedelta
    s,pid,_,_=ready;mid=admit(s,pid,max_seconds=10);real_datetime=mission.datetime
    class Later(real_datetime):
        @classmethod
        def now(cls,tz=None):return real_datetime.now(tz)+timedelta(seconds=20)
    monkeypatch.setattr(mission,'datetime',Later)
    state=mission.run(s,pid,mid,'operator',s.get(pid)['revision'])
    assert state['status']=='budget_exhausted' and not state['model_requests'] and not state['tool_requests']
