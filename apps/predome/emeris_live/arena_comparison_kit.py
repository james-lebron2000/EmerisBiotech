"""Offline comparison export over public DTOs only, without I/O or execution.

The verifier checks public-field consistency, member bytes and deterministically
rebuilt reports. It cannot authenticate an author, recover private registration
bindings or independently prove earlier prediction times or scientific review.
"""
from datetime import datetime, timezone
import hashlib
import html
from io import BytesIO
import json
import math
import re
import stat
import struct
from urllib.parse import quote
import zipfile
import zlib

from . import arena_public_brief as brief
from .arena_evidence_locations import validate_locations
from .arena_public_contract import credential_like, public_url


SCHEMA_VERSION = 'predome-public-comparison-research-kit-1'
COMPARISONS_VERSION = 'predome-public-trial-comparisons-1'
MEMBERS = frozenset(('clinical-record.json', 'comparisons.json',
    'comparison-report.html', 'comparison-report.md', 'manifest.json'))
MAX_ZIP_BYTES = 24 * 1024 * 1024
MAX_TOTAL_BYTES = 32 * 1024 * 1024
MAX_MEMBER_BYTES = 12 * 1024 * 1024
MAX_COMPARISON_BYTES = 4 * 1024 * 1024
MAX_ROWS = 256
HASH = re.compile(r'[a-f0-9]{64}')
IDENTIFIER = re.compile(r'[A-Za-z0-9_.-]{1,180}')
STATUSES = frozenset(('predicted', 'abstain', 'abstained', 'failed', 'pending',
    'unavailable', 'timed_out', 'withdrawn'))
JUDGMENTS = frozenset(('predicted', 'abstain', 'abstained'))
PAIR_STATUSES = frozenset(('comparable_judgments', 'incomplete_judgments', 'not_comparable'))
REASONS = {
    'missing_control': 'No clinical control is recorded.',
    'ambiguous_control': 'The clinical control is ambiguous.',
    'registration_metadata_unverified': 'Frozen conditions are not established.',
    'clinical_packet_changed': 'The registered clinical input differs or is unverified.',
    'no_added_patents': 'No patent addition is recorded.',
    'model_version_unconfirmed': 'Returned model identity is unconfirmed.',
    'model_version_changed': 'Returned model identities differ.',
    'actual_prompt_unverified': 'The recorded execution prompt binding is unverified.',
    'actual_config_unverified': 'The recorded execution configuration is unverified.',
    'actual_provider_input_unverified': 'The recorded provider input binding is unverified.',
    'blocked_input': 'Input evidence has a blocking issue.',
    'incomplete_judgments': 'The pair lacks two usable returned judgments.',
    'missing_baseline': 'No clinical-only baseline is recorded.',
    'clinical_input_changed': 'The registered clinical input differs.',
    'clinical_input_unverified': 'The registered clinical input is unverified.',
    'budget_cap_changed': 'The configured budget caps differ.',
    'budget_cap_unverified': 'The configured budget cap is unverified.',
    'incomplete_judgment': 'No usable returned judgment is recorded.',
}
VALIDATION_SCOPE = ('Public DTO consistency, file hashes and deterministic report reconstruction only. '
    'Registration/model-version flags and individual match metadata are recorded producer declarations. '
    'Private configuration, deadline, on-time, TSA and underlying model-weight bindings are not present '
    'and cannot be independently reverified by this kit.')
EXPORT_SCOPE = ('Export time describes this public kit only. Original generation and lock times are '
    'unchanged recorded values. This unsigned export does not independently prove past existence, '
    'source authenticity, scientific acceptance, predictive validity or Agent/patent benefit.')
RESEARCH_NOTE = ('Categorical judgments only; probability and confidence are N/A. Technical failures '
    'and unexecuted assignments are not drug failures. Shared configured caps do not establish equal '
    'actual computation. These records may reuse the same drug project and are not independent trials. '
    'No aggregate score, ranking, calibration or workflow/patent improvement is established.')


def _fail():
    raise ValueError('Invalid public comparison research kit')


def _object(value, fields, *, required=None):
    if not isinstance(value, dict) or set(value) - fields or not (fields if required is None else required) <= set(value):
        _fail()
    return value


def _canonical(value):
    try:
        return brief.canonical(value)
    except (TypeError, ValueError, RecursionError, OverflowError):
        _fail()


def _public_values(value, depth=0):
    if depth > 20:
        _fail()
    if isinstance(value, dict):
        if len(value) > 512 or any(not isinstance(key, str) for key in value):
            _fail()
        for item in value.values():
            _public_values(item, depth + 1)
    elif isinstance(value, list):
        if len(value) > 2048:
            _fail()
        for item in value:
            _public_values(item, depth + 1)
    elif isinstance(value, str):
        if len(value) > 10000 or credential_like(value):
            _fail()
    elif isinstance(value, float):
        if not math.isfinite(value):
            _fail()
    elif value is not None and type(value) not in (int, bool):
        _fail()


def _instant(value, *, date_only=False):
    if not isinstance(value, str) or not 1 <= len(value) <= 80:
        _fail()
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if date_only and re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            # Match the existing public gate's conservative calendar lower
            # bound; do not rewrite the stored date or invent model times.
            return parsed.replace(tzinfo=timezone.utc)
        if parsed.tzinfo is None:
            _fail()
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        _fail()


def _export_time(value):
    if value is None:
        value = datetime.now(timezone.utc).isoformat()
    return _instant(value).isoformat().replace('+00:00', 'Z')


def _source_list(value):
    if not isinstance(value, list) or len(value) > 128:
        _fail()
    for source in value:
        _object(source, brief.SOURCE_FIELDS, required=brief.LEGACY_SOURCE_FIELDS)
        validate_locations(source)
        for key in brief.LEGACY_SOURCE_FIELDS - {'page'}:
            if source[key] is not None and not isinstance(source[key], str):
                _fail()
        if source['page'] is not None and (type(source['page']) is not int or not 1 <= source['page'] <= 10000):
            _fail()
        for key in ('source_url', 'source_snapshot_url'):
            url = source[key]
            if url is not None and (not isinstance(url, str) or len(url) > 4000
                    or any(ord(char) < 33 for char in url) or public_url(url) is None):
                _fail()
        _public_values(source)


def _historical(record):
    target = record['target']
    return target.get('record_kind') == 'historical_replay' or target.get('scope') in ('archived_research', 'retrospective')


def _match(run, record):
    """Check public constraints, never reconstruct omitted private eligibility."""
    value = run['match']
    if value is None:
        if run['match_status'] not in ('not_evaluable', 'historical_replay'):
            _fail()
        return
    result, target = record['result'], record['target']
    if (type(value) is not bool or _historical(record) or not isinstance(result, dict)
            or result.get('review_status') != 'accepted' or result.get('status') != 'adjudicated'
            or target.get('status') != 'adjudicated' or result.get('scoring_eligible') is not True
            or result.get('label') not in ('success', 'failure') or run['status'] != 'predicted'
            or run['predicted_outcome'] not in ('Success', 'Failure')
            or value != (run['predicted_outcome'].lower() == result['label'])
            or run['match_status'] != ('matched' if value else 'mismatched')):
        _fail()
    cutoff = _instant(target.get('information_cutoff_utc'))
    generated, locked = _instant(run['generated_at_utc']), _instant(run['locked_at_utc'])
    published = _instant(result.get('first_public_at'), date_only=True)
    if not cutoff <= generated <= locked < published:
        _fail()


def _run(run, record):
    _object(run, brief.RUN_FIELDS)
    target = record['target']
    text_fields = brief.RUN_FIELDS - {'probability', 'confidence', 'sources', 'match'}
    if (any(run[key] is not None and not isinstance(run[key], str) for key in text_fields)
            or any(not isinstance(run[key], str) for key in ('status', 'track', 'condition', 'match_status'))):
        _fail()
    if (not isinstance(run['id'], str) or IDENTIFIER.fullmatch(run['id']) is None
            or run['target_id'] != target['id'] or run['cohort_id'] != target.get('cohort_id')
            or run['probability'] is not None or run['confidence'] is not None
            or run['calibration_status'] != 'not_established' or not isinstance(run['status'], str)
            or run['status'] not in STATUSES or not isinstance(run['predicted_outcome'], str)
            or run['predicted_outcome'] not in ('Success', 'Failure', 'Uncertain')
            or run['status'] != 'predicted' and run['predicted_outcome'] != 'Uncertain'):
        _fail()
    if any(isinstance(value, (dict, list)) for key, value in run.items() if key != 'sources'):
        _fail()
    for key in ('model_id', 'requested_model'):
        if (key == 'requested_model' and run[key] is None and _historical(record)
                and run['track'] == 'archived' and run['scope'] in ('archived_research', 'retrospective')):
            continue
        if not isinstance(run[key], str) or not run[key].strip() or len(run[key]) > 300:
            _fail()
    for key in ('generated_at_utc', 'locked_at_utc'):
        if run[key] is not None:
            _instant(run[key])
    _source_list(run['sources'])
    _public_values(run)
    _match(run, record)


def _reasons(value):
    if not isinstance(value, list) or len(value) > len(REASONS) or any(not isinstance(code, str) or code not in REASONS for code in value):
        _fail()
    if len(value) != len(set(value)):
        _fail()


def _bool_or_null(value):
    return value is None or type(value) is bool


def _all_runs(record, comparisons):
    result = {}
    values = list(record['runs'])
    for pair in comparisons['patent_comparisons']:
        values.extend(run for run in (pair['control'], pair['with_patents']) if run is not None)
    values.extend(item['run'] for item in comparisons['agent_comparisons'])
    for run in values:
        ident = run['id']
        if ident in result and result[ident] != run:
            _fail()
        result[ident] = run
    return [result[ident] for ident in sorted(result)]


def validate_comparisons(comparisons, record):
    """Return canonical public comparison JSON after closed export checks.

    Positive private-binding flags are retained as producer declarations, never
    derived from a matching display name or two missing public values.
    """
    brief.validate_record(record)
    _object(comparisons, {'schema_version', 'target_id', 'patent_comparisons', 'agent_comparisons', 'public_policy'})
    if comparisons['schema_version'] != COMPARISONS_VERSION or comparisons['target_id'] != record['target']['id']:
        _fail()
    raw = _canonical(comparisons)
    if len(raw) > MAX_COMPARISON_BYTES:
        _fail()
    _public_values(comparisons)
    policy = _object(comparisons['public_policy'], {'probability', 'confidence', 'clinical_prediction_validated', 'interpretation'})
    if (policy['probability'] is not None or policy['confidence'] is not None
            or policy['clinical_prediction_validated'] is not False
            or not isinstance(policy['interpretation'], str) or not policy['interpretation'].strip()):
        _fail()
    patents, agents = comparisons['patent_comparisons'], comparisons['agent_comparisons']
    if (not isinstance(patents, list) or not isinstance(agents, list)
            or len(patents) + len(agents) > MAX_ROWS or _historical(record) and (patents or agents)):
        _fail()
    baseline = {run['id']: run for run in record['runs']}
    for run in baseline.values():
        _run(run, record)
    treated_ids, agent_ids = set(), set()
    for pair in patents:
        _object(pair, {'control', 'with_patents', 'registration_matched', 'model_version_matched',
            'comparison_status', 'judgment_changed', 'reasons', 'added_sources'})
        control, treated = pair['control'], pair['with_patents']
        _run(treated, record)
        if (treated['track'] != 'patent_ablation' or treated['condition'] != 'with_patents'
                or treated['id'] in treated_ids):
            _fail()
        treated_ids.add(treated['id'])
        if control is not None:
            _run(control, record)
            if (control['track'] not in ('same_evidence', 'patent_ablation') or control['condition'] != 'clinical_only'
                    or any(control[key] != treated[key] for key in ('model_id', 'requested_model'))
                    or control['track'] == 'same_evidence' and baseline.get(control['id']) != control):
                _fail()
        _reasons(pair['reasons'])
        _source_list(pair['added_sources'])
        if any(source['kind'] != 'patent' for source in pair['added_sources']):
            _fail()
        if (type(pair['registration_matched']) is not bool or not _bool_or_null(pair['model_version_matched'])
                or not _bool_or_null(pair['judgment_changed']) or not isinstance(pair['comparison_status'], str)
                or pair['comparison_status'] not in PAIR_STATUSES):
            _fail()
        if pair['comparison_status'] == 'comparable_judgments':
            if (control is None or pair['registration_matched'] is not True
                    or pair['model_version_matched'] is not True or pair['reasons']
                    or not pair['added_sources'] or type(pair['judgment_changed']) is not bool
                    or any(run['status'] not in JUDGMENTS for run in (control, treated))
                    or not isinstance(control['model_version'], str) or not control['model_version'].strip()
                    or control['model_version'] != treated['model_version']
                    or pair['judgment_changed'] != (control['predicted_outcome'] != treated['predicted_outcome'])):
                _fail()
        elif pair['judgment_changed'] is not None:
            _fail()
        if pair['comparison_status'] != 'comparable_judgments' and not pair['reasons']:
            _fail()
        incomplete = pair['registration_matched'] and 'incomplete_judgments' in pair['reasons']
        if (pair['comparison_status'] == 'incomplete_judgments') != bool(incomplete):
            _fail()
        if (pair['comparison_status'] == 'incomplete_judgments' and control is not None
                and 'blocked_input' not in pair['reasons'] and all(run['status'] == 'predicted'
                    and run['predicted_outcome'] in ('Success', 'Failure') for run in (control, treated))):
            _fail()
        if pair['registration_matched'] and any(reason in pair['reasons'] for reason in
                ('registration_metadata_unverified', 'clinical_packet_changed', 'no_added_patents', 'missing_control', 'ambiguous_control')):
            _fail()
        if control is not None:
            if ((pair['model_version_matched'] is None) != ('model_version_unconfirmed' in pair['reasons'])
                    or (pair['model_version_matched'] is False) != ('model_version_changed' in pair['reasons'])):
                _fail()
        if control is None and (pair['registration_matched'] or pair['model_version_matched'] is not None
                or pair['comparison_status'] != 'not_comparable'
                or len(pair['reasons']) != 1 or pair['reasons'][0] not in ('missing_control', 'ambiguous_control')):
            _fail()
    for item in agents:
        _object(item, {'run', 'workflow', 'baseline_run_ids', 'clinical_input_matched',
            'common_budget_cap', 'component_models', 'reasons'})
        run = item['run']
        _run(run, record)
        if (item['workflow'] not in ('shared_agent', 'open_agent') or run['track'] != item['workflow']
                or run['condition'] != 'clinical_only' or run['id'] in agent_ids):
            _fail()
        agent_ids.add(run['id'])
        ids = item['baseline_run_ids']
        if (not isinstance(ids, list) or any(not isinstance(ident, str) for ident in ids)
                or len(ids) != len(set(ids)) or set(ids) != set(baseline)
                or not _bool_or_null(item['clinical_input_matched']) or not _bool_or_null(item['common_budget_cap'])):
            _fail()
        _reasons(item['reasons'])
        for key, changed, unknown in (
                ('clinical_input_matched', 'clinical_input_changed', 'clinical_input_unverified'),
                ('common_budget_cap', 'budget_cap_changed', 'budget_cap_unverified')):
            if ((item[key] is False) != (changed in item['reasons'])
                    or (item[key] is None) != (unknown in item['reasons'])):
                _fail()
        if 'missing_baseline' in item['reasons']:
            _fail()
        if (run['status'] == 'predicted' and run['predicted_outcome'] in ('Success', 'Failure')
                and 'incomplete_judgment' in item['reasons']):
            _fail()
        components = item['component_models']
        if not isinstance(components, list) or len(components) > 2:
            _fail()
        roles = set()
        for component in components:
            _object(component, {'role', 'requested_model'})
            if (component['role'] not in ('evidence_review', 'forecast') or component['role'] in roles
                    or not isinstance(component['requested_model'], str)
                    or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/-]{0,199}', component['requested_model']) is None):
                _fail()
            roles.add(component['role'])
    _all_runs(record, comparisons)
    return raw


def _value(value):
    if value is None or value == '':
        return 'N/A'
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False) if isinstance(value, (dict, list)) else str(value)


def _md(value):
    return re.sub(r'([\\`*_{}\[\]()#+!|])', r'\\\1', html.escape(_value(value).replace('\r', ' ').replace('\n', ' '), quote=False))


def _e(value):
    return html.escape(_value(value), quote=True)


def _url(value):
    checked = public_url(value)
    return brief.PUBLIC_ORIGIN + checked if checked and checked.startswith('/') else checked


def _references(sources):
    markdown, items = [], []
    for source in sources:
        details = [source.get('title') or source.get('id') or 'Public reference']
        for label, key in (('Source ID', 'id'), ('Published', 'publication_date'), ('Recorded page', 'page'),
                ('Recorded pages', 'page_references'), ('Source anchor', 'source_anchor'), ('Document version', 'document_version')):
            if source.get(key) is not None:
                details.append(label+': '+_value(source[key]))
        label = ' · '.join(details)
        links = [(name, _url(source.get(key))) for name, key in
                 (('Original source', 'source_url'), ('Captured snapshot', 'source_snapshot_url'))]
        links = [(name, value) for name, value in links if value]
        markdown.append('- '+_md(label)+': '+(' · '.join('['+name+']('+quote(value, safe=':/?#=&%+@~.-_')+')' for name, value in links) or 'No public link recorded.'))
        items.append('<li>'+_e(label)+': '+(' · '.join('<a href="'+_e(value)+'" rel="noopener noreferrer">'+name+'</a>' for name, value in links) or 'No public link recorded.')+'</li>')
    return markdown or ['- No references recorded.'], '<ul>'+(''.join(items) or '<li>No references recorded.</li>')+'</ul>'


def _run_report(run):
    if run is None:
        return ['No matching clinical control is recorded.'], '<p>No matching clinical control is recorded.</p>'
    facts = [('Model', run['requested_model'] or run['model_id']), ('Recorded model version', run['model_version']),
        ('Recorded status', run['status']), ('Judgment', run['predicted_outcome']),
        ('Probability / confidence', 'N/A / N/A'), ('Original generated UTC', run['generated_at_utc']),
        ('Original locked UTC', run['locked_at_utc']), ('Run ID', run['id']),
        ('Individual outcome comparison', 'Matched' if run['match'] is True else 'Mismatched' if run['match'] is False else 'Not evaluable')]
    reason = run['brief_reason'] or run['reason'] or 'No short rationale is recorded.'
    refs_md, refs_html = _references(run['sources'])
    md = [*['- '+label+': '+_md(value) for label, value in facts], '', _md(reason), '', 'Recorded references:', '', *refs_md]
    body = '<article class="run" data-run-id="'+_e(run['id'])+'"><dl>'+''.join('<dt>'+_e(label)+'</dt><dd>'+_e(value)+'</dd>' for label, value in facts)+'</dl><p>'+_e(reason)+'</p><h4>Recorded references</h4>'+refs_html+'</article>'
    return md, body


CSS = '''body{margin:0;padding:24px;font:15px/1.6 Arial,Helvetica,sans-serif;color:#19324b;background:#fff}main{max-width:1080px;margin:auto}h1{font-size:30px;line-height:1.2}h2{font-size:22px;margin:28px 0 12px}h3{font-size:17px}h4{font-size:14px;margin:12px 0 5px}a{color:#245783}p,li,dt,dd,h1,h2,h3{overflow-wrap:anywhere}section{margin-top:24px}.note{color:#536678;font-size:13px}.facts,.run dl{display:grid;grid-template-columns:180px minmax(0,1fr);gap:5px 14px}dt{font-weight:600}dd{margin:0;min-width:0}.run{border:1px solid #dbe2e9;border-radius:5px;padding:14px;min-width:0}.run dl{font-size:13px;grid-template-columns:135px minmax(0,1fr)}.run p,.run ul{font-size:13px}.run ul{padding-left:20px}.baseline,.agents,.pair{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.comparison{border-top:1px solid #dbe2e9;padding-top:12px;margin-top:16px;min-width:0}.pair>div,.agents>div{min-width:0}footer{margin-top:28px;border-top:1px solid #dbe2e9;padding-top:12px;font-size:12px;color:#536678}@media(max-width:700px){body{padding:16px}.baseline,.agents,.pair{grid-template-columns:minmax(0,1fr)}.facts,.run dl{grid-template-columns:minmax(0,1fr);gap:2px}.facts dd,.run dd{margin-bottom:7px}h1{font-size:25px}.run{padding:12px}}@media print{body{font-size:11px;padding:0}h1{font-size:24px}h2{font-size:18px}.run{break-inside:avoid}.note{font-size:10px}}'''


def _reports(record, comparisons, exported):
    target, consensus, result = record['target'], record['consensus'], record['result']
    title = target.get('trial_name') or target['nct_id']
    md = ['# '+_md(title)+' — Comparison research kit', '', RESEARCH_NOTE, '', '## Exact clinical question', '']
    facts = [(label, target.get(key)) for label, key in (
        ('NCT', 'nct_id'), ('Target ID', 'id'), ('Cohort ID', 'cohort_id'), ('Drug', 'drug'),
        ('Indication', 'indication'), ('Sponsor', 'sponsor'), ('Phase', 'phase'), ('Study design', 'design'),
        ('Registry status', 'registry_status'), ('Endpoint', 'endpoint'), ('Population', 'population'),
        ('Comparator', 'comparison'), ('Analysis timepoint', 'analysis_timepoint'), ('Success rule', 'success_definition'),
        ('Information cutoff UTC', 'information_cutoff_utc'), ('Original registration UTC', 'registered_at'),
        ('Execution status', 'execution_status'), ('Scope', 'scope'))]
    facts.append(('Original readout guidance', target['readout'].get('label')))
    if 'current_readout' in target:
        facts.append(('Current calendar guidance (not original model input)', target['current_readout']['label']))
    md.extend('- '+label+': '+_md(value) for label, value in facts)
    body = ['<h1>'+_e(title)+' · Comparison research kit</h1><p class="note">'+_e(RESEARCH_NOTE)+'</p>',
        '<section><h2>Exact clinical question</h2><dl class="facts">'+''.join('<dt>'+_e(label)+'</dt><dd>'+_e(value)+'</dd>' for label, value in facts)+'</dl></section>']
    scope_note = ('Historical replay / archived research; excluded from prospective hits.' if _historical(record)
        else 'Preserved withdrawn registration; no active competition.' if target.get('execution_status') in ('withdrawn_before_execution', 'withdrawn', 'superseded')
        else 'Original registered question; no formal-season membership is inferred.')
    count_text = '; '.join(name+' '+str(consensus['counts'][name]) for name in ('Success', 'Failure', 'Uncertain'))
    consensus_note = ('Baseline only: '+str(consensus['assigned'])+' assigned; '+str(consensus['agreement_denominator'])
        +' returned judgments. Majority: '+consensus['predicted_outcome']+'. Counts: '+count_text
        +'. Agreement: '+('N/A' if consensus['agreement'] is None else format(consensus['agreement'] * 100, '.1f')+'%')
        +'. Agreement is not accuracy. Extra patent/Agent arms add no votes.')
    md += ['', scope_note, '', '## Clinical-only baseline', '', consensus_note]
    base_html = []
    for run in record['runs']:
        lines, rendered = _run_report(run)
        md += ['', '### '+_md(run['requested_model'] or run['model_id']), '', *lines]
        base_html.append(rendered)
    body.append('<section><h2>Clinical-only baseline</h2><p>'+_e(scope_note)+'</p><p>'+_e(consensus_note)+'</p><div class="baseline">'+''.join(base_html)+'</div></section>')
    md += ['', '## Added patent evidence', '', 'All assigned patent-added rows are retained; incomplete pairs cannot show a judgment change.']
    patent_html = []
    for number, pair in enumerate(comparisons['patent_comparisons'], 1):
        verdict = ('Direction changed' if pair['judgment_changed'] else 'Direction unchanged') if pair['comparison_status'] == 'comparable_judgments' else 'Incomplete judgments' if pair['comparison_status'] == 'incomplete_judgments' else 'Comparison not established'
        flags = ('Recorded registration match: '+_value(pair['registration_matched'])+'; recorded model-version match: '
            +_value(pair['model_version_matched'])+'. These are producer declarations, not offline reauthentication.')
        md += ['', '### Patent row '+str(number)+' · '+verdict, '', flags]
        sides = []
        for label, run in (('Clinical evidence', pair['control']), ('Clinical + patent evidence', pair['with_patents'])):
            lines, rendered = _run_report(run)
            md += ['', '#### '+label, '', *lines]
            sides.append('<div><h4>'+label+'</h4>'+rendered+'</div>')
        reasons = ' '.join(REASONS[code] for code in pair['reasons'])
        references_md, references_html = _references(pair['added_sources'])
        md += ['', reasons or 'No blocking comparison reason is recorded.', '',
            'Added recorded sources (input metadata, not proof of actual citation or scientific review):', '', *references_md]
        patent_html.append('<article class="comparison"><h3>Patent row '+str(number)+' · '+verdict+'</h3><p class="note">'+_e(flags)+'</p><div class="pair">'+''.join(sides)+'</div><p>'+_e(reasons or 'No blocking comparison reason is recorded.')+'</p><h4>Added recorded sources</h4><p class="note">Input metadata, not proof of actual citation or scientific review.</p>'+references_html+'</article>')
    if not patent_html:
        md += ['', 'No patent-added assignments are recorded for this exact target.']
    body.append('<section><h2>Added patent evidence</h2>'+(''.join(patent_html) or '<p>No patent-added assignments are recorded for this exact target.</p>')+'</section>')
    md += ['', '## Agent workflow records', '', 'Whole-system observations; configured budget caps do not establish equal actual computation.']
    agent_html = []
    for item in comparisons['agent_comparisons']:
        label = 'Shared evidence-review workflow' if item['workflow'] == 'shared_agent' else 'Open Agent system'
        flags = ('Recorded clinical-input match: '+_value(item['clinical_input_matched'])+'; recorded common configured cap: '
            +_value(item['common_budget_cap'])+'; baseline links: '+', '.join(item['baseline_run_ids'])+'.')
        components = '; '.join('Declared '+component['role']+': '+component['requested_model'] for component in item['component_models']) or 'No component model names are confirmed.'
        lines, rendered = _run_report(item['run'])
        reasons = ' '.join(REASONS[code] for code in item['reasons'])
        md += ['', '### '+label, '', flags, '', _md(components), '', *lines, '', reasons]
        agent_html.append('<div><h3>'+label+'</h3><p class="note">'+_e(flags)+'</p><p>'+_e(components)+'</p>'+rendered+'<p>'+_e(reasons or 'No blocking comparison reason is recorded.')+'</p></div>')
    if not agent_html:
        md += ['', 'No Agent workflow assignments are recorded for this exact target.']
    body.append('<section><h2>Agent workflow records</h2><p class="note">Whole-system observations; configured caps do not prove equal actual computation.</p>'+('<div class="agents">'+''.join(agent_html)+'</div>' if agent_html else '<p>No Agent workflow assignments are recorded for this exact target.</p>')+'</section>')
    if result is None or result.get('status') == 'pending':
        outcome = 'Awaiting results in these saved records. No new official-result search was performed; pending is not drug failure.'
    elif _historical(record):
        outcome = 'Archived result: '+_value(result.get('label'))+'. Historical records do not earn prospective hits.'
    elif result.get('review_status') != 'accepted' or result.get('status') != 'adjudicated':
        outcome = 'Reported result awaiting independent scientific acceptance. No correctness score is available.'
    else:
        outcome = 'Actual outcome: '+_value(result.get('label'))+'. '+('Individual match metadata is producer-reported and remains subject to the original outcome/timing gates.' if result.get('scoring_eligible') is True else 'Mixed or inconclusive outcomes have no binary correctness score.')
    result_facts = [] if result is None else [(label, result.get(key)) for label, key in
        (('Review status', 'review_status'), ('Recorded first public release', 'first_public_at'),
         ('Source summary', 'summary'), ('Reported clinical effect', 'effect_size'),
         ('Reported confidence interval', 'confidence_interval'), ('Reported p value', 'p_value'),
         ('Verification note', 'verification_note'))]
    md += ['', '## Current actual clinical result', '', _md(outcome), '']
    md.extend('- '+label+': '+_md(value) for label, value in result_facts)
    result_sources = [] if result is None else [{'id': None, 'title': title, 'source_url': result.get(key), 'source_snapshot_url': None, 'publication_date': None, 'kind': 'clinical_result', 'page': None} for key, title in (('source_url', 'Official result source'), ('numeric_source_url', 'Reported numeric source')) if result.get(key)]
    result_md, result_html = _references(result_sources)
    md += ['', *result_md, '', '## Export and verification limits', '', 'Exported at UTC: '+_md(exported)+'.', '',
        VALIDATION_SCOPE, '', EXPORT_SCOPE, '', 'Recorded source locations do not prove that a model read an original or that the source was scientifically verified. Original PDFs, private inputs and timestamp proofs are not included.']
    body.append('<section><h2>Current actual clinical result</h2><p>'+_e(outcome)+'</p><dl class="facts">'+''.join('<dt>'+_e(label)+'</dt><dd>'+_e(value)+'</dd>' for label, value in result_facts)+'</dl>'+result_html+'</section>')
    body.append('<footer><p>Exported at UTC: '+_e(exported)+'</p><p>'+_e(VALIDATION_SCOPE)+'</p><p>'+_e(EXPORT_SCOPE)+'</p><p>Recorded locations do not prove model reading or scientific verification. Original PDFs, private inputs and timestamp proofs are not included.</p></footer>')
    document = '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'"><title>'+_e(title)+' · Comparison research kit</title><style>'+CSS+'</style></head><body><main>'+''.join(body)+'</main></body></html>\n'
    return ('\n'.join(md)+'\n').encode(), document.encode()


def _contents(record, comparisons, exported):
    record_raw = brief.validate_record(record)
    comparisons_raw = validate_comparisons(comparisons, record)
    markdown, document = _reports(record, comparisons, exported)
    files = {'clinical-record.json': record_raw, 'comparisons.json': comparisons_raw,
        'comparison-report.html': document, 'comparison-report.md': markdown}
    manifest = {'schema_version': SCHEMA_VERSION, 'record_version': record['version'],
        'comparisons_version': comparisons['schema_version'], 'target_id': record['target']['id'],
        'cohort_id': record['target'].get('cohort_id'), 'nct_id': record['target']['nct_id'],
        'exported_at_utc': exported, 'export_time_scope': 'public_comparison_export_only',
        'baseline_assigned_records': len(record['runs']),
        'patent_assigned_rows': len(comparisons['patent_comparisons']),
        'agent_assigned_records': len(comparisons['agent_comparisons']),
        'original_model_times': [{key: run[key] for key in ('id', 'generated_at_utc', 'locked_at_utc')}
                                 for run in _all_runs(record, comparisons)],
        'validation_scope': VALIDATION_SCOPE, 'original_model_time_note': EXPORT_SCOPE,
        'provider_calls': 0, 'ledger_writes': 0, 'independent_timestamp_attestation': False,
        'independent_authentication': False, 'clinical_prediction_validated': False,
        'files': {name: {'sha256': hashlib.sha256(value).hexdigest(), 'bytes': len(value)}
                  for name, value in sorted(files.items())}}
    files['manifest.json'] = _canonical(manifest)
    if any(len(value) > MAX_MEMBER_BYTES for value in files.values()) or sum(map(len, files.values())) > MAX_TOTAL_BYTES:
        _fail()
    return files, manifest


def build_kit(record, comparisons, *, exported_at_utc=None):
    """Return an unsigned five-file ZIP and manifest; no I/O, calls or writes."""
    exported = _export_time(exported_at_utc)
    files, manifest = _contents(record, comparisons, exported)
    output = BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, raw in sorted(files.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, raw)
    raw = output.getvalue()
    if len(raw) > MAX_ZIP_BYTES:
        _fail()
    return raw, manifest


def _decode(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                _fail()
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=unique, parse_constant=lambda _: _fail())
    except (ValueError, TypeError, UnicodeError, RecursionError):
        _fail()


def _members(raw):
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_ZIP_BYTES:
        _fail()
    # The bounded builder never needs multipart/ZIP64 or self-extracting ZIPs.
    # Require the ordinary comment-free end record at the actual end, so a
    # valid five-member archive cannot carry an ignored executable prefix/tail.
    if len(raw) < 22 or not raw.startswith(b'PK\x03\x04') or raw[-22:-18] != b'PK\x05\x06':
        _fail()
    _, disk, central_disk, disk_count, total_count, central_size, central_offset, comment_size = struct.unpack('<4s4H2IH', raw[-22:])
    if (disk or central_disk or disk_count != len(MEMBERS) or total_count != len(MEMBERS)
            or comment_size or central_offset + central_size != len(raw) - 22):
        _fail()
    try:
        with zipfile.ZipFile(BytesIO(raw)) as archive:
            entries = archive.infolist()
            if (len(entries) != len(MEMBERS) or {entry.filename for entry in entries} != MEMBERS
                    or archive.comment or min(entry.header_offset for entry in entries) != 0
                    or sum(entry.file_size for entry in entries) > MAX_TOTAL_BYTES):
                _fail()
            files = {}
            for entry in entries:
                if (entry.is_dir() or stat.S_IFMT(entry.external_attr >> 16) not in (0, stat.S_IFREG)
                        or entry.flag_bits & 1 or entry.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                        or entry.comment or entry.file_size > MAX_MEMBER_BYTES):
                    _fail()
                with archive.open(entry) as stream:
                    value = stream.read(MAX_MEMBER_BYTES + 1)
                if len(value) != entry.file_size or len(value) > MAX_MEMBER_BYTES:
                    _fail()
                files[entry.filename] = value
            if len(files['clinical-record.json']) > brief.MAX_RECORD_BYTES or len(files['comparisons.json']) > MAX_COMPARISON_BYTES or len(files['manifest.json']) > 512 * 1024:
                _fail()
            return files
    except (zipfile.BadZipFile, zlib.error, RuntimeError, EOFError, NotImplementedError, OSError):
        _fail()


def verify_kit(raw, expected_sha256=None):
    """Verify bytes and public reports, not identity, science or private gates.

    An optional externally supplied SHA pins these ZIP bytes only. Its authority
    depends on how the caller obtained it; this function supplies no trust root.
    """
    if expected_sha256 is not None and (not isinstance(expected_sha256, str)
            or HASH.fullmatch(expected_sha256) is None or not isinstance(raw, bytes)
            or hashlib.sha256(raw).hexdigest() != expected_sha256):
        _fail()
    files = _members(raw)
    manifest = _decode(files['manifest.json'])
    if not isinstance(manifest, dict) or manifest.get('schema_version') != SCHEMA_VERSION:
        _fail()
    exported = _export_time(manifest.get('exported_at_utc'))
    record, comparisons = _decode(files['clinical-record.json']), _decode(files['comparisons.json'])
    try:
        expected, rebuilt = _contents(record, comparisons, exported)
        if manifest != rebuilt or files != expected:
            _fail()
    except (TypeError, KeyError, AttributeError, OverflowError, RecursionError):
        _fail()
    return rebuilt
