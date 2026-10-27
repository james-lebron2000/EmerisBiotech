"""Acquire named official protocol/SAP originals; optionally index every PDF page.

This operator command does not create model inputs, predictions or review votes.
Original acquisition records remain separate from optional text extraction.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from emeris_live.trial_documents import acquire_documents, extract_pdf_pages


def write_json(path, value):
    raw = (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode()
    with path.open('xb') as stream:
        stream.write(raw)
    return hashlib.sha256(raw).hexdigest()


def verified_original(output, document):
    original = output / document['path']
    path = original.resolve()
    if original.is_symlink() or not path.is_relative_to(output.resolve()):
        raise ValueError('Acquired original left the output directory')
    raw = path.read_bytes()
    source = document['source']
    if len(raw) != source['bytes'] or hashlib.sha256(raw).hexdigest() != source['sha256']:
        raise ValueError('Acquired original integrity failed before extraction')
    return raw


def extract_acquired(manifest, output):
    """Verify the saved originals again at the extraction boundary."""
    original_manifest = (output / 'manifest.json').read_bytes()
    if json.loads(original_manifest) != manifest:
        raise ValueError('Acquisition manifest changed before extraction')
    if manifest['registry']['status'] == 'acquired':
        verified_original(output, manifest['registry'])
    elif any(document['status'] == 'acquired' for document in manifest['documents']):
        raise ValueError('Acquired document has no matching registry original')
    destination = output / 'document-extractions'
    destination.mkdir(mode=0o700)
    rows = []
    for document in manifest['documents']:
        if document['status'] != 'acquired':
            rows.append({'filename': document['filename'], 'status': 'not_acquired',
                         'pages_json_path': None, 'pages_json_sha256': None})
            continue
        raw = verified_original(output, document)
        source = document['source']
        result = extract_pdf_pages(raw)
        result.update(nct_id=manifest['nct_id'], filename=document['filename'],
                      acquisition_source=source,
                      registry_snapshot_sha256=manifest['registry']['source']['sha256'])
        relative = 'document-extractions/' + document['filename'] + '.pages.json'
        digest = write_json(output / relative, result)
        rows.append({'filename': document['filename'], 'status': result['status'],
                     'pages_json_path': relative, 'pages_json_sha256': digest,
                     'source_sha256': source['sha256'],
                     'total_pages': result.get('page_count'),
                     'extracted_pages': len(result.get('pages', []))})
    complete = bool(rows) and all(row['status'] == 'complete' for row in rows)
    report = {'schema_version': 'predome.trial-document-extractions-1',
              'created_at_utc': datetime.now(timezone.utc).isoformat(),
              'nct_id': manifest['nct_id'],
              'acquisition_manifest_sha256': hashlib.sha256(original_manifest).hexdigest(),
              'status': 'completed' if complete else 'incomplete', 'documents': rows,
              'model_calls': 0, 'ledger_writes': 0, 'human_scientific_reviews': 0,
              'use': 'source_text_for_separate_target_matching_and_scientific_review'}
    write_json(output / 'extraction-manifest.json', report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--nct', required=True, help='Exact ClinicalTrials.gov NCT identifier')
    parser.add_argument('--output-dir', required=True, type=Path, help='New directory; existing output is never overwritten')
    parser.add_argument('--document', action='append', default=[], metavar='FILENAME',
                        help='Exact listed protocol/SAP filename; repeat to select several. Omit for registry inventory only.')
    parser.add_argument('--extract', action='store_true', help='Additionally create separate page-indexed text files for the selected PDFs')
    args = parser.parse_args(argv)
    if args.extract and not args.document:
        parser.error('--extract requires at least one explicitly selected --document')
    try:
        manifest = acquire_documents(args.nct, args.output_dir, filenames=tuple(args.document))
        extraction = extract_acquired(manifest, args.output_dir) if args.extract else None
    except (OSError, ValueError):
        parser.exit(1, 'Document acquisition or saved-file integrity failed; inspect the new output manifest if present.\n')
    print(json.dumps({'nct_id': manifest['nct_id'], 'status': manifest['status'],
                      'acquired_documents': sum(row['status'] == 'acquired' for row in manifest['documents']),
                      'extraction_status': extraction['status'] if extraction else 'not_requested',
                      'output_dir': str(args.output_dir), 'model_calls': 0,
                      'ledger_writes': 0, 'human_scientific_reviews': 0}, ensure_ascii=False))
    return 0 if manifest['status'] == 'complete' and (extraction is None or extraction['status'] == 'completed') else 1


if __name__ == '__main__':
    raise SystemExit(main())
