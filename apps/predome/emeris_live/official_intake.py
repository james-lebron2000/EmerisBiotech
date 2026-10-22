"""Bounded official-source refresh; source matches are never clinical labels."""
from datetime import datetime,timezone
from html.parser import HTMLParser
from urllib.parse import urlsplit,urljoin
from urllib.request import Request,build_opener,HTTPRedirectHandler,HTTPSHandler
from urllib.error import HTTPError,URLError
from pathlib import Path
import base64,hashlib,json,re,ssl,subprocess,sys
from . import intake

ROOT=Path(__file__).resolve().parents[1]
HOSTS={'www.novartis.com','www.gsk.com','www.astrazeneca.com','investor.lilly.com','www.lilly.com','www.daiichisankyo.com'}
HOSTS.update({'www.roche.com','assets.roche.com','www.pfizer.com','cdn.pfizer.com',
              'www.bms.com','news.bms.com','investors.biogen.com','www.sanofi.com','www.sec.gov'})
LISTINGS=[
 'https://www.novartis.com/news/newsroom?type=media_release',
 'https://www.gsk.com/en-gb/media/press-releases/',
 'https://www.astrazeneca.com/media-centre.html?type=pressrelease',
 'https://investor.lilly.com/rss-news-feeds',
 'https://www.roche.com/media/releases',
 'https://www.pfizer.com/news/press-releases',
 'https://news.bms.com/news/default.aspx',
 'https://investors.biogen.com/news-releases',
 'https://www.sanofi.com/en/media-room/press-releases',
]
MAX_BYTES=6*1024*1024
PARSER_VERSION="official-document-v3"
PDF_WALL_TIMEOUT=60  # 12 CPU seconds may require 48 wall seconds at a 25% CPU quota.

def failure_details(source,url,error):
    """Bounded diagnostics, never exception bodies, cookies or efficacy labels."""
    status=error.code if isinstance(error,HTTPError) and type(error.code) is int else None
    headers=error.headers if isinstance(error,HTTPError) else None
    server=headers.get('Server') if headers else None
    server=server[:80] if isinstance(server,str) and re.fullmatch(r'[A-Za-z0-9 ._()/+-]{1,80}',server) else None
    retry=headers.get('Retry-After') if headers else None
    retry=int(retry) if isinstance(retry,str) and retry.isascii() and retry.isdigit() and len(retry)<=5 and int(retry)<=86400 else None
    timeout=isinstance(error,TimeoutError) or isinstance(error,URLError) and isinstance(error.reason,TimeoutError)
    category=('access_denied' if status in (401,403) else 'rate_limited' if status==429 else
              'not_found' if status==404 else 'source_unavailable' if status and 500<=status<=599 else
              'timeout' if timeout else 'parse_timeout' if isinstance(error,subprocess.TimeoutExpired) else
              'parse_error' if isinstance(error,subprocess.CalledProcessError) else 'unavailable')
    return {'source':source,'url':url,'error_type':type(error).__name__,'http_status':status,
            'category':category,'checked_at':intake.now(),'response_server':server,'retry_after_seconds':retry}

def allowed(url):
    p=urlsplit(url)
    try:port=p.port
    except ValueError:return False
    return p.scheme=='https' and p.hostname in HOSTS and not p.username and not p.password and port in (None,443)

class SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        if not allowed(newurl):raise ValueError('Official source redirect left configured domains')
        return super().redirect_request(req,fp,code,msg,headers,newurl)

def fetch_official(url):
    if not allowed(url):raise ValueError('Unapproved official source')
    opener=build_opener(SafeRedirect(),HTTPSHandler(context=ssl.create_default_context()))
    req=Request(url,headers={'User-Agent':'PredomeResearch/1.1.1 (public evidence review)','Accept':'text/html,application/pdf,application/xml;q=0.9'})
    with opener.open(req,timeout=20) as response:
        raw=response.read(MAX_BYTES+1)
        if len(raw)>MAX_BYTES:raise ValueError('Official source exceeds size limit')
        if not allowed(response.url):raise ValueError('Unapproved final source')
        return raw

class Document(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True);self.parts=[];self.links=[];self.meta={};self.skip=0;self.title='';self.in_title=False
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag in ('script','style','noscript'):self.skip+=1
        if tag=='title':self.in_title=True
        if tag=='meta':self.meta[(a.get('property') or a.get('name') or '').lower()]=a.get('content','')
        if tag=='a' and a.get('href'):self.links.append(a['href'])
    def handle_endtag(self,tag):
        if tag in ('script','style','noscript') and self.skip:self.skip-=1
        if tag=='title':self.in_title=False
        if tag in ('p','div','li','h1','h2','h3','tr','section','br'):self.parts.append('\n')
    def handle_data(self,value):
        if self.in_title:self.title+=value
        if not self.skip:self.parts.append(value)

def normalize(value):return ' '.join(str(value).split())
def extract(raw):
    if raw.startswith(b'%PDF-'):
        script='''import sys,io,json
if sys.platform.startswith("linux"):
 import resource
 resource.setrlimit(resource.RLIMIT_AS,(134217728,134217728));resource.setrlimit(resource.RLIMIT_CPU,(12,12))
from pypdf import PdfReader
r=PdfReader(io.BytesIO(sys.stdin.buffer.read(6291457)),strict=True)
if r.is_encrypted:
 from pypdf.constants import UserAccessPermissions
 opened=r.decrypt("");permissions=r.user_access_permissions
 if not opened or permissions is None or not (permissions & UserAccessPermissions.EXTRACT_TEXT_AND_GRAPHICS):
  raise ValueError("pdf_requires_password_or_disallows_text_extraction")
if len(r.pages)>200:raise ValueError("unsupported_pdf")
parts=[];size=0
for page in r.pages:
 t=page.extract_text() or "";size+=len(t)
 if size>500000:raise ValueError("text_too_large")
 parts.append(t)
print(json.dumps({"text":"\\n".join(parts),"title":str((r.metadata or {}).get("/Title", "Official guidance PDF"))}))
'''
        p=subprocess.run([sys.executable,'-c',script],input=raw,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=PDF_WALL_TIMEOUT,check=True)
        data=json.loads(p.stdout);data.update(published_at=None,links=[],content_type='application/pdf');return data
    text=raw.decode('utf-8',errors='replace');doc=Document();doc.feed(text)
    published=next((doc.meta[k] for k in ('article:published_time','datepublished','date','pubdate','dc.date.issued') if doc.meta.get(k)),None)
    if not published:
        match=re.search(r'"datePublished"\s*:\s*"([^"<>]+)"',text)
        if match:published=match.group(1)
    # Dates are metadata only; unknown or ambiguous remains null.
    if published:
        try:
            stamp=datetime.fromisoformat(published.replace('Z','+00:00'))
            published=stamp.date().isoformat() if stamp.date()<=datetime.now(timezone.utc).date() else None
        except ValueError:published=None
    return {'text':normalize(' '.join(doc.parts))[:500000],'title':normalize(doc.title)[:500],
            'published_at':published,'links':doc.links,'content_type':'text/html'}

def initialize_cache(store):
    intake.initialize(store)
    with store.connect() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS official_parse_cache(
            source_url TEXT NOT NULL,sha256 TEXT NOT NULL,parser_version TEXT NOT NULL,
            payload BLOB NOT NULL,PRIMARY KEY(source_url,sha256,parser_version))""")


def complete_document(doc):
    if not isinstance(doc,dict):return False
    if not all(isinstance(doc.get(k),str) for k in ('text','title','content_type')):return False
    if not doc['text'].strip() or len(doc['text'])>500000:return False
    if doc['content_type'] not in ('application/pdf','text/html'):return False
    if not isinstance(doc.get('links'),list) or not all(isinstance(x,str) for x in doc['links']):return False
    if 'published_at' not in doc:return False
    if doc['published_at'] is not None:
        try:
            stamp=datetime.strptime(doc['published_at'],'%Y-%m-%d')
            if stamp.date()>datetime.now(timezone.utc).date():return False
        except (TypeError,ValueError):return False
    return True


def cached_document(store,url,raw,report):
    """Reuse only authenticated extraction bound to freshly fetched, archived bytes.

    Cache contains document metadata, not the candidate's fallback guidance date.
    Thus changing guidance configuration cannot masquerade as document publication.
    """
    if not allowed(url):raise ValueError('Unapproved official URL')
    if not isinstance(raw,bytes) or len(raw)>MAX_BYTES:raise ValueError('Invalid official source bytes')
    sha=hashlib.sha256(raw).hexdigest()
    with store.connect() as db:
        cached=db.execute('SELECT payload FROM official_parse_cache WHERE source_url=? AND sha256=? AND parser_version=?',
                          (url,sha,PARSER_VERSION)).fetchone()
        candidates=db.execute('SELECT payload FROM intake_candidates WHERE sha256=? AND kind=? '
                              'AND (source_url=? OR substr(source_url,1,?)=?) LIMIT 1',
                              (sha,'official_announcement_candidate',url,len(url+'#predome-trial='),url+'#predome-trial=')).fetchall() if cached else []
        blob=db.execute('SELECT payload FROM intake_source_blobs WHERE sha256=?',(sha,)).fetchone() if cached else None
    if cached:
        phase='candidate_linkage'
        try:
            # Never accept the decoded structured text from a legacy candidate.
            linked=False
            for row in candidates:
                item=store.unpack(row['payload'])
                source=item.get('source',{})
                if source.get('url')==url and source.get('sha256')==sha and source.get('hash_scope')=='source_response_bytes':
                    linked=True;break
            if not linked:raise ValueError('No verified candidate linkage')
            phase='source_archive'
            if not blob:raise ValueError('Missing original source blob')
            archived=base64.b64decode(store.unpack(blob['payload'])['bytes_b64'],validate=True)
            if hashlib.sha256(archived).hexdigest()!=sha or archived!=raw:
                raise ValueError('Original source integrity failed')
            phase='extraction_cache'
            entry=store.unpack(cached['payload'])
            if entry.get('parser_version')!=PARSER_VERSION or entry.get('url')!=url or entry.get('sha256')!=sha:
                raise ValueError('Cache identity mismatch')
            doc=entry.get('document')
            if not complete_document(doc):raise ValueError('Incomplete cached document')
            expected_type='application/pdf' if raw.startswith(b'%PDF-') else 'text/html'
            if doc['content_type']!=expected_type:raise ValueError('Cache content type mismatch')
            report['cache_hits']+=1
            return doc,True,sha
        except Exception:
            report['cache_rejections']+=1
            # Retry parsing genuine fetched bytes. Cache rejection is not a readout.
            report['cache_warnings'].append({'url':url,'reason':phase+'_verification_failed',
                'action':'reparse_fresh_response'})
            if phase=='source_archive':
                report['failures'].append({'source':'official_source_archive','url':url,'error_type':'SourceIntegrityError'})
    report['parser_runs']+=1
    doc=extract(raw)
    if not complete_document(doc):raise ValueError('Incomplete source extraction')
    entry={'parser_version':PARSER_VERSION,'url':url,'sha256':sha,'document':doc,'parsed_at':intake.now()}
    with store.connect() as db:
        db.execute('INSERT OR REPLACE INTO official_parse_cache VALUES(?,?,?,?)',
                   (url,sha,PARSER_VERSION,store.pack(entry)))
        # Bound cache to current parsed version per URL; immutable candidates stay intact.
        db.execute('DELETE FROM official_parse_cache WHERE source_url=? AND (sha256<>? OR parser_version<>?)',
                   (url,sha,PARSER_VERSION))
    return doc,False,sha

def tracked_config():
    original=json.loads((ROOT/'dashboard_data/evidence_updates.json').read_text())
    records=[{'nct_id':t['nct_id'],'trial':t['trial'],'drug':t['drug'],
             'sources':[s for s in t['sources'] if allowed(s['url'])]} for t in original['trials']]
    extra_path=ROOT/'dashboard_data/official_watch_sources.json'
    if not extra_path.exists():return records
    extra=json.loads(extra_path.read_text())
    if extra.get('schema_version')!='predome.official_watch_sources.v1' or not isinstance(extra.get('records'),list):
        raise ValueError('Invalid additional official source configuration')
    index={r['nct_id']:r for r in records}
    for item in extra['records']:
        record=index.get(item.get('nct_id'))
        if not record or any(item.get(k)!=record[k] for k in ('trial','drug')):
            raise ValueError('Additional official source trial identity mismatch')
        for source in item.get('sources',[]):
            path=(ROOT/source['path']).resolve()
            if (not allowed(source['url']) or not path.is_relative_to(ROOT.resolve())
                    or hashlib.sha256(path.read_bytes()).hexdigest()!=source['sha256']
                    or source.get('source_role')!='additional_official_watch_source'):
                raise ValueError('Additional official source integrity mismatch')
            if source.get('source_date'):
                published=datetime.strptime(source['source_date'],'%Y-%m-%d').date()
                if published>datetime.now(timezone.utc).date():raise ValueError('Future additional source date')
            if not any(s['url']==source['url'] for s in record['sources']):record['sources'].append(source)
    return records

def identity(text,record):
    body=text.lower();matches=[]
    if record['nct_id'].lower() in body:matches.append('nct')
    drug=record['drug'].lower() in body
    alias=bool(re.search(r'(?<![a-z0-9])'+re.escape(record['trial'].lower())+r'(?![a-z0-9-])',body))
    if alias and drug:matches.append('trial_alias')
    if drug:matches.append('drug_name')
    return matches

def watch_config(store):
    """Current verified formal registrations precede legacy source configuration.

    Readout URLs are watch seeds, not outcome evidence or frozen-input updates.
    A newly fetched response gets its own byte hash and acquisition time.
    """
    from .arena_watch import current_targets
    from .arena_public import read_frozen
    records=[]
    for target in current_targets(read_frozen(store)):
        readout=target.get('readout',{})
        url=readout.get('source_url')
        if not allowed(url or '') or not re.fullmatch('[a-f0-9]{64}',str(readout.get('source_sha256',''))):
            raise ValueError('Formal watch requires an approved, registered source identity')
        date=readout.get('guidance_as_of')
        if date is not None:
            if not isinstance(date,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',date):
                raise ValueError('Invalid formal guidance date')
            if datetime.strptime(date,'%Y-%m-%d').date()>datetime.now(timezone.utc).date():
                raise ValueError('Future formal guidance date')
        if not all(isinstance(target.get(k),str) and target[k].strip() for k in ('trial_name','drug')):
            raise ValueError('Missing formal trial identity')
        records.append({'nct_id':target['nct_id'],'trial':target['trial_name'],'drug':target['drug'],
                        'sources':[{'url':url,'source_date':date}], 'configuration_basis':'verified_formal_registration'})
    index={r['nct_id']:r for r in records}
    for legacy in tracked_config():
        formal=index.get(legacy['nct_id'])
        if formal is None:
            records.append(legacy);continue
        if any(formal[k]!=legacy[k] for k in ('trial','drug')):continue
        for source in legacy['sources']:
            if not any(source['url']==s['url'] for s in formal['sources']):formal['sources'].append(source)
    return records

def collect_official(store,ncts,fetcher=fetch_official,config=None,listings=None):
    initialize_cache(store)
    report={'sources_checked':0,'new_candidates':0,'failures':[],
            'cache_hits':0,'parser_runs':0,'cache_rejections':0,'cache_warnings':[],'source_checks':[],
            'parser_version':PARSER_VERSION,
            'coverage':'Configured official guidance plus first-level matching news links; no whole-web or full-archive claim.',
            'matching_is_not_result_verification':True}
    try:
        selected={r['nct_id']:r for r in (config if config is not None else watch_config(store)) if r['nct_id'] in ncts}
        records=[selected[n] for n in dict.fromkeys(ncts) if n in selected]
    except (OSError,ValueError,TypeError,KeyError) as error:
        report['failures'].append(failure_details('official_source_configuration',None,error)|{'category':'integrity_error'})
        return report
    report['requested_trial_ids']=list(dict.fromkeys(ncts))
    report['configured_trial_ids']=list(dict.fromkeys(r['nct_id'] for r in records))
    report['unconfigured_trial_ids']=[n for n in report['requested_trial_ids'] if n not in report['configured_trial_ids']]
    queue={};dates={}
    for r in records:
        for source in r['sources']:
            if allowed(source['url']):queue[source['url']]=True;dates[source['url']]=source.get('source_date')
    for listing in (LISTINGS if listings is None else listings):
        try:
            if not allowed(listing):raise ValueError('Unapproved official listing')
            raw=fetcher(listing);checked_at=intake.now()
            report['parser_runs']+=1;doc=extract(raw);report['sources_checked']+=1
            report['source_checks'].append({'url':listing,'checked_at':checked_at,'sha256':hashlib.sha256(raw).hexdigest(),'cache_hit':False})
            for href in doc['links']:
                url=urljoin(listing,href).split('#')[0]
                if not allowed(url) or url==listing:continue
                slug=url.lower()
                if not any(r['drug'].lower().replace(' ','-') in slug or r['trial'].lower() in slug for r in records):continue
                if '/news/' in slug or '/news-release' in slug or '/press-releases/' in slug:queue.setdefault(url,True)
        except Exception as e:report['failures'].append(failure_details('official_listing',listing,e))
    scheduled=list(queue)[:16]
    report['scheduled_document_urls']=scheduled
    report['deferred_document_urls']=list(queue)[16:]
    report['all_discovered_documents_scheduled']=not report['deferred_document_urls']
    report['configured_source_urls']={r['nct_id']:[s['url'] for s in r['sources'] if allowed(s['url'])] for r in records}
    checked=[]
    for url in scheduled:
        try:
            if not allowed(url):raise ValueError('Unapproved official URL')
            raw=fetcher(url);checked_at=intake.now()
            doc,hit,sha=cached_document(store,url,raw,report);report['sources_checked']+=1
            checked.append(url)
            report['source_checks'].append({'url':url,'checked_at':checked_at,'sha256':sha,'cache_hit':hit})
            if not doc['text'].strip():raise ValueError('No source text extracted')
            for r in records:
                matches=identity(doc['text'],r)
                if not matches:continue
                source_date=doc['published_at'] or dates.get(url)
                structured={k:doc[k] for k in ('text','title','content_type')}
                structured.update(parser_version=PARSER_VERSION,published_at=source_date,publication_date_basis='document_metadata' if doc['published_at'] else 'previously_source_checked_guidance_date' if source_date else 'unknown',
                    trial_name=r['trial'],drug=r['drug'],trial_identity_match=matches,label=None,date_kind=None,
                    note='Official source text match only. Reviewer must identify the exact trial, target and readout statement; no date or efficacy label is inferred.')
                _,created=intake.save_candidate(store,r['nct_id'],'official_announcement_candidate',url,raw,structured,
                    source_key=url+'#predome-trial='+r['nct_id'],hash_scope='source_response_bytes')
                report['new_candidates']+=int(created)
        except Exception as e:report['failures'].append(failure_details('official_document',url,e))
    report['checked_document_urls']=checked
    report['trials_with_retrieved_configured_source']=[r['nct_id'] for r in records if any(s['url'] in checked for s in r['sources'])]
    report['trials_without_retrieved_configured_source']=[r['nct_id'] for r in records if not any(s['url'] in checked for s in r['sources'])]
    return report
