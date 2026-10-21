"""Operator-only actual DeepSeek replay, source-constrained claims and frozen raw outputs."""
from pathlib import Path
from datetime import datetime, timezone
import os, urllib.request, time
from predome_agents import Budget
from predome_pilot import ROOT
import json
import predome_pilot_llm as base
from predome_pilot import sha,write_new
SYSTEM='''You forecast declared primary-analysis success using ONLY the supplied anonymized design and prior-RCT facts. Never infer identity or use remembered historical outcomes. This is a historical development replay and pretrained memory cannot be excluded. Prior tier B means indirect evidence: do NOT call it target efficacy, target standard error, statistical power or calibrated success probability. A confidence interval crossing the null is not proof of zero effect. Missing prior evidence is unknown, not failed efficacy. Exact target alpha/precision/SAP are unavailable. No browsing/tools, no external sources, no peer predictions. Distinguish clinical effects from statistical significance. May abstain. Return JSON only {"forecasts":[{"case_id":"case-000","status":"predicted|abstain","probability":0.5 or null,"reasoning":"Chinese rationale","evidence_ids":["design" or supplied prior evidence_id],"claims":[{"claim":"Chinese factual statement limited to input","evidence_id":"design or supplied prior id"}],"assumptions":["explicit uncertain extrapolation"],"key_unknowns":["unknown"]}]}. Every case exactly once. Do not invent numeric facts. Cite only that case's supplied evidence. Probabilities are subjective uncalibrated research estimates, not validated clinical forecasts.'''

def validate(raw,packet):
    rows=base_validate(raw,packet)
    for r in rows:
        case=next(c for c in packet['cases'] if c['case_id']==r['case_id'])
        allowed={'design'}
        if case['prior_evidence']:allowed.add(case['prior_evidence']['evidence_id'])
        if not isinstance(r.get('evidence_ids'),list) or any(e not in allowed for e in r['evidence_ids']):raise ValueError('Invalid evidence citation')
        if r['status']=='predicted' and not r['evidence_ids']:raise ValueError('Unreferenced forecast')
        for k in ['claims','assumptions','key_unknowns']:
            if not isinstance(r.get(k),list):raise ValueError('Missing claims/assumptions/unknowns')
        for c in r['claims']:
            if c.get('evidence_id') not in allowed or not isinstance(c.get('claim'),str):raise ValueError('Unsupported claim reference')
    return rows
base_validate=base.validate

def call(model, packet, path):
    if model not in ['deepseek-flash', 'deepseek-v4-pro']: raise ValueError('Unapproved model')
    if Path(path).exists(): raise ValueError('Never overwrite an API record')
    key = os.environ.get('DEEPSEEK_API_KEY')
    if not key: raise ValueError('No key configured; no API call made')
    Budget(ROOT / 'predome_data/operator-api-budget.json').reserve(model)
    body = {'model': model, 'messages': [{'role': 'system', 'content': SYSTEM}, {'role': 'user', 'content': json.dumps(packet, ensure_ascii=False)}], 'temperature': 0, 'max_tokens': 7000, 'response_format': {'type': 'json_object'}, 'thinking': {'type': 'disabled'}, 'stream': False}
    record = {'model_requested': model, 'started_at_utc': datetime.now(timezone.utc).isoformat(), 'input_sha256': sha(packet), 'request_sha256': sha(body), 'system_prompt_sha256': sha(SYSTEM), 'labels_visible': False, 'peer_predictions_visible': False, 'identities_visible': False, 'pretraining_contamination_excluded': False, 'scope': 'retrospective_anonymized_design_plus_indirect_prior_RCT_replay', 'clinical_prediction_validated': False}
    request = urllib.request.Request('https://api.deepseek.com/chat/completions', data=json.dumps(body).encode(), headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
    start = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=180) as response: raw = json.load(response)
        choice = raw['choices'][0]
        if choice.get('finish_reason') != 'stop': raise ValueError('Incomplete response')
        rows = validate(json.loads(choice['message']['content']), packet)
        usage = raw.get('usage', {})
        ip, op = (.30, 1.20) if model == 'deepseek-flash' else (1.32, 3.96)
        record.update(status='completed', model_returned=raw.get('model'), request_id=raw.get('id'), usage=usage, forecasts=rows, cost_upper_estimate_usd=(usage.get('prompt_tokens', 0) * ip + usage.get('completion_tokens', 0) * op) / 1e6, cost_basis='2026-10-16 peak cache-miss tariff snapshot; estimate not invoice')
    except Exception as exc:
        record.update(status='failed', error_type=type(exc).__name__, http_status=getattr(exc, 'code', None), forecasts=[])
    record.update(completed_at_utc=datetime.now(timezone.utc).isoformat(), duration_seconds=time.monotonic()-start)
    write_new(path, {'record': record, 'sha256': sha(record)})
    return record


def main(run,data):
    packet=json.loads((run/'llm_inputs.json').read_text());report=json.loads((run/'report.json').read_text());calls=[]
    for model in ['deepseek-flash','deepseek-v4-pro']:
        record=call(model,packet,run/(model+'.json'));calls.append(record)
        print(json.dumps({k:record.get(k) for k in ['model_requested','status','usage','cost_upper_estimate_usd']},ensure_ascii=False),flush=True)
    comparison=base.compare(report,calls,json.loads(data.read_text()))
    comparison.update(seen_replay=True,new_independent_test=False,input_scope='anonymized_design_plus_indirect_prior_RCT',training_rows_visible=False)
    write_new(run/'comparison.json',comparison)
if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--run',type=Path,required=True);p.add_argument('--data',type=Path,required=True);a=p.parse_args();main(a.run,a.data)
