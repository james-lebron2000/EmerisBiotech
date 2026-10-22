"""Operator CLI for pre-registered, append-only real-model challenge runs."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from emeris_live.arena_store import ArenaLedger, sha
from emeris_live import arena_providers
from emeris_live.arena_providers import forecast, list_models


def read(path):
    def unique(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate JSON key in operator input')
            result[key] = value
        return result
    return json.loads(Path(path).read_text(), object_pairs_hook=unique,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite operator input')))


def execution_contract(slot, budget):
    if slot['track'] == 'open_agent':
        raise ValueError('Open systems require a separately audited runner')
    workflow = 'evidence_review' if slot['track'] == 'shared_agent' else 'single_pass'
    version = arena_providers.VERSION + ':' + workflow
    if slot.get('workflow_version', version) != version:
        raise ValueError('Workflow version does not match this runner')
    prompts = ([arena_providers.REVIEW_PROMPT] if workflow == 'evidence_review' else []) + [arena_providers.SYSTEM_PROMPT]
    return {'workflow_version': version,
        'expected_provider_input_sha256': sha(arena_providers._packet(slot['packet'])),
        'expected_prompt_sha256': sha([hashlib.sha256(p.encode()).hexdigest() for p in prompts]),
        'provider_implementation_sha256': hashlib.sha256(Path(arena_providers.__file__).read_bytes()).hexdigest(),
        'execution_budget_sha256': sha(budget)}


def validate_budget(budget):
    limits = {'max_requests': (1, 2), 'max_output_tokens': (128, 16384),
              'max_input_chars': (256, arena_providers.MAX_PACKET_BYTES), 'timeout_seconds': (5, 300)}
    if not isinstance(budget, dict) or any(type(budget.get(k)) is not int or not low <= budget[k] <= high
                                          for k, (low, high) in limits.items()):
        raise ValueError('Freeze explicit request, token, input and timeout limits before execution')


def verify_registered_model(row, model, budget):
    expected = row.get('provider_config_snapshot', {})
    if (not expected.get('config_sha256') or model.get('requested_model') != row.get('requested_model')
            or model.get('provider') != row.get('provider')
            or model.get('config_sha256') != expected['config_sha256']):
        raise ValueError('Model configuration changed since registration')
    current = execution_contract(row, budget)
    if any(row.get(k) != value for k, value in current.items()):
        raise ValueError('Runner, prompt, input or budget changed since registration')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data-dir',default=os.getenv('EMERIS_DATA_DIR'))
    sub=p.add_subparsers(dest='command',required=True)
    r=sub.add_parser('register');r.add_argument('spec')
    x=sub.add_parser('execute');x.add_argument('--cohort',required=True);x.add_argument('--max-calls',type=int,required=True)
    x.add_argument('--budget-file',required=True)
    v=sub.add_parser('review');v.add_argument('--target',required=True);v.add_argument('--reviewer',required=True)
    v.add_argument('--result',required=True);v.add_argument('--supersedes')
    att=sub.add_parser('attest');att.add_argument('receipt')
    sub.add_parser('status')
    a=p.parse_args()
    if not a.data_dir:p.error('--data-dir required')
    ledger=ArenaLedger(a.data_dir)
    if a.command=='register':
        spec=read(a.spec)
        budget=spec['protocol'].get('execution_budget')
        validate_budget(budget)
        models={m['id']:m for m in list_models()}
        for slot in spec['slots']:
            model=models.get(slot['model_id'])
            if not model or not model.get('requested_model'):raise ValueError('Model requires an explicitly configured version')
            slot['requested_model']=model['requested_model']
            slot['provider']=model['provider']
            slot['provider_config_snapshot']=model
            slot.update(execution_contract(slot,budget))
        ids=ledger.register(spec['cohort_id'],spec['targets'],spec['slots'],spec['protocol'])
        print(json.dumps({'registered_slots':len(ids),'run_ids':ids,'ledger_sha256':ledger.snapshot()['ledger_sha256']}))
    elif a.command=='execute':
        if not 1<=a.max_calls<=100:raise ValueError('Explicit limit of 1–100 slots required')
        state=ledger.snapshot();budget=read(a.budget_file)
        validate_budget(budget)
        cohort=next((c for c in state['cohorts'] if c['id']==a.cohort),None)
        if not cohort:raise ValueError('Unknown cohort')
        if cohort['protocol'].get('execution_budget')!=budget:
            raise ValueError('Execution budget must equal the pre-registered policy')
        models={m['id']:m for m in list_models()}
        rows=[r for r in state['runs'] if r['cohort_id']==a.cohort and r['status']=='pending' and not r.get('started_at')]
        for row in rows[:a.max_calls]:
            verify_registered_model(row,models.get(row['model_id'],{}),budget)
        for row in rows[:a.max_calls]:
            model=next((m for m in list_models() if m['id']==row['model_id']),{})
            verify_registered_model(row,model,budget)
            slot=ledger.start(row['id'])
            workflow='evidence_review' if slot['track']=='shared_agent' else 'single_pass'
            try:
                result=forecast(slot['model_id'],slot['packet'],workflow=workflow,budget=budget)
            except Exception:
                # A crash may have happened after billing. Preserve a failed slot,
                # never automatically repeat it or expose a raw provider exception.
                result={'status':'failed','probability':None,'error_code':'runner_exception',
                    'model_id':slot['model_id'],'requested_model':slot['requested_model'],
                    'provider':slot['provider'],'config_sha256':slot['provider_config_snapshot']['config_sha256'],
                    'workflow_version':slot['workflow_version'],'evidence_ids':[],
                    'cost_usd':None,'attempts':[],'usage':{},'billing_status':'unknown_after_runner_exception'}
            ledger.finish(row['id'],result)
            print(json.dumps({'run_id':row['id'],'model_id':row['model_id'],'status':result['status'],
                'probability':result.get('probability'),'returned_model':result.get('returned_model'),
                'latency_ms':result.get('latency_ms'),'error_code':result.get('error_code')},ensure_ascii=False),flush=True)
    elif a.command=='review':
        print(ledger.record_result(a.target,read(a.result),reviewer_id=a.reviewer,supersedes=a.supersedes))
    elif a.command=='attest':
        event=ledger.attest(read(a.receipt))
        print(json.dumps({'event_hash':event['event_hash'],'status':'attestation_appended'}))
    else:
        state=ledger.snapshot()
        print(json.dumps({'cohorts':len(state['cohorts']),'targets':len(state['targets']),
            'registered_slots':len(state['runs']),'completed_slots':sum(r['status']!='pending' for r in state['runs']),
            'ledger_sha256':state['ledger_sha256']},ensure_ascii=False))


if __name__=='__main__':main()
