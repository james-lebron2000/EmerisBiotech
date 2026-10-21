"""Read-only immutable historical benchmark; never loads or unpickles models."""
from pathlib import Path
import json, hashlib
from .predome_statistics import sha

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'predome_data/historical_v2'

def read_historical():
    envelope = json.loads((DATA / 'release.json').read_text())
    record = envelope['record']
    if sha(record) != envelope['sha256']: raise ValueError('Historical release integrity failed')
    for source in record['artifact_manifest']:
        p = (ROOT / source['path']).resolve()
        if not p.is_relative_to(ROOT) or not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest() != source['sha256']: raise ValueError('Historical artifact integrity failed')
    return envelope | {'integrity_verified': True}
