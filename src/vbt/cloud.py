"""Single-instance workbench behind an authenticating HTTPS proxy."""
import os
from pathlib import Path
from urllib.parse import urlsplit
from http.server import ThreadingHTTPServer
from .workbench import Workbench,handler

def validate_origin(value):
    u=urlsplit(value)
    if u.scheme!='https' or not u.hostname or u.username or u.password or u.path or u.query or u.fragment:
        raise ValueError('PUBLIC_ORIGIN must be an HTTPS origin without path or credentials')
    return value
class CloudWorkbench(Workbench):
    def start(self,kind,rid):
        if kind!='portfolio':raise ValueError('云端首版仅校验研究快照；分析任务需要独立受限执行器')
        return super().start(kind,rid)
def main():
    origin=validate_origin(os.environ.get('PUBLIC_ORIGIN',''))
    app=CloudWorkbench(Path('/workspace'),Path('/state'))
    with ThreadingHTTPServer(('0.0.0.0',8080),handler(app,origin)) as server:
        print('Private workbench backend ready; access only through authenticated proxy',flush=True)
        server.serve_forever()
if __name__=='__main__':main()
