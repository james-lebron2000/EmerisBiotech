"""Pure public-output policies shared by the projection and offline brief.

These bounded checks do not authorize publication, verify sources or rewrite
stored model responses. They have no server, database or provider dependencies.
"""
import calendar
from datetime import date, datetime, timezone
import re
from urllib.parse import parse_qsl, urlsplit


JUDGMENT_STATUSES = frozenset(('predicted', 'abstain', 'abstained'))


def public_consensus(runs, *, legacy=False):
    """Keep assignment coverage separate from agreement among returned judgments.

    Legacy public records used all assignments for both statistics. This option
    exists only to verify their original values, never to rewrite frozen records.
    """
    labels = ('Success', 'Failure', 'Uncertain')
    assigned = len(runs)
    counts = {label: sum(run['predicted_outcome'] == label for run in runs) for label in labels}
    judgments = runs if legacy else [run for run in runs if run.get('status') in JUDGMENT_STATUSES]
    denominator = len(judgments)
    agreement_counts = [sum(run['predicted_outcome'] == label for run in judgments) for label in labels]
    agreement = max(agreement_counts) / denominator if denominator >= (1 if legacy else 2) else None
    return {'predicted_outcome': next((label for label, count in counts.items() if count > assigned / 2), 'Uncertain'),
        'assigned': assigned, 'counts': counts, 'agreement': agreement, 'agreement_denominator': denominator,
        'average_probability': None, 'confidence': None, 'calibration_status': 'not_established'}


def validate_consensus(value, runs, *, legacy=False):
    """Verify public derived statistics from their actual sanitized run records."""
    expected = public_consensus(runs, legacy=legacy)
    if (not isinstance(value, dict) or value != expected
            or type(value.get('assigned')) is not int or type(value.get('agreement_denominator')) is not int
            or not isinstance(value.get('counts'), dict)
            or any(type(count) is not int for count in value['counts'].values())
            or value.get('agreement') is not None and type(value['agreement']) not in (int, float)):
        raise ValueError('Public consensus does not match its assigned records and returned judgments')
    return value


def credential_like(value):
    """Recognize the existing key/private-key patterns without echoing them."""
    return isinstance(value, str) and re.search(
        r'(?<![A-Za-z])sk-[A-Za-z0-9_-]{24,}|-----BEGIN .*PRIVATE KEY-----', value
    ) is not None


def public_url(value):
    """Preserve the public projection's existing URL contract."""
    if not isinstance(value, str): return None
    if re.fullmatch(r'/api/arena/sources/[a-f0-9]{64}', value): return value
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
                or any(re.search(r'token|secret|password|api.?key|authorization', key, re.I)
                       for key, _ in parse_qsl(parsed.query))):
            return None
        parsed.port
        return value
    except ValueError:
        return None


CURRENT_READOUT_FIELDS = {
    'label', 'start', 'end', 'precision', 'source_url', 'source_title',
    'source_granularity', 'source_note', 'published_at', 'retrieved_at',
    'approved_at', 'proposal_id', 'proposal_sha256', 'decision_id',
    'decision_sha256', 'source_sha256', 'hash_scope',
    'original_prediction_unchanged', 'timestamp_attestation',
}


def validate_current_readout(value):
    """Validate an already-approved public display record, without granting approval.

    Approval identity, exact target binding and original-byte checks belong to
    the source/review boundary. Offline consumers share this publication contract.
    """
    if not isinstance(value, dict) or set(value) != CURRENT_READOUT_FIELDS:
        raise ValueError('Unexpected current readout fields')
    for key, item in value.items():
        if key == 'original_prediction_unchanged':
            if item is not True:
                raise ValueError('A calendar update cannot rewrite a forecast')
            continue
        if key in ('start', 'end') and item is None:
            continue
        if not isinstance(item, str) or not item.strip() or len(item) > 1000 or credential_like(item):
            raise ValueError('Invalid current readout value')
    if (value['source_granularity'] not in ('named_trial', 'exact_analysis')
            or value['hash_scope'] not in ('source_response_bytes', 'captured_utf8_source_body')
            or value['timestamp_attestation'] != 'server_clock_not_independent_timestamp'
            or public_url(value['source_url']) != value['source_url']
            or not value['source_url'].startswith('https://')):
        raise ValueError('Unsupported current readout provenance')
    for key in ('source_sha256', 'proposal_sha256', 'decision_sha256'):
        if not re.fullmatch(r'[a-f0-9]{64}', value[key]):
            raise ValueError('Invalid current readout digest')
    for key, prefix in (('proposal_id', 'ru_'), ('decision_id', 'rd_')):
        if not re.fullmatch(prefix + r'[a-f0-9]{24}', value[key]):
            raise ValueError('Invalid current readout review identifier')
    times = []
    for key in ('published_at', 'retrieved_at', 'approved_at'):
        text = value[key]
        if key == 'published_at' and re.fullmatch(r'\d{4}-\d{2}-\d{2}', text):
            parsed = datetime.combine(date.fromisoformat(text), datetime.min.time(), timezone.utc)
        else:
            if not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})', text):
                raise ValueError('Dated current readout provenance required')
            parsed = datetime.fromisoformat(text.replace('Z', '+00:00')).astimezone(timezone.utc)
        times.append(parsed)
    if times != sorted(times):
        raise ValueError('Current readout provenance is out of order')
    precision = value['precision']
    if precision == 'unknown':
        if value['start'] is not None or value['end'] is not None:
            raise ValueError('Unconfirmed readout must not contain invented dates')
        return value
    bounds = []
    for key in ('start', 'end'):
        text = value[key]
        if not isinstance(text, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', text):
            raise ValueError('Current readout dates must use YYYY-MM-DD')
        bounds.append(date.fromisoformat(text))
    start, end = bounds
    if start > end or precision not in ('day', 'month', 'quarter', 'half_year', 'year', 'range'):
        raise ValueError('Invalid current readout window')
    if precision == 'day':
        if start != end:
            raise ValueError('A daily readout requires one date')
        return value
    if start == end:
        raise ValueError('A readout window requires distinct boundaries')
    if precision == 'range':
        return value
    width = {'month': 1, 'quarter': 3, 'half_year': 6, 'year': 12}[precision]
    if start.day != 1 or (start.month - 1) % width:
        raise ValueError('Current readout start differs from its precision')
    month = start.month + width - 1
    if end != date(start.year, month, calendar.monthrange(start.year, month)[1]):
        raise ValueError('Current readout end differs from its precision')
    return value
