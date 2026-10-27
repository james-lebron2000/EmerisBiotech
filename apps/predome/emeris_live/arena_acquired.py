"""Validate existing acquired Arena sources without network or runtime writes.

The runtime key decrypts intake tables only. Source integrity and exact registry
identity do not create an outcome label, reviewer identity or scientific approval.
"""
import base64
from contextlib import closing
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlsplit
from cryptography.fernet import Fernet

from .arena_store import instant

KINDS={'registry_update','reported_results_candidate','official_announcement_candidate','pubmed_literature_candidate'}
LIMIT=2000


def regular(path,maximum):
    p=Path(path)
    if p.is_symlink() or not p.is_file() or not 0<p.stat().st_size<=maximum:raise ValueError('Existing bounded regular file required')
    return p


def source_url(value):
    if not isinstance(value,str):raise ValueError('Public source URL required')
    p=urlsplit(value)
    if (p.scheme!='https' or not p.hostname or p.username or p.password or p.port not in (None,443)
            or p.fragment or len(value)>4000):raise ValueError('Public acquisition URL is invalid')
    return value


def read_acquired(data_dir,ncts):
    ncts=list(dict.fromkeys(ncts))
    if len(ncts)>30 or any(not re.fullmatch(r'NCT[0-9]{8}',n) for n in ncts):raise ValueError('Bounded exact registry identities required')
    if not ncts:return []
    root=Path(data_dir)
    if root.is_symlink() or not root.is_dir():raise ValueError('Existing runtime directory required')
    path=regular(root/'workbench.sqlite3',8*1024**3)
    cipher=Fernet(regular(root/'master.key',256).read_bytes())
    unpack=lambda value:json.loads(cipher.decrypt(value))
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row;db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
        rows=db.execute('SELECT id,nct,kind,source_url,sha256,created,payload FROM intake_candidates WHERE nct IN ('+','.join('?' for _ in ncts)+') ORDER BY created DESC,id DESC LIMIT ?', (*ncts,LIMIT+1))
        values=[];total_bytes=0
        for number,row in enumerate(rows,1):
            if number>LIMIT:raise ValueError('Intake inbox exceeds explicit bound; partition before reading')
            if len(row['payload'])>12*1024*1024:raise ValueError('Acquisition payload exceeds explicit bound')
            if row['kind'] not in KINDS:continue
            item=unpack(row['payload']);source=item.get('source',{});url=source_url(source.get('url'))
            if row['kind'] in ('registry_update','reported_results_candidate') and url!='https://clinicaltrials.gov/api/v2/studies/'+row['nct']:
                raise ValueError('Registry source does not bind the exact trial')
            if row['kind']=='official_announcement_candidate':
                from emeris_live.official_intake import allowed
                if not allowed(url):raise ValueError('Official source left approved domains')
            if row['kind']=='pubmed_literature_candidate' and urlsplit(url).hostname!='www.ebi.ac.uk':
                raise ValueError('Unapproved literature index source')
            digest=source.get('sha256')
            if (item.get('nct_id')!=row['nct'] or item.get('kind')!=row['kind']
                    or row['source_url'] not in (url,url+'#predome-trial='+row['nct'])
                    or not re.fullmatch(r'[a-f0-9]{64}',str(digest)) or digest!=row['sha256']):
                raise ValueError('Acquired candidate identity differs from its source record')
            if not re.fullmatch(r'NCT[0-9]{8}',row['nct']):raise ValueError('Invalid acquired registry identity')
            if instant(source.get('retrieved_at'))>datetime.now(timezone.utc):raise ValueError('Future acquisition time')
            blob=db.execute('SELECT payload FROM intake_source_blobs WHERE sha256=?',(digest,)).fetchone()
            if blob:
                if len(blob['payload'])>12*1024*1024:raise ValueError('Source archive exceeds explicit bound')
                raw=base64.b64decode(unpack(blob['payload'])['bytes_b64'],validate=True)
            else:
                text=item.get('raw_text')
                if not isinstance(text,str):raise ValueError('Missing acquired original source')
                raw=text.encode('utf-8')
            if not 0<len(raw)<=6*1024*1024 or hashlib.sha256(raw).hexdigest()!=digest:
                raise ValueError('Acquired original source bytes failed integrity')
            total_bytes+=len(raw)
            if total_bytes>32*1024*1024:raise ValueError('Selected source corpus exceeds 32 MiB; partition before reading')
            structured=item.get('structured',{})
            if row['kind'] in ('registry_update','reported_results_candidate'):
                value=json.loads(raw)
                if value.get('protocolSection',{}).get('identificationModule',{}).get('nctId')!=row['nct']:
                    raise ValueError('Original registry response has a different identity')
                from emeris_live.intake import registry_summary
                structured=registry_summary(value)
                expected='reported_results_candidate' if structured['has_results'] else 'registry_update'
                if expected!=row['kind']:raise ValueError('Registry candidate kind differs from original bytes')
            values.append({'id':row['id'],'nct_id':row['nct'],'kind':row['kind'],
                'url':url,'sha256':digest,'retrieved_at':source['retrieved_at'],
                'bytes':len(raw),'raw':raw,'structured':structured})
    return values
