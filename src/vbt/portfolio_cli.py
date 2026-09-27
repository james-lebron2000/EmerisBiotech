from pathlib import Path
import json
from .portfolio import PortfolioStore
from .portfolio_models import PortfolioReview
from .io import within

def add_parser(sp):
    p=sp.add_parser('portfolio',help='Versioned asset dossiers and experiment-readiness gates')
    sub=p.add_subparsers(dest='portfolio_command',required=True)
    a=sub.add_parser('build');a.add_argument('plan',type=Path);a.add_argument('--catalog',required=True,type=Path);a.add_argument('--parent')
    a=sub.add_parser('serve');a.add_argument('snapshot_id');a.add_argument('--port',type=int,default=18764)
    sub.add_parser('status')
    a=sub.add_parser('verify');a.add_argument('snapshot_id')
    a=sub.add_parser('review');a.add_argument('snapshot_id');a.add_argument('--hypothesis',required=True);a.add_argument('--action',required=True,choices=['continue_research','request_revision','stop','authorize_experiment']);a.add_argument('--reviewer',required=True);a.add_argument('--reason',required=True);a.add_argument('--signature',required=True)
    a=sub.add_parser('backup');a.add_argument('--output',required=True,type=Path)
    a=sub.add_parser('restore');a.add_argument('backup',type=Path);a.add_argument('--sha256',required=True)

def dispatch(args):
    store=PortfolioStore(args.workspace,args.state_dir)
    try:
        c=args.portfolio_command
        if c=='build':result=store.build(args.plan,args.catalog,args.parent)
        elif c=='serve':
            from http.server import ThreadingHTTPServer,SimpleHTTPRequestHandler
            from urllib.parse import unquote,urlsplit
            from .portfolio import PACKAGE_FILES
            root=store.require_valid(args.snapshot_id)
            class Handler(SimpleHTTPRequestHandler):
                def __init__(self,*a,**kw):super().__init__(*a,directory=str(root),**kw)
                def do_GET(self):
                    route=unquote(urlsplit(self.path).path).lstrip('/') or 'index.html'
                    if route not in PACKAGE_FILES:self.send_error(404);return
                    super().do_GET()
                def do_HEAD(self):
                    route=unquote(urlsplit(self.path).path).lstrip('/') or 'index.html'
                    if route not in PACKAGE_FILES:self.send_error(404);return
                    super().do_HEAD()
            print(f'http://127.0.0.1:{args.port} — read-only local portfolio',flush=True)
            with ThreadingHTTPServer(('127.0.0.1',args.port),Handler) as server:
                try:server.serve_forever()
                except KeyboardInterrupt:pass
            return 0
        elif c=='status':result=store.status()
        elif c=='verify':result=store.verify(args.snapshot_id)
        elif c=='review':result=store.review(args.snapshot_id,PortfolioReview(hypothesis_id=args.hypothesis,action=args.action,reviewer=args.reviewer,reason=args.reason,signature=args.signature))
        elif c=='backup':result=store.backup(within(args.output,store.workspace/'artifacts'))
        elif c=='restore':result=store.restore(args.backup,args.sha256)
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 2 if c=='verify' and not result['integrity_ok'] else 0
    finally:store.close()
