"""Snapshot-consistent, provenance-bearing CSV for analyst workflows."""
import csv,io,json

MAX_EXPORT_BYTES=512*1024*1024
MAX_MANIFEST_BYTES=1024*1024
from .io import now

COLUMNS=['company_id','company_name','nct_id','trial_title','lead_sponsor','interventions','conditions','observed_trial_phases','registry_status','primary_completion_date','source_updated','retrieved_at','collection_run_id','normalized_record_sha256','source_url','ownership_status','exported_at']

def text_cell(value):
 text=str(value if value is not None else '')
 # Protect spreadsheet readers without executing or interpreting source strings.
 return "'"+text if text.lstrip().startswith(('=','+','-','@')) else text

def trial_csv(market,q):
 where,params=market.trial_filters(q.get('market',''),q.get('company',''),q.get('q','').strip(),q.get('phase',''),q.get('status',''))
 stream=io.StringIO(newline='');writer=csv.writer(stream);exported=now()
 writer.writerow(COLUMNS);yield '\ufeff'+stream.getvalue();stream.seek(0);stream.truncate(0)
 with market.db() as db:
  # A stable read transaction prevents duplicate/missing pages during promotion.
  db.execute('BEGIN')
  rows=db.execute("SELECT trials.*,(SELECT name FROM companies c WHERE c.id=trials.company) AS company_name,(SELECT last_run FROM trial_lineage l WHERE l.company=trials.company AND l.nct=trials.nct) AS last_run FROM trials"+where+" ORDER BY json_extract(data,'$.source_updated') DESC,company,nct",params)
  for row in rows:
   d=json.loads(row['data']);values=[row['company'],row['company_name'],row['nct'],d.get('title'),d.get('sponsor'),'; '.join(x['name'] for x in d.get('interventions',[])),'; '.join(d.get('conditions',[])),'; '.join(d.get('phases',[])),d.get('status'),d.get('primary_completion',{}).get('date'),d.get('source_updated'),row['observed'],row['last_run'],row['hash'],d.get('source_url'),'unverified',exported]
   writer.writerow([text_cell(v) for v in values]);yield stream.getvalue();stream.seek(0);stream.truncate(0)


def init_schema(market):
 with market.db() as db:db.execute('CREATE TABLE IF NOT EXISTS market_exports (id TEXT PRIMARY KEY,created TEXT,manifest_sha256 TEXT)')


def create_export(market,q,max_rows=300_000):
 import hashlib,uuid,shutil
 init_schema(market);export_id=uuid.uuid4().hex;folder=market.root/'exports'/export_id;folder.mkdir(parents=True)
 filters={k:str(q[k]) for k in ('market','company','q','phase','status') if q.get(k)}
 try:
  path=folder/'observations.csv';digest=hashlib.sha256();size=0
  with path.open('wb') as file:
   for index,chunk in enumerate(trial_csv(market,filters)):
    if index>max_rows:raise ValueError('导出超过300000条，请缩小公司、市场或阶段范围')
    raw=chunk.encode();size+=len(raw)
    if size>MAX_EXPORT_BYTES:raise ValueError('导出超过512MiB，请缩小筛选范围')
    digest.update(raw);file.write(raw)
  # Count logical CSV records, including quoted multiline source values.
  with path.open(encoding='utf-8-sig',newline='') as file:count=sum(1 for _ in csv.reader(file))-1
  manifest={'schema_version':1,'export_id':export_id,'created':now(),'filters':filters,'row_count':count,'file':'observations.csv','bytes':size,'sha256':digest.hexdigest(),'scope':'Company-trial registry observations; ownership and full pipeline completeness unverified.','formula_cells':'Potential spreadsheet formulas are escaped with a leading apostrophe.'}
  serialized=json.dumps(manifest,ensure_ascii=False,sort_keys=True,indent=2).encode();(folder/'manifest.json').write_bytes(serialized)
  with market.db() as db:db.execute('INSERT INTO market_exports VALUES (?,?,?)',(export_id,manifest['created'],hashlib.sha256(serialized).hexdigest()))
  return manifest
 except Exception:
  shutil.rmtree(folder);raise


def export_file(market,export_id,filename):
 """Return a verified, read-only snapshot; the caller must close the stream.

 Verification and serving share frozen bytes. A later replacement or in-place
 mutation of the source export cannot change what the client receives.
 """
 import hashlib,re,tempfile
 from .io import within
 if not re.fullmatch('[a-f0-9]{32}',export_id) or filename not in ('observations.csv','manifest.json'):raise ValueError('导出附件不存在')
 init_schema(market)
 with market.db() as db:row=db.execute('SELECT * FROM market_exports WHERE id=?',(export_id,)).fetchone()
 if not row:raise ValueError('导出记录不存在或未完成')
 folder=within(market.root/'exports'/export_id,market.root/'exports');manifest_path=within(folder/'manifest.json',folder)
 with manifest_path.open('rb') as file:raw=file.read(MAX_MANIFEST_BYTES+1)
 if len(raw)>MAX_MANIFEST_BYTES or hashlib.sha256(raw).hexdigest()!=row['manifest_sha256']:raise ValueError('导出清单已变化，拒绝提供失效附件')
 manifest=json.loads(raw)
 if manifest.get('export_id')!=export_id or manifest.get('file')!='observations.csv' or type(manifest.get('bytes')) is not int or not 0<=manifest['bytes']<=MAX_EXPORT_BYTES:raise ValueError('导出清单范围不正确')
 snapshot=tempfile.SpooledTemporaryFile(max_size=8*1024*1024,mode='w+b')
 try:
  if filename=='manifest.json':snapshot.write(raw)
  else:
   path=within(folder/filename,folder);digest=hashlib.sha256();size=0
   with path.open('rb') as file:
    for chunk in iter(lambda:file.read(1024*1024),b''):
     size+=len(chunk)
     if size>manifest['bytes']:raise ValueError('导出文件大小已变化')
     digest.update(chunk);snapshot.write(chunk)
   if size!=manifest['bytes'] or digest.hexdigest()!=manifest.get('sha256'):raise ValueError('导出文件校验失败，请重新生成')
  snapshot.seek(0)
  return io.BufferedReader(snapshot),manifest
 except Exception:snapshot.close();raise


def export_page(app,q):
 """Read an existing export; creation is a CSRF-protected POST operation."""
 import html
 from datetime import datetime,timedelta,timezone
 from urllib.parse import urlencode
 from .market import Market
 from .market_view import PHASES,TRIAL_STATES
 ident=q.get('id','')
 market=Market(app.workspace,app.state)
 with export_file(market,ident,'manifest.json')[0] as file:manifest=json.load(file)
 esc=lambda value:html.escape(str(value),quote=True)
 filters=manifest.get('filters',{});back='/market?'+urlencode(filters)
 names={r['id']:r['name'] for r in market.rows('companies')}
 labels={'market':'市场','company':'公司','q':'关键词','phase':'登记阶段','status':'登记状态'}
 display={'company':names,'phase':PHASES,'status':TRIAL_STATES,'market':{'US':'美股','A':'A股','HK':'港股'}}
 chips=''.join('<span class="badge" title="'+esc(v)+'">'+esc(labels[k])+' · '+esc(display.get(k,{}).get(v,v))+'</span>' for k,v in filters.items() if k in labels) or '<span class="badge">全部已采集登记</span>'
 created=manifest['created']
 try:
  stamp=datetime.fromisoformat(created.replace('Z','+00:00'))
  if stamp.tzinfo:created=stamp.astimezone(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M')+' UTC+8'
 except ValueError:pass
 size=manifest['bytes']/1024
 body='<header class="page-header"><div><span class="eyebrow">RESEARCH EXPORT</span><h1>导出已就绪</h1><p class="lead">已固定筛选结果，可下载并核验来源。</p></div><a class="button secondary" href="'+esc(back)+'">返回本次筛选</a></header>'
 body+='<div class="export-layout"><section class="card export-summary"><span class="badge succeeded">快照已生成</span><h2>'+str(manifest['row_count'])+' 条临床登记关联</h2><div class="filter-chips">'+chips+'</div><p>包含来源链接、源更新日期、抓取时间及批次。导出结果固定为本次数据库读取快照，不随后续抓取变化。</p><div class="export-actions"><a class="button" href="/market/export-file?id='+ident+'&amp;file=observations.csv">下载 CSV</a><a class="button secondary" href="/market/export-file?id='+ident+'&amp;file=manifest.json">下载校验清单</a></div><p class="muted">CSV · '+format(size,'.1f')+' KiB · UTF-8 · 可在电子表格中查看</p></section>'
 body+='<section class="card"><h2>快照与完整性</h2><dl class="snapshot-facts"><dt>生成时间</dt><dd><time title="'+esc(manifest['created'])+'" datetime="'+esc(manifest['created'])+'">'+esc(created)+'</time></dd><dt>快照编号</dt><dd class="ref">'+esc(ident)+'</dd><dt>CSV SHA-256</dt><dd class="ref">'+esc(manifest['sha256'])+'</dd></dl><p class="data-note">下载时校验已保存文件与清单；校验失败的附件会被拒绝提供。</p></section></div>'
 return body+'<p class="notice">导出内容是公开临床登记关联，包含历史记录；不确认药物权益、完整管线或临床结局成功。</p>'
