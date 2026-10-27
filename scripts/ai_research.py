"""Research CLI; only explicit model-generate sends context to configured service."""
import argparse,json
from pathlib import Path
from vbt.programs import Programs
from vbt.ai_research import packet,ingest,run_tool

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--state-dir',type=Path,required=True);ap.add_argument('--program',required=True)
    sp=ap.add_subparsers(dest='command',required=True)
    a=sp.add_parser('packet');a.add_argument('--output',type=Path,required=True)
    a=sp.add_parser('import');a.add_argument('--proposal',type=Path,required=True);a.add_argument('--author',required=True);a.add_argument('--origin',choices=['assistant_session','external_model','human_import'],required=True);a.add_argument('--model-label',required=True)
    a=sp.add_parser('run');a.add_argument('--proposal-id',required=True);a.add_argument('--task',required=True);a.add_argument('--author',required=True)
    sp.add_parser('model-preview')
    a=sp.add_parser('model-generate');a.add_argument('--packet-hash',required=True);a.add_argument('--config-hash');a.add_argument('--send-context',action='store_true');a.add_argument('--author',required=True)
    args=ap.parse_args();store=Programs(args.state_dir)
    if args.command=='packet':
        with args.output.open('x') as f:json.dump(packet(store,args.program),f,ensure_ascii=False,indent=2)
        print('Local packet exported; no model request made')
    elif args.command=='import':
        raw=args.proposal.read_bytes()
        if len(raw)>64000:raise ValueError('Proposal exceeds 64KB')
        ingest(store,args.program,json.loads(raw),args.author,store.get(args.program)['revision'],{'kind':args.origin,'model_label':args.model_label})
        print('Unreviewed proposal recorded: '+store.events(args.program)[-1]['id'])
    elif args.command=='model-preview':
        from vbt.research_model import configuration
        context=packet(store,args.program)
        print(json.dumps(dict(configuration=configuration()[1],packet_hash=context['packet_hash'],send_context=False),ensure_ascii=False,indent=2))
    elif args.command=='model-generate':
        from vbt.research_model import generate
        result=generate(store,args.program,args.author,store.get(args.program)['revision'],args.packet_hash,args.config_hash,args.send_context)
        print(json.dumps({k:v for k,v in result.items() if k!='draft'},ensure_ascii=False,indent=2))
        if result['status']!='succeeded':raise SystemExit(2)
    else:print(json.dumps(run_tool(store,args.program,args.proposal_id,args.task,args.author,store.get(args.program)['revision']),ensure_ascii=False,indent=2))

if __name__=='__main__':main()
