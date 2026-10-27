"""Local batch budgets, explicit execution/recovery and complete attempt history."""
import json
from urllib.parse import quote
from .ai_research import TOOLS,proposals
from .research_queue import batches

STATES={'queued':'排队待执行','waiting_dependency':'等待前置任务','running_unknown':'有请求，尚无完成回执',
        'succeeded':'执行完成，待科学审阅','blocked':'受阻，需核查','timed_out':'执行超时','interrupted':'中断，原结果未知',
        'cancelled':'已取消','stale':'上下文已变化','complete':'全部工具已完成','budget_exhausted':'预算已耗尽',
        'ready':'可按已录预算执行','requires_attention':'需处理失败或依赖'}


def render(app,store,p):
    from .program_view import e,field,form
    records=batches(store,p['id'])
    body='<section class="section-head" id="research-batches"><h2>研究任务批次与预算</h2><span>先记录范围与预算，再运行主机只读工具</span></section><p class="data-note">批次固定任务、依赖、尝试次数与执行窗口。失败后暂停；恢复不会补造成功。重试保留独立记录并继续使用原预算。本地计算费用未计量，金额保持未知；模型调用与湿实验另行安排。</p>'
    body+='<p><a href="/program/task-batches?id='+quote(p['id'])+'">导出批次、请求与回执 JSON</a></p>'
    for b in reversed(records):
        body+='<section class="card"><h3>'+e(STATES[b['status']])+'</h3><p>任务 '+str(len(b['tasks']))+' 项；已派发 '+str(len(b['attempts']))+'/'+str(b['limits']['max_attempts'])+' 次；已计入预算 '+format(b['charged_seconds'],'.3f')+' 秒；剩余窗口 '+format(b['remaining_seconds'],'.3f')+' 秒。</p><p class="ref">批次 '+e(b['batch_id'])+' · '+e(b['created'])+' · '+e(b['author'])+'</p>'
        for job in b['tasks']:
            body+='<h4>'+e(job['question'])+'</h4><p>'+e(STATES[job['state']])+'；依赖 '+e(', '.join(b['dependencies'][job['id']]) or '无')+'</p>'
            for attempt in job['attempts']:
                r=attempt['receipt']
                body+='<p class="ref">请求 '+e(attempt['request_id'])+' · '+e(attempt['created'])+'；'+('尚无完成回执，不能判定成功' if r is None else e(STATES[r['status']])+'；实测耗时 '+e(r['elapsed_seconds'] if r['elapsed_seconds'] is not None else '未知')+' 秒')+'</p>'
            if job['state'] in ('blocked','timed_out','interrupted') and not b['stale'] and not b['cancelled'] and b['remaining_attempts'] and b['remaining_seconds']>=.01:
                body+=action(app,p,b,'batch_retry','显式重试此任务',job['id'])
        if b['status']=='ready':
            body+=action(app,p,b,'batch_run','按所录预算运行批次')
        if not b['cancelled'] and b['status']!='complete':
            body+=action(app,p,b,'batch_cancel','取消批次后续执行')
        if any(a['receipt'] is None for a in b['attempts']):
            body+=action(app,p,b,'batch_recover','核查旧执行并登记中断状态')
        body+='<details><summary>查看预算与完整尝试记录</summary><pre>'+e(json.dumps(b,ensure_ascii=False,indent=2))+'</pre></details></section>'
    for draft in proposals(store,p['id']):
        if draft['stale']:
            continue
        tasks=[t for t in draft['proposal']['tasks'] if t['tool'] in TOOLS]
        assigned={t['task_key'] for b in records for t in b['tasks']}
        from .research_queue import key
        tasks=[t for t in tasks if key([p['id'],draft['proposal']['packet_hash'],draft['event_id'],t['id'],t['tool']]) not in assigned]
        if not tasks:
            continue
        audit=[t['id'] for t in tasks if t['tool']=='audit_bound_sources']
        deps={t['id']:audit for t in tasks if t['tool']=='extract_interaction_evidence' and audit}
        body+='<details class="card"><summary>为当前草案建立工具批次</summary>'+form(app,p,'batch_enqueue','确定任务与资源上限')+'<input type="hidden" name="proposal_id" value="'+e(draft['event_id'])+'"><fieldset><legend>只运行勾选的主机工具任务</legend>'
        for t in tasks:
            body+='<label><input type="checkbox" name="task_ids" value="'+e(t['id'])+'" checked> '+e(TOOLS[t['tool']])+' · '+e(t['question'])+'</label>'
        body+='</fieldset>'+field('dependencies_json','前置依赖 JSON（仅限所选任务，可核对或修改）',value=json.dumps(deps,ensure_ascii=False))
        for name,label,value in [('max_attempts','最多派发次数（含重试，1–32）',str(min(32,len(tasks)*2))),('max_seconds','总执行窗口（秒，1–600）','60'),('per_task_seconds','单次工具时限（秒，1–120）','15')]:
            body+=field(name,label,'number',value=value)
        body+=field('author','预算与任务记录人（本地声明）','text')+'<p><button>保存批次与预算</button></p></form></details>'
    return body


def action(app,p,b,operation,title,task_id=None):
    from .program_view import e,field,form
    body=form(app,p,operation,title)+'<input type="hidden" name="batch_id" value="'+e(b['batch_id'])+'">'
    if task_id:
        body+='<input type="hidden" name="task_id" value="'+e(task_id)+'">'
    if operation in ('batch_retry','batch_recover','batch_cancel'):
        body+=field('reason','本次操作依据（至少 10 字符）')
    return body+field('author','操作记录人（本地声明）','text')+'<p><button>'+title+'</button></p></form>'
