"""Paired probability scores; missing forecasts retain their assigned denominator."""
from collections import Counter, defaultdict
import math
import random
from .arena_store import instant, sha
from .arena_citations import aggregate_runs


def _amount(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _native_cost(run, *, partial=False):
    """Read one native-currency record, never convert or double-count its alias."""
    field = 'known_cost_amount' if partial else 'cost_amount'
    legacy = 'known_cost_usd' if partial else 'cost_usd'
    amount, currency = run.get(field), run.get('cost_currency')
    usd = run.get(legacy)
    if amount is not None:
        if not _amount(amount) or currency not in ('USD', 'CNY'):
            return None
        if usd is not None and (currency != 'USD' or not _amount(usd) or usd != amount):
            return None
        return currency, amount
    if currency in (None, 'USD') and _amount(usd):
        return 'USD', usd
    return None


def _cost_record(run):
    full = _native_cost(run)
    partial = _native_cost(run, partial=True)
    if ((any(run.get(k) is not None for k in ('cost_amount', 'cost_usd')) and full is None)
            or (any(run.get(k) is not None for k in ('known_cost_amount', 'known_cost_usd')) and partial is None)):
        return None
    breakdown = run.get('cost_breakdown')
    known = {}
    if breakdown is not None:
        if not isinstance(breakdown, list) or len(breakdown) > 2:
            return None
        for item in breakdown:
            if (not isinstance(item, dict) or set(item) != {'currency', 'known_amount', 'complete'}
                    or item['currency'] not in ('CNY', 'USD') or item['currency'] in known
                    or not _amount(item['known_amount']) or type(item['complete']) is not bool):
                return None
            known[item['currency']] = item['known_amount']
            if item['complete'] and (len(breakdown) != 1 or (full and full != (item['currency'], item['known_amount']))):
                return None
        if partial and known.get(partial[0]) != partial[1]:
            return None
    elif partial:
        known[partial[0]] = partial[1]
    if full:
        if any(currency != full[0] or amount > full[1] for currency, amount in known.items()):
            return None
        known = {full[0]: full[1]}
    return full, known


def recorded_cost(run):
    """A complete cost must also agree with known portions and currency aliases."""
    record = _cost_record(run)
    return record[0] if record else None


def cost_summary(rows):
    """All assigned calls count; partial known spend is not a complete bill."""
    totals, known, bases = defaultdict(float), defaultdict(float), set()
    complete_runs = partial_runs = 0
    for row in rows:
        record = _cost_record(row)
        full, portions = record if record else (None, {})
        if full:
            complete_runs += 1
            totals[full[0]] += full[1]
        elif portions:
            partial_runs += 1
        for currency, amount in portions.items():
            known[currency] += amount
        if portions:
            bases.add(row.get('cost_basis') or 'not_recorded')
    complete = bool(rows) and complete_runs == len(rows)
    currency = next(iter(totals)) if complete and len(totals) == 1 else None
    amount = totals[currency] if currency else None
    return {'cost_complete': complete, 'cost_amount': amount, 'cost_currency': currency,
        'cost_usd': amount if currency == 'USD' else None,
        'cost_totals_by_currency': dict(sorted(totals.items())),
        'known_cost_totals_by_currency': dict(sorted(known.items())),
        'cost_recorded_runs': complete_runs, 'cost_partial_runs': partial_runs,
        'cost_unknown_runs': len(rows) - complete_runs - partial_runs,
        'cost_basis': next(iter(bases)) if len(bases) == 1 else 'mixed_or_unknown',
        'cost_is_estimate': any(r.get('cost_is_estimate') is True or r.get('cost_basis') == 'configured_rates_estimate' for r in rows)}


def paired_cost(control, treated):
    a, b = recorded_cost(control), recorded_cost(treated)
    reason = ('incomplete_cost' if a is None or b is None else
              'currency_mismatch' if a[0] != b[0] else
              'cost_basis_unconfirmed' if control.get('cost_basis') != 'configured_rates_estimate'
                  or treated.get('cost_basis') != control.get('cost_basis') else None)
    return {'comparable': reason is None, 'currency': a[0] if reason is None else None,
        'delta_amount': b[1] - a[1] if reason is None else None,
        'basis': control.get('cost_basis') if reason is None else None, 'reason': reason,
        'is_estimate': reason is None}


def _input_blocked(run):
    return any(i.get('severity') == 'blocking' for i in run.get('input_evidence_issues', []))


def _eligible(run, target):
    if _input_blocked(run):
        return False
    result = target.get('result') or {}
    if (target.get('status') != 'adjudicated' or result.get('label') not in ('success', 'failure')
            or run.get('status') != 'predicted' or run.get('submission_on_time') is not True
            or not run.get('returned_model')
            or type(run.get('probability')) not in (int,float) or not math.isfinite(run['probability'])
            or not 0 <= run['probability'] <= 1):
        return False
    try:
        proof = run.get('timestamp_proof') or {}
        if proof.get('status') == 'attested' and instant(proof['generated_at_utc']) >= instant(result['first_public_at']):
            return False
        return (instant(run['registered_at']) <= instant(run['locked_at_utc']) < instant(result['first_public_at'])
                and instant(run['locked_at_utc']) <= instant(target['submission_deadline_utc']))
    except (ValueError, KeyError, TypeError):
        return False


def _same_comparison_input(a, b, track):
    if track not in ('same_evidence','shared_agent'):
        return True
    return (a.get('input_sha256') is not None and a.get('input_sha256') == b.get('input_sha256')
            and a.get('workflow_version') == b.get('workflow_version')
            and a.get('expected_prompt_sha256') == b.get('expected_prompt_sha256')
            and a.get('execution_budget_sha256') == b.get('execution_budget_sha256'))


def _scores(rows):
    if not rows:
        return {'brier': None, 'log_loss': None, 'accuracy': None}
    return {'brier': sum((p-y)**2 for p,y in rows)/len(rows),
            'log_loss': -sum(y*math.log(max(1e-6,min(1-1e-6,p)))+(1-y)*math.log(max(1e-6,min(1-1e-6,1-p))) for p,y in rows)/len(rows),
            'accuracy': sum((p >= .5) == bool(y) for p,y in rows)/len(rows)}


def clustered_interval(differences, repetitions=2000):
    """Project resampling retains all paired target differences within each cluster."""
    by_project = defaultdict(list)
    for project, diff in differences:
        by_project[project].append(diff)
    if len(by_project) < 2:
        return None
    groups = list(by_project.values())
    rng = random.Random(20261022)
    values = []
    for _ in range(repetitions):
        sample = [value for group in rng.choices(groups, k=len(groups)) for value in group]
        values.append(sum(sample)/len(sample))
    values.sort()
    return {'lower': values[int(.025*repetitions)], 'upper': values[min(repetitions-1,int(.975*repetitions))],
            'method': 'paired drug-project cluster percentile bootstrap', 'repetitions': repetitions,
            'projects': len(groups), 'small_sample_warning': len(groups) < 20}


def build_metrics(snapshot, *, cohort_id=None, track='same_evidence', condition='clinical_only'):
    cohorts = snapshot.get('cohorts', [])
    if cohort_id is None and cohorts:
        cohort_id = cohorts[-1]['id']
    runs = [r for r in snapshot.get('runs', []) if r.get('scope') == 'prospective_challenge'
            and r.get('cohort_id') == cohort_id and r.get('track') == track and r.get('condition') == condition]
    targets = {t['id']: t for t in snapshot.get('targets', [])}
    groups = defaultdict(list)
    for run in runs:
        # A changed returned model version is a distinct participant, never pooled.
        version = run.get('requested_model') or 'version_unconfirmed'
        groups[(run['model_id'], version, run.get('workflow_version'))].append(run)
    returned_versions = {key: sorted({r['returned_model'] for r in rows if r.get('returned_model')}) for key, rows in groups.items()}
    answered = {key: {r['target_id']: r for r in rows if _eligible(r, targets.get(r['target_id'],{})) and len(returned_versions[key]) <= 1}
                for key, rows in groups.items()}
    common = set.intersection(*(set(v) for v in answered.values())) if len(answered) >= 2 else set()
    common = {ident for ident in common if all(_same_comparison_input(next(iter(answered.values()))[ident], rows[ident], track)
                                              for rows in answered.values())}
    by_model = []
    for key, rows in sorted(groups.items()):
        model, version, workflow = key
        usable = answered[key]
        def values(ids):
            return [(usable[i]['probability'], int(targets[i]['result']['label']=='success')) for i in ids]
        score = _scores(values(usable))
        common_score = _scores(values(common))
        by_model.append({'model_id': model, 'model_version': version, 'workflow_version': workflow,
            'returned_versions': returned_versions[key], 'mixed_versions_excluded': len(returned_versions[key]) > 1,
            'assigned': len(rows), 'answered': sum(r['status']=='predicted' for r in rows),
            'coverage': sum(r['status']=='predicted' for r in rows)/len(rows),
            'abstained': sum(r['status']=='abstained' for r in rows),
            'failed': sum(r['status'] in ('failed','unavailable','timed_out') for r in rows),
            'pending': sum(r['status']=='pending' for r in rows),
            'withdrawn':sum(r['status']=='withdrawn' for r in rows),
            'input_blocked':sum(_input_blocked(r) for r in rows),
            'scored_tasks': len(usable), **score, 'common_brier': common_score['brier'],
            'eligible_coverage': len(usable)/len(rows),
            'timestamp_basis': 'external_TSA_and_server_ledger' if usable and all(r.get('timestamp_proof',{}).get('status')=='attested' for r in usable.values()) else 'server_ledger_only_or_mixed',
            'common_log_loss': common_score['log_loss'], 'common_tasks': len(common),
            'independent_projects': len({targets[i]['project_id'] for i in usable}),
            'outcome_review_basis_counts':dict(Counter((targets[i].get('result') or {}).get('review_provenance',{}).get('basis','not_recorded') for i in usable)),
            **cost_summary(rows),'citation_support': aggregate_runs(rows)})
    paired = []
    keys = sorted(groups)
    for i, a in enumerate(keys):
        for b in keys[i+1:]:
            common_ab = set(answered[a]) & set(answered[b])
            common_ab = {ident for ident in common_ab if _same_comparison_input(answered[a][ident], answered[b][ident], track)}
            diffs = []
            for ident in sorted(common_ab):
                y = int(targets[ident]['result']['label']=='success')
                diffs.append((targets[ident]['project_id'], (answered[a][ident]['probability']-y)**2-(answered[b][ident]['probability']-y)**2))
            paired.append({'model_a': a[0], 'version_a': a[1], 'model_b': b[0], 'version_b': b[1],
                           'tasks': len(diffs), 'delta_brier_a_minus_b': sum(v for _,v in diffs)/len(diffs) if diffs else None,
                           'interval': clustered_interval(diffs)})
    return {'cohort_id': cohort_id, 'track': track, 'condition': condition,
            'assigned': len(runs), 'answered': sum(r['status']=='predicted' for r in runs),
            'abstained': sum(r['status']=='abstained' for r in runs),
            'failed': sum(r['status'] in ('failed','unavailable','timed_out') for r in runs),
            'pending': sum(r['status']=='pending' for r in runs),
            'withdrawn':sum(r['status']=='withdrawn' for r in runs),
            'input_blocked':sum(_input_blocked(r) for r in runs),
            'adjudicated': len({r['target_id'] for r in runs if targets.get(r['target_id'],{}).get('status')=='adjudicated'}),
            'common_tasks': len(common), 'independent_projects': len({targets[i]['project_id'] for i in common}),
            'ranking_allowed': False, 'reason': '展示配对评分与覆盖率；小样本和未解决结果不支持冠军声明。',
            'by_model': by_model, 'paired_differences': paired,'citation_support':aggregate_runs(runs),
            'baseline': {'name': '预先固定 0.5 参考值', 'common_brier': .25 if common else None, 'common_tasks': len(common)},
            'clinical_prediction_validated': False}


def patent_pairs(snapshot):
    runs = snapshot.get('runs', [])
    targets = {t['id']:t for t in snapshot.get('targets', [])}
    result = []
    for treated in runs:
        if treated.get('condition') != 'with_patents' or treated.get('track') != 'patent_ablation':
            continue
        controls = [r for r in runs if r.get('condition') == 'clinical_only'
            and r.get('track') in ('same_evidence','patent_ablation')
            and all(r.get(k)==treated.get(k) for k in ('cohort_id','target_id','model_id','requested_model',
                'returned_model','workflow_version','config_sha256','execution_budget_sha256','expected_prompt_sha256'))]
        if len(controls) != 1:
            continue
        control = controls[0]
        base = control.get('packet',{})
        intervention = treated.get('packet',{})
        stripped = {**intervention, 'evidence':[e for e in intervention.get('evidence',[]) if e.get('kind')!='patent']}
        if base != stripped or not treated.get('returned_model'):
            continue
        p, q = control.get('probability'), treated.get('probability')
        valid = control.get('status')=='predicted' and treated.get('status')=='predicted'
        target = targets.get(treated['target_id'],{})
        scored = _eligible(control,target) and _eligible(treated,target)
        y = int((target.get('result') or {}).get('label')=='success')
        result.append({'target_id':treated['target_id'],'model_id':treated['model_id'],'control_run_id':control['id'],
            'treated_run_id':treated['id'],'pair_verified':True,'probability_change':q-p if valid else None,
            'cost_comparison':paired_cost(control, treated),
            'delta_brier_with_minus_without':(q-y)**2-(p-y)**2 if scored else None,
            'interpretation':'概率变化不等于预测改善；评分等待事前锁定记录对应的已裁定结果。'})
    return result
