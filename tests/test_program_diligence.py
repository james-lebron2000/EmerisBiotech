import hashlib,json
import pytest
from vbt.programs import Programs
from vbt.workbench import Workbench,page
from test_programs import PROFILE,add

@pytest.fixture
def linked(tmp_path):
    s=Programs(tmp_path/'state');pid=s.create(PROFILE,'engineering tester');ref=add(s,pid)
    source=tmp_path/'NCT06137183_current.json';source.write_text('{"engineering_fixture":true}')
    data=dict(asset_name='Engineering reference only',nct='NCT06137183',purpose='fixture only, no real asset',registered_sponsor='Engineering fixture',phases=['PHASE2'],trial_status='TERMINATED',why_stopped='synthetic fixture',last_update='2020-01-01',snapshot_date='2020-01-02',rights_status='not_established',source_path=str(source),source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),source_url='https://example.invalid/fixture')
    s._append(pid,'asset_import',data,'test importer',s.get(pid)['revision'])
    return s,pid,ref,source

def payload(s,pid,ref):
    return dict(asset_id=s.get(pid)['assets'][0]['event_id'],action='defer',evidence_ids=[ref],result_ids=[],thesis='Scientific uncertainty prevents asset conclusion',unresolved='Ownership, deal availability and differentiation unknown',next_request='Obtain rights chain and independent functional validation')

def test_diligence_separate_from_rd_and_no_rights_inference(linked,tmp_path):
    s,pid,ref,_=linked;p=payload(s,pid,ref)
    s.add_diligence(pid,p,'engineering tester',s.get(pid)['revision'])
    saved=s.get(pid);assert saved['decisions']==[] and len(saved['diligence'])==1
    assert saved['diligence'][0]['rights_status']=='not_established'
    exported=json.loads(s.export(pid));assert exported['program']['diligence'][0]['evidence_ids']==[ref]
    body=page(Workbench(tmp_path/'work',s.state),'/programs',{'id':pid})
    assert '资产与 BD 尽调' in body and '来源试验阶段/状态' in body and '不等于资产所有权' in body
    assert '暂缓资产判断' in body

@pytest.mark.parametrize('override',[{'action':'buy'},{'asset_id':'other-project'},{'evidence_ids':[]},{'evidence_ids':['missing']},{'result_ids':['missing']}])
def test_invalid_diligence_rejected(linked,override):
    s,pid,ref,_=linked;p=payload(s,pid,ref);p.update(override)
    with pytest.raises(ValueError):s.add_diligence(pid,p,'tester',s.get(pid)['revision'])

def test_changed_asset_source_rejected(linked):
    s,pid,ref,source=linked;source.write_text('changed')
    with pytest.raises(ValueError,match='来源已变化'):s.add_diligence(pid,payload(s,pid,ref),'tester',s.get(pid)['revision'])
