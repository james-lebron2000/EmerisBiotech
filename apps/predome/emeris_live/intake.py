"""Bounded public-source intake. Discovery is never an efficacy adjudication."""
from datetime import datetime,timezone
from pathlib import Path
import hashlib,json,re,uuid,base64
from urllib.parse import urlencode
from urllib.request import Request,urlopen
from defusedxml import ElementTree as ET
from .trial_documents import document_inventory

NCT=re.compile(r'^NCT[0-9]{8}$')
def now():return datetime.now(timezone.utc).isoformat()
def fetch(url):
    req=Request(url,headers={'User-Agent':'PredomeResearch/1.1 (public evidence review)','Accept':'application/json, application/xml;q=0.9'})
    with urlopen(req,timeout=25) as r:
        raw=r.read(6*1024*1024+1)
        if len(raw)>6*1024*1024:raise ValueError('Source exceeds 6 MiB')
        return raw

def initialize(store):
    with store.connect() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS intake_candidates(id TEXT PRIMARY KEY,nct TEXT,kind TEXT,source_url TEXT,sha256 TEXT,created TEXT,payload BLOB,UNIQUE(source_url,sha256));
        CREATE TABLE IF NOT EXISTS intake_jobs(id TEXT PRIMARY KEY,started TEXT,completed TEXT,payload BLOB);
        CREATE TABLE IF NOT EXISTS intake_source_blobs(sha256 TEXT PRIMARY KEY,payload BLOB NOT NULL);
        CREATE TABLE IF NOT EXISTS intake_links(id TEXT PRIMARY KEY,candidate_id TEXT,outcome_id TEXT,actor TEXT,created TEXT);
        ''')

def save_candidate(store,nct,kind,url,raw,structured,source_key=None,hash_scope=None):
    if not NCT.fullmatch(nct):raise ValueError('Invalid trial id')
    h=hashlib.sha256(raw).hexdigest();id=uuid.uuid4().hex;source_key=source_key or url
    doc={'nct_id':nct,'kind':kind,'source':{'url':url,'sha256':h,'retrieved_at':now()},'structured':structured,
         'raw_text':raw.decode('utf-8',errors='replace') if not raw.startswith(b'%PDF-') else '[binary PDF; parsed text retained in structured.text]','review_status':'needs_review','auto_verified':False}
    if hash_scope:doc['source']['hash_scope']=hash_scope
    with store.connect() as db:
        old=db.execute('SELECT id FROM intake_candidates WHERE source_url=? AND sha256=?',(source_key,h)).fetchone()
        if old:return old['id'],False
        if hash_scope=='source_response_bytes':
            db.execute('INSERT OR IGNORE INTO intake_source_blobs VALUES(?,?)',(h,store.pack({'bytes_b64':base64.b64encode(raw).decode()})))
        db.execute('INSERT INTO intake_candidates VALUES(?,?,?,?,?,?,?)',(id,nct,kind,source_key,h,now(),store.pack(doc)))
    return id,True

def list_candidates(store,limit=100,offset=0):
    initialize(store)
    with store.connect() as db:
        rows=db.execute('SELECT * FROM intake_candidates ORDER BY created DESC,id DESC LIMIT ? OFFSET ?',(min(max(limit,1),200),max(offset,0))).fetchall()
    return [{k:v for k,v in _candidate_projection(store,r).items() if k!='raw_text'} for r in rows]

def get_candidate(store,id):
    initialize(store)
    with store.connect() as db:r=db.execute('SELECT * FROM intake_candidates WHERE id=?',(id,)).fetchone()
    if not r:raise KeyError(id)
    return _candidate_projection(store,r)


def _source_bytes(store,source):
    """Read archived bytes or the existing legacy text fallback, without recursion."""
    with store.connect() as db:row=db.execute('SELECT payload FROM intake_source_blobs WHERE sha256=?',(source['source']['sha256'],)).fetchone()
    if row:
        raw=base64.b64decode(store.unpack(row['payload'])['bytes_b64'],validate=True)
    else:
        raw=source['raw_text'].encode('utf-8')
    if hashlib.sha256(raw).hexdigest()!=source['source']['sha256']:raise ValueError('Original source bytes unavailable or integrity check failed')
    return raw


def _candidate_projection(store,row):
    """Derive registry inventory from verified originals; never update old payloads."""
    source={'id':row['id'],'created':row['created'],**store.unpack(row['payload'])}
    if source.get('kind') not in ('registry_update','reported_results_candidate'):
        return source
    structured=dict(source['structured']) if isinstance(source.get('structured'),dict) else {}
    digest=row['sha256'] if isinstance(row['sha256'],str) and re.fullmatch(r'[a-f0-9]{64}',row['sha256']) else None
    projection={'schema_version':'predome.registry-document-projection-1',
                'status':'source_unavailable','source_sha256':digest}
    structured.update(study_documents=None,study_documents_projection=projection)
    source={**source,'structured':structured}
    try:
        nct=source['nct_id'];metadata=source['source']
        if (not isinstance(nct,str) or not NCT.fullmatch(nct) or nct!=row['nct']
                or source['kind']!=row['kind'] or metadata.get('sha256')!=digest
                or metadata.get('url')!='https://clinicaltrials.gov/api/v2/studies/'+nct
                or row['source_url'] not in (metadata['url'],metadata['url']+'#predome-trial='+nct)):
            raise ValueError('Registry source identity mismatch')
        value=json.loads(_source_bytes(store,source))
        if value.get('protocolSection',{}).get('identificationModule',{}).get('nctId')!=nct:
            raise ValueError('Original registry identity mismatch')
        structured['study_documents']=document_inventory(value)
        projection['status']='verified_source_bytes'
    except Exception:
        # Unavailable or corrupt originals are unknown, never an empty listing.
        pass
    return source


def get_source_bytes(store,id):
    source=get_candidate(store,id)
    raw=_source_bytes(store,source)
    return raw,source

def jobs(store):
    initialize(store)
    with store.connect() as db:rows=db.execute('SELECT * FROM intake_jobs ORDER BY started DESC LIMIT 10').fetchall()
    return [{'id':r['id'],'started':r['started'],'completed':r['completed'],**store.unpack(r['payload'])} for r in rows]

def registry_summary(value):
    p=value.get('protocolSection',{});status=p.get('statusModule',{});result=value.get('resultsSection',{})
    return {'trial_name':p.get('identificationModule',{}).get('briefTitle'),'sponsor':p.get('sponsorCollaboratorsModule',{}).get('leadSponsor',{}).get('name'),
            'primary_completion':status.get('primaryCompletionDateStruct'),'study_completion':status.get('completionDateStruct'),
            'readout_date':None,'date_kind':'completion_is_not_readout','has_results':bool(value.get('hasResults')),
            'results_first_post_date':status.get('resultsFirstPostDateStruct'),
            'registered_primary_outcomes':p.get('outcomesModule',{}).get('primaryOutcomes',[]),
            'reported_outcome_measures':result.get('outcomeMeasuresModule',{}).get('outcomeMeasures',[]),
            'study_documents':document_inventory(value),
            'extraction_status':'needs_review','label':None,
            'note':'Reported measures, analyses, effect estimates and P values are retained as source structure. No primary success label is inferred.'}

def run_intake(store,ncts,fetcher=fetch,discover=True,official=False):
    all_requested=list(dict.fromkeys(ncts))
    if any(not isinstance(n,str) or not NCT.fullmatch(n) for n in all_requested):raise ValueError('Invalid tracked trial id')
    requested,deferred=all_requested[:30],all_requested[30:]
    initialize(store);start=now();id=uuid.uuid4().hex;report={'status':'completed','sources_checked':0,'new_candidates':0,'failures':[],'auto_approved':0,'scope':'bounded registered trial watch plus sponsor discovery; not whole-market coverage',
        'requested_ncts':requested,'deferred_ncts':deferred,'tracked_limit':30,'all_requested_ids_scheduled':not deferred,'registry_checked_ncts':[],'literature_checked_ncts':[]}
    result_candidates=[]
    for nct in requested:
        url='https://clinicaltrials.gov/api/v2/studies/'+nct
        try:
            raw=fetcher(url);value=json.loads(raw)
            actual=value.get('protocolSection',{}).get('identificationModule',{}).get('nctId')
            if actual!=nct:raise ValueError('Registry identity mismatch')
            summary=registry_summary(value);kind='reported_results_candidate' if summary['has_results'] else 'registry_update'
            cid,created=save_candidate(store,nct,kind,url,raw,summary);report['new_candidates']+=int(created);report['sources_checked']+=1;report['registry_checked_ncts'].append(nct)
            if kind=='reported_results_candidate':result_candidates.append(cid)
        except Exception as e:report['failures'].append({'nct_id':nct,'source':'ClinicalTrials.gov','error_type':type(e).__name__})
        puburl='https://www.ebi.ac.uk/europepmc/webservices/rest/search?'+urlencode({'query':nct,'format':'json','pageSize':10,'resultType':'core'})
        try:
            raw=fetcher(puburl);value=json.loads(raw)
            hits=[{'id':h.get('id'),'source':h.get('source'),'title':h.get('title'),'firstPublicationDate':h.get('firstPublicationDate'),'doi':h.get('doi'),'abstractText':h.get('abstractText'), 'publication_url':'https://pubmed.ncbi.nlm.nih.gov/'+h['pmid']+'/' if h.get('pmid') else None} for h in value.get('resultList',{}).get('result',[]) if nct in json.dumps(h)]
            if hits:
                _,created=save_candidate(store,nct,'pubmed_literature_candidate',puburl,raw,{'articles':hits,'index':'Europe PMC / PubMed','extraction_status':'needs_review','label':None,'note':'NCT text match is not proof of target primary outcome.'});report['new_candidates']+=int(created)
            report['sources_checked']+=1;report['literature_checked_ncts'].append(nct)
        except Exception as e:report['failures'].append({'nct_id':nct,'source':'PubMed via Europe PMC','error_type':type(e).__name__})
    if discover:
        # A discovery queue only: completion dates never promoted into readout dates.
        url='https://clinicaltrials.gov/api/v2/studies?'+urlencode({'query.spons':'Novartis OR AstraZeneca OR Eli Lilly OR GSK OR Pfizer OR Merck','filter.overallStatus':'ACTIVE_NOT_RECRUITING,RECRUITING','pageSize':20,'format':'json','sort':'LastUpdatePostDate:desc'})
        try:
            raw=fetcher(url);value=json.loads(raw)
            for study in value.get('studies',[]):
                nct=study.get('protocolSection',{}).get('identificationModule',{}).get('nctId','')
                if not NCT.fullmatch(nct):continue
                subset=json.dumps(study,ensure_ascii=False,sort_keys=True).encode()
                _,created=save_candidate(store,nct,'discovery_requires_readout_source',url+'#'+nct,subset,registry_summary(study));report['new_candidates']+=int(created)
            report['sources_checked']+=1
        except Exception as e:report['failures'].append({'source':'bounded sponsor discovery','error_type':type(e).__name__})
    if official:
        from .official_intake import collect_official
        extra=collect_official(store,requested)
        report['official_sources']=extra
        report['sources_checked']+=extra['sources_checked'];report['new_candidates']+=extra['new_candidates'];report['failures'].extend(extra['failures'])
    # Deterministic extraction runs for captured results in this batch. The job
    # retains source-bound digests for review; no outcome or success label is written.
    report['result_suggestions']={'records':[],'auto_approved':0,'writes_actual_outcomes':False}
    if result_candidates:
        from .result_suggestions import suggest_candidate
        for cid in result_candidates:
            try:
                suggestion=suggest_candidate(store,cid)
                report['result_suggestions']['records'].append({
                    'candidate_id':cid,'nct_id':suggestion['nct_id'],
                    'pipeline_version':suggestion['pipeline_version'],'digest':suggestion['digest'],
                    'primary_measure_count':suggestion['primary_measure_count'],
                    'matched_prediction_count':len(suggestion['matched_predictions']),
                    'pairing_count':suggestion['pairing_count'],
                    'pairing_count_total':suggestion.get('pairing_count_total',suggestion['pairing_count']),
                    'truncated':suggestion.get('truncated',False),'status':'needs_review'})
            except Exception as e:
                report['failures'].append({'candidate_id':cid,'source':'registry_result_suggestions','error_type':type(e).__name__})
    if report['failures'] or deferred:report['status']='partial' if report['sources_checked'] else 'failed'
    with store.connect() as db:db.execute('INSERT INTO intake_jobs VALUES(?,?,?,?)',(id,start,now(),store.pack(report)))
    return dict(job_id=id,started=start,**report)

def tracked_trial_ids(store):
    from .product import build_product
    from .platform_store import PlatformStore
    from .arena_watch import current_trial_ids
    # Formal forecasts take priority within the existing bounded daily intake.
    ids=current_trial_ids(store)+[t['nct_id'] for t in build_product()['upcoming']]
    for run in PlatformStore(store).list_public_snapshots():
        record=run.get('record',run);nct=record.get('trial_id')
        if nct and NCT.fullmatch(nct):ids.append(nct)
    return list(dict.fromkeys(ids))

def main(argv=None):
    import argparse,os
    from types import SimpleNamespace
    from .store import Store
    from .arena_watch import current_trial_ids
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir',type=Path,default=Path(os.environ.get('EMERIS_DATA_DIR',str(Path.home()/'.local/share/emeris-live'))))
    parser.add_argument('--arena-only',action='store_true')
    parser.add_argument('--skip-discovery',action='store_true')
    parser.add_argument('--skip-official',action='store_true')
    args=parser.parse_args(argv)
    ids=current_trial_ids(SimpleNamespace(path=args.data_dir)) if args.arena_only else None
    if args.arena_only and not ids:raise ValueError('No existing current formal cohort to collect')
    store=Store(args.data_dir)
    report=run_intake(store,ids if ids is not None else tracked_trial_ids(store),
        discover=not args.skip_discovery,official=not args.skip_official)
    print(json.dumps(report,ensure_ascii=False))
    return report


if __name__=='__main__':main()
