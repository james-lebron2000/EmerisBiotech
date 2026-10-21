/* Frozen research records are read-only. Scenario controls never mutate them. */
(()=>{'use strict';
const el=id=>document.getElementById(id),esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const percent=p=>{if(!Number.isFinite(p)||p<0||p>1)throw Error('预测概率字段无效');return (p*100).toFixed(1)+'%';};
const range=v=>{if(!Array.isArray(v)||v.length!==2)throw Error('预测区间字段无效');return v.map(percent).join('–');};
let registry;
const save=(filename,data,type)=>{const a=document.createElement('a'),url=URL.createObjectURL(new Blob([data],{type}));a.href=url;a.download=filename;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
function sensitivityTable(comparison){
 const samples=comparison.sensitivity||[],values=samples.map(x=>x.statistical_success_probability);
 if(!samples.length)return '<p>没有已登记的敏感性情景。</p>';
 const pairs=[...new Set(samples.map(x=>x.transport_sd+'|'+x.pairwise_events))];
 const rows=pairs.map(key=>{const [sd,events]=key.split('|').map(Number);const at=alpha=>{const v=samples.find(x=>x.transport_sd===sd&&x.pairwise_events===events&&x.two_sided_alpha===alpha);return v?percent(v.statistical_success_probability):'未提供';};return `<tr><td>${sd.toFixed(2)}</td><td>${events}</td><td>${at(0.05)}</td><td>${at(0.025)}</td></tr>`;}).join('');
 return `<div><h4>${esc(comparison.arm_id)}</h4><p>已登记情景范围 <strong>${percent(Math.min(...values))}–${percent(Math.max(...values))}</strong>；不是置信区间。</p><div class="data-table"><table><caption>统计学优势概率 · 冻结的假设情景</caption><thead><tr><th>迁移 SD</th><th>事件数</th><th>α 0.05</th><th>α 0.025</th></tr></thead><tbody>${rows}</tbody></table></div></div>`;
}
function card(entry){const r=entry.record,f=r.forecast,spec=r.model_spec;
const rows=f.comparisons.map(c=>`<tr><th scope="row">${esc(c.arm_label)}</th><td><strong>${percent(c.direction_probability)}</strong><small>预测 HR＜1</small></td><td><strong>${percent(c.statistical_success_probability)}</strong><small>预测官方主要 PFS 统计学优势</small></td><td>待裁定</td></tr>`).join('');
const sourceIds=new Set(f.source_ids||[]),sources=(r.catalog_snapshot.sources||[]).filter(s=>sourceIds.has(s.id));
return `<article class="panel forecast-card" data-forecast-id="${esc(r.id)}"><div class="heading"><h3>PACIFIC-9 · 正式候选研究预测</h3><span class="tag amber">已冻结 · 未校准</span></div><p class="muted">冻结前证据复核 UTC：${esc(r.evidence_refresh.checked_at_utc)}。这是历史复核记录，本次打开页面不代表已重新检索结果。</p><p>目标：首次官方预设主要分析中的 ITT / BICR PFS。两组比较均以 durvalumab + placebo 为对照。</p><div class="forecast-table"><table><thead><tr><th>组合</th><th>HR 方向概率</th><th>统计学优势概率</th><th>实际结果</th></tr></thead><tbody>${rows}</tbody></table></div><p class="assessment"><strong>统计学优势概率采用不完整设计信息下的模型近似。</strong>事件数为每个两组比较 200 / 400 / 600 等权假设，α=0.05 为单次最终分析近似；实际事件数、期中分析及完整 SAP 未获得。它不代表已经验证的成功率。</p><p><strong>至少一组达到统计学优势：</strong>${range(f.trial_level.statistical_success_probability_bounds)}（在本版模型下的相关性未知界限，不是置信区间）。共享对照的两个比较没有按独立事件相乘。</p><details><summary>固定参数与敏感性分析</summary><p>先验 log(HR) ~ N(0, 0.35²)，迁移误差 SD=0.20；参数在计算本版候选结果前记录。另保留 α=0.025、迁移 SD=0.10/0.20/0.30 的敏感性结果；不按事后结果择优。</p><div class="sensitivity-grid">${f.comparisons.map(sensitivityTable).join('')}</div><p>表内数值来自冻结记录，比较不同假设的影响；不能事后选择表现最好的情景作为正式预测。</p></details><details><summary>预先定义的裁定规则</summary><p>HR 方向需要对应比较的官方数值；统计学优势需要官方明确预设主要终点达到或未达到。未知、未披露和不匹配的亚组结果保持待裁定。两个标的分别评分，不合并为四个独立临床试验。</p><pre>${esc(JSON.stringify(spec.adjudication,null,2))}</pre></details><details><summary>完整冻结记录、证据复核与版本</summary><pre>${esc(JSON.stringify(r,null,2))}</pre></details><div class="sources">${sources.map(s=>{try{const u=new URL(s.url);if(!['https:','http:'].includes(u.protocol))return '';return `<a href="${esc(u.href)}" target="_blank" rel="noopener noreferrer">${esc(s.title)} ↗</a>`;}catch{return '';}}).join('')}</div><p class="hash">冻结时间 UTC：${esc(r.frozen_at_utc)}<br>模型：${esc(f.model_version)}<br>记录：${esc(r.id)}<br>SHA-256：${esc(entry.record_sha256)}</p><small>本站版本记录可检查内容一致性；尚无第三方可信时间戳。冻结时间与网页实际发布时间分别记录，管理员能控制的服务器不构成独立证明。</small></article>`;
}
async function load(){const response=await fetch('/api/forecasts');if(!response.ok)throw Error('预测登记暂不可用，不能显示未核实数字。');registry=await response.json();if(!registry.integrity_verified)throw Error('预测记录完整性未通过');
el('forecastRecords').innerHTML=registry.records.map(card).join('')||'<p>尚无已冻结记录。</p>';
const trials=new Set(registry.records.map(e=>e.record.forecast.trial_id));
el('forecastSummary').innerHTML=[[trials.size,'已登记试验'],[registry.records.reduce((n,e)=>n+e.record.forecast.comparisons.length,0),'治疗比较（共享试验）'],[0,'已裁定试验 · 准确率不可估计']].map(([n,t])=>`<div class="metric"><strong>${n}</strong><span>${t}</span></div>`).join('');
el('forecastJson').disabled=false;el('forecastReport').disabled=false;
}
el('forecastJson').addEventListener('click',()=>save('emeris-frozen-candidate-forecasts.json',JSON.stringify(registry,null,2),'application/json'));
el('forecastReport').addEventListener('click',()=>save('emeris-candidate-forecast-report.html',`<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>Emeris 正式候选研究预测</title><style>body{font:15px/1.7 sans-serif;max-width:1000px;margin:30px auto;padding:20px;color:#173b37}table{width:100%;border-collapse:collapse}th,td{border:1px solid #ddd;padding:12px;text-align:left}small{display:block}pre{white-space:pre-wrap;overflow-wrap:anywhere}details{margin:20px 0}.hash{font-size:11px;overflow-wrap:anywhere}.sources a{display:block}</style><h1>正式候选研究预测 · 未校准</h1><p>冻结版本不因网页情景参数变化而修改；尚未裁定，前瞻准确率不可估计。</p>${el('forecastRecords').innerHTML}</html>`,'text/html;charset=utf-8'));
load().catch(error=>{el('forecastError').hidden=false;el('forecastError').textContent=error.message;el('forecastRecords').textContent='没有可展示的已核验预测。';});
})();
