"""Local-only GEO mapping from explicit rules. Does not infer patient IDs or outcomes."""
import csv
from pathlib import Path
from .geo import read_geo, read_platform
from .datasets import validate
from .io import digest, write, now, ensure_storage
from .models import DatasetVersion, ResearchPlan, Cohort


def prepare_geo(registry, matrix_key, platform_key, rules, output, data_root):
    output=Path(output).resolve()
    if output.exists(): raise ValueError('Mapping output must be a new directory to preserve versions')
    matrix=registry.get(matrix_key);annotation=registry.get(platform_key)
    validate(matrix,data_root);validate(annotation,data_root)
    if matrix.evidence_kind!=annotation.evidence_kind:raise ValueError('Input evidence kinds differ')
    if matrix.format!='geo_matrix' or annotation.format!='geo_platform':raise ValueError('Requires GEO matrix and platform annotation')
    required={'cohort','patient_field','baseline_field','baseline_value','response_field','response_values','include_equals','endpoint_definition','endpoint_source','expression_scale','platform_id'}
    if not required.issubset(rules):raise ValueError('Missing mapping rules: '+str(required-set(rules)))
    if rules['platform_id']!=annotation.dataset_id:raise ValueError('Rules/platform accession mismatch')
    d=read_geo(matrix.local_path,metadata_only=True)
    if set(d['metadata'].get('!Sample_platform_id',[[]])[0])!={rules['platform_id']}:raise ValueError('Matrix/platform mismatch')
    mapped=[]
    for i,sid in enumerate(d['samples']):
        fields={}
        for source_row in d['metadata'].get('!Sample_characteristics_ch1',[]):
            raw=source_row[i]
            if ':' not in raw:continue
            key,value=raw.split(':',1);key=key.strip();value=value.strip()
            if key in fields and fields[key]!=value:raise ValueError(f'Conflicting characteristic {key} for {sid}')
            fields[key]=value
        excluded=[key for key,allowed in rules['include_equals'].items() if fields.get(key) not in allowed]
        raw_response=fields.get(rules['response_field'],'')
        mapped.append({'sample_id':sid,'patient_id':fields.get(rules['patient_field'],''),
            'baseline':'unknown' if rules['baseline_field'] not in fields else str(fields[rules['baseline_field']]==rules['baseline_value']).lower(),
            'response':rules['response_values'].get(raw_response,'unknown'),'include':str(not excluded).lower(),
            'exclusion_reason':'outside_prespecified_population:'+','.join(excluded) if excluded else '',
            'raw_response':raw_response,'source_ref':f'{matrix_key}#!Sample_characteristics_ch1;sample={sid};field={rules["response_field"]}'})
    platform=read_platform(annotation.local_path,genes={'OSMR','OSM'})
    probes=[{'probe_id':p['ID'],'gene_symbol':p['Gene symbol'],'platform_id':rules['platform_id'],
             'source_ref':f'{platform_key}#!platform_table;ID={p["ID"]}'} for p in platform['rows']]
    if not probes:raise ValueError('No unambiguous OSMR/OSM probe mappings')
    ensure_storage(output)
    output.mkdir(parents=True)
    for name,rows in [('samples.csv',mapped),('probes.csv',probes)]:
        with (output/name).open('w',newline='') as f:
            w=csv.DictWriter(f,fieldnames=rows[0].keys());w.writeheader();w.writerows(rows)
    provenance={'created_at':now(),'matrix':matrix.model_dump(),'platform_annotation':annotation.model_dump(),'rules':rules,
                'mapping_review':'deterministic source-field audit only; human scientific review pending','parser':'vbt.intake.prepare_geo'}
    write(output/'mapping_provenance.json',provenance)
    items=[];keys={}
    for name,kind in [('samples','csv'),('probes','csv'),('mapping_provenance','json')]:
        p=output/(name+'.'+kind);h=digest(p)
        ds=DatasetVersion(dataset_id=matrix.dataset_id+'_'+name,version='sha256-'+h[:16],source_url='derived:'+matrix.source_url,
                          local_path=str(p),size_bytes=p.stat().st_size,sha256=h,download_status='complete',format=kind,evidence_kind=matrix.evidence_kind)
        keys[name]=registry.register(ds,data_root);items.append(ds.model_dump())
    c=Cohort(name=rules['cohort'],matrix_key=matrix_key,sample_key=keys['samples'],probe_key=keys['probes'],
        endpoint_definition=rules['endpoint_definition'],endpoint_source=rules['endpoint_source'],expression_scale=rules['expression_scale'],
        platform_id=rules['platform_id'],mapping_reviewed_by='deterministic-source-field-audit (NOT human signoff)',mapping_reviewed_at=now())
    plan=ResearchPlan(question='Exploratory source-label analysis: baseline OSMR/OSM vs cohort-specific response; not exact paper replication',
        evidence_kind=matrix.evidence_kind,cohorts=[c],dataset_keys=[platform_key,keys['mapping_provenance']],expected_direction='unspecified',timeout_seconds=300)
    write(output/'manifest.json',items);write(output/'plan.json',plan.model_dump())
    return {'plan':str(output/'plan.json'),'mapping':str(output/'mapping_provenance.json'),'probe_count':len(probes),'matrix_samples':len(mapped),'clinical_release':False}
