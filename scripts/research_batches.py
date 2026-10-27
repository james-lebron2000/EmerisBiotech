"""Explicit local tool batches. No model calls, downloads or arbitrary execution."""
import argparse
import json
from pathlib import Path
from vbt.programs import Programs
from vbt.research_queue import enqueue,run_batch,cancel,recover,retry,export_batches,get_batch


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir',required=True,type=Path);parser.add_argument('--program',required=True)
    commands=parser.add_subparsers(dest='command',required=True)
    create=commands.add_parser('enqueue');create.add_argument('--proposal-id',required=True);create.add_argument('--task',action='append',required=True)
    create.add_argument('--dependencies-json',default='{}');create.add_argument('--max-attempts',type=int,default=4)
    create.add_argument('--max-seconds',type=float,default=60);create.add_argument('--per-task-seconds',type=float,default=15);create.add_argument('--author',required=True)
    for name in ('run','cancel','recover','retry'):
        sub=commands.add_parser(name);sub.add_argument('--batch',required=True);sub.add_argument('--author',required=True)
        if name!='run':sub.add_argument('--reason',required=True)
        if name=='retry':sub.add_argument('--task',required=True)
    sub=commands.add_parser('export');sub.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();store=Programs(args.state_dir)
    if args.command=='export':
        with args.output.open('xb') as stream:stream.write(export_batches(store,args.program))
        return
    revision=store.get(args.program)['revision']
    if args.command=='enqueue':
        result=enqueue(store,args.program,args.proposal_id,args.task,json.loads(args.dependencies_json),dict(max_attempts=args.max_attempts,max_seconds=args.max_seconds,per_task_seconds=args.per_task_seconds),args.author,revision)
    elif args.command=='run':
        receipts=run_batch(store,args.program,args.batch,args.author,revision)
        batch=get_batch(store,args.program,args.batch)
        result=dict(batch_id=args.batch,status=batch['status'],receipts=receipts,
                    remaining_attempts=batch['remaining_attempts'],remaining_seconds=batch['remaining_seconds'])
    elif args.command=='cancel':
        result=cancel(store,args.program,args.batch,args.author,revision,args.reason)
    elif args.command=='recover':
        result=recover(store,args.program,args.batch,args.author,revision,args.reason)
    else:
        result=retry(store,args.program,args.batch,args.task,args.author,revision,args.reason)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    if args.command=='run' and result['status']!='complete':raise SystemExit(2)


if __name__=='__main__':main()
