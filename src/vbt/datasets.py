import csv, json, time, re
from pathlib import Path
import pyarrow.parquet as pq
from .models import DatasetVersion
from .io import digest, within
from .geo import summary

ALIASES = {'dataset_id':['dataset_id','accession','dataset'], 'source_url':['source_url','url','source'],
           'local_path':['local_path','path','file','filename'], 'size_bytes':['size_bytes','bytes','size'],
           'sha256':['sha256','sha256sum'], 'download_status':['download_status','status'], 'version':['version','release']}

def read_manifest(path, data_root):
    path = Path(path).resolve()
    if path.suffix in ('.csv','.tsv'):
        with path.open() as f: raw=list(csv.DictReader(f, delimiter='\t' if path.suffix=='.tsv' else ','))
    else:
        raw=json.loads(path.read_text())
        if isinstance(raw,dict): raw=raw.get('datasets',raw.get('files',raw.get('downloads',raw)))
    if not isinstance(raw,list): raise ValueError('Manifest must contain a list (datasets/files/downloads)')
    items=[]
    for row in raw:
        d={k:next((row[a] for a in aliases if a in row),None) for k,aliases in ALIASES.items()}
        if any(d[k] is None for k in d): raise ValueError(f'Missing required manifest fields: {[k for k,v in d.items() if v is None]}')
        p=Path(d['local_path'])
        if not p.is_absolute():
            # Support manifest-relative and project-relative downloader receipts.
            candidates={x.resolve() for x in (path.parent/p,Path(data_root)/p,Path(data_root).parent/p) if x.is_file()}
            if len(candidates)!=1: raise ValueError(f'Ambiguous or missing manifest path: {p}; use an absolute path')
            p=candidates.pop()
        d['local_path']=str(within(p,data_root))
        d['download_status']=str(d['download_status']).lower()
        if d['download_status'] in ('completed','downloaded','verified','ok'): d['download_status']='complete'
        d['sha256']=str(d['sha256']).lower()
        if not re.fullmatch(r'[A-Za-z0-9_.:+-]+',str(d['version'])):
            d['source_version']=d['version']
            d['version']='sha256-'+d['sha256'][:16]
        d['format']=row.get('format') or ('geo_matrix' if str(p).endswith('series_matrix.txt.gz') else 'geo_platform' if str(p).endswith('.annot.gz') else p.suffix.lstrip('.'))
        for k in ('license','evidence_kind','required_columns'):
            if row.get(k) is not None and row[k] != '': d[k]=row[k]
        if isinstance(d.get('required_columns'),str): d['required_columns']=json.loads(d['required_columns'])
        items.append(DatasetVersion.model_validate(d))
    return items

def validate(ds, data_root, settle_seconds=0.15):
    p=within(ds.local_path,data_root)
    if not p.is_file(): raise ValueError(f'Missing data file: {p}')
    if any(x in p.name.lower() for x in ('.part','.tmp','.download')): raise ValueError('Unfinished transfer filename')
    before=p.stat()
    if before.st_size != ds.size_bytes: raise ValueError('Size mismatch')
    if digest(p) != ds.sha256: raise ValueError('SHA-256 mismatch')
    detail={}
    if ds.format=='geo_matrix': detail=summary(p)
    elif ds.format=='geo_platform':
        from .geo import read_platform
        detail=read_platform(p, genes=set())['summary']
    elif ds.format=='parquet':
        pf=pq.ParquetFile(p); names=pf.schema_arrow.names
        for batch in pf.iter_batches(batch_size=65536): batch.validate(full=True)
        if not set(ds.required_columns).issubset(names): raise ValueError('Missing Parquet columns')
        detail={'rows':pf.metadata.num_rows,'columns':names}
    elif ds.format in ('csv','tsv'):
        with p.open(newline='') as f:
            r=csv.DictReader(f,delimiter='\t' if ds.format=='tsv' else ',')
            if not r.fieldnames or len(r.fieldnames)!=len(set(r.fieldnames)): raise ValueError('Invalid CSV header')
            if not set(ds.required_columns).issubset(r.fieldnames): raise ValueError('Missing CSV columns')
            count=0
            for row in r:
                if None in row or None in row.values(): raise ValueError('Truncated CSV row')
                count+=1
            detail={'rows':count,'columns':r.fieldnames}
    elif ds.format=='json':
        obj=json.loads(p.read_text())
        if ds.required_columns and (not isinstance(obj,dict) or not set(ds.required_columns).issubset(obj)): raise ValueError('Missing JSON keys')
    time.sleep(settle_seconds)
    after=p.stat()
    if (before.st_size,before.st_mtime_ns,before.st_ino)!=(after.st_size,after.st_mtime_ns,after.st_ino): raise ValueError('File changed during validation')
    return detail
