import csv,io,json,threading,urllib.request
from http.server import ThreadingHTTPServer
import pytest
from vbt.market import Market
from vbt.market_export import create_export,export_file,text_cell,trial_csv,export_page
from vbt.workbench import Workbench,handler

@pytest.fixture
def market(tmp_path):
 m=Market(tmp_path/'w',tmp_path/'state');m.add_company('A','600276','恒瑞示例',['Synthetic'],'Synthetic test fixture')
 with m.db() as db:
  for n in range(250):
   d={'title':'=SYNTHETIC\nmultiline','sponsor':'Synthetic','interventions':[{'name':'Drug'}],'conditions':['Condition'],'phases':['PHASE3'],'status':'RECRUITING','primary_completion':{},'source_updated':'2026-10-17','source_url':'https://clinicaltrials.gov/study/NCT00000001'}
   db.execute('INSERT INTO trials VALUES (?,?,?,?,?)',('A:600276',f'NCT{n:08}',json.dumps(d),'synthetic-hash','2026-10-17'))
 return m

def test_export_includes_all_filtered_rows_with_provenance(market):
 result=create_export(market,{'q':'恒瑞','phase':'PHASE3'})
 stream,_=export_file(market,result['export_id'],'observations.csv')
 with stream:rows=list(csv.DictReader(io.StringIO(stream.read().decode('utf-8-sig'))))
 assert len(rows)==250==result['row_count']
 assert rows[0]['trial_title'].startswith("'=") and rows[0]['ownership_status']=='unverified'
 assert rows[0]['source_updated']=='2026-10-17' and rows[0]['normalized_record_sha256']=='synthetic-hash'

def test_tampering_manifest_or_file_fails_closed(market):
 result=create_export(market,{});folder=market.root/'exports'/result['export_id'];(folder/'observations.csv').write_text('truncated')
 with pytest.raises(ValueError):export_file(market,result['export_id'],'observations.csv')
 result=create_export(market,{});(market.root/'exports'/result['export_id']/'manifest.json').write_text('{}')
 with pytest.raises(ValueError):export_file(market,result['export_id'],'manifest.json')
 with pytest.raises(ValueError):export_file(market,'../escape','observations.csv')

def test_failed_export_is_not_registered(market):
 with pytest.raises(ValueError):create_export(market,{},max_rows=10)
 with market.db() as db:assert db.execute('SELECT count(*) FROM market_exports').fetchone()[0]==0
 assert not list((market.root/'exports').iterdir())

def test_http_download_headers_and_manifest(market):
 app=Workbench(market.root.parent.parent,market.state);result=create_export(market,{'phase':'PHASE2'})
 server=ThreadingHTTPServer(('127.0.0.1',0),handler(app));threading.Thread(target=server.serve_forever,daemon=True).start()
 try:
  response=urllib.request.urlopen(f'http://127.0.0.1:{server.server_port}/market/export-file?id={result["export_id"]}&file=manifest.json')
  assert 'attachment;' in response.headers['Content-Disposition']
  assert json.load(response)['row_count']==0
 finally:server.shutdown();server.server_close()

@pytest.mark.parametrize('source',['=1+1','+1','-1','@SUM(A1)','  =2','\t=2','\r\n@x'])
def test_spreadsheet_formulas_are_escaped(source):assert text_cell(source)=="'"+source

def test_download_uses_same_verified_snapshot_after_file_changes(market):
 import hashlib
 result=create_export(market,{})
 stream,meta=export_file(market,result['export_id'],'observations.csv')
 (market.root/'exports'/result['export_id']/'observations.csv').write_text('tampered after verification')
 with stream:
  with pytest.raises((io.UnsupportedOperation,AttributeError)):stream.write(b'cannot overwrite snapshot')
  assert hashlib.sha256(stream.read()).hexdigest()==meta['sha256']

def test_export_read_transaction_survives_concurrent_promotion(market):
 with market.db() as db:db.execute('PRAGMA journal_mode=WAL')
 chunks=iter(trial_csv(market,{}));header=next(chunks);first=next(chunks)
 with market.db() as db:
  db.execute('DELETE FROM trials');db.execute("UPDATE companies SET name='New snapshot company'")
 rows=list(csv.DictReader(io.StringIO((header+first+''.join(chunks)).lstrip('\ufeff'))))
 assert len(rows)==250 and {r['company_name'] for r in rows}=={'恒瑞示例'}
 assert market.count('trials')==0

def test_export_page_reads_existing_export_without_creating_another(market):
 result=create_export(market,{});app=Workbench(market.root.parent.parent,market.state)
 before=set((market.root/'exports').iterdir())
 assert '250 条' in export_page(app,{'id':result['export_id']})
 with pytest.raises(ValueError):export_page(app,{})
 assert set((market.root/'exports').iterdir())==before

def test_export_byte_limit_removes_incomplete_files(market,monkeypatch):
 import vbt.market_export as module
 monkeypatch.setattr(module,'MAX_EXPORT_BYTES',10)
 with pytest.raises(ValueError):create_export(market,{})
 with market.db() as db:assert db.execute('SELECT count(*) FROM market_exports').fetchone()[0]==0
 assert not list((market.root/'exports').iterdir())

def test_http_export_creation_requires_post_origin_and_token(market):
 from urllib.parse import urlencode
 from urllib.error import HTTPError
 app=Workbench(market.root.parent.parent,market.state)
 server=ThreadingHTTPServer(('127.0.0.1',0),handler(app));threading.Thread(target=server.serve_forever,daemon=True).start()
 origin=f'http://127.0.0.1:{server.server_port}'
 try:
  with pytest.raises(HTTPError) as error:urllib.request.urlopen(origin+'/market/export')
  assert error.value.code==404
  with pytest.raises(HTTPError) as error:urllib.request.urlopen(urllib.request.Request(origin+'/market/export',data=urlencode({'token':app.token}).encode()))
  assert error.value.code==403
  with pytest.raises(HTTPError) as error:urllib.request.urlopen(urllib.request.Request(origin+'/market/export',data=b'token=invalid',headers={'Origin':origin}))
  assert error.value.code==403
  with urllib.request.urlopen(urllib.request.Request(origin+'/market/export',data=urlencode({'token':app.token,'phase':'PHASE3'}).encode(),headers={'Origin':origin})) as response:
   assert response.status==200 and '/market/export?id=' in response.url
   assert '250 条'.encode() in response.read()
   saved_url=response.url
  with urllib.request.urlopen(saved_url) as response:assert response.status==200
  with market.db() as db:assert db.execute('SELECT count(*) FROM market_exports').fetchone()[0]==1
 finally:server.shutdown();server.server_close()

def test_http_download_cannot_race_source_file_verification(market,monkeypatch):
 import hashlib
 import vbt.market_export as module
 app=Workbench(market.root.parent.parent,market.state);result=create_export(market,{})
 original=module.export_file
 def race(*args):
  snapshot,meta=original(*args)
  (market.root/'exports'/result['export_id']/'observations.csv').write_text('changed after verification')
  return snapshot,meta
 monkeypatch.setattr(module,'export_file',race)
 server=ThreadingHTTPServer(('127.0.0.1',0),handler(app));threading.Thread(target=server.serve_forever,daemon=True).start()
 try:
  with urllib.request.urlopen(f'http://127.0.0.1:{server.server_port}/market/export-file?id={result["export_id"]}&file=observations.csv') as response:
   assert response.status==200 and hashlib.sha256(response.read()).hexdigest()==result['sha256']
 finally:server.shutdown();server.server_close()
