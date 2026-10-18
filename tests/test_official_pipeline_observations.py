"""Offline provenance gates; synthetic bytes never stand in for real validation."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from vbt import official_pipeline_observations as observations


REPO = Path(__file__).resolve().parents[1]
EXAMPLES = [
    'ascentage_pipeline_2025_observations.json',
    'carsgen_pipeline_2024_observations.json',
]


def read_example(name=EXAMPLES[0]):
    return json.loads((REPO / 'examples' / name).read_text())


@pytest.fixture
def synthetic_bundle(tmp_path, monkeypatch):
    # The fake PDF is solely an integrity fixture, not a parsed/scientific source.
    bundle = read_example()
    bundle['source_id'] = 'explicitly_synthetic_integrity_fixture'
    raw = b'%PDF-1.4\nExplicit synthetic integrity test only.\n'
    reference = observations._CAPTURE_ROOT / 'synthetic-fixture.pdf'
    source = bundle['source']
    source.update(raw_reference=str(reference), raw_bytes=len(raw),
                  raw_sha256=hashlib.sha256(raw).hexdigest())
    for row in bundle['rows']:
        row['source_sha256'] = source['raw_sha256']
    target = tmp_path / reference
    target.parent.mkdir(parents=True)
    target.write_bytes(raw)
    path = tmp_path / 'explicitly-synthetic-observations.json'

    def save(seal=True):
        path.write_text(json.dumps(bundle))
        if seal:
            approved = observations.bundle_digest(bundle)
            monkeypatch.setitem(observations._APPROVED, bundle['source_id'],
                                approved)
            monkeypatch.setitem(observations._REVIEWED_SEALS, bundle['source_id'],
                                observations._REVIEWED_SEALS.get(bundle['source_id'], frozenset()) | {approved})
        return path

    save()
    return bundle, target, path, save


@pytest.mark.parametrize('name,count', list(zip(EXAMPLES, [10, 18])))
def test_real_transcription_contract_and_frozen_digest(name, count):
    bundle = read_example(name)
    observations.validate_contract(bundle)
    assert observations.bundle_digest(bundle) == observations._APPROVED[bundle['source_id']]
    assert observations.reviewed_seal(bundle['source_id'], observations.bundle_digest(bundle), current=True)
    assert len(bundle['rows']) == count
    assert not bundle['review']['sample_only']
    assert not bundle['review']['human_sign_off']
    assert all(row['clinical_phase_code'] is None for row in bundle['rows'])


def test_preserves_unresolved_date_and_original_nonclinical_combined_stages():
    bundle = read_example(EXAMPLES[1])
    assert bundle['source']['content_as_of'] is None
    assert bundle['source']['content_as_of_precision'] == 'unresolved_report_date'
    assert bundle['source']['published_at'] is None
    assert {'Pre-clinical', 'Phase II/III'} <= {r['phase_original'] for r in bundle['rows']}
    assert any(r['target_original'] == 'Undisclosed' for r in bundle['rows'])


def test_synthetic_integrity_projection_retains_originals_and_limits(synthetic_bundle, tmp_path):
    bundle, _, path, _ = synthetic_bundle
    checked = observations.inspect_bundle(path, tmp_path)
    assert checked['integrity_status'] == 'verified'
    rows, metadata = observations.disclosure_rows(path, tmp_path)
    assert len(rows) == 10
    assert metadata['historical_disclosure'] is True
    assert metadata['as_of'] == '2025-12-31'
    assert all(r['original_observation'] == original for r, original in zip(rows, bundle['rows']))
    assert all(r['clinical_phase_code'] is None and not r['drug_ownership_verified'] for r in rows)
    assert all(r['scientific_review'] == 'pending' for r in rows)


@pytest.mark.parametrize('mutation', ['missing', 'same_size_tamper', 'truncated'])
def test_missing_or_changed_raw_fails_closed(synthetic_bundle, tmp_path, mutation):
    _, raw, path, _ = synthetic_bundle
    if mutation == 'missing':
        raw.unlink()
    elif mutation == 'same_size_tamper':
        content = raw.read_bytes()
        raw.write_bytes(content[:-1] + b'x')
    else:
        raw.write_bytes(b'%PDF-')
    checked = observations.inspect_bundle(path, tmp_path)
    assert checked['integrity_status'] == 'blocked'
    assert checked['rows'] == []
    assert checked['errors']
    with pytest.raises(ValueError):
        observations.disclosure_rows(path, tmp_path)


def test_changed_transcription_rejected_even_when_contract_valid(synthetic_bundle, tmp_path):
    bundle, _, path, save = synthetic_bundle
    bundle['rows'][0]['indication_original'] = 'Unreviewed replacement'
    save(seal=False)
    checked = observations.inspect_bundle(path, tmp_path)
    assert checked['integrity_status'] == 'blocked'
    assert 'changed after visual review' in checked['errors'][0]
    assert checked['rows'] == []


@pytest.mark.parametrize('escape', ['traversal', 'absolute', 'symlink'])
def test_source_path_escape_rejected_after_synthetic_reseal(synthetic_bundle, tmp_path, escape):
    bundle, raw, path, save = synthetic_bundle
    outside = tmp_path / 'outside.pdf'
    outside.write_bytes(raw.read_bytes())
    if escape == 'traversal':
        bundle['source']['raw_reference'] = str(observations._CAPTURE_ROOT / '..' / 'outside.pdf')
    elif escape == 'absolute':
        bundle['source']['raw_reference'] = str(outside)
    else:
        raw.unlink()
        raw.symlink_to(outside)
    save()
    checked = observations.inspect_bundle(path, tmp_path)
    assert checked['integrity_status'] == 'blocked'
    assert checked['rows'] == []


def test_explicitly_blocked_rows_remain_visible_but_not_projected(synthetic_bundle, tmp_path):
    bundle, _, path, save = synthetic_bundle
    row = bundle['rows'][0]
    row.update(status='blocked', phase_original=None,
               blocked_reason='Synthetic ambiguous terminal bar; no inferred phase')
    save()
    checked = observations.inspect_bundle(path, tmp_path)
    assert checked['integrity_status'] == 'verified'
    assert len(checked['rows']) == 9
    assert checked['blocked_rows'] == [row]
    projected, metadata = observations.disclosure_rows(path, tmp_path)
    assert len(projected) == 9
    assert metadata['blocked_rows'] == [row]


@pytest.mark.parametrize('mutation', ['row_sha', 'bbox', 'inferred_phase', 'publication_guess'])
def test_contract_rejects_unbound_or_overstated_observations(mutation):
    bundle = copy.deepcopy(read_example())
    if mutation == 'row_sha':
        bundle['rows'][0]['source_sha256'] = '0' * 64
    elif mutation == 'bbox':
        bundle['rows'][0]['locator']['row_bbox_pdf_pt'] = [0, 0, 9999, 9999]
    elif mutation == 'inferred_phase':
        bundle['rows'][0]['clinical_phase_code'] = 'PHASE3'
    else:
        bundle['source']['published_at'] = '2026-01-01'
    with pytest.raises(ValueError):
        observations.validate_contract(bundle)
