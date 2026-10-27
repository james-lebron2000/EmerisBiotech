"""Read-only research history with independent execution and review filters."""
import html
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit

KINDS = {'run': 'Analysis run', 'cycle': 'Research cycle', 'portfolio': 'Research snapshot'}
STATES = {'succeeded': 'Execution succeeded', 'failed': 'Execution failed', 'blocked': 'Execution blocked',
          'interrupted': 'Task interrupted', 'completed': 'Cycle completed', 'snapshot': 'Frozen',
          'recorded': 'Recorded', 'unknown': 'Unknown'}
REVIEWS = {'pending': 'Scientific review pending', 'pass': 'Passed', 'passed': 'Passed', 'revise': 'Revision required',
           'insufficient_evidence': 'Insufficient evidence', 'unknown': 'Unknown'}
SCOPES = {'unmarked': 'Not marked as synthetic', 'synthetic': 'Synthetic engineering tests only'}
PAGE_SIZE = 20
FILTER_KEYS = ('q', 'kind', 'status', 'review', 'scope', 'offset')


def e(value):
    return html.escape(str(value if value is not None else 'Not recorded'), quote=True)


def _badge(value, labels):
    css = value if value in labels else 'unknown'
    return '<span class="badge ' + e(css) + '">' + e(labels.get(value, value)) + '</span>'


def is_synthetic(record):
    # Only a pre-existing explicit marker is used; absence proves no validation.
    return record['title'].lstrip().upper().startswith('SYNTHETIC ENGINEERING TEST:')


def safe_return(value):
    """Retain local history filters without accepting a redirect destination."""
    if not isinstance(value, str) or len(value) > 2500 or any(ord(c) < 32 for c in value):
        return '/records'
    try:
        parsed = urlsplit(value)
        pairs = parse_qsl(parsed.query, keep_blank_values=True, max_num_fields=len(FILTER_KEYS))
    except ValueError:
        return '/records'
    if (parsed.scheme or parsed.netloc or parsed.path != '/records' or parsed.fragment
            or len({key for key, _ in pairs}) != len(pairs)
            or any(key not in FILTER_KEYS for key, _ in pairs)):
        return '/records'
    return '/records' + ('?' + urlencode(pairs) if pairs else '')


def _saved_time(value):
    """Parse an explicit aware timestamp; an ID, mtime or naive date is no substitute."""
    if (not isinstance(value, str) or not 1 <= len(value) <= 100
            or not re.fullmatch(r'\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:\d{2})', value)):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
        local = parsed.astimezone(timezone(timedelta(hours=8)))
        return parsed, local.strftime('%Y-%m-%d %H:%M') + ' UTC+8'
    except (ValueError, OverflowError):
        return None


def _record_time(record):
    """Render fields present in Workbench.records().data without reading artifacts.

    Runs save started_at/finished_at in status.json; cycles save created_at.
    Portfolio evaluation.json currently has no timestamp, so those rows remain
    unknown unless an explicit created_at is supplied in their saved data. The
    display is a transcription of stored fields, not an integrity attestation.
    """
    data = record.get('data')
    if not isinstance(data, dict):
        data = {}
    fields = (('started_at', 'Started'), ('finished_at', 'Finished')) if record['kind'] == 'run' else (('created_at', 'Created'),)
    parsed = [(key, label, _saved_time(data.get(key))) for key, label in fields]
    if len(parsed) == 2 and all(row[2] for row in parsed) and parsed[1][2][0] < parsed[0][2][0]:
        return '<div class="record-time cell-meta">Record time unknown · Saved start/end timestamps conflict</div>'
    if not any(value for _, _, value in parsed):
        return '<div class="record-time cell-meta">Record time unknown · Timestamp missing or unverified</div>'
    lines = []
    for key, label, value in parsed:
        if value is None:
            lines.append(e(label) + ' unknown')
        else:
            lines.append(e(label) + ': <time datetime="' + e(data[key]) + '" title="'
                         + e('Saved field  ' + key + ': ' + data[key]) + '">' + e(value[1]) + '</time>')
    return '<div class="record-time cell-meta">' + '<br>'.join(lines) + '</div>'


def record_table(records, return_to=None):
    if not records:
        return ('<div class="empty"><h3>No matching research records</h3>'
                '<p>Adjust filters or explore a retrospective case with a known outcome.</p>'
                '<a href="/case?id=osmr-uc">Explore the research workflow →</a></div>')
    body = ('<p class="table-caption history-caption"><span>Each record retains its own version and original state · Times use saved fields</span>'
            '<span class="scroll-hint">Scroll horizontally on narrow screens, or focus the table and use arrow keys</span></p>'
            '<div class="scroll history-scroll" role="region" aria-label="Research history table" tabindex="0">'
            '<table class="history-table" role="table"><thead><tr><th scope="col">Research question and version</th>'
            '<th scope="col">Record type</th><th scope="col">Execution / record state</th>'
            '<th scope="col">Scientific review</th></tr></thead><tbody>')
    for r in records:
        params = {'kind': r['kind'], 'id': r['id']}
        if return_to:
            params['return'] = safe_return(return_to)
        url = '/record?' + urlencode(params)
        sample = '<span class="badge pending">Synthetic engineering test</span> ' if is_synthetic(r) else ''
        body += ('<tr><td data-label="Research question and version">' + sample + '<a class="record-title" href="' + e(url) + '">' + e(r['title']) + '</a>'
                 '<div class="ref record-id">' + e(r['id']) + '</div>' + _record_time(r) + '</td><td data-label="Record type">'
                 + e(KINDS.get(r['kind'], r['kind'])) + '</td><td data-label="Execution / record state">'
                 + _badge(r['status'], STATES) + '</td><td data-label="Scientific review">'
                 + _badge(r['review'], REVIEWS) + '</td></tr>')
    return body + '</tbody></table></div>'


def _filter_chips(filters):
    labels = {'q': 'Search', 'kind': 'Type', 'status': 'Execution state', 'review': 'Scientific review', 'scope': 'Record scope'}
    options = {'kind': KINDS, 'status': STATES, 'review': REVIEWS, 'scope': SCOPES}
    chips = []
    for key, value in filters.items():
        if not value:
            continue
        label = labels[key] + ': ' + options.get(key, {}).get(value, value)
        remaining = {name: item for name, item in filters.items() if name != key and item}
        target = '/records' + ('?' + urlencode(remaining) if remaining else '') + '#history-results'
        chips.append('<a class="asset-clear-chip" href="' + e(target) + '" aria-label="'
                     + e('Remove filter ' + label) + '">' + e(label) + '<span aria-hidden="true">×</span></a>')
    if not chips:
        return ''
    return '<nav class="history-active-filters" aria-label="Applied research-history filters">' + ''.join(chips) + '</nav>'


def _pagination(filters, offset, count, position):
    if not count:
        return ''
    body = '<nav class="asset-pagination history-pagination" aria-label="Research history pages · ' + position + '">'
    body += '<span class="cell-meta">Page ' + str(offset // PAGE_SIZE + 1) + ' / ' + str((count - 1) // PAGE_SIZE + 1) + ' · At most ' + str(PAGE_SIZE) + ' records</span><div class="page-actions">'
    for rel, target, text in [('prev', offset - PAGE_SIZE, '← Previous page'), ('next', offset + PAGE_SIZE, 'Next page →')]:
        if 0 <= target < count:
            body += '<a class="button secondary small" rel="' + rel + '" href="/records?' + e(urlencode({**filters, 'offset': target})) + '#history-results">' + text + '</a>'
    return body + '</div></nav>'


def render(records, q):
    query = q.get('q', '').strip()
    kind, state, review = (q.get(key, '') for key in ('kind', 'status', 'review'))
    scope = q.get('scope', '')
    if scope and scope not in SCOPES:
        raise ValueError('Unknown research-record scope')
    review = 'passed' if review == 'pass' else review
    selected = [r for r in records if
                (not kind or r['kind'] == kind) and
                (not state or r['status'] == state) and
                (not review or ('passed' if r['review'] == 'pass' else r['review']) == review) and
                (not scope or (is_synthetic(r) if scope == 'synthetic' else not is_synthetic(r))) and
                query.casefold() in (r['title'] + ' ' + r['id']).casefold()]
    try:
        offset = max(0, int(q.get('offset', 0)))
    except (ValueError, TypeError):
        offset = 0
    offset = min(offset // PAGE_SIZE * PAGE_SIZE,
                 max(0, (len(selected) - 1) // PAGE_SIZE * PAGE_SIZE))
    body = ('<header class="page-header"><div><span class="eyebrow">RESEARCH HISTORY</span>'
            '<h1>History</h1><p class="lead">Find research questions, inspect failures and continue from existing evidence.</p>'
            '</div><div class="page-actions"><a class="button secondary" href="/jobs">View integrity tasks</a>'
            '<a class="button" href="/notes">Record a research question</a></div></header>')
    synthetic_count = sum(is_synthetic(r) for r in records)
    history_scope = 'Includes ' + str(synthetic_count) + ' explicitly marked synthetic engineering tests' if synthetic_count else 'Includes successful, failed and blocked records'
    metrics = [('All research records', len(records), history_scope),
               ('Failed or blocked execution', sum(r['status'] in ('failed', 'blocked', 'interrupted') for r in records), 'Original reasons and logs retained'),
               ('Review or further evidence required', sum(r['review'] in ('pending', 'revise', 'insufficient_evidence', 'unknown') for r in records), 'Counted by the saved scientific-review state')]
    body += '<section class="metric-row" aria-label="Research history overview">' + ''.join(
        '<div class="metric-card"><span class="metric-label">' + label + '</span><strong class="metric">'
        + format(count, ',') + '</strong><small>' + detail + '</small></div>' for label, count, detail in metrics) + '</section>'
    body += ('<section class="card filter-toolbar history-filter" aria-labelledby="history-filter-title">'
             '<div class="section-heading"><h2 id="history-filter-title">Find previous research</h2>'
             '<span class="cell-meta">Filter execution and scientific review independently</span></div>'
             '<form method="get" action="/records"><div class="form-grid">'
             '<div class="field field-search"><label for="history-query">Research question or run ID</label>'
             '<input type="search" id="history-query" name="q" value="' + e(query) + '" placeholder="For example OSMR, UC or a run ID"></div>')
    for key, label, value, options in [('kind', 'Record type', kind, KINDS),
                                        ('status', 'Execution / record state', state, STATES),
                                        ('review', 'Scientific review', review, REVIEWS)]:
        body += '<div class="field"><label for="history-' + key + '">' + label + '</label><select id="history-' + key + '" name="' + key + '"><option value="">All</option>'
        actual_options = {k: v for k, v in options.items() if not (key == 'review' and k == 'pass')}
        for row in records:
            recorded = 'passed' if key == 'review' and row[key] == 'pass' else row[key]
            actual_options.setdefault(recorded, recorded)
        for option, text in actual_options.items():
            body += '<option value="' + e(option) + '"' + (' selected' if option == value else '') + '>' + e(text) + '</option>'
        body += '</select></div>'
    body += ('<div class="field"><label for="history-scope">Record scope</label>'
             '<select id="history-scope" name="scope"><option value="">All records</option>'
             + ''.join('<option value="' + key + '"' + (' selected' if scope == key else '')
                       + '>' + label + '</option>' for key, label in SCOPES.items())
             + '</select></div>')
    body += ('</div><div class="form-actions"><button type="submit">Filter records</button>'
             '<a class="text-action" href="/records">Clear filters</a></div></form></section>')
    if synthetic_count:
        body += '<p class="data-note">Counts include explicitly marked synthetic engineering tests. Their results are engineering checks, not real clinical research or forecast performance.</p>'
    body += '<p class="data-note">The unmarked filter uses the original title only; it does not establish clinical validation or forecasting accuracy.</p>'
    body += ('<section class="card" id="history-results" tabindex="-1" aria-labelledby="history-results-title"><div class="panel-title"><h2 id="history-results-title">Research records</h2><span class="muted">'
             + str(len(selected)) + ' / ' + str(len(records)) + ' records</span></div>')
    if selected:
        body += ('<p class="data-note">Showing records ' + str(offset + 1) + '–'
                 + str(min(offset + PAGE_SIZE, len(selected))) + ' matching records</p>')
    filters = {'q': query, 'kind': kind, 'status': state, 'review': review, 'scope': scope}
    body += _filter_chips(filters)
    body += _pagination(filters, offset, len(selected), 'Top')
    return_to = '/records?' + urlencode({**filters, 'offset': offset})
    body += record_table(selected[offset:offset + PAGE_SIZE], return_to)
    body += _pagination(filters, offset, len(selected), 'Bottom')
    body += ('</section>'
             '<p class="data-note">Historical versions remain unchanged. Execution, integrity, scientific review and human sign-off are recorded separately; '
             'unrecorded states remain unknown and cannot be inferred from execution.</p>')
    return body
