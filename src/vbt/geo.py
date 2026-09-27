"""Strict streaming GEO series-matrix reader; never derives clinical labels."""
import csv, gzip, math
from pathlib import Path

def read_geo(path, probes=None, metadata_only=False):
    metadata, samples, values = {}, [], {}
    started = ended = False; n_rows = 0; seen = set()
    opener = gzip.open if str(path).endswith('.gz') else open
    with opener(path, 'rt', encoding='utf-8') as f:
        for row in csv.reader(f, delimiter='\t'):
            if not row: continue
            tag = row[0]
            if tag == '!series_matrix_table_begin':
                if started: raise ValueError('Duplicate GEO table')
                started = True; continue
            if tag == '!series_matrix_table_end': ended = True; continue
            if tag.startswith('!Sample_'):
                metadata.setdefault(tag, []).append(row[1:]); continue
            if started and not ended:
                if not samples:
                    if tag != 'ID_REF': raise ValueError('GEO table missing ID_REF header')
                    samples = row[1:]
                    if not samples or len(samples) != len(set(samples)): raise ValueError('Duplicate/empty GEO sample IDs')
                else:
                    if len(row) != len(samples)+1: raise ValueError('Truncated or malformed GEO row')
                    if tag in seen: raise ValueError('Duplicate GEO probe ID')
                    seen.add(tag); n_rows += 1
                    # Validate all cells, even when retaining only selected probes.
                    vals = [float(v) if v.lower() not in ('null','na','nan','') else float('nan') for v in row[1:]]
                    if any(math.isinf(v) for v in vals): raise ValueError('Infinite expression value')
                    if not metadata_only and (probes is None or tag in probes): values[tag] = vals
    if not started or not ended or not samples: raise ValueError('Incomplete GEO matrix (missing table boundary)')
    accessions = metadata.get('!Sample_geo_accession', [])
    if not accessions or accessions[0] != samples: raise ValueError('GEO sample metadata/header mismatch')
    for fields in metadata.values():
        if any(len(x) != len(samples) for x in fields): raise ValueError('GEO metadata/sample count mismatch')
    return {'samples':samples, 'metadata':metadata, 'values':values, 'n_rows':n_rows}

def summary(path):
    d = read_geo(path, metadata_only=True)
    return {'sample_count':len(d['samples']), 'expression_rows':d['n_rows'],
            'platforms':sorted(set(d['metadata'].get('!Sample_platform_id', [[]])[0])),
            'characteristic_fields':sorted({v.split(':',1)[0] for row in d['metadata'].get('!Sample_characteristics_ch1',[]) for v in row}),
            'has_expression':bool(d['n_rows'])}

def export_annotations(path, output):
    d = read_geo(path, metadata_only=True)
    # Duplicate characteristic fields preserved as separate source rows, never overwritten.
    with open(output, 'w', newline='') as f:
        w = csv.writer(f); w.writerow(['sample_id','source_field','source_occurrence','raw_value'])
        for field, rows in d['metadata'].items():
            for occurrence, row in enumerate(rows, 1):
                for sid, value in zip(d['samples'], row): w.writerow([sid,field,occurrence,value])

def read_platform(path, genes=None):
    started=ended=False;header=None;rows=[];count=0
    with gzip.open(path,'rt') as f:
        for row in csv.reader(f,delimiter='\t'):
            if not row:continue
            if row[0]=='!platform_table_begin':started=True;continue
            if row[0]=='!platform_table_end':ended=True;continue
            if started and not ended:
                if header is None:
                    header=row
                    if 'ID' not in header or 'Gene symbol' not in header:raise ValueError('Platform missing ID/Gene symbol')
                else:
                    if len(row)!=len(header):raise ValueError('Malformed platform annotation row')
                    count+=1;item=dict(zip(header,row))
                    if genes is None or item['Gene symbol'] in genes:rows.append(item)
    if not started or not ended or not header:raise ValueError('Incomplete platform annotation')
    return {'rows':rows,'summary':{'feature_rows':count,'columns':header}}
