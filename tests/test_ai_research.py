import hashlib,json,copy
import pytest
from vbt.programs import Programs,Conflict
from vbt.ai_research import packet,ingest,run_tool,proposals,validate_proposal,DOMAINS
from vbt.workbench import Workbench,page
from test_programs import PROFILE,add

@pytest.fixture
def ready(tmp_path):
    store=Programs(tmp_path/'state');pid=store.create(PROFILE,'engineering tester');ref=add(store,pid)
    source=tmp_path/'source';source.mkdir();raw=b'gene,endpoint,n,interaction_OR,interaction_CI_low,interaction_CI_high,lr_p,BH_q_four\nOSMR,healing,542,0.895,0.517,1.548,0.692,0.834\n'
    (source/'interaction_results.csv').write_bytes(raw);sha=hashlib.sha256(raw).hexdigest()
    store._append(pid,'import',dict(root=str(source),files={'interaction_results.csv':sha}),'engineering importer',store.get(pid)['revision'])
    store.add_evidence(pid,dict(direction='counter',review='pending',claim='Engineering fixture interaction, not clinical result',scope='synthetic software test only',limitations='no biological validity',source='artifact:interaction_results.csv',source_date='2026-10-26'),'engineering tester',store.get(pid)['revision'])
    context=packet(store,pid)
    proposal=dict(packet_hash=context['packet_hash'],summary='Engineering proposal fixture, not a real AI model output',risks=[dict(domain=k,status='unknown',reason='Unknown in this synthetic engineering fixture',references=[]) for k in DOMAINS],tasks=[dict(id='audit',tool='audit_bound_sources',question='Check the registered source integrity',rationale='File provenance must be checked independently',next_action='Read bounded existing source files only',success_criterion='Recorded file hashes match inputs',stop_criterion='Stop when a source is missing or changed',references=[ref]),dict(id='card',tool='extract_interaction_evidence',question='Extract existing reported interaction evidence',rationale='Within arm association differs from interaction',next_action='Read the already bound source table',success_criterion='Preserve exact effect and uncertainty',stop_criterion='Reject invalid or missing source table',references=[ref])])
    return store,pid,proposal,source

def record(store,pid,proposal):
    ingest(store,pid,proposal,'engineering tester',store.get(pid)['revision'],dict(kind='human_import',model_label='synthetic fixture; not real model'))
    return proposals(store,pid)[-1]['event_id']

def test_packet_stable_across_ai_events_and_excludes_paths(ready):
    store,pid,proposal,_=ready;context=packet(store,pid);rid=record(store,pid,proposal)
    assert packet(store,pid)==context and '/Users/' not in json.dumps(context) and str(store.state) not in json.dumps(context)
    assert proposals(store,pid)[-1]['origin']['kind']=='human_import'
    assert store.get(pid)['decisions']==[] and store.get(pid)['evidence'][0]['review']=='pending'

@pytest.mark.parametrize('change',['fake_ref','unknown_tool','extra_arguments','missing_domain','duplicate_task','stale_hash'])
def test_invalid_ai_proposal_rejected(ready,change):
    store,pid,proposal,_=ready;proposal=copy.deepcopy(proposal)
    if change=='fake_ref':proposal['tasks'][0]['references']=['fabricated']
    elif change=='unknown_tool':proposal['tasks'][0]['tool']='execute_shell'
    elif change=='extra_arguments':proposal['tasks'][0]['command']='rm -rf anything'
    elif change=='missing_domain':proposal['risks'].pop()
    elif change=='duplicate_task':proposal['tasks'].append(proposal['tasks'][0])
    else:proposal['packet_hash']='stale'
    with pytest.raises(ValueError):record(store,pid,proposal)
    assert proposals(store,pid)==[]

def test_tools_are_real_bounded_execution_and_not_review(ready):
    store,pid,proposal,_=ready;rid=record(store,pid,proposal)
    for tid in ['audit','card']:
        receipt=run_tool(store,pid,rid,tid,'engineering operator',store.get(pid)['revision'])
        assert receipt['status']=='succeeded' and receipt['scientific_review']=='pending'
    assert receipt['output']['rows'][0]['interaction_OR']==0.895
    assert receipt['output']['rows'][0]['interval_contains_null'] is True
    assert len([e for e in store.events(pid) if e['kind']=='ai_tool_request'])==2
    assert store.get(pid)['decisions']==[]

def test_stale_proposal_and_manual_task_cannot_execute(ready):
    store,pid,proposal,_=ready;proposal['tasks'][0]['tool']='manual_research';rid=record(store,pid,proposal)
    with pytest.raises(ValueError,match='人工研究'):run_tool(store,pid,rid,'audit','tester',store.get(pid)['revision'])
    add(store,pid)
    assert proposals(store,pid)[-1]['stale']
    with pytest.raises(ValueError,match='过期'):run_tool(store,pid,rid,'card','tester',store.get(pid)['revision'])

@pytest.mark.parametrize('damage',['changed','missing','invalid_csv','oversized','missing_cells'])
def test_tool_failure_leaves_no_success_receipt(ready,damage):
    store,pid,proposal,source=ready;rid=record(store,pid,proposal);path=source/'interaction_results.csv'
    if damage=='missing':path.unlink()
    elif damage=='changed':path.write_bytes(b'changed')
    else:
        raw=b'a,b\n1,2\n' if damage=='invalid_csv' else (b'gene,endpoint,n,interaction_OR,interaction_CI_low,interaction_CI_high,lr_p,BH_q_four\nOSMR,healing\n' if damage=='missing_cells' else b'x'*(1024*1024+1));path.write_bytes(raw)
        store._append(pid,'import',dict(root=str(source),files={path.name:hashlib.sha256(raw).hexdigest()}),'engineering importer',store.get(pid)['revision'])
    receipt=run_tool(store,pid,rid,'card','engineering operator',store.get(pid)['revision'])
    assert receipt['status']=='blocked' and receipt['output'] is None

def test_page_and_packet_http_preserve_no_model_claim(ready,tmp_path):
    import threading,urllib.request
    from http.server import ThreadingHTTPServer
    from vbt.workbench import handler
    store,pid,proposal,_=ready;record(store,pid,proposal);app=Workbench(tmp_path/'workspace',store.state)
    body=page(app,'/programs',{'id':pid});assert 'AI 研究草案与工具' in body and '不自动调用外部模型' in body
    assert '执行任意代码' not in body
    server=ThreadingHTTPServer(('127.0.0.1',0),handler(app));threading.Thread(target=server.serve_forever,daemon=True).start()
    try:
        context=json.load(urllib.request.urlopen(f'http://127.0.0.1:{server.server_port}/program/research-packet?id='+pid))
        assert context['packet_hash']==proposal['packet_hash']
    finally:server.shutdown();server.server_close()

def test_http_proposal_import_run_and_stale_receipt(ready,tmp_path):
    import threading,urllib.request
    from urllib.parse import urlencode
    from http.server import ThreadingHTTPServer
    from vbt.workbench import handler
    store,pid,proposal,_=ready;app=Workbench(tmp_path/'workspace',store.state)
    server=ThreadingHTTPServer(('127.0.0.1',0),handler(app));threading.Thread(target=server.serve_forever,daemon=True).start();base=f'http://127.0.0.1:{server.server_port}'
    def post(data):
        data=dict(data,token=app.token,id=pid,revision=store.get(pid)['revision'],author='engineering operator')
        return urllib.request.urlopen(urllib.request.Request(base+'/program/action',data=urlencode(data).encode(),headers={'Origin':base})).read().decode()
    try:
        post(dict(operation='ai_import',proposal_json=json.dumps(proposal),origin_kind='human_import',model_label='engineering fixture only'))
        rid=proposals(store,pid)[-1]['event_id']
        body=post(dict(operation='ai_run',proposal_id=rid,task_id='card'))
        assert '工具执行完成' in body and 'reported_interaction_card' in body
        add(store,pid)
        assert '此回执属于历史上下文' in page(app,'/programs',{'id':pid})
    finally:server.shutdown();server.server_close()
