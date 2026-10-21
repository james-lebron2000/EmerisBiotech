"""Read-only v3 development replay; authenticate files without loading models."""
import hashlib
import json
from pathlib import Path
import re

from .predome_statistics import sha

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'predome_data/historical_v3'
REPORT = 'docs/Predome_扩展训练与比较_20261017.md'
_HEX = re.compile(r'^[0-9a-f]{64}$')


def _artifact_path(value):
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        raise ValueError('Invalid expanded artifact path')
    path = (ROOT / value).resolve()
    if not path.is_relative_to(ROOT.resolve()) or not path.is_file():
        raise ValueError('Expanded artifact unavailable')
    return path


def read_expanded():
    envelope = json.loads((DATA / 'release.json').read_text(encoding='utf-8'))
    if not isinstance(envelope, dict) or not isinstance(envelope.get('record'), dict):
        raise ValueError('Invalid expanded release')
    record = envelope['record']
    if not isinstance(envelope.get('sha256'), str) or not _HEX.fullmatch(envelope['sha256']) or sha(record) != envelope['sha256']:
        raise ValueError('Expanded release integrity failed')
    manifest = record.get('artifact_manifest')
    if not isinstance(manifest, list) or not manifest:
        raise ValueError('Expanded artifact manifest unavailable')
    seen = set()
    for source in manifest:
        if not isinstance(source, dict):
            raise ValueError('Invalid expanded artifact')
        path = _artifact_path(source.get('path'))
        expected = source.get('sha256')
        if path in seen or not isinstance(expected, str) or not _HEX.fullmatch(expected):
            raise ValueError('Invalid expanded artifact manifest')
        seen.add(path)
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(chunk)
        actual = digest.hexdigest()
        if actual != expected:
            raise ValueError('Expanded artifact integrity failed')
    return envelope | {'integrity_verified': True}


def expanded_report_path(envelope):
    """Only serve the report if it belongs to the verified release manifest."""
    if not envelope.get('integrity_verified'):
        raise ValueError('Expanded report not verified')
    path = _artifact_path(REPORT)
    if not any(_artifact_path(item.get('path')) == path for item in envelope['record']['artifact_manifest']):
        raise ValueError('Expanded report missing from manifest')
    return path
