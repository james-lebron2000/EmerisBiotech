"""Fixed public443/internal18443 Nginx split; preserve unrelated server blocks."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import urllib.request

SITE=Path('/etc/nginx/sites-available/emeris-live')
BACKUP=Path('/etc/nginx/emeris-live.before-public-lab-20261023.conf')
RECEIPT=Path('/opt/emeris-live/public-lab-frontends.json')
HOST='8.148.145.28'
PUBLIC_ORIGIN='https://'+HOST
PORT=18443

TLS='''    server_name 8.148.145.28;
    ssl_certificate /etc/emeris-letsencrypt/live/8.148.145.28/fullchain.pem;
    ssl_certificate_key /etc/emeris-letsencrypt/live/8.148.145.28/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_session_cache shared:EMERISTLS:5m;
    ssl_session_timeout 1d;
    client_max_body_size 6m;
    client_body_timeout 20s;
    limit_req_status 429;
    limit_conn_status 429;
    limit_conn emeris_conn 12;
    access_log off;
    error_log /var/log/nginx/emeris-live.error.log warn;
'''
PROXY='''        proxy_pass http://127.0.0.1:19320;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_read_timeout 60s;
'''


def render():
    zones='''limit_req_zone $binary_remote_addr zone=emeris_api:10m rate=10r/s;
limit_req_zone $binary_remote_addr zone=emeris_auth:10m rate=2r/s;
limit_conn_zone $binary_remote_addr zone=emeris_conn:10m;
'''
    public='server {\n    listen 443 ssl http2;\n'+TLS
    for path in ('/arena/lab','/static/arena.html','/static/arena-lab.html','/static/arena.js','/static/dashboard.html','/static/analytics.html','/static/track-record.html','/static/predome.html','/static/trial-detail.html','/static/trials.html','/api/arena','/api/intake/status','/openapi.json','/docs','/redoc'):
        public+='    location = '+path+' { return 404; }\n'
    public+='    location ^~ /arena/lab/ { return 404; }\n'
    for path in ('/analytics','/predome','/trials'):
        public+='    location = '+path+' { return 302 https://8.148.145.28:18443'+path+'; }\n'
    public+='    location ^~ /trial/ { return 302 https://8.148.145.28:18443$request_uri; }\n'
    # Legacy published research also contains uncalibrated probabilities and
    # evaluation metadata. Keep it available only through the authenticated port.
    for path in ('/api/product','/api/dashboard','/api/predome','/api/forecasts','/api/analytics',
                 '/api/platform/snapshots','/api/platform/evaluations',
                 '/api/platform/public-outcomes','/api/platform/baselines'):
        public+='    location ^~ '+path+' { return 404; }\n'
    public+='    location = /track-record { return 302 /arena?period=reported; }\n'
    for path in ('= /api/arena/public','^~ /api/arena/public/','^~ /api/arena/sources/'):
        public+='    location '+path+' {\n        limit_req zone=emeris_api burst=40 nodelay;\n'+PROXY+'    }\n'
    public+='    location ^~ /api/arena/ { return 404; }\n'
    public+='    location /api/auth/ {\n        limit_req zone=emeris_auth burst=10 nodelay;\n'+PROXY+'    }\n'
    public+='    location / {\n        limit_req zone=emeris_api burst=40 nodelay;\n'+PROXY+'    }\n}\n'
    internal='server {\n    listen 18443 ssl http2;\n'+TLS
    internal+='''    location = / { return 302 /arena/lab; }
    location = /arena { return 302 /arena/lab; }
    location = /data { return 302 https://8.148.145.28/data; }
    location = /_lab_auth {
        internal;
        proxy_pass http://127.0.0.1:19320/api/arena/lab/access;
        proxy_pass_request_body off;
        proxy_set_header Content-Length "";
        proxy_set_header Cookie $http_cookie;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
'''
    for path in ('= /arena/lab','^~ /arena/lab/'):
        internal+='    location '+path+' {\n'+PROXY+'    }\n'
    for path in ('= /static/arena.html','= /static/arena-lab.html','= /api/arena','^~ /api/arena/','/api/'):
        internal+='    location '+path+' {\n        auth_request /_lab_auth;\n        limit_except GET HEAD { deny all; }\n'+PROXY+'    }\n'
    internal+='    location /static/ {\n'+PROXY+'    }\n'
    internal+='    location / {\n        auth_request /_lab_auth;\n        limit_except GET HEAD { deny all; }\n'+PROXY+'    }\n}\n'
    return (zones+public+internal).encode()


def upgrade_review_routes(old):
    """Insert the scoped review exception; preserve published proofs and old bytes.

    render() remains the historical 1.2.1 baseline used by its pinned publication
    workflow. It must not replace a later site's public proof aliases or changes.
    """
    if not isinstance(old,bytes) or not 0<len(old)<=262144:
        raise ValueError('An existing bounded frontend configuration is required')
    separator=b'server {\n    listen 18443 ssl http2;\n'
    public_anchor=b'    location ^~ /arena/lab/ { return 404; }\n'
    auth_anchor=b'    location = /_lab_auth {\n'
    api_anchor=b'    location = /api/arena {\n'
    if (old.count(separator)!=1 or old.count(b'server {\n    listen 443 ssl http2;\n')!=1
            or any(marker in old for marker in (b'/_review_auth',b'/api/arena/review/',b'/static/arena-review.'))):
        raise ValueError('Frontend is not an unmodified review-upgrade baseline')
    public,internal=old.split(separator,1)
    if (public.count(public_anchor)!=1 or internal.count(auth_anchor)!=1 or internal.count(api_anchor)!=1
            or b'    location ^~ /api/arena/ { return 404; }\n' not in public):
        raise ValueError('Expected public/internal access boundaries are missing')
    blockers=b''.join(('    location = /static/'+name+' { return 404; }\n').encode()
                      for name in ('arena-review.html','arena-review.js','arena-review.css'))
    review_auth=b'''    location = /_review_auth {
        internal;
        proxy_pass http://127.0.0.1:19320/api/arena/review/access;
        proxy_pass_request_body off;
        proxy_set_header Content-Length "";
        proxy_set_header Cookie $http_cookie;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
'''
    routes=b''
    for path,methods in (('^~ /api/arena/review/','GET HEAD POST'),('= /static/arena-review.html','GET HEAD')):
        routes+=('    location '+path+' {\n        auth_request /_review_auth;\n        limit_except '+methods+' { deny all; }\n'+PROXY+'    }\n').encode()
    return (public.replace(public_anchor,public_anchor+blockers,1)+separator
            +internal.replace(auth_anchor,review_auth+auth_anchor,1).replace(api_anchor,routes+api_anchor,1))


def digest(raw):return hashlib.sha256(raw).hexdigest()


def exclusive(path,raw,mode=0o600):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,mode)
    with os.fdopen(fd,'wb') as s:s.write(raw);s.flush();os.fsync(s.fileno())


def remote(expected):
    if SITE.is_symlink() or not SITE.is_file() or digest(SITE.read_bytes())!=expected:
        raise ValueError('Existing site changed; no configuration was applied')
    if BACKUP.exists() or RECEIPT.exists():raise ValueError('Frontend split already attempted; inspect prior receipt')
    with urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:19320/api/health',headers={'Host':HOST}),timeout=15) as r:
        if json.load(r).get('version')!='1.2.1':raise ValueError('Deploy app1.2.1 before frontend split')
    listeners=subprocess.check_output(['ss','-ltnH'],text=True)
    if any(row.split()[3].endswith(':'+str(PORT)) for row in listeners.splitlines()):
        raise ValueError('Internal port is already used; no unrelated listener will be changed')
    old=SITE.read_bytes();new=render();exclusive(BACKUP,old)
    staged=SITE.with_name(SITE.name+'.public-lab.tmp');exclusive(staged,new,0o644)
    try:
        os.replace(staged,SITE)
        subprocess.run(['nginx','-t'],check=True,capture_output=True)
        subprocess.run(['systemctl','reload','nginx'],check=True)
        receipt={'status':'configured','public_origin':PUBLIC_ORIGIN,'internal_origin':PUBLIC_ORIGIN+':'+str(PORT),
            'previous_config_sha256':digest(old),'config_sha256':digest(new),'backup':str(BACKUP),
            'public_raw_debug_routes':'404','internal_raw_api':'existing-session plus dedicated lab allowlist',
            'unrelated_sites_modified':False,'application_database_modified':False}
        exclusive(RECEIPT,(json.dumps(receipt,indent=2)+'\n').encode())
        print(json.dumps(receipt))
    except BaseException:
        restoration=SITE.with_name(SITE.name+'.restore.tmp');exclusive(restoration,old,0o644);os.replace(restoration,SITE)
        subprocess.run(['nginx','-t'],check=True,capture_output=True);subprocess.run(['systemctl','reload','nginx'],check=True)
        raise


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--remote',action='store_true')
    p.add_argument('--expected-sha256',required=True);a=p.parse_args()
    if not a.remote:p.error('Fixed remote operator only; upload the committed script through authenticated SSH')
    remote(a.expected_sha256)

if __name__=='__main__':main()
