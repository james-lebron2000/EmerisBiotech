"""Operator CLI for pre-registered, append-only real-model challenge runs."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import uuid
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from emeris_live.arena_store import ArenaLedger, sha
from emeris_live import arena_providers, arena_agents
from emeris_live.arena_attempt_diagnostics import sanitize_diagnostics
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


def agent_configuration(slot, budget, models):
    contract=arena_agents.freeze_agent_contract(slot.get('agent_spec'),slot['packet'],budget,models)
    component_names={m['id']:m.get('name',m['id']) for m in models}
    spec=contract['agent_spec']
    snapshot={'id':contract['model_id'],'name':component_names[spec['reviewer_model_id']]+' 审查 → '+component_names[spec['forecaster_model_id']]+' 预测',
        'provider':contract['provider'],'requested_model':contract['requested_model'],
        'config_sha256':contract['config_sha256'],'components':contract['components'],
        'adapter_version':arena_agents.VERSION,'tier':spec['tier']}
    return contract,snapshot


def execution_contract(slot, budget):
    runner_hash=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    if slot['track'] == 'open_agent':
        contract,_=agent_configuration(slot,budget,list_models())
        return {k:contract[k] for k in ('workflow_version','expected_provider_input_sha256',
            'expected_prompt_sha256','provider_implementation_sha256','execution_budget_sha256')} | {'runner_implementation_sha256':runner_hash}

    workflow = 'evidence_review' if slot['track'] == 'shared_agent' else 'single_pass'
    version = arena_providers.VERSION + ':' + workflow
    if slot.get('workflow_version', version) != version:
        raise ValueError('Workflow version does not match this runner')
    prompts = ([arena_providers.REVIEW_PROMPT] if workflow == 'evidence_review' else []) + [arena_providers.SYSTEM_PROMPT]
    return {'workflow_version': version,'runner_implementation_sha256':runner_hash,
        'expected_provider_input_sha256': sha(arena_providers._packet(slot['packet'])),
        'expected_prompt_sha256': sha([hashlib.sha256(p.encode()).hexdigest() for p in prompts]),
        'provider_implementation_sha256': arena_providers.provider_implementation_sha256(),
        'execution_budget_sha256': sha(budget)}


def validate_budget(budget):
    limits = {'max_requests': (1, 2), 'max_output_tokens': (128, 16384),
              'max_input_chars': (256, arena_providers.MAX_PACKET_BYTES), 'timeout_seconds': (5, 300)}
    if not isinstance(budget, dict) or any(type(budget.get(k)) is not int or not low <= budget[k] <= high
                                          for k, (low, high) in limits.items()):
        raise ValueError('Freeze explicit request, token, input and timeout limits before execution')


def verify_registered_model(row, model, budget):
    if row['track']=='open_agent':
        contract,model=agent_configuration(row,budget,list_models())
        if contract!=row.get('agent_contract'):
            raise ValueError('Agent contract changed since registration')
    expected = row.get('provider_config_snapshot', {})
    if (not expected.get('config_sha256') or model.get('requested_model') != row.get('requested_model')
            or model.get('provider') != row.get('provider')
            or model.get('config_sha256') != expected['config_sha256']):
        raise ValueError('Model configuration changed since registration')
    current = execution_contract(row, budget)
    if any(row.get(k) != value for k, value in current.items()):
        raise ValueError('Runner, prompt, input or budget changed since registration')


RECEIPT_FIELDS = frozenset(('status probability reasoning evidence_ids supporting_evidence_ids '
    'opposing_evidence_ids key_assumptions key_unknowns returned_model usage cost_usd known_cost_usd '
    'cost_amount known_cost_amount cost_currency cost_basis cost_note latency_ms prompt_sha256 '
    'request_sha256 provider_response_sha256 error_code adapter_version workflow workflow_version '
    'clinical_prediction_validated evidence_review provider requested_model config_sha256 stage_plan workflow_complete evidence_review_sha256 agent_trace agent_trace_sha256 agent_calls_dispatched cost_breakdown '
    'input_sha256 model_id budget preflight_cost_upper_estimate billing_status').split())
ATTEMPT_FIELDS = frozenset(('stage requested_model returned_model prompt_sha256 request_sha256 '
    'response_sha256 usage cost_usd cost_amount cost_currency cost_basis latency_ms status error_code input_sha256 input_chars input_bytes max_output_tokens timeout_seconds finish_reason visible_output_bytes response_bytes agent_step model_id diagnostics').split())
ACCOUNTING_FIELDS = frozenset(('attempts usage cost_usd known_cost_usd cost_amount known_cost_amount '
    'cost_currency cost_basis cost_note latency_ms returned_model request_sha256 provider_response_sha256').split())


def _json_value(value):
    """Only bounded JSON data from the adapter, never arbitrary exception objects."""
    if value is None or type(value) in (bool, int, str):
        return value
    if type(value) is float:
        return value if math.isfinite(value) else None
    if isinstance(value, list):
        return [_json_value(v) for v in value]
    if isinstance(value, dict):
        return {k: sanitize_diagnostics(v) if k == 'diagnostics' else _json_value(v)
                for k, v in value.items() if isinstance(k, str)
                and k.lower() not in ('api_key', 'authorization', 'token', 'secret', 'password',
                                      'endpoint', 'base_url', 'raw_response', 'raw_error', 'headers')}
    return None


def _diagnostics_copy(value):
    """Check optional metadata before ledger writes; preserve all other values."""
    if isinstance(value, dict):
        return {k: sanitize_diagnostics(v) if k == 'diagnostics' else _diagnostics_copy(v)
                for k, v in value.items()}
    if isinstance(value, list):
        return [_diagnostics_copy(v) for v in value]
    return value


def save_result_receipt(data_dir, run_id, result):
    """Persist sanitized adapter fields before ledger validation; never overwrite."""
    safe = {k: _json_value(v) for k, v in result.items() if k in RECEIPT_FIELDS}
    safe['attempts'] = [{k: sanitize_diagnostics(v) if k == 'diagnostics' else _json_value(v)
                         for k, v in attempt.items() if k in ATTEMPT_FIELDS}
                        for attempt in result.get('attempts', []) if isinstance(attempt, dict)]
    document = {'schema_version': 'arena-result-receipt-v1', 'run_id': run_id, 'result': safe}
    raw = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    directory = Path(data_dir) / 'arena_receipts'
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    # The generated name is independent of provider-controlled fields and cannot
    # escape the private runtime directory. O_EXCL also protects prior receipts.
    path = directory / (uuid.uuid4().hex + '.json')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    return safe, {'receipt_path': path.relative_to(Path(data_dir)).as_posix(),
                  'receipt_sha256': hashlib.sha256(raw).hexdigest()}


def finish_with_receipt(ledger, data_dir, run_id, slot, result):
    result = _diagnostics_copy(result)
    safe, link = save_result_receipt(data_dir, run_id, result)
    try:
        ledger.finish(run_id, {**result, **link})
        return result
    except ValueError:
        # The original result remains in the private receipt. Do not repeat the
        # call, hide its cost, or treat a rejected probability as a forecast.
        rejected = {k: v for k, v in safe.items() if k in ACCOUNTING_FIELDS}
        rejected.update(status='failed', probability=None, error_code='contract_rejected',
            model_id=slot['model_id'], requested_model=slot.get('requested_model'), provider=slot.get('provider'),
            config_sha256=slot.get('provider_config_snapshot', {}).get('config_sha256'),
            workflow_version=slot['workflow_version'], evidence_ids=[],
            supporting_evidence_ids=[], opposing_evidence_ids=[], contract_complete=False,
            billing_status='reported_attempts_preserved' if safe.get('attempts') else 'unknown_after_contract_rejection',
            **link)
        # Frozen identity describes the assigned slot; rejected observed identity
        # and input hashes are preserved only in the receipt, never substituted.
        ledger.finish(run_id, rejected)
        return rejected


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data-dir',default=os.getenv('EMERIS_DATA_DIR'))
    sub=p.add_subparsers(dest='command',required=True)
    r=sub.add_parser('register');r.add_argument('spec')
    x=sub.add_parser('execute');x.add_argument('--cohort',required=True);x.add_argument('--max-calls',type=int,required=True)
    x.add_argument('--budget-file',required=True);x.add_argument('--model');x.add_argument('--target')
    v=sub.add_parser('review');v.add_argument('--target',required=True);v.add_argument('--reviewer',required=True)
    v.add_argument('--result',required=True);v.add_argument('--supersedes')
    cr=sub.add_parser('citation-review');cr.add_argument('--run',required=True)
    cr.add_argument('--reviewer',required=True);cr.add_argument('--kind',choices=('ai','human'),required=True)
    cr.add_argument('--assessment',required=True);cr.add_argument('--supersedes')
    w=sub.add_parser('withdraw');w.add_argument('--cohort',required=True);w.add_argument('--reason',required=True)
    ei=sub.add_parser('evidence-issue');ei.add_argument('--evidence-id',required=True);ei.add_argument('--source-sha256',required=True)
    ei.add_argument('--note',required=True);ei.add_argument('--reference-url',required=True);ei.add_argument('--severity',choices=('blocking','warning'),default='blocking')
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
            if slot['track']=='open_agent':
                contract,model=agent_configuration(slot,budget,list(models.values()))
                if slot['model_id']!=contract['model_id']:
                    raise ValueError('Agent slot identity differs from its spec')
                slot['agent_contract']=contract
            else:
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
        rows=[r for r in state['runs'] if r['cohort_id']==a.cohort and r['status']=='pending' and not r.get('started_at')
              and (not a.model or r['model_id']==a.model) and (not a.target or r['target_id']==a.target)]
        for row in rows[:a.max_calls]:
            verify_registered_model(row,models.get(row['model_id'],{}),budget)
        for row in rows[:a.max_calls]:
            model=next((m for m in list_models() if m['id']==row['model_id']),{})
            verify_registered_model(row,model,budget)
            slot=ledger.start(row['id'])
            workflow='evidence_review' if slot['track']=='shared_agent' else 'single_pass'
            try:
                if slot['track']=='open_agent':
                    result=arena_agents.run_agent(slot['agent_spec'],slot['packet'],budget=budget,
                        models=list_models(),frozen_contract=slot['agent_contract'])
                else:
                    result=forecast(slot['model_id'],slot['packet'],workflow=workflow,budget=budget)
            except Exception:
                # A crash may have happened after billing. Preserve a failed slot,
                # never automatically repeat it or expose a raw provider exception.
                result={'status':'failed','probability':None,'error_code':'runner_exception',
                    'model_id':slot['model_id'],'requested_model':slot['requested_model'],
                    'provider':slot['provider'],'config_sha256':slot['provider_config_snapshot']['config_sha256'],
                    'workflow_version':slot['workflow_version'],'evidence_ids':[],
                    'cost_usd':None,'attempts':[],'usage':{},'billing_status':'unknown_after_runner_exception'}
            result=finish_with_receipt(ledger,a.data_dir,row['id'],slot,result)
            print(json.dumps({'run_id':row['id'],'model_id':row['model_id'],'status':result['status'],
                'probability':result.get('probability'),'returned_model':result.get('returned_model'),
                'latency_ms':result.get('latency_ms'),'error_code':result.get('error_code')},ensure_ascii=False),flush=True)
    elif a.command=='review':
        print(ledger.record_result(a.target,read(a.result),reviewer_id=a.reviewer,supersedes=a.supersedes))
    elif a.command=='citation-review':
        event=ledger.record_citation_review(a.run,read(a.assessment),reviewer_id=a.reviewer,
            reviewer_kind=a.kind,supersedes=a.supersedes)
        print(json.dumps({'event_hash':event['event_hash'],'status':'citation_review_appended'}))
    elif a.command=='withdraw':
        event=ledger.withdraw_unstarted_cohort(a.cohort,a.reason)
        print(json.dumps({'event_hash':event['event_hash'],'status':'withdrawn_before_execution'}))
    elif a.command=='evidence-issue':
        event=ledger.record_evidence_issue(a.evidence_id,a.source_sha256,note=a.note,reference_url=a.reference_url,severity=a.severity)
        print(json.dumps({'event_hash':event['event_hash'],'status':'evidence_issue_appended'}))
    elif a.command=='attest':
        event=ledger.attest(read(a.receipt))
        print(json.dumps({'event_hash':event['event_hash'],'status':'attestation_appended'}))
    else:
        state=ledger.snapshot()
        print(json.dumps({'cohorts':len(state['cohorts']),'targets':len(state['targets']),
            'registered_slots':len(state['runs']),'completed_slots':sum(r['status'] in ('predicted','failed','abstained','unavailable','timed_out') for r in state['runs']),
            'withdrawn_before_execution':sum(r['status']=='withdrawn' for r in state['runs']),
            'ledger_sha256':state['ledger_sha256']},ensure_ascii=False))


if __name__=='__main__':main()
