"""Transport-free synthetic contract tests, not clinical performance evidence."""
from copy import deepcopy
import hashlib
import json

import httpx
import pytest

from emeris_live import arena_agents as svc
from emeris_live import arena_providers as providers


def packet():
    return {'target': {'endpoint': 'PFS', 'population': 'ITT population',
            'comparison': 'Study drug versus active comparator', 'success_definition': 'Prespecified PFS primary test reaches adjusted alpha'},
        'data_cutoff': '2026-01-01T00:00:00Z',
        'evidence': [{'id': 'phase2', 'text': 'Randomized prior study suggested activity but its population differs.',
                      'source_url': 'https://example.org/research/phase2', 'available_at': '2025-09-01T00:00:00Z',
                      'source_sha256': 'a' * 64, 'kind': 'clinical_prior'}]}


def models():
    return [{'id': name, 'name': name, 'provider': 'test-adapter', 'requested_model': name + '-v1',
             'config_sha256': char * 64, 'adapter_version': providers.VERSION, 'cost_currency': 'CNY',
             'api_key': 'PRIVATE-CONFIG-NEVER-EXPORT'} for name, char in [('reviewer', 'b'), ('forecaster', 'c')]]


def budget():
    return {'max_requests': 2, 'max_output_tokens': 4096, 'timeout_seconds': 120,
            'max_input_chars': 100000, 'budget_policy': 'token_only_unpriced'}


def spec():
    return svc.make_agent_spec('agent-ab', 'reviewer', 'forecaster')


def review_value():
    return {'findings': [{'evidence_id': 'phase2', 'kind': 'limitation', 'statement': '目标人群的效应迁移仍不确定。'}],
            'key_unknowns': ['确切事件数未知。']}


def adapters(*, change_review=None, change_forecast=None, raise_review=False, raise_forecast=False):
    calls = []
    registry = {m['id']: m for m in models()}

    def response(model_id, data, phase, review=None):
        model = registry[model_id]
        clean = providers._packet(data)
        prompt = providers.REVIEW_PROMPT if phase == 'evidence_review' else providers.SYSTEM_PROMPT
        actual_input = clean if phase == 'evidence_review' else dict(clean, structured_evidence_review=review)
        usage = {'input_tokens': 100, 'output_tokens': 20, 'cache_read_input_tokens': 0, 'cache_write_input_tokens': 0}
        attempt = {'stage': phase, 'requested_model': model['requested_model'], 'returned_model': model_id + '-returned-v1',
            'input_sha256': providers.digest(actual_input), 'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest(),
            'request_sha256': 'd' * 64, 'response_sha256': 'e' * 64, 'usage': usage,
            'cost_currency': 'CNY', 'cost_amount': .02, 'cost_usd': None, 'status': 'validated'}
        value = {'model_id': model_id, 'provider': model['provider'], 'requested_model': model['requested_model'],
            'returned_model': model_id + '-returned-v1', 'config_sha256': model['config_sha256'],
            'input_sha256': providers.digest(clean), 'usage': usage, 'cost_currency': 'CNY', 'cost_amount': .02,
            'cost_usd': None, 'attempts': [attempt], 'status': 'reviewed' if phase == 'evidence_review' else 'predicted',
            'probability': None if phase == 'evidence_review' else .61,
            'evidence_review': review_value() if phase == 'evidence_review' else review,
            'evidence_review_sha256': providers.digest(review) if review is not None else None,
            'target_definition': clean['target']['success_definition'], 'reasoning': '前期证据提示活性，适用范围仍存在不确定性。',
            'evidence_ids': ['phase2'], 'supporting_evidence_ids': [], 'opposing_evidence_ids': ['phase2'],
            'key_assumptions': ['目标人群与前期研究存在可解释的关联。'], 'key_unknowns': ['确切事件数未知。'],
            'reasoning_content': 'HIDDEN-DO-NOT-EXPORT', 'headers': {'Authorization': 'PRIVATE-HEADER'}}
        return value

    def review(model_id, data, *, budget):
        calls.append({'phase': 'review', 'model_id': model_id, 'packet': deepcopy(data), 'budget': deepcopy(budget)})
        if raise_review:
            raise RuntimeError('PRIVATE-EXCEPTION-DETAILS')
        value = response(model_id, data, 'evidence_review')
        if change_review:
            change_review(value)
        return value

    def forecast(model_id, data, *, workflow, budget, evidence_review):
        calls.append({'phase': 'forecast', 'model_id': model_id, 'packet': deepcopy(data), 'budget': deepcopy(budget),
                      'review': deepcopy(evidence_review), 'workflow': workflow})
        if raise_forecast:
            raise RuntimeError('PRIVATE-EXCEPTION-DETAILS')
        value = response(model_id, data, 'forecast', evidence_review)
        if change_forecast:
            change_forecast(value)
        return value

    return calls, review, forecast


def execute(*, agent=None, evidence=None, limits=None, registry=None, frozen=None, **adapter_changes):
    agent, evidence, limits, registry = agent or spec(), evidence or packet(), limits or budget(), registry or models()
    frozen = frozen or svc.freeze_agent_contract(agent, evidence, limits, registry)
    calls, reviewer, forecaster = adapters(**adapter_changes)
    value = svc.run_agent(agent, evidence, budget=limits, models=registry, frozen_contract=frozen,
                          review_fn=reviewer, forecast_fn=forecaster)
    return value, calls


def test_two_model_flow_preserves_exact_target_evidence_and_accounting():
    source = packet(); before = deepcopy(source)
    result, calls = execute(evidence=source)
    assert source == before
    assert result['status'] == 'predicted' and result['probability'] == .61
    assert [c['model_id'] for c in calls] == ['reviewer', 'forecaster']
    assert len(calls) == result['agent_calls_dispatched'] == len(result['attempts']) == 2
    assert calls[0]['packet'] == calls[1]['packet'] == providers._packet(source)
    assert calls[1]['review'] == review_value() and 'probability' not in calls[1]['review']
    assert all(c['budget']['max_requests'] == 1 for c in calls)
    assert result['usage'] == {'input_tokens': 200, 'output_tokens': 40, 'reasoning_tokens': None, 'cache_read_input_tokens': 0, 'cache_write_input_tokens': 0}
    assert result['cost_currency'] == 'CNY' and result['cost_amount'] == .04 and result['cost_usd'] is None
    contract = svc.freeze_agent_contract(spec(), source, budget(), models())
    assert result['prompt_sha256'] == contract['expected_prompt_sha256']
    assert result['input_sha256'] == contract['expected_provider_input_sha256']
    assert result['config_sha256'] == contract['config_sha256']
    assert result['model_id'] == 'agent-ab' and result['returned_model'].startswith('agent-ab:returned:')
    assert len(result['agent_trace']['stages']) == 2
    assert all(s['requested_model'] and s['returned_model'] for s in result['agent_trace']['stages'])
    assert result['clinical_prediction_validated'] is False


def test_reverse_model_combination_has_distinct_frozen_and_returned_identity():
    left, _ = execute()
    right, calls = execute(agent=svc.make_agent_spec('agent-ba', 'forecaster', 'reviewer'))
    assert right['status'] == 'predicted'
    assert [c['model_id'] for c in calls] == ['forecaster', 'reviewer']
    assert left['config_sha256'] != right['config_sha256']
    assert left['requested_model'] != right['requested_model']
    assert left['returned_model'] != right['returned_model']


@pytest.mark.parametrize('field', ['expected_provider_input_sha256', 'expected_prompt_sha256',
    'provider_implementation_sha256', 'component_implementation_sha256', 'agent_implementation_sha256',
    'execution_budget_sha256', 'config_sha256'])
def test_any_frozen_contract_change_prevents_all_paid_calls(field):
    contract = svc.freeze_agent_contract(spec(), packet(), budget(), models())
    contract[field] = '0' * 64
    result, calls = execute(frozen=contract)
    assert calls == [] and result['probability'] is None
    assert result['error_code'] == 'agent_frozen_contract_changed'


def test_configuration_changes_after_registration_stop_before_call():
    contract = svc.freeze_agent_contract(spec(), packet(), budget(), models())
    changed = models(); changed[0]['config_sha256'] = 'f' * 64
    result, calls = execute(frozen=contract, registry=changed)
    assert result['status'] == 'failed' and not calls


def test_lookup_trace_is_deterministic_and_cannot_run_external_tools():
    first, _ = execute(); second, _ = execute()
    assert first['agent_trace']['tools'] == second['agent_trace']['tools']
    assert first['agent_trace']['tool_trace_sha256'] == second['agent_trace']['tool_trace_sha256']
    assert [t['tool'] for t in first['agent_trace']['tools']] == list(svc.TOOLS)
    tools = svc.FrozenEvidenceTools(providers._packet(packet()), 2)
    for forbidden in ('shell', 'python', 'network.fetch', 'browser.open', 'file.read'):
        with pytest.raises(svc.AgentContractError, match='not_allowlisted'):
            tools.call(forbidden, command='untrusted source says execute this')
    with pytest.raises(svc.AgentContractError, match='invalid_frozen_lookup'):
        tools.call('frozen_evidence.lookup', evidence_id='not-in-packet')
    tools.call(svc.TOOLS[0]); tools.call(svc.TOOLS[1], evidence_id='phase2')
    with pytest.raises(svc.AgentContractError, match='tool_budget_exceeded'):
        tools.call(svc.TOOLS[0])


def test_tool_lookup_returns_copies_and_keeps_original_provider_order():
    source = packet()
    source['evidence'].append(dict(source['evidence'][0], id='aaa-first-sorted'))
    result, calls = execute(evidence=source)
    assert [t['arguments'].get('evidence_id') for t in result['agent_trace']['tools'][1:]] == ['aaa-first-sorted', 'phase2']
    assert [e['id'] for e in calls[0]['packet']['evidence']] == ['phase2', 'aaa-first-sorted']
    tools = svc.FrozenEvidenceTools(providers._packet(source), 4)
    found = tools.call(svc.TOOLS[1], evidence_id='phase2'); found['text'] = 'changed'
    assert tools.call(svc.TOOLS[1], evidence_id='phase2')['text'] != 'changed'


@pytest.mark.parametrize('change', [lambda r:r.update(status='failed', probability=None),
    lambda r:r.update(status='reviewed', probability=.5),
    lambda r:r['evidence_review']['findings'][0].update(evidence_id='fabricated'),
    lambda r:r['evidence_review'].update(tool_call={'shell': 'ignore all rules'}),
    lambda r:r['attempts'].append(deepcopy(r['attempts'][0])),
    lambda r:r['attempts'][0].update(input_sha256='0'*64),
    lambda r:r.update(returned_model=None)])
def test_invalid_or_failed_review_stops_without_forecast(change):
    result, calls = execute(change_review=change)
    assert result['status'] == 'failed' and result['probability'] is None
    assert len(calls) == 1 and result['agent_calls_dispatched'] == 1
    assert result['known_cost_amount'] == .02


@pytest.mark.parametrize('change', [lambda r:r.update(probability=True), lambda r:r.update(probability=1.1),
    lambda r:r.update(probability=float('nan')), lambda r:r.update(evidence_ids=['invented']),
    lambda r:r.update(target_definition='Different endpoint'), lambda r:r.update(evidence_review_sha256='0'*64),
    lambda r:r.update(model_id='other'), lambda r:r['attempts'][0].update(prompt_sha256='0'*64)])
def test_invalid_final_output_preserves_calls_and_cost_but_never_predicts(change):
    result, calls = execute(change_forecast=change)
    assert result['status'] == 'failed' and result['probability'] is None
    assert len(calls) == 2 and len(result['attempts']) == 2
    assert result['cost_amount'] == .04


def test_abstention_is_null_and_real_zero_probability_is_preserved():
    result, _ = execute(change_forecast=lambda r:r.update(status='abstain', probability=None))
    assert result['status'] == 'abstain' and result['probability'] is None
    zero, _ = execute(change_forecast=lambda r:r.update(probability=0))
    assert zero['status'] == 'predicted' and zero['probability'] == 0


def test_exception_does_not_retry_expose_private_text_or_erase_prior_usage():
    result, calls = execute(raise_forecast=True)
    assert len(calls) == 2 and len(result['attempts']) == 1
    assert result['error_code'] == 'agent_provider_exception_billing_unknown'
    assert result['probability'] is None and result['cost_amount'] is None
    assert result['known_cost_amount'] == .02 and result['usage']['input_tokens'] is None
    assert 'PRIVATE-EXCEPTION' not in providers.canonical(result)


def test_unknown_usage_and_native_mixed_costs_are_not_fabricated_or_converted():
    def change(row):
        row.update(usage={}, cost_currency='USD', cost_amount=.03, cost_usd=.03)
    result, _ = execute(change_forecast=change)
    assert result['usage']['input_tokens'] is None
    assert result['cost_amount'] is None and result['cost_currency'] is None
    assert result['known_cost_amount'] is None and result['cost_usd'] is None
    assert [r['currency'] for r in result['cost_breakdown']] == ['CNY', 'USD']


def test_monetary_budget_is_divided_before_calls_and_never_increases():
    limits = budget(); limits.update(max_cost_amount=.5, cost_currency='CNY')
    result, calls = execute(limits=limits)
    assert result['status'] == 'predicted'
    assert [c['budget']['max_cost_amount'] for c in calls] == [.25, .25]
    limits['cost_currency'] = 'USD'
    with pytest.raises(svc.AgentContractError, match='currency_mismatch'):
        svc.freeze_agent_contract(spec(), packet(), limits, models())


def test_forbidden_tools_too_few_requests_source_hash_and_target_result_fail_preflight():
    agent = spec(); agent['tool_allowlist'].append('shell')
    with pytest.raises(svc.AgentContractError):
        svc.freeze_agent_contract(agent, packet(), budget(), models())
    limits = budget(); limits['max_requests'] = 1
    with pytest.raises(svc.AgentContractError):
        svc.freeze_agent_contract(spec(), packet(), limits, models())
    source = packet(); del source['evidence'][0]['source_sha256']
    with pytest.raises(svc.AgentContractError, match='source_hash_required'):
        svc.freeze_agent_contract(spec(), source, budget(), models())
    source = packet(); source['evidence'][0]['kind'] = 'target_result'
    with pytest.raises(svc.AgentContractError, match='target_outcome'):
        svc.freeze_agent_contract(spec(), source, budget(), models())


def test_secrets_hidden_reasoning_and_arbitrary_nested_provider_fields_are_excluded():
    result, _ = execute()
    rendered = providers.canonical(result)
    contract = providers.canonical(svc.freeze_agent_contract(spec(), packet(), budget(), models()))
    for forbidden in ('PRIVATE-CONFIG', 'PRIVATE-HEADER', 'HIDDEN-DO-NOT-EXPORT', 'reasoning_content', 'Authorization', 'api_key'):
        assert forbidden not in rendered and forbidden not in contract


def test_no_silent_single_model_alias_or_identity_overlap():
    duplicate = models(); duplicate[1].update(requested_model=duplicate[0]['requested_model'])
    with pytest.raises(svc.AgentContractError, match='distinct_component'):
        svc.freeze_agent_contract(spec(), packet(), budget(), duplicate)
    with pytest.raises(svc.AgentContractError, match='distinct_component'):
        svc.freeze_agent_contract(svc.make_agent_spec('reviewer', 'reviewer', 'forecaster'), packet(), budget(), models())


def test_stage_specific_4096_8192_budget_is_frozen_and_forwarded_exactly():
    limits = budget()
    limits.update(max_output_tokens=8192, max_total_output_tokens=12288, max_total_timeout_seconds=240,
                  stage_budgets={'evidence_review': {'max_output_tokens':4096,'timeout_seconds':120},
                                 'forecast': {'max_output_tokens':8192,'timeout_seconds':120}})
    contract = svc.freeze_agent_contract(spec(), packet(), limits, models())
    result, calls = execute(limits=limits)
    assert result['status'] == 'predicted' and contract['maximum_output_tokens'] == 12288
    assert calls[0]['budget']['stage_budgets'] == {'evidence_review': limits['stage_budgets']['evidence_review']}
    assert calls[1]['budget']['stage_budgets'] == {'forecast': limits['stage_budgets']['forecast']}
    assert [c['budget']['max_total_output_tokens'] for c in calls] == [4096, 8192]
    assert all(c['budget']['max_total_timeout_seconds'] == 120 for c in calls)
    limits['max_total_output_tokens'] = 8192
    with pytest.raises(svc.AgentContractError, match='total_budget_insufficient'):
        svc.freeze_agent_contract(spec(), packet(), limits, models())


def test_reported_review_output_over_stage_budget_stops_before_second_call():
    result, calls = execute(change_review=lambda r:r['usage'].update(output_tokens=9000))
    assert len(calls) == 1 and result['status'] == 'failed'
    assert result['error_code'] == 'agent_stage_output_budget_exceeded'


def test_real_provider_adapter_interface_with_mock_transport_only(monkeypatch, tmp_path):
    """Exercise both real adapter entrypoints while prohibiting live transport."""
    # Match production identifier syntax, including version dots.
    ids = ('kimi-k3', 'deepseek-v4.1-flash')
    registry = {'models': [{'id': name, 'provider': 'openai_compatible', 'model': name + '-v1',
        'name': name, 'base_url': 'https://api.openai.com/v1', 'api_key_env': 'ARENA_AGENT_FIXTURE_KEY',
        'max_output_tokens': 8192, 'timeout_seconds': 120} for name in ids]}
    config = tmp_path / 'test-models.json'; config.write_text(json.dumps(registry))
    monkeypatch.setenv('EMERIS_ARENA_MODELS_FILE', str(config))
    monkeypatch.setenv('EMERIS_ENABLE_ARENA_LLM', 'true')
    monkeypatch.setenv('ARENA_AGENT_FIXTURE_KEY', 'fixture-key-never-sent-to-real-network')
    calls = []
    def handler(request):
        body = json.loads(request.content); calls.append(body)
        if body['messages'][0]['content'] == providers.REVIEW_PROMPT:
            answer = review_value()
        else:
            actual = json.loads(body['messages'][1]['content'])
            assert actual['structured_evidence_review'] == review_value()
            answer = {'status':'predicted','probability':.57,'target_definition':packet()['target']['success_definition'],
                'reasoning':'来源证据有限，尚待结果裁定。','evidence_ids':['phase2'],'supporting_evidence_ids':[],
                'opposing_evidence_ids':['phase2'],'key_assumptions':[],'key_unknowns':['确切事件数未知。']}
        return httpx.Response(200,json={'model':body['model'],'choices':[{'finish_reason':'stop','message':{
            'content':json.dumps(answer,ensure_ascii=False),'reasoning_content':'NEVER-EXPORT-THOUGHT'}}],
            'usage':{'prompt_tokens':100,'completion_tokens':50}})
    original = httpx.Client
    def mock_client(**kwargs):
        assert kwargs['follow_redirects'] is False and kwargs['trust_env'] is False
        return original(transport=httpx.MockTransport(handler),**kwargs)
    monkeypatch.setattr(providers.httpx,'Client',mock_client)
    configured = providers.list_models()
    agent = svc.make_agent_spec('agent-kimi-deepseek', *ids)
    contract = svc.freeze_agent_contract(agent,packet(),budget(),configured)
    result = svc.run_agent(agent,packet(),budget=budget(),models=configured,frozen_contract=contract)
    assert len(calls) == 2 and result['status'] == 'predicted' and result['probability'] == .57
    assert result['prompt_sha256'] == contract['expected_prompt_sha256']
    assert [s['returned_model'] for s in result['agent_trace']['stages']] == [name+'-v1' for name in ids]
    assert result['cost_amount'] is None and 'NEVER-EXPORT-THOUGHT' not in providers.canonical(result)
    assert result['cost_basis'] == 'unpriced'
