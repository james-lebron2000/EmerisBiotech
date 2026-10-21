from pathlib import Path
from datetime import datetime,timezone
import json,hashlib
from emeris_live.predome_statistics import calculate,sha
ROOT=Path(__file__).resolve().parent;D=ROOT/'predome_data'
def main():
 packet=json.loads((D/'evidence_packet_v1.json').read_text());spec=json.loads((D/'stat_spec_v1.json').read_text());receipt=json.loads((D/'selection_receipt.json').read_text())
 assert receipt['stat_spec_sha256']==sha(spec)
 code=(ROOT/'emeris_live/predome_statistics.py').read_bytes();selected=datetime.now(timezone.utc).isoformat()
 code_receipt={'selected_at_utc':selected,'statistical_code_sha256':hashlib.sha256(code).hexdigest(),'spec_sha256':sha(spec),'stage':'before_first_calculation'}
 with (D/'stat_code_selection.json').open('x') as f:json.dump(code_receipt,f,indent=2)
 payload=calculate(packet,spec)
 record={'id':'predome-topaz-20261016-v1','calculated_at_utc':datetime.now(timezone.utc).isoformat(),'recipe_selected_at_utc':spec['selected_at_utc'],'code_sha256':code_receipt['statistical_code_sha256'],'payload':payload,'clinical_prediction_validated':False,'independent_timestamp_verified':False}
 envelope={'record':record,'sha256':sha(record)}
 with (D/'runs/statistical-topaz-v1.json').open('x') as f:json.dump(envelope,f,ensure_ascii=False,indent=2,allow_nan=False)
 for x in payload['comparisons']:print(x['case_id'],round(x['central']['statistical_success_proxy_probability'],4),[round(n,4) for n in x['sensitivity_range_not_ci']])
if __name__=='__main__':main()
