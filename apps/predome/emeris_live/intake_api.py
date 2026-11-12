from datetime import date,datetime,timezone
import uuid,os,math,hashlib,re,subprocess
from fastapi import APIRouter,Request,HTTPException,Query,Response
from pydantic import BaseModel,ConfigDict,Field,model_validator
from . import intake
from .platform_api import reviewer,page_response
from .platform_store import PlatformStore,instant,valid_url
router=APIRouter(prefix='/api/intake')

@router.get('/status')
def status(request:Request):
    history=intake.jobs(request.app.state.store)
    return {'latest':history[0] if history else None,'scheduled_read_only':os.environ.get('EMERIS_INTAKE_SCHEDULED')=='true','auto_approval':False,'sources':['ClinicalTrials.gov','PubMed via Europe PMC','Bounded official sponsor news and guidance'],'note':'Checks produce review candidates, never silently replace locked forecasts.'}

@router.get('/candidates')
def candidates(request:Request,limit:int=Query(default=20,ge=1,le=100),offset:int=Query(default=0,ge=0)):
    reviewer(request)
    return page_response(intake.list_candidates(request.app.state.store,limit+1,offset),limit,offset)

@router.get('/candidates/{candidate_id}/source')
def original_source(candidate_id:str,request:Request):
    reviewer(request)
    try:raw,source=intake.get_source_bytes(request.app.state.store,candidate_id)
    except KeyError:raise HTTPException(404,'Source candidate not found') from None
    except ValueError:raise HTTPException(409,'原始字节完整性未通过，请核对公开来源。') from None
    suffix='.pdf' if raw.startswith(b'%PDF-') else '.txt'
    return Response(raw,media_type='application/octet-stream',headers={'Content-Disposition':'attachment; filename="source-'+source['source']['sha256'][:16]+suffix+'"'})

@router.get('/candidates/{candidate_id}/suggestions')
def result_suggestions(candidate_id:str,request:Request):
    user=reviewer(request)
    if hasattr(request.app.state,'platform_rate_limit'):
        request.app.state.platform_rate_limit('result-suggestions:'+user['owner'],30,600)
    from .result_suggestions import suggest_candidate
    try:return suggest_candidate(request.app.state.store,candidate_id)
    except KeyError:raise HTTPException(404,'来源候选不存在。') from None
    except (ValueError,TypeError):raise HTTPException(422,'来源完整性、试验身份或结果结构未通过检查；请核对原始来源，不生成替代结论。') from None

class Link(BaseModel):
    model_config=ConfigDict(extra='forbid')
    snapshot_id:str
    article_id:str|None=None
    source_quote:str|None=Field(default=None,min_length=20,max_length=6000)
    source_publication_date:date|None=None
    endpoint:str=Field(min_length=1,max_length=2000)
    population:str=Field(min_length=1,max_length=2000)
    comparison:str=Field(min_length=1,max_length=2000)
    label:int|None=None
    readout_date:str
    effect_size:dict|None=None
    confidence_interval:dict|None=None
    p_value:float|None=Field(default=None,ge=0,le=1)

    @model_validator(mode='after')
    def valid_interval(self):
        ci=self.confidence_interval
        if ci is not None:
            if set(ci)!={'level','lower','upper'} or any(
                type(ci[k]) not in (int,float) or not math.isfinite(ci[k]) for k in ci
            ):
                raise ValueError('区间须包含来源明确的置信水平、下限和上限。')
            if not 0<ci['level']<1 or ci['lower']>ci['upper']:
                raise ValueError('置信水平须为0到1之间的小数，且下限不能高于上限。')
        return self

def official_outcome_source(store,candidate_id,body):
    """Recheck captured bytes and a short reviewer-selected source excerpt."""
    from .official_intake import extract,normalize,identity,allowed,tracked_config
    raw,candidate=intake.get_source_bytes(store,candidate_id)
    source=candidate.get('source') or {};structured=candidate.get('structured') or {}
    nct=candidate.get('nct_id');url=source.get('url');sha=source.get('sha256')
    with store.connect() as db:
        stored=db.execute('SELECT nct,kind,source_url,sha256 FROM intake_candidates WHERE id=?',(candidate_id,)).fetchone()
    if (not stored or stored['nct']!=nct or stored['kind']!='official_announcement_candidate'
            or stored['sha256']!=sha or stored['source_url'] not in (url,str(url)+'#predome-trial='+str(nct))
            or not allowed(url) or not valid_url(url) or '#' in url
            or hashlib.sha256(raw).hexdigest()!=sha):
        raise ValueError('官方来源身份或原始字节完整性未通过。')
    quote=normalize(body.source_quote or '')
    if not 20<=len(quote)<=6000:raise ValueError('请提供20到6000字符的连续官方原文摘录。')
    captured_text=structured.get('text')
    if not isinstance(captured_text,str) or quote not in normalize(captured_text):
        raise ValueError('摘录不在保存的官方正文中。')
    # Structured extraction is mutable intake metadata. The immutable source
    # bytes must independently contain the quote; a changed extracted paragraph
    # cannot introduce invented evidence while retaining the old source hash.
    document=extract(raw)
    if quote not in normalize(document['text']):raise ValueError('摘录不在核验原始字节后重新提取的官方正文中。')
    trial=structured.get('trial_name');drug=structured.get('drug')
    identity_record={'nct_id':nct,'trial':trial if isinstance(trial,str) and trial.strip() else '__missing_trial_alias__',
                     'drug':drug if isinstance(drug,str) and drug.strip() else '__missing_drug__'}
    configured=next((r for r in tracked_config() if r['nct_id']==nct),None)
    if configured:identity_record=configured
    matches=identity(quote,identity_record)
    exact_nct=bool(re.search(r'(?<![A-Za-z0-9])'+re.escape(nct)+r'(?![A-Za-z0-9])',quote,re.I))
    if not exact_nct and (not configured or 'trial_alias' not in matches):
        raise ValueError('摘录须包含确切NCT编号或试验简称及药名；只有药名不能关联结果。')
    saved_doc_date=structured.get('published_at') if structured.get('publication_date_basis')=='document_metadata' else None
    doc_date=document.get('published_at')
    if saved_doc_date and doc_date!=saved_doc_date:
        raise ValueError('保存的文档公开日期与原始字节元数据不一致。')
    if doc_date:
        published=date.fromisoformat(doc_date)
        if body.source_publication_date and body.source_publication_date!=published:
            raise ValueError('人工公开日期与文档元数据冲突。')
        basis='document_metadata'
    else:
        if not body.source_publication_date:raise ValueError('文档无已核对公开日期，须明确填写；历史指引日期不能自动作为结果公告日期。')
        published=body.source_publication_date;basis='reviewer_provided_not_independently_verified'
    today=datetime.now(timezone.utc).date()
    if document.get('published_at_utc') and instant(body.readout_date)>instant(document['published_at_utc']):
        raise ValueError('首次公开时间不能晚于原件中已知的带时区发布时间。')
    if published>today or instant(body.readout_date).date()>today:raise ValueError('结果日期或来源公开日期不能在未来。')
    if instant(body.readout_date).date()>published:raise ValueError('结果首次读出日期不能晚于本来源公开日期。')
    if source.get('retrieved_at') and published>instant(source['retrieved_at']).date():
        raise ValueError('来源公开日期不能晚于保存的抓取日期。')
    return {'url':url,'sha256':sha,'published_at':published.isoformat(),'retrieved_at':source.get('retrieved_at'),
            'hash_scope':'source_response_bytes','candidate_id':candidate_id,'candidate_kind':candidate['kind'],
            'excerpt':quote,'excerpt_sha256':hashlib.sha256(quote.encode()).hexdigest(),
            'publication_date_basis':basis,'quote_validation':'continuous_normalized_excerpt_reextracted_from_verified_source_bytes',
            'source_publication_timestamp_utc':document.get('published_at_utc'),
            'first_public_time_validation':'bounded_by_source_metadata_timestamp' if document.get('published_at_utc') else 'manual_time_not_independently_verified',
            'trial_identity_match':['nct'] if exact_nct else ['trial_alias','drug_name']}


@router.post('/candidates/{candidate_id}/link')
def link(candidate_id:str,body:Link,request:Request):
    user=reviewer(request);store=request.app.state.store
    try:source=intake.get_candidate(store,candidate_id)
    except KeyError:raise HTTPException(404,'Candidate not found') from None
    official=source['kind']=='official_announcement_candidate'
    if not official and (body.source_quote is not None or body.source_publication_date is not None):
        raise HTTPException(422,'原文摘录和人工来源公开日期仅用于官方公告候选。')
    if source['kind'] not in ('reported_results_candidate','pubmed_literature_candidate','official_announcement_candidate'):raise HTTPException(422,'登记更新或新发现记录不能作为决定性疗效结果。')
    public_source=dict(source['source'])
    if official:
        if hasattr(request.app.state,'platform_rate_limit'):
            request.app.state.platform_rate_limit('official-outcome-link:'+user['owner'],12,600)
        if body.article_id is not None:raise HTTPException(422,'官方公告无需论文条目编号。')
        try:public_source=official_outcome_source(store,candidate_id,body)
        except (ValueError,TypeError,KeyError,subprocess.SubprocessError):raise HTTPException(422,'官方原始来源、连续摘录、试验身份或公开日期未通过核对。') from None
    elif source['kind']=='pubmed_literature_candidate':
        matches=[a for a in source['structured'].get('articles',[]) if a.get('id')==body.article_id]
        if len(matches)!=1 or not matches[0].get('publication_url') or not matches[0].get('firstPublicationDate'):
            raise HTTPException(422,'请选择一个带原始论文链接和公开日期的检索条目。')
        article=matches[0]
        public_source={'url':article['publication_url'],'published_at':article['firstPublicationDate'],
                       'metadata_source':source['source']['url'],'metadata_sha256':source['source']['sha256'],'title':article.get('title')}
    else:
        posted=source['structured'].get('results_first_post_date') or {}
        if not posted.get('date'):raise HTTPException(422,'结果登记缺少可核对的首次公开日期，保留人工审核。')
        public_source['published_at']=posted['date']
    outcome=body.model_dump(exclude={'snapshot_id','article_id','source_quote','source_publication_date'})|{'trial_id':source['nct_id'],'source':public_source,'extraction_status':'complete' if body.label in (0,1) else 'needs_review','conflicts':[],'intake_candidate_id':candidate_id}
    try:record=PlatformStore(store).submit_outcome(user['owner'],body.snapshot_id,outcome,allow_other_owner=True)
    except (ValueError,KeyError):raise HTTPException(422,'结果与快照不匹配或不可访问；请核对目标定义。') from None
    with store.connect() as db:db.execute('INSERT INTO intake_links VALUES(?,?,?,?,?)',(uuid.uuid4().hex,candidate_id,record['id'],user['owner'],datetime.now(timezone.utc).isoformat()))
    return {'outcome':record,'status':'needs_independent_review','auto_approved':False}
