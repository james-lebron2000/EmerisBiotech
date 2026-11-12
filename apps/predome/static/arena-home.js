/* Read-only homepage projection of registered formal cohorts. No provider execution. */
(() => {
  'use strict';
  const section = document.getElementById('challenge-calendar');
  if (!section) return;
  const $ = id => document.getElementById(id);
  const state = {data: null, cohorts: [], inactiveCohorts: [], cohortId: ''};
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function safeUrl(value) {
    try { const u = new URL(value); return ['https:', 'http:'].includes(u.protocol) && !u.username && !u.password ? u.href : ''; } catch { return ''; }
  }
  function link(url, text, className = '') {
    const href = safeUrl(url);
    return href ? `<a class="${className}" href="${esc(href)}" target="_blank" rel="noopener noreferrer">${esc(text)} ↗</a>` : `<span>${esc(text)}（链接待核实）</span>`;
  }
  const date = value => /^\d{4}-\d{2}-\d{2}/.test(String(value || '')) ? String(value).slice(0, 10) : '日期未提供';
  const issues = run => Array.isArray(run?.input_evidence_issues) ? run.input_evidence_issues.filter(i=>i && typeof i === 'object') : [];
  const blocked = run => issues(run).some(i=>i.severity === 'blocking');
  const numericProbability = run => run?.status === 'predicted' && typeof run.probability === 'number' && Number.isFinite(run.probability) && run.probability >= 0 && run.probability <= 1;
  const validProbability = run => numericProbability(run) && !blocked(run);
  const inactiveCohort = c => ['withdrawn_before_execution','withdrawn','superseded'].includes(c.execution_status) || c.status === 'superseded';
  function displayModel(modelId, run) {
    const model = state.data.models.find(m=>m.id === modelId) || {};
    const components = run?.provider_config_snapshot?.components || model.components;
    if (Array.isArray(components) && components.length && (run?.provider === 'multi_model_agent' || model.provider === 'multi_model_agent')) {
      return components.map(c=>c.requested_model || c.id).filter(v=>typeof v === 'string' && v).join(' → ') || modelId;
    }
    return typeof run?.requested_model === 'string' && run.requested_model ? run.requested_model : model.id || modelId;
  }
  const historical = target => ['archived_research','retrospective'].includes(target?.scope) || target?.record_kind === 'historical_replay';
  const actualStatus = target => historical(target) ? 'historical' : ['adjudicated','verified','agreement_pending_independent_review'].includes(target.status) ? (target.result?.review_status === 'accepted' || target.result?.scoring_eligible === true) && target.status !== 'agreement_pending_independent_review' ? 'adjudicated' : 'agreement_pending_independent_review' : ['pending','announced'].includes(target.status) ? target.status : 'pending';
  const statusName = status => ({pending: '待揭晓', announced: '已公布 · 待裁定', agreement_pending_independent_review: '待独立科学审核', adjudicated: '科学审核已接纳', historical: '历史回放 · 不计前瞻'}[status]);
  function windowGroup(target) {
    const readout = target.readout || {}, year = /^(20\d{2})/.exec(String(readout.start || ''))?.[1];
    if (!year) return {key:'unknown', year:'', label:'读出窗口待核实', sort:99999};
    if (readout.precision === 'year') return {key:year+'-year', year, label:year+' 年 · 年度窗口', sort:Number(year)*10};
    if (readout.precision === 'half_year') {
      const half = String(readout.start).slice(5,10) === '07-01' && String(readout.end).slice(5,10) === '12-31' ? 'H2' : String(readout.start).slice(5,10) === '01-01' && String(readout.end).slice(5,10) === '06-30' ? 'H1' : '';
      return {key:year+'-'+(half || 'half'), year, label:half ? `${year} 年 ${half} · ${half === 'H2' ? '下' : '上'}半年窗口` : `${year} 年 · 半年窗口（见来源）`, sort:Number(year)*10+(half==='H2'?2:1)};
    }
    return {key:year+'-'+String(readout.precision||'other'), year, label:`${year} 年 · ${readout.label || '来源披露窗口'}`, sort:Number(year)*10+3};
  }
  function current() {
    const cohort = state.cohorts.find(c => c.id === state.cohortId);
    if (!cohort) return {cohort:null,targets:[],runs:[]};
    const ids = new Set(cohort.target_ids || []);
    const targets = state.data.targets.filter(t => t.cohort_id === cohort.id && (!ids.size || ids.has(t.id)));
    const targetIds = new Set(targets.map(t=>t.id));
    const runs = state.data.runs.filter(r => r.cohort_id === cohort.id && targetIds.has(r.target_id) && r.track === 'same_evidence' && r.condition === 'clinical_only');
    return {cohort, targets, runs};
  }
  function modelCell(modelId, runs) {
    const list = runs.filter(r=>r.model_id===modelId);
    const run = list.length === 1 ? list[0] : null;
    const modelName = displayModel(modelId,run);
    const good = validProbability(run);
    const label = list.length > 1 ? '记录冲突 · 待核对' : !run ? '尚无分配记录' : blocked(run) ? '输入证据问题 · 暂停比较' : run.execution_state === 'running' && run.status === 'pending' ? '运行中' : ({predicted:'无有效概率',pending:'等待运行',abstained:'拒答',failed:'运行失败',timed_out:'请求超时',unavailable:'暂不可用',withdrawn:'已撤回 · 未执行'}[run.status] || '状态待核对');
    const shown = good ? `${Number((run.probability * 100).toFixed(1))}%` : '—';
    const marker = good ? `<circle cx="${5+90*run.probability}" cy="8" r="4" class="challenge-dot"/>` : '';
    return `<li class="challenge-model${good?' challenge-model-valid':''}${blocked(run)?' challenge-model-blocked':''}" data-model-id="${esc(modelId)}"${run ? ` data-run-id="${esc(run.id)}"` : ''}><span class="challenge-model-name">${esc(modelName)}</span><strong class="challenge-probability">${shown}</strong><svg class="challenge-probability-track" viewBox="0 0 100 16" aria-hidden="true"><line x1="5" x2="95" y1="8" y2="8"/>${marker}</svg><span class="challenge-model-status">${good?'指定终点达标概率':esc(label)}</span></li>`;
  }
  function evidenceNotice(runs) {
    const affected = runs.filter(r=>issues(r).length);
    if (!affected.length) return '';
    const hasBlocking = affected.some(blocked);
    return `<div class="challenge-evidence-warning${hasBlocking?' challenge-evidence-blocking':''}"><strong>${hasBlocking?'输入证据存在阻断问题 · 受影响输出暂停比较':'输入证据提示 · 请核对来源限制'}</strong><p>${hasBlocking?'受影响记录不计入当前有效概率。原始冻结输出保留，证据修正不会改写旧预测。':'冻结输出保持原值，新增问题另行记录。'}</p><details><summary>查看受影响模型与证据问题</summary>${affected.map(r=>`<div class="challenge-issue-record"><p>${esc(displayModel(r.model_id,r))}${numericProbability(r)?` · 原始冻结值 ${Number((r.probability*100).toFixed(1))}%${blocked(r)?'（暂停比较）':''}`:''}</p>${issues(r).map(i=>`<p>${esc(i.severity === 'blocking' ? '阻断' : '提示')} · ${esc(i.evidence_id || '来源记录')}：${esc(i.note || '问题说明待补充')}${i.reference_url ? '<br>'+link(i.reference_url,'核对问题来源'):''}</p>`).join('')}</div>`).join('')}</details></div>`;
  }
  function resultBlock(target) {
    const status = actualStatus(target), result = target.result;
    if (status === 'pending') return '<div class="challenge-result"><span class="challenge-result-label">实际结果</span><strong>待揭晓</strong><p>等待目标终点的公开读出；未公布不计为失败。</p></div>';
    const outcome = {success:'达到预设标准',failure:'未达到预设标准',mixed:'混合结果',inconclusive:'结论不确定',not_adjudicable:'无法裁定'}[result?.label] || '结果标签待核实';
    const waiting = status === 'agreement_pending_independent_review', replay = status === 'historical';
    const title = waiting ? '来源核对一致 · 待独立科学审核' : replay ? '历史结果记录 · ' + outcome : status === 'announced' ? '已公布 · 待独立裁定' : outcome;
    const note = waiting ? '来源一致性不等于科学接纳；当前不计命中或正式评分。' : replay ? '历史标签与来源保留；本回放不计入前瞻成绩。' : status === 'announced' ? '尚未进入正式成绩；已公布信息不等于目标终点已完成裁定。' : '仅对应已登记的人群、对照与终点。';
    const reported = status === 'announced' && ['success','failure'].includes(result?.label) ? `<p>披露标签：${result.label === 'success' ? '报告达标' : '报告未达标'}（待裁定）</p>` : '';
    const statistics = (status === 'adjudicated' || replay ? ['effect_size','confidence_interval','p_value'] : []).filter(k=>result?.[k] !== null && result?.[k] !== undefined).map(k=>{const value=result[k];return `<p>${{effect_size:'报告效应值',confidence_interval:'报告置信区间',p_value:'报告 P 值'}[k]}：${esc(typeof value === 'object' ? JSON.stringify(value) : value)}</p>`;}).join('');
    return `<div class="challenge-result" data-readout-state="${status}"><span class="challenge-result-label">实际结果</span><strong>${esc(title)}</strong><p>${esc(note)}</p>${reported}${statistics}${result?.first_public_at ? `<p>首次公开：${esc(date(result.first_public_at))}</p>`:''}${result?.source_url ? link(result.source_url,result.source_title || '核对结果原始来源'):''}</div>`;
  }
  function card(target, allRuns, modelIds) {
    const runs = allRuns.filter(r=>r.target_id===target.id), readout = target.readout || {};
    const detailUrl = '/arena?target='+encodeURIComponent(target.id);
    const sourceNote = readout.source_note || (readout.source_granularity === 'drug_program' ? '该年份来自药物项目层级指引，关联本试验属于明确标记的推断，非本试验承诺公布日期。' : '');
    const targetStatus = actualStatus(target);
    return `<article class="challenge-card" data-target-id="${esc(target.id)}"><div class="challenge-card-top"><p class="challenge-sponsor">${esc(target.sponsor || '申办方待核实')}</p><span class="challenge-state challenge-state-${targetStatus}">${statusName(targetStatus)}</span></div><h4><a href="${esc(detailUrl)}">${esc(target.trial_name || target.nct_id || target.id)} <span aria-hidden="true">↗</span></a></h4><p class="challenge-drug">${esc(target.drug)} <span>· ${esc(target.nct_id)}</span></p><p class="challenge-indication">${esc(target.indication)}</p><div class="challenge-window"><span>来源窗口</span><strong>${esc(readout.label || '未提供')}</strong>${readout.source_granularity === 'drug_program' ? '<em>项目层级指引 · 非试验承诺日期</em>':''}<p>${readout.source_url ? link(readout.source_url,'读出指引原文'): '来源链接待核实'}${readout.guidance_as_of ? ` · ${esc(date(readout.guidance_as_of))}`:''}${Array.isArray(readout.page_references) && readout.page_references.length ? ` · 第 ${esc(readout.page_references.join('、'))} 页`:''}</p></div><p class="challenge-endpoint"><span>预测目标</span>${esc(target.endpoint || '终点待核实')}</p><ul class="challenge-models" aria-label="${esc(target.trial_name)} 的模型预测">${modelIds.map(m=>modelCell(m,runs)).join('') || '<li class="challenge-no-model">尚无模型分配记录</li>'}</ul>${evidenceNotice(runs)}${resultBlock(target)}<details class="challenge-details"><summary>核对输入、成功标准与时间边界</summary><dl><dt>分析人群</dt><dd>${esc(target.population || '未提供')}</dd><dt>对照方案</dt><dd>${esc(target.comparison || '未提供')}</dd><dt>达标定义</dt><dd>${esc(target.success_definition || '未提供')}</dd><dt>分析时点</dt><dd>${esc(target.analysis_timepoint || '未提供')}</dd><dt>信息截止</dt><dd>${esc(target.information_cutoff_utc || target.data_cutoff || '未提供')}</dd>${sourceNote ? `<dt>窗口来源限制</dt><dd>${esc(sourceNote)}</dd>`:''}</dl></details><a class="challenge-detail-link" href="${esc(detailUrl)}">查看冻结记录、模型理由与来源 →</a></article>`;
  }
  function render() {
    const {cohort, targets, runs} = current();
    $('challenge-summary').innerHTML = '';
    if (!cohort) {
      const withdrawn = state.inactiveCohorts.filter(c=>c.execution_status === 'withdrawn_before_execution');
      $('challenge-status').textContent = state.inactiveCohorts.length ? '正式赛季证据纠正中。撤回或已被替代的批次不作为当前预测展示。' : '尚无正式赛季登记。既有冻结记录仍可在下方查看。';
      $('challenge-body').innerHTML = state.inactiveCohorts.length ? `<div class="challenge-empty challenge-correction"><strong>证据纠正中 · 暂无可展示的当前正式赛季</strong><p>${withdrawn.length ? '已有批次在模型调用前撤回。登记槽位不代表模型已运行，也不构成预测。' : '原批次已被替代，等待有效的新批次登记。'}</p>${state.inactiveCohorts.map(c=>`<details><summary>${esc(c.id)} · ${c.execution_status === 'withdrawn_before_execution' ? '执行前撤回' : '已停用'}</summary><p>${esc(c.withdrawal?.reason || '该批次已停止作为当前赛季使用。')}</p>${Number.isInteger(c.withdrawal?.model_calls) ? `<p>撤回记录中的模型调用：${c.withdrawal.model_calls} 次</p>`:''}</details>`).join('')}<a href="/arena">查看保留的登记与撤回记录 →</a></div>` : '<div class="challenge-empty">正式赛季登记后，这里将显示每个项目的模型输出与实际读出状态。当前不展示示例预测。</div>';
      $('challenge-filters').hidden = true;
      return;
    }
    $('challenge-filters').hidden = false;
    const modelIds = [...new Set(runs.map(r=>r.model_id))].sort();
    const assignments = new Map();
    runs.forEach(r=>{const key=JSON.stringify([r.target_id,r.model_id]);if(!assignments.has(key))assignments.set(key,[]);assignments.get(key).push(r);});
    const answered = [...assignments.values()].filter(group=>group.length===1 && validProbability(group[0])).length;
    const adjudicated = targets.filter(t=>actualStatus(t)==='adjudicated').length;
    $('challenge-summary').innerHTML = [[new Set(targets.map(t=>t.project_id || t.id)).size,'药物项目'],[modelIds.length,'模型 ID'],[`${answered} / ${runs.length}`,'有效概率 / 分配记录'],[`${adjudicated} / ${targets.length}`,'科学审核已接纳 / 登记目标']].map(([n,l])=>`<div><strong>${esc(n)}</strong><span>${esc(l)}</span></div>`).join('');
    const search = $('challenge-search').value.trim().toLocaleLowerCase(), year = $('challenge-year').value;
    const shown = targets.filter(t=>(!year||windowGroup(t).year===year) && (!search||[t.trial_name,t.drug,t.sponsor,t.nct_id,t.indication,t.endpoint].join(' ').toLocaleLowerCase().includes(search)));
    $('challenge-status').textContent = `已读取正式赛季 · ${cohort.id} · 当前显示 ${shown.length} / ${targets.length} 项。失败、拒答与等待运行均保留，不换算成 0%。`;
    const groups = new Map();
    shown.forEach(t=>{const g=windowGroup(t);if(!groups.has(g.key))groups.set(g.key,{...g,targets:[]});groups.get(g.key).targets.push(t);});
    $('challenge-body').innerHTML = groups.size ? [...groups.values()].sort((a,b)=>a.sort-b.sort).map(g=>`<section class="challenge-window-group" aria-label="${esc(g.label)}"><div class="challenge-group-title"><h3>${esc(g.label)}</h3><span>${g.targets.length} 项</span></div><div class="challenge-cards">${g.targets.sort((a,b)=>(a.trial_name||'').localeCompare(b.trial_name||'')).map(t=>card(t,runs,modelIds)).join('')}</div></section>`).join('') : '<div class="challenge-empty">当前筛选没有匹配项目。可以清空搜索或选择全部年份。</div>';
  }
  function updateYears() {
    const previous=$('challenge-year').value;
    const years=[...new Set(current().targets.map(t=>windowGroup(t).year).filter(Boolean))].sort();
    $('challenge-year').innerHTML='<option value="">全部年份</option>'+years.map(y=>`<option value="${esc(y)}">${esc(y)} 年</option>`).join('');
    if(years.includes(previous))$('challenge-year').value=previous;
  }
  async function load() {
    section.setAttribute('aria-busy','true');
    $('challenge-refresh').disabled=true;
    $('challenge-status').textContent='正在读取已登记赛季与真实模型运行记录…';
    $('challenge-body').innerHTML='';$('challenge-summary').innerHTML='';$('challenge-filters').hidden=true;
    try {
      const response=await fetch('/api/arena',{headers:{Accept:'application/json'},cache:'no-store'});
      if(!response.ok)throw new Error('HTTP '+response.status);
      const data=await response.json();
      if(!data || !['cohorts','targets','runs','models'].every(k=>Array.isArray(data[k])) || !data.cohorts.every(c=>c&&typeof c.id==='string') || !data.targets.every(t=>t&&typeof t.id==='string') || !data.runs.every(r=>r&&typeof r.id==='string') || !data.models.every(m=>m&&typeof m.id==='string')) throw new Error('schema');
      state.data=data;
      state.inactiveCohorts=data.cohorts.filter(c=>c.protocol?.season_kind==='registered_12_project_season' && inactiveCohort(c));
      state.cohorts=data.cohorts.filter(c=>c.protocol?.season_kind==='registered_12_project_season' && !inactiveCohort(c)).sort((a,b)=>(Date.parse(b.registered_at)||0)-(Date.parse(a.registered_at)||0)||b.id.localeCompare(a.id));
      if(!state.cohorts.some(c=>c.id===state.cohortId))state.cohortId=state.cohorts[0]?.id||'';
      $('challenge-cohort').innerHTML=state.cohorts.map(c=>`<option value="${esc(c.id)}">${esc(c.id)} · ${esc(date(c.registered_at))}</option>`).join('');
      $('challenge-cohort').value=state.cohortId;
      updateYears();render();
    } catch {
      state.data=null;state.cohorts=[];state.inactiveCohorts=[];state.cohortId='';
      $('challenge-status').textContent='正式赛季暂时无法读取。请刷新重试；当前不显示未核实或过期的概率。';
      $('challenge-body').innerHTML='<div class="challenge-empty challenge-error">数据接口或结构未通过检查。下方既有冻结日历独立载入。</div>';
    } finally {section.setAttribute('aria-busy','false');$('challenge-refresh').disabled=false;}
  }
  $('challenge-search').addEventListener('input',render);
  $('challenge-year').addEventListener('change',render);
  $('challenge-cohort').addEventListener('change',()=>{state.cohortId=$('challenge-cohort').value;updateYears();render();});
  $('challenge-reset').addEventListener('click',()=>{$('challenge-search').value='';$('challenge-year').value='';render();});
  $('challenge-refresh').addEventListener('click',load);
  load();
})();
