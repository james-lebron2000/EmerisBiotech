import json, resource, sys
from pathlib import Path
from .models import ResearchPlan
from .io import load, write
from .analysis import run_geo
from .geo import summary, export_annotations

def main():
    config=load(sys.argv[1]); plan=ResearchPlan.model_validate(config['plan'])
    resource.setrlimit(resource.RLIMIT_CPU,(plan.timeout_seconds,plan.timeout_seconds))
    resource.setrlimit(resource.RLIMIT_FSIZE,(1024**3,1024**3))
    resource.setrlimit(resource.RLIMIT_NOFILE,(128,128))
    out=Path(config['output']);inputs=config['inputs']
    if plan.workflow=='geo_uc': result=run_geo(plan,inputs,out)
    elif plan.workflow=='inventory':
        rows=[]
        for key,ds in inputs.items():
            if ds['format']=='geo_matrix':
                rows.append({'dataset_key':key,**summary(ds['local_path'])})
                export_annotations(ds['local_path'],out/f'{ds["dataset_id"]}_source_annotations.csv')
        write(out/'inventory.json',rows)
        result={'execution':'succeeded','gaps':[]}
    else: raise ValueError('Custom code uses a separate entrypoint')
    write(out/'worker_result.json',result)
if __name__=='__main__': main()
