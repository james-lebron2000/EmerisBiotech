"""Program-only backup/verification and read-only restore; never overwrite."""
import argparse
import json
import sqlite3
from pathlib import Path
from vbt.program_backup import snapshot, verify, restore, source_status


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    subs=parser.add_subparsers(dest='command',required=True)
    command=subs.add_parser('create');command.add_argument('--state-dir',type=Path,required=True);command.add_argument('--output',type=Path,required=True);command.add_argument('--release',required=True)
    command=subs.add_parser('verify');command.add_argument('snapshot',type=Path)
    command=subs.add_parser('restore');command.add_argument('snapshot',type=Path);command.add_argument('--state-dir',type=Path,required=True)
    command=subs.add_parser('sources');command.add_argument('--state-dir',type=Path,required=True)
    args=parser.parse_args()
    try:
        if args.command=='create':result=snapshot(args.state_dir,args.output,args.release)
        elif args.command=='verify':result=verify(args.snapshot)
        elif args.command=='restore':result=restore(args.snapshot,args.state_dir)
        else:
            result=source_status(args.state_dir);print(json.dumps(result,ensure_ascii=False,indent=2));return 0 if result['complete'] else 2
    except (ValueError,OSError,sqlite3.Error) as exc:
        parser.exit(2,'Program backup operation failed: '+str(exc)+'\n')
    print(json.dumps(result,ensure_ascii=False,indent=2));return 0


if __name__=='__main__':raise SystemExit(main())
