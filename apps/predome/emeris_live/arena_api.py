"""Public read-only views for the Open Forecast challenge and source evidence."""
from copy import deepcopy
from pathlib import Path
import json
import hashlib
import re
from io import BytesIO
import zipfile
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from .arena_store import ArenaLedger, VERSION
from .arena_metrics import build_metrics, patent_pairs
from .arena_providers import list_models
from .arena_report import render_target_report
from .patent_evidence import read_catalog, get_patent
from .product import build_product

router = APIRouter()
ROOT = Path(__file__).resolve().parents[1]
TRACKS = [
    {'id':'same_evidence','name':'同证据 LLM','description':'同一目标与冻结证据，比较底层模型。'},
    {'id':'shared_agent','name':'统一 Agent','description':'固定证据审查和预测工作流，比较模型。'},
    {'id':'open_agent','name':'开放 Agent','description':'受控双模型证据审查与预测；固定证据查阅，分别登记系统组合和预算。'},
    {'id':'patent_ablation','name':'专利增益','description':'同模型同临床证据的配对实验；概率变化不等于改善。'},
    {'id':'archived','name':'既有冻结档案','description':'原始模型记录，保留原冻结时间，不计入新赛季。'},
]


def _source(source, ident):
    return {'id':ident, 'source_url':source.get('url'), 'source_sha256':source.get('sha256'),
            'availability_date':source.get('source_date'), 'retrieved_at':source.get('retrieved_at')}


def archived_views(store, *, product=None):
    product = build_product(store) if product is None else product
    prior = json.loads((ROOT/'predome_data/continuous_v4/prospective/prior_effect_records.json').read_text())['records']
    targets, runs, evidence = [], [], {}
    for trial in product['upcoming'] + product.get('resolved_trials', []):
        sources = trial.get('sources', [])
        registry = _source(sources[0], 'registry-'+trial['nct_id']) if sources else {}
        registry['text'] = json.dumps(trial.get('inputs',{}), ensure_ascii=False)
        registry['kind'] = 'trial_registry'
        registry['title'] = trial['trial_name']+' 注册信息'
        evidence[registry['id']] = registry
        if len(sources) > 1:
            guidance = _source(sources[1], 'guidance-'+trial['nct_id'])
            guidance.update(text='Company guidance: '+trial['window']['label'], kind='readout_guidance', title='公司读出窗口指引')
            evidence[guidance['id']] = guidance
        for item in prior:
            if trial['nct_id'] not in item['target_nct_ids']:
                continue
            value = _source(item['source'], item['record_id'])
            value.update(text=json.dumps({k:v for k,v in item.items() if k!='source'},ensure_ascii=False),
                         kind='clinical_prior', title=item['endpoint'], applicability=item.get('applicability'),
                         human_review_count=item.get('human_review_count',0))
            evidence[value['id']] = value
        arms = trial.get('inputs',{}).get('arms',[])
        comparison = ' vs '.join(a.get('label','') for a in arms)
        for original in trial['targets']:
            ident = 'archive-'+original['case_id']
            definition = original.get('target_definition',[])
            endpoint = '; '.join(d.get('measure','') for d in definition)
            result = original.get('actual_outcome')
            bound = None
            if result:
                bound = {'label':'success' if result['label']==1 else 'failure',
                    'source_url':(result.get('source') or {}).get('url'),
                    'source_sha256':(result.get('source') or {}).get('sha256'),
                    'first_public_at':result.get('readout_date'), 'verification_status':'adjudicated',
                    'effect_size':result.get('effect_size'), 'confidence_interval':result.get('confidence_interval'),
                    'p_value':result.get('p_value'), 'verified_at':result.get('verified_at')}
            t = {'id':ident,'nct_id':trial['nct_id'],'trial_name':trial['trial_name'],'drug':trial['asset'],
                'indication':trial['indication'],'sponsor':trial['company'],'project_id':trial['asset'],
                'endpoint':endpoint, 'population':endpoint+'; '+trial['indication'], 'comparison':comparison,
                'success_definition':original['success_rule'], 'analysis_timepoint':'; '.join(d.get('timeFrame','') for d in definition),
                'data_cutoff':original['evidence_as_of_utc'],'information_cutoff_utc':original['evidence_as_of_utc'],
                'scope':'archived_research','status':'adjudicated' if bound else 'pending',
                'readout':{**trial['window'],'source_url':trial['window'].get('source_url') or (sources[1]['url'] if len(sources)>1 else None),
                           'source_title':'公司原始读出指引','checked_at':trial.get('checked_at')},
                'status_note':trial.get('status_note'),'result':bound,'patent_ids':[],
                'evidence_ids':sorted({e for f in original['forecasts'].values() for e in f.get('evidence_ids',[])})}
            targets.append(t)
            for model, f in original['forecasts'].items():
                cited = [evidence[i] for i in f.get('evidence_ids',[]) if i in evidence]
                runs.append({'id':ident+'-'+model,'target_id':ident,'model_id':model,'track':'archived',
                    'condition':'clinical_only','scope':'archived_research',
                    'status':'abstained' if f['status']=='abstain' else f['status'],'probability':f.get('probability'),
                    'reason':f.get('reasoning') or f.get('reason'),
                    'supporting_evidence':[{'source_id':e['id'],'source_url':e.get('source_url'),'text':e.get('title',e['id'])} for e in cited],
                    'counter_evidence':[], 'assumptions':f.get('assumptions',[]),'unknowns':f.get('key_unknowns',[]),
                    'model_version':f.get('model_version'), 'workflow_version':original.get('pipeline_version'),
                    'input_sha256':None,'archive_record_sha256':original['record_sha256'],
                    'output_sha256':f.get('source_run_record_sha256'), 'generated_at_utc':f.get('generated_at_utc'),
                    'locked_at_utc':original['frozen_at_utc'],'timestamp_proof':{'status':'server_record_only','url':None},
                    'cost_usd':None,'latency_seconds':None,'token_usage':None,'evidence_ids':f.get('evidence_ids',[]),'patent_ids':[]})
    return targets, runs, evidence


def _patent_view(item):
    p = deepcopy(item)
    p.setdefault('id', p.get('publication_number'))
    p.setdefault('sha256', p.get('raw_sha256'))
    p.setdefault('pages', p.get('page_count'))
    mapping = p.get('drug_mapping') or {}
    p.setdefault('drug', mapping.get('drug_name') or mapping.get('candidate_name') or p.get('candidate_drug'))
    p['drug_aliases'] = [x for x in [mapping.get('drug_name'), mapping.get('candidate_name'), p.get('candidate_drug'), *mapping.get('aliases',[])] if isinstance(x,str)]
    p.setdefault('linkage_status', mapping.get('status','unknown'))
    p.setdefault('review_status', (p.get('experiment_cards') or [{}])[0].get('review',{}).get('status') or p.get('review',{}).get('status','not_reviewed'))
    p.setdefault('ocr_status', p.get('extraction_status','unknown'))
    p.setdefault('included_in_predictions', False)
    p.setdefault('excerpts', p.get('page_references',[]))
    return p


def _public_run(run):
    value = {k:deepcopy(v) for k,v in run.items() if k!='packet'}
    value['reason'] = run.get('reasoning') or run.get('reason') or {
        'provider_response_incomplete':'模型未在本次输出上限内完成响应；原始失败计入全部登记任务。',
        'provider_transport_error':'本次模型请求遇到网络传输错误，未得到可用输出；不自动重试。',
        'unrecorded_provider_result_after_contract_rejection':'多阶段响应登记失败；已停止该槽，原响应及计费用量无法恢复，不重新调用替换。',
        'contract_rejected':'响应未通过冻结契约检查，已保留原始回执并记录失败。',
    }.get(run.get('error_code'))
    value['model_version'] = run.get('returned_model') or run.get('requested_model')
    value['assumptions'] = run.get('key_assumptions',run.get('assumptions',[]))
    value['unknowns'] = run.get('key_unknowns',[])
    value['latency_seconds'] = run['latency_ms']/1000 if run.get('latency_ms') is not None else None
    value['token_usage'] = run.get('usage')
    evidence = {e['id']:e for e in run.get('packet',{}).get('evidence',[])}
    def refs(ids):
        return [{'source_id':ident,'text':evidence[ident].get('title',ident),'source_url':evidence[ident]['source_url'],
                 'page':evidence[ident].get('page')} for ident in ids if ident in evidence]
    value['supporting_evidence'] = refs(run.get('supporting_evidence_ids',run.get('evidence_ids',[])))
    value['counter_evidence'] = refs(run.get('opposing_evidence_ids',[]))
    value['patent_ids'] = sorted({e['publication_number'] for e in evidence.values() if e.get('kind')=='patent' and e.get('publication_number')})
    return value


def build_arena(store, *, cohort_id=None, track='same_evidence', condition='clinical_only'):
    ledger = ArenaLedger(store.path).snapshot()
    old_targets, old_runs, evidence = archived_views(store)
    catalog = read_catalog()
    patents = [_patent_view(p) for p in catalog['items']]
    models = list_models()
    known = {m['id'] for m in models}
    for model in sorted({r['model_id'] for r in old_runs} - known):
        models.append({'id':model,'name':model,'provider':'DeepSeek','model_version':model,
                       'available':False,'archive_only':True,'reason':'原始冻结档案标识；本次未实测连接。'})
    for run in ledger['runs']:
        if run.get('track')=='open_agent' and run['model_id'] not in known:
            frozen=run.get('provider_config_snapshot',{})
            components=frozen.get('components',[])
            models.append({'id':run['model_id'],'name':frozen.get('name',run['model_id']),
                'provider':'multi_model_agent','model_version':run.get('requested_model'),
                'available':bool(components) and all(next((m.get('available',False) for m in models if m['id']==c['id']),False) for c in components),
                'components':components,'note':'受控双模型系统：固定证据查阅 → 模型审查 → 另一模型预测。组件返回版本见运行过程，组合哈希仅标识系统。'})
            known.add(run['model_id'])
    if cohort_id is None:
        eligible_cohorts=[c for c in ledger['cohorts'] if c.get('execution_status')!='withdrawn_before_execution'
            and any(r['cohort_id']==c['id'] and r['track']==track and r['condition']==condition for r in ledger['runs'])]
        formal=[c for c in eligible_cohorts if c.get('protocol',{}).get('season_kind')=='registered_12_project_season']
        choice=(formal if track in ('same_evidence','patent_ablation') else []) or eligible_cohorts
        if choice:cohort_id=choice[-1]['id']
    new_runs = [_public_run(r) for r in ledger['runs']]
    for model in models:
        completed = [r for r in new_runs if r['model_id']==model['id'] and r.get('returned_model') and r.get('attempts')]
        model['real_api_verified'] = bool(completed)
        model['verified_runs'] = len(completed)
        model['returned_versions'] = sorted({r['returned_model'] for r in completed})
    pairs = patent_pairs(ledger)
    for pair in pairs:
        for run in new_runs:
            if run['id']==pair['treated_run_id']:
                run.update(paired_run_id=pair['control_run_id'],pair_verified=True)
    for run in ledger['runs']:
        for item in run.get('packet',{}).get('evidence',[]):
            # Every run's own frozen packet is independently downloadable, even
            # if a later packet reuses an evidence ID with a different version.
            evidence.setdefault(item['id'],item)
    for target in old_targets + ledger['targets']:
        target['patent_ids'] = [p['id'] for p in patents if any(target.get('drug','').lower() in alias.lower() or alias.lower() in target.get('drug','').lower() for alias in p.get('drug_aliases',[]) if target.get('drug'))]
    used_patents = {p for r in new_runs for p in r.get('patent_ids',[])}
    for patent in patents:
        patent['included_in_predictions'] = patent['id'] in used_patents
    # Public labels are model IDs; transport adapters stay only in audit metadata.
    for model in models:
        components=model.get('components') or []
        model['name']=' → '.join(c['id'] for c in components) if components else model['id']
    return {'version':VERSION,'capabilities':{'llm_available':any(m.get('available') for m in models),
        'public_run_enabled':False,'operator_run_enabled':True,'open_agent_runner_enabled':True,'clinical_prediction_validated':False,
        'notes':['公开页面展示真实档案；新赛季与既有记录分开。','配置可用不等于已经完成真实 API 验证。',
                 '凭据仅保存在服务端；公共访问不会触发付费调用。']},
        'models':models,'patents':patents,'patent_connectors':catalog.get('connectors',[]),
        'challenge_summary':{'registered_tasks':len(new_runs),'independent_projects':len({t['project_id'] for t in ledger['targets']}),'prospective_targets':len(ledger['targets']),'complete_season_started':any(c.get('protocol',{}).get('season_kind')=='registered_12_project_season' and c.get('execution_status')!='withdrawn_before_execution' for c in ledger['cohorts'])},
        'targets':ledger['targets']+old_targets,'runs':new_runs+old_runs,'evidence':list(evidence.values()),
        'metrics':build_metrics(ledger,cohort_id=cohort_id,track=track,condition=condition),
        'tracks':TRACKS,'cohorts':ledger['cohorts'],'patent_pairs':pairs,'ledger_sha256':ledger['ledger_sha256'],
        'historical_results_url':'/track-record'}


@router.get('/api/arena')
def overview(request:Request, cohort_id:str|None=None, track:str='same_evidence', condition:str='clinical_only'):
    try:
        return build_arena(request.app.state.store,cohort_id=cohort_id,track=track,condition=condition)
    except (OSError,ValueError,KeyError,TypeError):
        raise HTTPException(503,'挑战证据或运行记录未通过完整性检查。') from None


@router.get('/api/arena/targets/{target_id}')
def target_detail(target_id:str, request:Request):
    view = overview(request)
    target = next((t for t in view['targets'] if t['id']==target_id),None)
    if not target:
        raise HTTPException(404,'未找到该预测目标。')
    runs = [r for r in view['runs'] if r['target_id']==target_id]
    ids = set(target.get('evidence_ids',[])) | {e for r in runs for e in r.get('evidence_ids',[])}
    selected_evidence = [e for e in view['evidence'] if e['id'] in ids]
    if target.get('scope') == 'prospective_challenge':
        frozen = ArenaLedger(request.app.state.store.path).snapshot()
        selected_evidence = list({(e['id'],e.get('source_sha256')):e for r in frozen['runs'] if r['target_id']==target_id for e in r['packet']['evidence']}.values())
    try:
        manifest=json.loads((ROOT/'arena_data/season_acquisition_manifest.json').read_text())
        hashes={v['sha256'] for v in manifest.get('assets',[])}
    except (OSError,ValueError,KeyError,TypeError):
        hashes=set()
    selected_evidence=[{**e,'source_snapshot_url':'/api/arena/sources/'+e['source_sha256']}
                       if e.get('source_sha256') in hashes else e for e in selected_evidence]
    own_patents={p for r in runs for p in r.get('patent_ids',[])}
    return {'target':target,'runs':runs,'evidence':selected_evidence,
            'patents':[{**p,'included_in_predictions':p['id'] in own_patents} for p in view['patents'] if p['id'] in target.get('patent_ids',[])], 'result':target.get('result'),
            'related_targets':[{k:t.get(k) for k in ('id','trial_name','cohort_id','registered_at','scope')} for t in view['targets'] if t['nct_id']==target['nct_id'] and t['id']!=target_id]}


@router.get('/api/arena/targets/{target_id}/report')
def target_report(target_id:str, request:Request):
    report = render_target_report(target_detail(target_id,request))
    return Response(report,media_type='text/markdown',
        headers={'Content-Disposition':'attachment; filename="predome-target-report.md"'})


def _source_asset(identifier):
    if not re.fullmatch(r'[a-f0-9]{64}',identifier):
        raise HTTPException(404,'未找到公开来源快照。')
    try:
        manifest=json.loads((ROOT/'arena_data/season_acquisition_manifest.json').read_text())
        if manifest.get('schema_version')!='arena-public-acquisition-v1':
            raise ValueError('Unknown source manifest')
        item=next((v for v in manifest['assets'] if v.get('sha256')==identifier),None)
        if not item or not re.fullmatch(r'arena_data/source_snapshots/[A-Za-z0-9][A-Za-z0-9_.-]{0,160}\.(json|html|txt|pdf|xml)',item.get('path','')):
            raise ValueError('Unknown source path')
        folder=(ROOT/'arena_data/source_snapshots').resolve()
        path=ROOT/item['path']
        if (not folder.is_relative_to(ROOT.resolve()) or path.is_symlink()
                or not path.resolve().is_relative_to(folder) or not path.is_file()
                or type(item.get('bytes')) is not int or not 0<item['bytes']<=64*1024*1024
                or path.stat().st_size!=item['bytes']):
            raise ValueError('Invalid source object')
        raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=identifier:
            raise ValueError('Changed public source snapshot')
        return item,raw
    except (OSError,ValueError,KeyError,TypeError):
        raise HTTPException(404,'来源快照不存在或未通过完整性检查。') from None


@router.get('/api/arena/sources/{identifier}')
def source_snapshot(identifier:str):
    item,raw=_source_asset(identifier)
    suffix=Path(item['path']).suffix
    return Response(raw,media_type='application/octet-stream',
        headers={'Content-Disposition':'attachment; filename="predome-source-'+identifier[:12]+suffix+'"',
                 'X-Content-Type-Options':'nosniff'})


@router.get('/api/arena/patents/{publication_number}')
def patent_detail(publication_number:str):
    item = get_patent(publication_number)
    if not item:
        raise HTTPException(404,'未找到该专利。')
    return _patent_view(item)


@router.get('/api/arena/ledger')
def export_ledger(request:Request):
    value = ArenaLedger(request.app.state.store.path).snapshot()
    return JSONResponse(value,headers={'Content-Disposition':'attachment; filename=predome-open-forecast-ledger.json'})


@router.get('/api/arena/runs/{run_id}/packet')
def run_packet(run_id:str, request:Request):
    ledger = ArenaLedger(request.app.state.store.path).snapshot()
    run = next((r for r in ledger['runs'] if r['id']==run_id),None)
    if not run:
        raise HTTPException(404,'仅新赛季运行提供冻结输入包。既有档案请查原研究记录。')
    return JSONResponse({'run_id':run_id,'input_sha256':run['input_sha256'],'packet':run['packet']},
                        headers={'Content-Disposition':'attachment; filename=predome-evidence-packet.json'})


def _attestation(identifier):
    if not re.fullmatch(r'[a-zA-Z0-9-]{1,80}',identifier):
        raise HTTPException(404,'未找到时间凭证。')
    folder=ROOT/'arena_data/attestations'/identifier
    try:
        receipt=json.loads((folder/'receipt.json').read_text())
        allowed={'manifest.json','manifest.tsq','manifest.tsr','tsa.crt','cacert.pem','verification.txt'}
        if set(receipt['files'])!=allowed:
            raise ValueError('Unexpected attestation files')
        for name,expected in receipt['files'].items():
            if hashlib.sha256((folder/name).read_bytes()).hexdigest()!=expected:
                raise ValueError('Attestation file changed')
        return folder,receipt
    except (OSError,ValueError,KeyError):
        raise HTTPException(404,'时间凭证不存在或未通过完整性检查。') from None


@router.get('/api/arena/attestations/{identifier}')
def attestation(identifier:str):
    _,receipt=_attestation(identifier)
    return {**receipt,'download_url':'/api/arena/attestations/'+identifier+'/download',
        'verification_instructions':'Download the bundle; openssl ts -verify -data manifest.json -in manifest.tsr -CAfile cacert.pem -untrusted tsa.crt. Trust depends on the independently obtained pinned FreeTSA CA.'}


@router.get('/api/arena/attestations/{identifier}/download')
def attestation_download(identifier:str):
    folder,receipt=_attestation(identifier)
    buffer=BytesIO()
    with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as bundle:
        for name in sorted(set(receipt['files'])|{'receipt.json'}):
            bundle.write(folder/name,arcname=name)
    return Response(buffer.getvalue(),media_type='application/zip',
        headers={'Content-Disposition':'attachment; filename=predome-timestamp-'+identifier+'.zip'})
