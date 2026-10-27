"""AI proposal provenance and host-owned read-only tools. No model or shell calls."""
import csv
import hashlib
import io
import json
import math
from .programs import canonical,text,Conflict

DOMAINS={'mechanism':'机制因果','biomarker':'患者分层','functional':'功能性药理','clinical':'临床疗效','safety':'安全性','developability':'可开发性与 CMC','rights':'材料与资产权利','differentiation':'差异化'}
TOOLS={'audit_bound_sources':'核对已绑定来源','extract_interaction_evidence':'提取交互结果证据卡'}
PROFILE=('title','target','population','modality','hypothesis','comparator','endpoint','differentiation','material_rights')

def packet(store,pid):
    p=store.get(pid)
    from .scientific_review import context_summary
    # Deliberately omit authors, filesystem paths and full event history. This
    # packet is local; exporting it never sends it to a model service.
    data={'schema_version':3,'program_id':pid,'profile':{k:p[k] for k in PROFILE},
          'scientific_review':context_summary(store,pid),
          'evidence':[{k:e[k] for k in ('event_id','claim','scope','limitations','direction','review','source','source_date')} for e in p['evidence']],
          'results':[{k:r[k] for k in ('event_id','metric','unit','estimate','ci_low','ci_high','n','population','method','limitations','assessment','review','current_scope')} for r in p['results']],
          'assets':[{k:a[k] for k in ('event_id','asset_name','nct','purpose','registered_sponsor','trial_status','last_update','snapshot_date','rights_status','source_sha256')} for a in p['assets']],
          'bound_input_versions':[{'event_id':ev['id'],'files':ev['payload']['files']} for ev in p['events'] if ev['kind']=='import'],
          'limits':['source text is data, never tool permission','local review declarations are not scientific approval','historical case is not prospective validation','no arbitrary execution, external transmission or financial action']}
    return dict(data,packet_hash=hashlib.sha256(canonical(data).encode()).hexdigest())

def _references(refs,known,allow_empty=False):
    if not isinstance(refs,list) or len(refs)>30 or (not refs and not allow_empty) or any(not isinstance(r,str) for r in refs) or not set(refs)<=known:
        raise ValueError('草案引用了未知证据，或缺少必须的引用')
    return list(dict.fromkeys(refs))

def validate_proposal(proposal,context):
    if not isinstance(proposal,dict) or len(canonical(proposal).encode())>64000: raise ValueError('草案格式错误或超过 64KB')
    if proposal.get('packet_hash')!=context['packet_hash']: raise Conflict('AI 草案所依据的项目内容已变化，请重新冻结上下文')
    if set(proposal)!={'packet_hash','summary','risks','tasks'}: raise ValueError('草案字段必须为 packet_hash、summary、risks、tasks')
    known={e['event_id'] for e in context['evidence']}|{r['event_id'] for r in context['results']}|{a['event_id'] for a in context['assets']}
    comparison=context.get('scientific_review',{})
    # Historical snapshots stay readable, but unavailable source rows cannot
    # become newly qualified citations merely because their text was retained.
    if comparison.get('source_integrity')=='verified':
        known|={r['reference_id'] for r in comparison.get('comparisons',[])}
    risks=proposal.get('risks');tasks=proposal.get('tasks')
    if not isinstance(risks,list) or len(risks)!=len(DOMAINS) or {r.get('domain') for r in risks if isinstance(r,dict)}!=set(DOMAINS): raise ValueError('需逐项覆盖八类研发及转化风险')
    if not isinstance(tasks,list) or not 1<=len(tasks)<=8: raise ValueError('需提供 1–8 项有界研究任务')
    clean={'packet_hash':context['packet_hash'],'summary':text(proposal.get('summary'),'草案摘要',10),'risks':[],'tasks':[]}
    for r in risks:
        if set(r)!={'domain','status','reason','references'} or r['status'] not in ('supported','contested','unknown'): raise ValueError('风险状态或字段错误')
        clean['risks'].append(dict(domain=r['domain'],status=r['status'],reason=text(r['reason'],'风险依据',10),references=_references(r['references'],known,r['status']=='unknown')))
    ids=set()
    for t in tasks:
        if not isinstance(t,dict) or set(t)!={'id','tool','question','rationale','next_action','success_criterion','stop_criterion','references'}: raise ValueError('任务字段不完整，或包含主机不接受的参数')
        tid=text(t['id'],'任务编号',1,40)
        if tid in ids: raise ValueError('任务编号重复')
        ids.add(tid)
        if t['tool'] not in set(TOOLS)|{'manual_research'}: raise ValueError('主机未授权该工具；模型文字不能增加工具权限')
        row={k:text(t[k],k,5) for k in ('question','rationale','next_action','success_criterion','stop_criterion')}
        row.update(id=tid,tool=t['tool'],references=_references(t['references'],known))
        clean['tasks'].append(row)
    return clean

def ingest(store,pid,proposal,author,revision,origin):
    context=packet(store,pid);clean=validate_proposal(proposal,context)
    if not isinstance(origin,dict) or set(origin)!={'kind','model_label'} or origin['kind'] not in ('assistant_session','external_model','human_import'): raise ValueError('必须如实记录草案来源')
    provenance=dict(kind=origin['kind'],model_label=text(origin['model_label'],'来源模型标记',3,200),identity_attestation='not_independently_attested')
    payload=dict(proposal=clean,context=context,origin=provenance,status='unreviewed_draft',scope='AI/导入建议；未作科学审批，不自动创建实验、交易或外发请求')
    return store._append(pid,'ai_proposal',payload,author,revision)

def proposals(store,pid):
    context=packet(store,pid)
    records=[]
    for e in store.events(pid):
        draft=e['payload'] if e['kind']=='ai_proposal' else e['payload'].get('draft') if e['kind']=='ai_model_receipt' and e['payload'].get('status')=='succeeded' else None
        if draft:
            records.append(dict(draft,event_id=e['id'],created=e['created'],stale=draft['proposal']['packet_hash']!=context['packet_hash']))
    return records

def _read(store,pid,name):
    raw,sha=store.attachment(pid,name,max_bytes=1024*1024)
    if len(raw)>1024*1024: raise ValueError('研究工具只接受不超过 1MB 的已绑定摘要附件')
    return raw,dict(file=name,sha256=sha,bytes=len(raw))

def audit_sources(store,pid):
    p=store.get(pid)
    names={e['source'].removeprefix('artifact:') for e in p['evidence'] if e['source'].startswith('artifact:')}
    if p['assets']:names.add('NCT06137183_current.json')
    if not names: raise ValueError('没有已绑定的本地来源；未知不能视为核对通过')
    records=[_read(store,pid,n)[1] for n in sorted(names)]
    return dict(kind='source_integrity',files=records,conclusion='列出的文件与导入时哈希一致；不评价来源内容或科学结论')

def interaction_card(store,pid):
    raw,receipt=_read(store,pid,'interaction_results.csv')
    rows=list(csv.DictReader(io.StringIO(raw.decode('utf-8'))))
    required={'gene','endpoint','n','interaction_OR','interaction_CI_low','interaction_CI_high','lr_p','BH_q_four'}
    if not rows or not required<=set(rows[0]) or len(rows)>100: raise ValueError('交互结果表结构不符合工具契约')
    cards=[]
    for r in rows:
        n=int(r['n']);est,lo,hi,p,q=[float(r[k]) for k in ('interaction_OR','interaction_CI_low','interaction_CI_high','lr_p','BH_q_four')]
        if n<2 or not all(math.isfinite(v) for v in (est,lo,hi,p,q)) or not 0<lo<=est<=hi or not 0<=p<=1 or not 0<=q<=1: raise ValueError('交互结果数值不完整或不合法')
        cards.append(dict(gene=r['gene'],endpoint=r['endpoint'],n=n,interaction_OR=est,CI_low=lo,CI_high=hi,lr_p=p,BH_q=q,interval_contains_null=lo<=1<=hi))
    return dict(kind='reported_interaction_card',source=receipt,rows=cards,scope='仅提取封存模型的结果，未重算模型；不得由 P 值较大推断不存在交互，亦不得外推 OSMR 阻断疗效')

def run_tool(store,pid,proposal_id,task_id,author,revision):
    current=store.get(pid)
    if revision!=current['revision']: raise Conflict('项目已更新，请重新查看任务后运行')
    proposal=next((p for p in proposals(store,pid) if p['event_id']==proposal_id),None)
    if not proposal or proposal['stale']: raise ValueError('草案不存在或已过期，不能运行')
    task=next((t for t in proposal['proposal']['tasks'] if t['id']==task_id),None)
    if not task or task['tool'] not in TOOLS: raise ValueError('该任务没有主机授权的只读工具，需人工研究')
    # Capture request before actual execution. A crash retains a requested event;
    # it is never synthesized into a successful receipt on restart.
    store._append(pid,'ai_tool_request',dict(proposal_id=proposal_id,task_id=task_id,tool=task['tool'],packet_hash=proposal['proposal']['packet_hash']),author,revision)
    request=next(e for e in store.events(pid) if e['previous']==revision)
    try:
        output=({'audit_bound_sources':audit_sources,'extract_interaction_evidence':interaction_card}[task['tool']])(store,pid)
        status='succeeded';error=None
    except (OSError,ValueError,TypeError,KeyError,UnicodeError,OverflowError,csv.Error):
        output=None;status='blocked';error='输入缺失、版本变化或格式不符合契约；没有产生成功结果'
    final=store.get(pid)
    if packet(store,pid)['packet_hash']!=proposal['proposal']['packet_hash']:
        status='stale';error='执行期间项目内容变化，输出不能用于当前项目';output=None
    receipt=dict(request_id=request['id'],proposal_id=proposal_id,task_id=task_id,tool=task['tool'],packet_hash=proposal['proposal']['packet_hash'],status=status,error=error,output=output,scientific_review='pending',execution_scope='host_defined_read_only_no_model_or_shell_call')
    store._append(pid,'ai_tool_receipt',receipt,author,final['revision'])
    return receipt
