"""Generate clearly synthetic ENGINEERING fixtures, never fake patient evidence."""
import csv, gzip, json, sys
from pathlib import Path
from vbt.io import digest, write

def build(dest):
    dest=Path(dest).resolve();dest.mkdir(parents=True,exist_ok=True)
    sample_ids=[f'SYN{i:02}' for i in range(12)]
    matrix=dest/'SYNTHETIC_series_matrix.txt.gz'
    content='!Sample_geo_accession\t'+'\t'.join(sample_ids)+'\n!Sample_platform_id\t'+'\t'.join(['SYN_GPL']*12)+'\n!series_matrix_table_begin\nID_REF\t'+'\t'.join(sample_ids)+'\n'
    for name,values in [('p1',[1,2,1.5,2.2,1.8,2.5,4,5,4.5,5.2,4.8,5.5]),('p2',[2,3,2.5,3.2,2.8,3.5,5,6,5.5,6.2,5.8,6.5]),('p3',[2,4,3,2.5,3.5,4.5,3,4,2,4.5,2.5,3.5])]:
        content+=name+'\t'+'\t'.join(map(str,values))+'\n'
    # Deterministic bytes (gzip mtime=0).
    matrix.write_bytes(gzip.compress((content+'!series_matrix_table_end\n').encode(),mtime=0))
    samples=dest/'samples.csv'
    with samples.open('w',newline='') as f:
        w=csv.writer(f);w.writerow(['sample_id','patient_id','baseline','response','include','exclusion_reason','raw_response','source_ref'])
        for i,sid in enumerate(sample_ids):w.writerow([sid,f'SYN_PATIENT_{i}','true','responder' if i<6 else 'nonresponder','true','','synthetic label','synthetic fixture'])
    probes=dest/'probes.csv'
    with probes.open('w',newline='') as f:
        w=csv.writer(f);w.writerow(['probe_id','gene_symbol','platform_id','source_ref']);w.writerows([['p1','OSMR','SYN_GPL','synthetic fixture'],['p2','OSMR','SYN_GPL','synthetic fixture'],['p3','OSM','SYN_GPL','synthetic fixture']])
    datasets=[]
    for id,p,fmt in [('SYN_MATRIX',matrix,'geo_matrix'),('SYN_SAMPLES',samples,'csv'),('SYN_PROBES',probes,'csv')]:
        datasets.append({'dataset_id':id,'version':'v1','source_url':'synthetic://engineering-fixture','local_path':str(p),'size_bytes':p.stat().st_size,'sha256':digest(p),'download_status':'complete','format':fmt,'evidence_kind':'synthetic','license':'local engineering fixture'})
    write(dest/'manifest.json',datasets)
    plan={'question':'SYNTHETIC ENGINEERING TEST: baseline OSMR/OSM group comparison','evidence_kind':'synthetic','workflow':'geo_uc','expected_direction':'higher_in_nonresponders','cohorts':[{'name':'SYN_UC','matrix_key':'SYN_MATRIX@v1','sample_key':'SYN_SAMPLES@v1','probe_key':'SYN_PROBES@v1','endpoint_definition':'Synthetic responder label; no clinical meaning','endpoint_source':'synthetic fixture','expression_scale':'synthetic arbitrary units','platform_id':'SYN_GPL','mapping_reviewed_by':'engineering-fixture-generator','mapping_reviewed_at':'2026-09-25'}]}
    write(dest/'plan.json',plan)
    return dest
if __name__=='__main__': print(build(sys.argv[1]))
