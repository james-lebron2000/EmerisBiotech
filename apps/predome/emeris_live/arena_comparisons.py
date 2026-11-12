"""Read-only comparison bindings over verified native Arena records.

These helpers return internal record references for the HTTP projection to
allowlist. They do not publish records, score predictions, change the ledger or
declare scientific validity. Unknown metadata never establishes a fair pair.
"""
import hashlib
import json
import math
import re
from .arena_public_contract import credential_like

SCHEMA_VERSION = 'predome-public-trial-comparisons-1'
HASH = re.compile(r'[a-f0-9]{64}')
MODEL = re.compile(r'[A-Za-z0-9][A-Za-z0-9._/-]{0,199}')
PUBLIC_POLICY = {
    'probability': None, 'confidence': None,
    'clinical_prediction_validated': False,
    'interpretation': ('These are recorded judgments, not evidence of improvement. '
        'Patent pairs require the same registered conditions and unchanged clinical input. '
        'Agent rows describe whole systems; shared budget caps do not mean equal actual compute. '
        'Calibration and underlying model weights have not been independently established. '
        'These comparisons do not contribute to the clinical-only consensus or a ranking. '
        'Individual match metadata follows the existing independent outcome and timing gates; '
        'it is not evidence of workflow or patent benefit.'),
}


def _known_hash(value):
    return isinstance(value, str) and HASH.fullmatch(value) is not None


def _packet_verified(run):
    packet = run.get('packet')
    if not isinstance(packet, dict) or not isinstance(packet.get('evidence'), list):
        return False
    try:
        raw = json.dumps(packet, ensure_ascii=False, sort_keys=True,
                         separators=(',', ':'), allow_nan=False).encode()
    except (ValueError, TypeError):
        return False
    return _known_hash(run.get('input_sha256')) and hashlib.sha256(raw).hexdigest() == run['input_sha256']


def _blocked(run):
    return any(isinstance(item, dict) and item.get('severity') == 'blocking'
               for item in run.get('input_evidence_issues', []))


def _usable(run):
    if _blocked(run):
        return False
    if run.get('status') == 'abstained':
        return run.get('probability') is None
    value = run.get('probability')
    return (run.get('status') == 'predicted' and type(value) in (int, float)
            and math.isfinite(value) and 0 <= value <= 1)


def _judgment(run):
    value = run.get('probability')
    if run.get('status') != 'predicted' or value == .5:
        return 'Uncertain'
    return 'Success' if value > .5 else 'Failure'


def _known_version(value):
    return isinstance(value, str) and 0 < len(value) <= 200 and bool(value.strip())


def _registered_config(run):
    snapshot = run.get('provider_config_snapshot')
    value = snapshot.get('config_sha256') if isinstance(snapshot, dict) else None
    return value if _known_hash(value) else None


def patent_comparisons(runs):
    """Retain every assigned patent arm, including missing/failed controls.

    Matching is confined to one exact target, cohort and requested model. A
    nearby NCT, later revision, different budget or unknown version is not a
    substitute. Packet hashes differ because the patent arm adds evidence.
    """
    result = []
    for treated in runs:
        if treated.get('track') != 'patent_ablation' or treated.get('condition') != 'with_patents':
            continue
        candidates = [run for run in runs
            if run.get('track') in ('same_evidence', 'patent_ablation')
            and run.get('condition') == 'clinical_only'
            and all(run.get(key) == treated.get(key) for key in
                    ('cohort_id', 'target_id', 'model_id', 'requested_model'))]
        control = candidates[0] if len(candidates) == 1 else None
        reasons = []
        registration = False
        version = None
        if control is None:
            reasons.append('missing_control' if not candidates else 'ambiguous_control')
        else:
            keys = ('execution_budget_sha256', 'expected_prompt_sha256')
            metadata = (all(_known_hash(control.get(k)) and control[k] == treated.get(k) for k in keys)
                        and _registered_config(control) is not None
                        and _registered_config(control) == _registered_config(treated)
                        and all(_known_hash(run.get('expected_provider_input_sha256')) for run in (control, treated))
                        and _known_version(control.get('workflow_version'))
                        and control['workflow_version'] == treated.get('workflow_version')
                        and all(_known_version(control.get(k)) for k in
                                ('cohort_id', 'target_id', 'model_id', 'requested_model')))
            if not metadata:
                reasons.append('registration_metadata_unverified')
            base, added = control.get('packet') or {}, treated.get('packet') or {}
            evidence = added.get('evidence', [])
            patent_rows = [item for item in evidence if isinstance(item, dict) and item.get('kind') == 'patent']
            stripped = {**added, 'evidence': [item for item in evidence
                        if not isinstance(item, dict) or item.get('kind') != 'patent']}
            packet_match = (_packet_verified(control) and _packet_verified(treated)
                and not any(isinstance(item, dict) and item.get('kind') == 'patent'
                            for item in base.get('evidence', [])) and base == stripped)
            if not packet_match:
                reasons.append('clinical_packet_changed')
            if not patent_rows:
                reasons.append('no_added_patents')
            registration = metadata and packet_match and bool(patent_rows)
            a, b = control.get('returned_model'), treated.get('returned_model')
            if not _known_version(a) or not _known_version(b):
                reasons.append('model_version_unconfirmed')
            else:
                version = a == b
                if not version:
                    reasons.append('model_version_changed')
            if _blocked(control) or _blocked(treated):
                reasons.append('blocked_input')
            usable = _usable(control) and _usable(treated)
            if not usable:
                reasons.append('incomplete_judgments')
            else:
                if not all(_known_hash(run.get('prompt_sha256'))
                           and run['prompt_sha256'] == run.get('expected_prompt_sha256')
                           for run in (control, treated)):
                    reasons.append('actual_prompt_unverified')
                if not all(_registered_config(run) is not None
                           and run.get('config_sha256') == _registered_config(run)
                           for run in (control, treated)):
                    reasons.append('actual_config_unverified')
                if not all(_known_hash(run.get('expected_provider_input_sha256'))
                           and run.get('provider_input_sha256') == run['expected_provider_input_sha256']
                           for run in (control, treated)):
                    reasons.append('actual_provider_input_unverified')
        complete = registration and version is True and not reasons
        status = ('comparable_judgments' if complete else
                  'incomplete_judgments' if registration and 'incomplete_judgments' in reasons
                  else 'not_comparable')
        result.append({'control': control, 'with_patents': treated,
            'registration_matched': registration, 'model_version_matched': version,
            'comparison_status': status,
            'judgment_changed': _judgment(control) != _judgment(treated) if complete else None,
            'reasons': reasons})
    return result


def _component_models(run):
    """Declared model names only; no provider/config/receipt or hidden review."""
    contract = run.get('agent_contract') or {}
    components = contract.get('components', []) if isinstance(contract, dict) else []
    if not isinstance(components, list):
        return []
    result = []
    for role in ('evidence_review', 'forecast'):
        own = [value for value in components if isinstance(value, dict) and value.get('role') == role]
        model = own[0].get('requested_model') if len(own) == 1 else None
        if isinstance(model, str) and MODEL.fullmatch(model) and not credential_like(model):
            result.append({'role': role, 'requested_model': model})
    return result


def agent_comparisons(runs):
    """Describe system records without claiming a workflow causal effect."""
    result = []
    for run in runs:
        if run.get('track') not in ('shared_agent', 'open_agent') or run.get('condition') != 'clinical_only':
            continue
        baseline = [other for other in runs if other.get('track') == 'same_evidence'
                    and other.get('condition') == 'clinical_only'
                    and all(other.get(k) == run.get(k) for k in ('target_id', 'cohort_id'))]
        reasons = []
        matched, cap = None, None
        if not baseline:
            reasons.append('missing_baseline')
        else:
            if _packet_verified(run) and all(_packet_verified(other) for other in baseline):
                matched = all(run['packet'] == other['packet'] for other in baseline)
                if not matched:
                    reasons.append('clinical_input_changed')
            else:
                reasons.append('clinical_input_unverified')
            if _known_hash(run.get('execution_budget_sha256')) and all(
                    _known_hash(other.get('execution_budget_sha256')) for other in baseline):
                cap = all(run['execution_budget_sha256'] == other['execution_budget_sha256'] for other in baseline)
                if not cap:
                    reasons.append('budget_cap_changed')
            else:
                reasons.append('budget_cap_unverified')
        if _blocked(run):
            reasons.append('blocked_input')
        if not _usable(run):
            reasons.append('incomplete_judgment')
        result.append({'run': run, 'workflow': run['track'],
            'baseline_run_ids': [item['id'] for item in baseline],
            'clinical_input_matched': matched, 'common_budget_cap': cap,
            'component_models': _component_models(run), 'reasons': reasons})
    return result
