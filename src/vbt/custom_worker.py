"""Separate execution entry for proposed code, with the same OS sandbox."""
import resource, runpy, sys
from pathlib import Path
from .io import load
from .models import ResearchPlan

config=load(sys.argv[1]);plan=ResearchPlan.model_validate(config['plan'])
resource.setrlimit(resource.RLIMIT_CPU,(plan.timeout_seconds,plan.timeout_seconds))
resource.setrlimit(resource.RLIMIT_FSIZE,(1024**3,1024**3))
resource.setrlimit(resource.RLIMIT_NOFILE,(128,128))
runpy.run_path(str(Path(__file__).parent.parent/'submitted.py'),run_name='__main__')
