"""Append-only local tool batches; fixed workers, explicit retry, conservative recovery."""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import subprocess
import sys
import time
import uuid

from .programs import Conflict, canonical, text
from .ai_research import TOOLS, packet, proposals

DEFAULT_LIMITS = dict(max_attempts=4, max_seconds=60, per_task_seconds=15)


def key(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def limits(value):
    if not isinstance(value, dict) or set(value) != set(DEFAULT_LIMITS):
        raise ValueError('批次预算字段不符合契约')
    result = {}
    for name, maximum in [('max_attempts',32), ('max_seconds',600), ('per_task_seconds',120)]:
        raw = value[name]
        if isinstance(raw, bool):
            raise ValueError('批次预算必须是有效数值')
        try:
            number = float(raw)
        except (TypeError,ValueError):
            raise ValueError('批次预算必须是有效数值') from None
        if not math.isfinite(number) or not 1 <= number <= maximum or (name=='max_attempts' and not number.is_integer()):
            raise ValueError('批次预算超出主机范围')
        result[name] = int(number) if name=='max_attempts' else number
    return result


def dependencies(value, ids):
    if not isinstance(value, dict) or not set(value) <= set(ids):
        raise ValueError('依赖只能引用本批次任务')
    clean = {}
    for tid in ids:
        parents = value.get(tid, [])
        if (not isinstance(parents,list) or any(not isinstance(p,str) for p in parents)
                or len(parents)>8 or not set(parents)<=set(ids) or tid in parents):
            raise ValueError('依赖只能引用其他本批次任务')
        clean[tid] = sorted(set(parents))
    def visit(tid, path):
        if tid in path:
            raise ValueError('任务依赖存在循环')
        for parent in clean[tid]:
            visit(parent, path | {tid})
    for tid in ids:
        visit(tid,set())
    return clean


def batches(store, pid):
    events = store.events(pid); current_hash = packet(store,pid)['packet_hash']
    result = []
    for event in events:
        if event['kind'] != 'ai_batch':
            continue
        b = dict(event['payload'], event_id=event['id'], created=event['created'], author=event['author'], revision=events[-1]['digest'])
        b['cancelled'] = any(e['kind']=='ai_batch_cancel' and e['payload']['batch_id']==b['batch_id'] for e in events)
        b['stale'] = b['packet_hash'] != current_hash
        requests = [e for e in events if e['kind']=='ai_tool_request' and e['payload'].get('batch_id')==b['batch_id']]
        receipts = {e['payload']['request_id']:e for e in events if e['kind']=='ai_tool_receipt'}
        b['attempts'] = []
        for request in requests:
            receipt = receipts.get(request['id'])
            b['attempts'].append(dict(request_id=request['id'],request=request['payload'],created=request['created'],
                receipt=receipt['payload'] if receipt else None,receipt_event_id=receipt['id'] if receipt else None))
        b['charged_seconds'] = sum(a['receipt']['budget_charge_seconds'] if a['receipt'] else a['request']['reserved_seconds'] for a in b['attempts'])
        # A batch's execution window starts at the first dispatch, not at enqueue.
        wall_remaining = b['limits']['max_seconds']
        if requests:
            age = (datetime.now(timezone.utc)-datetime.fromisoformat(requests[0]['created'])).total_seconds()
            wall_remaining -= max(0,age)
        b['remaining_seconds'] = max(0,min(b['limits']['max_seconds']-b['charged_seconds'],wall_remaining))
        b['remaining_attempts'] = max(0,b['limits']['max_attempts']-len(requests))
        jobs = []
        for task in b['tasks']:
            attempts = [a for a in b['attempts'] if a['request']['task_id']==task['id']]
            latest = attempts[-1] if attempts else None
            state = latest['receipt']['status'] if latest and latest['receipt'] else 'running_unknown' if latest else 'queued'
            retry = next((e for e in reversed(events) if e['kind']=='ai_task_retry' and e['payload']['batch_id']==b['batch_id'] and e['payload']['task_id']==task['id']),None)
            if latest and latest['receipt'] and retry and retry['payload']['previous_request_id']==latest['request_id']:
                state = 'queued'
            jobs.append(dict(task,state=state,attempts=attempts))
        states = {j['id']:j['state'] for j in jobs}
        for job in jobs:
            if job['state']=='queued' and any(states[parent]!='succeeded' for parent in b['dependencies'][job['id']]):
                job['state']='waiting_dependency'
        b['tasks']=jobs
        b['status'] = ('cancelled' if b['cancelled'] else 'stale' if b['stale'] else
                       'complete' if all(j['state']=='succeeded' for j in jobs) else
                       'running_unknown' if any(a['receipt'] is None for a in b['attempts']) else
                       'budget_exhausted' if b['remaining_attempts']==0 or b['remaining_seconds']<.01 else
                       'requires_attention' if any(j['state'] in ('blocked','timed_out','interrupted','cancelled','stale') for j in jobs) else
                       'ready' if any(j['state']=='queued' for j in jobs) else 'requires_attention')
        result.append(b)
    return result


def get_batch(store,pid,batch_id):
    found=next((b for b in batches(store,pid) if b['batch_id']==batch_id),None)
    if found is None:
        raise ValueError('任务批次不属于本项目')
    return found


def enqueue(store,pid,proposal_id,task_ids,deps,budget,author,revision):
    p=store.get(pid)
    proposal=next((d for d in proposals(store,pid) if d['event_id']==proposal_id),None)
    if not proposal or proposal['stale']:
        raise ValueError('草案不存在或已过期')
    if not isinstance(task_ids,list) or not 1<=len(task_ids)<=8 or any(not isinstance(t,str) for t in task_ids) or len(set(task_ids))!=len(task_ids):
        raise ValueError('选择 1–8 个不重复的主机工具任务')
    known={t['id']:t for t in proposal['proposal']['tasks']}
    if not set(task_ids)<=set(known) or any(known[t]['tool'] not in TOOLS for t in task_ids):
        raise ValueError('任务需人工研究，或未获主机只读工具权限')
    clean_limits=limits(budget); clean_deps=dependencies(deps,task_ids)
    identity=dict(program_id=pid,proposal_id=proposal_id,packet_hash=proposal['proposal']['packet_hash'],
                  task_ids=sorted(task_ids),dependencies=clean_deps)
    batch_id=key(identity)
    tasks=[dict(known[t],task_key=key([pid,identity['packet_hash'],proposal_id,t,known[t]['tool']])) for t in task_ids]
    def existing():
        prior=batches(store,pid)
        same=next((b for b in prior if b['batch_id']==batch_id),None)
        if same:
            if same['limits']!=clean_limits:
                raise ValueError('同一批次预算不可静默变更')
            return same['batch_id']
        assigned={t['task_key'] for b in prior for t in b['tasks']}
        if any(t['task_key'] in assigned for t in tasks):
            raise ValueError('任务已属于其他批次，请使用已有批次或显式重试')
        return None
    found=existing()
    if found:
        return found
    if p['revision']!=revision:
        raise Conflict('项目已有更新，请刷新后创建批次')
    payload=dict(schema_version=1,**identity,batch_id=batch_id,tasks=tasks,limits=clean_limits,
                 max_tasks=len(tasks),monetary_cost=None,scope='host_defined_read_only_tools_no_model_or_arbitrary_code')
    try:
        store._append(pid,'ai_batch',payload,author,revision)
    except Conflict:
        # A concurrent identical enqueue is read back; no second allocation.
        found=existing()
        if found and packet(store,pid)['packet_hash']==identity['packet_hash']:
            return found
        raise
    return batch_id


@contextmanager
def execution_lock(store,pid):
    root=store.state/'research_execution'
    if root.is_symlink():
        raise ValueError('执行锁目录不能是符号链接')
    root.mkdir(exist_ok=True)
    path=root/(key(pid)+'.lock')
    fd=os.open(path,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError('执行锁不是普通文件')
        try:
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            raise Conflict('本项目有执行进程仍占用内核锁，不能重复派发或推定已中断') from None
        token=os.read(fd,128)
        if not token:
            token=uuid.uuid4().hex.encode();os.write(fd,token);os.fsync(fd)
        info=os.fstat(fd)
        yield fd,key([info.st_dev,info.st_ino,token.hex()])
    finally:
        # Do not explicitly unlock: an orphan child inherits this open-file
        # description and must retain the lock until it really exits.
        os.close(fd)


def cancel(store,pid,batch_id,author,revision,reason):
    batch=get_batch(store,pid,batch_id)
    if batch['cancelled'] or all(t['state']=='succeeded' for t in batch['tasks']):
        return
    store._append(pid,'ai_batch_cancel',dict(batch_id=batch_id,reason=text(reason,'取消依据',10)),author,revision)


def retry(store,pid,batch_id,task_id,author,revision,reason):
    with execution_lock(store,pid) as (_,identity):
        return _retry_locked(store,pid,batch_id,task_id,author,revision,reason,identity)


def _retry_locked(store,pid,batch_id,task_id,author,revision,reason,identity):
    batch=get_batch(store,pid,batch_id)
    if batch['stale'] or batch['cancelled']:
        raise ValueError('过期或已取消批次不能重新派发')
    job=next((t for t in batch['tasks'] if t['id']==task_id),None)
    if not job or job['state'] not in ('blocked','timed_out','interrupted'):
        raise ValueError('只有已终止且未成功的任务可显式重试；未知请求需先恢复核查')
    if batch['remaining_attempts']==0 or batch['remaining_seconds']<.01:
        raise ValueError('原批次预算已耗尽，不会因重试重置')
    if job['attempts'][-1]['request']['lock_identity']!=identity:
        raise ValueError('执行锁版本变化，不能核实旧执行已结束')
    store._append(pid,'ai_task_retry',dict(batch_id=batch_id,task_id=task_id,
        previous_request_id=job['attempts'][-1]['request_id'],verified_lock_identity=identity,reason=text(reason,'重试依据',10)),author,revision)


def _save_receipt(store,pid,request,author,status,error,output,elapsed,charge,recovery_reason=None):
    for _ in range(10):
        p=store.get(pid)
        if any(e['kind']=='ai_tool_receipt' and e['payload']['request_id']==request['id'] for e in p['events']):
            raise Conflict('请求已有回执，不可重复完成')
        batch=get_batch(store,pid,request['payload']['batch_id'])
        final_status='cancelled' if batch['cancelled'] else 'stale' if batch['stale'] else status
        receipt=dict(request_id=request['id'],proposal_id=request['payload']['proposal_id'],task_id=request['payload']['task_id'],
            tool=request['payload']['tool'],packet_hash=request['payload']['packet_hash'],batch_id=batch['batch_id'],
            task_key=request['payload']['task_key'],status=final_status,error=error,
            output=output if final_status=='succeeded' else None,elapsed_seconds=elapsed,budget_charge_seconds=charge,
            monetary_cost=None,recovery_reason=recovery_reason,scientific_review='pending',execution_scope='fixed_read_only_worker_credential_free_no_model_or_shell')
        try:
            store._append(pid,'ai_tool_receipt',receipt,author,p['revision'])
            return receipt
        except Conflict:
            continue
    raise Conflict('并发更新阻止保存回执；保留未完成请求，不重跑工具')


def recover(store,pid,batch_id,author,revision,reason):
    text(reason,'恢复核查依据',10)
    if store.get(pid)['revision']!=revision:
        raise Conflict('项目已有更新，请刷新后核查')
    with execution_lock(store,pid) as (_,identity):
        batch=get_batch(store,pid,batch_id);recovered=[]
        pending=[a for a in batch['attempts'] if a['receipt'] is None]
        if any(a['request']['lock_identity']!=identity for a in pending):
            raise ValueError('执行锁文件版本不一致，不能据此判断旧进程终止')
        for attempt in pending:
            request=next(e for e in store.events(pid) if e['id']==attempt['request_id'])
            r=_save_receipt(store,pid,request,author,'interrupted','completion_unknown_after_owner_exit',None,None,request['payload']['reserved_seconds'],reason)
            recovered.append(r)
        return recovered


def _worker(store,pid,request,lock_fd,seconds):
    script=Path(__file__).with_name('research_worker.py').resolve()
    env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','PYTHONDONTWRITEBYTECODE':'1'}
    args=[sys.executable,'-I','-B',str(script),'--state-dir',str(store.state),'--program',pid,
          '--tool',request['payload']['tool'],'--packet-hash',request['payload']['packet_hash'],'--seconds',str(seconds)]
    started=time.monotonic();process=None;state='blocked';error='worker_start_failed';output=None
    try:
        process=subprocess.Popen(args,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                                 env=env,pass_fds=(lock_fd,),start_new_session=True)
        while True:
            cancelled=any(e['kind']=='ai_batch_cancel' and e['payload']['batch_id']==request['payload']['batch_id'] for e in store.events(pid))
            remaining=seconds-(time.monotonic()-started)
            if cancelled or remaining<=0:
                state='cancelled' if cancelled else 'timed_out'
                error='cancelled_or_context_changed' if state!='timed_out' else 'execution_deadline'
                process.kill();process.communicate(timeout=2)
                break
            try:
                raw,_=process.communicate(timeout=min(.1,remaining))
            except subprocess.TimeoutExpired:
                continue
            if len(raw)>512*1024:
                error='worker_output_too_large';break
            try:
                from .research_model import strict_json
                data=strict_json(raw)
                if (not isinstance(data,dict) or set(data)!={'status','error','output'}
                        or data['status'] not in ('succeeded','blocked','stale')
                        or data['error'] not in (None,'source_invalid','context_changed','worker_deadline','tool_output_too_large','worker_internal')
                        or (data['status']=='succeeded' and (process.returncode!=0 or not isinstance(data['output'],dict)))):
                    raise ValueError('invalid_worker_output')
                state,error,output=data['status'],data['error'],data['output']
                if error=='worker_deadline':
                    state='timed_out'
            except (ValueError,TypeError,KeyError,UnicodeError,RecursionError):
                error='worker_output_invalid'
            break
    except (OSError,subprocess.SubprocessError):
        error='worker_transport_failed'
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            try:
                process.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                state='interrupted';error='worker_termination_unconfirmed';output=None
    if state=='interrupted':
        return state,error,None,None,seconds
    elapsed=round(time.monotonic()-started,6)
    return state,error,output,elapsed,elapsed


def execute_next(store,pid,batch_id,author,revision=None,task_id=None):
    if revision is not None and store.get(pid)['revision']!=revision:
        raise Conflict('项目已有更新，请刷新后执行')
    with execution_lock(store,pid) as (fd,identity):
        events=store.events(pid)
        completed={e['payload']['request_id'] for e in events if e['kind']=='ai_tool_receipt'}
        if any(e['kind']=='ai_tool_request' and e['id'] not in completed for e in events):
            raise ValueError('存在未完成工具请求，需先核查与显式恢复，不能派发其他批次')
        unknown_owners={e['payload']['request_id'] for e in events if e['kind']=='ai_tool_receipt' and e['payload'].get('error')=='worker_termination_unconfirmed'}
        if any(e['kind']=='ai_tool_request' and e['id'] in unknown_owners and e['payload'].get('lock_identity')!=identity for e in events):
            raise ValueError('执行锁版本变化，无法核实曾未确认终止的子进程')
        batch=get_batch(store,pid,batch_id)
        if batch['status']!='ready':
            raise ValueError('批次未就绪：'+batch['status'])
        job=next((t for t in batch['tasks'] if t['state']=='queued' and (task_id is None or t['id']==task_id)),None)
        if not job:
            raise ValueError('指定任务尚未就绪，需先满足依赖或显式重试')
        reserved=min(batch['remaining_seconds'],batch['limits']['per_task_seconds'])
        store._append(pid,'ai_tool_request',dict(schema_version=2,batch_id=batch_id,task_key=job['task_key'],
            proposal_id=batch['proposal_id'],task_id=job['id'],tool=job['tool'],packet_hash=batch['packet_hash'],
            reserved_seconds=reserved,lock_identity=identity),author,batch['revision'])
        request=next(e for e in store.events(pid) if e['previous']==batch['revision'])
        status,error,output,elapsed,charge=_worker(store,pid,request,fd,reserved)
        return _save_receipt(store,pid,request,author,status,error,output,elapsed,charge)


def single_tool(store,pid,proposal_id,task_id,author,revision):
    if store.get(pid)['revision']!=revision:
        raise Conflict('项目已有更新，请刷新后执行')
    draft=next((d for d in proposals(store,pid) if d['event_id']==proposal_id),None)
    if not draft or draft['stale']:
        raise ValueError('草案不存在或已过期')
    task=next((t for t in draft['proposal']['tasks'] if t['id']==task_id),None)
    if not task or task['tool'] not in TOOLS:
        raise ValueError('该任务需人工研究，未获主机工具权限')
    batch=next((b for b in batches(store,pid) if b['proposal_id']==proposal_id and any(t['id']==task_id for t in b['tasks'])),None)
    if not batch:
        bid=enqueue(store,pid,proposal_id,[task_id],{},dict(max_attempts=2,max_seconds=60,per_task_seconds=30),author,revision)
        batch=get_batch(store,pid,bid)
    job=next(t for t in batch['tasks'] if t['id']==task_id)
    if job['attempts'] and job['state']!='queued':
        # Historical receipt returned as history, never a new tool launch.
        if job['attempts'][-1]['receipt'] is not None:
            return dict(job['attempts'][-1]['receipt'],reused_receipt=True,source_check='historical_not_reexecuted')
        raise ValueError('请求尚无完成回执，需核查后显式恢复')
    return execute_next(store,pid,batch['batch_id'],author,task_id=task_id)


def run_batch(store,pid,batch_id,author,revision):
    if store.get(pid)['revision']!=revision:
        raise Conflict('项目已有更新，请刷新后运行批次')
    receipts=[]
    for _ in range(32):
        if get_batch(store,pid,batch_id)['status']!='ready':
            break
        receipt=execute_next(store,pid,batch_id,author)
        receipts.append(receipt)
        if receipt['status']!='succeeded':
            break
    return receipts


def export_batches(store,pid):
    return canonical(dict(schema_version=1,program_id=pid,batches=batches(store,pid),
                          scientific_review='not_inferred',cost='local_compute_unmetered_not_reported_as_zero')).encode()
