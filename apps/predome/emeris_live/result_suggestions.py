"""Auditable registry result extraction and NCT-only public prediction pairing.

Discovery suggestions never decide efficacy, write outcomes, or change forecasts.
Registered targets, predicted targets and reported results remain separate objects.
"""
from copy import deepcopy
import hashlib
import json
import re
from urllib.parse import urlsplit
from .intake import get_source_bytes
from .platform_store import PlatformStore, digest, canonical

PIPELINE_VERSION='registry-primary-result-suggestions.v1'
MAX_SUGGESTIONS=200
MAX_SUGGESTION_BYTES=2*1024*1024
SUGGESTION_ENVELOPE_BYTES=len(canonical({'suggestion_id':'rs_'+'0'*24,'digest':'0'*64}).encode())-1
BASE_POINTER='/resultsSection/outcomeMeasuresModule/outcomeMeasures'
BASE_WARNINGS=['nct_pairing_does_not_verify_target_match','prespecified_alpha_unknown',
               'multiplicity_and_primary_success_rule_unverified','effect_direction_unverified',
               'comparison_requires_independent_confirmation','first_decisive_readout_date_unverified',
               'registry_result_posting_is_not_first_clinical_readout','independent_review_required']


def mapping(value,context):
    if value is None:return {}
    if not isinstance(value,dict):raise ValueError(context+' must be an object')
    return value


def _public_matches(envelopes,nct):
    """Accept only the explicitly redacted public-snapshot projection."""
    result=[]
    for envelope in envelopes:
        envelope=mapping(envelope,'Public snapshot envelope')
        row=mapping(envelope.get('record'),'Public snapshot record')
        if (envelope.get('integrity_verified') is not True
                or row.get('public_view')!='explicitly_published_summary_not_full_private_input'
                or row.get('status')!='locked' or row.get('trial_id')!=nct):continue
        if not row.get('snapshot_id') or not row.get('prediction_id'):
            raise ValueError('Public snapshot identity missing')
        if not re.fullmatch(r'[0-9a-f]{64}',str(envelope.get('sha256',''))):
            raise ValueError('Public snapshot digest missing')
        result.append({'snapshot_id':row['snapshot_id'],'prediction_id':row['prediction_id'],
                       'snapshot_sha256':envelope['sha256'],'target':deepcopy(row.get('target',{}))})
    return sorted(result,key=lambda row:row['snapshot_id'])


def build_suggestions(candidate,source_bytes,public_snapshots,max_suggestions=MAX_SUGGESTIONS,max_suggestion_bytes=MAX_SUGGESTION_BYTES):
    """Pure deterministic projection over captured bytes and public envelopes.

    Caller must get bytes from server intake. Neither the mutable structured
    summary nor a predicted population/comparator supplies actual-result fields.
    """
    candidate=mapping(candidate,'Candidate')
    if type(max_suggestions) is not int or not 1<=max_suggestions<=MAX_SUGGESTIONS:
        raise ValueError('Suggestion limit must be between 1 and 200')
    if type(max_suggestion_bytes) is not int or not 1<=max_suggestion_bytes<=MAX_SUGGESTION_BYTES:
        raise ValueError('Suggestion byte budget must be between 1 and 2097152')
    if candidate.get('kind')!='reported_results_candidate':
        raise ValueError('Only captured reported-results candidates support registry suggestions')
    nct=candidate.get('nct_id')
    if not isinstance(nct,str) or not re.fullmatch(r'NCT\d{8}',nct):raise ValueError('Invalid candidate NCT')
    if not isinstance(source_bytes,bytes) or len(source_bytes)>6*1024*1024:
        raise ValueError('Captured registry response must be bytes within the intake size limit')
    source=mapping(candidate.get('source'),'Candidate source')
    if hashlib.sha256(source_bytes).hexdigest()!=source.get('sha256'):
        raise ValueError('Captured source integrity failed')
    url=urlsplit(str(source.get('url','')))
    if (url.scheme!='https' or url.hostname!='clinicaltrials.gov' or url.username or url.password
            or url.port not in (None,443) or url.path.rstrip('/')!='/api/v2/studies/'+nct or url.fragment):
        raise ValueError('Captured source must be the official registry study API for this NCT')
    try:raw=json.loads(source_bytes)
    except (ValueError,UnicodeDecodeError) as exc:raise ValueError('Captured registry JSON could not be parsed') from exc
    if not isinstance(raw,dict):raise ValueError('Captured registry response must be an object')
    protocol=mapping(raw.get('protocolSection'),'Registry protocol')
    if mapping(protocol.get('identificationModule'),'Registry identity').get('nctId')!=nct:
        raise ValueError('Raw registry NCT does not match candidate')
    registered=mapping(protocol.get('outcomesModule'),'Registered outcomes').get('primaryOutcomes',[])
    if not isinstance(registered,list) or any(not isinstance(x,dict) for x in registered):raise ValueError('Registered primary outcome schema malformed')
    measures=mapping(mapping(raw.get('resultsSection'),'Registry results').get('outcomeMeasuresModule'),'Reported outcomes').get('outcomeMeasures',[])
    if not isinstance(measures,list):raise ValueError('Reported outcome measure collection malformed')
    status=mapping(protocol.get('statusModule'),'Registry status')
    source_view={'url':source['url'],'sha256':source['sha256'],'retrieved_at':source.get('retrieved_at'),
                 'results_first_post_date':deepcopy(status.get('resultsFirstPostDateStruct')),
                 'hash_scope':'captured_registry_response_bytes'}
    matches=_public_matches(public_snapshots,nct)
    warnings=[];primary=[];suggestions=[]
    if raw.get('hasResults') is not True:warnings.append('registry_has_results_flag_not_true')
    if not matches:warnings.append('no_public_locked_prediction_match')
    if not registered:warnings.append('registered_primary_outcomes_missing')
    for mi,measure in enumerate(measures):
        if not isinstance(measure,dict):raise ValueError('Reported outcome measure schema malformed')
        if measure.get('type')!='PRIMARY':continue
        primary.append({'measure_index':mi,'json_pointer':f'{BASE_POINTER}/{mi}','raw':deepcopy(measure)})
    if not primary:warnings.append('no_reported_primary_measures')
    pairing_count_total=0
    for item in primary:
        analyses=item['raw'].get('analyses',[])
        if not isinstance(analyses,list):raise ValueError('Primary measure analysis collection malformed')
        pairing_count_total+=max(len(analyses),1)*len(matches)
    if pairing_count_total>max_suggestions:warnings.append('suggestion_limit_reached_review_all_raw_primary_results')
    suggestion_bytes=0;budget_exhausted=False
    for item in primary:
        if len(suggestions)>=max_suggestions or budget_exhausted:break
        measure=item['raw'];mi=item['measure_index'];mp=item['json_pointer']
        analyses=measure.get('analyses',[])
        groups=measure.get('groups',[])
        if not isinstance(analyses,list) or not isinstance(groups,list):
            raise ValueError('Primary measure analysis/group collection malformed')
        by_id={}
        for gi,group in enumerate(groups):
            if not isinstance(group,dict):raise ValueError('Outcome group schema malformed')
            gid=group.get('id')
            if gid is not None:
                if not isinstance(gid,str):raise ValueError('Outcome group id must be a string')
                by_id.setdefault(gid,[]).append((gi,group))
        measure_warnings=[]
        if len(primary)>1:measure_warnings.append('multiple_primary_measures_require_success_rule_review')
        if len(analyses)>1:measure_warnings.append('multiple_analyses_no_automatic_selection')
        if not analyses:measure_warnings.append('no_reported_statistical_analysis')
        if not measure.get('populationDescription'):measure_warnings.append('reported_analysis_population_missing')
        if not measure.get('timeFrame'):measure_warnings.append('reported_timeframe_missing')
        if any(len(v)>1 for v in by_id.values()):measure_warnings.append('duplicate_measure_local_group_ids')
        for ai,analysis in enumerate(analyses or [None]):
            if len(suggestions)>=max_suggestions or budget_exhausted:break
            if analysis is not None and not isinstance(analysis,dict):raise ValueError('Statistical analysis schema malformed')
            ai=ai if analysis is not None else None
            ap=f'{mp}/analyses/{ai}' if ai is not None else None
            values=analysis or {};ids=values.get('groupIds',[])
            if not isinstance(ids,list) or any(not isinstance(x,str) for x in ids):raise ValueError('Analysis groupIds schema malformed')
            analysis_warnings=list(BASE_WARNINGS)+measure_warnings
            mapped=[]
            if not ids:analysis_warnings.append('analysis_comparison_group_ids_missing')
            for gid in ids:
                candidates=by_id.get(gid,[])
                if len(candidates)!=1:
                    analysis_warnings.append('unresolved_or_ambiguous_measure_local_group_id')
                    mapped.append({'id':deepcopy(gid),'title':None,'description':None,'json_pointer':None})
                    continue
                gi,group=candidates[0]
                mapped.append({'id':gid,'title':deepcopy(group.get('title')),'description':deepcopy(group.get('description')),
                               'json_pointer':f'{mp}/groups/{gi}'})
            p=values.get('pValue')
            if p is None:analysis_warnings.append('p_value_not_reported')
            elif isinstance(p,str) and re.match(r'^\s*[<>≤≥]',p):analysis_warnings.append('p_value_inequality_preserved_not_exact')
            if 'nominal' in str(values.get('pValueComment','')).lower():analysis_warnings.append('nominal_p_value_not_primary_success')
            if values.get('nonInferiorityType') and values['nonInferiorityType']!='SUPERIORITY':
                analysis_warnings.append('noninferiority_or_other_test_requires_margin_and_rule_review')
            reported={'measure_index':mi,'measure_pointer':mp,'title':deepcopy(measure.get('title')),
                      'populationDescription':deepcopy(measure.get('populationDescription')),
                      'timeFrame':deepcopy(measure.get('timeFrame')),'paramType':deepcopy(measure.get('paramType')),
                      'unitOfMeasure':deepcopy(measure.get('unitOfMeasure')),'analysis_index':ai,'analysis_pointer':ap,
                      'analysis':deepcopy(analysis),'analysis_groups':mapped}
            for match in matches:
                if len(suggestions)>=max_suggestions or budget_exhausted:break
                suggestion={'pipeline_version':PIPELINE_VERSION,'candidate_id':candidate['id'],'nct_id':nct,
                    'snapshot_id':match['snapshot_id'],'prediction_id':match['prediction_id'],'snapshot_sha256':match['snapshot_sha256'],
                    'source':deepcopy(source_view),
                    'pairing':{'basis':'nct_only','target_match':'unverified','predicted_target':deepcopy(match['target']),
                               'registered_primary_outcomes':deepcopy(registered)},
                    'reported':deepcopy(reported),
                    'suggested_outcome':{'label':None,'endpoint':deepcopy(measure.get('title')),
                        'population':deepcopy(measure.get('populationDescription')),'comparison':None,'readout_date':None,
                        'p_value':None,'p_value_raw':deepcopy(p),
                        'effect_size':{k:deepcopy(values.get(k)) for k in ('paramType','paramValue')},
                        'confidence_interval':{k:deepcopy(values.get(k)) for k in ('ciPctValue','ciNumSides','ciLowerLimit','ciUpperLimit')}},
                    'warnings':sorted(set(analysis_warnings)),
                    'status':'needs_review','auto_verified':False}
                serialized=canonical(suggestion).encode()
                size=len(serialized)+SUGGESTION_ENVELOPE_BYTES
                if suggestion_bytes+size>max_suggestion_bytes:
                    budget_exhausted=True
                    warnings.append('suggestion_byte_limit_reached_review_raw_primary_results')
                    break
                sha=hashlib.sha256(serialized).hexdigest()
                suggestion_bytes+=size
                suggestions.append(suggestion | {'suggestion_id':'rs_'+sha[:24],'digest':sha})
    result={'pipeline_version':PIPELINE_VERSION,'candidate_id':candidate['id'],'nct_id':nct,
            'source':source_view,'source_integrity_verified':True,'label':None,'auto_verified':False,
            'status':'needs_review','warnings':sorted(set(warnings)),
            'registered_primary_outcomes':deepcopy(registered),'primary_measures':primary,
            'primary_measure_count':len(primary),'matched_predictions':matches,'suggestions':suggestions,
            'pairing_count':len(suggestions),'pairing_count_total':pairing_count_total,
            'truncated':pairing_count_total>len(suggestions),'suggestion_limit':max_suggestions,
            'suggestion_bytes':suggestion_bytes,'suggestion_byte_limit':max_suggestion_bytes,
            'scope':'captured_registry_primary_results_and_nct_only_public_prediction_pairs',
            'writes_actual_outcomes':False,'changes_frozen_predictions':False}
    return result | {'digest':digest(result)}


def suggest_candidate(store,candidate_id):
    """Read-only dynamic suggestions. Never query or expose a private snapshot."""
    raw,candidate=get_source_bytes(store,candidate_id)
    with store.connect() as db:
        row=db.execute('SELECT nct,source_url,sha256 FROM intake_candidates WHERE id=?',(candidate_id,)).fetchone()
    source=candidate.get('source') or {}
    if not row or (row['nct'],row['source_url'],row['sha256'])!=(candidate.get('nct_id'),source.get('url'),source.get('sha256')):
        raise ValueError('Candidate metadata does not match server intake capture')
    return build_suggestions(candidate,raw,PlatformStore(store).list_public_snapshots())
