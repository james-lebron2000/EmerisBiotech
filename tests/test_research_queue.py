"""Actual persistence/worker fixtures. Synthetic data is not UC validation."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from urllib.parse import urlencode

import pytest
from test_ai_research import ready,record
from test_programs import PROFILE,add
from vbt.ai_research import packet,run_tool
from vbt.programs import Conflict
from vbt.research_queue import enqueue,batches,get_batch,run_batch,execute_next,cancel,retry,recover,export_batches
from vbt.workbench import Workbench,handler,page


def create(ready,tasks=None,deps=None,budget=None):
    store,pid,proposal,source=ready;rid=record(store,pid,proposal)
    bid=enqueue(store,pid,rid,tasks or ['audit','card'],deps if deps is not None else {'card':['audit']},
                budget or dict(max_attempts=4,max_seconds=60,per_task_seconds=15),'engineering operator',store.get(pid)['revision'])
    return store,pid,rid,bid,source


def test_real_fixed_workers_follow_dependencies_and_preserve_scientific_state(ready,monkeypatch):
    store,pid,rid,bid,source=create(ready);context=packet(store,pid)
    import vbt.research_queue as queue
    original=queue.subprocess.Popen;launches=[]
    monkeypatch.setenv('OPENAI_API_KEY','SYNTHETIC_SECRET_NOT_IN_WORKER')
    def launch(args,**kwargs):
        assert '-I' in args and '-B' in args and kwargs['pass_fds'] and kwargs['stdin']==subprocess.DEVNULL
        assert set(kwargs['env'])=={'PATH','LANG','PYTHONDONTWRITEBYTECODE'}
        assert 'SYNTHETIC_SECRET_NOT_IN_WORKER' not in str(kwargs['env'])
        p=original(args,**kwargs);launches.append(p);return p
    monkeypatch.setattr(queue.subprocess,'Popen',launch)
    receipts=run_batch(store,pid,bid,'engineering worker',store.get(pid)['revision'])
    assert len(launches)==2 and all(p.poll() is not None for p in launches)
    assert [r['tool'] for r in receipts]==['audit_bound_sources','extract_interaction_evidence']
    assert all(r['status']=='succeeded' and r['scientific_review']=='pending' for r in receipts)
    assert receipts[-1]['output']['rows'][0]['interaction_OR']==0.895
    batch=get_batch(store,pid,bid)
    assert batch['status']=='complete' and len(batch['attempts'])==2 and batch['charged_seconds']>0
    assert packet(store,pid)==context and not store.get(pid)['decisions'] and not store.get(pid)['experiments']
    before=len(store.events(pid));assert run_batch(store,pid,bid,'operator',store.get(pid)['revision'])==[]
    cancel(store,pid,bid,'operator',store.get(pid)['revision'],'No remaining tasks after complete batch')
    assert len(store.events(pid))==before


def test_duplicate_enqueue_and_conflicting_budget_cannot_allocate_again(ready):
    store,pid,rid,bid,source=create(ready);count=len(store.events(pid))
    assert enqueue(store,pid,rid,['audit','card'],{'card':['audit']},dict(max_attempts=4,max_seconds=60,per_task_seconds=15),'operator','old revision')==bid
    assert len(store.events(pid))==count
    with pytest.raises(ValueError,match='预算不可'):
        enqueue(store,pid,rid,['audit','card'],{'card':['audit']},dict(max_attempts=8,max_seconds=60,per_task_seconds=15),'operator',store.get(pid)['revision'])
    with pytest.raises(ValueError,match='其他批次'):
        enqueue(store,pid,rid,['audit'],{},dict(max_attempts=4,max_seconds=60,per_task_seconds=15),'operator',store.get(pid)['revision'])


def test_concurrent_enqueue_has_one_storage_allocation(ready):
    store,pid,proposal,source=ready;rid=record(store,pid,proposal);revision=store.get(pid)['revision'];barrier=threading.Barrier(2);results=[]
    def submit():
        barrier.wait()
        results.append(enqueue(store,pid,rid,['audit'],{},dict(max_attempts=2,max_seconds=60,per_task_seconds=15),'operator',revision))
    threads=[threading.Thread(target=submit) for _ in range(2)]
    for t in threads:t.start()
    for t in threads:t.join(timeout=10);assert not t.is_alive()
    assert len(results)==2 and len(set(results))==1 and len(batches(store,pid))==1


def test_two_workers_cannot_claim_the_same_task(ready):
    store,pid,rid,bid,source=create(ready,tasks=['audit'],deps={})
    barrier=threading.Barrier(2);results=[];failures=[]
    def work():
        barrier.wait()
        try:results.append(execute_next(store,pid,bid,'engineering worker'))
        except (Conflict,ValueError) as error:failures.append(type(error).__name__)
    threads=[threading.Thread(target=work) for _ in range(2)]
    for t in threads:t.start()
    for t in threads:t.join(timeout=10);assert not t.is_alive()
    assert len(results)==1 and len(failures)==1
    assert len(get_batch(store,pid,bid)['attempts'])==1


@pytest.mark.parametrize('damage',['missing','changed','oversized','invalid_csv'])
def test_source_failure_stops_batch_without_claiming_success(ready,damage):
    store,pid,rid,bid,root=create(ready);path=root/'interaction_results.csv'
    if damage=='missing':path.unlink()
    elif damage=='changed':path.write_bytes(b'changed')
    elif damage=='oversized':path.write_bytes(b'x'*(1024*1024+1))
    else:path.write_bytes(b'bad,csv\nno,data\n')
    receipts=run_batch(store,pid,bid,'worker',store.get(pid)['revision'])
    assert len(receipts)==1 and receipts[0]['status']=='blocked' and receipts[0]['output'] is None
    batch=get_batch(store,pid,bid)
    assert batch['status']=='requires_attention' and batch['tasks'][1]['state']=='waiting_dependency'


def test_attempt_cap_stops_before_next_task(ready):
    store,pid,rid,bid,source=create(ready,budget=dict(max_attempts=1,max_seconds=60,per_task_seconds=15))
    receipts=run_batch(store,pid,bid,'worker',store.get(pid)['revision'])
    assert len(receipts)==1 and receipts[0]['status']=='succeeded'
    b=get_batch(store,pid,bid)
    assert b['status']=='budget_exhausted' and b['tasks'][1]['state']=='queued'
    assert run_batch(store,pid,bid,'worker',store.get(pid)['revision'])==[]


def test_cli_no_progress_is_not_reported_as_completed(ready):
    store,pid,rid,bid,source=create(ready,budget=dict(max_attempts=1,max_seconds=60,per_task_seconds=15))
    run_batch(store,pid,bid,'worker',store.get(pid)['revision'])
    before=len(store.events(pid))
    script=Path(__file__).resolve().parent.parent/'scripts/research_batches.py'
    result=subprocess.run([sys.executable,str(script),'--state-dir',str(store.state),'--program',pid,
        'run','--batch',bid,'--author','engineering CLI operator'],capture_output=True,text=True,timeout=10)
    payload=json.loads(result.stdout)
    assert result.returncode==2 and payload['status']=='budget_exhausted' and payload['receipts']==[]
    assert len(store.events(pid))==before


def slow_child(monkeypatch):
    import vbt.research_queue as queue
    original=queue.subprocess.Popen;children=[];started=threading.Event()
    def spawn(args,**kwargs):
        p=original([sys.executable,'-I','-c','import time; time.sleep(10)'],**kwargs)
        children.append(p);started.set();return p
    monkeypatch.setattr(queue.subprocess,'Popen',spawn)
    return children,started


def test_real_timeout_kills_child_and_exhausts_execution_window(ready,monkeypatch):
    store,pid,rid,bid,source=create(ready,budget=dict(max_attempts=4,max_seconds=1,per_task_seconds=1))
    children,started=slow_child(monkeypatch)
    receipts=run_batch(store,pid,bid,'worker',store.get(pid)['revision'])
    assert len(receipts)==1 and receipts[0]['status']=='timed_out' and receipts[0]['output'] is None
    assert children[0].poll() is not None and receipts[0]['elapsed_seconds']>=1
    assert get_batch(store,pid,bid)['status']=='budget_exhausted'


def test_cancel_running_child_and_queued_work_are_distinct(ready,monkeypatch):
    store,pid,rid,bid,source=create(ready);children,started=slow_child(monkeypatch);results=[]
    thread=threading.Thread(target=lambda:results.extend(run_batch(store,pid,bid,'worker',store.get(pid)['revision'])))
    thread.start();assert started.wait(5)
    cancel(store,pid,bid,'engineering operator',store.get(pid)['revision'],'Stop this synthetic execution intentionally')
    thread.join(10);assert not thread.is_alive()
    assert children[0].poll() is not None and len(results)==1 and results[0]['status']=='cancelled'
    batch=get_batch(store,pid,bid);assert batch['status']=='cancelled'
    assert len(batch['tasks'][1]['attempts'])==0
    with pytest.raises(ValueError):retry(store,pid,bid,'audit','operator',store.get(pid)['revision'],'No automatic retry after operator cancellation')


def test_crash_recovery_charges_unknown_reservation_and_explicit_retry_has_new_attempt(ready,monkeypatch):
    store,pid,rid,bid,source=create(ready,tasks=['audit'],deps={})
    import vbt.research_queue as queue
    real=queue._worker
    def crash(*args):raise KeyboardInterrupt()
    monkeypatch.setattr(queue,'_worker',crash)
    with pytest.raises(KeyboardInterrupt):execute_next(store,pid,bid,'worker')
    before=get_batch(store,pid,bid)
    assert before['status']=='running_unknown' and before['attempts'][0]['receipt'] is None
    assert run_batch(store,pid,bid,'worker',store.get(pid)['revision'])==[]
    recovered=recover(store,pid,bid,'engineering operator',store.get(pid)['revision'],'Verified former controller no longer holds kernel lock')
    assert recovered[0]['status']=='interrupted' and recovered[0]['elapsed_seconds'] is None
    assert recovered[0]['budget_charge_seconds']==15 and recovered[0]['monetary_cost'] is None
    assert recovered[0]['recovery_reason']
    retry(store,pid,bid,'audit','operator',store.get(pid)['revision'],'Inputs verified; approve one new read-only attempt')
    monkeypatch.setattr(queue,'_worker',real)
    receipts=run_batch(store,pid,bid,'worker',store.get(pid)['revision'])
    assert receipts[0]['status']=='succeeded' and receipts[0]['request_id']!=recovered[0]['request_id']
    b=get_batch(store,pid,bid);assert len(b['attempts'])==2 and b['charged_seconds']>15


def test_orphan_child_keeps_kernel_lock_and_prevents_false_recovery(ready,monkeypatch):
    store,pid,rid,bid,source=create(ready,tasks=['audit'],deps={})
    import vbt.research_queue as queue
    children=[]
    def orphan(store,pid,request,fd,seconds):
        children.append(subprocess.Popen([sys.executable,'-I','-c','import time; time.sleep(10)'],
            env={'PATH':'/usr/bin:/bin'},pass_fds=(fd,),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL))
        raise KeyboardInterrupt()
    monkeypatch.setattr(queue,'_worker',orphan)
    try:
        with pytest.raises(KeyboardInterrupt):execute_next(store,pid,bid,'worker')
        assert children[0].poll() is None
        with pytest.raises(Conflict,match='内核锁'):
            recover(store,pid,bid,'operator',store.get(pid)['revision'],'Cannot infer child termination from missing parent')
        assert get_batch(store,pid,bid)['attempts'][0]['receipt'] is None
    finally:
        for child in children:child.kill();child.wait(timeout=5)
    assert recover(store,pid,bid,'operator',store.get(pid)['revision'],'Child is now confirmed terminated and lock is released')[0]['status']=='interrupted'


def test_lock_replacement_does_not_prove_old_execution_terminal(ready,monkeypatch):
    store,pid,rid,bid,source=create(ready,tasks=['audit'],deps={})
    import vbt.research_queue as queue
    monkeypatch.setattr(queue,'_worker',lambda *args:(_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):execute_next(store,pid,bid,'worker')
    for path in (store.state/'research_execution').iterdir():path.unlink()
    with pytest.raises(ValueError,match='锁文件版本'):
        recover(store,pid,bid,'operator',store.get(pid)['revision'],'Replaced lock is not proof of prior execution exit')
    assert get_batch(store,pid,bid)['attempts'][0]['receipt'] is None


def test_context_change_after_worker_cannot_qualify_output(ready,monkeypatch):
    store,pid,rid,bid,source=create(ready,tasks=['audit'],deps={})
    def changed(*args):
        add(store,pid)
        return 'succeeded',None,{'engineering':'not scientific'},.1,.1
    monkeypatch.setattr('vbt.research_queue._worker',changed)
    receipt=execute_next(store,pid,bid,'worker')
    assert receipt['status']=='stale' and receipt['output'] is None


def test_cancel_race_before_claim_cannot_launch_child(ready,monkeypatch):
    store,pid,rid,bid,source=create(ready,tasks=['audit'],deps={})
    original=store._append;cancelled=[]
    def append(pid,kind,payload,author,revision):
        if kind=='ai_tool_request' and not cancelled:
            cancelled.append(True)
            cancel(store,pid,bid,'engineering operator',store.get(pid)['revision'],'Cancel arrived before dispatch transaction committed')
        return original(pid,kind,payload,author,revision)
    monkeypatch.setattr(store,'_append',append)
    launches=[];monkeypatch.setattr('vbt.research_queue._worker',lambda *args:launches.append(True))
    with pytest.raises(Conflict):execute_next(store,pid,bid,'worker')
    assert not launches and get_batch(store,pid,bid)['status']=='cancelled' and not get_batch(store,pid,bid)['attempts']


def test_explicit_retry_requires_live_kernel_owner_to_release(ready,monkeypatch):
    store,pid,rid,bid,source=create(ready,tasks=['audit'],deps={})
    import vbt.research_queue as queue
    monkeypatch.setattr(queue,'_worker',lambda store,pid,request,fd,seconds:('interrupted','worker_termination_unconfirmed',None,None,seconds))
    execute_next(store,pid,bid,'worker')
    with queue.execution_lock(store,pid) as (fd,_):
        child=subprocess.Popen([sys.executable,'-I','-c','import time; time.sleep(10)'],
            env={'PATH':'/usr/bin:/bin'},pass_fds=(fd,),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        with pytest.raises(Conflict):retry(store,pid,bid,'audit','operator',store.get(pid)['revision'],'Do not retry while previous kernel owner remains live')
    finally:child.kill();child.wait(timeout=5)
    retry(store,pid,bid,'audit','operator',store.get(pid)['revision'],'Kernel owner is confirmed gone; allow one new attempt')
    assert get_batch(store,pid,bid)['tasks'][0]['state']=='queued'


@pytest.mark.parametrize('case',['cycle','foreign_dep','unknown_task','manual','bad_budget','extra_budget'])
def test_invalid_batch_contracts_never_queue(ready,case):
    store,pid,proposal,root=ready
    if case=='manual':proposal['tasks'][0]['tool']='manual_research'
    rid=record(store,pid,proposal);tasks=['audit','card'];deps={'card':['audit']};budget=dict(max_attempts=4,max_seconds=60,per_task_seconds=15)
    if case=='cycle':deps={'audit':['card'],'card':['audit']}
    elif case=='foreign_dep':deps={'card':['foreign']}
    elif case=='unknown_task':tasks=['foreign']
    elif case=='bad_budget':budget['max_seconds']='NaN'
    elif case=='extra_budget':budget['command']='not authorized'
    with pytest.raises(ValueError):enqueue(store,pid,rid,tasks,deps,budget,'operator',store.get(pid)['revision'])
    assert batches(store,pid)==[]


def test_legacy_button_uses_queue_and_repeated_click_does_not_execute_twice(ready):
    store,pid,proposal,source=ready;rid=record(store,pid,proposal)
    first=run_tool(store,pid,rid,'audit','operator',store.get(pid)['revision'])
    second=run_tool(store,pid,rid,'audit','operator',store.get(pid)['revision'])
    assert second['request_id']==first['request_id'] and second['reused_receipt']
    assert len(batches(store,pid))==1 and len(get_batch(store,pid,first['batch_id'])['attempts'])==1


def test_http_batch_flow_csrf_and_export_use_persistent_worker(ready,tmp_path):
    store,pid,proposal,source=ready;rid=record(store,pid,proposal);app=Workbench(tmp_path/'workspace',store.state)
    server=ThreadingHTTPServer(('127.0.0.1',0),handler(app));threading.Thread(target=server.serve_forever,daemon=True).start();base=f'http://127.0.0.1:{server.server_port}'
    def post(data,token=None):
        data=dict(data,token=app.token if token is None else token,id=pid,revision=store.get(pid)['revision'],author='engineering HTTP operator')
        return urllib.request.urlopen(urllib.request.Request(base+'/program/action',data=urlencode(data,doseq=True).encode(),headers={'Origin':base})).read().decode()
    try:
        data=dict(operation='batch_enqueue',proposal_id=rid,task_ids=['audit','card'],dependencies_json='{"card":["audit"]}',max_attempts='4',max_seconds='60',per_task_seconds='15')
        with pytest.raises(urllib.error.HTTPError) as exc:post(data,token='bad')
        assert exc.value.code==403 and not batches(store,pid)
        assert '研究任务批次与预算' in post(data)
        bid=batches(store,pid)[-1]['batch_id']
        assert '全部工具已完成' in post(dict(operation='batch_run',batch_id=bid))
        response=urllib.request.urlopen(base+'/program/task-batches?id='+pid)
        assert 'sandbox;' in response.headers['Content-Security-Policy']
        export=json.load(response);assert len(export['batches'][0]['attempts'])==2 and export['scientific_review']=='not_inferred'
        assert len(json.loads(export_batches(store,pid))['batches'])==1
    finally:server.shutdown();server.server_close()
