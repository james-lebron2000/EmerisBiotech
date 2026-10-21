"""Join the complete neutral annual universe to source-adjudicated annotations.

No missing outcome becomes negative. No current registry field becomes a
historical predictor. The steward supplies a redacted scaffold for new tests.
"""
from pathlib import Path
from datetime import datetime,timezone
import argparse,json,re
from continuous_audit import ROOT,DATA,canonical,contract_hashes,digest,validate
from predome_prior import effect_features
from predome_pilot import write_new
from continuous_time import result_interval,time_split

AREA_RULES=[('oncology',r'cancer|carcinoma|neoplasm|melanoma|lymphoma|leukemia|glioblastoma|myeloma|sarcoma|tumou?r'),('immunology',r'lupus|multiple sclerosis|psoriasis|colitis|crohn|arthritis|dermatitis'),('neurology',r'alzheimer|parkinson|dementia|amyotrophic'),('cardiovascular',r'heart failure|cardiovascular|coronary|hypertension'),('metabolic',r'diabetes|obes|hyperlipid'),('infectious',r'covid|influenza|pneumonia|hiv|hepatitis|viral|bacterial|infection')]
ASSET_REVIEWED={'previously_reviewed_investigational_core','independently_reviewed_investigational_core'}

def features(old):
    """Same baseline-only regex rules as v3, with baseline arm links now available."""
    condition=' '.join(x.get('name','') for x in old['conditions_raw']).lower()
    areas=[k for k,p in AREA_RULES if re.search(p,condition)]
    families=set()
    for outcome in old['primary_endpoints_raw']:
        text=' '.join(str(outcome.get(k,'')) for k in ['measure','time_frame','description']).lower()
        families.add('survival' if re.search(r'survival|time to|time until|hazard|event.free',text) else 'binary_response' if re.search(r'sri.?4|responder|response rate|complete response|bicl?a|percentage|proportion|pasi.?75|remission|acr.?[0-9]',text) else 'continuous' if re.search(r'change from baseline|change in|score|cognitive|cdr.?sb|mean',text) else 'unknown')
    # Use explicit comparator-arm identity, never placebo mentions in double-dummy experimental arms.
    norm=lambda v:re.sub('[^a-z]','',str(v).lower())
    controls=[g for g in old.get('design_groups_raw',[]) if norm(g.get('group_type')) in ['activecomparator','placebocomparator']]
    isplacebo=any(norm(g.get('group_type'))=='placebocomparator' for g in controls)
    active=any(norm(g.get('group_type'))=='activecomparator' for g in controls)
    placebo=1 if isplacebo else 0 if controls else None
    if placebo is None and re.search(r'\bplacebo[-\s]+controlled\b|\b(?:versus|vs\.?|compared with|compared to)\s+placebo\b',old.get('official_title',''),re.I):placebo=1
    expids={str(g['id']) for g in old.get('design_groups_raw',[]) if norm(g.get('group_type'))=='experimental'}
    controlids={str(g['id']) for g in controls}
    links=old.get('design_group_interventions_raw',[])
    e={str(x['intervention_id']) for x in links if str(x['design_group_id']) in expids}
    c={str(x['intervention_id']) for x in links if str(x['design_group_id']) in controlids}
    # Arm labels alone can reverse investigational/control roles (e.g. some
    # active-comparator records). No drug modality is inferred from that proxy.
    modality=None
    return {'planned_enrollment':old.get('planned_enrollment') if str(old.get('enrollment_type_raw','')).lower() in ['anticipated','estimated'] else None,
            'phase':3,'primary_endpoint_count':len(old['primary_endpoints_raw']),'randomized':1,'placebo_controlled':placebo,
            'therapeutic_area':areas[0] if len(areas)==1 else None,
            'endpoint_family':next(iter(families)) if len(families)==1 else 'mixed' if families else 'unknown',
            'modality':modality,'comparator_type':'mixed' if active and isplacebo else 'active' if active else 'placebo' if isplacebo else None}

def core(value):return re.sub(r'[^a-z0-9]','',str(value).lower())

def assemble(universe_path,scaffold_path,seen_path,output,prior_path=None):
    universe_path=Path(universe_path);scaffold_path=Path(scaffold_path);output=Path(output)
    oldrows=json.loads(universe_path.read_text())['records']
    scaffold=json.loads(scaffold_path.read_text());by={r['nct_id']:r for r in scaffold['records']}
    if len(by)!=len(scaffold['records']):raise ValueError('Duplicate annotations')
    if not set(by)<=set(r['nct_id'] for r in oldrows):raise ValueError('Annotation outside fixed universe')
    seen=json.loads(Path(seen_path).read_text());aliases=seen.get('asset_alias_to_core',{})
    canonical_core=lambda value:aliases.get(core(value),core(value))
    seen_assets={canonical_core(x) for x in seen.get('asset_groups',[])+seen.get('asset_components',[])}
    prior_by={r['target_nct_id']:r for r in json.loads(Path(prior_path).read_text())['selections']} if prior_path else {}
    rows=[];parent={}
    def find(x):
        parent.setdefault(x,x)
        if parent[x]!=x:parent[x]=find(parent[x])
        return parent[x]
    def union(a,b):
        aa,bb=find(a),find(b)
        if aa!=bb:parent[max(aa,bb)]=min(aa,bb)
    for old in oldrows:
        note=by.get(old['nct_id'],{});asset=note.get('asset_group')
        components=[canonical_core(v) for v in note.get('investigational_components',[]) if core(v)]
        if components and note.get('asset_mapping_status') not in ASSET_REVIEWED:
            if note.get('eligible'):raise ValueError('Accepted primary target has unresolved investigational core')
            components=[]
        if asset and not components and note.get('asset_mapping_status') in ASSET_REVIEWED:
            components=[canonical_core(v) for v in re.split(r'[+/]',asset) if core(v)]
        for a in components:find(a)
        for a in components[1:]:union(components[0],a)
        decisive=note.get('decisive_primary_at');match=note.get('historical_target_match');interval=result_interval(note)
        if match in [True,'match','major_target_matched']:match='matched'
        accepted=bool(note.get('eligible')) and match=='matched' and bool(components)
        status=note.get('adjudication_status','pending_uniform_source_adjudication')
        if accepted and (not interval or interval[0]<=old['features_as_of'] or note.get('first_partial_at') and note['first_partial_at']<=old['features_as_of']):
            accepted=False;status='excluded_target_disclosed_at_or_before_baseline'
        if accepted and time_split(note)=='excluded':accepted=False;status='outside_or_across_first_result_split_boundary'
        source_id=note.get('source_neutral_id');source_hash=note.get('source_sha256')
        if accepted and (not source_id or not source_hash):raise ValueError('Accepted label lacks source commitment: '+old['nct_id'])
        r={'nct_id':old['nct_id'],'trial_name':note.get('trial_name') or old.get('brief_title'),
           'sponsor_group':old.get('sponsor_group'),'features_as_of':old['features_as_of'],'features':features(old),
           'baseline_record_sha256':canonical(old),'planned_primary_completion_date':old['planned_primary_completion_date'],
           'primary_endpoints_raw':old['primary_endpoints_raw'],'feature_sources':old['feature_sources'],
           'asset_group':asset if components else None,'investigational_components':components,'asset_mapping_status':note.get('asset_mapping_status'),
           'eligible':accepted,'adjudication_status':status,'label_status':'source_adjudicated_primary' if accepted else status,
           'label':note.get('label') if accepted else None,'decisive_primary_at':decisive,'decisive_primary_interval':note.get('decisive_primary_interval'),'first_partial_at':note.get('first_partial_at'),
           'historical_target_match':match,'target_success_rule':note.get('target_success_rule'),
           'source_neutral_id':source_id,'source_sha256':source_hash,
           'human_review_count':note.get('human_review_count',0),'reviewer_kind':'independent_agent_review_not_human_double',
           'seen_NCT':old['nct_id'] in set(seen['nct_ids']),'seen_asset':bool(set(components)&seen_assets),
           'prior_features':{k:None for k in ['prior_benefit_z','prior_logratio_se','prior_target_endpoint_match','prior_population_match','prior_comparator_match','prior_time_match','prior_metric','prior_tier']},
           'prior_evidence_status':'pending_uniform_prior_RCT_search','selected_prior_record':None}
        prior=prior_by.get(r['nct_id'])
        if prior and prior.get('record'):
            rec=prior['record'];r['selected_prior_record']=rec;f=effect_features(rec)
            r['prior_features'].update({k:f[k] for k in ['prior_benefit_z','prior_logratio_se']})
            r['prior_features'].update(prior_metric=rec['effect_measure'],prior_tier=prior['tier'])
            r['selected_prior_math']=f;r['prior_selection_sha256']=canonical(prior)
            for flag in ['target_endpoint','population','comparator','time']:
                v=prior.get(flag+'_match');r['prior_features']['prior_'+flag+'_match']=int(v) if type(v) is bool else None
            r['prior_evidence_status']='source_reviewed_pre_baseline_RCT'
        elif prior:
            r['prior_evidence_status']='no_eligible_RCT_in_scoped_v3_library_not_global'
            r['prior_search_scope']=prior.get('rationale')
        rows.append(r)
    for r in rows:
        components=r['investigational_components']
        if components:r['asset_group']='component-'+find(components[0])
    # Propagate exposure through combination edges, independent of outcomes.
    exposed={find(a) for a in seen_assets if a in parent}
    for r in rows:
        if r['seen_NCT'] or r['seen_asset']:
            exposed.update(find(a) for a in r['investigational_components'])
    for r in rows:r['seen_asset']=bool(r['investigational_components'] and find(r['investigational_components'][0]) in exposed)
    output.mkdir(parents=True,exist_ok=False)
    receipt={'created_at_utc':datetime.now(timezone.utc).isoformat(),'status':'source_clock_checks_passed',
             'dataset_sha256':canonical(rows),'contract_hashes':contract_hashes(),
             'universe':{'path':str(universe_path.resolve().relative_to(ROOT)),'sha256':digest(universe_path)},
             'scaffold':{'path':str(scaffold_path.resolve().relative_to(ROOT)),'sha256':digest(scaffold_path)},
             'seen':{'path':str(Path(seen_path).resolve().relative_to(ROOT)),'sha256':digest(seen_path)},
             'prior_selections':{'path':str(Path(prior_path).resolve().relative_to(ROOT)),'sha256':digest(prior_path)} if prior_path else None,
             'feature_extractor_sha256':digest(__file__),'unknowns_retained':True,'human_review_is_separate':True}
    check=validate(rows,receipt)
    write_new(output/'dataset.json',rows);write_new(output/'source_audit.json',receipt|{'entry_check':check})
    write_new(output/'flow.json',{'universe_n':len(rows),'eligible_n':sum(r['eligible'] for r in rows),'unknown_or_excluded_n':sum(not r['eligible'] for r in rows),
                               'historical_prior_RCT_numeric_n':sum(r['selected_prior_record'] is not None for r in rows),
                               'status_counts':{s:sum(r['adjudication_status']==s for r in rows) for s in sorted({r['adjudication_status'] for r in rows})}})
    return rows,receipt

if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('universe');a.add_argument('scaffold');a.add_argument('seen');a.add_argument('output');a.add_argument('--priors');x=a.parse_args()
    rows,receipt=assemble(x.universe,x.scaffold,x.seen,x.output,x.priors)
    print(json.dumps({'n':len(rows),'eligible':sum(r['eligible'] for r in rows),'dataset_sha256':receipt['dataset_sha256']}))
