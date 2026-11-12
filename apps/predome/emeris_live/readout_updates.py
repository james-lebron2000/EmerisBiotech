"""Reviewed calendar overlays. Original prediction snapshots are never rewritten."""
from datetime import date, datetime, timezone
import calendar
import hashlib
import json
import re
import secrets
import subprocess
from html import unescape
from html.parser import HTMLParser
from .platform_store import canonical, digest, instant, valid_url, page_sql
from .intake import get_candidate, get_source_bytes

CONFIRMATIONS=('readout_not_completion','source_quote_supports_date','trial_identity_confirmed','primary_source_checked','precision_correct')
KINDS={'official_announcement_candidate','pubmed_literature_candidate','reported_results_candidate','registry_update','discovery_requires_readout_source'}
FIELDS={'nct_id','candidate_id','date_kind','start','end','precision','label','quoted_evidence','reason'}
ARENA_FIELDS={'arena_target_id','source_granularity','applicability'}
ARENA_CONFIRMATION='exact_target_applicability_confirmed'


def now():return datetime.now(timezone.utc).isoformat()


class _Text(HTMLParser):
    def __init__(self):super().__init__();self.parts=[]
    def handle_data(self,data):self.parts.append(data)


def normalized(value):return ' '.join(unescape(str(value)).split())


def strings(value):
    if isinstance(value,str):yield value
    elif isinstance(value,dict):
        for item in value.values():yield from strings(item)
    elif isinstance(value,list):
        for item in value:yield from strings(item)


def contains_quote(value,quote):
    return any(quote in normalized(text) for text in strings(value))


def source_record(store,candidate_id,nct,quote):
    """Bind a fetched candidate, not client-supplied provenance or invented hashes."""
    candidate=get_candidate(store,candidate_id)
    if candidate.get('nct_id')!=nct:raise ValueError('Candidate trial identity mismatch')
    if candidate.get('kind') not in KINDS:raise ValueError('Unsupported source candidate kind')
    raw=candidate.get('raw_text')
    source=candidate.get('source') or {};structured=candidate.get('structured') or {}
    if not isinstance(raw,str) or not raw:raise ValueError('Stored source body required')
    sha=source.get('sha256')
    with store.connect() as db:
        stored=db.execute('SELECT nct,kind,source_url,sha256 FROM intake_candidates WHERE id=?',(candidate_id,)).fetchone()
    allowed_keys={source.get('url'),str(source.get('url'))+'#predome-trial='+nct}
    if not stored or stored['nct']!=nct or stored['kind']!=candidate['kind'] or stored['sha256']!=sha or stored['source_url'] not in allowed_keys:
        raise ValueError('Source metadata does not match server intake record')
    if not isinstance(sha,str) or not re.fullmatch(r'[0-9a-f]{64}',sha):raise ValueError('Captured source SHA-256 required')
    if not valid_url(source.get('url')):raise ValueError('HTTPS source URL required')
    official=candidate['kind']=='official_announcement_candidate'
    extracted=None
    document=None
    # Verify the original response bytes, including binary PDFs, from the
    # encrypted source blob. Legacy UTF-8 sources have a checked fallback.
    original_bytes,captured=get_source_bytes(store,candidate_id)
    if captured!=candidate or hashlib.sha256(original_bytes).hexdigest()!=sha:
        raise ValueError('Captured source changed while loading its original bytes')
    if official:
        from .official_intake import extract,identity,watch_config
        try:document=extract(original_bytes)
        except (ValueError,TypeError,KeyError,subprocess.SubprocessError) as exc:
            raise ValueError('Official source could not be re-extracted from verified original bytes') from exc
        extracted=document.get('text')
        if not isinstance(extracted,str) or quote not in normalized(extracted):
            raise ValueError('Quoted readout evidence not present in verified original source bytes')
        # Intake metadata is not an immutable source: reject alterations instead
        # of authorizing a quote merely because someone changed structured.text.
        if not isinstance(structured.get('text'),str) or normalized(structured['text'])!=normalized(extracted):
            raise ValueError('Captured source changed after proposal or extraction differs from original bytes')
        exact_nct=bool(re.search(r'(?<![a-z0-9])'+re.escape(nct)+r'(?![a-z0-9])',quote,re.I))
        config=next((record for record in watch_config(store) if record['nct_id']==nct),None) if not exact_nct else None
        alias_match=bool(config and 'trial_alias' in identity(quote,config))
        if not exact_nct and not alias_match:
            raise ValueError('Official quote must identify the exact NCT or configured trial alias and drug')
    parsed=None
    if not official:
        text=_Text();text.feed(raw)
        try:parsed=json.loads(raw)
        except (ValueError,TypeError):pass
        if quote not in normalized(raw) and quote not in normalized(' '.join(text.parts)) and not contains_quote(parsed,quote):
            raise ValueError('Quoted readout evidence not present in fetched source')
    published=source.get('published_at') or structured.get('published_at')
    publication_basis='captured_source_publication_metadata'
    if official:
        document_date=document.get('published_at')
        if document_date:
            if published and instant(published).date()!=instant(document_date).date():
                raise ValueError('Captured publication date differs from original document metadata')
            published=document_date;publication_basis='document_metadata'
        else:
            # Financial guidance PDFs usually have no machine publication
            # metadata. Retain the captured guidance date as such; it never
            # becomes an actual result date or a newly inferred publication.
            publication_basis='captured_guidance_date_not_readout_date'
    if not published and candidate['kind']=='pubmed_literature_candidate':
        articles=[a for a in structured.get('articles',[]) if contains_quote(a,quote)]
        if len(articles)==1:
            published=articles[0].get('firstPublicationDate')
            publication_basis='matched_article_publication_date_not_readout_date'
    if not published and isinstance(parsed,dict):
        status=parsed.get('protocolSection',{}).get('statusModule',{})
        published=(status.get('lastUpdatePostDateStruct') or {}).get('date')
        publication_basis='registry_last_update_date_not_readout_date'
    if not published:raise ValueError('Captured source publication date required')
    retrieved=source.get('retrieved_at')
    if instant(published)>instant(now()) or instant(retrieved)>instant(now()):raise ValueError('Future source provenance date')
    if instant(published)>instant(retrieved):raise ValueError('Source publication date after retrieval')
    return {'candidate_id':candidate_id,'candidate_kind':candidate['kind'],
            'url':source['url'],'sha256':sha,
            'hash_scope':source.get('hash_scope') or structured.get('hash_scope') or 'source_response_bytes',
            'published_at':published,'publication_date_basis':publication_basis,'retrieved_at':retrieved,
            'quote_text_sha256':hashlib.sha256((extracted if official else raw).encode('utf-8')).hexdigest(),
            'quote_text_hash_scope':'server_extracted_text' if official else 'captured_utf8_source_body'}


def validate_window(proposal):
    kind=proposal.get('date_kind');precision=proposal.get('precision')
    if kind=='readout_time_unconfirmed' and ARENA_FIELDS<=set(proposal):
        if proposal.get('start') is not None or proposal.get('end') is not None or precision!='unknown':
            raise ValueError('Unconfirmed Arena readout requires null start/end and unknown precision')
        return
    if kind not in ('official_exact_date','estimated_window'):
        raise ValueError('Only readout exact dates or estimated windows are allowed; completion dates are not readout dates')
    values=[]
    for key in ('start','end'):
        value=proposal.get(key)
        if not isinstance(value,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',value):raise ValueError('Calendar dates must use YYYY-MM-DD')
        values.append(date.fromisoformat(value))
    start,end=values
    if end<start:raise ValueError('Window ends before it starts')
    if kind=='official_exact_date':
        if precision!='day' or start!=end:raise ValueError('An official exact readout date requires equal start/end and day precision')
        return
    if precision not in ('month','quarter','half_year','year','range') or start==end:
        raise ValueError('Estimated windows require a nonzero range with supported precision')
    months={'month':1,'quarter':3,'half_year':6,'year':12}
    if precision in months:
        width=months[precision]
        if start.day!=1 or (start.month-1)%width:
            raise ValueError('Window start does not match calendar precision')
        month=start.month+width-1
        expected=date(start.year,month,calendar.monthrange(start.year,month)[1])
        if end!=expected:raise ValueError('Window end does not match calendar precision')


def arena_binding(store,proposal):
    """Bind an operational timing review to the current frozen exact question."""
    from .arena_public import read_frozen
    from .arena_watch import current_targets
    from .arena_store import result_target_binding
    if (proposal.get('source_granularity') not in ('named_trial','exact_analysis')
            or proposal.get('applicability')!='exact_registered_target'):
        raise ValueError('Arena timing requires named-trial or exact-analysis applicability')
    target_id=proposal.get('arena_target_id')
    if not isinstance(target_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',target_id):
        raise ValueError('Invalid Arena target identifier')
    selected=[target for target in current_targets(read_frozen(store)) if target['id']==target_id]
    if len(selected)!=1 or selected[0].get('nct_id')!=proposal.get('nct_id'):
        raise ValueError('Arena timing must identify the exact active registered target and NCT')
    return result_target_binding(selected[0])


def validate_binding(proposal):
    present=ARENA_FIELDS&set(proposal)
    if not present:
        if {'arena_target_binding','arena_target_binding_sha256'}&set(proposal):
            raise ValueError('Arena timing fields must be supplied together')
        return False
    if present!=ARENA_FIELDS:
        raise ValueError('Arena timing fields must be supplied together')
    binding=proposal.get('arena_target_binding')
    from .arena_store import RESULT_BINDING_FIELDS
    if (not isinstance(binding,dict) or set(binding)!=set(RESULT_BINDING_FIELDS)
            or any(not isinstance(binding[key],str) or not binding[key].strip() for key in RESULT_BINDING_FIELDS)
            or binding.get('id')!=proposal['arena_target_id']
            or binding.get('nct_id')!=proposal['nct_id']
            or digest(binding)!=proposal.get('arena_target_binding_sha256')
            or proposal.get('source_granularity') not in ('named_trial','exact_analysis')
            or proposal.get('applicability')!='exact_registered_target'):
        raise ValueError('Arena target binding integrity failed')
    return True


def approved_records(db,unpack):
    """Read verified approvals in one existing DB snapshot without schema writes."""
    required={'readout_update_proposals','readout_update_decisions'}
    tables={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not tables&required:return []
    if not required<=tables:raise ValueError('Incomplete calendar schema')
    records=[]
    rows=db.execute('''SELECT d.*,p.id AS p_id,p.nct_id AS p_nct_id,p.submitted_by AS p_submitted_by,
        p.candidate_id AS p_candidate_id,p.sha256 AS p_sha256,p.payload AS p_payload,p.created AS p_created
        FROM readout_update_decisions d LEFT JOIN readout_update_proposals p ON p.id=d.proposal_id
        ORDER BY d.created DESC,d.rowid DESC''')
    for row in rows:
        proposal=unpack(row['p_payload']) if row['p_id'] is not None else None
        review=unpack(row['payload'])
        if (not isinstance(proposal,dict) or not isinstance(review,dict)
                or digest(proposal)!=row['p_sha256'] or digest(review)!=row['sha256']):
            raise ValueError('Readout record integrity failed')
        if any(proposal.get(field)!=row[column] for field,column in (
                ('id','p_id'),('nct_id','p_nct_id'),('submitted_by','p_submitted_by'),
                ('candidate_id','p_candidate_id'),('created_at','p_created'))):
            raise ValueError('Readout proposal SQL identity mismatch')
        if any(review.get(field)!=row[column] for field,column in (
                ('id','id'),('proposal_id','proposal_id'),('reviewer_id','reviewer_id'),
                ('decision','decision'),('created_at','created'))):
            raise ValueError('Readout decision SQL identity mismatch')
        if (review.get('proposal_sha256')!=row['p_sha256']
                or review.get('decision') not in ('approved','rejected')
                or not isinstance(review.get('reviewer_id'),str) or not review['reviewer_id']
                or review['reviewer_id'].casefold()==str(proposal.get('submitted_by')).casefold()):
            raise ValueError('Review proposal binding mismatch')
        is_arena=validate_binding(proposal)
        if review['decision']!='approved':continue
        confirmations=review.get('confirmations')
        if (not isinstance(confirmations,dict)
                or any(confirmations.get(key) is not True for key in CONFIRMATIONS)
                or is_arena and confirmations.get(ARENA_CONFIRMATION) is not True):
            raise ValueError('Approved readout confirmations incomplete')
        validate_window(proposal)
        records.append({'proposal':proposal,'review':review,'proposal_sha256':row['p_sha256'],
                        'decision_sha256':row['sha256']})
    return records


class ReadoutUpdateStore:
    def __init__(self,store):
        self.store=store
        with store.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS readout_update_proposals(id TEXT PRIMARY KEY,nct_id TEXT NOT NULL,submitted_by TEXT NOT NULL,candidate_id TEXT NOT NULL,sha256 TEXT NOT NULL,payload BLOB NOT NULL,created TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS readout_update_decisions(id TEXT PRIMARY KEY,proposal_id TEXT NOT NULL UNIQUE,reviewer_id TEXT NOT NULL,decision TEXT NOT NULL,sha256 TEXT NOT NULL,payload BLOB NOT NULL,created TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS readout_proposals_order ON readout_update_proposals(created DESC,id DESC);
            CREATE INDEX IF NOT EXISTS readout_decisions_order ON readout_update_decisions(decision,created);
            ''')
            for table in ('readout_update_proposals','readout_update_decisions'):
                for action in ('UPDATE','DELETE'):
                    db.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_no_{action.lower()} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'append-only readout review'); END")

    def decode(self,row):
        value=self.store.unpack(row['payload'])
        if digest(value)!=row['sha256']:raise ValueError('Readout record integrity failed')
        return value

    def propose(self,reviewer_id,proposal):
        if not isinstance(reviewer_id,str) or not reviewer_id:raise ValueError('Reviewer identity required')
        if not isinstance(proposal,dict) or set(proposal) not in (FIELDS,FIELDS|ARENA_FIELDS):raise ValueError('Proposal fields must match the review schema; source provenance is server supplied')
        if len(canonical(proposal).encode())>24000:raise ValueError('Proposal too large')
        proposal=dict(proposal)
        if not isinstance(proposal['nct_id'],str) or not re.fullmatch(r'NCT\d{8}',proposal['nct_id']):raise ValueError('Invalid trial identifier')
        for key,low,high in (('candidate_id',1,100),('label',1,160),('quoted_evidence',20,6000),('reason',10,3000)):
            value=proposal[key]
            if not isinstance(value,str) or not low<=len(value.strip())<=high:raise ValueError('Invalid '+key)
            proposal[key]=value.strip()
        validate_window(proposal)
        is_arena=ARENA_FIELDS<=set(proposal)
        if is_arena:
            binding=arena_binding(self.store,proposal)
            proposal.update(arena_target_binding=binding,arena_target_binding_sha256=digest(binding))
        quote=normalized(proposal['quoted_evidence'])
        proposal['source']=source_record(self.store,proposal['candidate_id'],proposal['nct_id'],quote)
        proposal.update({'id':'ru_'+secrets.token_hex(12),'created_at':now(),'submitted_by':reviewer_id,
                         'provenance':'authenticated_account_proposed_operational_calendar_update','quoted_evidence':quote,
                         'review_scope':'operational_calendar_review_not_clinical_scientific_adjudication',
                         'calendar_effect':'none_until_distinct_account_approved'})
        with self.store.connect() as db:
            db.execute('INSERT INTO readout_update_proposals VALUES(?,?,?,?,?,?,?)',(proposal['id'],proposal['nct_id'],reviewer_id,proposal['candidate_id'],digest(proposal),self.store.pack(proposal),proposal['created_at']))
        return self.get(proposal['id'],reviewer_id)

    def get(self,proposal_id,actor_id=None):
        with self.store.connect() as db:
            row=db.execute('SELECT * FROM readout_update_proposals WHERE id=?',(proposal_id,)).fetchone()
            if not row:raise KeyError(proposal_id)
            decision=db.execute('SELECT * FROM readout_update_decisions WHERE proposal_id=?',(proposal_id,)).fetchone()
            return self._view(row,decision,actor_id)

    def _view(self,row,decision,actor_id):
        proposal=self.decode(row);review=self.decode(decision) if decision else None
        return {'id':row['id'],'created_at':row['created'],'submitted_by':row['submitted_by'],'proposal':proposal,
                'status':review['decision'] if review else 'pending_review','decision':review,
                'can_review':bool(actor_id and actor_id.casefold()!=row['submitted_by'].casefold() and not review),'sha256':row['sha256']}

    def list(self,actor_id,limit=20,offset=0):
        suffix,args=page_sql(limit,offset)
        with self.store.connect() as db:
            rows=db.execute('SELECT * FROM readout_update_proposals ORDER BY created DESC,id DESC'+suffix,args)
            return [self._view(row,db.execute('SELECT * FROM readout_update_decisions WHERE proposal_id=?',(row['id'],)).fetchone(),actor_id) for row in rows]

    def review(self,reviewer_id,proposal_id,decision,reason,confirmations=None):
        if decision not in ('approved','rejected'):raise ValueError('Decision must be approved or rejected')
        if not isinstance(reason,str) or not 10<=len(reason.strip())<=3000:raise ValueError('Review reason must contain 10–3000 characters')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM readout_update_proposals WHERE id=?',(proposal_id,)).fetchone()
            if not row:raise KeyError(proposal_id)
            proposal=self.decode(row)
            if not isinstance(reviewer_id,str) or not reviewer_id or reviewer_id.casefold()==row['submitted_by'].casefold():raise ValueError('Submitting reviewer cannot review their own update')
            if db.execute('SELECT id FROM readout_update_decisions WHERE proposal_id=?',(proposal_id,)).fetchone():raise ValueError('Decision is immutable; submit a new proposal')
            if decision=='approved':
                if any((confirmations or {}).get(key) is not True for key in CONFIRMATIONS):raise ValueError('All independent readout confirmations are required')
                validate_window(proposal)
                is_arena=validate_binding(proposal)
                if is_arena:
                    if (confirmations or {}).get(ARENA_CONFIRMATION) is not True:
                        raise ValueError('Exact target applicability confirmation is required')
                    binding=arena_binding(self.store,proposal)
                    if binding!=proposal['arena_target_binding'] or digest(binding)!=proposal['arena_target_binding_sha256']:
                        raise ValueError('Arena target binding changed after proposal')
                current=source_record(self.store,proposal['candidate_id'],proposal['nct_id'],proposal['quoted_evidence'])
                if current!=proposal['source']:raise ValueError('Captured source changed after proposal')
            review={'id':'rd_'+secrets.token_hex(12),'proposal_id':proposal_id,'reviewer_id':reviewer_id,
                    'decision':decision,'reason':reason.strip(),'confirmations':confirmations or {},'created_at':now(),
                    'proposal_sha256':row['sha256'],'timestamp_attestation':'server_clock_not_independent_timestamp',
                    'review_scope':'operational_calendar_review_not_clinical_scientific_adjudication',
                    'identity_assurance':'authenticated_account_not_verified_scientific_credentials'}
            db.execute('INSERT INTO readout_update_decisions VALUES(?,?,?,?,?,?,?)',(review['id'],proposal_id,reviewer_id,decision,digest(review),self.store.pack(review),review['created_at']))
        return self.get(proposal_id,reviewer_id)


def approved_public_updates(store):
    """Latest approved overlay per trial; pending/rejected records never publish."""
    result={}
    with store.connect() as db:
        for record in approved_records(db,store.unpack):
            proposal=record['proposal'];review=record['review']
            if ARENA_FIELDS&set(proposal) or proposal['nct_id'] in result:continue
            result[proposal['nct_id']]={k:proposal[k] for k in ('nct_id','date_kind','start','end','precision','label','quoted_evidence')} | {
                'source':proposal['source'],'source_url':proposal['source']['url'],
                'published_at':proposal['source']['published_at'],'retrieved_at':proposal['source']['retrieved_at'],
                'source_sha256':proposal['source']['sha256'],'hash_scope':proposal['source']['hash_scope'],
                'provenance':{'kind':'independently_reviewed_readout_calendar_overlay','proposal_id':proposal['id'],
                    'proposal_sha256':record['proposal_sha256'],'decision_id':review['id'],'decision_sha256':record['decision_sha256'],
                    'approved_at':review['created_at'],'original_prediction_unchanged':True,
                    'review_scope':'operational_calendar_review_not_clinical_scientific_adjudication',
                    'timestamp_attestation':'server_clock_not_independent_timestamp'}}
    return result
