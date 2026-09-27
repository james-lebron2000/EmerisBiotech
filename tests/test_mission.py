import json
import pytest
from vbt.workbench import Workbench,page
@pytest.fixture
def app(tmp_path):return Workbench(tmp_path/'workspace',tmp_path/'state')
def test_empty_is_unknown(app):
 text=page(app,'/mission',{});assert '尚无进展记录' in text and '投资优势' in text;assert not app.rows('milestones')
def test_append_restart_export(app):
 app.update_milestone('data','in_progress','TEST','Starting a test data contract')
 app.update_milestone('data','blocked','TEST','Missing historical timestamps')
 again=Workbench(app.workspace,app.state);assert len(again.rows('milestones'))==2
 assert json.loads((app.workspace/'artifacts/workbench/history.json').read_text())['milestones'][0]['status']=='blocked'
 assert 'Missing historical timestamps' in page(again,'/mission',{})
@pytest.mark.parametrize('state,ref',[('complete',''),('evidence_submitted',''),('evidence_submitted','invented')])
def test_no_false_completion(app,state,ref):
 with pytest.raises(ValueError):app.update_milestone('clinical',state,'TEST','test evidence statement',ref)
def test_linked_evidence_is_not_approval(app):
 p=app.workspace/'artifacts/runs/example';p.mkdir(parents=True);(p/'status.json').write_text('{}')
 app.update_milestone('clinical','evidence_submitted','TEST','<script>not approved</script>','example')
 text=page(app,'/mission',{});assert '&lt;script&gt;' in text and '<script>' not in text;assert '待独立审查' in text
def test_existing_notes_survive_migration(app):
 app.add('TEST','note','Existing note retained')
 with app.db() as db:db.execute('DROP TABLE milestones')
 again=Workbench(app.workspace,app.state);assert len(again.rows('entries'))==1;assert again.rows('milestones')==[]
def test_web_update(app):
 import threading,http.client
 from http.server import ThreadingHTTPServer
 from urllib.parse import urlencode
 from vbt.workbench import handler
 server=ThreadingHTTPServer(('127.0.0.1',0),handler(app));threading.Thread(target=server.serve_forever,daemon=True).start()
 try:
  conn=http.client.HTTPConnection('127.0.0.1',server.server_port)
  body=urlencode({'token':app.token,'milestone':'cloud','status':'blocked','author':'TEST','note':'Awaiting selected deployment destination'})
  conn.request('POST','/milestone',body,{'Origin':f'http://127.0.0.1:{server.server_port}','Content-Type':'application/x-www-form-urlencoded'})
  response=conn.getresponse();assert response.status==303 and response.getheader('Location')=='/mission?saved=1';response.read();conn.close()
  assert app.rows('milestones')[0]['milestone']=='cloud'
 finally:server.shutdown();server.server_close()
