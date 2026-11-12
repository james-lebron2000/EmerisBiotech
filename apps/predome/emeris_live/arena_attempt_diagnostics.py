"""Internal, bounded diagnostics; never response text or an acceptance rule.

This standard-library contract has no provider, storage or command dependency.
Historical attempts without this optional field are not upgraded or inferred.
"""
import json


VERSION = 'arena-attempt-diagnostics-1'
MAX_RESPONSE_BYTES = 524288
MAX_DIAGNOSTIC_BYTES = 2048
FAILURE_STAGES = frozenset(('transport', 'http_response', 'provider_envelope_json',
    'provider_envelope_shape', 'visible_output_guard', 'forecast_json',
    'forecast_schema', 'evidence_review_json', 'evidence_review_schema', 'workflow_guard'))
CONTENT_TYPES = frozenset(('json', 'html', 'plain', 'other', 'unknown'))
BODY_STATES = frozenset(('unread', 'complete', 'partial', 'limit_exceeded'))
JSON_KINDS = frozenset(('syntax_error', 'unicode_error', 'type_error',
    'recursion_limit', 'duplicate_key', 'nonfinite_constant'))
TOP_LEVEL_TYPES = frozenset(('object', 'array', 'string', 'number', 'boolean', 'null'))
FIELDS = frozenset(('schema_version', 'failure_stage', 'http_status',
    'content_type_class', 'body_read', 'json_error', 'visible_format',
    'json_mode_requested'))


def new_diagnostics(json_mode_requested=None):
    """Create known initial state; callers fill only observed metadata."""
    if json_mode_requested is not None and type(json_mode_requested) is not bool:
        raise ValueError('Diagnostic JSON-mode flag must be boolean or null')
    return {'schema_version': VERSION, 'failure_stage': None, 'http_status': None,
        'content_type_class': None,
        'body_read': {'state': 'unread', 'observed_bytes': None},
        'json_error': None,
        'visible_format': {'empty': None, 'starts_with_object': None,
            'outer_code_fence': None, 'top_level_type': None},
        'json_mode_requested': json_mode_requested}


def _object(value, fields):
    return (type(value) is dict and len(value) == len(fields)
            and all(type(key) is str for key in value) and set(value) == fields)


def _enum(value, choices):
    return value is None or type(value) is str and len(value) <= 80 and value in choices


def _integer(value, minimum, maximum):
    return value is None or type(value) is int and minimum <= value <= maximum


def sanitize_diagnostics(value):
    """Return an independent complete typed object, or null for any bad field.

    Do not salvage arbitrary nested objects, exception messages, headers, input
    snippets or unknown keys. Invalid optional diagnostics cannot change the
    original attempt's terminal state, usage, cost or acceptance decision.
    """
    if not _object(value, FIELDS) or type(value['schema_version']) is not str or value['schema_version'] != VERSION:
        return None
    if (not _enum(value['failure_stage'], FAILURE_STAGES)
            or not _integer(value['http_status'], 100, 599)
            or not _enum(value['content_type_class'], CONTENT_TYPES)
            or value['json_mode_requested'] is not None and type(value['json_mode_requested']) is not bool):
        return None
    body = value['body_read']
    if (not _object(body, {'state', 'observed_bytes'})
            or type(body['state']) is not str or body['state'] not in BODY_STATES):
        return None
    count = body['observed_bytes']
    if body['state'] == 'unread':
        if count is not None:
            return None
    elif body['state'] == 'limit_exceeded':
        # A cap+1 sentinel means at least this size, never an exact full size.
        if type(count) is not int or count != MAX_RESPONSE_BYTES + 1:
            return None
    elif type(count) is not int or not 0 <= count <= MAX_RESPONSE_BYTES:
        return None
    error = value['json_error']
    if error is not None:
        if (not _object(error, {'kind', 'line', 'column', 'position'})
                or type(error['kind']) is not str or error['kind'] not in JSON_KINDS
                or not _integer(error['line'], 1, MAX_RESPONSE_BYTES + 1)
                or not _integer(error['column'], 1, MAX_RESPONSE_BYTES + 1)
                or not _integer(error['position'], 0, MAX_RESPONSE_BYTES)
                or error['kind'] != 'syntax_error' and any(error[k] is not None for k in ('line', 'column', 'position'))):
            return None
    visible = value['visible_format']
    if (not _object(visible, {'empty', 'starts_with_object', 'outer_code_fence', 'top_level_type'})
            or any(visible[k] is not None and type(visible[k]) is not bool
                   for k in ('empty', 'starts_with_object', 'outer_code_fence'))
            or not _enum(visible['top_level_type'], TOP_LEVEL_TYPES)):
        return None
    # All leaves are now fixed enums, bounded exact integers, booleans or null.
    # Encoding also creates a detached copy and enforces the serialized cap.
    raw = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)
    if len(raw.encode()) > MAX_DIAGNOSTIC_BYTES:
        return None
    return json.loads(raw)
