"""Loopback runner; production TLS terminates at a dedicated trusted reverse proxy."""
import os
from pathlib import Path
import uvicorn
from emeris_live.main import create_app

if __name__=='__main__':
    uvicorn.run(create_app(),host='127.0.0.1',port=int(os.environ.get('EMERIS_PORT','19320')),access_log=False,proxy_headers=True,forwarded_allow_ips='127.0.0.1',limit_concurrency=40,timeout_keep_alive=5)
