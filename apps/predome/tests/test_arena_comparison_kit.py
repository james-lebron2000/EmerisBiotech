"""Public research kits use actual producers over explicitly synthetic ledgers.

No network, model or real scientific review occurs. Tampering tests reseal member
hashes deliberately: the verifier must also reject semantic/report disagreement.
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
from io import BytesIO
import json
from pathlib import Path
import socket
import sqlite3
import stat
import struct
import subprocess
import sys
import zipfile

import pytest

from emeris_live import arena_comparison_kit as kit
from emeris_live import arena_public as public
from emeris_live.arena_store import result_target_binding
from arena_scientific_fixture import scientifically_accept
from test_arena_public_comparisons import CANARY, factory


def captured(sample):
    base = '/api/arena/public/targets/' + sample.target['id']
    record, comparisons = sample.client.get(base + '/record'), sample.client.get(base + '/comparisons')
    assert record.status_code == comparisons.status_code == 200
    return record.json(), comparisons.json()


@pytest.fixture
def inputs(factory):
    sample = factory(agents=True)
    record, comparisons = captured(sample)
    return sample, record, comparisons


def members(raw):
    with zipfile.ZipFile(BytesIO(raw)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def packed(files, *, duplicate=None, mode=None):
    out = BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, raw in files.items():
            info = zipfile.ZipInfo(name)
            info.create_system = 3
            info.external_attr = ((mode if mode is not None else stat.S_IFREG) | 0o644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, raw)
        if duplicate is not None:
            archive.writestr(duplicate, files[duplicate])
    return out.getvalue()


def reseal(files):
    files = dict(files)
    manifest = json.loads(files['manifest.json'])
    manifest['files'] = {name: {'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}
                         for name, raw in files.items() if name != 'manifest.json'}
    files['manifest.json'] = json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2).encode() + b'\n'
    return packed(files)


class Document(HTMLParser):
    def __init__(self, raw):
        super().__init__()
        self.tags, self.text, self.runs = [], [], {}
        self.current = None
        self.feed(raw.decode())

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.tags.append((tag, attrs))
        if tag == 'article' and 'run' in attrs.get('class', '').split():
            self.current = self.runs.setdefault(attrs['data-run-id'], [])

    def handle_endtag(self, tag):
        if tag == 'article':
            self.current = None

    def handle_data(self, data):
        self.text.append(data)
        if self.current is not None:
            self.current.append(data)


def test_actual_builder_verifier_preserve_sources_assignments_baseline_and_original_times(inputs, monkeypatch):
    sample, record, comparisons = inputs
    before, ledger = deepcopy((record, comparisons)), sample.ledger.events()
    stamp = datetime.now(timezone.utc).isoformat()

    def forbidden(*args, **kwargs):
        raise AssertionError('Pure comparison export attempted execution or I/O')

    with monkeypatch.context() as patch:
        for target, name in ((socket, 'create_connection'), (socket, 'getaddrinfo'),
                             (sqlite3, 'connect'), (subprocess, 'run'), (Path, 'write_bytes')):
            patch.setattr(target, name, forbidden)
        raw, manifest = kit.build_kit(record, comparisons, exported_at_utc=stamp)
        assert kit.verify_kit(raw) == manifest
        assert kit.verify_kit(raw, hashlib.sha256(raw).hexdigest()) == manifest
        assert kit.build_kit(record, comparisons, exported_at_utc=stamp)[0] == raw
    files = members(raw)
    assert set(files) == kit.MEMBERS
    assert json.loads(files['clinical-record.json']) == record
    assert json.loads(files['comparisons.json']) == comparisons
    assert json.loads(files['manifest.json']) == manifest
    assert set(manifest['files']) == kit.MEMBERS - {'manifest.json'}
    assert manifest['schema_version'] == 'predome-public-comparison-research-kit-1'
    assert manifest['record_version'] == 'arena-clinical-public-1.4'
    assert manifest['comparisons_version'] == 'predome-public-trial-comparisons-1'
    assert manifest['baseline_assigned_records'] == len(record['runs']) == 1
    assert manifest['patent_assigned_rows'] == 1 and manifest['agent_assigned_records'] == 2
    assert manifest['provider_calls'] == manifest['ledger_writes'] == 0
    for key in ('independent_authentication', 'independent_timestamp_attestation', 'clinical_prediction_validated'):
        assert manifest[key] is False
    expected = {run['id']: {key: run[key] for key in ('id', 'generated_at_utc', 'locked_at_utc')}
        for run in [*record['runs'], comparisons['patent_comparisons'][0]['with_patents'],
                    *(item['run'] for item in comparisons['agent_comparisons'])]}
    assert manifest['original_model_times'] == [expected[key] for key in sorted(expected)]
    for name, metadata in manifest['files'].items():
        assert metadata == {'sha256': hashlib.sha256(files[name]).hexdigest(), 'bytes': len(files[name])}
    combined = b'\n'.join(files.values()).decode()
    assert CANARY not in combined and 'synthetic_private_error' not in combined
    document = Document(files['comparison-report.html'])
    assert not any(tag in ('script', 'iframe', 'img', 'link', 'form', 'object') for tag, _ in document.tags)
    assert not any(any(key.startswith('on') for key in attrs) for _, attrs in document.tags)
    assert any(tag == 'meta' and "default-src 'none'" in attrs.get('content', '') for tag, attrs in document.tags)
    assert 'grid-template-columns:minmax(0,1fr)' in files['comparison-report.html'].decode()
    for filename in ('comparison-report.md', 'comparison-report.html'):
        text = files[filename].decode()
        for phrase in ('Clinical-only baseline', 'Added patent evidence', 'Agent workflow records',
                       'Current actual clinical result', 'Awaiting results', 'failed',
                       'No aggregate score', 'not independent trials', 'N/A', 'cannot be independently reverified'):
            assert phrase in text
    treated = comparisons['patent_comparisons'][0]['with_patents']
    assert 'Synthetic patent experiment' in ''.join(document.runs[treated['id']])
    assert 'Synthetic patent experiment' not in ''.join(document.runs[record['runs'][0]['id']])
    assert (record, comparisons) == before and sample.ledger.events() == ledger


@pytest.mark.parametrize('status', ['pending', 'failed', 'abstained'])
def test_pending_failed_and_explicit_abstention_survive_actual_producer_and_round_trip(factory, status):
    sample = factory(baseline_status=status, patent_status=status)
    record, comparisons = captured(sample)
    raw, _ = kit.build_kit(record, comparisons)
    loaded = json.loads(members(raw)['comparisons.json'])
    pair = loaded['patent_comparisons'][0]
    assert pair['control']['status'] == pair['with_patents']['status'] == status
    assert pair['control']['predicted_outcome'] == pair['with_patents']['predicted_outcome'] == 'Uncertain'
    assert pair['judgment_changed'] is (False if status == 'abstained' else None)
    assert kit.verify_kit(raw)['patent_assigned_rows'] == 1


def test_same_display_versions_do_not_upgrade_unknown_returned_identity(factory):
    record, comparisons = captured(factory(patent_returned=False))
    pair = comparisons['patent_comparisons'][0]
    assert pair['control']['model_version'] == pair['with_patents']['model_version']
    assert pair['model_version_matched'] is None
    raw, _ = kit.build_kit(record, comparisons)
    assert json.loads(members(raw)['comparisons.json'])['patent_comparisons'][0]['model_version_matched'] is None
    assert b'Comparison not established' in members(raw)['comparison-report.md']


@pytest.mark.parametrize('historical', [False, True])
def test_empty_comparisons_need_no_fabricated_assignments(inputs, historical):
    _, record, comparisons = inputs
    comparisons['patent_comparisons'] = comparisons['agent_comparisons'] = []
    if historical:
        record['target'].update(scope='archived_research', record_kind='historical_replay', cohort_id=None)
        for run in record['runs']:
            run.update(scope='archived_research', track='archived', cohort_id=None, match_status='historical_replay')
    raw, manifest = kit.build_kit(record, comparisons)
    assert manifest['patent_assigned_rows'] == manifest['agent_assigned_records'] == 0
    assert manifest['cohort_id'] == (None if historical else record['target']['cohort_id'])
    assert kit.verify_kit(raw) == manifest
    for phrase in (b'No patent-added assignments', b'No Agent workflow assignments'):
        assert phrase in members(raw)['comparison-report.md']


def test_actual_archived_public_projection_keeps_missing_requested_identity_and_unknown_lock(
        factory, monkeypatch):
    """Old archive-shaped adapter data runs through the actual public producer.

    The historical data is explicitly synthetic; run_view, detail, comparison
    empty handling and the kit are production implementations, not substitutes.
    """
    sample = factory()
    target = deepcopy(sample.target)
    target.update(id='archive-synthetic-original', cohort_id='synthetic-legacy-cohort',
                  scope='archived_research', public_source_ids=['e1'])
    source = deepcopy(sample.slots[0]['packet']['evidence'][0])
    source['archive_only_source'] = True
    # archived_views historically omits requested_model and has no locked time.
    old = {'id': 'archive-synthetic-A', 'target_id': target['id'],
        'cohort_id': target['cohort_id'], 'model_id': 'legacy-A', 'model_version': 'legacy-returned',
        'track': 'archived', 'condition': 'clinical_only', 'scope': 'archived_research',
        'status': 'predicted', 'probability': .71, 'reasoning': 'Synthetic preserved archive summary.',
        'generated_at_utc': '2020-01-02T10:00:00Z', 'locked_at_utc': None, 'evidence_ids': ['e1']}
    monkeypatch.setattr(public, 'read_frozen', lambda _: {'targets': [], 'runs': [], 'cohorts': []})
    monkeypatch.setattr(public, 'read_archived', lambda _: ([target], [old], {'e1': source}))
    base = '/api/arena/public/targets/' + target['id']
    record_response = sample.client.get(base + '/record')
    comparison_response = sample.client.get(base + '/comparisons')
    assert record_response.status_code == comparison_response.status_code == 200
    record, comparisons = record_response.json(), comparison_response.json()
    assert record['runs'][0]['requested_model'] is None
    assert record['runs'][0]['locked_at_utc'] is None
    assert record['runs'][0]['match'] is None
    raw, manifest = kit.build_kit(record, comparisons)
    assert kit.verify_kit(raw) == manifest
    assert json.loads(members(raw)['clinical-record.json']) == record
    assert '### legacy-A' in members(raw)['comparison-report.md'].decode()
    assert manifest['original_model_times'][0]['locked_at_utc'] is None


@pytest.mark.parametrize('fault', ['target', 'cohort', 'unknown_root', 'unknown_run', 'unknown_source',
    'probability', 'confidence', 'policy_probability', 'foreign_baseline', 'missing_baseline',
    'changed_baseline_reference', 'duplicate_agent', 'duplicate_treated', 'bad_source_url',
    'credential', 'location_bool', 'nonfinite', 'bad_component', 'extra_component',
    'false_change', 'unknown_version_changed', 'incomplete_false_registration',
    'not_comparable_without_reason', 'agent_contradictory_input', 'agent_contradictory_budget'])
def test_export_boundary_rejects_cross_scope_private_or_self_contradictory_public_payloads(inputs, fault):
    _, record, comparisons = inputs
    pair = comparisons['patent_comparisons'][0]
    if fault == 'target':
        comparisons['target_id'] = 'different-target'
    elif fault == 'cohort':
        pair['with_patents']['cohort_id'] = 'different-cohort'
    elif fault == 'unknown_root':
        comparisons['internal_analysis'] = CANARY
    elif fault == 'unknown_run':
        pair['with_patents']['prompt'] = CANARY
    elif fault == 'unknown_source':
        pair['added_sources'][0]['text'] = CANARY
    elif fault in ('probability', 'confidence'):
        pair['with_patents'][fault] = .71
    elif fault == 'policy_probability':
        comparisons['public_policy']['probability'] = .71
    elif fault == 'foreign_baseline':
        comparisons['agent_comparisons'][0]['baseline_run_ids'] = ['foreign-run']
    elif fault == 'missing_baseline':
        comparisons['agent_comparisons'][0]['baseline_run_ids'] = []
    elif fault == 'changed_baseline_reference':
        pair['control']['reason'] = 'Changed detached original.'
    elif fault == 'duplicate_agent':
        comparisons['agent_comparisons'].append(deepcopy(comparisons['agent_comparisons'][0]))
    elif fault == 'duplicate_treated':
        comparisons['patent_comparisons'].append(deepcopy(pair))
    elif fault == 'bad_source_url':
        pair['added_sources'][0]['source_url'] = 'https://example.org/?api_key=synthetic'
    elif fault == 'credential':
        comparisons['agent_comparisons'][0]['run']['model_version'] = 'sk-' + 'S' * 32
    elif fault == 'location_bool':
        pair['added_sources'][0]['page_references'] = [True]
    elif fault == 'nonfinite':
        pair['with_patents']['reason'] = float('nan')
    elif fault == 'bad_component':
        comparisons['agent_comparisons'][1]['component_models'][0]['role'] = 'internal_prompt'
    elif fault == 'extra_component':
        comparisons['agent_comparisons'][1]['component_models'][0]['private_trace'] = CANARY
    elif fault == 'false_change':
        pair['judgment_changed'] = False
    elif fault == 'unknown_version_changed':
        pair['model_version_matched'] = None
    elif fault == 'incomplete_false_registration':
        pair.update(comparison_status='incomplete_judgments', registration_matched=False,
                    judgment_changed=None, reasons=['incomplete_judgments'])
    elif fault == 'not_comparable_without_reason':
        pair.update(comparison_status='not_comparable', judgment_changed=None)
    elif fault == 'agent_contradictory_input':
        comparisons['agent_comparisons'][0]['reasons'] = ['clinical_input_changed']
    elif fault == 'agent_contradictory_budget':
        comparisons['agent_comparisons'][0]['common_budget_cap'] = False
    with pytest.raises(ValueError):
        kit.build_kit(record, comparisons)


def test_public_text_is_escaped_in_both_reports_and_keeps_each_reference_scope(inputs):
    _, record, comparisons = inputs
    hostile = '<script>alert("synthetic")</script> | [title](javascript:fake)'
    run = comparisons['patent_comparisons'][0]['with_patents']
    run['reason'] = run['brief_reason'] = hostile
    run['sources'][0]['title'] = hostile
    raw, _ = kit.build_kit(record, comparisons)
    files = members(raw)
    doc = Document(files['comparison-report.html'])
    assert not any(tag == 'script' for tag, _ in doc.tags)
    assert hostile in ''.join(doc.text)
    assert '&lt;script&gt;' in files['comparison-report.md'].decode()
    assert '\\|' in files['comparison-report.md'].decode()
    assert kit.verify_kit(raw)['schema_version'] == kit.SCHEMA_VERSION


@pytest.mark.parametrize('field', ['model_version', 'scope', 'brief_reason', 'reason',
    'match_status', 'status', 'track', 'condition'])
@pytest.mark.parametrize('value', [True, 7])
def test_public_run_text_fields_do_not_accept_boolean_or_numeric_metadata(inputs, field, value):
    _, record, comparisons = inputs
    pair = comparisons['patent_comparisons'][0]
    pair.update(model_version_matched=None, comparison_status='not_comparable',
                judgment_changed=None, reasons=['model_version_unconfirmed'])
    pair['with_patents'][field] = value
    with pytest.raises(ValueError):
        kit.build_kit(record, comparisons)


def test_unknown_nullable_text_stays_null_without_fabricated_identity_or_summary(inputs):
    _, record, comparisons = inputs
    pair = comparisons['patent_comparisons'][0]
    pair.update(model_version_matched=None, comparison_status='not_comparable',
                judgment_changed=None, reasons=['model_version_unconfirmed'])
    for field in ('model_version', 'scope', 'brief_reason', 'reason'):
        pair['with_patents'][field] = None
    raw, _ = kit.build_kit(record, comparisons)
    kit.verify_kit(raw)
    stored = json.loads(members(raw)['comparisons.json'])['patent_comparisons'][0]
    assert stored['model_version_matched'] is None
    assert all(stored['with_patents'][key] is None for key in ('model_version', 'scope', 'brief_reason', 'reason'))


@pytest.mark.parametrize('direction', ['Success', 'Failure'])
def test_two_directional_returned_patent_judgments_cannot_be_described_as_incomplete(inputs, direction):
    _, record, comparisons = inputs
    pair = comparisons['patent_comparisons'][0]
    pair.update(comparison_status='incomplete_judgments', judgment_changed=None,
                reasons=['incomplete_judgments'])
    pair['with_patents']['predicted_outcome'] = direction
    with pytest.raises(ValueError):
        kit.build_kit(record, comparisons)


@pytest.mark.parametrize('direction', ['Success', 'Failure'])
def test_directional_returned_agent_judgment_cannot_be_described_as_incomplete(inputs, direction):
    _, record, comparisons = inputs
    item = comparisons['agent_comparisons'][0]
    item['run']['predicted_outcome'] = direction
    item['reasons'] = ['incomplete_judgment']
    with pytest.raises(ValueError):
        kit.build_kit(record, comparisons)


@pytest.mark.parametrize('arm', ['patent', 'agent'])
def test_uncertain_does_not_establish_usability_or_force_completion(inputs, arm):
    _, record, comparisons = inputs
    if arm == 'patent':
        pair = comparisons['patent_comparisons'][0]
        pair.update(comparison_status='incomplete_judgments', judgment_changed=None,
                    reasons=['incomplete_judgments'])
        pair['with_patents']['predicted_outcome'] = 'Uncertain'
    else:
        item = comparisons['agent_comparisons'][0]
        item['run']['predicted_outcome'] = 'Uncertain'
        item['reasons'] = ['incomplete_judgment']
    raw, _ = kit.build_kit(record, comparisons)
    assert json.loads(members(raw)['comparisons.json']) == comparisons
    kit.verify_kit(raw)


def test_unscored_pending_assignment_keeps_anomalous_original_time_order_without_repair(factory):
    record, comparisons = captured(factory(baseline_status='pending', patent_status='pending'))
    treated = comparisons['patent_comparisons'][0]['with_patents']
    treated.update(generated_at_utc='2020-02-04T10:00:00Z', locked_at_utc='2020-02-03T10:00:00Z')
    assert treated['match'] is None and treated['status'] == 'pending'
    before = deepcopy((record, comparisons))
    raw, _ = kit.build_kit(record, comparisons)
    loaded = json.loads(members(raw)['comparisons.json'])['patent_comparisons'][0]['with_patents']
    assert loaded['generated_at_utc'] == '2020-02-04T10:00:00Z'
    assert loaded['locked_at_utc'] == '2020-02-03T10:00:00Z'
    assert loaded['match'] is None and loaded['predicted_outcome'] == 'Uncertain'
    assert (record, comparisons) == before
    kit.verify_kit(raw)


@pytest.mark.parametrize('fault', ['report_html', 'report_md', 'manifest_count', 'manifest_time',
    'manifest_extra', 'manifest_claim', 'record_consensus', 'comparison_probability',
    'duplicate_json_key', 'nonfinite_json', 'extra_member', 'missing_member', 'duplicate_member',
    'symlink', 'directory', 'traversal', 'oversize_member', 'invalid_zip', 'expected_sha'])
def test_verifier_rejects_resealed_tampering_and_unsafe_zip_or_json(inputs, monkeypatch, fault):
    _, record, comparisons = inputs
    raw, _ = kit.build_kit(record, comparisons)
    files = members(raw)
    if fault in ('report_html', 'report_md'):
        files['comparison-report.' + ('html' if fault == 'report_html' else 'md')] += b'\nSYNTHETIC FALSE IMPROVEMENT CLAIM\n'
        raw = reseal(files)
    elif fault.startswith('manifest_'):
        value = json.loads(files['manifest.json'])
        if fault == 'manifest_count': value['patent_assigned_rows'] += 1
        elif fault == 'manifest_time': value['original_model_times'][0]['locked_at_utc'] = None
        elif fault == 'manifest_extra': value['authority'] = 'synthetic unsupported identity'
        else: value['independent_timestamp_attestation'] = True
        files['manifest.json'] = json.dumps(value).encode()
        raw = packed(files)
    elif fault == 'record_consensus':
        value = json.loads(files['clinical-record.json'])
        value['consensus']['assigned'] = 0
        files['clinical-record.json'] = json.dumps(value).encode()
        raw = reseal(files)
    elif fault == 'comparison_probability':
        value = json.loads(files['comparisons.json'])
        value['patent_comparisons'][0]['with_patents']['probability'] = .71
        files['comparisons.json'] = json.dumps(value).encode()
        raw = reseal(files)
    elif fault == 'duplicate_json_key':
        files['comparisons.json'] = files['comparisons.json'].replace(b'{', b'{"target_id":"duplicate",', 1)
        raw = reseal(files)
    elif fault == 'nonfinite_json':
        files['comparisons.json'] = files['comparisons.json'].replace(b'"probability": null', b'"probability": NaN', 1)
        raw = reseal(files)
    elif fault == 'extra_member':
        files['source.pdf'] = b'Synthetic unmanifested source'
        raw = reseal(files)
    elif fault == 'missing_member':
        del files['comparisons.json']; raw = packed(files)
    elif fault == 'duplicate_member':
        with pytest.warns(UserWarning, match='Duplicate name'):
            raw = packed(files, duplicate='comparisons.json')
    elif fault == 'symlink':
        raw = packed(files, mode=stat.S_IFLNK)
    elif fault == 'directory':
        raw = packed(files, mode=stat.S_IFDIR)
    elif fault == 'traversal':
        files['../comparisons.json'] = files.pop('comparisons.json'); raw = packed(files)
    elif fault == 'oversize_member':
        monkeypatch.setattr(kit, 'MAX_MEMBER_BYTES', 10)
    elif fault == 'invalid_zip':
        raw = b'Synthetic not a ZIP'
    with pytest.raises(ValueError):
        kit.verify_kit(raw, '0' * 64 if fault == 'expected_sha' else None)


@pytest.mark.parametrize('fault', ['prefix', 'trailer', 'comment', 'central_offset', 'archive_limit', 'total_limit'])
def test_strict_zip_framing_and_resource_limits(inputs, monkeypatch, fault):
    _, record, comparisons = inputs
    raw, _ = kit.build_kit(record, comparisons)
    if fault == 'prefix':
        raw = b'Synthetic ignored prefix' + raw
    elif fault == 'trailer':
        raw += b'Synthetic ignored tail'
    elif fault == 'comment':
        buffer = BytesIO(raw)
        with zipfile.ZipFile(buffer, 'a') as archive:
            archive.comment = b'Synthetic ignored comment'
        raw = buffer.getvalue()
    elif fault == 'central_offset':
        raw = raw[:-6] + struct.pack('<I', 1) + raw[-2:]
    elif fault == 'archive_limit':
        monkeypatch.setattr(kit, 'MAX_ZIP_BYTES', 10)
    else:
        monkeypatch.setattr(kit, 'MAX_TOTAL_BYTES', 10)
    with pytest.raises(ValueError):
        kit.verify_kit(raw)


@pytest.fixture
def accepted_inputs(factory):
    sample = factory(agents=True)
    outcome = {'target_binding': result_target_binding(sample.target), 'label': 'success',
        'source_url': 'https://example.org/synthetic-actual-result', 'source_sha256': '6' * 64,
        'first_public_at': datetime.now(timezone.utc).isoformat(),
        'endpoint_match_note': 'Synthetic exact registered primary endpoint.'}
    for reviewer in ('synthetic-one', 'synthetic-two'):
        sample.ledger.record_result(sample.target['id'], outcome, reviewer_id=reviewer,
            reviewer_kind='ai', review_note='Synthetic source inspection, not an actual scientific review.')
    scientifically_accept(sample.ledger, sample.target)
    record, comparisons = captured(sample)
    assert comparisons['patent_comparisons'][0]['with_patents']['match'] is True
    return record, comparisons


@pytest.mark.parametrize('fault', ['early_lock', 'missing_generation', 'late_lock', 'mixed', 'unaccepted'])
def test_nonnull_matches_require_the_public_outcome_direction_and_visible_time_bounds(accepted_inputs, fault):
    record, comparisons = accepted_inputs
    raw, _ = kit.build_kit(record, comparisons)
    assert kit.verify_kit(raw)['clinical_prediction_validated'] is False
    treated = comparisons['patent_comparisons'][0]['with_patents']
    if fault == 'early_lock': treated['locked_at_utc'] = record['target']['information_cutoff_utc']
    elif fault == 'missing_generation': treated['generated_at_utc'] = None
    elif fault == 'late_lock': treated['locked_at_utc'] = record['result']['first_public_at']
    else:
        for result in (record['result'], record['target']['result']):
            if fault == 'mixed': result.update(label='mixed', scoring_eligible=False)
            else: result.update(review_status='agreement_pending_independent_review', scoring_eligible=False)
    with pytest.raises(ValueError):
        kit.build_kit(record, comparisons)


def test_result_date_only_uses_existing_conservative_bound_without_rewriting_model_times(accepted_inputs):
    record, comparisons = accepted_inputs
    # A compatibility DTO over actual producer fields, not a new adjudication or
    # backdated ledger record. Public JSON is unchanged by the kit.
    record['target']['information_cutoff_utc'] = '2020-01-01T00:00:00Z'
    record['target']['registered_at'] = '2020-01-02T00:00:00Z'
    for result in (record['result'], record['target']['result']):
        result['first_public_at'] = '2020-02-04'
    runs = [*record['runs']]
    for pair in comparisons['patent_comparisons']:
        runs.extend((pair['control'], pair['with_patents']))
    runs.extend(item['run'] for item in comparisons['agent_comparisons'])
    for run in runs:
        run['generated_at_utc'] = '2020-02-03T09:00:00Z'
        run['locked_at_utc'] = '2020-02-03T10:00:00Z'
    before = deepcopy((record, comparisons))
    raw, _ = kit.build_kit(record, comparisons)
    assert kit.verify_kit(raw)['clinical_prediction_validated'] is False
    assert json.loads(members(raw)['clinical-record.json'])['result']['first_public_at'] == '2020-02-04'
    assert (record, comparisons) == before
    comparisons['patent_comparisons'][0]['with_patents']['generated_at_utc'] = '2020-02-03'
    with pytest.raises(ValueError):
        kit.build_kit(record, comparisons)


def test_cli_actual_isolated_stdlib_success_and_fixed_failure_do_not_extract_or_echo(tmp_path, inputs):
    _, record, comparisons = inputs
    raw, _ = kit.build_kit(record, comparisons)
    source = tmp_path / 'research kit.zip'; source.write_bytes(raw)
    script = Path(__file__).resolve().parents[1] / 'scripts/verify_comparison_kit.py'
    def state():
        return {str(path.relative_to(tmp_path)): path.read_bytes() if path.is_file() else 'directory'
                for path in tmp_path.rglob('*')}
    before = state()
    command = [sys.executable, '-I', '-S', str(script), str(source)]
    good = subprocess.run([*command, '--expected-sha256', hashlib.sha256(raw).hexdigest()],
        capture_output=True, text=True, timeout=20, env={'PATH': '/usr/bin:/bin', 'LANG': 'C'})
    assert good.returncode == 0, good.stderr
    result = json.loads(good.stdout)
    assert result['status'] == 'public_comparison_kit_verified'
    assert result['expected_sha256_supplied'] is True and result['independent_authentication'] is False
    assert 'cannot be independently reverified' in result['validation_scope']
    for arguments in ([*command, '--expected-sha256', 'sk-' + 'S' * 32],
                      [*command, '--expected-sha256', '0' * 64]):
        bad = subprocess.run(arguments, capture_output=True, text=True, timeout=20,
            env={'PATH': '/usr/bin:/bin', 'LANG': 'C'})
        assert bad.returncode == 1 and bad.stdout == ''
        assert bad.stderr == 'Comparison kit verification failed; no files were extracted or changed.\n'
        assert str(source) not in bad.stderr and 'sk-' not in bad.stderr
    assert state() == before
