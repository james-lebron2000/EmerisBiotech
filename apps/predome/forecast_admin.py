"""Offline operator CLI; the website never accepts public forecast mutations."""
import argparse
import hashlib
import json
from pathlib import Path
from emeris_live.forecast_model import build_forecast,adjudicate
from emeris_live.forecast_registry import freeze,read_registry

ROOT=Path(__file__).resolve().parent
def main():
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='command',required=True)
    f=commands.add_parser('freeze');f.add_argument('--id',required=True)
    commands.add_parser('verify')
    a=commands.add_parser('adjudicate');a.add_argument('--id',required=True);a.add_argument('--observation',type=Path,required=True);a.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();directory=ROOT/'trial_data/forecasts'
    if args.command=='freeze':
        catalog=json.loads((ROOT/'trial_data/candidates.json').read_text())
        spec=json.loads((ROOT/'trial_data/forecast_model_spec_v1.json').read_text())
        refresh=json.loads((ROOT/'trial_data/evidence/forecast_refresh_20261010.json').read_text())
        payload=build_forecast(catalog,spec)
        result=freeze(directory,args.id,payload,spec=spec,evidence_refresh=refresh,catalog=catalog,
                      model_code_sha256=hashlib.sha256((ROOT/'emeris_live/forecast_model.py').read_bytes()).hexdigest())
    elif args.command=='verify':
        result=read_registry(directory)
    else:
        records=read_registry(directory)['records']
        entry=next((r for r in records if r['record']['id']==args.id),None)
        if entry is None:raise ValueError('Unknown frozen forecast')
        receipt=json.loads((ROOT/'trial_data/publication_receipts'/(args.id+'.json')).read_text())
        if receipt['record_sha256']!=entry['record_sha256'] or receipt['publication_verified'] is not True:
            raise ValueError('Verified publication receipt required before prospective scoring')
        # Use public availability time, not an earlier local preparation time.
        result=adjudicate(entry['record']['forecast'],json.loads(args.observation.read_text()),
                          frozen_at_utc=receipt['published_at_utc'])
        result['frozen_record_sha256']=entry['record_sha256']
        result['publication_receipt']=receipt
        # Export only, never overwrite the frozen prediction or an earlier verdict.
        with args.output.open('x') as out:json.dump(result,out,ensure_ascii=False,indent=2)
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
