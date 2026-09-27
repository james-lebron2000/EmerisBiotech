import importlib.metadata, json, os, platform, shutil, subprocess, sys, uuid
from pathlib import Path
from .models import ResearchPlan, EvidenceClaim, AnalysisRun
from .io import write, load, digest, now, within, ensure_storage
from .datasets import validate
from .executor import execute
from .agents import review, require_tool, scientific_checklist
from .report import render


def evidence_claims(run,plan):
    if plan.workflow!='geo_uc' or not (run/'work'/'results.json').exists(): return []
    claims=[]
    for row in load(run/'work'/'results.json'):
        if row['status']!='estimated':continue
        direction='unknown'
        if row['q_value']<.05 and plan.expected_direction!='unspecified':
            same=(row['median_difference']>0)==(plan.expected_direction=='higher_in_nonresponders')
            direction='support' if same else 'oppose'
        claims.append(EvidenceClaim(claim_id=f'C{len(claims)+1:03}',direction=direction,
            text=f"{row['cohort']} {row['gene']}: baseline expression median difference (nonresponder - responder) = {row['median_difference']:.6g}, 95% bootstrap CI [{row['ci_low']:.6g}, {row['ci_high']:.6g}], P={row['p_value']:.6g}, BH q={row['q_value']:.6g}; observational association only.",
            artifact='work/results.json',row_id=row['row_id'],limitations=['Exploratory observational comparison','Cohort-specific endpoint; no cross-cohort pooling','Human scientific review required']).model_dump())
    return claims


def seal(run):
    files={}
    for p in sorted(run.rglob('*')):
        if p.is_symlink(): raise ValueError('Symlink in run artifacts')
        if p.is_file() and p.name!='manifest.json': files[str(p.relative_to(run))]={'sha256':digest(p),'size_bytes':p.stat().st_size}
    write(run/'manifest.json',{'schema_version':1,'run_id':run.name,'sealed_at':now(),'files':files})


def verify_run(run,check_inputs=True):
    run=Path(run).resolve();errors=[];evidence_errors=[]
    try:
        manifest=load(run/'manifest.json')
        if manifest['run_id']!=run.name: errors.append('Run identity mismatch')
        required={'status.json','inputs/plan.json','inputs/datasets.json','inputs/worker_config.json','inputs/resolution.json','claims.json','review.json','audit.html','environment.json'}
        if not required.issubset(manifest['files']):errors.append('Missing mandatory manifest artifacts')
        expected=set(manifest['files']);actual={str(p.relative_to(run)) for p in run.rglob('*') if p.is_file() and p.name!='manifest.json'}
        if expected!=actual:errors.append('Artifact inventory differs from sealed manifest')
        for rel,meta in manifest['files'].items():
            p=within(run/rel,run)
            if (run/rel).is_symlink() or not p.is_file() or p.stat().st_size!=meta['size_bytes'] or digest(p)!=meta['sha256']: errors.append('Artifact changed/missing: '+rel)
        plan=ResearchPlan.model_validate(load(run/'inputs'/'plan.json'))
        inputs=load(run/'inputs'/'datasets.json')
        resolution=load(run/'inputs'/'resolution.json')
        if set(inputs)|set(resolution['missing_keys']) != set(plan.keys()):errors.append('Frozen inputs do not match plan')
        if check_inputs:
            for key,ds in inputs.items():
                p=Path(ds['local_path'])
                if not p.is_file() or p.stat().st_size!=ds['size_bytes'] or digest(p)!=ds['sha256']: errors.append('External input changed/missing: '+key)
        # Do not parse untrusted altered artifacts for evidence checks.
        if not errors:
            claims=[EvidenceClaim.model_validate(c) for c in load(run/'claims.json')]
            ids=set();linked=set()
            for c in claims:
                if c.claim_id in ids: evidence_errors.append('Duplicate claim identifier')
                ids.add(c.claim_id)
                p=within(run/c.artifact,run)
                if c.artifact not in expected: evidence_errors.append('Unsealed evidence artifact');continue
                rows=load(p)
                matches=[r for r in rows if isinstance(r,dict) and r.get('row_id')==c.row_id] if isinstance(rows,list) else []
                if len(matches)!=1 or matches[0].get('status')!='estimated':evidence_errors.append('Missing/non-estimable evidence row: '+c.row_id)
                linked.add(c.row_id)
            if plan.workflow=='geo_uc':
                result_path=run/'work'/'results.json'
                rows=load(result_path) if result_path.exists() else []
                estimated={r['row_id'] for r in rows if r['status']=='estimated'}
                if estimated!=linked:evidence_errors.append('Claim coverage mismatch')
                if not estimated:evidence_errors.append('No estimated scientific results')
            elif plan.workflow=='custom':evidence_errors.append('Custom outputs have not been admitted as scientific evidence')
    except Exception as e:errors.append(f'{type(e).__name__}: {e}')
    return {'run_id':run.name,'integrity_ok':not errors,'evidence_ok':not errors and not evidence_errors,'errors':errors,'evidence_errors':evidence_errors,'scientific_correctness_verified':False}


def code_snapshot(run):
    shutil.copytree(Path(__file__).parent,run/'code'/'vbt',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    workspace=Path(__file__).resolve().parents[2]
    if (workspace/'uv.lock').exists():shutil.copyfile(workspace/'uv.lock',run/'code'/'uv.lock')
    try:
        git=subprocess.run(['git','rev-parse','HEAD'],cwd=workspace,capture_output=True,text=True)
        commit=git.stdout.strip() if git.returncode==0 else 'uncommitted'
    except OSError:commit='unavailable'
    return commit


def run_plan(registry, plan, data_root, custom_script=None, parent_run=None, code_from=None):
    require_tool('geo_analyst','execute_geo')
    run_id=now().replace(':','').replace('.','-')+'-'+uuid.uuid4().hex[:8]
    run=registry.workspace/'artifacts'/'runs'/run_id
    ensure_storage(run)
    (run/'inputs').mkdir(parents=True);(run/'work').mkdir()
    status=AnalysisRun(run_id=run_id,execution='pending',record_integrity='pending',scientific_review='pending').model_dump()
    status.update(started_at=now(),parent_run=parent_run,errors=[],owner_pid=os.getpid())
    write(run/'inputs'/'plan.json',plan.model_dump())
    inputs={};input_paths=[]
    # Freeze available identities even when revalidation fails, to preserve cause.
    for key in plan.keys():
        try:
            ds=registry.get(key);inputs[key]=ds.model_dump()
            if ds.evidence_kind!=plan.evidence_kind:raise ValueError('Synthetic/real evidence kind mismatch')
            validate(ds,data_root);input_paths.append(ds.local_path)
        except Exception as e:status['errors'].append(f'{key}: {type(e).__name__}: {e}')
    write(run/'inputs'/'datasets.json',inputs)
    write(run/'inputs'/'resolution.json',{'missing_keys':sorted(set(plan.keys())-set(inputs)),'errors':status['errors'].copy()})
    write(run/'inputs'/'worker_config.json',{'plan':plan.model_dump(),'inputs':inputs,'output':str(run/'work')})
    if code_from:
        shutil.copytree(Path(code_from)/'code',run/'code')
        status['code_commit']=load(Path(code_from)/'status.json')['code_commit']
    else: status['code_commit']=code_snapshot(run)
    write(run/'environment.json',{'python':sys.version,'executable':sys.executable,'platform':platform.platform(),
        'packages':{name:importlib.metadata.version(name) for name in ('vbt-trust','pydantic','duckdb','pyarrow','numpy','scipy')}})
    if custom_script:
        if plan.workflow!='custom': raise ValueError('Custom code requires a custom plan')
        shutil.copyfile(custom_script,run/'code'/'submitted.py')
    elif plan.workflow=='custom':status['errors'].append('Custom code missing')
    write(run/'status.json',status);registry.save_run(status)
    if status['errors']: status['execution']='blocked'
    else:
        try:
            status['execution']='running';write(run/'status.json',status);registry.save_run(status)
            execution=execute(run,input_paths,plan.timeout_seconds,custom=plan.workflow=='custom');status['executor']=execution
            symlinks=[p for p in (run/'work').rglob('*') if p.is_symlink()]
            for p in symlinks:p.unlink()
            if symlinks: status['errors'].append('Worker created forbidden output symlinks (removed without following)')
            if execution['returncode'] or execution['stop_reason'] or symlinks:
                status['execution']='failed';status['errors'].append('Worker failed; see stderr.log / executor stop_reason')
            elif plan.workflow=='custom':status['execution']='succeeded'
            else:status['execution']=load(run/'work'/'worker_result.json')['execution']
            # Catch external changes between initial validation and completion.
            for key,ds in inputs.items():
                if digest(ds['local_path'])!=ds['sha256']:
                    status['errors'].append('Input changed during execution: '+key);status['execution']='failed'
        except KeyboardInterrupt:
            status['execution']='interrupted';status['errors'].append('Interrupted; resume creates a new linked run')
        except Exception as e:
            status['execution']='failed';status['errors'].append(f'{type(e).__name__}: {e}')
    finish(run,plan,status,registry)
    return run


def finish(run,plan,status,registry):
    for p in (run/'work').rglob('*'):
        if p.is_symlink():
            p.unlink();status['errors'].append('Forbidden output symlink removed during finalization');status['execution']='failed'
    claims=evidence_claims(run,plan) if status['execution'] in ('succeeded','blocked') else []
    write(run/'claims.json',claims)
    decision=review(plan,status['execution'],claims,status['errors'])
    write(run/'review.json',decision.model_dump())
    write(run/'review_checklist.json',scientific_checklist(plan,run,status['execution'],claims,status['errors']))
    status.update(scientific_review=decision.decision,record_integrity='complete',finished_at=now())
    write(run/'status.json',status);render(run);seal(run)
    registry.save_run({**status,'manifest_sha256':digest(run/'manifest.json')})


def resume(registry,run,data_root):
    run=within(run,registry.workspace/'artifacts'/'runs')
    # A killed parent can leave an unsealed run. Seal its interruption record first.
    if not (run/'manifest.json').exists():
        status=load(run/'status.json')
        try:
            os.kill(status.get('owner_pid',-1),0)
        except (ProcessLookupError,PermissionError): pass
        else: raise ValueError('Run owner is still alive; refusing concurrent resume')
        status.update(execution='interrupted',errors=status['errors']+['Recovered unsealed interrupted run; computation will restart'])
        finish(run,ResearchPlan.model_validate(load(run/'inputs'/'plan.json')),status,registry)
    result=verify_run(run)
    if not result['integrity_ok']:raise ValueError('Cannot resume changed inputs/artifacts: '+str(result['errors']))
    plan=ResearchPlan.model_validate(load(run/'inputs'/'plan.json'))
    # Reuse archived code and refuse dependency drift.
    old_env=load(run/'environment.json')
    for name,version in old_env['packages'].items():
        if importlib.metadata.version(name)!=version:raise ValueError('Dependency drift before resume: '+name)
    anchor=registry.db.execute('SELECT payload FROM runs WHERE run_id=?',(run.name,)).fetchone()
    if anchor and json.loads(anchor[0]).get('manifest_sha256')!=digest(run/'manifest.json'):raise ValueError('Manifest differs from local registry anchor')
    child=run_plan(registry,plan,data_root,run/'code'/'submitted.py' if plan.workflow=='custom' else None,parent_run=run.name,code_from=run)
    return child
