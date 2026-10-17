"""Observable exact-source reconciliation; historical proof is integrity checked."""
import hashlib
import html
import json
import re
from urllib.parse import urlencode

from .io import within
from .market_jobs import identity_schedule
from .identity_collection import runs, events, validate_url, DIRECTORY_URLS, NCT_API
from .sponsor_review import _stamp
from .market_view import _timestamp

STATES = {'ready':'可排队', 'active':'已排队或执行中', 'cooldown':'等待重试', 'blocked':'需要补证据',
          'unknown':'待核对', 'ineligible':'当前不符合条件', 'succeeded':'来源名称核对完成',
          'failed':'来源核对失败', 'cancelled':'已取消', 'interrupted':'核对中断',
          'running':'核对中', 'superseded':'旧尝试已失效', 'busy':'同一建议正在核对'}


def e(value):
    return html.escape(str(value if value is not None else '未记录'), quote=True)


def _button(app, action, label, fields=None):
    return '<form method="post" action="'+action+'"><input type="hidden" name="token" value="'+e(app.token)+'">'+''.join('<input type="hidden" name="'+e(k)+'" value="'+e(v)+'">' for k,v in (fields or {}).items())+'<button>'+e(label)+'</button></form>'


def render(app, market, q):
    scheduled=identity_schedule(market)
    enabled=market.setting('identity.enabled','0')=='1'
    counts={status:sum(r['status']==status for r in scheduled) for status in ('ready','active','cooldown','blocked','unknown')}
    body=('<section class="card" id="source-identity"><h2>美股严格来源名称核对</h2>'
          '<p>只处理当前候选版本中的精确名称建议，重新读取官方证券目录和对应 NCT 登记。证券代码、规范名称与精确 lead sponsor 均符合规则后，记录自动来源核对并排队采集公司登记。</p>'
          '<p class="data-note">这项核对只建立证券主体与登记申办方的来源名称关联，不构成人工签署、药物权益核验、完整管线验收或科学审批。</p>'
          '<p>符合自动核对范围 '+str(len(scheduled))+' 项 · 可排队 '+str(counts['ready'])+' · 执行或排队 '+str(counts['active'])+' · 冷却 '+str(counts['cooldown'])+' · 受阻或待核对 '+str(counts['blocked']+counts['unknown'])+'</p>'
          '<div class="page-actions">'+_button(app,'/market/identity-batch','核对全部符合条件的建议')
          +_button(app,'/market/identity-schedule','关闭持续来源核对' if enabled else '开启持续来源核对',{'enabled':'0' if enabled else '1'})+'</div>'
          '<p class="ref">持续来源核对'+('已开启' if enabled else '未开启')+'；每轮最多新增20项，失败后默认冷却24小时。单次手动批量最多1000项。持续任务还要求市场自动更新开启，且服务保持运行。</p>')
    if scheduled:
        body+='<details><summary>查看每项调度状态与缺口</summary>'
        for row in scheduled:
            body+='<p><a href="#proposal-'+e(row['proposal_id'])+'">'+e(row['candidate_id'])+'</a> · '+e(STATES.get(row['status'],row['status']))+' · '+e(row['reason'])+'</p>'
            if row['retry_at']:body+='<p class="ref">下次可重试 '+_timestamp(row['retry_at'])+'</p>'
        body+='</details>'
    body+='</section>'
    history=runs(market,limit=30)
    body+='<details class="card" id="identity-history"><summary>自动来源核对历史 · 最近 '+str(len(history))+' 条</summary>'
    if not history:body+='<p class="data-note">尚无自动来源核对运行记录，排队数量不能当作核对完成数量。</p>'
    for row in history:
        body+='<article class="card"><span class="badge">'+e(STATES.get(row['status'],row['status']))+'</span> <strong>'+e(row['candidate_id'])+'</strong><p class="ref">开始 '+_timestamp(row['started'])+' · 结束 '+_timestamp(row['finished'])+'</p>'
        if row['error']:body+='<p class="notice">'+e(row['error'])+'</p>'
        body+='<p class="data-note">自动来源核对 · 非人工签署 · 权益与科学审批待独立核对</p><p class="ref">运行 '+e(row['id'])+' · 建议 '+e(row['proposal_id'])+'</p>'
        if row['evidence_sha256']:
            body+='<p class="ref">证据清单 SHA-256 '+e(row['evidence_sha256'])+'</p><p>'
            for key,label in [('proof','查看核对清单'),('exchange','查看证券目录原文'),('registry','查看 NCT 原文')]:
                body+='<a class="text-link" target="_blank" rel="noopener noreferrer" href="/market/identity-evidence?'+e(urlencode({'id':row['id'],'file':key}))+'">'+label+' ↗</a> · '
            body+='</p>'
        body+='<p><a href="/mapping?identity_run='+e(row['id'])+'#identity-events">查看事件记录 →</a></p></article>'
    body+='</details>'
    chosen=q.get('identity_run','')
    if chosen:
        if not re.fullmatch(r'[0-9a-f]{32}',chosen):raise ValueError('来源核对运行编号不正确')
        with market.db() as db:found=db.execute('SELECT 1 FROM identity_collection_runs WHERE id=?',(chosen,)).fetchone()
        if not found:raise ValueError('来源核对运行不存在')
        body+='<section class="card" id="identity-events"><h2>来源核对事件记录</h2><p class="ref">'+e(chosen)+'</p>'
        for event in events(market,chosen):body+='<details><summary>'+_timestamp(event['created'])+' · '+e(event['event'])+'</summary><pre>'+e(event['details'])+'</pre></details>'
        body+='</section>'
    return body


def evidence_file(market, run_id, kind):
    """Return frozen verified bytes, including old proof without re-approving it.

    Download checks integrity of the recorded proof and both source files.
    Historical source age never silently re-accepts a current mapping.
    """
    if not re.fullmatch(r'[0-9a-f]{32}',str(run_id)) or kind not in ('proof','exchange','registry'):
        raise ValueError('来源核对附件不存在')
    with market.db() as db:
        exists=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='identity_collection_runs'").fetchone()
        row=db.execute('SELECT * FROM identity_collection_runs WHERE id=?',(run_id,)).fetchone() if exists else None
    if not row or row['status']!='succeeded' or not row['evidence_path'] or not re.fullmatch(r'[0-9a-f]{64}',str(row['evidence_sha256'])):
        raise ValueError('没有完成的来源核对附件')
    expected_folder=market.root/'identity_review'/run_id
    if expected_folder.is_symlink() or expected_folder.resolve()!=expected_folder.absolute():
        raise ValueError('来源核对运行目录不能使用符号链接')
    folder=within(expected_folder,market.root/'identity_review')

    def read(path, maximum, expected_hash=None):
        if not isinstance(path,str) or not path.startswith('artifacts/market/identity_review/'+run_id+'/'):
            raise ValueError('来源核对附件路径越界')
        target=within(market.root/path.removeprefix('artifacts/market/'),folder)
        with target.open('rb') as file:raw=file.read(maximum+1)
        if len(raw)>maximum or (expected_hash is not None and hashlib.sha256(raw).hexdigest()!=expected_hash):
            raise ValueError('来源核对附件校验失败')
        return raw

    proof_raw=read(row['evidence_path'],1024*1024,row['evidence_sha256'])
    proof=json.loads(proof_raw)
    if (not isinstance(proof,dict) or proof.get('collector_run_id')!=run_id
        or proof.get('proposal_id')!=row['proposal_id'] or proof.get('candidate_id')!=row['candidate_id']
        or proof.get('candidate_hash')!=row['candidate_hash'] or proof.get('human_sign_off') is not False
        or proof.get('drug_ownership_verified') is not False or proof.get('reviewer_type')!='automated_source_review'
        or proof.get('status')!='verified' or proof.get('scope')!='listed_entity_to_exact_registry_sponsor_name_only'):
        raise ValueError('核对清单与运行范围不符')
    if (proof.get('market')!='US' or not re.fullmatch(r'[A-Z0-9.\-]{1,16}',str(proof.get('ticker')))
        or proof['market']+':'+proof['ticker']!=row['candidate_id']
        or proof.get('official_source_url') not in DIRECTORY_URLS
        or not re.fullmatch(re.escape(NCT_API)+r'NCT[0-9]{8}',str(proof.get('registry_api_url')))):
        raise ValueError('核对清单缺少有效证券或登记来源')
    for timestamp in ('checked_at','exchange_fetched_at','registry_fetched_at'):
        _stamp(proof.get(timestamp))
    exchange=json.loads(read(proof.get('exchange_capture_manifest'),1024*1024))
    registry=json.loads(read(proof.get('evidence_capture_manifest'),1024*1024))
    if not isinstance(exchange,dict) or not isinstance(registry,list) or len(registry)!=1 or not isinstance(registry[0],dict):
        raise ValueError('来源清单格式不正确')
    payload={'proof':proof_raw}
    for key,record,url in [('exchange',exchange,proof.get('official_source_url')),('registry',registry[0],proof.get('registry_api_url'))]:
        validate_url(url)
        if not re.fullmatch(r'[0-9a-f]{64}',str(proof.get(key+'_sha256'))):
            raise ValueError('来源文件缺少有效校验值')
        if (record.get('url')!=url or record.get('status')!=200 or type(record.get('bytes')) is not int
            or record['bytes']<=0 or record.get('fetched_at')!=proof.get(key+'_fetched_at')
            or record.get('kind')!=key or (key=='registry' and record.get('ticker')!=proof.get('ticker'))):
            raise ValueError('来源清单范围或状态不正确')
        raw=read(record.get('path'),8*1024*1024,proof.get(key+'_sha256'))
        if len(raw)!=record['bytes'] or record.get('sha256')!=proof.get(key+'_sha256'):
            raise ValueError('来源文件大小或校验值不符')
        payload[key]=raw
    return payload[kind], 'text/plain; charset=utf-8' if kind=='exchange' else 'application/json; charset=utf-8'
