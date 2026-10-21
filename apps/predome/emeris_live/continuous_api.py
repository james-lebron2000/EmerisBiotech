"""Read-only continuous-window research release, no model unpickling or paid routes."""
from pathlib import Path
import json,hashlib
from .predome_statistics import sha
ROOT=Path(__file__).resolve().parents[1];DATA=ROOT/'predome_data/continuous_v4';REPORT='docs/Predome_连续队列回测与前瞻_20261019.md'
OPTIMIZATION='docs/Predome_70目标与优化实验方案_20261019.md'
def read_continuous():
    envelope=json.loads((DATA/'release.json').read_text());record=envelope['record']
    if sha(record)!=envelope['sha256']:raise ValueError('Continuous release hash failed')
    if not record.get('artifact_manifest'):raise ValueError('No artifact manifest')
    seen=set()
    for item in record['artifact_manifest']:
        value=item['path'];p=(ROOT/value).resolve()
        if Path(value).is_absolute() or not p.is_relative_to(ROOT) or not p.is_file() or p in seen:raise ValueError('Invalid continuous artifact path')
        seen.add(p);h=hashlib.sha256()
        with p.open('rb') as f:
            for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
        if h.hexdigest()!=item['sha256']:raise ValueError('Continuous artifact hash failed')
    return envelope|{'integrity_verified':True}
def report_path(envelope):
    if not envelope.get('integrity_verified') or REPORT not in [p['path'] for p in envelope['record']['artifact_manifest']]:raise ValueError('Report not bound to release')
    return ROOT/REPORT
def optimization_path(envelope):
    if not envelope.get('integrity_verified') or OPTIMIZATION not in [p['path'] for p in envelope['record']['artifact_manifest']]:raise ValueError('Optimization report not bound to release')
    return ROOT/OPTIMIZATION
