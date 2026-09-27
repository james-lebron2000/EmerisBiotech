import argparse, json, sys
from pathlib import Path
import duckdb
from .io import load, write, within, digest, now
from .models import ResearchPlan, DecisionRecord
from .datasets import read_manifest
from .registry import Registry
from .engine import run_plan,verify_run,resume
from .geo import summary


def parser():
    p=argparse.ArgumentParser(description='Virtual Biotech: offline-first research audit. No data downloads.')
    p.add_argument('--workspace',type=Path,default=Path(__file__).resolve().parents[2])
    p.add_argument('--data-root',type=Path)
    p.add_argument('--state-dir',type=Path)
    sp=p.add_subparsers(dest='command',required=True)
    a=sp.add_parser('workbench');a.add_argument('--port',type=int,default=18764)
    a=sp.add_parser('register');a.add_argument('manifest',type=Path)
    a=sp.add_parser('prepare-geo');a.add_argument('--matrix',required=True);a.add_argument('--platform',required=True);a.add_argument('--rules',type=Path,required=True);a.add_argument('--output',type=Path,required=True)
    sp.add_parser('datasets')
    a=sp.add_parser('inspect');a.add_argument('path',type=Path)
    a=sp.add_parser('run');a.add_argument('plan',type=Path);a.add_argument('--script',type=Path)
    a=sp.add_parser('verify');a.add_argument('run_id')
    a=sp.add_parser('resume');a.add_argument('run_id')
    a=sp.add_parser('export');a.add_argument('--output',type=Path)
    a=sp.add_parser('restore');a.add_argument('backup',type=Path)
    a=sp.add_parser('results');a.add_argument('run_id')
    a=sp.add_parser('human-review');a.add_argument('run_id');a.add_argument('--reviewer',required=True);a.add_argument('--decision',choices=['pass','revise','insufficient_evidence'],required=True);a.add_argument('--reason',required=True);a.add_argument('--signature',required=True)
    a=sp.add_parser('model-draft');a.add_argument('--question',required=True);a.add_argument('--base-url',required=True);a.add_argument('--model',required=True);a.add_argument('--key-env',default='VBT_MODEL_API_KEY')
    a=sp.add_parser('forecast-score');a.add_argument('records',type=Path);a.add_argument('--training-groups',type=Path,required=True)
    from .portfolio_cli import add_parser
    add_parser(sp)
    return p


def main(argv=None):
    args=parser().parse_args(argv)
    if args.command=='workbench':
        from .workbench import serve
        serve(args.workspace,args.state_dir,args.port);return 0
    if args.command=='forecast-score':
        from .forecast import score_forecasts
        try:
            result=score_forecasts(load(args.records),load(args.training_groups))
            print(json.dumps(result,ensure_ascii=False,indent=2));return 0
        except Exception as e:
            print(json.dumps({'status':'error','error':str(e)},ensure_ascii=False),file=sys.stderr);return 2
    if args.command=='portfolio':
        from .portfolio_cli import dispatch
        try:return dispatch(args)
        except Exception as e:
            print(json.dumps({'status':'error','error':f'{type(e).__name__}: {e}'},ensure_ascii=False),file=sys.stderr);return 2
    workspace=args.workspace.resolve();root=(args.data_root or workspace.parent/'data').resolve()
    registry=Registry(workspace,args.state_dir)
    try:
        def runpath():return within(workspace/'artifacts'/'runs'/args.run_id,workspace/'artifacts'/'runs')
        result=None;code=0
        if args.command=='register':
            result={'registered':[],'errors':[]}
            for ds in read_manifest(args.manifest,root):
                try:result['registered'].append(registry.register(ds,root))
                except Exception as e:result['errors'].append({'dataset_id':ds.dataset_id,'error':str(e)})
            code=2 if result['errors'] else 0
        elif args.command=='prepare-geo':
            from .intake import prepare_geo
            destination=within(args.output,workspace/'artifacts'/'mappings')
            result=prepare_geo(registry,args.matrix,args.platform,load(args.rules),destination,root)
        elif args.command=='datasets':result=registry.entries()
        elif args.command=='inspect': result=summary(within(args.path,root))
        elif args.command=='run':
            plan=ResearchPlan.model_validate(load(args.plan))
            r=run_plan(registry,plan,root,args.script);result={**load(r/'status.json'),'audit':str(r/'audit.html')}
            code=0 if result['execution']=='succeeded' else 2
        elif args.command=='verify':
            r=runpath();result=verify_run(r)
            anchor=registry.db.execute('SELECT payload FROM runs WHERE run_id=?',(r.name,)).fetchone()
            if anchor and json.loads(anchor[0]).get('manifest_sha256')!=digest(r/'manifest.json'):
                result['integrity_ok']=False;result['errors'].append('Manifest differs from local registry anchor')
            code=0 if result['integrity_ok'] else 2
        elif args.command=='resume':
            r=resume(registry,runpath(),root);result={**load(r/'status.json'),'audit':str(r/'audit.html')}
            code=0 if result['execution']=='succeeded' else 2
        elif args.command=='export':result=registry.export(within(args.output,workspace/'artifacts') if args.output else None)
        elif args.command=='restore':result={'restored_datasets':registry.restore(args.backup,root)}
        elif args.command=='results':
            r=runpath();v=verify_run(r)
            if not v['integrity_ok']:raise ValueError('Cannot query changed run: '+str(v['errors']))
            p=r/'work'/'results.parquet'
            with duckdb.connect(':memory:') as con:
                # Fixed parameterized read query only. No arbitrary SQL or extensions.
                cur=con.execute('SELECT * FROM read_parquet(?)',[str(p)])
                result=[dict(zip([x[0] for x in cur.description],row)) for row in cur.fetchall()]
        elif args.command=='human-review':
            r=runpath();v=verify_run(r);status=load(r/'status.json')
            if not v['integrity_ok']:raise ValueError('Human review requires unchanged artifacts')
            if args.decision=='pass' and (not v['evidence_ok'] or status['execution']!='succeeded' or not load(r/'claims.json')):raise ValueError('Cannot pass a blocked/failed/evidence-incomplete run')
            review=DecisionRecord(decision=args.decision,reviewer=args.reviewer,reviewer_type='human',reason=args.reason,human_signature=args.signature)
            dest=workspace/'artifacts'/'reviews'/r.name/(now().replace(':','')+'.json')
            write(dest,{'run_id':r.name,'manifest_sha256':digest(r/'manifest.json'),'recorded_at':now(),'attestation':'Local user-entered review, not cryptographic identity verification','review':review.model_dump()})
            result={'review_record':str(dest),'original_run_unchanged':True}
        elif args.command=='model-draft':
            from .agents import OpenAICompatibleProvider,draft_plan
            destination=workspace/'artifacts'/'model_drafts'/(now().replace(':','')+'.json')
            try:
                provider=OpenAICompatibleProvider(args.base_url,args.model,args.key_env)
                plan=draft_plan(provider,args.question)
                # Model cannot silently invent registered data references.
                for key in plan.keys():registry.get(key)
                result={'status':'draft_requires_review','plan':plan.model_dump(),'model':args.model}
            except Exception as e:
                result={'status':'failed','error':str(e),'model':args.model,'question':args.question};code=2
            write(destination,result);result['record']=str(destination)
        print(json.dumps(result,indent=2,ensure_ascii=False,default=str,allow_nan=False));return code
    except Exception as e:
        print(json.dumps({'status':'error','error':f'{type(e).__name__}: {e}'},ensure_ascii=False),file=sys.stderr);return 2
    finally:registry.close()
if __name__=='__main__':sys.exit(main())
