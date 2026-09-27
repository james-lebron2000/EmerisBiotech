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
