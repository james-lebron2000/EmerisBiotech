"""Paired probability scores; missing forecasts retain their assigned denominator."""
from collections import defaultdict
import math
import random
from .arena_store import instant, sha
from .arena_citations import aggregate_runs


def _eligible(run, target):
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
        costs = [r.get('cost_usd') for r in rows]
        costs_complete = bool(costs) and all(type(c) in (int,float) and math.isfinite(c) and c >= 0 for c in costs)
        by_model.append({'model_id': model, 'model_version': version, 'workflow_version': workflow,
            'returned_versions': returned_versions[key], 'mixed_versions_excluded': len(returned_versions[key]) > 1,
            'assigned': len(rows), 'answered': sum(r['status']=='predicted' for r in rows),
            'coverage': sum(r['status']=='predicted' for r in rows)/len(rows),
            'abstained': sum(r['status']=='abstained' for r in rows),
            'failed': sum(r['status'] in ('failed','unavailable','timed_out') for r in rows),
            'pending': sum(r['status']=='pending' for r in rows),
            'scored_tasks': len(usable), **score, 'common_brier': common_score['brier'],
            'eligible_coverage': len(usable)/len(rows),
            'timestamp_basis': 'external_TSA_and_server_ledger' if usable and all(r.get('timestamp_proof',{}).get('status')=='attested' for r in usable.values()) else 'server_ledger_only_or_mixed',
            'common_log_loss': common_score['log_loss'], 'common_tasks': len(common),
            'independent_projects': len({targets[i]['project_id'] for i in usable}),
            'cost_usd': sum(costs) if costs_complete else None,
            'cost_complete': costs_complete,'citation_support': aggregate_runs(rows)})
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
            'delta_brier_with_minus_without':(q-y)**2-(p-y)**2 if scored else None,
            'interpretation':'概率变化不等于预测改善；评分等待事前锁定记录对应的已裁定结果。'})
    return result
