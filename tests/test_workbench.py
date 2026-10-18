import json,threading,urllib.request,urllib.error
from http.server import ThreadingHTTPServer
from urllib.parse import urlencode
import pytest
from vbt.workbench import Workbench,page,handler
@pytest.fixture
def app(tmp_path):
 w=tmp_path/'workspace';p=w/'artifacts/runs/test-run';p.mkdir(parents=True);(p/'status.json').write_text(json.dumps({'execution':'failed','scientific_review':'revise'}));(p/'stderr.log').write_text('example failure');return Workbench(w,tmp_path/'state')
def test_history_and_escape(app):
 assert len(app.records())==1;assert '执行失败' in page(app,'/records',{})
 app.add('tester','question','<script>alert(1)</script>','test-run')
 assert '&lt;script&gt;' in page(app,'/notes',{}) and '<script>alert' not in page(app,'/notes',{})
 again=Workbench(app.workspace,app.state);assert len(again.rows('entries'))==1
 assert json.loads((app.workspace/'artifacts/workbench/history.json').read_text())['entries'][0]['reference']=='test-run'
def test_restart_preserves_interrupted(app):
 with app.db() as db:db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?)',('j','2026','run','test-run','running','test'))
 again=Workbench(app.workspace,app.state);assert again.rows('jobs')[0]['status']=='interrupted'
def test_bad_operation(app):
 with pytest.raises(ValueError):app.add('','note','test')
 with pytest.raises(ValueError):app.start('cycle','bad')
def test_http_routes_and_csrf(app):
 server=ThreadingHTTPServer(('127.0.0.1',0),handler(app));threading.Thread(target=server.serve_forever,daemon=True).start();base=f'http://127.0.0.1:{server.server_port}'
 try:
  assert '研究控制台' in urllib.request.urlopen(base).read().decode()
  assert urllib.request.urlopen(base+'/artifact?kind=run&id=test-run&file=stderr.log').read()==b'example failure'
  for url in ['/artifact?kind=run&id=test-run&file=../../secret','/record?kind=run&id=missing']:
   with pytest.raises(urllib.error.HTTPError) as err:urllib.request.urlopen(base+url)
   assert err.value.code==404
  body=urlencode({'token':app.token,'author':'tester','kind':'note','body':'integration test'}).encode()
  with pytest.raises(urllib.error.HTTPError) as err:urllib.request.urlopen(urllib.request.Request(base+'/note',data=body))
  assert err.value.code==403
  request=urllib.request.Request(base+'/note',data=body,headers={'Origin':base});assert urllib.request.urlopen(request).status==200
  assert len(app.rows('entries'))==1
  with pytest.raises(urllib.error.HTTPError):urllib.request.urlopen(urllib.request.Request(base,headers={'Host':'attacker.test'}))
 finally:server.shutdown();server.server_close()
def test_background_status(app,monkeypatch):
 import vbt.workbench as module
 class Result:returncode=0;stdout='verified';stderr=''
 monkeypatch.setattr(module.subprocess,'run',lambda *a,**kw:Result())
 with app.db() as db:db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?)',('j','2026','run','test-run','running','test'))
 app.execute('j','run','test-run');assert app.rows('jobs')[0]['status']=='succeeded'

@pytest.mark.parametrize('review',[None,''])
def test_missing_scientific_review_remains_unknown(app,review):
 path=app.workspace/'artifacts/runs/test-run/status.json'
 path.write_text(json.dumps({'execution':'succeeded','scientific_review':review}))
 assert app.records()[0]['review']=='unknown'
 path.write_text(json.dumps({'execution':'succeeded'}))
 assert app.records()[0]['review']=='unknown'

def test_record_back_link_retains_only_safe_history_context(app):
 back='/records?q=OSMR&status=failed&scope=unmarked&offset=20'
 body=page(app,'/record',{'kind':'run','id':'test-run','return':back})
 assert 'href="/records?q=OSMR&amp;status=failed&amp;scope=unmarked&amp;offset=20"' in body
 body=page(app,'/record',{'kind':'run','id':'test-run','return':'https://evil.test/records'})
 assert 'href="/records">← 返回历史' in body and 'evil.test' not in body

def test_integrity_jobs_separates_market_collection(app):
 body=page(app,'/jobs',{})
 assert '<h1>完整性校验</h1>' in body
 assert 'href="/coverage#collection-queue"' in body
 assert '暂无校验任务' in body

def test_record_explains_saved_gates_before_summary_and_technical_attachments(app):
 path=app.workspace/'artifacts/runs/test-run'
 saved={'execution':'succeeded','record_integrity':'complete','scientific_review':'revise'}
 (path/'status.json').write_text(json.dumps(saved))
 (path/'review.json').write_text(json.dumps({'reviewer_type':'automated','human_signature':None}))
 (path/'audit.html').write_text('<h1>Audit</h1>')
 body=page(app,'/record',{'kind':'run','id':'test-run'})
 assert body.index('研究记录的四项独立状态') < body.index('研究摘要') < body.index('证据与研究附件')
 assert '保存状态：记录完整' in body and '保存状态：需修订' in body
 assert '未记录人工签署' in body and '尚未在此页面重新核验' in body
 assert '>审计报告</a>' in body
 assert body.index('技术文件与执行日志') < body.index('stderr.log')
 assert json.loads((path/'status.json').read_text())==saved

def test_record_signature_field_cannot_become_approval(app):
 path=app.workspace/'artifacts/runs/test-run'
 (path/'review.json').write_text(json.dumps({'reviewer_type':'human','human_signature':'<img src=x>'}))
 body=page(app,'/record',{'kind':'run','id':'test-run'})
 assert '签署字段已记录 · 尚未核验' in body and '不代表签名有效或研究已获批准' in body
 assert '<img src=x>' not in body and '已通过人工审批' not in body
 (path/'review.json').write_text(json.dumps({'reviewer_type':'automated','human_signature':'someone'}))
 assert '签署字段待核对' in page(app,'/record',{'kind':'run','id':'test-run'})

def test_missing_gates_stay_unknown_despite_successful_execution(app):
 path=app.workspace/'artifacts/runs/test-run'
 (path/'status.json').write_text(json.dumps({'execution':'succeeded','scientific_review':'pass'}))
 body=page(app,'/record',{'kind':'run','id':'test-run'})
 assert '完整性状态未知' in body and '签署状态未知' in body
 assert '执行成功' in body and '保存状态：通过' in body

def test_note_fields_have_accessible_names(app):
 from html.parser import HTMLParser
 class Fields(HTMLParser):
  def __init__(self,body):super().__init__();self.tags=[];self.feed(body)
  def handle_starttag(self,tag,attrs):self.tags.append((tag,dict(attrs)))
 tags=Fields(page(app,'/notes',{})).tags
 labels={a['for'] for t,a in tags if t=='label'}
 fields={a['id'] for t,a in tags if t in ('select','input','textarea') and a.get('id')}
 assert labels==fields=={'note-kind','note-author','note-body'}

def test_cycle_labels_starting_hypothesis_separately_from_saved_decision(app):
 path=app.workspace/'artifacts/cycles/question';path.mkdir(parents=True)
 saved={'hypothesis':'The association predicts success.','complete_research_cycle':True,'decision':{'scope':'Terminate this inference.'}}
 (path/'cycle.json').write_text(json.dumps(saved))
 body=page(app,'/record',{'kind':'cycle','id':'question'})
 assert '研究循环 · 原始研究假设' in body and '起始假设保留原文' in body
 assert body.index('The association predicts success.') < body.index('研究摘要') < body.index('Terminate this inference.')
 assert json.loads((path/'cycle.json').read_text())==saved

@pytest.mark.parametrize('kind,folder,name',[('run','runs','status.json'),('cycle','cycles','cycle.json'),('portfolio','portfolio/snapshots','evaluation.json')])
@pytest.mark.parametrize('saved',[None,[],42])
def test_malformed_history_keeps_visible_unknown_record_and_other_routes(app,kind,folder,name,saved):
 path=app.workspace/'artifacts'/folder/'malformed-version';path.mkdir(parents=True)
 raw=json.dumps(saved);(path/name).write_text(raw)
 record=app.record(kind,'malformed-version')
 assert record is not None and record['status']=='unknown' and record['review']=='unknown'
 assert '记录格式待核实' in record['title'] and record['data']['saved_value']==saved
 assert '临床资产研究控制台' in page(app,'/',{})
 assert '记录格式待核实' in page(app,'/records',{})
 assert '记录格式待核实' in page(app,'/record',{'kind':kind,'id':'malformed-version'})
 assert (path/name).read_text()==raw
