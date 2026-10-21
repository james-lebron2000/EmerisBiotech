(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const validP = v => typeof v === 'number' && Number.isFinite(v) && v >= 0 && v <= 1;
  const pct = v => validP(v) ? `${(v * 100).toFixed(1)}%` : '未预测';
  const fmt = v => {
    if (!v) return '未知 / 尚未记录';
    if (!String(v).includes('T')) return String(v) + '（来源日期）';
    const d = new Date(v);
    return Number.isNaN(d.valueOf()) ? '日期格式未确认' : new Intl.DateTimeFormat('zh-CN', {
      timeZone:'Asia/Taipei',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'
    }).format(d) + '（台北）';
  };
  const url = u => {try {const x = new URL(u);return ['http:','https:'].includes(x.protocol) ? x.href : '';} catch {return '';}};
  const source = s => url(s?.url) ? `<a href="${esc(url(s.url))}" target="_blank" rel="noopener noreferrer">${esc(new URL(s.url).hostname)} ↗</a><small> 来源日期：${esc(fmt(s.published_at || s.source_date || s.availability_date))}${s.retrieved_at||s.retrieved_at_utc?' · 抓取：'+esc(fmt(s.retrieved_at||s.retrieved_at_utc)):''}</small>${s.locator?'<p class="caption">定位：'+esc(s.locator)+'</p>':''}<span class="hash">${esc(s.sha256 || '')}</span>${s.hash_scope?'<small>哈希范围：'+esc(s.hash_scope)+'</small>':''}` : '来源未提供';
  const val = v => {
    if (v === null || v === undefined) return '未抽取 / 未报告';
    if (Array.isArray(v)) return v.map(String).join(' – ');
    if (typeof v === 'object') {
      if (v.measure && v.value !== undefined) return `${v.measure} ${v.value}${v.unit ? ' ' + v.unit : ''}`;
      if (v.lower !== undefined && v.upper !== undefined) return `${v.lower} – ${v.upper}${typeof v.level === 'number' && v.level > 0 && v.level <= 1 ? ' (' + (v.level * 100) + '% CI)' : ''}`;
      return JSON.stringify(v);
    }
    return String(v);
  };
  const section = (n, eyebrow, title, content) => `<section class="panel"><p class="eyebrow">${n} / ${eyebrow}</p><h2>${esc(title)}</h2>${content}</section>`;
  function drugAnnotation(t) {
    const a=t?.drug_annotation;if(!a)return '';
    return `<article class="detail-target"><h3>索引药物：${esc(a.asset||t.asset)}</h3><dl><dt>药物类型</dt><dd>${esc(t.modality_zh||t.modality)} · ${esc(t.modality)}</dd><dt>分子靶点</dt><dd>${esc(t.molecular_target_zh||t.molecular_target)}</dd></dl><p>${esc(a.mechanism?.zh||a.mechanism?.en||'机制未提供')}${a.payload_target?'<br>载荷作用靶点：'+esc(a.payload_target):''}</p><p class="detail-note">注释仅覆盖本页索引药物，不代表整个联合治疗方案。机制资料经系统核对来源，尚未经独立人工审核。它不证明临床疗效，也未作为新证据改写已冻结预测。</p><details><summary>核对机制原始出处及记录范围</summary><ul class="source-list">${(a.sources||[a.source].filter(Boolean)).map(s=>'<li>'+source(s)+'</li>').join('')}</ul></details></article>`;
  }
  function readoutRows(t) {
    const exact=t.readout_date_kind==='official_exact_date';
    let rows=`<tr><td>${exact?'Official Announced Readout Date':'Estimated Readout Window'}</td><td>${exact?esc(t.official_announced_readout_date||t.window.start):esc(t.window.start)+' — '+esc(t.window.end)}</td><td>${exact?'公司明确的读出日期，不表示结果已经发布。':'公司预计窗口；'+esc(t.window.precision)+'。保留来源精度，实际结果见已核验记录。'}</td></tr>`;
    if(t.readout_update){const old=t.previous_display_window||t.original_window;rows+=`<tr><td>更新前窗口</td><td>${esc(old?.start)} — ${esc(old?.end)}</td><td>${esc(old?.label)}；原预测保留</td></tr><tr><td>时间更新批准</td><td>${esc(fmt(t.readout_update.provenance?.approved_at))}</td><td>独立审核当前日历时间，不是疗效结果核验</td></tr>`;}
    return rows;
  }
  function readoutEvidence(t) {
    const u=t?.readout_update;if(!u)return '';
    return `<details><summary>读出时间更新的原文与审核</summary><blockquote>${esc(u.quoted_evidence)}</blockquote><p>${source(u.source)}</p><p>批准：${esc(fmt(u.provenance?.approved_at))}。原预测输入、概率及锁定时间保持不变。</p></details>`;
  }
  const decision = f => !validP(f.probability) ? 'Inconclusive · 未给出概率' : f.probability === .5 ? 'Inconclusive · 50%' : f.probability > .5 ? 'Positive · 预计达标' : 'Negative · 预计不达标';
  function forecastCards(forecasts, publicOnly = false) {
    return `<div class="model-output">${Object.entries(forecasts || {}).map(([m,f]) => `<article><strong>${esc(m)}</strong><div class="probability">${pct(f.probability)}</div><p>${decision(f)}</p>${publicOnly ? '<p class="caption">公开摘要仅显示已授权披露的目标、概率和版本。</p>' : `<p>${esc(f.reasoning || '未提供理由')}</p><p>关键风险：${esc([].concat(f.key_unknowns || []).join('；') || '未单独列出')}</p>`}<p class="caption">生成：${esc(fmt(f.generated_at_utc || f.created_at_utc))}<br>模型版本：${esc(f.model_version || "未记录")}</p></article>`).join('')}</div>`;
  }
 function ensembleCard(e,kind='report'){
  const cls=kind==='report'?'model-card':'detail-target';
  if(!e||typeof e!=='object')return `<article class="${cls} ensemble-card" data-ensemble-status="legacy"><h3>固定等权描述性汇总</h3><p>本版本未预先记录集成${kind==='report'?'':'，无预先锁定的集成概率'}。保留原模型输出，不追补或重算旧预测。</p></article>`;
  const valid=p=>typeof p==='number'&&Number.isFinite(p)&&p>=0&&p<=1;
  const available=e.status==='available'&&valid(e.probability),members=Array.isArray(e.members)?e.members:[];
  const statuses={predicted:'已提供概率',abstain:'模型拒答',failed:'运行失败',unavailable:'模型不可用',missing:'未提供该模型'};
  const state=e.status==='not_applicable'?'本方法不适用集成':'集成不可用';
  const heading=available?`<div class="probability ensemble-probability">${pct(e.probability)}</div>`:`<p class="ensemble-unavailable"><strong>${state}</strong> · 不显示集成概率</p>`;
  const memberList=members.length?'<ul class="ensemble-members">'+members.map(m=>`<li><strong>${esc(m.model||'模型未提供')}</strong> · 策略权重 ${valid(m.weight)?pct(m.weight):'未知'} · ${esc(statuses[m.status]||m.status||'状态未知')} · 成员概率 ${m.status==='predicted'&&valid(m.probability)?pct(m.probability):'未提供'}<br><span class="caption">请求模型：${esc(m.model_version||'未提供')}<br>提供商返回模型：${esc(m.returned_model||'未提供')}</span></li>`).join('')+'</ul>':'<p>成员记录未提供，不能在页面自行推算。</p>';
  const hashes=members.map(m=>`<p>${esc(m.model||'模型未提供')}</p><span class="hash">组件 SHA-256：${esc(m.component_sha256||'未提供')}<br>输入 SHA-256：${esc(m.input_sha256||'未提供')}<br>请求 SHA-256：${esc(m.request_sha256||'未提供')}<br>提示词 SHA-256：${esc(m.prompt_sha256||'未提供')}</span>`).join('');
  return `<article class="${cls} ensemble-card" data-ensemble-status="${available?'available':e.status==='not_applicable'?'not_applicable':'unavailable'}"><h3>固定等权描述性汇总</h3>${heading}<p>${available?'两个指定模型各占 50% 权重；汇总值来自该版本已保存的记录。':esc(e.reason||'两模型完整输出或一致输入绑定未满足要求，不能汇总。')}</p><p class="caption">这是未经校准的描述性汇总，不代表预测能力已经提升，不计入模型绩效。两个成员必须同时满足规则；拒答、单模型或失败时，不把剩余模型改成 100% 权重。模型分歧不是置信区间。</p>${memberList}<details class="ensemble-audit"><summary>查看输入、目标、策略与组件指纹</summary><p>生成时间：${esc(e.generated_at_utc||'未提供')}<br>证据截止：${esc(e.data_cutoff||'未提供')}<br>策略版本：${esc(e.policy_version||'未提供')}</p><span class="hash">策略 SHA-256：${esc(e.policy_sha256||'未提供')}<br>目标 SHA-256：${esc(e.target_sha256||'未提供')}<br>输入 SHA-256：${esc(e.input_sha256||'未提供')}</span>${hashes}<p class="caption">指纹用于追溯绑定，不能单凭公开摘要复算完整私有输入；它不证明医学有效性。</p></details></article>`;
 }
  function models(targets) {
    return targets.map(t => `<article class="detail-target"><h3>${esc((t.target_definition || []).map(x => x.measure).join('；'))}</h3><p>事件定义：${esc(t.success_rule)}</p><p><span class="badge">${t.verification_status === 'approved' ? 'Verified · 本目标已核验' : 'Awaiting Readout · 本目标待核验'}</span></p>${ensembleCard(t.ensemble,'public')}${forecastCards(t.forecasts)}<p class="caption">模型概率差：${pct(t.model_disagreement)}；这是模型间差异，不是统计学置信区间。概率未经前瞻校准。</p></article>`).join('');
  }
  function publicVersions(published) {
    return published.map(envelope => {
      const r = envelope.record, target = r.target || {};
      return `<article class="detail-target"><h3>${esc(target.endpoint || '指定终点')}</h3><p>达标事件：${esc(target.success_definition)}</p><details><summary>查看公开目标人群与比较</summary><p class="criteria">人群：${esc(target.population)}</p><p class="criteria">比较：${esc(target.comparison)}</p></details>${ensembleCard(r.ensemble,'public')}${forecastCards(r.forecasts, true)}<dl><dt>模型 / 流程版本</dt><dd>${esc(r.model_version)} / ${esc(r.pipeline_version)}</dd><dt>证据截止</dt><dd>${esc(fmt(r.data_cutoff))}</dd><dt>预测生成</dt><dd>${esc(fmt(r.prediction_timestamp))}</dd><dt>预测锁定</dt><dd>${esc(fmt(r.locked_at))}</dd></dl><span class="hash">快照 ${esc(r.snapshot_id)}<br>SHA-256 ${esc(envelope.sha256)}</span><p class="caption">哈希对应完整锁定快照；公开摘要不能单独复算完整快照哈希。服务器时间不是独立可信时间戳。</p></article>`;
    }).join('');
  }
  function approvedSourceExcerpt(s) {
    if(typeof s?.excerpt!=='string'||!s.excerpt)return '';
    const basis=s.publication_date_basis==='document_metadata'?'从原公告元数据读取':'由提交者依据原文填写';
    return `<details class="approved-source-excerpt"><summary>查看已核验的公告原文摘录</summary><blockquote>${esc(s.excerpt)}</blockquote><p>本公告发布日期：${esc(fmt(s.published_at))}；${basis}，已随该结果接受独立审核。公告日期与首次结果读出日期分别保留。</p><span class="hash">摘录 SHA-256：${esc(s.excerpt_sha256||'未记录')}</span></details>`;
  }
  function assessmentText(a, label) {
    if (!a.score_eligible) return a.coverage_eligible ? 'Indeterminate · 未回答' : 'Not Evaluable · 不计前瞻表现';
    if (!validP(a.probability) || ![0,1].includes(label)) return 'Indeterminate · 信息不足';
    return (a.probability >= .5) === Boolean(label) ? 'Correct · 阈值方向一致' : 'Incorrect · 阈值方向不一致';
  }
  function approvedResults(outcomes) {
    if (!outcomes.length) return '<p>尚无本试验目标的独立批准结果。缺少公告或核验不能推断试验失败。</p>';
    return outcomes.map(r => `<article class="detail-target"><h3>${esc(r.target?.endpoint)}</h3><p><strong class="${r.label === 1 ? 'result-yes' : 'result-no'}">${r.label === 1 ? '达到指定终点' : '未达到指定终点'}</strong> · Verified · 独立核验已批准</p><p>实际读出：${esc(fmt(r.readout_date || r.result_date))}<br>审核完成：${esc(fmt(r.verified_at))}</p><div class="model-output"><article><h3>Locked Prediction</h3>${Object.entries(r.forecasts || {}).map(([m,f]) => `<p>${esc(m)}：${pct(f.probability)}</p>`).join('')}</article><article><h3>Actual Readout</h3><p>Effect Size：${esc(val(r.effect_size))}<br>CI（按来源记录）：${esc(val(r.confidence_interval))}<br>P Value：${esc(val(r.p_value))}</p></article></div><p>${source(r.source)}</p>${approvedSourceExcerpt(r.source)}<p class="caption">${r.eligible_for_prospective ? '本结果至少有一个模型记录满足前瞻评价条件；模型和版本适用范围以下表为准。' : '结果已核验，但此记录未满足前瞻绩效条件；不计作事前命中。'} 逐条方向按预先采用的 ≥50% 阈值判断；单次结果不证明模型校准。</p><div class="table-scroll"><table class="results-table"><thead><tr><th>模型版本</th><th>概率</th><th>预测与实际</th><th>绩效范围</th></tr></thead><tbody>${(r.assessments || []).map(a => `<tr><td>${esc(a.model)} · ${esc(a.model_version)}</td><td>${pct(a.probability)}</td><td>${esc(assessmentText(a,r.label))}</td><td>${a.score_eligible ? 'Prospective · 可前瞻评分' : a.coverage_eligible ? '前瞻覆盖 · 未回答，不计概率评分' : a.scope === 'retrospective' ? 'Retrospective · 事后记录' : 'Not Evaluable · 不进入前瞻绩效'}</td></tr>`).join('')}</tbody></table></div><span class="hash">快照 ${esc(r.snapshot_id)}<br>结果 SHA-256 ${esc(r.outcome_sha256)}</span></article>`).join('');
  }
  function uniqueDate(values) {
    const dates = [...new Set(values.filter(Boolean))];
    return dates.length === 1 ? fmt(dates[0]) : dates.length ? '多个目标 / 版本，见下方逐项日期' : '未知 / 尚未记录';
  }
  function comparableForm(initial) {
    return `<p class="detail-note">查询当前 ClinicalTrials.gov 的疾病关键词匹配登记。它提供比较证据线索，尚未验证机制、人群或疗效的可迁移性；不会修改本页已冻结的预测或充当历史时点的模型输入。</p><form id="comparable-form"><label for="comparable-condition">疾病关键词（可编辑；点击查询后发送至 ClinicalTrials.gov）</label><div class="filters"><input id="comparable-condition" name="condition" value="${esc(initial || '')}" minlength="3" maxlength="180" required placeholder="例如 Non-Small Cell Lung Cancer" autocomplete="off" aria-describedby="comparable-help"><button id="comparable-submit" class="button primary" type="submit">查询当前登记</button></div><p id="comparable-help" class="caption">${initial ? '初值来自本页公开适应症，请按研究问题修改。' : '尚无明确的公开适应症字段，请自行输入；不会从用户目标、药名或私有输入推断。'} 最多展示5条；不提供相似度分数或疗效排名。</p></form><p id="comparable-status" role="status" aria-live="polite">尚未查询。输入关键词并提交后才访问外部登记库。</p><div id="comparable-results" aria-busy="false"></div>`;
  }
  function comparableCard(r) {
    const design = r.study_design || {}, enrollment = r.enrollment || {};
    const fields = [
      ['申办方 / 公司',r.sponsor],['登记适应症',Array.isArray(r.conditions) ? r.conditions.join('；') : null],
      ['分期',Array.isArray(r.phase) ? r.phase.join(' / ') : null],
      ['分配方式',design.allocation],['干预模型',design.interventionModel],
      ['研究目的',design.primaryPurpose],['盲法',design.maskingInfo?.masking],
      ['登记入组人数',enrollment.count === 0 || enrollment.count ? `${enrollment.count}（${enrollment.type || '类型未知'}）` : null]
    ];
    const endpoints = Array.isArray(r.primary_endpoints) ? r.primary_endpoints : [];
    return `<article class="detail-target comparable-card"><h3>${esc(r.trial_name || '登记标题未提供')}</h3><p>${esc(r.nct_id)} · 当前登记查询结果</p><dl>${fields.map(([name,value]) => `<dt>${esc(name)}</dt><dd>${esc(value || '未抽取 / 未报告')}</dd>`).join('')}</dl><p class="caption">入组人数保留登记类型：ACTUAL 为实际值，ESTIMATED 为估计值；不能把实际值称作计划样本量。</p><details><summary>查看主要终点与登记设计</summary>${endpoints.length ? '<ul>'+endpoints.map(e=>`<li><strong>${esc(e.measure || '终点名称未报告')}</strong><p>观察时间窗：${esc(e.timeFrame || '未报告')}</p>${e.description?'<p>'+esc(e.description)+'</p>':''}</li>`).join('')+'</ul>' : '<p>主要终点未抽取 / 未报告。</p>'}<p class="caption">登记终点不是已公布疗效结果；本查询没有提取成功/失败标签。</p></details><p>登记更新日期：${esc(fmt(r.registry_updated_at))}（不是读出日期）</p>${url(r.source_url)?`<a href="${esc(url(r.source_url))}" target="_blank" rel="noopener noreferrer">查看 ClinicalTrials.gov 完整登记设计 ↗</a>`:'<p>登记原文链接未提供。</p>'}</article>`;
  }
  function bindComparables(id) {
    const form=$('comparable-form'), input=$('comparable-condition'), button=$('comparable-submit');
    form.addEventListener('submit', async event => {
      event.preventDefault();
      const condition=input.value.trim();
      if(condition.length<3 || condition.length>180){$('comparable-status').textContent='请输入3至180个字符的疾病关键词。';input.focus();return;}
      const controller=new AbortController(), timer=setTimeout(()=>controller.abort(),25000);
      button.disabled=true;input.disabled=true;$('comparable-results').setAttribute('aria-busy','true');
      $('comparable-results').replaceChildren();$('comparable-status').textContent='正在查询当前登记库…结果不会写入已冻结预测。';
      try {
        const params=new URLSearchParams({condition,exclude_nct:id});
        const response=await fetch('/api/predict/comparables?'+params.toString(),{cache:'no-store',signal:controller.signal});
        if(!response.ok)throw Error(response.status===429?'查询较频繁，请稍后重试。':'登记查询暂未完成，请稍后重试。');
        const data=await response.json();
        if(!Array.isArray(data.records))throw Error('登记查询返回格式异常，请稍后重试。');
        const records=data.records.filter(r=>r && /^NCT\d{8}$/.test(r.nct_id) && r.nct_id!==id).slice(0,5);
        const total=Number.isInteger(data.total_registry_matches) && data.total_registry_matches>=0 ? String(data.total_registry_matches) : '未知';
        const hash=typeof data.response_sha256==='string' && /^[a-f0-9]{64}$/i.test(data.response_sha256) ? data.response_sha256 : '未提供';
        $('comparable-results').innerHTML=`<div class="provenance-box"><p>本次关键词：${esc(condition)}<br>查询抓取：${esc(fmt(data.retrieved_at))}<br>登记库命中总量：${esc(total)}；排除本试验后本页展示 ${records.length} 条（上限5条）。</p><p class="caption">按登记库返回顺序显示，未经机制、设计或疗效可比性的专家裁定。以下数据来自本次查询，不代表冻结时点可获得的证据。</p><span class="hash">原始登记 API 响应 SHA-256：${esc(hash)}</span><p class="caption">哈希覆盖本次原始 API 响应字节，不是单条试验或已冻结预测的哈希。</p>${url(data.source)?`<a href="${esc(url(data.source))}" target="_blank" rel="noopener noreferrer">登记 API 来源 ↗</a>`:''}</div>${records.map(comparableCard).join('')}`;
        $('comparable-status').textContent=records.length?`找到 ${records.length} 条当前匹配登记，仅供比较证据整理。`:'本次查询未返回可展示的其他登记。可修改关键词重试；这不表示不存在相关试验。';
      } catch(e) {
        $('comparable-status').textContent=e.name==='AbortError'?'查询超时，请稍后重试。':e.message || '查询未完成，请重试。';
      } finally {
        clearTimeout(timer);button.disabled=false;input.disabled=false;$('comparable-results').setAttribute('aria-busy','false');
      }
    });
  }
  async function load() {
    try {
      const id = decodeURIComponent(location.pathname.split('/').pop());
      if (!/^NCT\d{8}$/.test(id)) throw Error('试验编号无效');
      const resp = await fetch('/api/product/trials/' + encodeURIComponent(id), {cache:'no-store'});
      if (!resp.ok) throw Error(resp.status === 404 ? '此试验尚未进入平台公开档案。可前往提交试验页面导入 NCT。' : '记录读取或完整性核验失败');
      const d = await resp.json(), t = d.upcoming, h = (d.historical || [])[0];
      const published = d.public_predictions || [], outcomes = d.public_verified_outcomes || [];
      const userOnly = !t && !h;
      if (userOnly && !published.length) throw Error('此试验没有可公开的记录。');
      const title = t?.trial_name || h?.trial_name || id;
      const dates = [
        ['Trial Registration', fmt(t?.registered_at)],
        ['Prediction Generated', h ? uniqueDate(Object.values(h.forecasts||{}).map(f=>f.created_at_utc)) : uniqueDate(published.map(p => p.record.prediction_timestamp))],
        ['Prediction Locked', uniqueDate(published.map(p => p.record.locked_at).concat((t?.targets || []).map(x => x.frozen_at_utc)))],
        ['Public Readout', uniqueDate(outcomes.map(r => r.readout_date || r.result_date).concat(h ? [h.result_date] : []))],
        ['Outcome Verified', uniqueDate(outcomes.map(r => r.verified_at))]
      ];
      const overview = `<section class="panel"><p class="eyebrow">01 / TRIAL OVERVIEW</p><h1>${esc(title)}</h1><p>${esc(id)}${t?.company || h?.asset ? ' · ' + esc(t?.company || h?.asset) : ''}</p><p>${esc(t?.indication || h?.endpoint || '用户公开锁定的研究预测')}</p><a href="https://clinicaltrials.gov/study/${esc(id)}" target="_blank" rel="noopener noreferrer">查看 ClinicalTrials.gov ↗</a>${userOnly ? '<p class="detail-note">输入由用户提供。此页展示已授权公开的锁定摘要；快照完整性不代表登记字段准确、证据完整或临床有效性。该记录不等同于平台核对过的归档证据包。</p>' : ''}<div class="date-track">${dates.map(([name,value]) => `<div><small>${name}</small><strong>${esc(value)}</strong></div>`).join('')}</div><p class="caption">生成、锁定、公告和核验分别记录；缺失日期不推断。历史回测发生在结果公布之后。</p></section>`;
      let design;
      if (t) design = `<p>${esc(t.registry_summary)}</p><dl><dt>分期</dt><dd>${esc((t.phase || []).join('/'))}</dd><dt>分组</dt><dd>${esc(t.trial_design?.allocation || '未知')}</dd><dt>治疗 / 对照</dt><dd>${(t.inputs?.arms || []).map(a => esc(a.label)).join(' / ')}</dd><dt>入组规模</dt><dd>${esc(t.inputs?.design?.enrollmentInfo?.count ?? '未知')}（${esc(t.inputs?.design?.enrollmentInfo?.type || '未知')}）</dd></dl><div class="table-scroll"><table class="readout-table"><thead><tr><th>日期类型</th><th>日期 / 范围</th><th>含义</th></tr></thead><tbody>${readoutRows(t)}<tr><td>Primary Completion Date</td><td>${esc(t.primary_completion_date?.date || '未知')}</td><td>登记主要完成日，不是读出日</td></tr><tr><td>Study Completion Date</td><td>${esc(t.study_completion_date?.date || '未知')}</td><td>登记研究完成日，不是读出日</td></tr></tbody></table></div><p class="caption">${esc(t.status_note)}</p>${readoutEvidence(t)}${drugAnnotation(t)}`;
      else if (h) design = `<p>历史设计快照：${esc(fmt(h.features_as_of))}。${source(h.feature_source)}</p>`;
      else design = '<p>公开摘要未提供经过平台核对的完整登记设计、入组规模或读出窗口。已公开的目标人群与比较见下方预测记录。</p>';
      const prediction = t ? models(t.targets) : h ? ensembleCard(h.ensemble,'public')+forecastCards(h.forecasts) : publicVersions(published);
      const history = published.length ? publicVersions(published) : t ? t.targets.map(tg => `<details><summary>${esc(tg.case_id)} · ${esc(tg.pipeline_version)}</summary><p>证据截止：${esc(fmt(tg.evidence_as_of_utc))}<br>冻结：${esc(fmt(tg.frozen_at_utc))}</p><span class="hash">${esc(tg.record_sha256)}</span></details>`).join('') : `<p>结果：${esc(fmt(h.result_date))}<br>回测模型：${esc(uniqueDate(Object.values(h.forecasts||{}).map(f=>f.created_at_utc)))}</p><p>这条历史回测不能计入前瞻表现。</p>`;
      const hd=h?.result_details;
      const historicalResult = h ? `<article class="detail-target"><h3>历史回测对照的已公布结果</h3><p><strong>${h.actual_label === 1 ? '达到主要终点' : '未达到主要终点'}</strong> · 首次公告 ${esc(fmt(h.result_date))}</p><p>${esc(h.endpoint)}</p><p>首次结果依据：${source(h.label_source)}</p>${(hd?.endpoints||[]).map(e=>`<article class="model-card"><h3>${esc(e.endpoint)}</h3><p>${esc(e.population)}<br>${esc(e.comparison)}</p><p>Effect Size：${esc(val(e.effect_size))}<br>CI：${esc(val(e.confidence_interval))}<br>P Value：${esc(e.p_value_text??val(e.p_value))}</p><p>${esc(e.analysis)}<br>${esc(e.interpretation_note)}</p><p>详细结果论文：${source(e.source)}</p></article>`).join('')||'<p>数值暂未提供；不从二元标签推算效应量、CI或P值。</p>'}<p>${(hd?.notes||[]).map(esc).join('<br>')}</p>${hd?.source?.unresolved_correction?`<p><a href="${esc(url(hd.source.unresolved_correction.url))}" target="_blank" rel="noopener">待核对的勘误 ↗</a></p>`:''}<p class="caption">数值经系统来源核对，尚非独立人工审核。SHA 对应已保存的论文索引与摘要响应，不代表出版社全文。详细论文日期与首次公告分别保留；结果发表于回测之前，不计作事前预测成功。</p></article>` : '';
      const inputs = t ? `<details><summary>原始入排标准</summary><p class="criteria">${esc(t.inputs?.eligibility?.eligibilityCriteria)}</p></details>` : userOnly ? '<p>私有方案正文、私有输入字段与可能引用方案的模型理由不在公开页面披露。</p>' : '';
      const evidence = t ? (t.sources || []).map(s => `<li>${source(s)}</li>`).join('') : h ? `<li>${source(h.feature_source)}</li>` : published.flatMap(p => (p.record.evidence_sources || []).map(s => `<li>${source(s)}</li>`)).join('');
      $('trial-page').innerHTML = overview + section('02','STUDY DESIGN','研究设计与时间',design)
        + section('03','PREDOME PREDICTION',h ? '历史回测 · Retrospective Evaluation' : `已锁定预测 · ${t?.prediction_status || '公开版本与核验状态见下方'}`,prediction)
        + section('04','EVIDENCE & REASONING','输入、解释与证据边界',`<p>模型理由是研究判断，不是已证实的因果机制。缺失信息保持未知。</p>${inputs}<p>模型训练数据截止未能独立确认；历史评估无法排除预训练记忆。当前概率未经前瞻校准。</p>`)
        + section('05','COMPARABLE TRIALS','相似登记与比较线索',comparableForm(!userOnly && typeof t?.indication==='string' ? t.indication.slice(0,180) : ''))
        + section('06','PREDICTION HISTORY','预测快照与版本',history)
        + section('07','ACTUAL READOUT','实际临床结果',approvedResults(outcomes) + historicalResult)
        + section('08','VERIFICATION & SOURCES','核验标准与原始来源',`<p>必须匹配同一终点、人群、比较、预设分析及公布时间，并由授权独立审核员确认。核验结果与是否满足前瞻绩效条件分别显示。</p><ul class="source-list">${evidence || '<li>未附可公开的输入来源。</li>'}</ul><a href="/analytics">整体概率评分与校准 →</a>`);
      $('detail-status').textContent = `${userOnly ? '公开锁定摘要已读取；用户输入证据等级单独标注' : '归档记录完整性核验通过'} · ${id}`;
      bindComparables(id);
    } catch (e) {
      $('detail-status').textContent = e.message;
      $('trial-page').innerHTML = '<a class="button primary" href="/predict">导入或提交试验 →</a>';
    }
  }
  load();
})();
