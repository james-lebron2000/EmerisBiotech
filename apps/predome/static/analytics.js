'use strict';
(()=>{
 const $=id=>document.getElementById(id),esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const num=(v,d=3)=>typeof v==='number'&&Number.isFinite(v)?v.toFixed(d):'—',pct=v=>typeof v==='number'&&Number.isFinite(v)?`${(v*100).toFixed(1)}%`:'—';
 const modelNames={'deepseek-v4-pro':'DeepSeek Pro','deepseek-flash':'DeepSeek Flash',beta_pooled:'归档训练集基础率',beta_area:'归档分领域基础率',constant_0_5:'固定 50% 基线',fixed_0_5:'固定 50% 无信息参照',design_logistic:'归档设计逻辑回归'};
 const names={oncology:'肿瘤',immunology:'免疫',neurology:'神经',metabolic:'代谢',unknown:'未知',randomized:'随机分组',retrospective_no_forward_horizon:'回测 · 无事前提前时间'};
 let data=null,requestId=0,registryRequest=0,registryOffset=0,registryNext=null;
 function theme(value){document.documentElement.dataset.theme=value;$('theme-toggle').textContent=value==='dark'?'浅色模式':'深色模式';try{localStorage.setItem('predome-theme',value);}catch{}}
 try{theme(localStorage.getItem('predome-theme')||'light');}catch{theme('light');}
 $('theme-toggle').addEventListener('click',()=>theme(document.documentElement.dataset.theme==='dark'?'light':'dark'));
 const table=(heads,rows)=>`<table><thead><tr>${heads.map(h=>`<th>${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.map(r=>`<tr>${r.map(c=>`<td>${c}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
 const empty=()=>'<div class="empty"><strong>尚无可评分的已核验预测</strong><span>只有在目标结果公布、来源与终点审核通过后，才进入前瞻评价。</span></div>';
 function svg(title,body,w=500,h=310){return `<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="${esc(title)}">${body}</svg>`;}
 function tipAttrs(value){return `tabindex="0" data-tip="${esc(value)}" aria-label="${esc(value)}"`;}
 function attachTips(id){const root=$(id);for(const element of root.querySelectorAll('[data-tip]')){const activate=()=>{$(id+'-tip').textContent=element.dataset.tip;};element.addEventListener('mouseenter',activate);element.addEventListener('focus',activate);element.addEventListener('click',activate);}}
 function calibration(){
  const bins=data.calibration_bins,points=bins.filter(b=>b.n>0);$('calibration-tip').textContent=points.length?'点击点查看分箱人数与区间；区间未调整同一药物多试验的相关性。':'Insufficient verified predictions to establish performance.';
  if(!points.length){$('calibration').innerHTML=empty();}else{
   const x=p=>55+p*405,y=p=>255-p*215;
   let plot='';for(let i=0;i<=5;i++){const p=i/5;plot+=`<line class="axis" x1="55" y1="${y(p)}" x2="460" y2="${y(p)}"/><text x="45" y="${y(p)+4}" text-anchor="end">${Math.round(p*100)}%</text><text x="${x(p)}" y="276" text-anchor="middle">${Math.round(p*100)}%</text>`;}
   plot+='<text x="55" y="16">实际达标频率</text><text x="255" y="302" text-anchor="middle">模型预测概率</text>';
   plot+=`<line class="ideal" x1="${x(0)}" y1="${y(0)}" x2="${x(1)}" y2="${y(1)}"/>`;
   plot+=`<polyline class="curve" points="${points.map(b=>`${x(b.mean_probability)},${y(b.observed_rate)}`).join(' ')}"/>`;
   plot+=points.map(b=>{const px=x(b.mean_probability),lo=y(b.wilson95[0]),hi=y(b.wilson95[1]),tip=`分箱 ${pct(b.lower)}–${pct(b.upper)}；n=${b.n}，资产组=${b.asset_groups}；平均概率 ${pct(b.mean_probability)}；实际 ${b.successes}/${b.n}（${pct(b.observed_rate)}）；Wilson 95% ${pct(b.wilson95[0])}–${pct(b.wilson95[1])}`;return `<line class="interval" x1="${px}" y1="${lo}" x2="${px}" y2="${hi}"/><line class="interval" x1="${px-5}" y1="${lo}" x2="${px+5}" y2="${lo}"/><line class="interval" x1="${px-5}" y1="${hi}" x2="${px+5}" y2="${hi}"/><circle class="point" cx="${px}" cy="${y(b.observed_rate)}" r="6" ${tipAttrs(tip)}/><text x="${px+10}" y="${y(b.observed_rate)-9}">n=${b.n}</text>`;}).join('');
   $('calibration').innerHTML=svg('校准分箱与 Wilson 95% 描述性区间',plot);attachTips('calibration');
  }
  $('calibration-table').innerHTML=table(['概率箱','n / 资产组','平均概率','实际频率','Wilson 95%'],bins.map(b=>[`${pct(b.lower)}–${pct(b.upper)}`,`${b.n} / ${b.asset_groups}`,pct(b.mean_probability),pct(b.observed_rate),b.wilson95?`${pct(b.wilson95[0])}–${pct(b.wilson95[1])}`:'—']));
 }
 function distribution(){
  const bins=data.distribution,max=Math.max(1,...bins.flatMap(b=>[b.success,b.failure]));$('distribution-tip').textContent=data.summary.answered?'点击柱形查看实际达标与未达标计数。':'没有结果标签，不能绘制成功/失败分布。';
  if(!data.summary.answered){$('distribution').innerHTML=empty();}else{
   const y=n=>255-215*n/max;let plot='';for(let i=0;i<=max;i+=Math.max(1,Math.ceil(max/5))){plot+=`<line class="axis" x1="45" y1="${y(i)}" x2="480" y2="${y(i)}"/><text x="36" y="${y(i)+4}" text-anchor="end">${i}</text>`;}
   plot+='<text x="45" y="17">试验数量</text><text x="265" y="302" text-anchor="middle">预测概率区间（%）</text>';
   plot+=bins.map((b,i)=>{const x=49+i*43;return `<rect class="good" x="${x}" y="${y(b.success)}" width="16" height="${255-y(b.success)}" ${tipAttrs(`${pct(b.lower)}–${pct(b.upper)}：实际达标 ${b.success} 项`)}/><rect class="bad" x="${x+18}" y="${y(b.failure)}" width="16" height="${255-y(b.failure)}" ${tipAttrs(`${pct(b.lower)}–${pct(b.upper)}：实际未达标 ${b.failure} 项`)}/><text x="${x+16}" y="275" text-anchor="middle">${Math.round(b.lower*100)}</text>`;}).join('');$('distribution').innerHTML=svg('按真实结果分层的预测概率分布',plot);attachTips('distribution');
  }
  $('distribution-table').innerHTML=table(['概率区间','实际达标','实际未达标'],bins.map(b=>[`${pct(b.lower)}–${pct(b.upper)}`,String(b.success),String(b.failure)]));
 }
 function cumulative(){
  const records=data.cumulative;$('cumulative-note').textContent=`${data.scope==='retrospective'?'历史回测结果按过去的公布日期重排，不代表当时已生成预测。':'按已核验的实际结果公布日期排序。'}滚动窗口为最近 ${data.rolling} 条，包含边界日期的全部同日记录。Brier 越低越好。`;
  $('cumulative-tip').textContent=records.length?'点击数据点查看累计/滚动分母。':'没有可评价结果，不绘制累计曲线。';
  if(!records.length){$('cumulative').innerHTML=empty();}else{
   const start=Date.parse(records[0].date),end=Date.parse(records[records.length-1].date),x=d=>end===start?460:60+(Date.parse(d)-start)/(end-start)*800,y=v=>245-v*215;
   let plot='';for(let i=0;i<=5;i++){plot+=`<line class="axis" x1="60" y1="${y(i/5)}" x2="860" y2="${y(i/5)}"/><text x="48" y="${y(i/5)+4}" text-anchor="end">${(i/5).toFixed(1)}</text>`;}
   plot+='<text x="60" y="15">Brier</text>';
   ['cumulative','rolling'].forEach((type,idx)=>{const cls=idx?'bad':'good';plot+=`<polyline class="line-${cls}" points="${records.map(r=>`${x(r.date)},${y(r[type].brier)}`).join(' ')}"/>`;plot+=records.map(r=>`<circle class="dot-${cls}" cx="${x(r.date)}" cy="${y(r[type].brier)}" r="5" ${tipAttrs(`${r.date} · ${idx?'滚动':'累计'} n=${r[type].n}，Brier=${num(r[type].brier)}，累计资产组=${r.asset_groups}`)}/>`).join('');});
   records.forEach((r,i)=>{if(i===0||i===records.length-1||i%Math.max(1,Math.ceil(records.length/5))===0)plot+=`<text x="${x(r.date)}" y="273" text-anchor="middle">${esc(r.date)}</text>`;});$('cumulative').innerHTML=svg('累计与滚动 Brier 时间趋势',plot,920,300);attachTips('cumulative');
  }
  $('cumulative-table').innerHTML=table(['结果日期','累计 n','累计 Brier','滚动 n','滚动 Brier','累计资产组'],records.map(r=>[esc(r.date),String(r.cumulative.n),num(r.cumulative.brier),String(r.rolling.n),num(r.rolling.brier),String(r.asset_groups)]));
 }
 function subgroups(){const key=$('subgroup').value,groups=data.subgroups[key]||[];$('subgroups').innerHTML=groups.length?table(['组别','总数 / 可评分','资产组','Brier / 准确率'],groups.map(g=>[esc(names[g.value]||g.value),`${g.total} / ${g.n}`,String(g.asset_groups),g.metrics?`${num(g.metrics.brier)} / ${pct(g.metrics.accuracy)}`:'<span class="badge">样本不足 · 不展示分数</span>'])):'<p class="caption">尚无该范围的已核验记录。</p>';}
 const reasons={awaiting_independent_verified_readout:'等待独立核验的结果',baseline_not_registered_before_readout:'基线登记未早于结果公布',prediction_not_eligible_for_prospective_comparison:'原预测未满足前瞻比较条件',private_snapshot_not_in_public_baseline_registry:'私有快照不纳入公开账本',baseline_integrity_or_binding_failed:'基线指纹或目标绑定核验失败',baseline_not_registered:'尚未登记基线',baseline_ledger_ineligible:'账本记录不合格',baseline_integrity_unverified:'基线完整性未核验',baseline_missing_full_target:'目标定义不完整',baseline_trial_id_mismatch:'试验标识不一致',baseline_prediction_id_mismatch:'预测标识不一致',baseline_snapshot_id_mismatch:'来源快照不一致',baseline_snapshot_hash_mismatch:'来源快照指纹不一致',baseline_not_fixed_half:'不符合固定 50% 策略',baseline_probability_not_eligible:'参照概率不合格',baseline_missing_policy_sha256:'缺少策略指纹',baseline_missing_record_sha256:'缺少登记指纹',baseline_missing_id:'缺少登记标识',baseline_feature_inputs_not_empty:'基线含有特征输入',baseline_not_before_readout:'基线登记未早于结果公布',baseline_evidence_cutoff_mismatch:'证据截止不一致',baseline_invalid_cutoff_order:'时间顺序不合格',baseline_missing_audit_timestamps:'缺少审计时间',ambiguous_baseline_records:'存在无法区分的基线记录',baseline_missing:'缺少已冻结基线',baseline_locked_after_readout:'基线锁定太晚',baseline_not_pre_readout:'基线未在结果前锁定',baseline_target_mismatch:'目标不一致',baseline_hash_invalid:'基线指纹未通过',baseline_policy_mismatch:'策略版本不一致'};
 function baselineCoverage(){
  const prospective=data.scope==='prospective';$('prospective-baseline-panel').hidden=!prospective;
  $('baseline-description').textContent=prospective?'固定 50% 只作为无信息参照。评分仅使用所选模型与基线共同合格、目标相同的已核验记录；基线实际锁定必须早于结果，不为旧预测回填时间。':'使用归档的基线预测，不用测试集标签重新估计基础成功率。下列方法共享同一批可评分目标。';
  if(!prospective)return;
  const c=data.baseline_coverage;
  $('baseline-coverage').innerHTML=c?[[c.total_cases,'所选模型可评分目标'],[c.eligible_cases,'可与基线配对评分'],[c.missing_cases,'缺少基线'],[c.excluded_cases,'基线未符合评分条件'],[pct(c.coverage),'可评分目标的配对覆盖率']].map(([v,label])=>`<article><span>${esc(label)}</span><strong>${esc(v??'未知')}</strong></article>`).join(''):'<p class="caption">当前响应没有基线覆盖信息，不能据此判断已冻结或待结果数量。</p>';
  $('baseline-coverage-note').textContent=!c?'基线覆盖尚未返回。':c.status==='no_scored_cases'?'尚无可评分的已核验结果，因此没有配对分数。上方 0 不表示没有待结果基线；已冻结状态见下方公开账本。':`仅在所选模型的可评分目标中计算配对覆盖率；排除原因：${Object.entries(c.reason_counts||{}).map(([key,n])=>(reasons[key]||key)+' '+n+' 条').join('；')||'未提供或无排除'}。缺失和太晚锁定的基线均不纳入比较。`;
 }
 const registryStates={awaiting_verified_readout:'等待结果公布与审核',timing_eligible_after_verified_readout:'结果已审核 · 登记时间符合条件',not_eligible:'未符合评分条件'};
 function registryBusy(value){$('baseline-prev').disabled=value||registryOffset===0;$('baseline-next').disabled=value||registryNext===null;$('baseline-refresh').disabled=value;}
 function clearRegistry(){++registryRequest;registryOffset=0;registryNext=null;for(const id of ['baseline-registry','baseline-registry-summary','baseline-registry-summary-note','baseline-registry-status','baseline-page'])$(id).textContent='';registryBusy(false);}
 async function loadRegistry(offset=0){
  if(!data||data.scope!=='prospective')return;
  const serial=++registryRequest;registryOffset=offset;registryNext=null;registryBusy(true);$('baseline-registry-status').textContent='正在读取公开基线账本…';$('baseline-registry').textContent='';$('baseline-page').textContent='';
  try{
   const response=await fetch('/api/platform/baselines?limit=20&offset='+offset,{cache:'no-store'});if(!response.ok)throw Error('账本读取失败');const result=await response.json();
   if(serial!==registryRequest||!data||data.scope!=='prospective')return;
   if(!Array.isArray(result.records)||!result.summary||result.summary.scope!=='public_snapshot_targets_not_model_rows')throw Error('账本格式不一致');
   const s=result.summary;
   $('baseline-registry-summary').innerHTML=[[s.registered_snapshot_count,'已登记冻结基线'],[s.awaiting_verified_readout_count,'等待结果与审核'],[s.missing_registration_count,'缺少基线登记'],[s.pre_readout_registered_verified_count,'已核验且登记时间符合'],[s.late_registered_verified_count,'登记未早于结果'],[s.other_ineligible_verified_count,'其他未符合条件']].map(([n,label])=>`<article><span>${esc(label)}</span><strong>${esc(n??'未知')}</strong></article>`).join('');
   $('baseline-registry-summary-note').textContent=`以上覆盖全部公开锁定目标快照 ${s.public_locked_snapshot_count??'未知'} 条，不是所选模型行数，也不是当前页数量。登记时间符合仍需通过最早版本和同目标配对规则才进入上方评分。策略：${s.policy_version||'未提供'}。`;
   $('baseline-registry').innerHTML=result.records.length?table(['目标与来源快照','固定参照 / 状态','基线实际登记 / 原证据截止','登记指纹与规则'],result.records.map(r=>{
    const detail=Array.isArray(r.exclusion_reasons)?r.exclusion_reasons.map(k=>reasons[k]||k).join('；'):'';
    return [`${esc(r.trial_id||'未提供')}<small>${esc(r.prediction_id)}</small>${r.target?`<details><summary>${esc(r.target.endpoint||'查看目标定义')}</summary><p>人群：${esc(r.target.population||'未提供')}<br>比较：${esc(r.target.comparison||'未提供')}<br>达标定义：${esc(r.target.success_definition||'未提供')}</p></details>`:''}<a href="/api/platform/snapshots/${encodeURIComponent(r.snapshot_id||'')}" target="_blank" rel="noopener noreferrer">查看来源预测快照 ↗</a><span class="hash">${esc(r.snapshot_id)}<br>SHA-256 ${esc(r.snapshot_sha256)}</span>`,`${pct(r.probability)}<small>${esc(registryStates[r.state]||'状态未知')}${detail?' · '+esc(detail):''}</small><small>${r.integrity_verified===true?'指纹完整性已核对':'完整性未确认'}</small>`,`${esc(r.recorded_at||'未提供')}<small>真实基线登记时间</small>${esc(r.effective_evidence_cutoff||'未提供')}<small>沿用原预测证据截止，不是登记时间</small>${r.source_forecast_locked_at?esc(r.source_forecast_locked_at)+'<small>原预测锁定时间</small>':''}`,`<span class="hash">${esc(r.baseline_id)}<br>SHA-256 ${esc(r.record_sha256)}<br>目标 SHA-256 ${esc(r.target_sha256)}<br>策略 SHA-256 ${esc(r.policy_sha256)}</span>${esc(r.policy_version||'未提供')}<small>策略固定 50% · 不读取药物特征</small><small>${esc(r.registration_origin||'登记来源未提供')}</small>`];
   })):'<p class="caption">此页没有公开基线记录；以上全目标统计独立显示，不据空页推断没有待办。</p>';
   const pagination=result.pagination||{};registryNext=pagination.has_more&&Number.isInteger(pagination.next_offset)&&pagination.next_offset>offset?pagination.next_offset:null;
   $('baseline-page').textContent=`显示 ${result.records.length?offset+1:0}–${offset+result.records.length} 条`;
   $('baseline-registry-status').textContent=`已读取 ${result.records.length} 条公开基线登记。此账本显示冻结状态，不代表已有性能验证。`;
  }catch(e){if(serial!==registryRequest)return;$('baseline-registry').textContent='';$('baseline-registry-summary').textContent='';$('baseline-registry-summary-note').textContent='';$('baseline-registry-status').textContent='公开基线账本读取或完整性核验失败，暂不能确认冻结数量。可重新读取；模型评价数据独立显示。';}
  finally{if(serial===registryRequest)registryBusy(false);}
 }
 $('baseline-prev').addEventListener('click',()=>loadRegistry(Math.max(0,registryOffset-20)));
 $('baseline-next').addEventListener('click',()=>{if(registryNext!==null)loadRegistry(registryNext);});
 $('baseline-refresh').addEventListener('click',()=>loadRegistry(registryOffset));
 function render(){
  const s=data.summary,retro=data.scope==='retrospective';$('scope-note').textContent=retro?'Retrospective Evaluation · 此处模型在结果公布后运行。历史特征有时间截断，但预训练记忆无法排除；同一资产的多项试验并非独立样本，所有分数仅描述这批回测。':s.answered?'前瞻评价只纳入公布前锁定、目标一致且已审核的公开记录。样本数量和模型版本分别记录；不将历史回测加入分母。':'Insufficient verified predictions to establish performance. 当前尚无通过前瞻核验的可评分记录。';
  $('summary').innerHTML=[[`${s.answered} / ${s.total_records}`,'可评分 / 已纳入记录',`拒答 ${s.abstained} · 失败 ${s.failed??0} · 不可用 ${s.unavailable??0}`],[String(s.asset_groups),'已知药物资产组',`不同试验 ${s.distinct_trials} 项`],[pct(s.accuracy),'固定阈值方向准确率','阈值 ≥50%；概率并非确定结论'],[num(s.brier),'Brier 概率误差','越低越好 · 不作单例质量判断'],[pct(s.coverage),'预测回答覆盖率',s.performance_status==='insufficient_verified_predictions'?'不足以建立稳定表现结论':'描述性分析 · 非临床验证']].map(([v,l,n])=>`<article><span>${esc(l)}</span><strong>${esc(v)}</strong><small>${esc(n)}</small></article>`).join('');
  calibration();distribution();cumulative();subgroups();baselineCoverage();
  $('baselines').innerHTML=data.summary.answered>0&&data.baseline_common_n>0&&data.baselines.length?table(['方法','相同 n','Brier','Log loss'],data.baselines.map(b=>[esc(modelNames[b.name]||b.name),String(b.n),num(b.brier),num(b.log_loss)])):'<p class="caption">尚无具备相同目标与信息截止记录的已归档基线，暂不比较。</p>';
  $('baseline-note').textContent=data.scope==='prospective'?`共同可评分样本 n=${data.baseline_common_n}。固定 50% 不读取药物特征、不拟合结果标签，也不是历史成功率估计。没有合格结果时不显示 Brier 或 Log loss；同批比较只描述这些目标。`:`共同可评分样本 n=${data.baseline_common_n}。归档设计基线与 LLM 读取的证据内容有所不同；同样本比较不等于证明某条技术路径更优，也不按此样本挑选模型。`;
  $('records').innerHTML=table(['试验 / 目标','概率 / 回答状态','实际','证据截止 / 生成时间','范围','来源'],data.records.map(r=>{let url='';try{const u=new URL(r.source_url);if(['https:','http:'].includes(u.protocol))url=u.href;}catch{}return [esc(r.trial_name)+`<small>${esc(r.trial_id)} · ${esc(r.endpoint)}</small>`,pct(r.probability)+`<small>${esc(({predicted:'已回答',abstain:'模型拒答',failed:'运行失败',unavailable:'模型不可用'})[r.prediction_status]||'状态未记录')} · ${esc(r.model_version||r.model)}</small>`,r.label===1?'达标':'未达标',`${esc(r.features_as_of||'未提供')}<small>${esc(r.prediction_at||'未提供')}</small>`,retro?'事后回测':'前瞻已审核',url?`<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">结果来源 ↗</a>`:'未提供'];}));
  $('method').innerHTML=`<p>固定分箱：0–20%、20–40%、40–60%、60–80%、80–100%；边界归入右侧箱，100%归入最后一箱。拒答不转换为零概率或阴性结果。</p><p>校准区间采用 Wilson 95%，为描述性二项区间，未调整药物资产内相关性；不能把这些窄或宽区间当作独立外部验证。子组至少需20条可评分记录、5个已知资产组才显示描述性分数。</p><p>回答覆盖率保留已独立核验且符合时间和完整目标要求的公开请求，拒答、失败、不可用均在分母中；未锁定草稿不在分母中。概率评分只使用已回答记录。同一试验、完整目标、模型标识采用首次公开锁定请求，跨后续版本也不替换最初拒答或失败；模型版本分开显示。本次排除记录 ${data.excluded.length} 条。没有充分验证数据时不声称校准完成或临床准确率。</p><p>输入版本<span class="hash">${esc(data.provenance.dashboard.expanded_release_sha256||'由审核账本提供')}</span></p>`;
 }
 async function load(){const id=++requestId;clearRegistry();$('prospective-baseline-panel').hidden=true;$('status').textContent='正在核验并计算所选范围…';$('download').disabled=true;try{const params=new URLSearchParams({scope:$('scope').value,model:$('model').value,rolling:$('rolling').value});const response=await fetch('/api/analytics?'+params,{cache:'no-store'});if(!response.ok)throw Error('读取失败');const next=await response.json();if(id!==requestId)return;if(next.schema_version!=='predome.analytics.v1')throw Error('格式错误');data=next;const selected=$('model').value;$('model').innerHTML=data.models.map(m=>`<option value="${esc(m)}">${esc(modelNames[m]||m)}</option>`).join('');$('model').value=selected;render();$('status').textContent=`已计算 ${data.scope==='prospective'?'前瞻已审核记录':'历史回测记录'} · ${modelNames[data.model]||data.model} · ${data.summary.answered} 条可评分记录`;$('download').disabled=false;if(data.scope==='prospective')loadRegistry();}catch(e){if(id!==requestId)return;data=null;clearRegistry();$('prospective-baseline-panel').hidden=true;$('status').textContent='读取或完整性核验失败，当前不展示性能指标。请重试。';['summary','scope-note','calibration','distribution','cumulative','calibration-table','distribution-table','cumulative-table','subgroups','baselines','baseline-note','records','method','calibration-tip','distribution-tip','cumulative-tip'].forEach(k=>$(k).textContent='');}}
 ['scope','model','rolling'].forEach(id=>$(id).addEventListener('change',load));$('subgroup').addEventListener('change',()=>{if(data)subgroups();});
 $('download').addEventListener('click',()=>{if(!data)return;const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download=`Predome-analytics-${data.scope}-${data.model}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});load();
})();
