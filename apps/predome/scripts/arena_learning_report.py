"""Export an offline error-review queue; never call providers or change a ledger."""
from collections import Counter, defaultdict
from datetime import datetime, timezone
import argparse
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from emeris_live.arena_store import ArenaLedger, canonical, https, instant, sha
from emeris_live.arena_metrics import _eligible

VERSION = 'arena-learning-report-1.2'
HASH = re.compile(r'[a-f0-9]{64}')
MODEL_FIELDS = ('cohort_id', 'track', 'condition', 'provider', 'model_id', 'requested_model',
                'returned_model', 'workflow_version', 'config_sha256', 'execution_budget_sha256')
ISSUES = {
    'input_correction': '核对追加登记的来源更正；保留原始输出，仅以新截止、新证据包和新登记评估更正版本。',
    'withdrawn_before_execution': '执行前撤回不表示模型或疗效失败；查阅原因及后继队列，保留原分配记录。',
    'source_missing': '核对原始公开来源、版本和内容哈希；仅在新证据包中补齐，经审核后重新封存。',
    'citation_invalid': '用离线固定样例复现引用校验；保留原失败记录，不进行语义重试或替换。',
    'evidence_time_invalid': '核对证据可用时间与截止时间，排除目标结果；不得回填旧预测输入。',
    'target_definition_missing': '补齐端点、人群、对照及成功判据；新目标须重新注册。',
    'timeout': '离线检查响应耗时和资源限制；任何预算调整均须事前审核并登记新版本。',
    'abstention': '人工区分合理拒答与资料缺口，不把拒答改成0概率，也不按未知结局强迫回答。',
    'provider_unavailable': '离线核对服务配置和访问条件；失败仍保留在原分母，不重跑替换。',
    'budget_gate': '核对冻结预算与工作流要求；不自动扩大调用量、token或金额上限。',
    'incomplete_output': '用截断响应样例检查解析与失败归档，不进行自动补全或语义修复。',
    'output_contract_invalid': '用隔离样例检查结构、有限概率和目标对应，不用后验结果改写原输出。',
    'provider_transport': '离线检查传输错误分类；原调用可能已计费，禁止自动重试。',
    'version_unconfirmed': '核实请求与返回模型标识及配置版本，无法确认的记录不用于误差评分。',
    'run_incomplete': '核查是否中断在已开始未完成状态；先确认计费与请求状态，不自动重试。',
    'unclassified_failure': '人工复核故障类别；不导出原始provider错误或凭据。',
}
ERROR_CATEGORIES = {
    **dict.fromkeys(('provider_timeout', 'timeout'), 'timeout'),
    **dict.fromkeys(('unsupported_citation', 'prediction_without_evidence', 'invalid_evidence_id'), 'citation_invalid'),
    **dict.fromkeys(('evidence_missing', 'evidence_source_missing', 'invalid_evidence', 'invalid_evidence_text'), 'source_missing'),
    **dict.fromkeys(('future_cutoff', 'evidence_after_cutoff', 'evidence_not_eligible', 'invalid_evidence_date',
                     'evidence_date_missing'), 'evidence_time_invalid'),
    'target_incomplete': 'target_definition_missing',
    **dict.fromkeys(('model_not_registered', 'provider_not_configured', 'credential_missing', 'invalid_registry'), 'provider_unavailable'),
    **dict.fromkeys(('invalid_budget', 'input_budget_exceeded', 'request_budget_exceeded', 'budget_price_unknown',
                     'budget_currency_mismatch', 'cost_budget_exceeded', 'explicit_unpriced_policy_required'), 'budget_gate'),
    **dict.fromkeys(('provider_response_incomplete', 'provider_output_missing', 'provider_response_too_large'), 'incomplete_output'),
    **dict.fromkeys(('invalid_output', 'invalid_probability', 'invalid_json', 'duplicate_json_key', 'nonfinite_json',
                     'invalid_review', 'target_mismatch', 'invalid_provider_response', 'credential_echo_rejected',
                     'contract_rejected'), 'output_contract_invalid'),
    'provider_transport_error': 'provider_transport',
}
AGENT_ROLES = ('evidence_review', 'forecast')
STAGE_STATUSES = ('reviewed', 'predicted', 'abstain', 'abstained', 'failed', 'unavailable', 'timed_out')


def _error_category(code):
    if not isinstance(code, str):
        return None
    if re.fullmatch(r'provider_http_\d{3}', code):
        return 'provider_transport'
    return ERROR_CATEGORIES.get(code)


def _execution_issues(status, code):
    """Keep the single-model status/error classification shared with Agent stages."""
    issues = set()
    if status in ('abstain', 'abstained'):
        issues.add('abstention')
    if status == 'unavailable':
        issues.add('provider_unavailable')
    if status == 'timed_out':
        issues.add('timeout')
    category = _error_category(code)
    if category:
        issues.add(category)
    elif code or status == 'failed':
        issues.add('unclassified_failure')
    return issues


def _agent_stage_observations(run):
    """Read three stage fields only; never export arbitrary provider values."""
    trace = run.get('agent_trace')
    stages = trace.get('stages') if isinstance(trace, dict) else None
    if not isinstance(stages, list):
        return []
    result = []
    for stage in stages:
        if not isinstance(stage, dict) or stage.get('role') not in AGENT_ROLES:
            continue
        status = stage.get('status')
        status = status if isinstance(status, str) and status in STAGE_STATUSES else 'unknown'
        code = stage.get('error_code')
        categories = _execution_issues(status, code)
        if status == 'unknown':
            categories.add('unclassified_failure')
        if categories:
            result.append({'role': stage['role'], 'status': status,
                           'error_code': code if _error_category(code) else None,
                           'categories': sorted(categories)})
    return result


def read_snapshot(ledger_path):
    """Do not instantiate ArenaLedger: its constructor writes schema/permissions."""
    path = Path(ledger_path).resolve(strict=True)
    if not path.is_file():
        raise ValueError('An existing SQLite ledger file is required')
    db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=10)
    db.row_factory = sqlite3.Row
    try:
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        events = ArenaLedger._verified_events(None, db)
    finally:
        db.close()

    class FrozenEvents:
        def events(self):
            return events

    return ArenaLedger.snapshot(FrozenEvents())


def _engineering(run, agent_stages=None):
    issues = set()
    if run.get('input_evidence_issues'):issues.add('input_correction')
    if run.get('withdrawn_before_execution'):issues.add('withdrawn_before_execution')
    packet = run.get('packet') or {}
    target = packet.get('target') or {}
    if any(not target.get(k) for k in ('endpoint', 'population', 'comparison', 'success_definition')):
        issues.add('target_definition_missing')
    evidence = packet.get('evidence') or []
    if not evidence:
        issues.add('source_missing')
    ids = set()
    for item in evidence:
        if not isinstance(item, dict):
            issues.add('source_missing')
            continue
        if not https(item.get('source_url')) or not HASH.fullmatch(str(item.get('source_sha256', ''))):
            issues.add('source_missing')
        if not isinstance(item.get('id'), str) or not item['id'] or item['id'] in ids:
            issues.add('citation_invalid')
        else:
            ids.add(item['id'])
        try:
            available = item.get('availability_date') or item.get('available_at') or item.get('publication_date')
            # Ledger inputs require timestamp precision; no missing-date inference.
            if instant(available) > instant(packet['data_cutoff']):
                issues.add('evidence_time_invalid')
        except (ValueError, TypeError, KeyError):
            issues.add('evidence_time_invalid')
        if (item.get('is_target_result') is True or item.get('eligible_for_forecast') is False
                or item.get('kind') in ('target_result', 'adjudicated_result')):
            issues.add('evidence_time_invalid')
    cited = run.get('evidence_ids') or []
    if not isinstance(cited, list) or any(not isinstance(e, str) or e not in ids for e in cited):
        issues.add('citation_invalid')
        cited = []
    if run.get('status') == 'predicted' and not cited:
        issues.add('citation_invalid')
    for field in ('supporting_evidence_ids', 'opposing_evidence_ids'):
        values = run.get(field) or []
        if not isinstance(values, list) or any(not isinstance(e, str) or e not in cited for e in values):
            issues.add('citation_invalid')
    if run.get('status') == 'predicted' and (not run.get('returned_model') or not run.get('requested_model')):
        issues.add('version_unconfirmed')
    if run.get('status') == 'pending' and run.get('started_at'):
        issues.add('run_incomplete')
    code = run.get('error_code')
    execution = _execution_issues(run.get('status'), code)
    agent_stages = _agent_stage_observations(run) if agent_stages is None else agent_stages
    stage_categories = {category for stage in agent_stages for category in stage['categories']}
    # The wrapper is not a second unknown cause when a recorded stage explains it.
    # Keep unrelated top-level failures and genuinely unknown stage failures visible.
    if code == 'agent_component_failed' and stage_categories - {'unclassified_failure'}:
        execution.discard('unclassified_failure')
    issues.update(execution)
    issues.update(stage_categories)
    return sorted(issues)


def _exclusion(run, target, engineering):
    if run.get('withdrawn_before_execution'):return 'withdrawn_before_execution'
    if any(i.get('severity')=='blocking' for i in run.get('input_evidence_issues',[])):return 'blocking_input_correction'
    if run.get('status') != 'predicted':
        return 'no_probability_prediction'
    result = target.get('result') or {}
    if target.get('status') != 'adjudicated':
        return 'outcome_not_adjudicated'
    if result.get('label') not in ('success', 'failure'):
        return 'outcome_not_binary_adjudicable'
    if run.get('contract_complete') is False:
        return 'incomplete_execution_contract'
    if 'version_unconfirmed' in engineering:
        return 'model_version_unconfirmed'
    if set(engineering) & {'source_missing', 'citation_invalid', 'evidence_time_invalid', 'target_definition_missing'}:
        return 'evidence_contract_not_satisfied'
    if not _eligible(run, target):
        return 'time_or_probability_not_eligible'
    return None


def _loss(run, target):
    p, y = run['probability'], int(target['result']['label'] == 'success')
    clipped = max(1e-6, min(1 - 1e-6, p))
    correct = (p >= .5) == bool(y)
    category = ('correct_direction' if correct else 'predicted_success_actual_failure' if p >= .5
                else 'predicted_failure_actual_success')
    return {'run_id': run['id'], 'target_id': run['target_id'], 'project_id': target['project_id'],
        'probability': p, 'actual_label': target['result']['label'], 'brier': (p-y)**2,
        'log_loss': -(y*math.log(clipped)+(1-y)*math.log(1-clipped)),
        'correct_at_threshold_0_5': correct, 'error_category': category,
        'high_confidence_error': not correct and (p >= .8 or p <= .2),
        'outcome_source_sha256': target['result'].get('source_sha256'),
        'outcome_review_basis':target['result'].get('review_provenance',{}).get('basis','not_recorded'),
        'independent_human_review_verified':False,
        'timestamp_basis': 'external_TSA_and_server_ledger' if run.get('timestamp_proof',{}).get('status')=='attested' else 'server_ledger_only'}


def _summary(rows):
    scored = [r['loss'] for r in rows if r['loss'] is not None]
    projects = defaultdict(list)
    for row in scored:
        projects[row['project_id']].append(row['brier'])
    statuses = Counter(r['run']['status'] for r in rows)
    engineering = Counter(c for r in rows for c in r['engineering'])
    return {'assigned': len(rows), 'predicted': statuses['predicted'],
        'coverage': statuses['predicted']/len(rows) if rows else None,
        'abstained': statuses['abstained'] + statuses['abstain'],
        'failed': sum(statuses[k] for k in ('failed','unavailable','timed_out')),
        'pending': statuses['pending'], 'scored_forecasts': len(scored),
        'scored_targets': len({r['target_id'] for r in scored}), 'independent_projects': len(projects),
        'brier': sum(r['brier'] for r in scored)/len(scored) if scored else None,
        'log_loss': sum(r['log_loss'] for r in scored)/len(scored) if scored else None,
        'accuracy': sum(r['correct_at_threshold_0_5'] for r in scored)/len(scored) if scored else None,
        'project_equal_weight_brier': sum(sum(v)/len(v) for v in projects.values())/len(projects) if projects else None,
        'engineering_categories': dict(sorted(engineering.items())),
        'outcome_error_categories': dict(sorted(Counter(r['error_category'] for r in scored).items())),
        'scoring_denominator': 'Eligible adjudicated forecasts only; assigned denominator separately retained.',
        'causal_or_calibration_claim': False}


def _groups(rows, fields):
    groups = defaultdict(list)
    for row in rows:
        identity = {field: row['run'].get(field) for field in fields}
        groups[canonical(identity)].append(row)
    return [{'group': json.loads(key), **_summary(group)} for key, group in sorted(groups.items())]


def build_report(snapshot, *, cohort_id=None):
    targets = {t['id']: t for t in snapshot.get('targets', [])}
    if cohort_id is not None and cohort_id not in {c['id'] for c in snapshot.get('cohorts', [])}:
        raise ValueError('Unknown requested cohort')
    rows = []
    for run in snapshot.get('runs', []):
        if run.get('scope') != 'prospective_challenge' or (cohort_id and run.get('cohort_id') != cohort_id):
            continue
        target = targets.get(run['target_id'], {})
        agent_stages = _agent_stage_observations(run)
        engineering = _engineering(run, agent_stages)
        exclusion = _exclusion(run, target, engineering)
        rows.append({'run': {**run, 'project_id': target.get('project_id')}, 'engineering': engineering,
                     'agent_stages': agent_stages,
                     'exclusion': exclusion, 'loss': _loss(run,target) if exclusion is None else None})
    scored = [r['loss'] for r in rows if r['loss'] is not None]
    engineering_rows = [{'run_id': r['run']['id'], 'target_id': r['run']['target_id'],
        'status': r['run']['status'], 'categories': r['engineering'],
        **({'agent_stages': r['agent_stages']} if r['agent_stages'] else {})}
        for r in rows if r['engineering']]
    categories = sorted({c for r in engineering_rows for c in r['categories']})
    proposals = []
    for category in categories:
        evidence = [r['run_id'] for r in engineering_rows if category in r['categories']]
        proposals.append({'id': 'engineering-' + category, 'kind': 'engineering_review',
            'finding_category': category, 'supporting_run_ids': evidence,
            'status': 'pending_human_review', 'candidate_version': None, 'applied': False, 'training_performed': False,
            'proposed_change': ISSUES[category], 'predictive_improvement_claim': False})
    mistakes = [r for r in scored if not r['correct_at_threshold_0_5']]
    if mistakes:
        proposals.append({'id': 'outcome-error-review', 'kind': 'evidence_hypothesis_review',
            'status': 'pending_human_review', 'candidate_version': None, 'applied': False, 'training_performed': False,
            'supporting_run_ids': [r['run_id'] for r in mistakes],
            'independent_projects': len({r['project_id'] for r in mistakes}),
            'proposed_change': '人工逐项复核冻结输入、端点映射、间接证据和未知参数；误差类别不说明原因。任何新提示词或证据策略仅作为待检验假设。',
            'predictive_improvement_claim': False})
    summary = _summary(rows)
    # Global mixing of models, versions or conditions is not a comparison score.
    for key in ('brier','log_loss','accuracy','project_equal_weight_brier'):
        summary[key] = None
    summary['pooled_metric_policy'] = 'Use separated groups below; no global score across participants or conditions.'
    body = {'schema_version': VERSION, 'generated_at_utc': datetime.now(timezone.utc).isoformat(),
        'cohort_id': cohort_id, 'ledger_sha256': snapshot.get('ledger_sha256'),
        'ledger_event_count': len(snapshot.get('events', [])),
        'mode': 'offline_read_only_review', 'status': 'adjudicated_review_available' if scored else 'no_eligible_adjudicated_predictions',
        'provider_calls': 0, 'ledger_writes': 0, 'training_performed': False, 'automatic_deployment': False,
        'summary': summary,
        'model_groups': _groups(rows, MODEL_FIELDS),
        'workflow_groups': _groups(rows, ('cohort_id','track','condition','workflow_version','execution_budget_sha256')),
        'project_groups': _groups(rows, ('cohort_id','track','condition','project_id')),
        'group_interpretation': 'Workflow/project groups describe pooled records, not causal workflow comparisons. Model groups retain requested and returned versions. Correlated forecasts are not independent trials.',
        'outcome_losses': scored, 'engineering_observations': engineering_rows,
        'excluded_from_loss': [{'run_id':r['run']['id'],'reason':r['exclusion']} for r in rows if r['exclusion']],
        'version_proposals': proposals,
        'next_version_gates': [
            'Human review approves an explicit hypothesis, candidate version, resource budget, baseline and stop rule.',
            'Offline evaluation splits by independent drug project and time; diagnostic data are not held-out confirmation data.',
            'Shadow execution uses a new version and new run IDs, explicitly outside the original official comparison.',
            'Only a newly preregistered prospective cohort can test improvement; freeze inputs, prompts, versions and budgets again.',
            'Old runs and failures remain immutable; no automatic model, prompt, weight or production deployment.'
        ],
        'proposal_required_review_fields': ['hypothesis', 'candidate_version', 'baseline',
            'resource_budget', 'independent_holdout_projects', 'stop_rule', 'reviewer'],
        'limitations': [
            'No adjudicated eligible outcome means no accuracy, loss, calibration or predictive optimization conclusion.',
            'A wrong direction is a descriptive label, not a biological mechanism or causal error diagnosis.',
            'Coverage includes failures and abstentions. Brier/log loss never impute a probability for either.',
            'This report verifies ledger hashes but does not fetch sources or independently revalidate TSA cryptography.',
            'Server-only timestamps remain explicitly distinct from external TSA evidence; pretrained outcome contamination cannot be excluded.'
        ], 'clinical_prediction_validated': False}
    body['report_sha256'] = sha(body)
    return body


def write_report(report, output_path):
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # New report per export; never replace a ledger, prior report or symlink.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        stream.write(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    return path.resolve()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ledger', required=True, help='Existing arena.sqlite3; opened with SQLite mode=ro')
    parser.add_argument('--cohort')
    parser.add_argument('--output', required=True, help='New JSON file; existing files are never overwritten')
    args = parser.parse_args()
    report = build_report(read_snapshot(args.ledger), cohort_id=args.cohort)
    output = write_report(report, args.output)
    print(json.dumps({'output': str(output), 'status': report['status'],
        'assigned': report['summary']['assigned'], 'scored_forecasts': report['summary']['scored_forecasts'],
        'provider_calls': 0, 'ledger_writes': 0, 'report_sha256': report['report_sha256']}))


if __name__ == '__main__':
    main()
