"""Run a bounded project research ticket; no hidden model/credential fallback."""
import argparse
import json
from pathlib import Path
from vbt.programs import Programs
from vbt.research_mission import create,run,stop,export


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--state-dir',required=True,type=Path);p.add_argument('--program',required=True)
    commands=p.add_subparsers(dest='command',required=True)
    a=commands.add_parser('create');a.add_argument('--mode',choices=['existing_evidence','model_research'],required=True)
    for name in ('trigger-id','goal','criteria','max-model-calls','max-tool-attempts','max-seconds','author'):a.add_argument('--'+name,required=True)
    a.add_argument('--allow-model-send',action='store_true');a.add_argument('--config-hash',default='')
    for name in ('run','stop','export'):
        a=commands.add_parser(name);a.add_argument('--mission',required=True)
        if name!='export':a.add_argument('--author',required=True)
        if name=='stop':a.add_argument('--reason',required=True)
        if name=='export':a.add_argument('--output',required=True,type=Path)
    args=p.parse_args();s=Programs(args.state_dir);revision=s.get(args.program)['revision']
    if args.command=='create':
        data=vars(args);data['allow_model_send']='yes' if args.allow_model_send else 'no'
        result={'mission_id':create(s,args.program,data,args.author,revision)}
    elif args.command=='run':
        state=run(s,args.program,args.mission,args.author,revision)
        print(json.dumps({'mission_id':state['mission_id'],'status':state['status'],'report':state['report']},ensure_ascii=False,indent=2))
        return 0 if state['status']=='delivered_unreviewed' else 2
    elif args.command=='stop':result=stop(s,args.program,args.mission,args.author,revision,args.reason)
    else:
        with args.output.open('xb') as stream:stream.write(export(s,args.program,args.mission))
        return 0
    print(json.dumps(result,ensure_ascii=False,indent=2));return 0


if __name__=='__main__':raise SystemExit(main())
