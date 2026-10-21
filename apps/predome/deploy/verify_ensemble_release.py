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
    assert health['version'] == '1.1.8' and health['clinical_prediction_validated'] is False
    caps = get('/api/predict/capabilities')
    assert caps['pipeline_version'] == 'predome-user-trial-1.1.5'
    policy = caps['ensemble_policy']
    assert policy['policy_version'] == 'equal-weight-two-model.v1'
    assert policy['policy_sha256'] == POLICY_SHA256
    assert policy['members'] == list(MEMBERS)
    assert policy['weights'] == {model: 0.5 for model in MEMBERS}
    assert policy['requires_all_members'] is True
    assert policy['clinical_prediction_validated'] is False
    assert policy['evaluation_scope'] == 'descriptive_only_excluded_from_model_performance'
    comparisons = {}
    for name, route in [('snapshots', '/api/platform/snapshots?limit=100'),
                        ('baselines', '/api/platform/baselines?limit=100')]:
        previous = json.loads((ROOT / f'verification/upgrade/{name}-before-ensemble-1.1.8.json').read_text())
        current = get(route)
        # Compare the entire response, including old hashes and registration times.
        assert current == previous, name + ' changed during deployment'
        comparisons[name] = {'records': len(current['records']), 'full_response_unchanged': True,
                             'response_sha256': hashlib.sha256(json.dumps(current, sort_keys=True).encode()).hexdigest()}
    analytics = get('/api/analytics')
    assert analytics['summary']['n'] == 0
    doc = get('/api/product/method', raw=True).decode()
    assert '1.1.8' in doc and '等权双模型' in doc and '不纳入当前单模型绩效' in doc
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
