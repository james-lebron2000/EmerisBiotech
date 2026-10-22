/* Sanitized public clinical projection only. Never fetch the internal arena API. */
(() => {
  'use strict';
  const $=id=>document.getElementById(id),arr=x=>Array.isArray(x)?x:[],str=x=>typeof x==='string'?x:'',esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const short=(x,n=180)=>str(x).length>n?str(x).slice(0,n)+'…':str(x),query=new URLSearchParams(location.search);
  const state={data:null,detail:null,targetId:query.get('target')||'',period:query.get('period')==='reported'?'reported':'upcoming',current:[],reported:[],request:0};
  const inactive=c=>['withdrawn_before_execution','withdrawn','superseded'].includes(c?.execution_status)||c?.status==='superseded';
  const isReported=t=>['announced','adjudicated','verified','result_announced'].includes(t?.status)||['announced','adjudicated','verified'].includes(t?.result?.status);
  const day=x=>/^\d{4}-\d{2}-\d{2}/.test(str(x))?x.slice(0,10):'—';
  const utc=x=>{const d=new Date(x);return str(x)&&Number.isFinite(d.getTime())?d.toISOString().slice(0,19).replace('T',' ')+' UTC':'—';};
  const outcome=x=>['Success','Failure','Uncertain'].includes(x)?x:'Uncertain';
  function safeURL(x){if(typeof x!=='string'||!x)return '';try{const u=new URL(x,location.origin);return ['https:','http:'].includes(u.protocol)&&!u.username&&!u.password?u.href:'';}catch{return '';}}
  function link(url,label){const u=safeURL(url);return u?`<a href="${esc(u)}"${new URL(u).origin!==location.origin?' target="_blank" rel="noopener noreferrer"':''}>${esc(label)} ↗</a>`:'—';}
  function readoutSource(r){return safeURL(r?.source_url)?' · '+link(r.source_url,'Source'):'';}
  const urlFor=id=>'/arena?'+new URLSearchParams({target:id}).toString();
  function safeModel(run){return str(run.requested_model)||str(run.model_id)||'—';}
  function ownRuns(){const t=state.detail?.target;return arr(state.detail?.runs).filter(r=>r.target_id===t?.id&&r.condition==='clinical_only'&&(r.track==='same_evidence'||(t?.scope==='archived_research'&&r.track==='archived'&&r.scope==='archived_research')));}
  function displayStatus(run){return {failed:'No usable response',timed_out:'Timed out',abstained:'Abstained',abstain:'Abstained',pending:run.execution_state==='running'?'Running':'Awaiting run',withdrawn:'Withdrawn before execution',unavailable:'Unavailable'}[run.status]||'';}
  function availableTargets(){return state.period==='reported'?state.reported:state.current.filter(t=>!isReported(t));}
  function prepare(data){
    state.data=data;
    const cohorts=arr(data.cohorts).filter(c=>c&&(c.kind==='registered_12_project_season'||c.protocol?.season_kind==='registered_12_project_season')&&!inactive(c));
    const latest=cohorts.find(c=>c.id===data.current_cohort_id)||cohorts.sort((a,b)=>(Date.parse(b.registered_at)||0)-(Date.parse(a.registered_at)||0))[0];
    state.current=data.targets.filter(t=>latest&&t.cohort_id===latest.id&&!inactive(t)).sort((a,b)=>str(a.trial_name).localeCompare(str(b.trial_name)));
    // Retain distinct exact hypotheses. Revisions of the same clinical question use
    // the current formal record first, otherwise the most recently registered one.
    const ranked=data.targets.filter(t=>isReported(t)&&!inactive(t)).sort((a,b)=>Number(state.current.some(c=>c.id===b.id))-Number(state.current.some(c=>c.id===a.id))||(Date.parse(b.registered_at)||0)-(Date.parse(a.registered_at)||0));
    const groups=new Map();ranked.forEach(t=>{const key=JSON.stringify([t.nct_id,t.endpoint,t.population,t.comparison,t.success_definition]);if(!groups.has(key))groups.set(key,t);});state.reported=[...groups.values()];
  }
  function controls(){
    $('public-controls').hidden=false;let available=availableTargets();const explicit=arr(state.data?.targets).find(t=>t.id===state.targetId);
    if(explicit&&!available.some(t=>t.id===explicit.id))available=[explicit,...available];
    const nctCounts=new Map();available.forEach(t=>nctCounts.set(t.nct_id,(nctCounts.get(t.nct_id)||0)+1));
    $('public-trial').innerHTML=available.map(t=>`<option value="${esc(t.id)}">${esc(t.trial_name||t.nct_id||'Unnamed trial')}${nctCounts.get(t.nct_id)>1?' · '+esc(short(t.endpoint,65)):''}${!state.current.some(c=>c.id===t.id)?' · Earlier record':''}</option>`).join('');$('public-trial').value=state.targetId;
    document.querySelectorAll('[data-public-period]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.publicPeriod===state.period)));
    $('public-count').textContent=state.period==='reported'?`${state.reported.length} reported study questions`:`${state.current.length} current formal trials`;
  }
  function banner(t){
    const current=state.current.some(c=>c.id===t.id),latest=state.current.find(c=>c.nct_id&&c.nct_id===t.nct_id&&c.id!==t.id);
    const replay=t.scope==='archived_research'||t.scope==='retrospective';if(current)return '';
    const label=inactive(t)?'Original record · Withdrawn before execution':replay?'Historical replay · Not a prospective prediction':'Earlier record · Original target preserved';
    return `<aside class="public-record-banner" data-original-record="true"><strong>${label}</strong><span>${replay?'This analysis may postdate the public result and is not evidence of a prospective hit.':inactive(t)?'Registration did not result in model execution.':'This is the exact record in the selected link; it has not been replaced by a later prediction.'}</span>${latest?`<br><a class="public-latest-link" href="${esc(urlFor(latest.id))}">View the latest formal record for this trial →</a>`:''}</aside>`;
  }
  const registryLabels={RANDOMIZED:'Randomized',NON_RANDOMIZED:'Non-randomized',PARALLEL:'Parallel group',CROSSOVER:'Crossover',SINGLE_GROUP:'Single group',FACTORIAL:'Factorial',SEQUENTIAL:'Sequential',NONE:'Open label',SINGLE:'Single masked',DOUBLE:'Double masked',TRIPLE:'Triple masked',QUADRUPLE:'Quadruple masked',TREATMENT:'Treatment',PREVENTION:'Prevention',ACTIVE_NOT_RECRUITING:'Active, not recruiting',RECRUITING:'Recruiting',NOT_YET_RECRUITING:'Not yet recruiting',COMPLETED:'Completed',TERMINATED:'Terminated',SUSPENDED:'Suspended',WITHDRAWN:'Withdrawn',ENROLLING_BY_INVITATION:'Enrolling by invitation',UNKNOWN:'Unknown'};
  const registryText=value=>registryLabels[value]||str(value);
  function designText(d){if(typeof d==='string')return d||'—';if(!d||typeof d!=='object')return '—';return [d.allocation,d.intervention_model,d.masking,d.primary_purpose].map(v=>registryText(typeof v==='string'?v:typeof v?.masking==='string'?v.masking:'')).filter(Boolean).join(' · ')||'—';}
  function facts(t){
    const current=state.current.some(c=>c.id===t.id),phase=(Array.isArray(t.phase)?t.phase.join(' / '):str(t.phase)).replace(/\bPHASE([1-4])\b/g,'Phase $1'),r=t.readout||{};
    const registry=safeURL(t.registry_url)||(/^NCT\d{8}$/.test(t.nct_id||'')?'https://clinicaltrials.gov/study/'+t.nct_id:'');
    const cells=[['Drug',t.drug],['Indication',t.indication],['Phase',phase],['Trial Status',registryText(t.registry_status)],['Study Design',designText(t.design)],['Primary Endpoint',t.endpoint,'public-fact-endpoint'],[isReported(t)&&t.scope==='archived_research'?'Actual Readout':'Expected Readout',r.label,'public-fact-readout']];
    return `<section class="public-module public-trial-module" aria-label="Trial overview"><div class="public-trial-top"><div><h1>${esc(t.trial_name||'Trial title unavailable')}</h1><div class="public-trial-meta">${registry?link(registry,t.nct_id||'NCT registry'):'<span>NCT: '+esc(t.nct_id||'—')+'</span>'}<span>${esc(t.sponsor||'')}</span></div></div><span class="public-trial-label">${current?'Current formal record':t.scope==='archived_research'?'Historical replay':'Earlier record'}</span></div><dl class="public-facts">${cells.map(([label,value,cls])=>`<div class="public-fact ${cls||''}"><dt>${label}</dt><dd title="${esc(str(value))}">${esc((label==='Primary Endpoint'?str(value):short(value,160))||'—')}${cls==='public-fact-readout'?readoutSource(r):''}${cls==='public-fact-readout'&&r.source_granularity==='drug_program'?'<small>Program-level guidance; not a trial-specific promised date.</small>':''}</dd></div>`).join('')}</dl></section>`;
  }
  function safeDirection(run){return ['failed','timed_out','abstained','abstain','pending','withdrawn','unavailable'].includes(run.status)?'Uncertain':outcome(run.predicted_outcome);}
  function matchStatus(t,run){
    const r=t.result||{},adjudicated=['adjudicated','verified'].includes(t.status)||['adjudicated','verified'].includes(r.status),direction=safeDirection(run);
    const published=Date.parse(r.first_public_at||r.published_at),generated=Date.parse(run.generated_at_utc),locked=Date.parse(run.locked_at_utc);
    if(!adjudicated||!['success','failure'].includes(r.label)||run.target_id!==t.id||run.status!=='predicted'||direction==='Uncertain'||[t.scope,run.scope].some(s=>['archived_research','retrospective'].includes(s))||![published,generated,locked].every(Number.isFinite)||generated>locked||generated>=published||locked>=published)return '';
    const correct=direction.toLowerCase()===r.label;
    if(correct&&run.match===true&&run.match_status==='matched')return 'matched';
    if(!correct&&run.match===false&&run.match_status==='mismatched')return 'mismatched';
    return '';
  }
  function forecastTable(runs){
    const rows=[...runs].sort((a,b)=>safeModel(a).localeCompare(safeModel(b))),replay=state.detail?.target?.scope==='archived_research';
    return `<section class="public-module public-model-module" aria-label="AI model predictions"><div class="public-model-heading"><div><h2>AI Clinical Trial Predictions</h2><p class="public-model-subtitle">Compare predictions from multiple AI models for the same clinical trial.</p></div><p>${rows.length} assigned model${rows.length===1?'':'s'} · ${replay?'historical replay':'same evidence'}</p></div>${rows.length?`<table class="public-table"><thead><tr><th scope="col">Model / Version</th><th scope="col">Predicted Outcome</th><th scope="col">Success Probability</th><th scope="col">Confidence</th></tr></thead><tbody>${rows.map((r,i)=>{const direction=safeDirection(r),reasonId='public-reason-'+i,match=matchStatus(state.detail.target,r),version=str(r.model_version);return `<tr class="public-model-row" data-run-id="${esc(r.id)}"><td data-label="Model / Version"><div class="public-model-id">${esc(safeModel(r))}</div>${version&&version!==safeModel(r)?`<span class="public-model-version">Version: ${esc(version)}</span>`:''}<div class="public-model-meta"><time class="public-prediction-time"${str(r.generated_at_utc)?` datetime="${esc(r.generated_at_utc)}"`:''}>${r.status==='predicted'?'Prediction time:':'Recorded:'} ${utc(r.generated_at_utc)}</time><button class="public-reason-toggle" type="button" data-reason-id="${reasonId}" aria-controls="${reasonId}" aria-expanded="false">Short rationale</button></div></td><td data-label="Predicted Outcome"><span class="public-outcome public-outcome-${direction.toLowerCase()}">${direction}</span>${match?`<span class="public-match public-match-${match}">${match==='matched'?'✓ Matched':'✕ Mismatched'}</span>`:''}${displayStatus(r)?`<span class="public-run-status">${esc(displayStatus(r))}</span>`:''}</td><td class="public-probability" data-label="Success Probability"><span class="public-value-na">N/A</span><span class="public-empty-meter" role="img" aria-label="No calibrated success probability available"></span></td><td class="public-confidence" data-label="Confidence"><span class="public-value-na">N/A</span></td></tr><tr id="${reasonId}" class="public-reason-row" hidden><td colspan="4"><p>${esc(short(r.brief_reason||r.reason,650)||'No short rationale is available for this record.')}</p></td></tr>`;}).join('')}</tbody></table>`:'<p class="public-summary-note">No same-evidence model assignments are available for this exact record. No example models or estimates are substituted.</p>'}<p class="public-calibration-note"><strong>Probability and confidence are not established.</strong> Clinical calibration has not been demonstrated; direction labels are model judgments, not validated clinical probabilities.</p></section>`;
  }
  function consensusData(runs){
    const source=state.detail?.consensus||state.detail?.target?.consensus;
    if(!source||!Number.isInteger(source.assigned)||source.assigned!==runs.length||source.assigned<1)return null;
    const counts=source.counts||{},sum=['Success','Failure','Uncertain'].reduce((n,k)=>n+(Number.isInteger(counts[k])&&counts[k]>=0?counts[k]:NaN),0);
    if(sum!==source.assigned)return null;
    const expected=['Success','Failure','Uncertain'].find(k=>counts[k]>source.assigned/2)||'Uncertain';
    if(source.predicted_outcome!==expected||typeof source.agreement!=='number'||!Number.isFinite(source.agreement)||Math.abs(source.agreement-Math.max(...Object.values(counts))/source.assigned)>1e-8)return null;
    return source;
  }
  function summary(t,runs){
    const c=consensusData(runs),r=t.result||{},adjudicated=['adjudicated','verified'].includes(t.status)||['adjudicated','verified'].includes(r.status),reported=isReported(t);
    const actual=reported?(!adjudicated?'Awaiting adjudication':{success:'Success',failure:'Failure',mixed:'Mixed',inconclusive:'Inconclusive',not_adjudicable:'Inconclusive'}[r.label]||'Inconclusive'):'Awaiting results';
    const endpoint=adjudicated&&r.label==='success'?'Met':adjudicated&&r.label==='failure'?'Not Met':'Not Yet Determined';
    const binary=adjudicated&&['success','failure'].includes(r.label);
    const matched=runs.filter(x=>matchStatus(t,x)==='matched').length,mismatched=runs.filter(x=>matchStatus(t,x)==='mismatched').length;
    const comparison=matched+mismatched?`${matched} matched / ${mismatched} mismatched (${matched+mismatched} evaluable)`:'Not yet evaluable';
    const agreement=c?`${Number((c.agreement*100).toFixed(1))}% (${Math.max(c.counts.Success,c.counts.Failure,c.counts.Uncertain)} / ${c.assigned} assigned)`:'N/A';
    return `<section class="public-module public-summary-module" aria-label="Consensus and actual result"><div class="public-consensus"><h2>AI Consensus${c&&c.counts.Success>0&&c.counts.Failure>0?'<span class="public-divergence">Directions differ</span>':''}</h2>${c?`<svg class="public-agreement-bar" role="img" viewBox="0 0 100 5" preserveAspectRatio="none" aria-label="${c.counts.Success} Success, ${c.counts.Failure} Failure, ${c.counts.Uncertain} Uncertain out of ${c.assigned} assigned models">${['Success','Failure','Uncertain'].map((k,i,ks)=>c.counts[k]>0?`<rect class="public-agreement-${k.toLowerCase()}" x="${ks.slice(0,i).reduce((n,key)=>n+c.counts[key],0)/c.assigned*100}" y="0" width="${Math.max(0,c.counts[k]/c.assigned*100-0.3)}" height="5"><title>${k}: ${c.counts[k]} / ${c.assigned}</title></rect>`:'').join('')}</svg>`:''}<div class="public-summary-row"><span>Majority Prediction</span><span class="public-consensus-outcome">${c?outcome(c.predicted_outcome):'Uncertain'}</span></div><div class="public-summary-row"><span>Models Predicting Success</span><span class="public-success-count">${c?`${c.counts.Success} / ${c.assigned} assigned`:'N/A'}</span></div><div class="public-summary-row"><span>Model agreement</span><span class="public-agreement">${agreement}</span></div><div class="public-summary-row"><span>Average success probability</span><span>N/A</span></div><p class="public-summary-note">Strict majority of all assigned models. Missing responses count as Uncertain. Agreement is not confidence or statistical significance.</p></div><div class="public-actual" data-outcome-status="${esc(t.status||'pending')}"><h2>Actual clinical result</h2><div class="public-summary-row"><span>Actual outcome</span><span class="public-actual-outcome">${actual}</span></div><div class="public-summary-row"><span>Primary endpoint</span><span class="public-endpoint-met">${endpoint}</span></div>${!reported?`<div class="public-summary-row public-pending-readout"><span>Expected Readout</span><span>${esc(t.readout?.label||'—')}${readoutSource(t.readout)}</span></div>`:''}<div class="public-summary-row"><span>Official announcement</span><span>${reported?esc(day(r.first_public_at||r.published_at)):'—'}${reported&&r.source_url?' · '+link(r.source_url,'Source'):''}</span></div><div class="public-summary-row"><span>Prediction vs Reality</span><span class="public-reality-match">${comparison}</span></div><p class="public-summary-note">${!reported?'The target outcome has not been resolved. Unpublished is not failure.':!adjudicated?'Public information awaits adjudication against the exact registered target.':!binary?'Mixed or inconclusive outcomes do not produce a right-or-wrong score.':'Match labels require an exact adjudicated binary outcome and a prediction made before its public release.'}</p>${str(r.verification_note)?`<p class="public-summary-note public-result-verification">${esc(r.verification_note)}</p>`:''}</div></section>`;
  }
  function render(){
    const t=state.detail.target,runs=ownRuns();controls();$('public-body').innerHTML=banner(t)+facts(t)+forecastTable(runs)+summary(t,runs)+`<a class="public-record-link" href="/api/arena/public/targets/${encodeURIComponent(t.id)}/record" download="predome-public-clinical-record.json">Download public clinical record ↓</a>`;
    $('arena-status').textContent=`公开记录已读取 · ${state.current.some(c=>c.id===t.id)?'Current formal record':'Earlier record'} · ${t.nct_id||'NCT unavailable'}`;
  }
  async function api(path){const c=new AbortController(),timer=setTimeout(()=>c.abort(),25000);try{const r=await fetch(path,{signal:c.signal,cache:'no-store',credentials:'same-origin',headers:{Accept:'application/json'}});if(!r.ok)throw Error('Public clinical records could not be read');return await r.json();}finally{clearTimeout(timer);}}
  function empty(){controls();$('public-body').innerHTML=`<section class="public-empty"><h2>${state.period==='reported'?'No reported outcomes available':arr(state.data?.cohorts).some(c=>c.kind==='registered_12_project_season')?'Current season is being corrected':'No current formal season available'}</h2><p>${state.period==='reported'?'No published target outcome is available in these public records. Pending studies are not scored as failures.':'No active formal record is available. Withdrawn registrations are not model predictions.'}</p></section>`;$('arena-status').textContent='公开记录已读取 · No matching clinical records';}
  function failure(){state.detail=null;$('public-body').innerHTML='<section class="public-empty"><h2>Clinical record unavailable</h2><p>The public record could not be verified. No prediction values are substituted.</p><button id="public-retry" type="button">Try again</button></section>';$('arena-status').textContent='Public clinical record unavailable';}
  function writeURL(){const p=new URLSearchParams();if(state.targetId)p.set('target',state.targetId);if(state.period==='reported')p.set('period','reported');history.replaceState(null,'',location.pathname+(p.size?'?'+p.toString():''));}
  async function load(refresh=true){
    const seq=++state.request;state.detail=null;$('public-body').innerHTML='';$('public-body').setAttribute('aria-busy','true');$('arena-status').textContent='Loading public clinical records…';
    try{
      if(refresh||!state.data){const data=await api('/api/arena/public');if(seq!==state.request)return;if(!data||!['models','targets','runs','cohorts'].every(k=>Array.isArray(data[k]))||!data.targets.every(t=>t&&typeof t.id==='string'))throw Error('Invalid public schema');prepare(data);}
      if(state.targetId){const target=state.data.targets.find(t=>t.id===state.targetId);if(!target)throw Error('Original record not found');state.period=isReported(target)?'reported':'upcoming';}
      if(!state.targetId){const choices=availableTargets();if(!choices.length){empty();writeURL();return;}state.targetId=choices[0].id;}
      controls();const detail=await api('/api/arena/public/targets/'+encodeURIComponent(state.targetId));if(seq!==state.request)return;
      if(!detail||detail.target?.id!==state.targetId||!Array.isArray(detail.runs)||!detail.runs.every(r=>r&&typeof r.id==='string'&&r.probability===null&&r.confidence===null))throw Error('Invalid sanitized prediction schema');
      state.detail=detail;render();writeURL();
    }catch{if(seq===state.request)failure();}finally{if(seq===state.request)$('public-body').setAttribute('aria-busy','false');}
  }
  $('public-trial').addEventListener('change',()=>{state.targetId=$('public-trial').value;load(false);});
  document.addEventListener('click',e=>{const period=e.target.closest('[data-public-period]');if(period){state.period=period.dataset.publicPeriod;state.targetId='';load(false);}const toggle=e.target.closest('[data-reason-id]');if(toggle){const row=$(toggle.dataset.reasonId);row.hidden=!row.hidden;toggle.setAttribute('aria-expanded',String(!row.hidden));}if(e.target.id==='public-retry')load(true);});
  load();
})();
