"""Capabilities are dispatched by the host, never granted by model text."""
from dataclasses import dataclass
from typing import Protocol
import json, os, urllib.request
from .models import ResearchPlan, DecisionRecord

ROLES={
 'orchestrator':{'tools':['create_plan','inspect_status'],'data':'dataset metadata only'},
 'geo_analyst':{'tools':['read_registered_data','execute_geo'],'data':'frozen run inputs only'},
 'scientific_reviewer':{'tools':['read_artifacts','review_evidence'],'data':'read-only artifacts and provenance'},
}

def require_tool(role,tool):
    if role not in ROLES or tool not in ROLES[role]['tools']: raise PermissionError(f'{role} cannot call {tool}')

class ModelProvider(Protocol):
    def complete(self, role: str, prompt: str) -> str: ...

@dataclass
class DisabledProvider:
    def complete(self,role,prompt): raise RuntimeError('No model configured; deterministic workflows remain available')

@dataclass
class OpenAICompatibleProvider:
    base_url: str
    model: str
    key_env: str = 'VBT_MODEL_API_KEY'
    timeout: int = 60
    def complete(self, role, prompt):
        if role not in ROLES: raise ValueError('Unknown role')
        if not self.base_url.startswith('https://'): raise ValueError('Model endpoint must use HTTPS')
        key=os.environ.get(self.key_env)
        if not key: raise RuntimeError(f'Model credential absent: {self.key_env}')
        # Only the supplied question/metadata are sent; never automatically upload datasets.
        body={'model':self.model,'messages':[{'role':'system','content':f'Role: {role}. Return a ResearchPlan JSON. No tools or code execution. Do not invent dataset keys. Never claim clinical validation.'},{'role':'user','content':prompt}]}
        req=urllib.request.Request(self.base_url.rstrip('/')+'/chat/completions',data=json.dumps(body).encode(),headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'})
        try:
            with urllib.request.urlopen(req,timeout=self.timeout) as resp: result=json.load(resp)
        except Exception as e:
            # Do not expose HTTP bodies, requests, or secrets through exception messages.
            raise RuntimeError('Model request failed: '+type(e).__name__) from None
        return result['choices'][0]['message']['content']

def draft_plan(provider,prompt):
    require_tool('orchestrator','create_plan')
    return ResearchPlan.model_validate_json(provider.complete('orchestrator',prompt))

def review(plan, execution, claims, errors):
    require_tool('scientific_reviewer','review_evidence')
    if execution!='succeeded' or errors or not claims:
        return DecisionRecord(decision='insufficient_evidence',reviewer='deterministic-reviewer-v1',reviewer_type='automated',reason='Missing/failed analysis or unresolved evidence. Human scientific review has not occurred.')
    return DecisionRecord(decision='revise',reviewer='deterministic-reviewer-v1',reviewer_type='automated',reason='Computational checklist completed. A human must review endpoint mapping, assumptions, source evidence and claim strength; statistical association is not causal or clinical validation.')


def scientific_checklist(plan, run_dir, execution, claims, errors):
    """Independent deterministic audit of emitted artifacts, not an LLM opinion."""
    from pathlib import Path
    from .io import load
    r=Path(run_dir);items=[]
    def item(topic,status,evidence,note):items.append({'topic':topic,'status':status,'evidence':evidence,'note':note})
    item('question_and_scope','recorded','inputs/plan.json',plan.question)
    item('input_provenance','needs_review' if errors else 'recorded','inputs/datasets.json','Versions and hashes are recorded; source content validity still needs domain review.')
    if plan.workflow=='geo_uc':
        rows=load(r/'work'/'results.json') if (r/'work'/'results.json').exists() else []
        flow=load(r/'work'/'sample_flow.json') if (r/'work'/'sample_flow.json').exists() else []
        item('patient_as_statistical_unit','checked' if flow and all(f['status']=='analyzed' for f in flow) else 'incomplete','work/sample_flow.json','Included baseline patient IDs are required and unique within each cohort; no cells/probes counted as patients.')
        item('endpoint_definition','needs_human_review','inputs/plan.json','Review deposited label semantics against each trial protocol. A nonempty source field is not protocol verification.')
        item('mapping_and_preprocessing','needs_human_review','inputs/datasets.json','Inspect sample exclusions, platform-specific gene annotations and deposited expression scale.')
        item('statistics','checked' if rows and all(x['status']=='estimated' for x in rows) else 'incomplete','work/results.json','Mann-Whitney U assesses distributions; bootstrap CI estimates median difference. They need not give identical significance decisions. BH family fixed in plan.')
        item('leakage','limited_design_check','inputs/plan.json','Only explicitly mapped baseline samples enter comparisons; no prediction model or train/test performance claim. Mapping provenance still requires review.')
        item('claim_strength','checked' if claims else 'incomplete','claims.json','Claims limited to observational association; no target causality, treatment efficacy or clinical release.')
    else:
        item('scientific_evidence','not_established','claims.json','Inventory/custom execution alone does not establish scientific evidence.')
    item('execution_failures','unresolved' if execution!='succeeded' else 'none_recorded','status.json','Errors and data gaps are retained; incomplete tests are never counted as negative outcomes.')
    item('human_signoff','pending','review.json','No human identity or approval is fabricated.')
    return items
