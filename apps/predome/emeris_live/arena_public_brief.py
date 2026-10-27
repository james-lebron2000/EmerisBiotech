"""Build an offline clinical brief from one sanitized public DTO, without I/O.

This is a new public export format. It does not change historical campaign ZIPs,
prediction records, scoring, or timestamp attestations.
"""
from datetime import datetime, timezone
import hashlib
import html
from io import BytesIO
import json
import math
import re
from urllib.parse import quote
import zipfile

from .arena_public_contract import credential_like, public_url, validate_current_readout, validate_consensus
from .arena_evidence_locations import validate_locations


SCHEMA_VERSION = 'predome-public-clinical-brief-1'
RECORD_VERSION = 'arena-clinical-public-1.4'
PREVIOUS_RECORD_VERSION = 'arena-clinical-public-1.3'
CALENDAR_RECORD_VERSION = 'arena-clinical-public-1.2'
PUBLIC_ORIGIN = 'https://8.148.145.28'
ROOT_FIELDS = {'version', 'target', 'runs', 'consensus', 'sources', 'evidence', 'result', 'public_policy'}
TARGET_FIELDS = {'id', 'cohort_id', 'trial_name', 'nct_id', 'drug', 'sponsor', 'indication',
    'endpoint', 'population', 'comparison', 'success_definition', 'analysis_timepoint', 'scope',
    'registered_at', 'execution_status', 'information_cutoff_utc', 'record_kind', 'verification_note',
    'status', 'registry_url', 'phase', 'design', 'registry_status', 'readout', 'current_readout', 'result', 'sources', 'consensus'}
RUN_FIELDS = {'id', 'target_id', 'cohort_id', 'model_id', 'requested_model', 'scope', 'track', 'condition',
    'generated_at_utc', 'locked_at_utc', 'model_version', 'status', 'predicted_outcome', 'probability',
    'confidence', 'calibration_status', 'brief_reason', 'reason', 'sources', 'match', 'match_status'}
LOCATION_FIELDS = {'page_references', 'source_anchor', 'document_version'}
LEGACY_SOURCE_FIELDS = {'id', 'title', 'source_url', 'source_snapshot_url', 'publication_date', 'kind', 'page'}
SOURCE_FIELDS = LEGACY_SOURCE_FIELDS | LOCATION_FIELDS
READOUT_FIELDS = {'label', 'start', 'end', 'precision', 'source_url', 'source_title', 'source_granularity', 'source_note'}
DESIGN_FIELDS = {'allocation', 'intervention_model', 'masking', 'primary_purpose', 'enrollment'}
RESULT_FIELDS = {'status', 'review_status', 'scoring_eligible', 'label', 'first_public_at', 'source_url',
    'summary', 'effect_size', 'confidence_interval', 'p_value', 'numeric_source_url', 'verification_note'}
CONSENSUS_FIELDS = {'predicted_outcome', 'assigned', 'counts', 'agreement', 'agreement_denominator',
    'average_probability', 'confidence', 'calibration_status'}
POLICY_FIELDS = {'calibration_status', 'probability', 'confidence', 'direction_rule', 'consensus_rule',
    'comparison_scope', 'clinical_prediction_validated'}
CLINICAL_FIELDS = {'measure', 'value', 'estimate', 'unit', 'lower', 'upper', 'level'}
MAX_RECORD_BYTES = 2 * 1024 * 1024


def canonical(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)+'\n').encode()


def _object(value, fields):
    if not isinstance(value, dict) or set(value) - fields:
        raise ValueError('Unexpected fields in public clinical brief input')
    return value


def _flat(value, structured=()):
    if any(isinstance(item, (dict, list)) for key, item in value.items() if key not in structured):
        raise ValueError('Unexpected nested public clinical value')


def _scalars(value):
    """Reject credentials/nonfinite values even when a public text field echoes them."""
    if isinstance(value, dict):
        for item in value.values():
            _scalars(item)
    elif isinstance(value, list):
        for item in value:
            _scalars(item)
    elif isinstance(value, str):
        if credential_like(value):
            raise ValueError('Credential-like content is not public brief material')
    elif isinstance(value, float) and not math.isfinite(value):
        raise ValueError('Nonfinite public clinical value')
    elif value is not None and type(value) not in (int, float, bool):
        raise ValueError('Unsupported public clinical value')


def _sources(value, *, allow_locations):
    if not isinstance(value, list):
        raise ValueError('A public source list is required')
    for source in value:
        _object(source, SOURCE_FIELDS if allow_locations else LEGACY_SOURCE_FIELDS)
        _flat(source, ('page_references',) if allow_locations else ())
        if allow_locations:
            validate_locations(source)
        if any(source.get(key) is not None and _safe_url(source[key]) is None
               for key in ('source_url', 'source_snapshot_url')):
            raise ValueError('A source link does not satisfy the public URL policy')


def validate_record(record, *, allow_legacy=False):
    """Validate the export boundary; forecast/score calculations remain upstream."""
    _object(record, ROOT_FIELDS)
    if (set(record) != ROOT_FIELDS or record['version'] != RECORD_VERSION
            and not (allow_legacy and record['version'] in (PREVIOUS_RECORD_VERSION, CALENDAR_RECORD_VERSION))):
        raise ValueError('Current sanitized public record required')
    target = _object(record['target'], TARGET_FIELDS)
    _flat(target, ('design', 'readout', 'current_readout', 'sources', 'result', 'consensus'))
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,180}', str(target.get('id', ''))):
        raise ValueError('An exact public target identifier is required')
    if not re.fullmatch(r'NCT\d{8}', str(target.get('nct_id', ''))):
        raise ValueError('An exact public NCT identifier is required')
    _flat(_object(target.get('readout'), READOUT_FIELDS))
    if 'current_readout' in target:
        validate_current_readout(target['current_readout'])
    for value in (target.get('registry_url'), target['readout'].get('source_url')):
        if value is not None and _safe_url(value) is None:
            raise ValueError('A clinical link does not satisfy the public URL policy')
    design = target.get('design')
    if design is not None:
        _object(design, DESIGN_FIELDS)
        _flat(design, ('enrollment',))
        if design.get('enrollment') is not None:
            _flat(_object(design['enrollment'], {'count', 'type'}))
    for source_list in (record['sources'], record['evidence'], target.get('sources')):
        _sources(source_list, allow_locations=record['version'] == RECORD_VERSION)
    if record['sources'] != record['evidence'] or record['sources'] != target['sources']:
        raise ValueError('Public sources changed within the selected record')
    consensus = _object(record['consensus'], CONSENSUS_FIELDS)
    policy = _object(record['public_policy'], POLICY_FIELDS)
    _flat(consensus, ('counts',))
    _flat(_object(consensus.get('counts'), {'Success', 'Failure', 'Uncertain'}))
    _flat(policy)
    if (consensus != target.get('consensus') or policy.get('clinical_prediction_validated') is not False
            or consensus.get('calibration_status') != 'not_established' or policy.get('calibration_status') != 'not_established'
            or any(obj.get(key) is not None for obj in (consensus, policy)
                   for key in ('probability', 'confidence', 'average_probability'))):
        raise ValueError('Unsupported public calibration or consensus claim')
    result = record['result']
    historical = target.get('record_kind') == 'historical_replay' or target.get('scope') in ('archived_research', 'retrospective')
    if result != target.get('result'):
        raise ValueError('Public result changed within the selected record')
    eligible = False
    if result is not None:
        _object(result, RESULT_FIELDS)
        _flat(result, ('effect_size', 'confidence_interval', 'p_value'))
        if any(result.get(key) is not None and _safe_url(result[key]) is None
               for key in ('source_url', 'numeric_source_url')):
            raise ValueError('A result link does not satisfy the public URL policy')
        for key in ('effect_size', 'confidence_interval', 'p_value'):
            value = result.get(key)
            if isinstance(value, dict):
                _flat(_object(value, CLINICAL_FIELDS))
            elif isinstance(value, list) and (len(value) > 3 or any(isinstance(v, (dict, list)) for v in value)):
                raise ValueError('Unexpected nested clinical result')
        eligible = (not historical and result.get('review_status') == 'accepted'
                    and result.get('status') == 'adjudicated' and result.get('label') in ('success', 'failure')
                    and result.get('scoring_eligible') is True)
        if result.get('scoring_eligible') is True and not eligible:
            raise ValueError('An unresolved or nonbinary result cannot receive binary scoring eligibility')
    runs = record['runs']
    if not isinstance(runs, list) or not runs or len(runs) > 128:
        raise ValueError('Actual assigned public records are required')
    identifiers = set()
    for run in runs:
        _object(run, RUN_FIELDS)
        _flat(run, ('sources',))
        if (not isinstance(run.get('id'), str) or run['id'] in identifiers
                or run.get('target_id') != target['id'] or run.get('cohort_id') != target.get('cohort_id')
                or run.get('condition') != 'clinical_only'
                or run.get('track') not in (('same_evidence', 'archived') if historical else ('same_evidence',))
                or run.get('probability') is not None or run.get('confidence') is not None
                or run.get('calibration_status') != 'not_established'
                or run.get('predicted_outcome') not in ('Success', 'Failure', 'Uncertain')):
            raise ValueError('Assignments must share the exact public clinical-only question')
        identifiers.add(run['id'])
        _sources(run.get('sources'), allow_locations=record['version'] == RECORD_VERSION)
        if run.get('status') != 'predicted' and run['predicted_outcome'] != 'Uncertain':
            raise ValueError('A missing or failed prediction cannot become a directional judgment')
        if run.get('match') is not None:
            if (not eligible or type(run['match']) is not bool or run.get('status') != 'predicted'
                    or run['predicted_outcome'] not in ('Success', 'Failure')
                    or run.get('match_status') != ('matched' if run['match'] else 'mismatched')
                    or run['match'] != (run['predicted_outcome'].lower() == result['label'])):
                raise ValueError('The public brief cannot assign an unsupported prediction match')
        elif run.get('match_status') not in ('not_evaluable', 'historical_replay'):
            raise ValueError('A missing public comparison cannot become a hit or miss')
    validate_consensus(consensus, runs, legacy=record['version'] == CALENDAR_RECORD_VERSION)
    _scalars(record)
    raw = canonical(record)
    if len(raw) > MAX_RECORD_BYTES:
        raise ValueError('Public clinical record exceeds the brief size limit')
    return raw


def _text(value):
    if value is None or value == '':
        return 'N/A'
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
    return str(value)


def _md(value):
    return re.sub(r'([\\`*_{}\[\]()#+!|])', r'\\\1', html.escape(_text(value).replace('\r', ' ').replace('\n', ' '), quote=False))


def _safe_url(value):
    if not isinstance(value, str) or any(ord(char) < 33 for char in value):
        return None
    checked = public_url(value)
    return PUBLIC_ORIGIN + checked if checked and checked.startswith('/') else checked


def _design(value):
    if not isinstance(value, dict):
        return _text(value)
    labels = {'RANDOMIZED':'Randomized', 'NON_RANDOMIZED':'Nonrandomized', 'PARALLEL':'Parallel group',
        'CROSSOVER':'Crossover', 'SINGLE_GROUP':'Single group', 'FACTORIAL':'Factorial', 'SEQUENTIAL':'Sequential',
        'NONE':'Open label', 'SINGLE':'Single masked', 'DOUBLE':'Double masked', 'TRIPLE':'Triple masked',
        'QUADRUPLE':'Quadruple masked', 'TREATMENT':'Treatment', 'PREVENTION':'Prevention'}
    parts = [labels.get(value[key], _text(value[key])) for key in
             ('allocation', 'intervention_model', 'masking', 'primary_purpose') if value.get(key)]
    enrollment = value.get('enrollment') or {}
    count = enrollment.get('count')
    if type(count) is int and count >= 0:
        kind = {'ACTUAL':'Actual enrollment', 'ESTIMATED':'Estimated enrollment'}.get(enrollment.get('type'), 'Recorded enrollment')
        parts.append(kind+': '+str(count))
    return ' · '.join(parts) or 'N/A'


def _scope(target, cohort_kind):
    if target.get('execution_status') == 'withdrawn_before_execution':
        return 'withdrawn_registration', 'Preserved withdrawn registration', 'Withdrawn before model execution; no prediction was generated for these assigned slots.'
    if target.get('execution_status') in ('withdrawn', 'superseded'):
        return 'preserved_inactive_record', 'Preserved inactive research record', 'Withdrawn or superseded original record; it is not the current formal season.'
    if target.get('record_kind') == 'historical_replay' or target.get('scope') in ('archived_research', 'retrospective'):
        return 'historical_replay', 'Historical replay / archived research record', 'Excluded from prospective performance claims.'
    if cohort_kind == 'registered_patent_pair_pilot':
        return 'independent_research_pilot', 'Independent research pilot', 'Separate from the formal season; this brief includes only the clinical-only control arm.'
    if cohort_kind == 'registered_12_project_season':
        return 'formal_season', 'Formal season clinical question', 'Same-evidence clinical-only assignments for this exact registered target.'
    return 'preserved_research_record', 'Preserved research record', 'Original target retained; no formal-season membership is inferred.'


def _result_text(result, historical):
    if result is None or result.get('status') == 'pending':
        return 'Awaiting results. No correctness score is available.'
    if historical:
        return 'Archived outcome: '+_text(result.get('label'))+'. Historical replay does not earn prospective hits.'
    if result.get('review_status') != 'accepted' or result.get('status') != 'adjudicated':
        return 'Reported result awaiting independent scientific review. No correctness score is available.'
    return 'Actual outcome: '+_text(result.get('label'))+'. '+('Binary comparison is eligible only for the qualified predictions shown in this brief.'
        if result.get('scoring_eligible') is True else 'Mixed or inconclusive results do not produce a right-or-wrong score.')


def _source_list(sources, empty):
    """Render recorded references in both output contexts, without fetching them."""
    markdown, items = [], []
    for source in sources:
        label = source.get('title') or source.get('id') or 'Public source'
        metadata = []
        if source.get('publication_date'):
            metadata.append('Published '+_text(source['publication_date']))
        if type(source.get('page')) is int and source['page'] > 0:
            metadata.append('Page '+str(source['page']))
        if source.get('page_references'):
            metadata.append('Recorded pages '+', '.join(str(page) for page in source['page_references']))
        if source.get('source_anchor'):
            metadata.append('Source anchor '+source['source_anchor'])
        if source.get('document_version'):
            metadata.append('Document version '+source['document_version'])
        suffix = ' · '+' · '.join(metadata) if metadata else ''
        links = [(name, _safe_url(source.get(key))) for name, key in
                 (('Original source', 'source_url'), ('Captured snapshot', 'source_snapshot_url'))]
        links = [(name, value) for name, value in links if value]
        md_links = ['['+name+']('+quote(value, safe=':/?#=&%+@~.-_')+')' for name, value in links]
        markdown.append('- '+_md(_text(label)+suffix)+': '+(' · '.join(md_links) if md_links else 'No public link recorded.'))
        html_links = ['<a href="'+html.escape(value, quote=True)+'" rel="noopener noreferrer">'+name+'</a>' for name, value in links]
        items.append('<li>'+html.escape(_text(label)+suffix)+': '+(' · '.join(html_links) if html_links else 'No public link recorded.')+'</li>')
    return markdown or ['- '+empty], ''.join(items) or '<li>'+html.escape(empty)+'</li>'


CSS = '''body{font:15px/1.55 Arial,Helvetica,sans-serif;color:#172c43;background:#fff;margin:0;padding:24px}main{max-width:1040px;margin:auto}h1{font-size:30px;line-height:1.2}h2{font-size:20px;margin-top:28px}h3{font-size:16px}a{color:#174d80}p,dd,td,li{overflow-wrap:anywhere}.scope,.note{color:#4b5f70}.facts{display:grid;grid-template-columns:170px minmax(0,1fr);gap:6px 18px}.facts dt{font-weight:bold}.facts dd{margin:0}.table-wrap{overflow-x:auto}table{width:100%;border-collapse:collapse;font-size:12px;min-width:800px}th,td{text-align:left;vertical-align:top;padding:8px;border-bottom:1px solid #d7dfe6}th{background:#eef3f7}.sources{padding-left:20px}.model-evidence{padding:12px 0;border-top:1px solid #d7dfe6;font-size:13px}.model-evidence h4{font-size:15px;margin:4px 0 8px}.model-evidence p{margin:6px 0}footer{border-top:1px solid #d7dfe6;margin-top:24px;padding-top:14px;font-size:12px;color:#4b5f70}@media(max-width:600px){body{padding:16px}.facts{grid-template-columns:1fr;gap:3px}.facts dd{margin-bottom:9px}h1{font-size:25px}}@media print{body{padding:0;font-size:11px}h1{font-size:23px}h2{font-size:16px}table{min-width:0;font-size:9px}.table-wrap{overflow:visible}th,td{padding:4px}a{color:inherit}tr{break-inside:avoid}footer{font-size:9px}}'''


def _window(value):
    if value.get('start') is None or value.get('end') is None:
        return 'Readout unconfirmed'
    return str(value['start'])+' – '+str(value['end'])+' ('+str(value.get('precision') or 'unknown')+')'


def build_brief(record, *, cohort_kind=None, exported_at_utc=None):
    """Return a four-file ZIP and its unsigned manifest; no files/network/DB writes."""
    record_raw = validate_record(record)
    target, runs, consensus, result = record['target'], record['runs'], record['consensus'], record['result']
    kind, scope, scope_note = _scope(target, cohort_kind)
    exported = exported_at_utc or datetime.now(timezone.utc).isoformat()
    parsed = datetime.fromisoformat(exported.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('An export time with an explicit timezone is required')
    page_url = PUBLIC_ORIGIN + '/arena?target=' + quote(target['id'], safe='')
    title = target.get('trial_name') or target['nct_id']
    facts = [('NCT', target['nct_id']), ('Drug', target.get('drug')), ('Indication', target.get('indication')),
        ('Sponsor', target.get('sponsor')), ('Phase', target.get('phase')), ('Study design', _design(target.get('design'))),
        ('Trial status', target.get('registry_status')), ('Primary endpoint', target.get('endpoint')),
        ('Population', target.get('population')), ('Comparison', target.get('comparison')),
        ('Analysis timepoint', target.get('analysis_timepoint')), ('Success definition', target.get('success_definition')),
        ('Information cutoff UTC', target.get('information_cutoff_utc')), ('Original registration UTC', target.get('registered_at')),
        ('Expected readout', target['readout'].get('label'))]
    update = target.get('current_readout')
    if update is not None:
        facts[-1] = ('Original frozen readout', target['readout'].get('label'))
        facts.extend([('Original frozen window', _window(target['readout'])),
            ('Current reviewed readout', update.get('label')), ('Current reviewed window', _window(update)),
            ('Calendar source publication', update['published_at']), ('Calendar source retrieved UTC', update['retrieved_at']),
            ('Calendar update approved UTC', update['approved_at']),
            ('Calendar update scope', 'Display calendar only. Original model input, information cutoff and stored generation/lock times are unchanged; this update was not model input. Server clock, not an independent timestamp proof.')])
    if target.get('execution_status'):
        facts.append(('Execution status', target['execution_status']))
    headers = ['Model / version', 'Outcome', 'Success probability', 'Confidence', 'Status',
               'Original generated UTC', 'Original locked UTC', 'Prediction vs reality']
    rows = []
    for run in runs:
        model = run.get('requested_model') or run.get('model_id')
        version = run.get('model_version')
        identity = _text(model) + (' / '+_text(version) if version and version != model else '')
        comparison = 'Matched' if run.get('match') is True else 'Mismatched' if run.get('match') is False else 'Not yet evaluable'
        if kind == 'historical_replay':
            comparison = 'Historical record; no prospective score'
        rows.append([identity, run['predicted_outcome'], 'N/A', 'N/A', run.get('status'),
                     run.get('generated_at_utc'), run.get('locked_at_utc'), comparison])
    outcome_note = _result_text(result, kind == 'historical_replay')
    counts = ' · '.join(label+' '+_text(consensus['counts'].get(label)) for label in ('Success', 'Failure', 'Uncertain'))
    calibration = 'Probability and confidence are not clinically calibrated. Every assigned record remains in direction counts and majority. Agreement uses returned predictions and explicit abstentions only, requires at least two judgments, and excludes technical failures and unexecuted slots. Model agreement is not accuracy or confidence.'
    agreement = ('N/A (fewer than two returned judgments)' if consensus['agreement'] is None
                 else format(consensus['agreement'] * 100, '.1f').rstrip('0').rstrip('.') + '%')
    agreement_note = 'Model agreement: '+agreement+'; returned judgments: '+str(consensus['agreement_denominator'])+'/'+str(consensus['assigned'])+'.'
    export_note = 'Export time describes this public brief only. Original model-generation and lock times above are unchanged stored timestamps; this export does not independently prove those earlier times, expand a TSA proof, or generate a new prediction.'
    integrity_note = 'File hashes establish integrity only. This ZIP contains no independent timestamp attestation. Clinical predictive performance is not established.'
    md = ['# '+_md(title), '', _md(scope)+'. '+_md(scope_note), '', '[Open this exact trial record]('+page_url+')',
          '', '## Clinical trial', '', '| Field | Recorded value |', '| --- | --- |']
    md.extend('| '+_md(label)+' | '+_md(value)+' |' for label, value in facts)
    md += ['', '## AI Clinical Trial Predictions', '', '| '+' | '.join(headers)+' |', '| '+' | '.join('---' for _ in headers)+' |']
    md.extend('| '+' | '.join(_md(value) for value in row)+' |' for row in rows)
    reference_note = 'These are references recorded for each model, not an independent claim-by-claim support review. A citation does not establish that a judgment is correct.'
    if any(any(source.get(key) for key in LOCATION_FIELDS) for source in record['sources']):
        reference_note += ' Locations are frozen evidence-card metadata; they do not establish that a model read an original document or that the recorded location was scientifically verified.'
    md += ['', calibration, '', '### Model rationales and recorded references', '', reference_note]
    html_models = []
    for run, row in zip(runs, rows):
        rationale = run.get('brief_reason') or run.get('reason') or 'No short rationale is available for this record.'
        reference_md, reference_html = _source_list(run['sources'], 'No references recorded for this model.')
        md += ['', '#### '+_md(row[0]), '', _md(rationale), '', 'Recorded references:', '', *reference_md]
        html_models.append('<article class="model-evidence" data-run-id="'+html.escape(run['id'], quote=True)+'"><h4>'
            +html.escape(row[0])+'</h4><p class="model-rationale">'+html.escape(_text(rationale))
            +'</p><p><strong>Recorded references</strong></p><ul class="sources">'+reference_html+'</ul></article>')
    md += ['', '## Consensus and actual result', '',
           'Majority prediction: '+_md(consensus.get('predicted_outcome'))+'.',
           'Assigned model records: '+str(consensus['assigned'])+'.',
           'Direction counts: '+_md(counts)+'.', agreement_note, '', _md(outcome_note)]
    if result:
        md += ['Official announcement: '+_md(result.get('first_public_at'))+'.', _md(result.get('verification_note'))]
    md += ['', '### Public sources', '']
    sources = [dict(s) for s in record['sources']]
    for guidance, name in [(target['readout'], 'Original frozen readout timing source'),
                           (update, 'Current reviewed calendar source')]:
        if (guidance and _safe_url(guidance.get('source_url'))
                and not any(s.get('source_url') == guidance['source_url'] for s in sources)):
            sources.append({'title': guidance.get('source_title') or name, 'source_url': guidance['source_url']})
    if result and _safe_url(result.get('source_url')):
        sources.append({'title': 'Official clinical result', 'source_url': result['source_url']})
    source_md, html_sources = _source_list(sources, 'No public source metadata recorded.')
    md.extend(source_md)
    md += ['', 'Exported at UTC: '+_md(exported)+'.', '', export_note, '', integrity_note]
    e = lambda value: html.escape(_text(value), quote=True)
    html_facts = ''.join('<dt>'+e(label)+'</dt><dd>'+e(value)+'</dd>' for label, value in facts)
    html_rows = ''.join('<tr>'+''.join('<td>'+e(value)+'</td>' for value in row)+'</tr>' for row in rows)
    html_result = '<p>'+e(outcome_note)+'</p>'
    if result:
        html_result += '<p>Official announcement: '+e(result.get('first_public_at'))+'</p><p>'+e(result.get('verification_note'))+'</p>'
    document = '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'"><title>'+e(title)+' · Predome Clinical Brief</title><style>'+CSS+'</style></head><body><main><h1>'+e(title)+'</h1><p class="scope"><strong>'+e(scope)+'</strong> · '+e(scope_note)+'</p><p><a href="'+html.escape(page_url, quote=True)+'" rel="noopener noreferrer">Open this exact trial record</a></p><section><h2>Clinical trial</h2><dl class="facts">'+html_facts+'</dl></section><section><h2>AI Clinical Trial Predictions</h2><div class="table-wrap"><table><thead><tr>'+''.join('<th scope="col">'+e(name)+'</th>' for name in headers)+'</tr></thead><tbody>'+html_rows+'</tbody></table></div><p class="note">'+e(calibration)+'</p><div class="model-notes"><h3>Model rationales and recorded references</h3><p class="note">'+e(reference_note)+'</p>'+''.join(html_models)+'</div></section><section><h2>Consensus and actual result</h2><p>Majority prediction: '+e(consensus.get('predicted_outcome'))+' · Assigned records: '+e(consensus['assigned'])+'</p><p>Direction counts: '+e(counts)+'</p><p>'+e(agreement_note)+'</p>'+html_result+'<h3>Public sources</h3><ul class="sources">'+html_sources+'</ul></section><footer><p>Exported at UTC: '+e(exported)+'</p><p>'+e(export_note)+'</p><p>'+e(integrity_note)+'</p></footer></main></body></html>\n'
    files = {'clinical-record.json': record_raw, 'clinical-brief.md': ('\n'.join(md)+'\n').encode(),
             'clinical-brief.html': document.encode()}
    manifest = {'schema_version': SCHEMA_VERSION, 'record_version': record['version'], 'exported_at_utc': exported,
        'export_time_scope': 'public_brief_export_only', 'target_id': target['id'], 'nct_id': target['nct_id'],
        'cohort_id': target.get('cohort_id'), 'record_kind': kind, 'assigned_records': len(runs), 'page_url': page_url,
        'comparison_scope': 'archived_historical_clinical_only' if kind == 'historical_replay' else 'same_evidence_clinical_only',
        'original_model_times': [{key: run.get(key) for key in ('id', 'generated_at_utc', 'locked_at_utc')} for run in runs],
        'original_model_time_note': export_note, 'provider_calls': 0, 'ledger_writes': 0,
        'independent_timestamp_attestation': False, 'clinical_prediction_validated': False,
        'files': {name: {'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)} for name, raw in sorted(files.items())}}
    files['manifest.json'] = canonical(manifest)
    out = BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as bundle:
        for name, raw in sorted(files.items()):
            bundle.writestr(name, raw)
    return out.getvalue(), manifest
