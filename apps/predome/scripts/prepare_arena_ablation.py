"""Prepare the separate patent/workflow pilot; never rewrite the first cohort."""
from copy import deepcopy
from datetime import datetime,timezone,timedelta
import hashlib,json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from emeris_live.patent_evidence import freeze_evidence_pack
ROOT=Path(__file__).resolve().parents[1]

def main():
    now=datetime.now(timezone.utc);cutoff=(now-timedelta(seconds=30)).isoformat()
    prior=json.loads((ROOT/'arena_data/pilot_spec.json').read_text())
    t=deepcopy(prior['targets'][0]);t.update(id='of-ablation-20261022-avanzar-pfs',trial_name='AVANZAR · 专利与 Agent 对照',
        information_cutoff_utc=cutoff,data_cutoff=cutoff,submission_deadline_utc=(now+timedelta(hours=4)).isoformat())
    raw=Path('/tmp/predome-avanzar-ablation-screen.json').read_bytes();source=json.loads(raw)
    assert source['hasResults'] is False and source['protocolSection']['identificationModule']['nctId']=='NCT05687266'
    (ROOT/'arena_data/sources/NCT05687266-ablation-20261022.json').write_bytes(raw)
    t['readout_screen'].update(checked_at_utc=now.isoformat(),
        finding='2026-10-22 repeat bounded sponsor-domain English/Chinese query found TROPION-Breast02 and other-trial releases; no decisive AVANZAR primary result found. Registry hasResults=false. This does not prove the absence of unindexed or nonsearched disclosures.')
    t['readout_screen']['sources'][0].update(sha256=hashlib.sha256(raw).hexdigest(),retrieved_at=now.isoformat())
    base=deepcopy(prior['slots'][0]['packet']);base['data_cutoff']=cutoff
    base['evidence'][0]['source_sha256']=hashlib.sha256(raw).hexdigest()
    patent_pack=freeze_evidence_pack('Dato-DXd',cutoff)
    if patent_pack['status']!='ready':raise ValueError('Reviewed patent is not eligible at this cutoff')
    selected=patent_pack['items'][0];card=selected['experiment_cards'][0]
    patent={'id':card['evidence_id'],'kind':'patent','evidence_kind':'patent','title':card['title'],
      'publication_number':selected['publication_number'],'publication_date':selected['publication_date'],
      'available_at':selected['drug_mapping']['reviewed_at'],'source_url':card['pdf_url'],'source_sha256':selected['raw_sha256'],
      'page':93,'pdf_page_1_based':93,'example_id':'7','section_type':'reported_experiment',
      'mapping_status':'verified','review_status':'verified','review_basis':'two_agent_visual_source_check',
      'human_review_count':0,'review_receipt':card['review']['receipt_sha256'],'eligible_for_forecast':True,
      'text':'WO2020240467A1 Example 7, PDF page 93, Table 2 (data cutoff 2019-11-16): phase 1 open-label DS-1062a in previously treated NSCLC. Investigator-assessed UNCONFIRMED objective responses: 6 mg/kg 5/18 = 27.8% (exact 95% CI 9.7–53.5%); 8 mg/kg 13/34 = 38.2% (22.2–56.4%). 88/95 were response evaluable across ALL dose groups; 88 is not a denominator for either selected dose. No randomized concurrent comparator. This is ORR, not PFS, and cannot be substituted for AVANZAR benefit or compared causally with docetaxel. Patent PDF p87 [0419] identifies ADC(1)=DS-1062a; p88 cites NCT03401385. Official NCI and current registry identify DS-1062a/DS1062a as datopotamab deruxtecan; those identity sources were obtained 2026-10-22. Two AI roles checked pages/identity/numbers, share a base model, and have correlated error risk. Human scientific reviews=0; no clinical validation; full regulatory sequence alignment not performed. Only this selected experiment, not other examples or ADC(2), is included.'}
    with_patent=deepcopy(base);with_patent['evidence'].append(patent)
    t['evidence_ids']=[e['id'] for e in with_patent['evidence']];t['patent_ids']=[selected['publication_number']]
    budget={'budget_policy':'token_only_unpriced','max_requests':2,'max_output_tokens':4096,'max_input_chars':6000,'timeout_seconds':120}
    models=['kimi-k3','deepseek-v4.1-flash'];slots=[]
    for model in models:
      for track,condition,packet,workflow in [('same_evidence','clinical_only',base,'single_pass'),('patent_ablation','with_patents',with_patent,'single_pass'),('shared_agent','clinical_only',base,'evidence_review')]:
        slots.append({'target_id':t['id'],'model_id':model,'track':track,'condition':condition,
          'workflow_version':'arena-providers-1.0.0:'+workflow,'packet':deepcopy(packet)})
    protocol={'version':'ablation-pilot-20261022-v1','execution_budget':budget,'maximum_slots':6,'maximum_provider_requests':8,
      'purpose':'Separate preregistered operational patent and workflow pilot, one asset, not independent replication or a full season.',
      'selection_policy':'Kimi K3 and DeepSeek V4.1 Flash represent two families with valid structured responses in pilot. Engineering-compatibility selection, not outcome-based model superiority; all four initial records retained.',
      'primary_selection':'First attempt per slot. No retry or silent response replacement. Shared-agent track uses a review call followed by a forecast and is scored separately.',
      'patent_contrast':'Exact same clinical packet, prompt, requested model, configuration and budget within this cohort; only selected reviewed patent added.',
      'scientific_limit':'Two AI-role patent checks share a base model, zero independent human reviews; early unconfirmed ORR cannot be treated as phase 3 efficacy.',
      'pricing_status':'Unpriced token-bounded pilot; pricing page requires login; actual invoice amount unknown.',
      'independent_timestamp':'RFC3161 FreeTSA before calls and after outputs; trust pinned third-party CA, not legal notarization.',
      'evaluation':'Score only a matching future adjudicated primary endpoint; preserve all failures/abstentions and independent-asset count=1. No success-rate claim.'}
    spec={'cohort_id':'of-ablation-20261022-v1','targets':[t],'slots':slots,'protocol':protocol}
    for name,value in [('ablation_spec.json',spec),('ablation_budget.json',budget),('ablation_patent_packet.json',patent_pack)]:
      path=ROOT/'arena_data'/name
      if path.exists():raise ValueError('Prepared proposals are never overwritten')
      path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    print('Prepared six slots / at most eight requests; no model called.')

if __name__=='__main__':main()
