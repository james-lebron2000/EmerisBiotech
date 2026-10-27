"""Build the first real, prospective four-model pilot from verified local sources.

Operator refreshes the targeted readout-screen record before registration.
No forecast is copied from historical model outputs into the new input packet.
"""
import json,hashlib
from pathlib import Path
from datetime import datetime,timezone,timedelta
ROOT=Path(__file__).resolve().parents[1]

def main():
    current=datetime.now(timezone.utc)
    source=ROOT/'arena_data/sources/NCT05687266-20261022.json'
    raw=source.read_bytes();registry=json.loads(raw);p=registry['protocolSection']
    if p['identificationModule']['nctId']!='NCT05687266' or registry.get('hasResults') is not False:
        raise ValueError('Pilot registry must match NCT05687266 without posted results')
    prior=json.loads((ROOT/'predome_data/continuous_v4/prospective/prior_effect_records.json').read_text())['records']
    prior=next(i for i in prior if i['record_id']=='tropion_lung01_nsq_pfs')
    archived=(ROOT/prior['source']['path']).read_bytes()
    if hashlib.sha256(archived).hexdigest()!=prior['source']['sha256']:
        raise ValueError('Pilot clinical prior source SHA-256 differs')
    cutoff=(current-timedelta(minutes=2)).isoformat()
    deadline=(current+timedelta(hours=3)).isoformat()
    target={'id':'of-pilot-20261022-avanzar-pfs','project_id':'datopotamab-deruxtecan','nct_id':'NCT05687266',
      'trial_name':'AVANZAR','drug':'datopotamab deruxtecan','sponsor':'AstraZeneca / Daiichi Sankyo','indication':'first-line advanced NSCLC',
      'endpoint':p['outcomesModule']['primaryOutcomes'][0]['measure'],
      'population':'Previously untreated advanced non-squamous NSCLC, TROP2 biomarker positive, without actionable genomic alterations.',
      'comparison':'Dato-DXd 6 mg/kg + durvalumab + carboplatin versus pembrolizumab + histology-specific platinum chemotherapy.',
      'success_definition':'First official decisive primary analysis reports a favorable statistically qualifying PFS result by BICR in the non-squamous TROP2 biomarker-positive population under the prespecified SAP. Other populations, OS, unadjusted nominal p-values, commercial decisions and delayed readouts do not resolve this target. If multiplicity-adjusted qualification is unclear, adjudication remains unresolved.',
      'analysis_timepoint':'First officially reported decisive analysis of the specified primary PFS hypothesis; registry timeframe approximately 3 years.',
      'information_cutoff_utc':cutoff,'data_cutoff':cutoff,'submission_deadline_utc':deadline,'scope':'prospective_challenge',
      'readout':{'label':'H2 2026 公司指引；待更新核查','start':'2026-07-01','end':'2026-12-31','precision':'half_year',
       'source_url':'https://www.astrazeneca.com/content/dam/az/PDF/2026/h1q2/H1-and-Q2-2026-results-clinical-trials-appendix.pdf',
       'source_title':'AstraZeneca H1 2026 Clinical Trials Appendix','guidance_as_of':'2026-07-27','requires_refresh':True},
      'status_note':'2026-10-22限定官方中英文检索未发现本目标决定性结果；不能排除未索引公告。注册记录预计主要完成时间2027-11，不能用其替换公司读出窗口。',
      'readout_screen':{'status':'no_decisive_public_result_found','checked_at_utc':current.isoformat(),
        'method':'Registry hasResults check plus domain-limited English and Chinese search of sponsor official sources.',
        'queries':['AVANZAR datopotamab trial results 2026','AVANZAR 研究 结果 2026'],
        'domains':['astrazeneca.com','daiichisankyo.com','clinicaltrials.gov','astrazeneca.com.cn','daiichisankyo.com.cn'],
        'finding':'Retrieved results concerned TROPION-Breast02, TROPION-Lung01 or other trials, not the AVANZAR target.',
        'sources':[{'url':'https://clinicaltrials.gov/api/v2/studies/NCT05687266','sha256':hashlib.sha256(raw).hexdigest(),'retrieved_at':'2026-10-22T15:34:00.360843+00:00'},
          {'url':'https://www.astrazeneca.com/content/dam/az/PDF/2026/h1q2/H1-and-Q2-2026-results-clinical-trials-appendix.pdf','role':'earlier company timing guidance, not negative result proof'}]},
      'evidence_ids':['registry-NCT05687266','tropion_lung01_nsq_pfs'],'patent_ids':[]}
    evidence=[{'id':'registry-NCT05687266','kind':'trial_registry','title':'AVANZAR registered design',
      'text':'Randomized open-label phase III AVANZAR, total enrollment 1350. There are four primary hypotheses: PFS and OS in non-squamous TROP2-positive and overall non-squamous populations. This target is only the TROP2-positive non-squamous PFS hypothesis. Alpha allocation, event counts, biomarker prevalence and missing-data estimand are not supplied. Registry estimated primary completion 2027-11 does not establish readout timing.',
      'source_url':'https://clinicaltrials.gov/api/v2/studies/NCT05687266','source_sha256':hashlib.sha256(raw).hexdigest(),
      'available_at':'2026-10-09T23:59:59+00:00'},
      {'id':'tropion_lung01_nsq_pfs','kind':'clinical_prior','title':'TROPION-Lung01 indirect randomized evidence',
      'text':'In previously treated non-squamous NSCLC, Dato-DXd monotherapy versus docetaxel showed PFS HR 0.63 (95% CI 0.51–0.79), subgroup n=229 versus 232. This is indirect evidence: later-line versus first-line, monotherapy versus triple combination, different comparator, and no isolation of the target TROP2 biomarker-positive population. It is not an AVANZAR treatment effect. Source was previously archived as a primary-reader excerpt, not a full paper download; no independent human review.',
      'source_url':prior['source']['url'],'source_sha256':prior['source']['sha256'],'available_at':'2024-09-09T23:59:59+00:00'}]
    packet={'target':{k:target[k] for k in ('endpoint','population','comparison','success_definition')},'data_cutoff':cutoff,'evidence':evidence}
    budget={'budget_policy':'token_only_unpriced','max_requests':1,'max_output_tokens':4096,'max_input_chars':6000,'timeout_seconds':120}
    models=['glm-5.2','kimi-k3','deepseek-v4.1-flash','deepseek-v4-pro']
    protocol={'version':'pilot-20261022-v1','purpose':'First prospective operational pilot; full 12-project season remains to be enrolled',
      'execution_budget':budget,'maximum_slots':4,'pricing_status':'Unverified: provider pricing page requires account login. No monetary total is claimed.',
      'primary_selection':'First run of each registered slot; failures retained, no replacement or semantic retry.',
      'comparison':'Same evidence and instructions; four gateway-reported models from three model families.',
      'metrics':'Brier/log loss after exact target outcome adjudication; coverage includes all four assigned slots; historical replay excluded.',
      'independent_timestamp':'Server append-only hash chain; external public attestation still pending.'}
    spec={'cohort_id':'of-pilot-20261022-v1','targets':[target],
       'slots':[{'target_id':target['id'],'model_id':m,'track':'same_evidence','condition':'clinical_only','workflow_version':'arena-providers-1.0.0:single_pass','packet':packet} for m in models], 'protocol':protocol}
    out=ROOT/'arena_data';out.mkdir(exist_ok=True)
    for name,value in [('pilot_spec.json',spec),('pilot_budget.json',budget)]:
      path=out/name
      if path.exists():raise ValueError('Pilot spec exists; frozen proposals are not overwritten automatically')
      path.write_text(json.dumps(value,ensure_ascii=False,indent=2))
    print('Prepared four-slot prospective pilot. No provider request made.')
if __name__=='__main__':main()
