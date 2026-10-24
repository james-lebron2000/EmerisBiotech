"""Prepare a new operational cohort after the frozen 1.0 adapter failures.

No API invocation, legacy rewrite, retrospective outcome input or best-of choice.
Run only after a fresh, saved sponsor/registry readout screen.
"""
from copy import deepcopy
from datetime import datetime,timezone,timedelta
import hashlib,json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from emeris_live.arena_store import validate_target,validate_packet
from emeris_live.arena_agents import make_agent_spec
ROOT=Path(__file__).resolve().parents[1]


def prepare(root=ROOT):
    now=datetime.now(timezone.utc)
    screen_path=root/'arena_data/source_snapshots/avanzar-v2-readout-screen.web.json'
    screen=json.loads(screen_path.read_text());cutoff=screen['information_cutoff_utc']
    meta=json.loads((root/'arena_data/source_snapshots/avanzar-v2-registry-receipt.json').read_text())
    raw=(root/meta['path']).read_bytes();registry=json.loads(raw)
    if hashlib.sha256(raw).hexdigest()!=meta['sha256'] or registry.get('hasResults') is not False:
        raise ValueError('Registry integrity or unknown-result gate failed')
    prior=json.loads((root/'arena_data/ablation_spec.json').read_text())
    target=deepcopy(prior['targets'][0]);target.update(id='of-workflow-20261023-avanzar-pfs',
        trial_name='AVANZAR · v2 工作流与专利对照',information_cutoff_utc=cutoff,data_cutoff=cutoff,
        submission_deadline_utc=(now+timedelta(hours=4)).isoformat())
    if registry['protocolSection']['outcomesModule']['primaryOutcomes'][0]['measure']!=target['endpoint']:
        raise ValueError('Registered endpoint changed; re-review rather than guessing')
    target['readout_screen'].update(checked_at_utc=now.isoformat(),
        finding='Repeated bounded sponsor-domain English/Chinese queries and current registry found no decisive result for this exact AVANZAR PFS hypothesis. No global absence guarantee.',
        query_snapshot_sha256=hashlib.sha256(screen_path.read_bytes()).hexdigest(),
        query_snapshot_path=screen_path.relative_to(root).as_posix())
    target['readout_screen']['sources'][0].update(sha256=meta['sha256'],retrieved_at=meta['retrieved_at'])
    base=deepcopy(prior['slots'][0]['packet']);base['data_cutoff']=cutoff
    base['evidence'][0].update(source_sha256=meta['sha256'],retrieved_at=meta['retrieved_at'],snapshot_kind='raw_registry_json')
    combo=json.loads((root/'arena_data/source_snapshots/avanzar-tropion-lung04-receipt.json').read_text())
    if hashlib.sha256((root/combo['path']).read_bytes()).hexdigest()!=combo['sha256']:
        raise ValueError('Combination source snapshot changed')
    base['evidence'].append({'id':'tropion-lung04-firstline-triplet-2023','kind':'clinical_prior',
        'title':'TROPION-Lung04 一线联合早期观察',
        'text':'Sponsor-reported phase Ib TROPION-Lung04, 2023-03-06 cutoff: among previously untreated advanced NSCLC, Dato-DXd + durvalumab + carboplatin triplet had ORR 10/13=76.9% (95% CI 46.2–95.0); Dato-DXd + durvalumab doublet had 7/14=50.0% (23.0–77.0). ORR includes confirmed and pending responses. Single-arm small cohorts, no concurrent randomized comparator; no isolated TROP2-positive PFS treatment effect. These observations cannot establish superiority over pembrolizumab chemotherapy. Snapshot is a web-tool extraction of the official 2023-09-10 page because direct HTML download returned 403; SHA binds the extracted snapshot, not original HTML. Zero independent human scientific reviews.',
        'source_url':combo['source_url'],'source_sha256':combo['sha256'],
        'available_at':combo['retrieved_at'],'retrieved_at':combo['retrieved_at'],
        'source_publication_date':combo['publication_date'],'snapshot_kind':combo['snapshot_kind'],
        'original_document_sha256':None})
    treated=deepcopy(base)
    patent=next(e for slot in prior['slots'] if slot['condition']=='with_patents' for e in slot['packet']['evidence'] if e['kind']=='patent')
    treated['evidence'].append(deepcopy(patent))
    target['evidence_ids']=[e['id'] for e in treated['evidence']]
    budget={'budget_policy':'token_only_unpriced','max_requests':2,'max_output_tokens':8192,
        'max_input_chars':8500,'timeout_seconds':120,'max_total_output_tokens':16384,
        'max_total_timeout_seconds':240}
    slots=[]
    for model in ('kimi-k3','deepseek-v4.1-flash'):
        for track,condition,packet in (('same_evidence','clinical_only',base),('patent_ablation','with_patents',treated),('shared_agent','clinical_only',base)):
            slots.append({'target_id':target['id'],'model_id':model,'track':track,'condition':condition,'packet':deepcopy(packet)})
    for name,reviewer,forecaster in (('agent-kimi-deepseek','kimi-k3','deepseek-v4.1-flash'),('agent-deepseek-kimi','deepseek-v4.1-flash','kimi-k3')):
        slots.append({'target_id':target['id'],'model_id':name,'track':'open_agent','condition':'clinical_only',
            'packet':deepcopy(base),'agent_spec':make_agent_spec(name,reviewer,forecaster,tier='two-call-8192-v2')})
    protocol={'version':'workflow-pilot-20261023-v2','purpose':'New operational cohort after documented truncated review failures; one repeated asset, no new independent clinical replication.',
        'execution_budget':budget,'maximum_slots':8,'maximum_provider_requests':12,
        'selection_policy':'Same two model families as the prior patent/workflow pilot; no outcome-based selection.',
        'stage_policy':'At most 8192 output tokens and 120 seconds per provider call. Single-pass uses one call; shared and cross-model systems use at most two. Actual usage shown separately; no claim of equal consumed compute.',
        'primary_selection':'First attempt in each registered slot; all legacy results, failures and refusals remain; no replacements.',
        'data_changes':'Adds publicly sourced first-line phase Ib combination evidence, marked as nonrandomized ORR; never changes old packets.',
        'source_integrity':'Web-extracted snapshots and original HTTP documents have explicit different hash bases.',
        'agent_scope':'Deterministic lookup of registered evidence, one structured review then one forecast; no arbitrary network or code tools.',
        'pricing_status':'Token-bounded and unpriced. Actual money cost unknown until rates/invoice independently verified.',
        'timestamp_policy':'Registration TSA before any call, prediction TSA after every slot has a terminal record.',
        'evaluation':'Same-evidence, shared-agent, open-agent and patent tracks separate; probability change before actual adjudication is not improvement.'}
    validate_target(target)
    for slot in slots: validate_packet(slot['packet'],target,slot['condition'])
    return {'cohort_id':'of-workflow-20261023-v2','targets':[target],'slots':slots,'protocol':protocol},budget


def main():
    spec,budget=prepare()
    for name,obj in (('workflow_v2_spec.json',spec),('workflow_v2_budget.json',budget)):
        path=ROOT/'arena_data'/name
        with path.open('x') as f:json.dump(obj,f,ensure_ascii=False,indent=2);f.write('\n')
    print('Prepared 8 new operational slots, maximum 12 provider requests. No model called.')

if __name__=='__main__': main()
