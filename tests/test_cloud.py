import pytest
from vbt.cloud import validate_origin,CloudWorkbench
@pytest.mark.parametrize('value',['http://example.com','https://user:pass@example.com','https://example.com/path','https://example.com?key=x',''])
def test_invalid_origin(value):
 with pytest.raises(ValueError):validate_origin(value)
def test_valid_origin():assert validate_origin('https://research.example.com')=='https://research.example.com'
def test_cloud_rejects_execution(tmp_path):
 app=CloudWorkbench(tmp_path/'workspace',tmp_path/'state')
 with pytest.raises(ValueError):app.start('run','any-run')
def test_https_proxy_origin(tmp_path):
 import threading,http.client
 from urllib.parse import urlencode
 from http.server import ThreadingHTTPServer
 from vbt.workbench import handler
 app=CloudWorkbench(tmp_path/'workspace',tmp_path/'state');origin='https://research.example.com'
 server=ThreadingHTTPServer(('127.0.0.1',0),handler(app,origin));threading.Thread(target=server.serve_forever,daemon=True).start()
 try:
  body=urlencode({'token':app.token,'author':'TEST','kind':'note','body':'Test cloud note'})
  for supplied,expected in [(origin,303),('https://attacker.example',403)]:
   conn=http.client.HTTPConnection('127.0.0.1',server.server_port)
   conn.request('POST','/note',body,{'Host':'research.example.com','Origin':supplied,'Content-Type':'application/x-www-form-urlencoded'})
   response=conn.getresponse();assert response.status==expected;response.read();conn.close()
  assert len(app.rows('entries'))==1
 finally:server.shutdown();server.server_close()
