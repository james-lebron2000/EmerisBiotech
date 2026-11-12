(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const esc = v => String(v ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const safe = u => {try {const x=new URL(u);return x.protocol==='https:'?x.href:'';}catch{return '';}};
  let csrf='',active=null,sourceId=null,proposalSourceId=null,activeReadout=null;
  let suggestionSourceId=null,suggestionData=null,suggestionRequest=0,linkRevision=0,linkBusy=false;
  let proposalRevision=0,proposalBusy=false,proposalTargets=[],proposalTargetsReady=false;
  let readoutReviewRevision=0;
  const checks={endpoint_match:'主要终点一致',population_match:'目标人群一致',comparison_match:'治疗与对照一致',source_checked:'已经打开并核对原始公开来源',prespecified_analysis:'对应预设的主要分析',earliest_result_checked:'核对了最早公开结果时间',evidence_cutoff_checked:'预测证据未越过截止日期'};
  const dateChecks={readout_not_completion:'该时间指临床读出，而不是主要完成日、研究完成日或会议日期',source_quote_supports_date:'原文摘录直接支持这个日期或窗口，没有缩小或扩大原文精度',trial_identity_confirmed:'原始来源对应同一试验及注册编号',primary_source_checked:'已打开并核对公司原始公告及其公开日期',precision_correct:'时间类型、开始/结束日期和来源精度一致'};
  const arenaDateCheck={exact_target_applicability_confirmed:'已逐项核对冻结目标的终点、人群、比较、成功规则及分析时间，确认此日程适用于这个精确目标；同一 NCT 不足以确认适用'};
  const pages={
    intake:{url:'/api/intake/candidates',records:[],next:0,more:true,busy:false},
    outcome:{url:'/api/platform/reviews',records:[],next:0,more:true,busy:false},
    readout:{url:'/api/readout-updates',records:[],next:0,more:true,busy:false}
  };
  async function request(path,body) {
    const r=await fetch(path,{method:body?'POST':'GET',cache:'no-store',headers:body?{'Content-Type':'application/json','X-CSRF-Token':csrf}:{},body:body?JSON.stringify(body):undefined});
    const d=await r.json();
    if(!r.ok)throw Error(typeof d.detail==='string'?d.detail:Array.isArray(d.detail)?d.detail.map(x=>`${x.loc?.at(-1)||'字段'}: ${x.msg}`).join('；'):'请求未完成，请核对字段或权限。');
    return d;
  }
  function sourceLink(source,label='核对原始来源 ↗') {
    const href=safe(source?.url);
    return href?`<a target="_blank" rel="noopener noreferrer" href="${esc(href)}">${esc(label)}</a>`:'<span>来源链接未确认</span>';
  }
  function officialText(entry, purpose='时间') {
    const text=entry.structured?.text||'';
    return text?`<p class="criteria">${esc(text.slice(0,700))}${text.length>700?'…':''}</p><details><summary>展开已抓取原文，核对并复制相关${esc(purpose)}段落</summary><pre>${esc(text)}</pre></details>`:'<p class="caption">未提取到可核对原文。保持待核实，不从标题或登记完成日期推断读出或临床结果。</p>';
  }
  function renderSources() {
    $('intake-list').innerHTML=pages.intake.records.length?pages.intake.records.map(s=>{
      const official=s.kind==='official_announcement_candidate';
      return `<article class="trial-card"><h3>${esc(s.nct_id)} · ${official?'公司原始公告候选':esc(s.kind)}</h3><p>${esc(s.structured?.title||s.structured?.trial_name||s.structured?.note||'文献检索候选')}</p><p>抓取：${esc(s.source?.retrieved_at||'未记录')} · ${esc(s.review_status||'待审核')}${official?'<br>来源公开日期：'+esc(s.structured?.publication_date_basis==='document_metadata'?(s.structured?.published_at||'未知'):'文档日期未确认；需核实，不能沿用旧指引日期'):''}</p>${sourceLink(s.source)} <a href="/api/intake/candidates/${encodeURIComponent(s.id)}/source" class="text-link">下载已抓取原始来源 ↓</a>${official?officialText(s):`<details><summary>查看抽取字段</summary><pre>${esc(JSON.stringify(s.structured,null,2))}</pre></details>`}${s.kind==='reported_results_candidate'?`<button type="button" class="button" data-suggestions="${esc(s.id)}">查看统计抽取与配对建议</button>`:''}${official?`<button type="button" class="button" data-readout-propose="${esc(s.id)}">提议读出时间更新</button><button type="button" class="button" data-link="${esc(s.id)}">提取临床结果，提交独立核验</button><p class="caption">公告候选不是已批准时间；此操作不判定疗效成败。</p>`:['reported_results_candidate','pubmed_literature_candidate'].includes(s.kind)?`<button type="button" class="button" data-link="${esc(s.id)}">关联锁定预测并提取结果</button>`:'<p class="caption">需另找正式读出依据，不可直接判定成败或自动采用登记完成日。</p>'}</article>`;
    }).join(''):'<p>目前没有来源候选。</p>';
  }
  function renderOutcomes() {
    $('outcome-list').innerHTML=pages.outcome.records.length?pages.outcome.records.map(r=>`<article class="trial-card"><h3>${esc(r.outcome.trial_id)} · ${esc(r.status)}</h3><p>${esc(r.outcome.endpoint)}</p><p>实际标签：${r.outcome.label===null?'未确定':r.outcome.label===1?'达标':'未达标'}；${esc(r.outcome.readout_date)}</p><button type="button" class="button" data-review="${esc(r.id)}">打开核验记录</button></article>`).join(''):'<p>目前没有待核验结果；不展示模拟记录。</p>';
  }
  const dateKind=p=>p.date_kind==='readout_time_unconfirmed'?'读出时间未确认':p.date_kind==='official_exact_date'?'官方明确公布的读出日期':'公司预计读出窗口';
  const dateRange=p=>p.date_kind==='readout_time_unconfirmed'?'未确认；未填日期':(p.start||'未记录')+(p.end!==p.start?' — '+(p.end||'未记录'):'');
  const arenaReadout=p=>typeof p?.arena_target_id==='string'&&p.arena_target_id!=='';
  function readoutScope(p) {
    return arenaReadout(p)?'正式 Arena 精确目标 · '+p.arena_target_id:'旧版 NCT 日历 · 不更新正式 Arena 目标';
  }
  function renderReadouts() {
    $('readout-list').innerHTML=pages.readout.records.length?pages.readout.records.map(r=>{
      const p=r.proposal;
      return `<article class="trial-card"><h3>${esc(p.nct_id)} · ${esc(r.status)}</h3><p>${esc(readoutScope(p))}</p><p>${dateKind(p)}：${esc(dateRange(p))}<br>来源精度：${esc(p.precision)} · ${esc(p.label)}</p><p class="criteria">“${esc(p.quoted_evidence)}”</p><p>提交：${esc(r.created_at)}${r.decision?'<br>审核：'+esc(r.decision.created_at):''}</p>${sourceLink(p.source)}<button type="button" class="button" data-readout-review="${esc(r.id)}">${r.can_review?'打开独立时间核验':'查看提议与审核状态'}</button>${r.status==='pending_review'&&!r.can_review?'<p class="caption">当前账号不能审核这条提议，需另一位独立审核员。</p>':''}</article>`;
    }).join(''):'<p>尚无读出时间提议。请从上方公司原始公告候选提取，不依据登记完成日补造日期。</p>';
  }
  const renderers={intake:renderSources,outcome:renderOutcomes,readout:renderReadouts};
  async function loadPage(name,reset=false) {
    const p=pages[name],button=$(name+'-more'),status=$(name+'-page-status');
    if(p.busy)return;
    const offset=reset?0:p.next;
    if(!reset&&!p.more)return;
    p.busy=true;button.disabled=true;status.textContent='正在加载…';
    try {
      const d=await request(`${p.url}?limit=20&offset=${offset}`);
      if(!Array.isArray(d.records))throw Error('列表格式不正确。');
      const combined=reset?d.records:p.records.concat(d.records);
      p.records=[...new Map(combined.map(r=>[r.id,r])).values()];
      p.more=Boolean(d.pagination?.has_more);
      p.next=d.pagination?.next_offset;
      if(p.more&&(!Number.isInteger(p.next)||p.next<=offset))throw Error('分页游标无效，请刷新后重试。');
      renderers[name]();button.hidden=!p.more;
      status.textContent=`已载入 ${p.records.length} 条${p.more?'；还有更多记录。':'；已到当前列表末尾。'}`;
    } catch(e) {
      status.textContent='加载未完成：'+e.message;
      if(!p.records.length)button.hidden=false;
    } finally {p.busy=false;button.disabled=false;}
  }
  function failureDetails(failures) {
    if(!Array.isArray(failures)||!failures.length){$('intake-failures').innerHTML='';return;}
    const categories={access_denied:'访问被拒绝',rate_limited:'来源限流',not_found:'来源未找到',source_unavailable:'来源服务不可用',timeout:'访问超时',parse_timeout:'文档解析超时',parse_error:'文档解析未完成',integrity_error:'来源配置完整性未通过',unavailable:'未能读取来源'};
    $('intake-failures').innerHTML=`<details><summary>查看本次检索未完成的来源（${failures.length} 条）</summary><p class="caption">来源访问失败不等于临床试验失败，也不表示结果尚未公布。旧记录缺少状态码或时间时保留未记录。</p>${failures.map(f=>`<article class="detail-target"><h3>${esc(f.source||'来源未记录')}</h3><p>${sourceLink({url:f.url})}<br>HTTP 状态：${Number.isInteger(f.http_status)?esc(f.http_status):'未记录'}<br>类别：${esc(categories[f.category]||f.category||'未记录')}<br>核查时间：${esc(f.checked_at||'未记录')}<br>错误类型：${esc(f.error_type||'未记录')}${Number.isInteger(f.retry_after_seconds)&&f.retry_after_seconds>=0&&f.retry_after_seconds<=86400?'<br>来源建议等待：'+esc(f.retry_after_seconds)+' 秒':''}${f.response_server?'<br>响应服务标识：'+esc(f.response_server):''}</p></article>`).join('')}</details>`;
  }
  async function load() {
    try {
      const status=await request('/api/intake/status');
      $('intake-status').textContent=status.latest?`最近完成：${status.latest.completed}；核查来源 ${status.latest.sources_checked}；新增候选 ${status.latest.new_candidates}；失败 ${(status.latest.failures||[]).length}；状态 ${status.latest.status}。定期任务${status.scheduled_read_only?'已配置':'尚未启用'}。`:'尚无完成的检索任务；不将未运行显示为已同步。';
      failureDetails(status.latest?.failures);
      const batch=status.latest?.result_suggestions;
      if(Array.isArray(batch?.records)){const count=batch.records.reduce((n,r)=>n+(Number(r.pairing_count)||0),0);$('intake-status').textContent+=` 本批自动整理结果来源 ${batch.records.length} 条，返回待审核配对 ${count} 条${batch.records.some(r=>r.truncated)?'（部分达到显示上限，须核对原始来源）':''}；这不是全部待办数量，请继续查看审核队列。`;}
    } catch(e) {$('intake-status').textContent='检索状态暂未读取：'+e.message;$('intake-failures').innerHTML='';}
    try {
      const cap=await request('/api/platform/capabilities');
      if(!cap.authenticated){$('review-status').innerHTML='请先<a href="/predict">登录平台</a>。';return;}
      if(!cap.reviewer){$('review-status').textContent=cap.reviewer_configured?'此账号没有独立审核权限。':'管理员尚未配置独立审核账号；待审核结果不会进入绩效。';return;}
      const state=await request('/api/state');csrf=state.csrf;
      $('review-content').hidden=false;$('review-status').textContent='仅授权审核员可访问此队列。疗效结果与读出时间提议分别审核。';
      await Promise.all(Object.keys(pages).map(name=>loadPage(name,true)));
    } catch(e) {$('review-status').textContent=e.message;}
  }
  function openOutcome(id) {
    active=pages.outcome.records.find(r=>r.id===id);if(!active)return;
    $('review-detail').innerHTML=`<p>${esc(active.outcome.trial_id)} · ${esc(active.snapshot_id)}</p><pre>${esc(JSON.stringify(active.outcome,null,2))}</pre>${sourceLink(active.outcome.source)}`;
    $('confirmations').innerHTML=Object.entries(checks).map(([k,v])=>`<label class="check-label"><input type="checkbox" name="${k}">${esc(v)}</label>`).join('');
    $('review-form').reset();$('review-feedback').textContent='';$('review-dialog').showModal();
  }
  function resetLink() {
    ++linkRevision;sourceId=null;linkBusy=false;$('link-form').reset();
    $('link-form').querySelector('button').disabled=false;
    $('link-target-reference').hidden=true;$('link-target-reference').innerHTML='';
    $('link-source-detail').hidden=true;$('link-source-detail').innerHTML='';
    $('link-official-fields').hidden=true;$('link-publication-basis').textContent='';
    $('link-source-quote').required=false;$('link-source-publication-date').required=false;
    $('link-source-publication-date').readOnly=false;$('link-source-publication-date').value='';
    $('link-source-quote').value='';$('link-feedback').textContent='';
  }
  function openLink(id) {
    const entry=pages.intake.records.find(x=>x.id===id);if(!entry)return;
    resetLink();sourceId=id;const official=entry.kind==='official_announcement_candidate';
    $('link-article').innerHTML=entry.kind==='pubmed_literature_candidate'?'<option value="">选择具体论文，不能用搜索结果代替</option>'+(entry.structured.articles||[]).map(a=>`<option value="${esc(a.id)}">${esc(a.title)} · ${esc(a.firstPublicationDate||'日期未知')}</option>`).join(''):official?'<option value="">已抓取的公司原始公告</option>':'<option value="">ClinicalTrials.gov 结果登记原文</option>';
    if(official){
      $('link-official-fields').hidden=false;$('link-source-detail').hidden=false;
      $('link-source-quote').required=true;$('link-source-publication-date').required=true;
      const metadata=entry.structured?.publication_date_basis==='document_metadata',rawDate=entry.structured?.published_at;
      const date=metadata&&typeof rawDate==='string'&&/^\d{4}-\d{2}-\d{2}$/.test(rawDate)?rawDate:'';
      $('link-source-publication-date').value=date;$('link-source-publication-date').readOnly=!!date;
      $('link-publication-basis').textContent=date?'日期来源：文档元数据。保持已抓取日期，只读展示；仍需独立审核来源真实性。':'日期来源：待审核员根据本篇原文显式提供；尚未经独立核验。未使用来源指引日期或抓取日期。';
      $('link-source-detail').innerHTML=`<h3>${esc(entry.nct_id)} · ${esc(entry.structured?.title||'公司原始公告')}</h3><p>${sourceLink(entry.source)} · <a href="/api/intake/candidates/${encodeURIComponent(entry.id)}/source">下载已抓取原文 ↓</a><br>抓取：${esc(entry.source?.retrieved_at||'未记录')}</p><span class="hash">原始来源 SHA-256：${esc(entry.source?.sha256||'未记录')}</span>${officialText(entry,'临床结果与试验身份')}`;
    }
    $('link-dialog').showModal();
  }
  const warningLabels={nct_pairing_does_not_verify_target_match:'同一NCT不代表目标已经匹配',registry_has_results_flag_not_true:'登记库未明确标记已有结果',no_public_locked_prediction_match:'没有同NCT公开锁定预测',registered_primary_outcomes_missing:'缺少登记主要终点定义',no_reported_primary_measures:'未找到报告中的PRIMARY指标',suggestion_limit_reached_review_all_raw_primary_results:'建议条数已达上限，须核对全部原始结果',suggestion_byte_limit_reached_review_raw_primary_results:'建议字节数已达上限，须核对全部原始结果',multiple_primary_measures_require_success_rule_review:'多个主要终点，须核对整体成功规则',multiple_analyses_no_automatic_selection:'存在多项分析，系统不自动选择其中一项',no_reported_statistical_analysis:'未报告统计分析条目',reported_analysis_population_missing:'缺少实际分析人群描述',reported_timeframe_missing:'缺少观察时间',duplicate_measure_local_group_ids:'该指标内存在重复组别ID',analysis_comparison_group_ids_missing:'缺少本分析的比较组ID',unresolved_or_ambiguous_measure_local_group_id:'组别ID无法唯一关联到该指标内的组别',p_value_not_reported:'未报告P值',p_value_inequality_preserved_not_exact:'P值仅为不等式，已保留原文，不能当精确值',nominal_p_value_not_primary_success:'名义P值不等于主要终点成功',noninferiority_or_other_test_requires_margin_and_rule_review:'非劣效或其他检验须核对界值及预设规则'};
  const rawValue=v=>v===null||v===undefined||v===''?'未报告 / 未抽取':typeof v==='object'?JSON.stringify(v):String(v);
  function warningHTML(warnings) {
    return (warnings||[]).length?`<details open><summary>需要人工核对的事项</summary><ul>${warnings.map(w=>'<li>'+esc(typeof w==='string'&&warningLabels[w]?warningLabels[w]:rawValue(w))+'</li>').join('')}</ul></details>`:'';
  }
  function targetReference(target) {
    return `<dl><dt>预测终点</dt><dd>${esc(target?.endpoint)}</dd><dt>预测人群</dt><dd>${esc(target?.population)}</dd><dt>预测比较</dt><dd>${esc(target?.comparison)}</dd><dt>预测达标定义</dt><dd>${esc(target?.success_definition)}</dd></dl>`;
  }
  function suggestionCards(data) {
    // One card per source measure/analysis; candidate snapshots remain separate.
    const groups=new Map();
    for(const s of data.suggestions||[]) {
      const r=s.reported||{},key=JSON.stringify([r.measure_index,r.analysis_index]);
      if(!groups.has(key))groups.set(key,{reported:r,items:[]});
      groups.get(key).items.push(s);
    }
    // Extraction is still useful when no public prediction has the same NCT.
    for(const m of data.primary_measures||[]) {
      const raw=m.raw||{},analyses=Array.isArray(raw.analyses)&&raw.analyses.length?raw.analyses:[null];
      analyses.forEach((a,i)=>{
        const index=a===null?null:i,key=JSON.stringify([m.measure_index,index]);
        if(!groups.has(key))groups.set(key,{reported:{
          measure_index:m.measure_index,analysis_index:index,measure_pointer:m.json_pointer,
          title:raw.title,populationDescription:raw.populationDescription,timeFrame:raw.timeFrame,
          paramType:raw.paramType,unitOfMeasure:raw.unitOfMeasure,analysis:a,
          analysis_groups:(raw.groups||[]).filter(g=>(a?.groupIds||[]).includes(g.id))
        },items:[]});
      });
    }
    return [...groups.values()].map(({reported:r,items})=>{
      const a=r.analysis||{},statRows=[
        ['统计方法',a.statisticalMethod],['效应指标',a.paramType],['效应估计',a.paramValue],
        ['P 值（来源原文）',a.pValue??items[0]?.suggested_outcome?.p_value_raw],
        ['区间水平（来源原文）',a.ciPctValue],['区间侧数',a.ciNumSides],
        ['区间下限',a.ciLowerLimit],['区间上限',a.ciUpperLimit],
        ['离散度类型 / 值',[a.dispersionType,a.dispersionValue].filter(v=>v!==undefined&&v!==null).join(' / ')],
        ['非劣效 / 优效分析类型',a.nonInferiorityType]
      ];
      return `<article class="detail-target suggestion-analysis"><h3>PRIMARY #${esc(r.measure_index)} · ${esc(r.title||'来源未提供标题')}</h3><p><strong>${r.analysis_index===null||r.analysis_index===undefined?'未提供独立统计分析条目':'Analysis #'+esc(r.analysis_index)}</strong> · 此卡片仅显示这一项分析</p><dl><dt>来源人群描述</dt><dd>${esc(rawValue(r.populationDescription))}</dd><dt>观察时间</dt><dd>${esc(rawValue(r.timeFrame))}</dd><dt>指标 / 单位</dt><dd>${esc(rawValue(r.paramType))} / ${esc(rawValue(r.unitOfMeasure))}</dd></dl><h4>本分析报告的组别</h4><p>比较组说明：${esc(rawValue(a.groupDescription))}</p>${(r.analysis_groups||[]).length?`<ul>${r.analysis_groups.map(g=>'<li>'+esc(rawValue(g.id))+' · '+esc(rawValue(g.title))+' — '+esc(rawValue(g.description))+'</li>').join('')}</ul>`:'<p>组别未列出或无法关联，不能推断比较方向。</p>'}<div class="table-scroll"><table class="results-table"><thead><tr><th>来源统计字段</th><th>原始值</th></tr></thead><tbody>${statRows.map(([label,value])=>'<tr><td>'+esc(label)+'</td><td>'+esc(rawValue(value))+'</td></tr>').join('')}</tbody></table></div><p class="caption">保留 &lt;、&gt; 等不等式，不把它们转换为精确 P 值。区间、估计和 P 值不跨分析拼接，也不据此自动判定试验成功。</p><details><summary>查看本分析完整来源字段</summary><pre>${esc(JSON.stringify(r.analysis,null,2))}</pre><p class="hash">${esc(r.measure_pointer)}<br>${esc(r.analysis_pointer||'')}</p></details>${items.length?items.map(s=>`<details class="suggestion-pair"><summary>同 NCT 的公开预测候选 · ${esc(s.snapshot_id)}</summary><p class="detail-note">预测目标，非已核验结果。仅按试验编号建立候选配对，终点、人群、比较及主要分析尚未确认。</p>${targetReference(s.pairing?.predicted_target)}${warningHTML(s.warnings)}<span class="hash">来源快照 SHA-256 ${esc(s.snapshot_sha256)}<br>建议 ID ${esc(s.suggestion_id)}<br>建议 SHA-256 ${esc(s.digest)}</span><button type="button" class="button" data-suggestion-select="${esc(s.suggestion_id)}">选此快照，人工提取实际结果</button></details>`).join(''):data.truncated?'<p>该分析未在当前返回的配对建议中展开。达到显示上限不代表没有候选预测，请核对全部原始来源。</p>':'<p>没有可配对的公开锁定预测；来源抽取不代表已完成目标匹配。</p>'}</article>`;
    }).join('');
  }
  async function openSuggestions(id) {
    const entry=pages.intake.records.find(s=>s.id===id);
    if(!entry||entry.kind!=='reported_results_candidate')return;
    suggestionSourceId=id;suggestionData=null;const serial=++suggestionRequest;
    $('suggestions-content').innerHTML='';$('suggestions-status').textContent='正在核对来源并读取统计抽取与公开快照候选…';
    $('suggestions-retry').hidden=true;
    if(!$('suggestions-dialog').open)$('suggestions-dialog').showModal();
    try {
      const d=await request('/api/intake/candidates/'+encodeURIComponent(id)+'/suggestions');
      if(serial!==suggestionRequest)return;
      if(d.candidate_id!==id||d.nct_id!==entry.nct_id||!Array.isArray(d.primary_measures)||!Array.isArray(d.suggestions))throw Error('建议与当前来源不一致，未载入。');
      suggestionData=d;
      const cards=suggestionCards(d);
      $('suggestions-content').innerHTML=`<h3>${esc(d.nct_id)} · 来源字段抽取</h3><p>${sourceLink(d.source)} · <a href="/api/intake/candidates/${encodeURIComponent(id)}/source">下载已抓取来源 ↓</a><br>结果登记首次公开日期：${esc(rawValue(d.source?.results_first_post_date?.date??d.source?.results_first_post_date))}<br>抓取：${esc(rawValue(d.source?.retrieved_at))}</p><p class="caption">结果登记首次公开日期不自动等于临床首次读出日期。这里不生成成败标签，也不替代独立核验。</p><span class="hash">来源 SHA-256 ${esc(d.source?.sha256)}<br>来源哈希范围 ${esc(d.source?.hash_scope)}<br>建议集合 SHA-256 ${esc(d.digest)}<br>流程版本 ${esc(d.pipeline_version)}</span>${d.truncated?`<p class="detail-note">显示 ${esc(d.suggestions.length)} / 共 ${esc(d.pairing_count_total)} 项建议，剩余需查看原始来源。当前返回上限不是全部配对数。</p>`:''}${warningHTML(d.warnings)}${cards||'<p class="empty">未找到可展示的 PRIMARY 结果字段或分析。没有建议不代表临床失败，请保留人工核查。</p>'}<details><summary>登记方案中的主要终点（用于核对，不代表实际结果）</summary><pre>${esc(JSON.stringify(d.registered_primary_outcomes||[],null,2))}</pre></details>`;
      $('suggestions-status').textContent=`已读取 ${d.primary_measures.length} 个 PRIMARY 来源指标；显示 ${d.suggestions.length}${d.truncated?' / 共 '+d.pairing_count_total:''} 条待核实配对建议；尚未形成临床结论。`;
    } catch(e) {
      if(serial!==suggestionRequest)return;
      suggestionData=null;$('suggestions-content').innerHTML='';$('suggestions-status').textContent='建议读取失败：'+e.message;$('suggestions-retry').hidden=false;
    }
  }
  function chooseSuggestion(id) {
    const item=suggestionData?.suggestions.find(s=>s.suggestion_id===id);
    if(!item||item.candidate_id!==suggestionSourceId)return;
    $('suggestions-dialog').close();openLink(suggestionSourceId);
    $('link-snapshot').value=item.snapshot_id;
    $('link-target-reference').innerHTML=`<p class="detail-note"><strong>预测目标，非已核验结果。</strong>仅关联下列快照。实际终点、人群、比较、成败、首次读出日期及统计数值均需依据原始结果重新核实填写。</p><p class="hash">${esc(item.snapshot_id)} · 建议 SHA-256 ${esc(item.digest)}</p><details><summary>查看这条预测的目标定义</summary>${targetReference(item.pairing?.predicted_target)}</details>`;
    $('link-target-reference').hidden=false;
    $('link-feedback').textContent='仅填入快照 ID。所有实际结果字段保持空白，待人工核实。';
  }
  function selectedProposalTarget() {
    const entry=pages.intake.records.find(s=>s.id===proposalSourceId);
    return proposalTargetsReady&&entry&&$('readout-nct').value===entry.nct_id?proposalTargets.find(t=>t.id===$('readout-arena-target').value&&t.nct_id===entry.nct_id):null;
  }
  function updateDateFields() {
    const exact=$('readout-kind').value==='official_exact_date',unknown=$('readout-kind').value==='readout_time_unconfirmed';
    $('readout-start').disabled=unknown;$('readout-start').required=!unknown;
    $('readout-end-label').hidden=exact||unknown;$('readout-end').disabled=exact||unknown;$('readout-end').required=!exact&&!unknown;
    if(unknown){$('readout-start').value='';$('readout-end').value='';}
    const precision=$('readout-precision');
    if(exact||unknown)precision.value=unknown?'unknown':'day';
    else if(['day','unknown',''].includes(precision.value))precision.value='range';
    precision.disabled=exact||unknown;
    precision.querySelector('[value="day"]').disabled=!exact;
    precision.querySelector('[value="unknown"]').disabled=!unknown;
  }
  function updateProposalTarget() {
    const target=selectedProposalTarget();
    $('readout-arena-scope').hidden=!target;
    $('readout-source-granularity').disabled=!target;$('readout-source-granularity').required=!!target;
    $('readout-arena-reference').innerHTML=target?`<h3>${esc(target.trial_name)} · ${esc(target.nct_id)}</h3><p class="hash">精确目标 ID：${esc(target.id)}</p>${targetReference(target)}<p>分析时间：${esc(target.analysis_timepoint||'未记录')}</p>`:'';
    $('readout-kind').querySelector('[value="readout_time_unconfirmed"]').disabled=!target;
    if(!target&&$('readout-kind').value==='readout_time_unconfirmed')$('readout-kind').value='estimated_window';
    updateDateFields();
  }
  function resetProposal() {
    ++proposalRevision;proposalSourceId=null;proposalBusy=false;proposalTargets=[];proposalTargetsReady=false;
    $('readout-propose-form').reset();$('readout-propose-submit').disabled=false;
    for(const field of $('readout-propose-form').elements)field.disabled=false;
    $('readout-arena-target').innerHTML='<option value="">旧版 NCT 日历（不更新正式 Arena 目标）</option>';
    $('readout-arena-target').disabled=true;$('readout-arena-status').textContent='尚未载入正式 Arena 精确目标。';
    $('readout-source-detail').innerHTML='';$('readout-propose-feedback').textContent='';
    updateProposalTarget();
  }
  async function loadProposalTargets(id,revision,nct) {
    $('readout-arena-status').textContent='正在只读核对当前正式 Arena 目标；未选择时只提议旧版 NCT 日历。';
    try {
      const data=await request('/api/arena/public');
      if(revision!==proposalRevision||proposalSourceId!==id||!$('readout-propose-dialog').open)return;
      if(!Array.isArray(data.cohorts)||!Array.isArray(data.targets))throw Error('正式 Arena 目标格式不正确。');
      const inactive=value=>['withdrawn_before_execution','withdrawn','superseded'].includes(value);
      const cohort=data.cohorts.find(c=>c.id===data.current_cohort_id&&(c.kind==='registered_12_project_season'||c.protocol?.season_kind==='registered_12_project_season')&&!inactive(c.execution_status)&&!inactive(c.status));
      const matches=cohort?data.targets.filter(t=>t.cohort_id===cohort.id&&t.nct_id===nct&&!inactive(t.execution_status)&&!inactive(t.status)):[];
      if(matches.some(t=>typeof t.id!=='string'||!/^[A-Za-z0-9_-]{1,100}$/.test(t.id)||typeof t.endpoint!=='string'||!t.endpoint.trim())||new Set(matches.map(t=>t.id)).size!==matches.length)throw Error('正式 Arena 目标身份不完整或重复。');
      proposalTargets=matches;proposalTargetsReady=true;
      $('readout-arena-target').innerHTML='<option value="">旧版 NCT 日历（不更新正式 Arena 目标）</option>'+matches.map(t=>`<option value="${esc(t.id)}">${esc(t.trial_name||t.nct_id)} · ${esc(t.endpoint)}</option>`).join('');
      $('readout-arena-target').disabled=!matches.length;
      $('readout-arena-status').textContent=matches.length?`找到同 NCT 的 ${matches.length} 个当前正式目标；请显式选择并核对具体分析。未选择仍为旧版 NCT 日历。`:'未找到同 NCT 的当前正式 Arena 目标；只能提议旧版 NCT 日历，不能借用其他试验或旧目标。';
      updateProposalTarget();
    } catch(e) {
      if(revision!==proposalRevision||proposalSourceId!==id||!$('readout-propose-dialog').open)return;
      proposalTargets=[];proposalTargetsReady=false;$('readout-arena-target').value='';$('readout-arena-target').disabled=true;
      $('readout-arena-status').textContent='正式 Arena 目标读取失败：'+e.message+' 旧目标选择已清空；可继续旧版 NCT 日历，或重新打开此来源重试。';
      updateProposalTarget();
    }
  }
  function openProposal(id) {
    const entry=pages.intake.records.find(s=>s.id===id);
    if(!entry||entry.kind!=='official_announcement_candidate')return;
    resetProposal();proposalSourceId=id;const revision=proposalRevision;$('readout-nct').value=entry.nct_id;
    $('readout-source-detail').innerHTML=`<h3>${esc(entry.structured?.title||'公司原始公告')}</h3><p>${sourceLink(entry.source)} · <a href="/api/intake/candidates/${encodeURIComponent(entry.id)}/source">下载已抓取原始来源 ↓</a><br>来源公开日期：${esc(entry.source?.published_at||entry.structured?.published_at||'未知')}<br>抓取：${esc(entry.source?.retrieved_at||'未记录')}</p><span class="hash">来源 SHA-256 ${esc(entry.source?.sha256||'未记录')}</span>${officialText(entry)}`;
    updateDateFields();$('readout-propose-feedback').textContent='日期字段保持空白，请按原文填写；公告发布日期本身不是临床读出日。';
    if(!$('readout-propose-dialog').open)$('readout-propose-dialog').showModal();
    loadProposalTargets(id,revision,entry.nct_id);
  }
  function openReadoutReview(id) {
    activeReadout=pages.readout.records.find(r=>r.id===id);if(!activeReadout)return;
    ++readoutReviewRevision;
    const r=activeReadout,p=r.proposal;
    const binding=p.arena_target_binding,granularity={named_trial:'同名试验指引',exact_analysis:'明确指向精确分析'}[p.source_granularity]||'未记录';
    const scope=arenaReadout(p)?`<section class="detail-target"><h3>服务器绑定的正式 Arena 精确目标</h3><p>目标 ID：${esc(p.arena_target_id)}<br>来源层级：${esc(granularity)}<br>适用性：${esc(p.applicability)}</p>${targetReference(binding)}<p>分析时间：${esc(binding?.analysis_timepoint||'未记录')}</p><span class="hash">目标定义 SHA-256：${esc(p.arena_target_binding_sha256||'未记录')}</span></section>`:'<p class="detail-note">旧版 NCT 日历提议，不会更新正式 Arena 的精确目标日历。</p>';
    $('readout-review-detail').innerHTML=`<p>${esc(p.nct_id)} · ${esc(r.status)}</p><h3>${dateKind(p)}</h3><p>${esc(p.label)} · ${esc(dateRange(p))}<br>来源精度：${esc(p.precision)}</p>${scope}<blockquote>${esc(p.quoted_evidence)}</blockquote><p>${sourceLink(p.source)}<br>来源公开：${esc(p.source?.published_at||'未知')}<br>提议提交：${esc(r.created_at)}${r.decision?'<br>独立审核：'+esc(r.decision.created_at):''}</p><span class="hash">来源 SHA-256 ${esc(p.source?.sha256||'未记录')}</span><p>提议依据：${esc(p.reason)}</p>`;
    const unknownChecks=arenaReadout(p)&&p.date_kind==='readout_time_unconfirmed'?{
      source_quote_supports_date:'原文直接支持读出时间未确认；没有把未检到公告或登记完成日当作未确认的依据',
      precision_correct:'日期为空、来源精度为 unknown，与原文的未确认状态一致'}:{};
    $('readout-confirmations').innerHTML=Object.entries({...dateChecks,...unknownChecks,...(arenaReadout(p)?arenaDateCheck:{})}).map(([k,v])=>`<label class="check-label"><input type="checkbox" name="${k}">${esc(v)}</label>`).join('');
    $('readout-review-form').reset();
    for(const field of $('readout-review-form').elements)field.disabled=!r.can_review;
    $('readout-review-feedback').textContent=r.can_review?'通过后更新当前日历；不会改写原预测或自动判定临床疗效。':r.status==='pending_review'?'当前账号不能审核这条提议。提交者不可自审，请等待另一位独立审核员。':'这条提议已有不可覆盖的审核记录。';
    if(!$('readout-review-dialog').open)$('readout-review-dialog').showModal();
  }
  document.addEventListener('click',e=>{
    const actions=[['data-review',openOutcome],['data-link',openLink],['data-readout-propose',openProposal],['data-readout-review',openReadoutReview],['data-suggestions',openSuggestions],['data-suggestion-select',chooseSuggestion]];
    for(const [attr,handler] of actions){const b=e.target.closest(`[${attr}]`);if(b){handler(b.getAttribute(attr));return;}}
  });
  for(const name of Object.keys(pages))$(name+'-more').onclick=()=>loadPage(name);
  for(const [button,dialog] of [['close-review','review-dialog'],['close-link','link-dialog'],['close-readout-propose','readout-propose-dialog'],['close-readout-review','readout-review-dialog']])$(button).onclick=()=>$(dialog).close();
  $('link-dialog').addEventListener('close',()=>{if(!$('link-dialog').open)resetLink();});
  $('readout-propose-dialog').addEventListener('close',()=>{if(!$('readout-propose-dialog').open)resetProposal();});
  $('readout-review-dialog').addEventListener('close',()=>{if(!$('readout-review-dialog').open){++readoutReviewRevision;activeReadout=null;}});
  $('close-suggestions').onclick=()=>{++suggestionRequest;suggestionData=null;$('suggestions-dialog').close();};
  $('suggestions-retry').onclick=()=>openSuggestions(suggestionSourceId);
  $('link-snapshot').addEventListener('input',()=>{$('link-target-reference').hidden=true;$('link-target-reference').innerHTML='';});
  $('readout-kind').onchange=updateDateFields;
  $('readout-arena-target').onchange=updateProposalTarget;
  $('review-form').onsubmit=async e=>{
    e.preventDefault();const submit=e.currentTarget.querySelector('button[type="submit"]');submit.disabled=true;
    try {
      const confirmations=Object.fromEntries(Object.keys(checks).map(k=>[k,!!$('review-form').querySelector(`[name="${k}"]`).checked]));
      await request('/api/platform/reviews/'+encodeURIComponent(active.id),{decision:$('decision').value,reason:$('reason').value,confirmations});
      $('review-feedback').textContent='审核记录已保存。';await loadPage('outcome',true);
    } catch(e) {$('review-feedback').textContent=e.message;}finally{submit.disabled=false;}
  };
  $('link-form').onsubmit=async e=>{
    e.preventDefault();if(linkBusy)return;const submit=e.currentTarget.querySelector('button'),revision=linkRevision,id=sourceId;submit.disabled=true;linkBusy=true;
    try {
      const entry=pages.intake.records.find(x=>x.id===id);if(!entry)throw Error('来源已切换，请重新打开候选。');
      const ciFields=['link-ci-level','link-ci-low','link-ci-high'].map(id=>$(id).value);
      let confidenceInterval=null;
      if(ciFields.some(v=>v!=='')){
        if(ciFields.some(v=>v===''))throw Error('填写CI时须同时提供来源置信水平、下限和上限，不能默认95%。');
        const [level,lower,upper]=ciFields.map(Number);
        if(![level,lower,upper].every(Number.isFinite)||level<=0||level>=100)throw Error('置信水平须大于0且小于100%，上下限须为有效数值。');
        if(lower>upper)throw Error('CI下限不能大于上限。');
        confidenceInterval={level:level/100,lower,upper};
      }
      const body={article_id:$('link-article').value||null,snapshot_id:$('link-snapshot').value,endpoint:$('link-endpoint').value,population:$('link-population').value,comparison:$('link-comparison').value,label:$('link-label').value===''?null:Number($('link-label').value),readout_date:$('link-date').value,effect_size:$('link-effect-value').value!==''?{type:$('link-effect-type').value,value:Number($('link-effect-value').value)}:null,confidence_interval:confidenceInterval,p_value:$('link-p').value!==''?Number($('link-p').value):null};
      if(entry.kind==='official_announcement_candidate'){
        const quote=$('link-source-quote').value.trim(),date=$('link-source-publication-date').value;
        if(quote.length<20||quote.length>6000)throw Error('请连续摘录20至6000字的结果原文及试验身份。');
        if(!/^\d{4}-\d{2}-\d{2}$/.test(date))throw Error('请核实并填写本篇公告公开日期，不能沿用旧指引日期。');
        if(body.readout_date>date)throw Error('首次公开结果日期不能晚于所引用公告的公开日期。');
        body.source_quote=quote;body.source_publication_date=date;
      }
      await request('/api/intake/candidates/'+encodeURIComponent(id)+'/link',body);
      if(revision!==linkRevision)return;
      $('link-feedback').textContent='已保存，等待另一位独立审核员核验。';await loadPage('outcome',true);
    } catch(e) {if(revision===linkRevision)$('link-feedback').textContent=e.message;}finally{if(revision===linkRevision){submit.disabled=false;linkBusy=false;}}
  };
  $('readout-propose-form').onsubmit=async e=>{
    e.preventDefault();if(proposalBusy)return;
    const revision=proposalRevision,id=proposalSourceId;proposalBusy=true;$('readout-propose-submit').disabled=true;let saved=false;
    try {
      const entry=pages.intake.records.find(s=>s.id===id);
      if(!$('readout-propose-dialog').open||!entry||entry.kind!=='official_announcement_candidate'||$('readout-nct').value!==entry.nct_id)throw Error('来源已切换，请重新打开公司原始公告。');
      const exact=$('readout-kind').value==='official_exact_date',unknown=$('readout-kind').value==='readout_time_unconfirmed',target=selectedProposalTarget();
      if($('readout-arena-target').value&&!target)throw Error('正式 Arena 目标未载入或已切换，请重新选择当前来源的精确目标。');
      if(unknown&&!target)throw Error('只有显式选择正式 Arena 精确目标才能提议读出时间未确认。');
      const body={nct_id:entry.nct_id,candidate_id:id,date_kind:$('readout-kind').value,start:unknown?null:$('readout-start').value,end:unknown?null:exact?$('readout-start').value:$('readout-end').value,precision:unknown?'unknown':exact?'day':$('readout-precision').value,label:$('readout-label').value,quoted_evidence:$('readout-quote').value,reason:$('readout-reason').value};
      if(!unknown&&!exact&&body.end<body.start)throw Error('窗口结束不能早于开始。');
      if(target){
        const granularity=$('readout-source-granularity').value;
        if(!['named_trial','exact_analysis'].includes(granularity))throw Error('请选择原文支持的来源适用层级。');
        Object.assign(body,{arena_target_id:target.id,source_granularity:granularity,applicability:'exact_registered_target'});
      }
      for(const field of $('readout-propose-form').elements)field.disabled=true;
      await request('/api/readout-updates',body);saved=true;
      if(revision!==proposalRevision||proposalSourceId!==id)return;
      $('readout-propose-feedback').textContent='时间提议已保存，等待另一位独立审核员；当前日历尚未据此更新。';
      await loadPage('readout',true);
    } catch(e) {if(revision===proposalRevision)$('readout-propose-feedback').textContent=e.message;}finally{if(revision===proposalRevision){proposalBusy=false;for(const field of $('readout-propose-form').elements)field.disabled=false;$('readout-arena-target').disabled=!proposalTargetsReady||!proposalTargets.length;updateProposalTarget();$('readout-propose-submit').disabled=saved;}}
  };
  $('readout-review-form').onsubmit=async e=>{
    e.preventDefault();if(!activeReadout?.can_review){$('readout-review-feedback').textContent='当前账号不可审核此提议。';return;}
    const reviewing=activeReadout,revision=readoutReviewRevision;
    $('readout-review-submit').disabled=true;
    try {
      if(!$('readout-decision').value)throw Error('请选择审核结论。');
      const keys=[...Object.keys(dateChecks),...(arenaReadout(reviewing.proposal)?Object.keys(arenaDateCheck):[])];
      const confirmations=Object.fromEntries(keys.map(k=>[k,!!$('readout-review-form').querySelector(`[name="${k}"]`)?.checked]));
      if($('readout-decision').value==='approved'&&arenaReadout(reviewing.proposal)&&!confirmations.exact_target_applicability_confirmed)throw Error('请先确认此日程适用于服务器绑定的精确目标。');
      await request('/api/readout-updates/'+encodeURIComponent(reviewing.id)+'/review',{decision:$('readout-decision').value,reason:$('readout-review-reason').value,confirmations});
      if(revision!==readoutReviewRevision||activeReadout?.id!==reviewing.id)return;
      $('readout-review-feedback').textContent='时间审核记录已保存。通过的更新可在当前日历核对，原始预测保持不变。';
      reviewing.can_review=false;for(const field of $('readout-review-form').elements)field.disabled=true;
      await loadPage('readout',true);
    } catch(e) {if(revision===readoutReviewRevision){$('readout-review-feedback').textContent=e.message;$('readout-review-submit').disabled=false;}}
  };
  load();
})();
