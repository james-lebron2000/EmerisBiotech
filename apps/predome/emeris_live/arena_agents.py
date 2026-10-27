"""Bounded two-model evidence review → forecast executor.

Evidence lookup is deterministic orchestration, not model-chosen browsing. Only
the injected provider adapters may make model requests. No shell, filesystem
tool, arbitrary URL fetch, retries, or output repair is exposed to a model.
"""
from copy import deepcopy
import hashlib
import math
from pathlib import Path
import re
import time

from . import arena_providers as providers
from .arena_attempt_diagnostics import sanitize_diagnostics


VERSION = 'arena-agents-1.1.0'
WORKFLOW = 'frozen_lookup_review_forecast'
TOOLS = ('frozen_evidence.list', 'frozen_evidence.lookup')
USAGE_FIELDS = ('input_tokens', 'output_tokens', 'reasoning_tokens', 'cache_read_input_tokens', 'cache_write_input_tokens')
FORECAST_FIELDS = ('status', 'probability', 'target_definition', 'reasoning', 'evidence_ids',
                   'supporting_evidence_ids', 'opposing_evidence_ids', 'key_assumptions', 'key_unknowns')
ATTEMPT_FIELDS = ('stage', 'requested_model', 'returned_model', 'request_sha256', 'prompt_sha256',
                  'response_sha256', 'input_sha256', 'usage', 'cost_usd', 'cost_amount',
                  'cost_currency', 'cost_basis', 'latency_ms', 'status', 'error_code', 'input_chars',
                  'input_bytes', 'max_output_tokens', 'timeout_seconds', 'finish_reason',
                  'visible_output_bytes', 'response_bytes', 'diagnostics')
MODEL_FIELDS = ('id', 'name', 'provider', 'requested_model', 'config_sha256', 'adapter_version', 'cost_currency')
BUDGET_FIELDS = {'max_requests', 'max_output_tokens', 'timeout_seconds', 'max_input_chars',
                 'max_cost_usd', 'max_cost_amount', 'cost_currency', 'budget_policy', 'stage_budgets',
                 'max_total_output_tokens', 'max_total_timeout_seconds'}
HASH = re.compile(r'[a-f0-9]{64}')
IDENTIFIER = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,99}')
MODEL_IDENTIFIER = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,99}')


class AgentContractError(ValueError):
    """Fixed public code; never include raw provider exceptions or configuration."""


def _sha(value):
    return providers.digest(value)


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _hash(value):
    return value if isinstance(value, str) and HASH.fullmatch(value) else None


def make_agent_spec(agent_id, reviewer_model_id, forecaster_model_id, *, tier='two-call-4096-v1', max_tool_calls=101):
    """Make an explicit spec; reverse the two IDs for a second distinct system."""
    return {'agent_id': agent_id, 'version': VERSION, 'tier': tier,
            'reviewer_model_id': reviewer_model_id, 'forecaster_model_id': forecaster_model_id,
            'tool_allowlist': list(TOOLS), 'max_tool_calls': max_tool_calls}


def _spec(value):
    expected = {'agent_id', 'version', 'tier', 'reviewer_model_id', 'forecaster_model_id', 'tool_allowlist', 'max_tool_calls'}
    if not isinstance(value, dict) or set(value) != expected or value.get('version') != VERSION:
        raise AgentContractError('invalid_agent_spec')
    if (any(not isinstance(value.get(k), str) or not IDENTIFIER.fullmatch(value[k]) for k in ('agent_id', 'tier'))
            or any(not isinstance(value.get(k), str) or not MODEL_IDENTIFIER.fullmatch(value[k])
                   for k in ('reviewer_model_id', 'forecaster_model_id'))):
        raise AgentContractError('invalid_agent_identity')
    if value['reviewer_model_id'] == value['forecaster_model_id'] or value['agent_id'] in (value['reviewer_model_id'], value['forecaster_model_id']):
        raise AgentContractError('distinct_component_models_required')
    if value['tool_allowlist'] != list(TOOLS):
        raise AgentContractError('agent_tools_not_allowlisted')
    if type(value['max_tool_calls']) is not int or not 2 <= value['max_tool_calls'] <= 101:
        raise AgentContractError('invalid_tool_budget')
    return deepcopy(value)


def _budget(value):
    if not isinstance(value, dict) or set(value) - BUDGET_FIELDS:
        raise AgentContractError('invalid_agent_budget')
    limits = {'max_requests': (2, 2), 'max_output_tokens': (128, 16384),
              'timeout_seconds': (5, 300), 'max_input_chars': (256, providers.MAX_PACKET_BYTES)}
    if any(type(value.get(k)) is not int or not low <= value[k] <= high for k, (low, high) in limits.items()):
        raise AgentContractError('invalid_agent_budget')
    if value.get('budget_policy', 'configured_rates') not in ('configured_rates', 'token_only_unpriced'):
        raise AgentContractError('invalid_agent_budget_policy')
    money = [k for k in ('max_cost_usd', 'max_cost_amount') if value.get(k) is not None]
    if len(money) > 1 or any(not _finite(value[k]) or not .000002 <= value[k] <= 100 for k in money):
        raise AgentContractError('invalid_agent_monetary_budget')
    if value.get('max_cost_amount') is not None and value.get('cost_currency') not in ('CNY', 'USD'):
        raise AgentContractError('invalid_agent_budget_currency')
    stages = value.get('stage_budgets')
    if stages is not None:
        if not isinstance(stages, dict) or set(stages) != {'evidence_review', 'forecast'}:
            raise AgentContractError('invalid_agent_stage_budget')
        for stage in stages.values():
            if (not isinstance(stage, dict) or set(stage) != {'max_output_tokens', 'timeout_seconds'}
                    or any(type(stage[k]) is not int or not low <= stage[k] <= value[k]
                           for k, low in (('max_output_tokens', 128), ('timeout_seconds', 5)))):
                raise AgentContractError('invalid_agent_stage_budget')
    else:
        stages = {role: {k: value[k] for k in ('max_output_tokens', 'timeout_seconds')}
                  for role in ('evidence_review', 'forecast')}
    for total_key, stage_key in (('max_total_output_tokens', 'max_output_tokens'), ('max_total_timeout_seconds', 'timeout_seconds')):
        required = sum(s[stage_key] for s in stages.values())
        if total_key in value and (type(value[total_key]) is not int or not required <= value[total_key] <= 2 * value[stage_key]):
            raise AgentContractError('agent_total_budget_insufficient')
    return deepcopy(value)


def _components(spec, models):
    if not isinstance(models, list) or any(not isinstance(m, dict) for m in models):
        raise AgentContractError('invalid_model_registry')
    result = []
    for role, key in (('evidence_review', 'reviewer_model_id'), ('forecast', 'forecaster_model_id')):
        rows = [m for m in models if m.get('id') == spec[key]]
        if len(rows) != 1:
            raise AgentContractError('component_model_not_unique')
        row = {k: deepcopy(rows[0].get(k)) for k in MODEL_FIELDS}
        if (not isinstance(row['requested_model'], str) or not row['requested_model']
                or not isinstance(row['provider'], str) or not row['provider']
                or not HASH.fullmatch(str(row['config_sha256']))):
            raise AgentContractError('component_version_and_config_required')
        result.append({'role': role, **row})
    if tuple(result[0][k] for k in ('provider', 'requested_model')) == tuple(result[1][k] for k in ('provider', 'requested_model')):
        raise AgentContractError('distinct_component_models_required')
    return result


def _packet(packet):
    try:
        clean = providers._packet(packet)
    except (ValueError, TypeError, providers.ProviderError):
        raise AgentContractError('invalid_frozen_evidence_packet') from None
    if any(not HASH.fullmatch(str(e.get('source_sha256'))) for e in clean['evidence']):
        raise AgentContractError('source_hash_required')
    if any(e.get('kind') in ('target_result', 'trial_result', 'adjudicated_result') or e.get('is_target_result') is True for e in packet['evidence']):
        raise AgentContractError('target_outcome_evidence_forbidden')
    return clean


def freeze_agent_contract(spec, packet, budget, models):
    """Freeze this dictionary on the assigned slot before the first paid call.

    Returned top-level execution fields match ArenaLedger.finish. Store the
    complete dictionary as agent_contract, including its component identities.
    """
    spec, budget, clean = _spec(spec), _budget(budget), _packet(packet)
    components = _components(spec, models)
    if 1 + len(clean['evidence']) > spec['max_tool_calls']:
        raise AgentContractError('tool_budget_exceeded')
    if len(providers.canonical(clean)) > budget['max_input_chars']:
        raise AgentContractError('input_budget_exceeded')
    currency = 'USD' if budget.get('max_cost_usd') is not None else budget.get('cost_currency')
    if (budget.get('max_cost_usd') is not None or budget.get('max_cost_amount') is not None) and any(c['cost_currency'] != currency for c in components):
        raise AgentContractError('component_budget_currency_mismatch')
    agent_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    provider_hash = providers.provider_implementation_sha256()
    prompt_hashes = [hashlib.sha256(p.encode()).hexdigest() for p in (providers.REVIEW_PROMPT, providers.SYSTEM_PROMPT)]
    config = {'agent_spec': spec, 'components': components, 'provider_version': providers.VERSION}
    identity = _sha({'agent_id': spec['agent_id'], 'components': [(c['role'], c['id'], c['requested_model']) for c in components]})
    return {
        'agent_spec': spec, 'components': components, 'provider': 'multi_model_agent',
        'model_id': spec['agent_id'], 'requested_model': spec['agent_id'] + ':requested:' + identity[:20],
        'config_sha256': _sha(config), 'workflow_version': VERSION + ':' + WORKFLOW,
        'expected_provider_input_sha256': _sha(clean), 'expected_prompt_sha256': _sha(prompt_hashes),
        'provider_implementation_sha256': _sha({'agent': agent_hash, 'provider': provider_hash}),
        'agent_implementation_sha256': agent_hash, 'component_implementation_sha256': provider_hash,
        'execution_budget_sha256': _sha(budget), 'stage_prompt_sha256': prompt_hashes,
        'tier': spec['tier'], 'execution_budget': budget,
        'maximum_provider_calls': 2,
        'maximum_output_tokens': sum(_stage_budget(budget, c['role'])['max_total_output_tokens'] for c in components),
        'maximum_timeout_seconds': sum(_stage_budget(budget, c['role'])['max_total_timeout_seconds'] for c in components),
        'maximum_tool_calls': spec['max_tool_calls'],
        'tool_selection': 'deterministic_all_registered_evidence_not_model_driven',
    }


class FrozenEvidenceTools:
    """Only two exact methods; untrusted text never selects executable actions."""
    def __init__(self, packet, maximum):
        self._items = {e['id']: deepcopy(e) for e in packet['evidence']}
        self.maximum = maximum
        self.trace = []

    def call(self, tool, **arguments):
        if tool not in TOOLS:
            raise AgentContractError('agent_tools_not_allowlisted')
        if len(self.trace) >= self.maximum:
            raise AgentContractError('tool_budget_exceeded')
        if tool == TOOLS[0] and not arguments:
            result = sorted(self._items)
        elif tool == TOOLS[1] and set(arguments) == {'evidence_id'} and arguments['evidence_id'] in self._items:
            result = deepcopy(self._items[arguments['evidence_id']])
        else:
            raise AgentContractError('invalid_frozen_lookup')
        self.trace.append({'sequence': len(self.trace) + 1, 'tool': tool, 'arguments': deepcopy(arguments),
                           'result_sha256': _sha(result), 'remaining_tool_calls': self.maximum - len(self.trace) - 1})
        return result


def _usage(value):
    value = value if isinstance(value, dict) else {}
    return {k: value.get(k) if type(value.get(k)) is int and value[k] >= 0 else None for k in USAGE_FIELDS}


def _stage_budget(budget, role):
    result = dict(budget, max_requests=1)
    stage = budget.get('stage_budgets', {}).get(role, {k: budget[k] for k in ('max_output_tokens', 'timeout_seconds')})
    result['stage_budgets'] = {role: deepcopy(stage)}
    result['max_total_output_tokens'] = stage['max_output_tokens']
    result['max_total_timeout_seconds'] = stage['timeout_seconds']
    for key in ('max_cost_usd', 'max_cost_amount'):
        if result.get(key) is not None:
            result[key] /= 2
    return result


def _stage_record(component, raw, input_hash):
    """Allowlisted summaries; no arbitrary dictionaries or raw model envelopes."""
    raw = raw if isinstance(raw, dict) else {}
    record = {'role': component['role'], 'model_id': component['id'], 'requested_model': component['requested_model'],
              'returned_model': raw.get('returned_model') if isinstance(raw.get('returned_model'), str) else None,
              'expected_config_sha256': component['config_sha256'], 'config_sha256': _hash(raw.get('config_sha256')),
              'status': raw.get('status') if isinstance(raw.get('status'), str) else 'failed',
              'error_code': raw.get('error_code') if isinstance(raw.get('error_code'), str) and re.fullmatch(r'[a-z0-9_]{1,100}', raw['error_code']) else None,
              'input_sha256': input_hash, 'provider_input_sha256': _hash(raw.get('input_sha256')),
              'review_sha256': _hash(raw.get('evidence_review_sha256')), 'usage': _usage(raw.get('usage')),
              'cost_amount': raw.get('cost_amount') if _finite(raw.get('cost_amount')) else None,
              'cost_usd': raw.get('cost_usd') if _finite(raw.get('cost_usd')) else None,
              'cost_currency': raw.get('cost_currency') if raw.get('cost_currency') in ('CNY', 'USD') else None,
              'attempts': []}
    for attempt in raw.get('attempts', []) if isinstance(raw.get('attempts'), list) else []:
        if not isinstance(attempt, dict):
            continue
        row = {}
        for key in ATTEMPT_FIELDS:
            value = attempt.get(key)
            if key == 'diagnostics':
                if key in attempt:
                    row[key] = sanitize_diagnostics(value)
            elif key == 'usage':
                row[key] = _usage(value)
            elif value is None or type(value) in (str, int, bool) or (type(value) is float and math.isfinite(value)):
                row[key] = value
        record['attempts'].append(row)
    return record


def _validate_stage(component, raw, record, expected_input, expected_prompt):
    if not isinstance(raw, dict) or raw.get('model_id') != component['id'] or raw.get('requested_model') != component['requested_model']:
        raise AgentContractError('component_model_contract_changed')
    if raw.get('config_sha256') != component['config_sha256'] or raw.get('input_sha256') != expected_input:
        raise AgentContractError('component_input_or_config_changed')
    if len(record['attempts']) != 1:
        raise AgentContractError('component_request_count_mismatch')
    if record['attempts'][0].get('prompt_sha256') != expected_prompt:
        raise AgentContractError('component_prompt_changed')
    if record['attempts'][0].get('input_sha256') != record['input_sha256']:
        raise AgentContractError('component_stage_input_changed')
    returned = record['returned_model']
    if not isinstance(returned, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}', returned):
        raise AgentContractError('component_returned_model_missing')


def _account(result, stages):
    attempts = [dict(a, agent_step=s['role'], model_id=s['model_id']) for s in stages for a in s['attempts']]
    result['attempts'] = attempts
    result['usage'] = {k: sum(s['usage'][k] for s in stages) if stages and all(s['usage'][k] is not None for s in stages) else None for k in USAGE_FIELDS}
    currencies = {s['cost_currency'] for s in stages if s['cost_currency']}
    currency = next(iter(currencies)) if len(currencies) == 1 else None
    result['cost_currency'] = currency
    complete = bool(stages) and len(stages) == result['agent_calls_dispatched']
    amounts = [s['cost_amount'] for s in stages]
    result['known_cost_amount'] = round(sum(v for v in amounts if v is not None), 10) if currency and any(v is not None for v in amounts) else None
    result['cost_amount'] = result['known_cost_amount'] if complete and currency and all(s['cost_currency'] == currency and s['cost_amount'] is not None for s in stages) else None
    dollars = [s['cost_usd'] for s in stages]
    result['known_cost_usd'] = round(sum(v for v in dollars if v is not None), 10) if any(v is not None for v in dollars) else None
    result['cost_usd'] = result['known_cost_usd'] if complete and all(v is not None for v in dollars) else None
    result['cost_breakdown'] = [{'currency': cur,
        'known_amount': round(sum(s['cost_amount'] for s in stages if s['cost_currency'] == cur and s['cost_amount'] is not None), 10),
        'complete': complete and all(s['cost_currency'] == cur and s['cost_amount'] is not None for s in stages)} for cur in sorted(currencies)
        if any(s['cost_currency'] == cur and s['cost_amount'] is not None for s in stages)]
    if any(s['cost_amount'] is not None or s['cost_usd'] is not None for s in stages):
        result['cost_basis'] = 'configured_rates_estimate'
        result['cost_note'] = '已知金额按配置费率估算；缺失部分保持未知，不同币种不换算，不是服务商账单。'
    else:
        result['cost_basis'] = 'unpriced'
        result['cost_note'] = '尚无可核对费用，价格或用量未齐全；未知不等于零费用，也不是服务商账单。'
    for output, key in (('prompt_sha256', 'prompt_sha256'), ('request_sha256', 'request_sha256'), ('provider_response_sha256', 'response_sha256')):
        values = [a.get(key) for a in attempts]
        result[output] = _sha(values) if values and all(isinstance(v, str) and HASH.fullmatch(v) for v in values) else None


def run_agent(spec, packet, *, budget, models, frozen_contract, review_fn=None, forecast_fn=None):
    """Execute one registered system once; caller preserves its denominator.

    frozen_contract is mandatory and must match code, prompt, model, budget,
    target, cutoff and evidence before either adapter is called. Exceptions from
    an adapter become a failure with unknown billing; no automatic retry occurs.
    """
    started = time.monotonic()
    spec = _spec(spec)
    if not isinstance(frozen_contract, dict):
        raise AgentContractError('frozen_agent_contract_required')
    result = {'model_id': spec['agent_id'], 'provider': 'multi_model_agent', 'workflow': WORKFLOW,
        'workflow_version': VERSION + ':' + WORKFLOW, 'adapter_version': VERSION,
        'status': 'failed', 'probability': None, 'reasoning': '', 'evidence_ids': [],
        'supporting_evidence_ids': [], 'opposing_evidence_ids': [], 'key_assumptions': [], 'key_unknowns': [],
        'requested_model': frozen_contract.get('requested_model'), 'returned_model': None,
        'config_sha256': frozen_contract.get('config_sha256'), 'input_sha256': None,
        'error_code': None, 'agent_calls_dispatched': 0, 'clinical_prediction_validated': False,
        'cost_basis': 'unpriced', 'cost_note': '费用尚未记录；未知不等于零费用。',
        'agent_trace': {'agent_id': spec['agent_id'], 'tier': spec['tier'], 'tools': [], 'stages': [],
                        'tool_selection': 'deterministic_all_registered_evidence_not_model_driven'}}
    stages = result['agent_trace']['stages']
    try:
        current = freeze_agent_contract(spec, packet, budget, models)
        if current != frozen_contract:
            raise AgentContractError('agent_frozen_contract_changed')
        clean = _packet(packet)
        result['input_sha256'] = current['expected_provider_input_sha256']
        result['agent_trace'].update(agent_implementation_sha256=current['agent_implementation_sha256'],
            component_implementation_sha256=current['component_implementation_sha256'],
            frozen_contract_sha256=_sha(current), execution_budget_sha256=current['execution_budget_sha256'])
        result['budget'] = deepcopy(budget)
        tools = FrozenEvidenceTools(clean, spec['max_tool_calls'])
        # Look up every frozen item in sorted order; preserve packet ordering for
        # the exact provider input, so registration input hashes remain identical.
        indexed = {ident: tools.call(TOOLS[1], evidence_id=ident) for ident in tools.call(TOOLS[0])}
        frozen_input = dict(clean, evidence=[indexed[e['id']] for e in clean['evidence']])
        result['agent_trace']['tools'] = tools.trace
        result['agent_trace']['tool_trace_sha256'] = _sha(tools.trace)
        review_fn = review_fn or providers.review_evidence
        forecast_fn = forecast_fn or providers.forecast
        review = None
        for index, component in enumerate(current['components']):
            if time.monotonic() - started > current['maximum_timeout_seconds']:
                raise AgentContractError('agent_total_timeout')
            stage_budget = _stage_budget(budget, component['role'])
            phase_input = frozen_input if index == 0 else dict(frozen_input, structured_evidence_review=review)
            result['agent_calls_dispatched'] += 1
            try:
                if index == 0:
                    raw = review_fn(component['id'], deepcopy(frozen_input), budget=deepcopy(stage_budget))
                else:
                    raw = forecast_fn(component['id'], deepcopy(frozen_input), workflow='single_pass',
                                      budget=deepcopy(stage_budget), evidence_review=deepcopy(review))
            except Exception:
                stages.append(_stage_record(component, {}, _sha(phase_input)))
                raise AgentContractError('agent_provider_exception_billing_unknown') from None
            record = _stage_record(component, raw, _sha(phase_input))
            record['stage_budget'] = deepcopy(stage_budget)
            stages.append(record)
            if time.monotonic() - started > current['maximum_timeout_seconds']:
                raise AgentContractError('agent_total_timeout')
            if not isinstance(raw, dict) or raw.get('status') not in (('reviewed',) if index == 0 else ('predicted', 'abstain', 'abstained')):
                if isinstance(raw, dict) and raw.get('status') in ('unavailable', 'timed_out'):
                    result['status'] = raw['status']
                raise AgentContractError('agent_component_failed')
            _validate_stage(component, raw, record, current['expected_provider_input_sha256'], current['stage_prompt_sha256'][index])
            if record['usage']['output_tokens'] is not None and record['usage']['output_tokens'] > stage_budget['max_total_output_tokens']:
                raise AgentContractError('agent_stage_output_budget_exceeded')
            if index == 0:
                if raw.get('probability') is not None:
                    raise AgentContractError('review_stage_must_not_predict')
                try:
                    review = providers._review_output(providers.canonical(raw.get('evidence_review')), frozen_input)
                except (providers.ProviderError, TypeError, ValueError):
                    raise AgentContractError('invalid_agent_review') from None
                record['review_sha256'] = _sha(review)
                result['evidence_review'] = deepcopy(review)
            else:
                if raw.get('evidence_review_sha256') != _sha(review):
                    raise AgentContractError('agent_review_not_bound_to_forecast')
                try:
                    output = {k: deepcopy(raw.get(k)) for k in FORECAST_FIELDS}
                    if output['status'] == 'abstained':
                        output['status'] = 'abstain'
                    output = providers._forecast_output(providers.canonical(output), frozen_input)
                except (providers.ProviderError, TypeError, ValueError):
                    raise AgentContractError('invalid_agent_forecast') from None
                result.update(output)
                result['returned_model'] = spec['agent_id'] + ':returned:' + _sha([(s['model_id'], s['returned_model']) for s in stages])[:20]
                result['evidence_review_sha256'] = _sha(review)
    except (AgentContractError, providers.ProviderError) as exc:
        result['error_code'] = str(exc)
        result['probability'] = None
        result['reasoning'] = '受控多模型流程未完成；保留已登记任务、阶段回执和已知用量，不重试或替换原输出。'
    finally:
        _account(result, stages)
        result['agent_trace_sha256'] = _sha(result['agent_trace'])
        result['latency_ms'] = round((time.monotonic() - started) * 1000)
    return result
