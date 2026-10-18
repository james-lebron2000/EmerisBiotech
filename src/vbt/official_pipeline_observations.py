"""Offline, visually reviewed observations from two frozen issuer PDF charts.

This adapter performs no collection, model calls, database writes, phase ranking,
rights attribution or trial-success inference.  Missing/tampered source PDFs fail
closed.  The bundles are historical source observations, not live pipeline truth.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from .io import digest, within

_APPROVED = {
    'ascentage_annual_pipeline_2025': 'b0d5f778d0a7e7b8433484ab36a245a5fed32bf069fdc22947b60f446ab27eed',
    'carsgen_annual_pipeline_2024': '7fc9a9672d22c5dd4ecab513827e9e4ffb0453a5f87f69d5c0906da2999201e7',
}
# Explicit source-code trust anchors for already sealed history. Do not derive
# this set from the current intake pointer or learn approvals from a DB ledger.
# A new reviewed revision requires an intentional code review; replacing the
# current intake pointer must not silently revoke a known historical version.
_REVIEWED_SEALS = {
    'ascentage_annual_pipeline_2025': frozenset({
        'b0d5f778d0a7e7b8433484ab36a245a5fed32bf069fdc22947b60f446ab27eed'}),
    'carsgen_annual_pipeline_2024': frozenset({
        '7fc9a9672d22c5dd4ecab513827e9e4ffb0453a5f87f69d5c0906da2999201e7'}),
}
_CAPTURE_ROOT = Path('artifacts/market/china_expansion_v2/2026-10-17')


def bundle_digest(bundle):
    return hashlib.sha256(json.dumps(bundle, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def reviewed_seal(source_id, seal, *, current=False):
    """Trust only explicitly reviewed source/digest pairs, never DB assertions."""
    return (isinstance(seal, str) and bool(re.fullmatch(r'[a-f0-9]{64}', seal))
            and seal in _REVIEWED_SEALS.get(source_id, frozenset())
            and (not current or _APPROVED.get(source_id) == seal))


def _box(box, page_size):
    if (not isinstance(box, list) or len(box) != 4 or
            any(type(n) not in (int, float) for n in box) or
            not 0 <= box[0] < box[2] <= page_size[0] or
            not 0 <= box[1] < box[3] <= page_size[1]):
        raise ValueError('Invalid PDF-point evidence box')


def validate_contract(bundle):
    """Validate preserved originals, row-level provenance, dates and boundaries."""
    if bundle.get('schema_version') != 'official-pipeline-observation-bundle-v1':
        raise ValueError('Unsupported observation bundle schema')
    source, figure, review, scope = (bundle[k] for k in ('source', 'figure', 'review', 'scope'))
    url = urlsplit(source['url'])
    if url.scheme != 'https' or not url.hostname or url.username or url.password or url.port not in (None, 443):
        raise ValueError('Invalid official source URL')
    if not re.fullmatch(r'[a-f0-9]{64}', source['raw_sha256']):
        raise ValueError('Missing original PDF SHA-256')
    if source['published_at'] is not None or source['published_at_precision'] != 'unknown':
        raise ValueError('These bundles have no independently established publication date')
    if source['content_as_of_precision'] == 'day':
        from datetime import date
        if date.fromisoformat(source['content_as_of']).isoformat() != source['content_as_of']:
            raise ValueError('Invalid content date')
    elif source['content_as_of_precision'] != 'unresolved_report_date' or source['content_as_of'] is not None:
        raise ValueError('Unresolved report date must remain unknown')
    if scope.get('historical_disclosure') is not True or any(scope.get(k) is not False for k in
            ('current_pipeline_complete', 'drug_ownership_verified', 'highest_stage_inferred',
             'clinical_success_inferred', 'phase_codes_inferred', 'cross_indication_merging', 'publication_date_guessed')):
        raise ValueError('Observation scope cannot imply current completeness, rights or prediction')
    if review['human_sign_off'] is not False or review['scientific_review'] != 'pending':
        raise ValueError('Visual transcription is not a human scientific signature')
    rows = bundle['rows']
    if not isinstance(rows, list) or not 1 <= len(rows) <= 1000 or len({r['row_id'] for r in rows}) != len(rows):
        raise ValueError('Invalid or repeated observation rows')
    if figure['visible_row_count'] != len(rows) or review['rows_visually_reviewed'] != len(rows) or review['sample_only'] is not False:
        raise ValueError('These frozen bundles require full selected-chart row review')
    if (type(figure['pdf_page']) is not int or figure['pdf_page'] < 1
            or sorted(row['chart_row_ordinal'] for row in rows) != list(range(1, len(rows) + 1))):
        raise ValueError('Selected-chart row ordinals/pages must be complete and unique')
    size = figure['page_size_pdf_pt']
    if not isinstance(size, list) or len(size) != 2 or any(type(n) not in (int, float) or n <= 0 for n in size):
        raise ValueError('Missing PDF page size')
    _box(figure['chart_bbox_pdf_pt'], size)
    stages = {c['label_original']: c['header_bbox_pdf_pt'] for c in figure['stage_columns']}
    for box in stages.values():
        _box(box, size)
    for row in rows:
        if (not isinstance(row['drug_original'], str) or not row['drug_original'].strip() or
                not isinstance(row['indication_original'], str) or not row['indication_original'].strip()):
            raise ValueError('Drug and indication originals are required')
        if row['source_url'] != source['url'] or row['source_sha256'] != source['raw_sha256']:
            raise ValueError('Row is not bound to the original PDF')
        if row['clinical_phase_code'] is not None or row['human_sign_off'] is not False or row['drug_ownership_verified'] is not False or row['scientific_review'] != 'pending':
            raise ValueError('A chart row cannot manufacture a phase code or approval')
        locator = row['locator']
        if locator['pdf_page'] != figure['pdf_page'] or locator['printed_page'] != figure['printed_page'] or locator['chart_row_ordinal'] != row['chart_row_ordinal']:
            raise ValueError('Row page/ordinal provenance changed')
        for key in ('row_bbox_pdf_pt', 'drug_cell_bbox_pdf_pt', 'phase_header_bbox_pdf_pt'):
            _box(locator[key], size)
        if row['status'] == 'visually_supported':
            if row['phase_original'] not in stages or locator['phase_header_bbox_pdf_pt'] != stages[row['phase_original']] or row['blocked_reason'] is not None:
                raise ValueError('Phase must retain its visually checked original chart column')
        elif row['status'] != 'blocked' or not isinstance(row['blocked_reason'], str) or not row['blocked_reason'].strip():
            raise ValueError('Uncertain rows need an explicit blocked reason')


def inspect_bundle(path, workspace):
    """Read/verify one bundle and local raw PDF without side effects or fallback."""
    report = {'integrity_status': 'blocked', 'scientific_review': 'pending',
              'human_sign_off': False, 'rows': [], 'blocked_rows': [], 'errors': []}
    try:
        path = Path(path)
        if path.stat().st_size > 500_000:
            raise ValueError('Observation bundle exceeds bounded size')
        bundle = json.loads(path.read_bytes())
        validate_contract(bundle)
        if not reviewed_seal(bundle['source_id'], bundle_digest(bundle), current=True):
            raise ValueError('Observation transcription changed after visual review')
        source = bundle['source']
        relative = Path(source['raw_reference'])
        if relative.is_absolute() or '..' in relative.parts or not relative.is_relative_to(_CAPTURE_ROOT):
            raise ValueError('Original PDF path escapes the reviewed source folder')
        base = Path(workspace).resolve()
        raw = within(base / relative, base)
        within(raw, base / _CAPTURE_ROOT)
        if raw.stat().st_size != source['raw_bytes'] or raw.stat().st_size > 20_000_000 or digest(raw) != source['raw_sha256']:
            raise ValueError('Original PDF size/SHA-256 does not match the reviewed source')
        with raw.open('rb') as handle:
            if handle.read(5) != b'%PDF-':
                raise ValueError('Original capture is not a PDF')
        report.update(integrity_status='verified', source_id=bundle['source_id'], source=source,
                      rows=[r for r in bundle['rows'] if r['status'] == 'visually_supported'],
                      blocked_rows=[r for r in bundle['rows'] if r['status'] == 'blocked'], bundle=bundle)
    except (OSError, KeyError, TypeError, ValueError) as exc:
        report['errors'].append(str(exc) if not isinstance(exc, OSError) else 'Original bundle/PDF is unavailable; restore the reviewed relative source capture')
    return report


def disclosure_rows(path, workspace):
    """Return a disclosures-compatible projection, retaining all original data.

    This returns observations/metadata only and does not ingest or publish them.
    Blocked source rows stay in metadata and are never silently completed.
    """
    checked = inspect_bundle(path, workspace)
    if checked['integrity_status'] != 'verified':
        raise ValueError('; '.join(checked['errors']))
    return project_bundle(checked['bundle'])


def project_bundle(bundle):
    """Pure projection of a contract-valid bundle; not a source-integrity verdict.

    Callers publishing/serving rows must separately verify the frozen seal and PDF
    bytes. This also permits verification against the new sealed archive instead
    of requiring the original intake directory to remain mounted forever.
    """
    validate_contract(bundle)
    source = bundle['source']
    rows = []
    for row in bundle['rows']:
        if row['status'] != 'visually_supported':
            continue
        loc = row['locator']
        rows.append({'drug_code': row['drug_code_original'], 'drug_name': row['drug_original'],
                     'target': row['target_original'], 'modality': row['category_original'],
                     'indication': row['indication_original'], 'phase': row['phase_original'],
                     'source_locator': f"PDF page {loc['pdf_page']} (printed {loc['printed_page']}), chart row {loc['chart_row_ordinal']}; bbox {loc['row_bbox_pdf_pt']}",
                     'therapeutic_area': None, 'missing_fields': [key for key, value in
                         [('target', row['target_original']), ('modality', row['category_original'])] if value in (None, '', 'Undisclosed')],
                     'phase_semantics': row['phase_semantics'], 'clinical_phase_code': None,
                     'source_url': row['source_url'], 'source_sha256': row['source_sha256'],
                     'original_observation': row, 'drug_ownership_verified': False, 'scientific_review': 'pending',
                     'human_sign_off': False, 'historical_disclosure': True,
                     'content_as_of': source['content_as_of'], 'content_as_of_precision': source['content_as_of_precision']})
    return rows, {'published_at': None, 'as_of': source['content_as_of'],
                  'as_of_precision': source['content_as_of_precision'], 'date_locator': source['content_as_of_original'],
                  'reporting_period_original': source['reporting_period_original'], 'scope': bundle['figure']['scope'],
                  'blocked_rows': [r for r in bundle['rows'] if r['status'] == 'blocked'],
                  'visual_review': bundle['review'], 'historical_disclosure': True}
