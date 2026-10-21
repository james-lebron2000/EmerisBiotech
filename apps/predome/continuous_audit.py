"""Fail-closed source and clock checks at the v4 fitting boundary.

The receipt is engineering provenance, not independent clinical adjudication.
Outcome source contents stay with the steward until the model has frozen.
"""
from pathlib import Path
import hashlib,json,math

ROOT=Path(__file__).resolve().parent
DATA=ROOT/'predome_data/continuous_v4'
CONTRACTS=['protocol.json','label_contract.json','amendment_01_annual_inception.json',
           'performance_goal_contract.json','performance_goal_amendment_01.json','amendment_02_result_date_precision.json']

def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()

def canonical(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()).hexdigest()

def contract_hashes():
    return {name:digest(DATA/name) for name in CONTRACTS}

def validate_descriptor(desc,cache):
    rel=desc.get('path') or desc.get('local_path')
    if not rel:raise ValueError('Missing source path')
    path=(ROOT/rel).resolve()
    if Path(rel).is_absolute() or not path.is_relative_to(ROOT) or not path.is_file():
        raise ValueError('Invalid source path')
    if path not in cache:cache[path]=digest(path)
    if cache[path]!=desc.get('sha256'):raise ValueError('Source hash mismatch: '+str(rel))

def validate(rows,receipt,seen=None):
    """Verify the frozen full dataset plus every selected pre-result input source.

    Reviewers attest target alignment in neutral fields; this function checks
    those attestations, source existence/hashes and clocks, not semantic truth.
    """
    if receipt.get('dataset_sha256')!=canonical(rows):raise ValueError('Audited dataset hash mismatch')
    if receipt.get('contract_hashes')!=contract_hashes():raise ValueError('Effective protocol mismatch')
    if receipt.get('status')!='source_clock_checks_passed':raise ValueError('Dataset audit not passed')
    universe_path=ROOT/receipt['universe']['path']
    if digest(universe_path)!=receipt['universe']['sha256']:raise ValueError('Universe changed')
    baseline=json.loads(universe_path.read_text())['records'];by={r['nct_id']:r for r in baseline}
    ids=[r['nct_id'] for r in rows]
    if len(ids)!=len(set(ids)) or set(ids)!=set(by):raise ValueError('Dataset must retain complete neutral universe')
    cache={};eligible=0
    validate_descriptor(receipt['scaffold'],cache)
    if receipt.get('feature_extractor_sha256')!=digest(Path(__file__).with_name('continuous_dataset.py')):
        raise ValueError('Feature extractor changed after dataset assembly')
    validate_descriptor(receipt['seen'],cache);frozen_seen=json.loads((ROOT/receipt['seen']['path']).read_text())
    if seen is not None and canonical(seen)!=canonical(frozen_seen):raise ValueError('Seen exposure inventory changed at fit entry')
    for key in ['identity_manifest','original_inventory','independent_role_review']:
        if frozen_seen.get(key):validate_descriptor(frozen_seen[key],cache)
    prior_selections={}
    if receipt.get('prior_selections'):
        validate_descriptor(receipt['prior_selections'],cache)
        prior_selections={r['target_nct_id']:r for r in json.loads((ROOT/receipt['prior_selections']['path']).read_text())['selections']}
    for row in rows:
        old=by[row['nct_id']];cutoff=old['features_as_of']
        if row.get('features_as_of')!=cutoff:raise ValueError('Historical baseline replaced')
        if row.get('baseline_record_sha256')!=canonical(old):raise ValueError('Baseline row mismatch')
        sources=old.get('feature_sources',{})
        for required in ['studies','designs','design_outcomes','conditions','interventions','sponsors']:
            if required not in sources:raise ValueError('Missing baseline source '+required)
        for desc in sources.values():
            validate_descriptor(desc,cache)
            if desc.get('published_at','9999')>cutoff:raise ValueError('Post-cutoff input source')
        if not row.get('eligible'):continue
        eligible+=1
        if not row.get('asset_group') or not row.get('investigational_components'):
            raise ValueError('Unresolved investigational asset')
        if row.get('asset_mapping_status') not in ['previously_reviewed_investigational_core','independently_reviewed_investigational_core']:
            raise ValueError('Unreviewed investigational asset role')
        if row.get('historical_target_match')!='matched':raise ValueError('Unmatched primary target')
        if row.get('label_status')!='source_adjudicated_primary':raise ValueError('Unaccepted label status')
        if not row.get('target_success_rule'):raise ValueError('Missing primary success rule')
        if not row.get('source_neutral_id') or not row.get('source_sha256'):raise ValueError('Missing steward source commitment')
        from continuous_time import result_interval,time_split
        interval=result_interval(row)
        if not interval or cutoff>=interval[0]:raise ValueError('Target result not after baseline')
        if time_split(row) in ['train','calibration'] and (type(row.get('label')) is not int or row['label'] not in [0,1]):
            raise ValueError('Training/calibration direction not adjudicated')
        partial=row.get('first_partial_at')
        if partial and cutoff>=partial:raise ValueError('Partial target disclosure at/before baseline')
        fs=row['features']
        if fs.get('primary_endpoint_count')!=len(old['primary_endpoints_raw']):raise ValueError('Primary count mismatch')
        planned_type=str(old.get('enrollment_type_raw','')).lower()
        if fs.get('planned_enrollment') is not None and planned_type not in ['anticipated','estimated']:
            raise ValueError('Actual enrollment used as planned input')
        from continuous_dataset import features
        if fs!=features(old):raise ValueError('Features do not reproduce from frozen baseline')
        for value in fs.values():
            if isinstance(value,float) and not math.isfinite(value):raise ValueError('Nonfinite input')
        prior=row.get('selected_prior_record')
        if prior:
            selection=prior_selections.get(row['nct_id'])
            if not selection or canonical(selection)!=row.get('prior_selection_sha256') or canonical(selection['record'])!=canonical(prior):
                raise ValueError('Prior selection not bound to reviewed source')
            if not prior.get('randomized') or prior.get('prior_nct_id')==row['nct_id']:raise ValueError('Invalid RCT prior')
            source=prior['source'];validate_descriptor(source,cache)
            if source.get('publication_date','9999')>cutoff:raise ValueError('RCT prior after baseline')
            from predome_prior import effect_features
            derived=effect_features(prior)
            if not math.isclose(derived['prior_benefit_z'],row['prior_features']['prior_benefit_z'],rel_tol=1e-12,abs_tol=1e-14):
                raise ValueError('Prior arithmetic mismatch')
            def same(a,b):
                return a is b if a is None or b is None else math.isclose(a,b,rel_tol=1e-12,abs_tol=1e-14)
            if not same(derived['prior_logratio_se'],row['prior_features'].get('prior_logratio_se')):raise ValueError('Prior ratio SE mismatch')
            saved=row.get('selected_prior_math') or {}
            if set(derived)!=set(saved) or any((saved[k] is not v) if isinstance(v,bool) else not same(saved[k],v) for k,v in derived.items()):
                raise ValueError('Prior SE/theta derivation mismatch')
            if row['prior_features']['prior_metric']!=prior['effect_measure'] or row['prior_features']['prior_tier']!=selection['tier']:raise ValueError('Prior scale/tier mismatch')
            for flag in ['target_endpoint','population','comparator','time']:
                value=selection.get(flag+'_match');expected=int(value) if type(value) is bool else None
                if row['prior_features'].get('prior_'+flag+'_match')!=expected:raise ValueError('Prior applicability flag mismatch')
            if row['prior_features'].get('prior_tier') not in ['A','B']:raise ValueError('Inapplicable numeric prior')
        elif any(row.get('prior_features',{}).get(k) is not None for k in ['prior_benefit_z','prior_logratio_se']):
            raise ValueError('Prior numeric feature without source')
    return {'status':'verified_at_fit_entry','eligible_n':eligible,'verified_source_files':len(cache),
            'contract_hashes':contract_hashes(),'semantic_label_review_is_separate':True}
