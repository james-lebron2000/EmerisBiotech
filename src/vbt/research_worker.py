"""Fixed local read-only worker. No shell, model client, or caller-supplied code."""
import argparse
import json
from pathlib import Path
import signal
import sys

# -I prevents user site/PYTHONPATH injection. Only this installed/source package
# root is supplied by the host's fixed script, not by proposal text.
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from vbt.programs import Programs, canonical
from vbt.ai_research import TOOLS, audit_sources, interaction_card, packet, _read


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--state-dir',required=True)
    parser.add_argument('--program',required=True)
    parser.add_argument('--tool',choices=list(TOOLS),required=True)
    parser.add_argument('--packet-hash',required=True)
    parser.add_argument('--seconds',type=float,required=True)
    args=parser.parse_args()
    def deadline(*_):
        raise TimeoutError()
    signal.signal(signal.SIGALRM,deadline);signal.setitimer(signal.ITIMER_REAL,args.seconds)
    result=dict(status='blocked',error='worker_internal',output=None)
    try:
        store=Programs(args.state_dir)
        if packet(store,args.program)['packet_hash']!=args.packet_hash:
            result['error']='context_changed';result['status']='stale'
        else:
            output=({'audit_bound_sources':audit_sources,'extract_interaction_evidence':interaction_card}[args.tool])(store,args.program)
            sources=output['files'] if args.tool=='audit_bound_sources' else [output['source']]
            for source in sources:
                _,current=_read(store,args.program,source['file'])
                if current['sha256']!=source['sha256']:
                    raise ValueError('source changed during tool')
            if packet(store,args.program)['packet_hash']!=args.packet_hash:
                result.update(status='stale',error='context_changed')
            else:
                result.update(status='succeeded',error=None,output=output)
    except TimeoutError:
        result.update(error='worker_deadline',output=None)
    except (OSError,ValueError,TypeError,KeyError,UnicodeError,OverflowError):
        result.update(error='source_invalid',output=None)
    except Exception:
        result.update(error='worker_internal',output=None)
    finally:
        signal.setitimer(signal.ITIMER_REAL,0)
    raw=canonical(result).encode()
    if len(raw)>512*1024:
        raw=canonical(dict(status='blocked',error='tool_output_too_large',output=None)).encode()
    sys.stdout.buffer.write(raw)
    return 0 if result['status']=='succeeded' else 2


if __name__=='__main__':
    raise SystemExit(main())
