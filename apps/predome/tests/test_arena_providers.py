"""Transport-isolated adapter tests, not live API or forecasting validation."""
from copy import deepcopy
import json

import httpx
import pytest

from emeris_live import arena_providers as svc


KEY = 'fixture-secret-never-sent-to-a-real-service'


def packet():
    return {'target': {'endpoint': 'PFS', 'population': 'ITT population',
                       'comparison': 'Study drug versus active comparator',
                       'success_definition': 'Prespecified PFS primary test favors treatment at its adjusted alpha'},
            'data_cutoff': '2026-01-01',
            'evidence': [{'id': 'phase2', 'text': 'Randomized prior study reported efficacy, but population transport remains uncertain.',
                          'source_url': 'https://example.org/research/phase2', 'availability_date': '2025-09-01'}]}


def answer(**changes):
    value = {'status': 'predicted', 'probability': .6,
             'target_definition': packet()['target']['success_definition'], 'reasoning': '先前随机研究提供有限支持。',
             'evidence_ids': ['phase2'], 'supporting_evidence_ids': ['phase2'], 'opposing_evidence_ids': [],
             'key_assumptions': ['研究间效应可迁移。'], 'key_unknowns': ['确切事件数未知。']}
    value.update(changes)
    return value


def envelope(provider, content=None, **changes):
    text = json.dumps(answer() if content is None else content, ensure_ascii=False)
    if provider == 'anthropic':
        value = {'model': 'fixture-returned-v1', 'stop_reason': 'end_turn',
                 'content': [{'type': 'thinking', 'thinking': 'HIDDEN-REASONING-DO-NOT-PUBLISH'}, {'type': 'text', 'text': text}],
                 'usage': {'input_tokens': 10, 'output_tokens': 8}}
    elif provider == 'gemini':
        value = {'modelVersion': 'fixture-returned-v1',
                 'candidates': [{'finishReason': 'STOP', 'content': {'parts': [
                     {'thought': True, 'text': 'HIDDEN-REASONING-DO-NOT-PUBLISH'}, {'text': text}]}}],
                 'usageMetadata': {'promptTokenCount': 10, 'candidatesTokenCount': 6, 'thoughtsTokenCount': 2}}
    else:
        value = {'model': 'fixture-returned-v1', 'choices': [{'finish_reason': 'stop',
                 'message': {'content': text, 'reasoning_content': 'HIDDEN-REASONING-DO-NOT-PUBLISH'}}],
                 'usage': {'prompt_tokens': 10, 'completion_tokens': 8}}
    value.update(changes)
    return value


def configure(monkeypatch, tmp_path, provider='openai', *, pricing=True, **changes):
    row = {'id': 'fixture', 'name': 'Fixture model, tests only', 'provider': provider,
           'model': 'fixture-requested-v1', 'api_key_env': 'ARENA_TEST_KEY'}
    if provider == 'openai_compatible':
        row['base_url'] = 'https://api.openai.com/v1'
    if pricing:
        row['pricing'] = {'input_usd_per_million': 1, 'output_usd_per_million': 2,
                          'source_url': 'https://example.org/fixture-prices-not-real', 'verified_at': '2026-01-01'}
    row.update(changes)
    path = tmp_path/'models.json'
    path.write_text(json.dumps({'models': [row]}))
    monkeypatch.setenv('EMERIS_ARENA_MODELS_FILE', str(path))
    monkeypatch.setenv('EMERIS_ENABLE_ARENA_LLM', 'true')
    monkeypatch.setenv('ARENA_TEST_KEY', KEY)
    return path


def transport(monkeypatch, handler):
    original = httpx.Client
    calls = []
    def capture(request):
        calls.append(request)
        return handler(request)
    def factory(**kwargs):
        assert kwargs['follow_redirects'] is False
        assert kwargs['trust_env'] is False
        return original(transport=httpx.MockTransport(capture), **kwargs)
    monkeypatch.setattr(svc.httpx, 'Client', factory)
    return calls


@pytest.mark.parametrize('provider', ['openai', 'openai_compatible', 'deepseek', 'anthropic', 'gemini'])
def test_all_protocols_normalize_valid_response_and_hide_thoughts(monkeypatch, tmp_path, provider):
    configure(monkeypatch, tmp_path, provider)
    calls = transport(monkeypatch, lambda request: httpx.Response(200, json=envelope(provider)))
    original = packet(); before = deepcopy(original)
    result = svc.forecast('fixture', original, budget={'max_output_tokens': 512})
    assert original == before
    assert result['status'] == 'predicted' and result['probability'] == .6
    assert result['returned_model'] == 'fixture-returned-v1'
    assert result['requested_model'] == 'fixture-requested-v1'
    assert result['usage']['input_tokens'] == 10 and result['usage']['output_tokens'] == 8
    assert result['cost_usd'] == pytest.approx(.000026)
    assert result['cost_basis'] == 'configured_rates_estimate'
    assert result['attempts'][0]['status'] == 'validated' and len(calls) == 1
    assert all(len(result[k]) == 64 for k in ('input_sha256', 'config_sha256', 'request_sha256', 'prompt_sha256', 'provider_response_sha256'))
    serialized = json.dumps(result)
    assert KEY not in serialized and 'HIDDEN-REASONING' not in serialized
    body = json.loads(calls[0].content)
    assert KEY not in calls[0].url.query.decode()
    assert KEY not in json.dumps(body)
    if provider == 'anthropic':
        assert calls[0].url.path == '/v1/messages' and calls[0].headers['x-api-key'] == KEY
        assert body['max_tokens'] == 512 and body['system'] == svc.SYSTEM_PROMPT
    elif provider == 'gemini':
        assert calls[0].url.path.endswith('/models/fixture-requested-v1:generateContent')
        assert calls[0].headers['x-goog-api-key'] == KEY
        assert body['generationConfig']['maxOutputTokens'] == 512
    else:
        assert calls[0].url.path.endswith('/chat/completions')
        assert body['max_completion_tokens' if provider == 'openai' else 'max_tokens'] == 512
        assert body['response_format'] == {'type': 'json_object'}


def test_registry_is_configuration_not_live_verification(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    models = svc.list_models()
    assert models[0]['available'] is True
    assert models[0]['availability_basis'] == 'configuration_only_not_live_probe'
    assert 'base_url' not in models[0] and 'api_key_env' not in models[0]
    monkeypatch.delenv('ARENA_TEST_KEY')
    assert svc.list_models()[0]['reason'] == 'credential_missing'
    result = svc.forecast('fixture', packet())
    assert result['status'] == 'unavailable' and result['probability'] is None and result['attempts'] == []


def test_default_registry_does_not_invent_model_ids(monkeypatch):
    monkeypatch.delenv('EMERIS_ARENA_MODELS_FILE', raising=False)
    for provider in svc.DEFAULTS:
        monkeypatch.delenv('EMERIS_ARENA_'+provider.upper()+'_MODEL', raising=False)
    assert len(svc.list_models()) == 4
    assert all(m['model'] is None and not m['available'] for m in svc.list_models())


@pytest.mark.parametrize('base_url', ['http://api.openai.com/v1', 'https://127.0.0.1/v1',
                                     'https://api.openai.com/v1?key=secret', 'https://secret@api.openai.com/v1',
                                     'https://attacker.example/v1', 'https://localhost/v1'])
def test_unsafe_provider_config_fails_without_disclosing_endpoint(monkeypatch, tmp_path, base_url):
    configure(monkeypatch, tmp_path, base_url=base_url)
    state = svc.registry_status()
    assert state['models'] == [] and not state['configured']
    assert base_url not in json.dumps(state)
    assert svc.forecast('fixture', packet())['attempts'] == []


@pytest.mark.parametrize('changes,code', [
    ({'probability': True}, 'invalid_probability'), ({'probability': -1}, 'invalid_probability'),
    ({'probability': 1.1}, 'invalid_probability'), ({'probability': '0.6'}, 'invalid_probability'),
    ({'probability': None}, 'invalid_probability'), ({'probability': float('nan')}, 'nonfinite_json'),
    ({'status': 'abstain'}, 'invalid_probability'), ({'target_definition': 'Different endpoint'}, 'target_mismatch'),
    ({'evidence_ids': ['invented']}, 'unsupported_citation'),
    ({'supporting_evidence_ids': ['invented']}, 'unsupported_citation'),
    ({'evidence_ids': [], 'supporting_evidence_ids': []}, 'prediction_without_evidence'),
    ({'extra_provider_claim': 'not allowed'}, 'invalid_output')])
def test_invalid_forecast_never_becomes_probability_or_retry(monkeypatch, tmp_path, changes, code):
    configure(monkeypatch, tmp_path)
    calls = transport(monkeypatch, lambda r: httpx.Response(200, json=envelope('openai', answer(**changes))))
    result = svc.forecast('fixture', packet())
    assert result['status'] == 'failed' and result['probability'] is None
    assert result['error_code'] == code and len(calls) == 1
    assert result['attempts'][0]['status'] == 'invalid_output'
    assert result['cost_usd'] is not None  # Failed parsing still spent tokens.


def test_abstention_is_valid_and_keeps_null_probability(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    transport(monkeypatch, lambda r: httpx.Response(200, json=envelope('openai', answer(status='abstain', probability=None))))
    result = svc.forecast('fixture', packet())
    assert result['status'] == 'abstain' and result['probability'] is None and result['error_code'] is None


@pytest.mark.parametrize('change,code', [
    ({'availability_date': '2026-02-01'}, 'evidence_after_cutoff'),
    ({'availability_date': None}, 'evidence_date_missing'),
    ({'source_url': None}, 'evidence_source_missing'),
    ({'eligible_for_forecast': False}, 'evidence_not_eligible')])
def test_evidence_gates_run_before_transport(monkeypatch, tmp_path, change, code):
    configure(monkeypatch, tmp_path)
    value = packet(); value['evidence'][0].update(change)
    calls = transport(monkeypatch, lambda r: pytest.fail('Network must not be called'))
    result = svc.forecast('fixture', value)
    assert result['error_code'] == code and calls == []


def test_packet_projection_excludes_peer_forecasts_and_actual_results(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    calls = transport(monkeypatch, lambda r: httpx.Response(200, json=envelope('openai')))
    value = packet(); value.update(peer_outputs='LEAKED-PEER', actual_result='LEAKED-RESULT')
    assert svc.forecast('fixture', value)['status'] == 'predicted'
    assert b'LEAKED-' not in calls[0].content


def test_shared_review_uses_same_model_and_preserves_every_paid_step(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    review = {'findings': [{'evidence_id': 'phase2', 'kind': 'limitation', 'statement': '研究人群不同。'}], 'key_unknowns': ['可迁移性未知。']}
    responses = iter([envelope('openai', review), envelope('openai')])
    calls = transport(monkeypatch, lambda r: httpx.Response(200, json=next(responses)))
    result = svc.forecast('fixture', packet(), workflow='evidence_review')
    assert result['status'] == 'predicted' and len(calls) == 2
    assert result['cost_usd'] == pytest.approx(.000052)
    assert result['usage']['input_tokens'] == 20 and result['evidence_review'] == review
    first, second = [json.loads(r.content) for r in calls]
    assert first['model'] == second['model']
    assert 'structured_evidence_review' not in first['messages'][1]['content']
    assert json.loads(second['messages'][1]['content'])['structured_evidence_review'] == review
    assert [a['stage'] for a in result['attempts']] == ['evidence_review', 'forecast']


def test_bad_review_stops_second_call_and_records_cost(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    bad = {'findings': [{'evidence_id': ['invalid'], 'kind': 'support', 'statement': 'Invalid ID'}], 'key_unknowns': []}
    calls = transport(monkeypatch, lambda r: httpx.Response(200, json=envelope('openai', bad)))
    result = svc.forecast('fixture', packet(), workflow='evidence_review')
    assert len(calls) == 1 and result['error_code'] == 'invalid_review'
    assert result['probability'] is None and result['cost_usd'] is not None


def test_unknown_prices_fail_closed_for_usd_budget(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path, pricing=False)
    calls = transport(monkeypatch, lambda r: pytest.fail('No request with unpriced USD budget'))
    result = svc.forecast('fixture', packet(), budget={'max_cost_usd': 1})
    assert result['error_code'] == 'budget_price_unknown' and result['cost_usd'] is None and calls == []


def test_unpriced_calls_require_explicit_token_policy(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path, pricing=False)
    calls = transport(monkeypatch, lambda r: httpx.Response(200, json=envelope('openai')))
    assert svc.forecast('fixture', packet())['error_code'] == 'explicit_unpriced_policy_required'
    assert calls == []
    result = svc.forecast('fixture', packet(), budget={'budget_policy': 'token_only_unpriced',
                          'max_output_tokens': 1024, 'max_requests': 1, 'max_input_chars': 4000})
    assert result['status'] == 'predicted' and len(calls) == 1
    assert result['cost_usd'] is None and result['cost_amount'] is None
    assert result['budget']['monetary_limit_enforced'] is False


def test_cny_estimates_are_not_relabelled_usd(monkeypatch, tmp_path):
    path = configure(monkeypatch, tmp_path, pricing=False)
    config = json.loads(path.read_text())
    config['models'][0]['pricing'] = {'currency': 'CNY', 'input_per_million': 1, 'output_per_million': 2,
                                     'source_url': 'https://example.org/prices', 'verified_at': '2026-01-01'}
    path.write_text(json.dumps(config))
    calls = transport(monkeypatch, lambda r: httpx.Response(200, json=envelope('openai')))
    result = svc.forecast('fixture', packet(), budget={'max_cost_amount': 1, 'cost_currency': 'CNY'})
    assert result['status'] == 'predicted' and result['cost_currency'] == 'CNY'
    assert result['cost_amount'] == pytest.approx(.000026) and result['cost_usd'] is None
    assert result['attempts'][0]['cost_usd'] is None
    assert svc.list_models()[0]['cost_currency'] == 'CNY'
    assert svc.forecast('fixture', packet(), budget={'max_cost_usd': 1})['error_code'] == 'budget_currency_mismatch'
    assert len(calls) == 1


def test_operator_allowlisted_gateway_and_packet_limit(monkeypatch, tmp_path):
    monkeypatch.setenv('EMERIS_ARENA_ALLOWED_PROVIDER_HOSTS', 'sem.ensureok.ai')
    configure(monkeypatch, tmp_path, provider='openai_compatible', base_url='https://sem.ensureok.ai/v1')
    calls = transport(monkeypatch, lambda r: httpx.Response(200, json=envelope('openai')))
    assert svc.forecast('fixture', packet(), budget={'max_input_chars': 256})['error_code'] == 'input_budget_exceeded'
    assert calls == []
    assert svc.forecast('fixture', packet())['status'] == 'predicted'
    assert str(calls[0].url) == 'https://sem.ensureok.ai/v1/chat/completions'


def test_cost_and_call_budgets_enforced_before_request(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    calls = transport(monkeypatch, lambda r: pytest.fail('No over-budget request'))
    assert svc.forecast('fixture', packet(), budget={'max_cost_usd': .000001})['error_code'] == 'cost_budget_exceeded'
    assert svc.forecast('fixture', packet(), workflow='evidence_review', budget={'max_requests': 1})['error_code'] == 'request_budget_exceeded'
    assert calls == []


def test_missing_usage_or_cache_pricing_does_not_report_zero_cost(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    calls = transport(monkeypatch, lambda r: httpx.Response(200, json=envelope('openai', usage={})))
    result = svc.forecast('fixture', packet())
    assert result['status'] == 'predicted' and result['cost_usd'] is None
    assert result['usage']['input_tokens'] is None and len(calls) == 1
    assert svc._cost(svc._registry()[0], {'input_tokens': 100, 'output_tokens': 10, 'cache_read_input_tokens': 10, 'cache_write_input_tokens': 0}) is None


@pytest.mark.parametrize('status,code', [(401, 'provider_authentication'), (429, 'provider_rate_limited'),
                                      (302, 'provider_redirect_rejected'), (500, 'provider_http_error')])
def test_provider_errors_are_sanitized_and_not_retried(monkeypatch, tmp_path, status, code):
    configure(monkeypatch, tmp_path)
    calls = transport(monkeypatch, lambda r: httpx.Response(status, text='Private provider error '+KEY,
                       headers={'location': 'https://attacker.example/?credential='+KEY}))
    result = svc.forecast('fixture', packet())
    assert result['error_code'] == code and len(calls) == 1
    assert KEY not in json.dumps(result) and 'attacker.example' not in json.dumps(result)
    assert result['cost_usd'] is None


def test_timeout_stays_failed_with_attempt_record(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    def timeout(r):
        raise httpx.ReadTimeout('SECRET-ERROR '+KEY)
    calls = transport(monkeypatch, timeout)
    result = svc.forecast('fixture', packet())
    assert result['error_code'] == 'provider_timeout' and len(calls) == 1
    assert 'SECRET-ERROR' not in json.dumps(result)


def test_oversized_response_is_stopped(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    transport(monkeypatch, lambda r: httpx.Response(200, content=b'x'*(svc.MAX_RESPONSE_BYTES+1)))
    result = svc.forecast('fixture', packet())
    assert result['error_code'] == 'provider_response_too_large' and result['probability'] is None


def test_duplicate_json_fields_are_rejected(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    response = envelope('openai')
    response['choices'][0]['message']['content'] = '{"status":"predicted","status":"abstain"}'
    transport(monkeypatch, lambda r: httpx.Response(200, json=response))
    assert svc.forecast('fixture', packet())['error_code'] == 'duplicate_json_key'


def test_truncated_response_and_credential_echo_are_not_published(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    response = envelope('openai')
    response['choices'][0]['finish_reason'] = 'length'
    transport(monkeypatch, lambda r: httpx.Response(200, json=response))
    result = svc.forecast('fixture', packet())
    assert result['error_code'] == 'provider_response_incomplete' and result['cost_usd'] is not None
    response['choices'][0]['finish_reason'] = 'stop'
    response['choices'][0]['message']['content'] = json.dumps(answer(reasoning=KEY))
    result = svc.forecast('fixture', packet())
    assert result['error_code'] == 'credential_echo_rejected' and KEY not in json.dumps(result)


def test_exact_gateway_model_ids_may_contain_dots(tmp_path,monkeypatch):
    config=tmp_path/'models.json'
    config.write_text(json.dumps({'models':[{'id':'glm-5.2','provider':'openai_compatible','model':'glm-5.2',
        'api_key_env':'ISOLATED_TEST_KEY','base_url':'https://gateway.example.org/v1'}]}))
    monkeypatch.setenv('EMERIS_ARENA_MODELS_FILE',str(config))
    monkeypatch.setenv('EMERIS_ARENA_ALLOWED_PROVIDER_HOSTS','gateway.example.org')
    assert svc.list_models()[0]['requested_model']=='glm-5.2'
