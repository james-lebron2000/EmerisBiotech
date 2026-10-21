from pathlib import Path
import json,hashlib
from .predome_statistics import sha
ROOT=Path(__file__).resolve().parents[1];D=ROOT/'predome_data'
def read_release():
 envelope=json.loads((D/'release_v1.json').read_text());record=envelope['record']
 if sha(record)!=envelope['sha256']:raise ValueError('Predome release hash mismatch')
 for item in [record['statistical_record']]+record['llm_records']:
  if sha(item['record'])!=item['sha256']:raise ValueError('Predome run hash mismatch')
 if sha(json.loads((D/'evidence_packet_v1.json').read_text()))!=record['evidence_packet_sha256']:raise ValueError('Predome evidence hash mismatch')
 stat=record['statistical_record']['record']
 if stat['code_sha256']!=hashlib.sha256((ROOT/'emeris_live/predome_statistics.py').read_bytes()).hexdigest():raise ValueError('Frozen statistical code mismatch')
 if stat['payload']['spec_sha256']!=sha(json.loads((D/'stat_spec_v1.json').read_text())):raise ValueError('Frozen recipe mismatch')
 return envelope|{'integrity_verified':True}
