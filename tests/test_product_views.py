import json
import threading
import urllib.request
from urllib.parse import urlencode
from http.server import ThreadingHTTPServer
import pytest
from vbt.workbench import Workbench, page, handler
from vbt.product_views import CASES

@pytest.fixture
def app(tmp_path):
    return Workbench(tmp_path/'workspace', tmp_path/'state')

def test_empty_workspace_has_demo_without_fake_runs(app):
    assert app.records() == []
    for key in CASES:
        assert 'Decision timeline' in page(app, '/case', {'id':key})
    assert 'No matching' in page(app, '/records', {})
    assert 'six-minute' in page(app, '/guide', {})
    with pytest.raises(ValueError): page(app, '/case', {'id':'missing'})

def test_decision_persists_and_is_separate(app):
    app.decide('osmr-uc','stop','<reviewer>','<script>test decision</script>')
    restored = Workbench(app.workspace, app.state)
    assert len(restored.rows('decisions')) == 1
    assert restored.records() == []
    assert restored.rows('entries') == []
    html = page(restored, '/case', {'id':'osmr-uc'})
    assert '&lt;reviewer&gt;' in html and '&lt;script&gt;' in html
    assert '<script>' not in html
    exported = json.loads((app.workspace/'artifacts/workbench/history.json').read_text())
    assert exported['demo_decisions'][0]['action'] == 'stop'
    assert 'test decision' not in page(restored, '/case', {'id':'bcma-autoimmune'})

@pytest.mark.parametrize('case,action,author,reason',[
    ('unknown','stop','a','long enough reason'),
    ('osmr-uc','approve','a','long enough reason'),
    ('osmr-uc','stop',' ','long enough reason'),
    ('osmr-uc','stop','a','short'),
    ('osmr-uc','stop','a','x'*5001),
])
def test_invalid_decisions_rejected(app,case,action,author,reason):
    with pytest.raises(ValueError): app.decide(case,action,author,reason)
    assert app.rows('decisions') == []

def test_http_decision_roundtrip(app):
    server=ThreadingHTTPServer(('127.0.0.1',0),handler(app))
    threading.Thread(target=server.serve_forever,daemon=True).start()
    base=f'http://127.0.0.1:{server.server_port}'
    try:
        data=urlencode(dict(token=app.token,case_id='osmr-uc',action='revise',author='Demo',reason='Record a clear evidence boundary')).encode()
        response=urllib.request.urlopen(urllib.request.Request(base+'/decision',data=data,headers={'Origin':base}))
        assert response.url.endswith('/case?id=osmr-uc&saved=1')
        assert 'Saved' in response.read().decode()
        assert len(app.rows('decisions'))==1
    finally:
        server.shutdown();server.server_close()


@pytest.mark.parametrize('kind,folder,name,data,extra',[
 ('portfolio','portfolio/snapshots','evaluation.json',{},('plan.json',None)),
 ('portfolio','portfolio/snapshots','evaluation.json',{},('plan.json',{'hypotheses':None})),
 ('portfolio','portfolio/snapshots','evaluation.json',{},('plan.json',{'hypotheses':[None]})),
 ('cycle','cycles','cycle.json',{'decision':None},None),
 ('run','runs','status.json',{'execution':'failed'},('work/results.json',[None])),
 ('run','runs','status.json',{'execution':'failed','errors':None},None),
])
def test_detail_reports_malformed_sections_without_replacing_originals(app,kind,folder,name,data,extra):
 path=app.workspace/'artifacts'/folder/'malformed-detail';path.mkdir(parents=True)
 (path/name).write_text(json.dumps(data))
 if extra:
  target=path/extra[0];target.parent.mkdir(parents=True,exist_ok=True);target.write_text(json.dumps(extra[1]))
 before={str(p):p.read_bytes() for p in path.rglob('*') if p.is_file()}
 body=page(app,'/record',{'kind':kind,'id':'malformed-detail'})
 assert 'format requires review' in body and 'Research summary' in body
 assert {str(p):p.read_bytes() for p in path.rglob('*') if p.is_file()}==before
