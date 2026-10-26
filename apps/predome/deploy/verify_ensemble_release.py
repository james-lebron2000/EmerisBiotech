"""GET-only release checks; never creates model outputs or clinical records."""
import hashlib
import json
import ssl
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import certifi

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from emeris_live.prediction_ensemble import MEMBERS, POLICY_SHA256


def main(base, out):
    ctx = ssl.create_default_context(cafile=certifi.where())

    def get(route, raw=False):
        with urllib.request.urlopen(base.rstrip('/') + route, context=ctx, timeout=30) as response:
            body = response.read()
        return body if raw else json.loads(body)

    health = get('/api/health')
    if health['version'] != '1.1.8' or health['clinical_prediction_validated'] is not False:
        raise ValueError('Health does not match the unvalidated 1.1.8 release contract')
    caps = get('/api/predict/capabilities')
    if caps['pipeline_version'] != 'predome-user-trial-1.1.5':
        raise ValueError('Prediction pipeline version differs from the release contract')
    policy = caps['ensemble_policy']
    if policy['policy_version'] != 'equal-weight-two-model.v1':
        raise ValueError('Ensemble policy version differs from the release contract')
    if policy['policy_sha256'] != POLICY_SHA256:
        raise ValueError('Ensemble policy hash differs from the bound implementation')
    if policy['members'] != list(MEMBERS):
        raise ValueError('Ensemble members differ from the bound implementation')
    if policy['weights'] != {model: 0.5 for model in MEMBERS}:
        raise ValueError('Ensemble weights are not the required equal weights')
    if policy['requires_all_members'] is not True:
        raise ValueError('Ensemble no longer requires every member')
    if policy['clinical_prediction_validated'] is not False:
        raise ValueError('Ensemble clinical validation claim differs from the release contract')
    if policy['evaluation_scope'] != 'descriptive_only_excluded_from_model_performance':
        raise ValueError('Ensemble evaluation scope differs from the descriptive-only contract')
    comparisons = {}
    for name, route in [('snapshots', '/api/platform/snapshots?limit=100'),
                        ('baselines', '/api/platform/baselines?limit=100')]:
        previous = json.loads((ROOT / f'verification/upgrade/{name}-before-ensemble-1.1.8.json').read_text())
        current = get(route)
        # Compare the entire response, including old hashes and registration times.
        if current != previous:
            raise ValueError(name + ' changed during deployment')
        comparisons[name] = {'records': len(current['records']), 'full_response_unchanged': True,
                             'response_sha256': hashlib.sha256(json.dumps(current, sort_keys=True).encode()).hexdigest()}
    analytics = get('/api/analytics')
    if analytics['summary']['n'] != 0:
        raise ValueError('Unexpected prospective evaluated records in the ensemble release')
    doc = get('/api/product/method', raw=True).decode()
    if '1.1.8' not in doc or '等权双模型' not in doc or '不纳入当前单模型绩效' not in doc:
        raise ValueError('Published product method differs from the ensemble release contract')
    receipt = {'status': 'passed', 'checked_at_utc': datetime.now(timezone.utc).isoformat(),
               'base': base, 'scope': 'GET only; no provider invocation or clinical validation',
               'health': health, 'ensemble_policy': policy, 'llm_available': caps['llm_available'],
               'preserved_records': comparisons, 'prospective_evaluated_count': analytics['summary']['n'],
               'product_contract_updated': True}
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    print(json.dumps(receipt, ensure_ascii=False))


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'https://8.148.145.28',
         sys.argv[2] if len(sys.argv) > 2 else ROOT / 'verification/upgrade/ensemble-public-integrity-1.1.8.json')
