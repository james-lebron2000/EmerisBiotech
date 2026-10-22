"""Validate public snapshots and prepare a prospective season; never call a model.

Curated candidate definitions and bounded bilingual readout-screen receipts are
inputs. This utility neither proves outcome absence nor registers a ledger.
Original HTTP bytes and web-reader extracts remain explicitly distinguishable.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
MODELS = ['glm-5.2', 'kimi-k3', 'deepseek-v4.1-flash', 'deepseek-v4-pro']


def instant(value):
    parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('Timezone-aware timestamp required')
    return parsed.astimezone(timezone.utc)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def valid_url(value):
    u = urlsplit(str(value))
    return u.scheme == 'https' and bool(u.hostname) and not u.username and not u.password


def verify_sources(catalog, root):
    root = Path(root).resolve()
    sources = {}
    for source in catalog['sources']:
        name = source['id']
        rel = source['path']
        if name in sources or not re.fullmatch(r'arena_data/source_snapshots/[A-Za-z0-9_.-]+\.(?:json|html|txt|pdf)', rel):
            raise ValueError('Duplicate source or unsafe snapshot path')
        path = root / rel
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root):
            raise ValueError('Source must be an existing regular file inside the package')
        raw = path.read_bytes()
        if digest(raw) != source['sha256'] or len(raw) != source['bytes']:
            raise ValueError('Public source checksum or size mismatch: ' + name)
        if not valid_url(source['source_url']):
            raise ValueError('Public source must have credential-free HTTPS URL')
        if instant(source['retrieved_at']) > datetime.now(timezone.utc):
            raise ValueError('Source retrieval cannot be in the future')
        sources[name] = source
    return sources


def prepare(catalog, root=ROOT, now=None, minimum_projects=12, workflow_version=None):
    current = now or datetime.now(timezone.utc)
    sources = verify_sources(catalog, root)
    cutoff = instant(catalog['information_cutoff_utc'])
    if cutoff > current:
        raise ValueError('Evidence cutoff cannot be in the future')
    candidates = catalog['candidates']
    projects = [c['project_id'] for c in candidates]
    if len(projects) < minimum_projects or len(set(projects)) != len(projects):
        raise ValueError('Minimum distinct projects required; one target per drug asset')
    if len({c['nct_id'] for c in candidates}) != len(candidates):
        raise ValueError('Duplicate target trial')
    if workflow_version is None:
        sys.path.insert(0, str(ROOT))
        from emeris_live.arena_providers import VERSION
        workflow_version = VERSION + ':single_pass'
    deadline = (current + timedelta(hours=24)).isoformat()
    targets, slots = [], []
    for c in candidates:
        if c.get('eligibility') != 'qualified_for_prospective_research' or c.get('result') is not None:
            raise ValueError('Known, unresolved-screen or excluded outcomes cannot be enrolled')
        screen = c['readout_screen']
        checked = instant(screen['checked_at_utc'])
        if screen['status'] != 'no_decisive_public_result_found' or not cutoff <= checked <= current or (current-checked).total_seconds() > 86400:
            raise ValueError('Readout screen must follow cutoff and be fresh within 24 hours')
        if set(screen.get('languages', [])) != {'en', 'zh'} or not screen.get('queries'):
            raise ValueError('Bounded English and Chinese screening required')
        for sid in screen['receipt_source_ids']:
            if sid not in sources:
                raise ValueError('Screen receipt missing')
        readout = c['readout']
        if readout.get('basis') != 'sponsor_readout_guidance' or readout['source_id'] not in sources:
            raise ValueError('Readout requires sponsor guidance, not completion-date inference')
        if readout['end'] < current.date().isoformat():
            raise ValueError('Expired readout guidance requires refreshed selection')
        if readout['precision'] not in ('year', 'half_year', 'quarter', 'month', 'day'):
            raise ValueError('Explicit readout precision required')
        registry_source = sources[c['registry_source_id']]
        registry = json.loads((Path(root)/registry_source['path']).read_text())
        if registry.get('hasResults') is not False or registry.get('resultsSection'):
            raise ValueError('Registry already carries results; prospective candidate blocked')
        protocol = registry['protocolSection']
        if protocol['identificationModule']['nctId'] != c['nct_id']:
            raise ValueError('Registry identity mismatch')
        primary = protocol['outcomesModule']['primaryOutcomes'][c['registry_primary_index']]
        if primary['measure'] != c['endpoint']:
            raise ValueError('Selected endpoint disagrees with frozen registry')
        if c['hypothesis_type'] not in ('superiority', 'non_inferiority'):
            raise ValueError('Explicit statistical hypothesis type required')
        if c['hypothesis_type'] == 'non_inferiority' and 'non-inferiority' not in c['success_definition'].lower():
            raise ValueError('Non-inferiority target must preserve its success criterion')
        target = {k:c[k] for k in ('id','project_id','nct_id','trial_name','drug','sponsor','indication','endpoint','population','comparison','success_definition','analysis_timepoint')}
        target.update(information_cutoff_utc=cutoff.isoformat(), data_cutoff=cutoff.isoformat(), submission_deadline_utc=deadline, scope='prospective_challenge', result=None,
                      readout={**readout, 'source_url':sources[readout['source_id']]['source_url'], 'source_sha256':sources[readout['source_id']]['sha256']},
                      readout_screen={**screen,'sources':[{'url':sources[sid]['source_url'],'sha256':sources[sid]['sha256'],'retrieved_at':sources[sid]['retrieved_at']} for sid in [c['registry_source_id'],readout['source_id']]]},
                      status_note=c['limitations'], patent_ids=[], human_review_count=0, hypothesis_type=c['hypothesis_type'])
        evidence=[]
        for card in c['evidence_cards']:
            source=sources[card['source_id']]
            if instant(source['retrieved_at']) > cutoff:
                raise ValueError('Evidence snapshot was acquired after cutoff')
            if card.get('is_target_result') or card['kind'] not in ('trial_registry','clinical_prior','trial_protocol'):
                raise ValueError('Target outcomes or unsupported evidence kind prohibited')
            evidence.append({'id':card['id'],'kind':card['kind'],'title':card['title'],'text':card['text'],
                'source_url':source['source_url'],'source_sha256':source['sha256'],'available_at':source['retrieved_at'],
                'public_date':source.get('public_date'),'snapshot_kind':source['snapshot_kind'],'source_path':source['path'],
                'page_references':card.get('page_references',[]),'source_anchor':card.get('source_anchor'),
                'is_target_result':False,'human_review_count':0})
        if not any(e['kind']=='trial_registry' for e in evidence) or not any(e['kind'] in ('clinical_prior','trial_protocol') for e in evidence):
            raise ValueError('Registry and bounded prior/protocol evidence both required')
        target['evidence_ids']=[e['id'] for e in evidence]
        packet={'target':{k:target[k] for k in ('endpoint','population','comparison','success_definition')},'data_cutoff':cutoff.isoformat(),'evidence':evidence}
        targets.append(target)
        for model in MODELS:
            slots.append({'target_id':target['id'],'model_id':model,'track':'same_evidence','condition':'clinical_only','workflow_version':workflow_version,'packet':packet})
    budget={'budget_policy':'token_only_unpriced','max_requests':1,'max_output_tokens':8192,'max_input_chars':12000,
            'timeout_seconds':120,'max_total_output_tokens':8192,'max_total_timeout_seconds':120}
    spec={'cohort_id':catalog['cohort_id'],'targets':targets,'slots':slots,'protocol':{
        'version':'season-20261023-v1','season_kind':'registered_12_project_season',
        'purpose':'Prospective operational research comparison; future adjudication required for predictive performance.',
        'execution_budget':budget,'maximum_slots':len(slots),'independent_asset_clusters':len(projects),
        'independence_limits':catalog['independence_limits'],'pricing_status':'Unverified gateway monetary tariff; token caps are not a fee estimate.',
        'primary_selection':'First assigned run retained, including failures/abstentions; no semantic retry or best-of selection.',
        'comparison':'Same evidence packet within each project; four gateway model IDs representing three families. Provider identity is gateway-reported.',
        'metrics':'Brier/log loss after exact target adjudication; coverage denominator includes all assigned slots; asset-cluster uncertainty; zero current prospective accuracy claim.',
        'source_policy':'Public sources only; original HTTP bytes distinguished from archived reader extracts. Newly acquired snapshots have retrieval-time availability.',
        'screening_limits':'Bounded indexed bilingual official-source search cannot prove absence. Re-screen before ledger registration; any decisive target result blocks registration.',
        'external_attestation':'To be obtained before execution by operator; preparation itself has no TSA or ledger status.',
        'human_review_count':0,'clinical_prediction_validated':False}}
    return spec,budget


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidates',type=Path,default=ROOT/'docs/arena-season-candidates-20261023.json')
    parser.add_argument('--source-root',type=Path,default=ROOT)
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--workflow-version',default=None)
    args=parser.parse_args()
    catalog=json.loads(args.candidates.read_text())
    spec,budget=prepare(catalog,args.source_root,workflow_version=args.workflow_version)
    # Import pure validators only: never construct ArenaLedger or provider clients.
    sys.path.insert(0,str(ROOT))
    from emeris_live.arena_store import validate_target,validate_packet
    for target in spec['targets']:
        validate_target(target)
        for slot in spec['slots']:
            if slot['target_id']==target['id']:validate_packet(slot['packet'],target,slot['condition'])
    outputs={'season_spec.json':spec,'season_budget.json':budget,'source_manifest.json':{'schema_version':'arena-public-acquisition-v1','assets':catalog['sources']}}
    if any((args.output_dir/name).exists() for name in outputs):
        raise ValueError('Existing proposals are immutable; use a new output directory')
    args.output_dir.mkdir(parents=True,exist_ok=True)
    for name,value in outputs.items():
        (args.output_dir/name).write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    print(json.dumps({'prepared_targets':len(spec['targets']),'prepared_slots':len(spec['slots']),'registered':False,'model_calls':0,'output_dir':str(args.output_dir)},ensure_ascii=False))


if __name__=='__main__':
    main()
