"""macOS Seatbelt runner. Fails closed on hosts without a working sandbox."""
import json, os, signal, subprocess, sys, time
from pathlib import Path

class SandboxUnavailable(RuntimeError): pass

def profile(code_root, inputs_dir, output, input_files):
    q=lambda p:json.dumps(str(Path(p).resolve()))
    read_roots={Path('/System'),Path('/usr/lib'),Path('/usr/share'),Path('/opt/homebrew/Cellar'),Path('/opt/homebrew/opt'),Path(sys.base_prefix),Path(sys.prefix),Path(code_root),Path(inputs_dir),Path(output)}
    rules=['(version 1)','(deny default)','(allow process*)','(allow sysctl-read)','(allow mach-lookup)','(allow file-read-metadata)','(allow file-map-executable)']
    rules += [f'(allow file-read* (subpath {q(p)}))' for p in sorted(read_roots)]
    rules += [f'(allow file-read* (literal {q(p)}))' for p in input_files]
    # macOS Python launchers inspect ancestor directories during exec. Grant only
    # directory entries, not the contents of sibling files or subtrees.
    ancestors={a for p in list(read_roots)+[Path(f).resolve() for f in input_files] for a in p.resolve().parents}
    rules += [f'(allow file-read* (literal {q(a)}))' for a in sorted(ancestors)]
    rules += ['(allow file-read* (subpath "/dev"))','(allow file-write* (literal "/dev/null"))',f'(allow file-write* (subpath {q(output)}))']
    # Network, private home directories, registry and source writes remain denied.
    return '\n'.join(rules)

def execute(run_dir, input_files, timeout, custom=False):
    run_dir=Path(run_dir).resolve();out=run_dir/'work';out.mkdir(exist_ok=True)
    if sys.platform!='darwin' or not Path('/usr/bin/sandbox-exec').exists():
        raise SandboxUnavailable('macOS sandbox-exec required; unsandboxed fallback disabled')
    source=profile(run_dir/'code',run_dir/'inputs',out,input_files)
    (run_dir/'sandbox.sb').write_text(source)
    env={'PATH':'/usr/bin:/bin','HOME':str(out),'TMPDIR':str(out),'PYTHONPATH':str(run_dir/'code'),
         'PYTHONDONTWRITEBYTECODE':'1','PYTHONNOUSERSITE':'1','OPENBLAS_NUM_THREADS':'1','OMP_NUM_THREADS':'1','VECLIB_MAXIMUM_THREADS':'1',
         'LANG':'en_US.UTF-8'}
    memory_kb=json.loads((run_dir/'inputs'/'worker_config.json').read_text())['plan']['memory_gb']*1024**2
    module='vbt.custom_worker' if custom else 'vbt.worker'
    framework=Path(sys.base_prefix)/'Resources/Python.app/Contents/MacOS/Python'
    binary=str(framework) if framework.exists() else sys.executable
    if framework.exists():env['__PYVENV_LAUNCHER__']=sys.executable
    command=['/usr/bin/sandbox-exec','-f',str(run_dir/'sandbox.sb'),binary,'-m',module,str(run_dir/'inputs'/'worker_config.json')]
    with (run_dir/'stdout.log').open('wb') as stdout, (run_dir/'stderr.log').open('wb') as stderr:
        p=subprocess.Popen(command,cwd=out,env=env,stdout=stdout,stderr=stderr,start_new_session=True)
        reason=None;deadline=time.monotonic()+timeout
        try:
            while p.poll() is None:
                if time.monotonic()>deadline: reason='wall_timeout';break
                files=list(out.rglob('*'))
                if any(f.is_symlink() for f in files): reason='symlink_output';break
                if len(files)>2000 or sum(f.stat().st_size for f in files if f.is_file())>1024**3:
                    reason='output_budget_exceeded';break
                usage=subprocess.run(['/bin/ps','-axo','pgid=,rss='],capture_output=True,text=True)
                rss=sum(int(parts[1]) for line in usage.stdout.splitlines() if len(parts:=line.split())==2 and parts[0]==str(p.pid))
                if rss>memory_kb: reason='memory_budget_exceeded';break
                time.sleep(.2)
        finally:
            if reason or p.poll() is None:
                try: os.killpg(p.pid,signal.SIGKILL)
                except ProcessLookupError: pass
            try: p.wait(timeout=3)
            except subprocess.TimeoutExpired: reason=reason or 'termination_pending'
            # Also reap spawned descendants even if the leader exited first.
            try: os.killpg(p.pid,signal.SIGKILL)
            except ProcessLookupError: pass
    return {'returncode':p.returncode if p.returncode is not None else -999,'stop_reason':reason,'sandbox':'macOS Seatbelt','network':'denied','environment':'allowlist; no credentials'}
