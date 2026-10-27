"""Offline integrity checks and CSV for approved synthetic patient exports.

This verifies supported fixtures against the installed engine, not signatures,
clinical validity, identity, or authenticity of unsigned browser metadata.
"""
import argparse
import csv
import hashlib
import io
import json
import math
import uuid
from datetime import datetime
from pathlib import Path
from .patient_lab import canonical
from .molecule_lab import simulate_molecule
from .cohort_lab import cohort_result
from .regimen_cohort import regimen_cohort_result, regimen_csv

MAX_BYTES = 8 * 1024 * 1024


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('Duplicate JSON field: ' + key)
        value[key] = item
    return value


def _invalid_constant(value):
    raise ValueError('Non-finite JSON number: ' + value)


def strict_json(text):
    try:
        return json.loads(text, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
    except (json.JSONDecodeError, TypeError, RecursionError) as error:
        raise ValueError('Invalid JSON export') from error


def _equal(left, right):
    # JSON.stringify changes integral floats to integers. Preserve JSON number
    # semantics while preventing bool/number equivalence (False must not equal 0).
    if type(left) in (int, float) and type(right) in (int, float):
        return math.isfinite(left) and math.isfinite(right) and left == right
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_equal(left[k], right[k]) for k in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(_equal(a, b) for a, b in zip(left, right))
    return left == right


def _metadata(record):
    if not isinstance(record, dict):
        raise ValueError('Missing run metadata')
    run_id, created = record.get('run_id'), record.get('created_at')
    try:
        valid_id = isinstance(run_id, str) and str(uuid.UUID(run_id)) == run_id
        stamp = datetime.fromisoformat(created.replace('Z', '+00:00'))
        if not valid_id or stamp.tzinfo is None:
            raise ValueError('Invalid metadata')
    except (ValueError, AttributeError, TypeError) as error:
        raise ValueError('Run UUID and timezone-qualified creation time are required') from error


def verify_export(envelope):
    """Return the independently reproduced template; reject unsupported/tampered data."""
    if not isinstance(envelope, dict):
        raise ValueError('Export must be a JSON object')
    text = envelope.get('canonical_template')
    if not isinstance(text, str) or len(text.encode('utf-8')) > MAX_BYTES:
        raise ValueError('Missing or oversized canonical template')
    checksum = hashlib.sha256(text.encode('utf-8')).hexdigest()
    if envelope.get('template_sha256') != checksum:
        raise ValueError('Canonical template checksum mismatch')
    payload = strict_json(text)
    if not isinstance(payload, dict) or not _equal(payload, envelope.get('result')):
        raise ValueError('Duplicate exported result does not match the canonical template')
    schema = payload.get('schema_version')
    if schema == 'patient-molecule-v1':
        record = envelope.get('record')
        _metadata(record)
        if record.get('template_sha256') != checksum:
            raise ValueError('Saved record checksum mismatch')
        if type(record.get('cutoff')) is not str or record['cutoff'] not in ('3', '5', '7'):
            raise ValueError('Invalid saved cutoff')
        if not isinstance(record.get('patient'), str) or not isinstance(record.get('molecule'), str):
            raise ValueError('Saved patient and molecule identifiers must be strings')
        expected = simulate_molecule(record['patient'], record['molecule'], int(record['cutoff']))
        if expected['response']['status'] != 'estimable':
            raise ValueError('Saved simulation has no estimable patient-molecule pair')
    elif schema == 'patient-cohort-v1':
        _metadata(envelope)
        cutoff = payload.get('cutoff_day')
        expected = cohort_result(cutoff)
        view = envelope.get('view')
        if not isinstance(view, dict) or set(view) != {'assay_day', 'population', 'highlight_patient'}:
            raise ValueError('Invalid cohort view metadata')
        day = view['assay_day']
        if type(day) not in (int, float) or not 0 <= day <= cutoff + 3 or view['population'] not in ('paired', 'available'):
            raise ValueError('Unsupported cohort day or summary population')
        ids = {r['patient']['patient_id'] for r in expected['patients']}
        if view['highlight_patient'] is not None and view['highlight_patient'] not in ids:
            raise ValueError('Unknown highlighted cohort patient')
    elif schema == 'regimen-cohort-v1':
        _metadata(envelope)
        expected = regimen_cohort_result()
        view = envelope.get('view')
        if not isinstance(view, dict) or set(view) != {'week', 'highlight_patient', 'order'}:
            raise ValueError('Invalid regimen cohort view metadata')
        week = view['week']
        if type(week) not in (int, float) or not 0 <= week <= expected['horizon_weeks']:
            raise ValueError('Unsupported regimen cohort week')
        if view['order'] not in ('id', 'difference', 'burden'):
            raise ValueError('Unsupported regimen cohort order')
        ids = {r['patient']['patient_id'] for r in expected['patients']}
        if view['highlight_patient'] is not None and view['highlight_patient'] not in ids:
            raise ValueError('Unknown regimen cohort patient')
    else:
        raise ValueError('Unsupported patient export schema')
    if text != canonical(expected):
        raise ValueError('Template is not an approved synthetic fixture for the installed model version')
    return expected


def cohort_csv(payload):
    """All patients, both molecules, every assay day; unavailable values stay blank."""
    if payload != cohort_result(payload.get('cutoff_day')):
        raise ValueError('CSV requires a reproduced authoritative cohort template')
    columns = ['cohort_version', 'model_version', 'input_sha256', 'patient_id', 'sample_id',
               'molecule_id', 'cutoff_day', 'assay_day', 'viability_percent', 'status',
               'trajectory_kind', 'paired_summary_included', 'source_kind']
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=columns, lineterminator='\n')
    writer.writeheader()
    paired = set(payload['paired_patient_ids'])
    for row in payload['patients']:
        patient = row['patient']
        for molecule, response in row['responses'].items():
            for day in range(payload['cutoff_day'] + 4):
                point = response['trajectory'][day] if response['status'] == 'estimable' else None
                writer.writerow({'cohort_version': payload['cohort_version'], 'model_version': payload['model_version'],
                    'input_sha256': payload['input_sha256'], 'patient_id': patient['patient_id'],
                    'sample_id': patient['sample_id'], 'molecule_id': molecule, 'cutoff_day': payload['cutoff_day'],
                    'assay_day': day, 'viability_percent': point['viability_percent'] if point else '',
                    'status': response['status'], 'trajectory_kind': point['kind'] if point else 'unavailable',
                    'paired_summary_included': str(patient['patient_id'] in paired).lower(), 'source_kind': 'synthetic'})
    return stream.getvalue()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('export', type=Path, help='Single-patient or cohort JSON export')
    parser.add_argument('--csv', type=Path, help='Create a new cohort CSV; never overwrite an existing file')
    args = parser.parse_args()
    try:
        if args.export.stat().st_size > MAX_BYTES:
            raise ValueError('Export exceeds the 8 MiB size limit')
        expected = verify_export(strict_json(args.export.read_text(encoding='utf-8')))
        if args.csv:
            if expected['schema_version'] not in ('patient-cohort-v1', 'regimen-cohort-v1'):
                raise ValueError('CSV is supported for cohort exports only')
            with args.csv.open('x', encoding='utf-8', newline='') as file:
                file.write(regimen_csv(expected) if expected['schema_version']=='regimen-cohort-v1' else cohort_csv(expected))
        print(json.dumps({'record_integrity': 'verified', 'approved_synthetic_template': True,
            'schema_version': expected['schema_version'], 'model_version': expected['model_version'],
            'scientific_review': expected['scientific_review'], 'clinical_use': False,
            'limits': 'Unsigned metadata; no identity, signature or clinical validation.'}))
    except (ValueError, OSError, KeyError, TypeError, OverflowError) as error:
        parser.exit(1, 'Verification failed: ' + str(error) + '\n')


if __name__ == '__main__':
    main()
