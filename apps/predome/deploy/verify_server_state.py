"""Read-only operational receipt for the active release on the deployment host."""
import hashlib
import json
import sqlite3
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


def main(output):
    current = Path('/opt/emeris-live/current').resolve()
    manifest = json.loads((current / 'RELEASE_MANIFEST.json').read_text())
    for item in manifest['files']:
        assert hashlib.sha256((current / item['path']).read_bytes()).hexdigest() == item['sha256'], item['path']

    def get(route):
        req = urllib.request.Request('http://127.0.0.1:19320' + route, headers={'Host': '8.148.145.28'})
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.load(response)

    health = get('/api/health')
    assert health['version'] == manifest['version']
    with sqlite3.connect('file:/var/lib/emeris-live/workbench.sqlite3?mode=ro', uri=True) as db:
        integrity = db.execute('PRAGMA integrity_check').fetchone()[0]
        assert integrity == 'ok'
        counts = {t: db.execute('SELECT COUNT(*) FROM ' + t).fetchone()[0] for t in (
            'platform_prospective_baselines', 'platform_prediction_snapshots',
            'platform_actual_outcomes', 'platform_outcome_verifications', 'intake_candidates', 'intake_jobs')}
    services = {}
    for unit in ('emeris-live', 'emeris-intake.timer', 'emeris-intake.service'):
        raw = subprocess.check_output(['systemctl', 'show', unit, '--property=ActiveState,SubState,Result,NRestarts,MemoryCurrent'], text=True)
        services[unit] = dict(line.split('=', 1) for line in raw.splitlines() if '=' in line)
    assert services['emeris-live']['ActiveState'] == 'active'
    assert services['emeris-intake.timer']['ActiveState'] == 'active'
    processes = subprocess.check_output(['ps', '-p', '1720,1852,1878', '-o', 'pid=,comm='], text=True).strip()
    for pid in ('1720', '1852', '1878'):
        assert any(line.split()[0] == pid for line in processes.splitlines()), 'Pre-existing process missing: ' + pid
    receipt = {'status': 'passed', 'checked_at_utc': datetime.now(timezone.utc).isoformat(),
               'release': str(current), 'files_verified': len(manifest['files']), 'sqlite_integrity': integrity,
               'counts': counts, 'health': health, 'services': services,
               'platform_capabilities': get('/api/platform/capabilities'),
               'prediction_capabilities': get('/api/predict/capabilities'),
               'baseline_summary': get('/api/platform/baselines')['summary'],
               'preserved_processes': processes, 'scope': 'read-only operational checks; no clinical validation'}
    Path(output).write_text(json.dumps(receipt, indent=2))
    print(json.dumps(receipt))


if __name__ == '__main__':
    main(sys.argv[1])
