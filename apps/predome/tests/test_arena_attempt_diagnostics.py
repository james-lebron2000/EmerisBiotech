"""Actual internal diagnostic contract: typed metadata, never raw content."""
from copy import deepcopy
import json

import pytest

from emeris_live import arena_attempt_diagnostics as diagnostics


def test_initial_state_is_detached_and_keeps_unknowns():
    value = diagnostics.new_diagnostics(True)
    safe = diagnostics.sanitize_diagnostics(value)
    assert safe == value
    assert safe['body_read'] == {'state': 'unread', 'observed_bytes': None}
    assert safe['http_status'] is None and safe['json_error'] is None
    safe['visible_format']['empty'] = True
    assert value['visible_format']['empty'] is None
    assert diagnostics.new_diagnostics()['visible_format']['empty'] is None
    assert len(json.dumps(safe).encode()) <= 2048


@pytest.mark.parametrize('value', [None, {}, [], 'private response', ValueError('private response'),
    {'schema_version': diagnostics.VERSION}])
def test_missing_or_noncontract_objects_do_not_become_diagnostics(value):
    assert diagnostics.sanitize_diagnostics(value) is None


@pytest.mark.parametrize('field,value', [
    ('schema_version', 'arena-attempt-diagnostics-2'),
    ('failure_stage', 'secret exception string'),
    ('failure_stage', True),
    ('http_status', True), ('http_status', 200.0), ('http_status', 99), ('http_status', 600),
    ('http_status', float('nan')), ('http_status', float('inf')),
    ('content_type_class', 'application/json; token=CANARY'),
    ('json_mode_requested', 1), ('json_mode_requested', 'true'),
])
def test_unknown_enums_and_nonexact_numbers_are_rejected(field, value):
    raw = diagnostics.new_diagnostics()
    raw[field] = value
    assert diagnostics.sanitize_diagnostics(raw) is None


@pytest.mark.parametrize('body,accepted', [
    ({'state': 'unread', 'observed_bytes': None}, True),
    ({'state': 'unread', 'observed_bytes': 0}, False),
    ({'state': 'complete', 'observed_bytes': 0}, True),
    ({'state': 'complete', 'observed_bytes': diagnostics.MAX_RESPONSE_BYTES}, True),
    ({'state': 'partial', 'observed_bytes': 0}, True),
    ({'state': 'partial', 'observed_bytes': 17}, True),
    ({'state': 'partial', 'observed_bytes': None}, False),
    ({'state': 'complete', 'observed_bytes': True}, False),
    ({'state': 'complete', 'observed_bytes': -1}, False),
    ({'state': 'complete', 'observed_bytes': diagnostics.MAX_RESPONSE_BYTES + 1}, False),
    ({'state': 'limit_exceeded', 'observed_bytes': diagnostics.MAX_RESPONSE_BYTES + 1}, True),
    ({'state': 'limit_exceeded', 'observed_bytes': diagnostics.MAX_RESPONSE_BYTES + 2}, False),
    ({'state': 'limit_exceeded', 'observed_bytes': None}, False),
])
def test_read_state_distinguishes_unknown_exact_and_lower_bound(body, accepted):
    value = diagnostics.new_diagnostics()
    value['body_read'] = body
    assert (diagnostics.sanitize_diagnostics(value) is not None) is accepted


@pytest.mark.parametrize('stage', sorted(diagnostics.FAILURE_STAGES))
def test_each_fixed_stage_survives_without_exception_text(stage):
    value = diagnostics.new_diagnostics(False)
    value['failure_stage'] = stage
    assert diagnostics.sanitize_diagnostics(value)['failure_stage'] == stage


@pytest.mark.parametrize('kind', sorted(diagnostics.JSON_KINDS))
def test_known_parser_categories_preserve_unavailable_positions(kind):
    value = diagnostics.new_diagnostics()
    value['failure_stage'] = 'forecast_json'
    value['json_error'] = {'kind': kind, 'line': None, 'column': None, 'position': None}
    assert diagnostics.sanitize_diagnostics(value)['json_error'] == value['json_error']


def test_syntax_offsets_are_numbers_not_the_parser_document():
    value = diagnostics.new_diagnostics()
    value['failure_stage'] = 'provider_envelope_json'
    value['json_error'] = {'kind': 'syntax_error', 'line': 1, 'column': 1, 'position': 0}
    assert diagnostics.sanitize_diagnostics(value)['json_error']['position'] == 0
    for key, invalid in [('line', 0), ('column', -1), ('position', True),
                         ('position', diagnostics.MAX_RESPONSE_BYTES + 1)]:
        poisoned = deepcopy(value)
        poisoned['json_error'][key] = invalid
        assert diagnostics.sanitize_diagnostics(poisoned) is None
    value['json_error']['kind'] = 'duplicate_key'
    assert diagnostics.sanitize_diagnostics(value) is None


@pytest.mark.parametrize('nested', [None, 'body_read', 'json_error', 'visible_format'])
def test_unknown_nested_keys_cannot_smuggle_raw_content(nested):
    value = diagnostics.new_diagnostics()
    if nested == 'json_error':
        value[nested] = {'kind': 'syntax_error', 'line': 1, 'column': 2, 'position': 1}
    place = value if nested is None else value[nested]
    place['raw_response'] = 'SYNTHETIC_CREDENTIAL_REASONING_PROMPT_CANARY'
    assert diagnostics.sanitize_diagnostics(value) is None


@pytest.mark.parametrize('kind', sorted(diagnostics.TOP_LEVEL_TYPES))
def test_visible_top_level_types_are_fixed_labels(kind):
    value = diagnostics.new_diagnostics()
    value['visible_format'].update(empty=False, starts_with_object=kind == 'object',
        outer_code_fence=False, top_level_type=kind)
    assert diagnostics.sanitize_diagnostics(value)['visible_format']['top_level_type'] == kind
    value['visible_format']['empty'] = 0
    assert diagnostics.sanitize_diagnostics(value) is None


def test_serialized_limit_is_enforced_without_retaining_original(monkeypatch):
    value = diagnostics.new_diagnostics()
    monkeypatch.setattr(diagnostics, 'MAX_DIAGNOSTIC_BYTES', 8)
    assert diagnostics.sanitize_diagnostics(value) is None


@pytest.mark.parametrize('invalid', [1, 0, 'false', [], {}])
def test_builder_does_not_coerce_request_mode(invalid):
    with pytest.raises(ValueError, match='boolean or null'):
        diagnostics.new_diagnostics(invalid)
