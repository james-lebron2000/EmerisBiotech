"""Read-only company/intervention/trial leads; registry roles never establish ownership.

Only exact NFKC/whitespace/case variants are combined. Trial phases remain a
set of observed trial phases, not an inferred development stage for an asset.
"""
import hashlib
import json
import unicodedata
from datetime import datetime, timezone


COMPARATOR_ROLES = frozenset({'ACTIVE_COMPARATOR', 'PLACEBO_COMPARATOR', 'SHAM_COMPARATOR'})
PLACEHOLDER_ROLES = frozenset({'PLACEBO_COMPARATOR', 'SHAM_COMPARATOR'})
PLACEBOS = frozenset({'placebo', '安慰剂'})
SALINES = frozenset({'saline', 'normal saline', 'physiological saline', '0.9% sodium chloride',
                     'sodium chloride 0.9%', '生理盐水'})


def normalize_name(value):
    return ' '.join(unicodedata.normalize('NFKC', value).split()).casefold()


def _display_name(value):
    return ' '.join(unicodedata.normalize('NFKC', value).split())


def _roles(value):
    if not isinstance(value, list):
        return []
    return sorted({r.strip().upper() for r in value if isinstance(r, str) and r.strip()})


def _role_status(role_sets):
    if any('EXPERIMENTAL' in roles for roles in role_sets):
        return 'experimental_arm'
    if role_sets and all(roles and set(roles) <= COMPARATOR_ROLES for roles in role_sets):
        return 'comparator_only'
    return 'unresolved'


def _placeholder(name, roles):
    if name in PLACEBOS and 'EXPERIMENTAL' not in roles:
        return True
    # Saline can itself be a therapeutic active comparator. Only an explicitly
    # placebo/sham role permits excluding it as a placeholder.
    return name in SALINES and bool(roles) and set(roles) <= PLACEHOLDER_ROLES


def _stamp(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed.replace(tzinfo=timezone.utc).timestamp() if parsed.tzinfo is None else parsed.timestamp()
    except (ValueError, OverflowError):
        return None


def _latest(values):
    valid = [(stamp, value) for value in values if (stamp := _stamp(value)) is not None]
    return max(valid)[1] if valid else None


def _run_key(run):
    return (_stamp(run.get('finished')) or _stamp(run.get('started')) or 0, run.get('started') or '', run['id'])


def _freshness(lineage_run, history, latest):
    if not lineage_run or not latest:
        return 'unknown'
    if lineage_run == latest['id']:
        return 'seen_in_latest'
    prior = history.get(lineage_run)
    if prior and prior['status'] == 'succeeded' and _run_key(prior) < _run_key(latest):
        return 'not_seen_in_latest'
    return 'unknown'


def build_assets(market, cid=None):
    """Return observed intervention leads, with no writes or new source requests.

    ``freshness`` compares lineage to the latest *successful complete* registry
    query, not to a failed/partial attempt. ``retained_trials`` identifies older
    observations not seen in that query and never implies termination. Individual
    source entries preserve mixed freshness and arm roles. Legacy records without
    lineage remain unknown. ``source_updated`` is the newest known source date;
    it is never replaced by the collection time.
    """
    where = ' WHERE t.company=?' if cid is not None else ''
    parameters = (cid,) if cid is not None else ()
    with market.db() as db:
        # Read all components in one SQLite snapshot while a collector may publish.
        db.execute('BEGIN')
        lineage_exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='trial_lineage'").fetchone()
        join = ' LEFT JOIN trial_lineage l ON l.company=t.company AND l.nct=t.nct' if lineage_exists else ''
        lineage = 'l.last_run,l.first_seen,l.last_seen' if lineage_exists else 'NULL AS last_run,NULL AS first_seen,NULL AS last_seen'
        trials = db.execute('SELECT t.company,t.nct,t.data,t.hash,t.observed,' + lineage + ' FROM trials t' + join + where + ' ORDER BY t.company,t.nct', parameters).fetchall()
        run_filter = "source='ClinicalTrials.gov'" + (' AND company=?' if cid is not None else '')
        runs = [dict(r) for r in db.execute('SELECT id,company,started,finished,status FROM runs WHERE ' + run_filter, parameters)]
    history = {r['id']: r for r in runs}
    latest_success, latest_attempt = {}, {}
    for run in runs:
        company = run['company']
        if company not in latest_attempt or _run_key(run) > _run_key(latest_attempt[company]):
            latest_attempt[company] = run
        if run['status'] == 'succeeded' and (company not in latest_success or _run_key(run) > _run_key(latest_success[company])):
            latest_success[company] = run
    assets = {}
    for row in trials:
        try:
            data = json.loads(row['data'])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid registry observation: {row['company']} / {row['nct']}") from exc
        if not isinstance(data, dict) or not isinstance(data.get('interventions', []), list):
            raise ValueError(f"Invalid intervention list: {row['company']} / {row['nct']}")
        for intervention in data.get('interventions', []):
            if not isinstance(intervention, dict) or intervention.get('type') not in ('DRUG', 'BIOLOGICAL'):
                continue
            raw_name = intervention.get('name')
            if not isinstance(raw_name, str) or not raw_name.strip():
                continue
            normalized = normalize_name(raw_name)
            roles = _roles(intervention.get('arm_roles'))
            if _placeholder(normalized, roles):
                continue
            key = (row['company'], normalized)
            if key not in assets:
                identity = json.dumps(key, ensure_ascii=False, separators=(',', ':')).encode()
                assets[key] = {
                    'id': hashlib.sha256(identity).hexdigest(), 'company': row['company'],
                    'drug_name': _display_name(raw_name), 'normalized_name': normalized,
                    'ownership': 'unverified', 'sources': {}, '_role_sets': [],
                    '_phases': set(), '_conditions': set(),
                }
            asset = assets[key]
            asset['_role_sets'].append(roles)
            asset['_phases'].update(x for x in data.get('phases', []) if isinstance(x, str))
            asset['_conditions'].update(x for x in data.get('conditions', []) if isinstance(x, str))
            if row['nct'] not in asset['sources']:
                latest = latest_success.get(row['company'])
                attempt = latest_attempt.get(row['company'])
                asset['sources'][row['nct']] = {
                    'nct': row['nct'], 'url': data.get('source_url'),
                    'title': data.get('title'), 'sponsor': data.get('sponsor'), 'status': data.get('status'),
                    'phases': data.get('phases', []), 'conditions': data.get('conditions', []),
                    'source_updated': data.get('source_updated'), 'observed': row['observed'],
                    'data_sha256': row['hash'], 'arm_roles': [], 'intervention_names': [],
                    'last_run': row['last_run'], 'latest_run': latest['id'] if latest else None,
                    'freshness': _freshness(row['last_run'], history, latest),
                    'first_seen': row['first_seen'], 'last_seen': row['last_seen'],
                    'latest_attempt_status': attempt['status'] if attempt else None,
                    '_role_sets': [],
                }
            source = asset['sources'][row['nct']]
            source['arm_roles'] = sorted(set(source['arm_roles']) | set(roles))
            source['intervention_names'] = sorted(set(source['intervention_names']) | {raw_name})
            source['_role_sets'].append(roles)
    result = []
    for asset in assets.values():
        asset['sources'] = sorted(asset['sources'].values(), key=lambda r: r['nct'])
        for source in asset['sources']:
            source['role_status'] = _role_status(source.pop('_role_sets'))
        asset['trials'] = [source['nct'] for source in asset['sources']]
        asset['phases'] = sorted(asset.pop('_phases'))
        asset['conditions'] = sorted(asset.pop('_conditions'))
        asset['role_status'] = _role_status(asset.pop('_role_sets'))
        asset['arm_roles'] = sorted({role for source in asset['sources'] for role in source['arm_roles']})
        asset['source_updated'] = _latest(source['source_updated'] for source in asset['sources'])
        asset['observed'] = _latest(source['observed'] for source in asset['sources'])
        fresh = [source['freshness'] for source in asset['sources']]
        asset['freshness'] = 'seen_in_latest' if 'seen_in_latest' in fresh else 'not_seen_in_latest' if all(x == 'not_seen_in_latest' for x in fresh) else 'unknown'
        asset['retained_trials'] = [source['nct'] for source in asset['sources'] if source['freshness'] == 'not_seen_in_latest']
        result.append(asset)
    return sorted(result, key=lambda a: (a['company'], a['normalized_name']))
