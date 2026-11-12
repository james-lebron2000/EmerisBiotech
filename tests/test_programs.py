import json
import sqlite3
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from urllib.parse import urlencode
import pytest
from vbt.programs import Programs, Conflict
from vbt.workbench import Workbench, handler, page

PROFILE=dict(title='OSMR UC test project',target='OSMR',population='UC adults',modality='blocking antibody',hypothesis='interaction differs by treatment',comparator='placebo',endpoint='week 8 healing',differentiation='validated patient selection',material_rights='unknown; not secured')
def evidence(direction='support',review='pending'):
    return dict(direction=direction,review=review,claim='Known test result',scope='UC healing only',limitations='not clinical validation',source='https://example.org/study',source_date='2026-10-26')

@pytest.fixture
def store(tmp_path):return Programs(tmp_path/'state')

def add(store,pid,direction='support',review='pending'):
    store.add_evidence(pid,evidence(direction,review),'test researcher',store.get(pid)['revision'])
    return store.get(pid)['evidence'][-1]['event_id']

def experiment(store,pid,refs):
    store.add_experiment(pid,dict(question='Validate interaction externally',design='donor-separated independent cohort',success='frozen precision and calibration requirements',stop='no gain beyond clinical baseline',owner='proposed assay lead',currency='CNY',cost='1000.25',days='14',evidence_ids=refs),'test researcher',store.get(pid)['revision'])

def test_patient_program_and_conflict_preserve_history(store):
    pid=store.create(PROFILE,'tester');old=store.get(pid)['revision'];add(store,pid)
    with pytest.raises(Conflict):store.add_evidence(pid,evidence(),'tester',old)
    assert len(store.get(pid)['events'])==2
    with store.db() as db:
        with pytest.raises(sqlite3.IntegrityError):db.execute('DELETE FROM program_events')
        with pytest.raises(sqlite3.IntegrityError):db.execute("UPDATE program_events SET author='other'")
    assert Programs(store.state).get(pid)['revision']==store.get(pid)['revision']

def test_unknown_and_pending_block_advance_but_allow_hold(store):
    pid=store.create(PROFILE,'tester');ref=add(store,pid)
    decision=dict(action='advance',reason='Requires more independent evidence',remaining_risk='uncertain external validity',evidence_ids=[ref])
    with pytest.raises(ValueError,match='暂不能推进'):store.decide(pid,decision,'tester',store.get(pid)['revision'])
    decision['action']='hold';store.decide(pid,decision,'tester',store.get(pid)['revision'])
    p=store.get(pid);assert p['decisions'][0]['basis_revision']==p['events'][-2]['digest']
    assert p['decisions'][0]['decision_scope'].startswith('研究计划决定')

def test_advance_requires_counter_reference_and_current_experiment(store):
    pid=store.create(PROFILE,'tester');s=add(store,pid,'support','reviewed');c=add(store,pid,'counter','reviewed');experiment(store,pid,[s,c])
    payload=dict(action='advance',reason='Proceed only with proposed validation',remaining_risk='material rights remain unknown',evidence_ids=[s])
    with pytest.raises(ValueError,match='必须引用反证'):store.decide(pid,payload,'tester',store.get(pid)['revision'])
    payload['evidence_ids']=[s,c];store.decide(pid,payload,'tester',store.get(pid)['revision'])
    assert store.get(pid)['decisions'][-1]['action']=='advance'
    old_profile=store.get(pid)['events'][0]['payload']
    store.revise(pid,dict(PROFILE,hypothesis='different treatment and population',revision_reason='New counterevidence changes population'),'tester',store.get(pid)['revision'])
    assert store.get(pid)['events'][0]['payload']==old_profile
    assert len(store.gaps(store.get(pid)))==2
    with pytest.raises(ValueError,match='暂不能推进'):store.decide(pid,payload,'tester',store.get(pid)['revision'])

def test_review_append_changes_effective_state_not_original(store):
    pid=store.create(PROFILE,'tester');ref=add(store,pid)
    original=store.get(pid)['events'][1]['payload']
    store.review_evidence(pid,dict(evidence_id=ref,review='reviewed',reason='Checked the source population and endpoint'),'reviewer',store.get(pid)['revision'])
    assert store.get(pid)['evidence'][0]['review']=='reviewed'
    assert store.get(pid)['events'][1]['payload']==original and original['review']=='pending'

@pytest.mark.parametrize('cost',['NaN','inf','-1','0','garbage'])
def test_invalid_budget_rejected(store,cost):
    pid=store.create(PROFILE,'tester');ref=add(store,pid)
    with pytest.raises(ValueError):store.add_experiment(pid,dict(question='Check uncertainty',design='controlled assay',success='frozen success',stop='frozen failure',owner='proposed lead',currency='CNY',cost=cost,days='14',evidence_ids=[ref]),'tester',store.get(pid)['revision'])

def test_cross_project_references_rejected(store):
    a=store.create(PROFILE,'tester');b=store.create(PROFILE,'tester');ref=add(store,b)
    with pytest.raises(ValueError):experiment(store,a,[ref])
    with pytest.raises(ValueError):store.decide(a,dict(action='stop',reason='Different project reference is invalid',remaining_risk='unknown context',evidence_ids=[ref]),'tester',store.get(a)['revision'])

def test_attachments_fail_on_missing_or_changed_source(store,tmp_path):
    import hashlib
    pid=store.create(PROFILE,'tester');root=tmp_path/'artifacts';root.mkdir();p=root/'interaction_results.csv';p.write_bytes(b'a,b\n1,2\n');sha=hashlib.sha256(p.read_bytes()).hexdigest()
    store._append(pid,'import',dict(root=str(root),files={p.name:sha}),'test importer',store.get(pid)['revision'])
    assert store.attachment(pid,p.name)[1]==sha
    p.write_bytes(b'changed')
    with pytest.raises(ValueError,match='哈希不一致'):store.attachment(pid,p.name)
    with pytest.raises(ValueError):store.attachment(pid,'../../secret')

def test_http_persistence_csrf_multi_reference_export(tmp_path):
    app=Workbench(tmp_path/'workspace',tmp_path/'state');store=Programs(app.state)
    server=ThreadingHTTPServer(('127.0.0.1',0),handler(app));threading.Thread(target=server.serve_forever,daemon=True).start();base=f'http://127.0.0.1:{server.server_port}'
    def post(payload,origin=True):
        return urllib.request.urlopen(urllib.request.Request(base+'/program/action',data=urlencode(dict(payload,token=app.token),doseq=True).encode(),headers={'Origin':base} if origin else {}))
    try:
        assert '研发项目决策' in urllib.request.urlopen(base+'/programs').read().decode()
        with pytest.raises(urllib.error.HTTPError) as err:post(dict(PROFILE,operation='create',author='tester'),False)
        assert err.value.code==403
        response=post(dict(PROFILE,operation='create',author='tester'));assert response.status==200
        p=store.list()[0];pid=p['id'];s=add(store,pid);c=add(store,pid,'counter')
        post(dict(operation='decision',id=pid,revision=store.get(pid)['revision'],author='tester',action='hold',reason='Wait for independent scientific review',remaining_risk='uncertain external validity',evidence_ids=[s,c]))
        exported=json.load(urllib.request.urlopen(base+'/program/export?id='+pid))
        assert exported['program']['decisions'][-1]['evidence_ids']==[s,c]
        assert exported['scientific_review']=='not_inferred'
        with pytest.raises(urllib.error.HTTPError) as stale:
            post(dict(operation='decision',id=pid,revision=p['revision'],author='tester',action='hold',reason='Do not replace the newer decision',remaining_risk='external validity remains unknown',evidence_ids=[s]))
        assert stale.value.code==409
        body=page(app,'/programs',{'id':pid})
        from html.parser import HTMLParser
        class IDs(HTMLParser):
            def __init__(self):super().__init__();self.ids=[]
            def handle_starttag(self,t,a):self.ids.extend(v for k,v in a if k=='id')
        parser=IDs();parser.feed(body);assert len(parser.ids)==len(set(parser.ids))
        assert '历史链已在读取时校验' in body
    finally:server.shutdown();server.server_close()

def test_html_escapes_claims_and_profiles(store,tmp_path):
    app=Workbench(tmp_path/'work',store.state)
    pid=store.create(dict(PROFILE,title='<script>alert(1)</script>'),'tester')
    ev=evidence();ev['claim']='<img src=x onerror=alert(1)>';store.add_evidence(pid,ev,'tester',store.get(pid)['revision'])
    body=page(app,'/programs',{'id':pid})
    assert '<script>alert' not in body and '<img src=x' not in body and '&lt;script&gt;' in body

def test_import_is_idempotent_and_rejects_corruption(store,tmp_path):
    import importlib.util,hashlib
    from pathlib import Path
    spec=importlib.util.spec_from_file_location('uc_importer',Path(__file__).resolve().parents[1]/'scripts/import_uc_program.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    root=tmp_path/'sealed';root.mkdir()
    contents={'verification.json':'{"status":"passed"}','interaction_results.csv':'gene,endpoint,interaction_OR,interaction_CI_low,interaction_CI_high,lr_p\nOSMR,healing,0.9,0.5,1.5,0.69\n','UC_优化补充报告_20261026.html':'<h1>Engineering fixture only</h1>'}
    files=[]
    for name,body in contents.items():
        (root/name).write_text(body);files.append(dict(path=name,sha256=hashlib.sha256(body.encode()).hexdigest()))
    (root/'ARTIFACT_MANIFEST.json').write_text(json.dumps(dict(run_id='uc_optimization_20261026',files=files)))
    pid=module.import_uc(store,root);before=store.get(pid)['revision'];assert module.import_uc(store,root)==pid
    assert before==store.get(pid)['revision'] and len(store.get(pid)['evidence'])==3
    assert all(r['review']=='pending' for r in store.get(pid)['evidence'])
    assert store.get(pid)['decisions']==[]
    (root/'verification.json').write_text('changed')
    with pytest.raises(ValueError,match='hash mismatch'):module.import_uc(store,root)

def test_corrupt_history_cannot_be_exported_as_verified(store):
    pid=store.create(PROFILE,'tester')
    with store.db() as db:
        db.execute('DROP TRIGGER program_no_update')
        db.execute("UPDATE program_events SET author='tampered'")
    with pytest.raises(ValueError,match='哈希链不一致'):store.export(pid)
