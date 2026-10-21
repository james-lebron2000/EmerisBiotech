import json,tempfile
from pathlib import Path
from datetime import datetime,timezone
from emeris_live import platform_store,product
from emeris_live.store import Store
from emeris_live.analytics import build_analytics
platform_store.now=lambda:'2026-10-21T00:00:00+00:00'
store=Store(Path(tempfile.mkdtemp())/'db')
platform_store.import_legacy_archive(store)
ledger=platform_store.PlatformStore(store)
base=product.build_product(store)
arch=next(x for x in ledger.list_public_snapshots() if x['record']['archive_provenance']['case_id']=='forward-002')
run=ledger.create_run('SYNTHETIC PRIVATE OWNER',{'nct_id':'NCT01234567','drug':'SECRET INPUT','protocol_text':'SECRET PROTOCOL'},{'forecast_kind':'uncalibrated_llm_research','forecasts':{'test-model':{'status':'predicted','probability':.73,'reasoning':'SECRET REASONING'}}},'test-v1','test-pipeline','2026-10-19',target={'endpoint':'Synthetic primary endpoint','population':'ITT test population','comparison':'Synthetic drug versus placebo','success_definition':'Prespecified superiority'},evidence=[{'url':'https://example.org/prior','availability_date':'2026-10-18'}],generated_at='2026-10-20')
user=ledger.lock('SYNTHETIC PRIVATE OWNER',run['id'],publish=True)
for snapshot in (arch,user):
 r=snapshot['record'];t=r['target']
 item=ledger.submit_outcome('SYNTHETIC SUBMITTER',r['snapshot_id'],{'trial_id':r['trial_id'],'endpoint':t['endpoint'],'population':t['population'],'comparison':t['comparison'],'label':1,'readout_date':'2026-10-20','source':{'url':'https://example.org/synthetic-result','published_at':'2026-10-20','sha256':'a'*64},'effect_size':{'measure':'HR','value':.7},'confidence_interval':{'level':.9,'lower':.6,'upper':.9},'p_value':.01,'extraction_status':'complete','conflicts':[]},allow_other_owner=True)
 ledger.verify_outcome('SYNTHETIC REVIEWER',item['id'],'approved','SECRET REVIEWER REASON',dict.fromkeys(('endpoint_match','population_match','comparison_match','source_checked','prespecified_analysis','earliest_result_checked','evidence_cutoff_checked'),True))
root=Path('/tmp/predome-public-ui');root.mkdir(exist_ok=True)
(root/'data.json').write_text(json.dumps(product.build_product(store)))
(root/'empty.json').write_text(json.dumps(base))
for nct in ('NCT01234567',arch['record']['trial_id'],base['historical'][0]['nct_id']):
 (root/(nct+'.json')).write_text(json.dumps(product.trial_detail(nct,store)))
(root/'analytics.json').write_text(json.dumps(build_analytics(prospective_records=ledger.evaluation_rows())))
print(arch['record']['trial_id'],base['historical'][0]['nct_id'])
