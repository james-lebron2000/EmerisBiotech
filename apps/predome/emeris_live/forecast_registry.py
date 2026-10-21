"""Versioned public research forecasts: local integrity, not trusted timestamps.

Only an offline operator can append. Web routes are read-only. Administrators
could replace this entire store: a hash chain is not independent attestation.
"""
from datetime import datetime, timezone, timedelta
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def sha(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def instant(value):
    result=datetime.fromisoformat(value.replace('Z','+00:00'))
    if result.tzinfo is None:raise ValueError('Timezone required')
    return result.astimezone(timezone.utc)


def read_registry(directory):
    root=Path(directory)
    index=json.loads((root/'index.json').read_text())
    records=[];previous=None;seen=set()
    for item in index['entries']:
        identifier=item['id']
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,99}',identifier) or identifier in seen:
            raise ValueError('Invalid or duplicate forecast ID')
        seen.add(identifier)
        record=json.loads((root/(identifier+'.json')).read_text())
        if record['id']!=identifier or sha(record)!=item['sha256'] or record['previous_record_sha256']!=previous:
            raise ValueError('Forecast registry integrity check failed')
        previous=item['sha256']
        records.append({'record':record,'record_sha256':previous})
    if index['head_sha256']!=previous:raise ValueError('Registry head mismatch')
    return {'schema_version':'forecast-registry-1','records':records,'head_sha256':previous,
            'integrity_verified':True,'independent_timestamp_verified':False,
            'clinical_prediction_validated':False,
            'notice':'正式登记的未校准研究预测；本地哈希链不是第三方可信时间戳。'}


def freeze(directory,identifier,payload,*,spec,evidence_refresh,catalog,model_code_sha256,now=None):
    """Append once, only with recent explicit pre-readout source review.

The refresh is an operator-reviewed declaration, not automatic web verification.
It is included in the record so its scope and limitations remain reviewable.
"""
    now=now or datetime.now(timezone.utc)
    if now.tzinfo is None:raise ValueError('Timezone required')
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,99}',identifier):raise ValueError('Invalid ID')
    candidates=[c for c in catalog['candidates'] if c['id']==payload['candidate_id']]
    if len(candidates)!=1 or payload['candidate_id']!=spec['candidate_id']:
        raise ValueError('Forecast, specification and catalog candidate mismatch')
    if payload['model_version']!=spec['model_version'] or payload['trial_id']!=spec['trial_id'] or payload['trial_id']!=candidates[0]['nct_id']:
        raise ValueError('Model or target trial mismatch')
    if candidates[0]['status_as_of']['code']!='pending_no_public_result_found':
        raise ValueError('Catalog does not identify an eligible pending target')
    unsigned={k:v for k,v in payload.items() if k!='payload_sha256'}
    if payload.get('payload_sha256')!=sha(unsigned) or payload.get('spec_sha256')!=sha(spec) or payload.get('catalog_sha256')!=sha(catalog):
        raise ValueError('Payload is not bound to the supplied specification and evidence')
    if payload.get('clinical_prediction_validated') is not False:
        raise ValueError('Unvalidated research status is required')
    checked=instant(evidence_refresh['checked_at_utc'])
    if checked>now or now-checked>timedelta(hours=24):raise ValueError('Refresh must precede freezing by at most 24 hours')
    matches=[r for r in evidence_refresh['candidates'] if r['candidate_id']==payload['candidate_id']]
    if len(matches)!=1 or matches[0].get('target_results_found') is not False or not matches[0].get('sources'):
        raise ValueError('Unresolved or already-reported target cannot be frozen')
    if not re.fullmatch('[a-f0-9]{64}',model_code_sha256):raise ValueError('Invalid model hash')
    root=Path(directory);root.mkdir(parents=True,exist_ok=True)
    with (root/'.append.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        index_path=root/'index.json'
        index=json.loads(index_path.read_text()) if index_path.exists() else {'entries':[],'head_sha256':None}
        if index_path.exists():read_registry(root)
        dest=root/(identifier+'.json')
        if dest.exists() or any(e['id']==identifier for e in index['entries']):raise ValueError('Frozen forecast cannot be overwritten')
        record={'id':identifier,'frozen_at_utc':now.astimezone(timezone.utc).isoformat(),
                'publication_status':'frozen_for_publication','independent_timestamp_verified':False,
                'previous_record_sha256':index['head_sha256'],'forecast':payload,
                'model_spec':spec,'model_spec_sha256':sha(spec),'model_code_sha256':model_code_sha256,
                'catalog_sha256':sha(catalog),'catalog_snapshot':catalog,'evidence_refresh':evidence_refresh,
                'evidence_refresh_sha256':sha(evidence_refresh),
                'outcome_at_freeze':'pending','clinical_prediction_validated':False}
        record_hash=sha(record)
        with dest.open('x') as output:output.write(json.dumps(record,ensure_ascii=False,indent=2))
        index['entries'].append({'id':identifier,'sha256':record_hash})
        index['head_sha256']=record_hash
        temporary=root/'index.next'
        temporary.write_text(json.dumps(index,ensure_ascii=False,indent=2))
        os.replace(temporary,index_path)
        return {'id':identifier,'record_sha256':record_hash}
