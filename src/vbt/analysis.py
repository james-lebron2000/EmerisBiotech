"""Pre-specified patient-level UC comparisons. No label inference or pooling."""
import csv
from pathlib import Path
import numpy as np
from scipy.stats import mannwhitneyu, false_discovery_control
import pyarrow as pa
import pyarrow.parquet as pq
from .geo import read_geo, export_annotations
from .io import write

SAMPLE_COLUMNS={'sample_id','patient_id','baseline','response','include','exclusion_reason','raw_response','source_ref'}
PROBE_COLUMNS={'probe_id','gene_symbol','platform_id','source_ref'}

def table(path,required):
    with open(path,newline='') as f:
        r=csv.DictReader(f)
        if not required.issubset(r.fieldnames or []): raise ValueError(f'Missing mapping columns: {sorted(required-set(r.fieldnames or []))}')
        return list(r)

def cohort_analysis(cohort, inputs, plan, out):
    ds=inputs[cohort.matrix_key]
    matrix=Path(ds['local_path'])
    d=read_geo(matrix,metadata_only=True)
    export_annotations(matrix,out/f'{cohort.name}_source_annotations.csv')
    needs=[]
    for field in ('sample_key','probe_key','endpoint_definition','endpoint_source','expression_scale','platform_id','mapping_reviewed_by','mapping_reviewed_at'):
        if not getattr(cohort,field): needs.append(field)
    if not d['n_rows']: needs.append('nonempty_expression_matrix')
    if needs: return [], {'cohort':cohort.name,'matrix_samples':len(d['samples']),'status':'blocked','missing':needs}
    samples=table(inputs[cohort.sample_key]['local_path'],SAMPLE_COLUMNS)
    probes=table(inputs[cohort.probe_key]['local_path'],PROBE_COLUMNS)
    byid={s['sample_id']:s for s in samples}
    if len(byid)!=len(samples): raise ValueError('Duplicate sample IDs in clinical mapping')
    if set(byid)!=set(d['samples']): raise ValueError('Every matrix sample must have an explicit include/exclude mapping')
    platforms=set(d['metadata'].get('!Sample_platform_id',[[]])[0])
    if platforms!={cohort.platform_id}: raise ValueError('Matrix platform does not match frozen cohort platform')
    eligible=[]; flow=[]; seen=set()
    for sid in d['samples']:
        s=byid[sid]
        if s['include'] not in ('true','false') or s['baseline'] not in ('true','false','unknown'):
            raise ValueError('include must be true/false; baseline true/false/unknown')
        if s['response'] not in ('responder','nonresponder','unknown'): raise ValueError('Unknown response mapping value')
        reason=''
        if s['include']=='false':
            reason=s['exclusion_reason']
            if not reason: raise ValueError('Excluded samples require a reason')
        elif s['baseline']!='true': reason='not_confirmed_baseline'
        elif s['response']=='unknown': reason='missing_response'
        elif not s['patient_id'].strip(): reason='missing_patient_id'
        elif not s['raw_response'].strip() or not s['source_ref'].strip(): reason='missing_label_provenance'
        if not reason:
            if s['patient_id'] in seen: raise ValueError('Duplicate patient among baseline samples; resolve technical replicates before analysis')
            seen.add(s['patient_id']);eligible.append(sid)
        flow.append({'sample_id':sid,'patient_id':s['patient_id'],'included':not bool(reason),'reason':reason,'response':s['response'],'source_ref':s['source_ref']})
    write(out/f'{cohort.name}_sample_flow.json',flow)
    mapping={}; probe_seen=set()
    for p in probes:
        if p['platform_id']!=cohort.platform_id: raise ValueError('Probe annotation platform mismatch')
        if not p['source_ref'].strip(): raise ValueError('Probe mapping needs provenance')
        if p['probe_id'] in probe_seen: raise ValueError('Duplicate/ambiguous probe annotation')
        probe_seen.add(p['probe_id'])
        if any(x in p['gene_symbol'] for x in ('///',';',',')): raise ValueError('Ambiguous probe-to-gene mapping')
        if p['gene_symbol'] in plan.genes: mapping.setdefault(p['gene_symbol'],[]).append(p['probe_id'])
    wanted={p for ps in mapping.values() for p in ps}
    full=read_geo(matrix,probes=wanted)
    if wanted-set(full['values']): raise ValueError('Annotated selected probes absent from matrix')
    indices={s:i for i,s in enumerate(d['samples'])}
    results=[]; expression_rows=[]
    for gene in plan.genes:
        ids=mapping.get(gene,[])
        if not ids:
            results.append({'row_id':f'{cohort.name}:{gene}','cohort':cohort.name,'gene':gene,'status':'non_estimable','reason':'missing_probe_mapping'});continue
        # Paper: arithmetic mean across all mapped probes on the supplied processed scale.
        expr=np.mean(np.array([full['values'][p] for p in ids]),axis=0)
        groups={'responder':[],'nonresponder':[]}; missing=0
        for sid in eligible:
            value=float(expr[indices[sid]])
            finite=bool(np.isfinite(value));missing+=not finite
            expression_rows.append({'cohort':cohort.name,'gene':gene,'sample_id':sid,'patient_id':byid[sid]['patient_id'],'response':byid[sid]['response'],'value':value if finite else None})
            if finite: groups[byid[sid]['response']].append(value)
        a,b=np.array(groups['nonresponder']),np.array(groups['responder'])
        row={'row_id':f'{cohort.name}:{gene}','cohort':cohort.name,'gene':gene,'n_nonresponder':len(a),'n_responder':len(b),'missing_expression':missing,'n_probes':len(ids),'endpoint':cohort.endpoint_definition,'expression_scale':cohort.expression_scale}
        if min(len(a),len(b))<2:
            row.update(status='non_estimable',reason='fewer_than_two_patients_in_a_group');results.append(row);continue
        rng=np.random.default_rng(plan.seed)
        boot=[]
        for _ in range(plan.bootstrap_replicates): boot.append(float(np.median(rng.choice(a,len(a)))-np.median(rng.choice(b,len(b)))))
        ci=np.quantile(boot,[.025,.975]);test=mannwhitneyu(a,b,alternative='two-sided',method='asymptotic')
        row.update(status='estimated',median_nonresponder=float(np.median(a)),median_responder=float(np.median(b)),
                   iqr_nonresponder=np.quantile(a,[.25,.75]).tolist(),iqr_responder=np.quantile(b,[.25,.75]).tolist(),
                   median_difference=float(np.median(a)-np.median(b)),ci_low=float(ci[0]),ci_high=float(ci[1]),
                   cliffs_delta=float(2*test.statistic/(len(a)*len(b))-1),p_value=float(test.pvalue),
                   method='two-sided Mann-Whitney U, asymptotic tie correction; 10000+ percentile bootstrap median difference')
        results.append(row)
    if expression_rows: pq.write_table(pa.Table.from_pylist(expression_rows),out/f'{cohort.name}_expression.parquet')
    return results,{'cohort':cohort.name,'matrix_samples':len(d['samples']),'eligible_patients':len(eligible),'excluded':len(d['samples'])-len(eligible),'status':'analyzed'}

def run_geo(plan, inputs, out):
    all_results=[];flows=[];gaps=[]
    for c in plan.cohorts:
        try:
            rows,flow=cohort_analysis(c,inputs,plan,out)
            all_results.extend(rows);flows.append(flow)
            if flow['status']=='blocked': gaps.append(flow)
        except (ValueError,KeyError) as e:
            gaps.append({'cohort':c.name,'status':'blocked','reason':str(e)})
    # Keep the full prespecified family, including unperformed tests. Unperformed
    # p=1 entries only affect the adjustment; they are NEVER reported as negative.
    byid={r['row_id']:r for r in all_results}
    ordered=[f'{c.name}:{g}' for c in plan.cohorts for g in plan.genes]
    ps=[byid.get(k,{}).get('p_value',1.0) for k in ordered]
    qs=false_discovery_control(ps,method='bh') if ps else []
    for k,q in zip(ordered,qs):
        if k in byid and 'p_value' in byid[k]: byid[k]['q_value']=float(q)
    for k in ordered:
        if k not in byid: all_results.append({'row_id':k,'cohort':k.split(':')[0],'gene':k.split(':')[1],'status':'non_estimable','reason':'cohort_blocked'})
    write(out/'results.json',all_results);write(out/'sample_flow.json',flows);write(out/'data_gaps.json',gaps)
    if all_results:
        # from_pylist infers schema from the FIRST row; unify keys to preserve all fields.
        keys=sorted({k for r in all_results for k in r})
        pq.write_table(pa.Table.from_pylist([{k:r.get(k) for k in keys} for r in all_results]),out/'results.parquet')
    return {'execution':'blocked' if gaps or any(r['status']!='estimated' for r in all_results) else 'succeeded', 'gaps':gaps}
