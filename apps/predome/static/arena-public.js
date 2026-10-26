/* Sanitized public clinical projection only. Never fetch the internal arena API. */
(() => {
  'use strict';
  const $=id=>document.getElementById(id),arr=x=>Array.isArray(x)?x:[],str=x=>typeof x==='string'?x:'',esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const short=(x,n=180)=>str(x).length>n?str(x).slice(0,n)+'…':str(x),query=new URLSearchParams(location.search);
  const state={data:null,detail:null,targetId:query.get('target')||'',period:query.get('period')==='reported'?'reported':'upcoming',current:[],pilots:[],reported:[],request:0};
  const inactive=c=>['withdrawn_before_execution','withdrawn','superseded'].includes(c?.execution_status)||c?.status==='superseded';
  const isReported=t=>['announced','adjudicated','verified','result_announced','agreement_pending_independent_review'].includes(t?.status)||['announced','adjudicated','verified','agreement_pending_independent_review'].includes(t?.result?.status);
  const historical=t=>['archived_research','retrospective'].includes(t?.scope)||t?.record_kind==='historical_replay';
  const accepted=t=>!historical(t)&&(t?.result?.review_status==='accepted'||t?.result?.scoring_eligible===true)&&(['adjudicated','verified'].includes(t?.status)||['adjudicated','verified'].includes(t?.result?.status));
  const agreementPending=t=>!historical(t)&&!accepted(t)&&([t?.status,t?.result?.status].some(s=>['adjudicated','verified','agreement_pending_independent_review'].includes(s)));
  const day=x=>/^\d{4}-\d{2}-\d{2}/.test(str(x))?x.slice(0,10):'—';
  const utc=x=>{const d=new Date(x);return str(x)&&Number.isFinite(d.getTime())?d.toISOString().slice(0,19).replace('T',' ')+' UTC':'—';};
  const outcome=x=>['Success','Failure','Uncertain'].includes(x)?x:'Uncertain';
  function safeURL(x){if(typeof x!=='string'||!x)return '';try{const u=new URL(x,location.origin);return ['https:','http:'].includes(u.protocol)&&!u.username&&!u.password?u.href:'';}catch{return '';}}
  function link(url,label){const u=safeURL(url);return u?`<a href="${esc(u)}"${new URL(u).origin!==location.origin?' target="_blank" rel="noopener noreferrer"':''}>${esc(label)} ↗</a>`:'—';}
  function readoutSource(r){return safeURL(r?.source_url)?' · '+link(r.source_url,'Source'):'';}
  const urlFor=id=>'/arena?'+new URLSearchParams({target:id}).toString();
  function safeModel(run){return str(run.requested_model)||str(run.model_id)||'—';}
  function pilot(t){return arr(state.data?.cohorts).some(c=>c.id===t?.cohort_id&&(c.kind==='registered_patent_pair_pilot'||c.protocol?.season_kind==='registered_patent_pair_pilot'));}
  function recordLabel(t){return pilot(t)?'Independent research pilot':state.current.some(c=>c.id===t?.id)?'Current formal record':'Earlier record';}
  function ownRuns(){const t=state.detail?.target;return arr(state.detail?.runs).filter(r=>r.target_id===t?.id&&r.condition==='clinical_only'&&(r.track==='same_evidence'||(t?.scope==='archived_research'&&r.track==='archived'&&r.scope==='archived_research')));}
  function displayStatus(run){return {failed:'No usable response',timed_out:'Timed out',abstained:'Abstained',abstain:'Abstained',pending:run.execution_state==='running'?'Running':'Awaiting run',withdrawn:'Withdrawn before execution',unavailable:'Unavailable'}[run.status]||'';}
  function availableTargets(){return state.period==='reported'?state.reported:[...state.current,...state.pilots].filter(t=>!isReported(t));}
  function prepare(data){
    state.data=data;
    const cohorts=arr(data.cohorts).filter(c=>c&&(c.kind==='registered_12_project_season'||c.protocol?.season_kind==='registered_12_project_season')&&!inactive(c));
    const latest=cohorts.find(c=>c.id===data.current_cohort_id)||cohorts.sort((a,b)=>(Date.parse(b.registered_at)||0)-(Date.parse(a.registered_at)||0))[0];
    state.current=data.targets.filter(t=>latest&&t.cohort_id===latest.id&&!inactive(t)).sort((a,b)=>str(a.trial_name).localeCompare(str(b.trial_name)));
    const pilotCohorts=new Set(arr(data.cohorts).filter(c=>pilot({cohort_id:c.id})&&!inactive(c)).map(c=>c.id));
    state.pilots=data.targets.filter(t=>pilotCohorts.has(t.cohort_id)&&!inactive(t)).sort((a,b)=>str(a.trial_name).localeCompare(str(b.trial_name))||(Date.parse(b.registered_at)||0)-(Date.parse(a.registered_at)||0));
    // Retain distinct exact hypotheses. Revisions of the same clinical question use
    // the current formal record first, otherwise the most recently registered one.
    const ranked=data.targets.filter(t=>isReported(t)&&!inactive(t)).sort((a,b)=>Number(state.current.some(c=>c.id===b.id))-Number(state.current.some(c=>c.id===a.id))||(Date.parse(b.registered_at)||0)-(Date.parse(a.registered_at)||0));
    const groups=new Map();ranked.forEach(t=>{const key=JSON.stringify([t.nct_id,t.endpoint,t.population,t.comparison,t.success_definition,pilot(t)?t.cohort_id:null]);if(!groups.has(key))groups.set(key,t);});state.reported=[...groups.values()];
  }
  function currentReadout(t){
    const r=t.current_readout;if(r===undefined)return null;
    const fields='label start end precision source_url source_title source_granularity source_note published_at retrieved_at approved_at proposal_id proposal_sha256 decision_id decision_sha256 original_prediction_unchanged source_sha256 hash_scope timestamp_attestation'.split(' ');
    if(!r||typeof r!=='object'||Array.isArray(r)||Object.keys(r).length!==fields.length||!fields.every(k=>Object.hasOwn(r,k))||Object.values(r).some(v=>v!==null&&!['string','boolean'].includes(typeof v))||r.original_prediction_unchanged!==true||!['named_trial','exact_analysis'].includes(r.source_granularity)||!['source_response_bytes','captured_utf8_source_body'].includes(r.hash_scope)||r.timestamp_attestation!=='server_clock_not_independent_timestamp'||!['source_sha256','proposal_sha256','decision_sha256'].every(k=>/^[a-f0-9]{64}$/.test(r[k]))||!safeURL(r.source_url)||!r.proposal_id||!r.decision_id)throw Error('Invalid current calendar update');
    return r;
  }
  const displayReadout=t=>currentReadout(t)||t.readout||{};
  // Validate calendar dates without allowing Date.parse to normalize invalid days.
  function calendarDay(value){
    const match=/^(\d{4})-(\d{2})-(\d{2})$/.exec(str(value));if(!match)return null;
    const [year,month,date]=match.slice(1).map(Number);if(year<1000||month<1||month>12||date<1)return null;
    const days=new Date(Date.UTC(year,month,0)).getUTCDate();return date<=days?{year,month,date,key:value}:null;
  }
  function readoutWindow(t){
    const r=displayReadout(t),start=calendarDay(r.start),end=calendarDay(r.end),unknown={valid:false,label:'Readout unconfirmed',original:str(r.label),end:'',year:null};
    if(!start||!end||start.key>end.key)return unknown;
    if(r.precision==='range')return {valid:true,label:`${start.key} – ${end.key}`,original:str(r.label),end:end.key,year:start.year};
    if(start.year!==end.year)return unknown;
    const monthEnd=end.date===new Date(Date.UTC(end.year,end.month,0)).getUTCDate();let label='';
    if(r.precision==='day'&&start.key===end.key)label=start.key;
    else if(r.precision==='month'&&start.date===1&&start.month===end.month&&monthEnd)label=start.key.slice(0,7);
    else if(r.precision==='quarter'&&start.date===1&&[1,4,7,10].includes(start.month)&&end.month===start.month+2&&monthEnd)label=`${start.year} Q${(start.month+2)/3}`;
    else if(r.precision==='half_year'&&start.date===1&&[1,7].includes(start.month)&&end.month===start.month+5&&monthEnd)label=`H${start.month===1?1:2} ${start.year}`;
    else if(r.precision==='year'&&start.month===1&&start.date===1&&end.month===12&&end.date===31)label=String(start.year);
    return label?{valid:true,label,original:str(r.label),end:end.key,year:start.year}:unknown;
  }
  function targetRuns(t){
    return arr(state.data?.runs).filter(r=>r.target_id===t.id&&r.condition==='clinical_only'&&(r.track==='same_evidence'||(historical(t)&&r.track==='archived'&&r.scope==='archived_research')));
  }
  function storedLock(runs){
    const values=runs.map(r=>str(r.locked_at_utc)).filter(v=>/^\d{4}-\d{2}-\d{2}T/.test(v)&&calendarDay(v.slice(0,10))&&Number.isFinite(Date.parse(v))).map(v=>new Date(v).toISOString()).sort();
    if(!values.length)return 'No lock recorded';
    const first=values[0],last=values[values.length-1],shortDate=v=>v.slice(0,10),time=v=>v.slice(11,16);
    const range=first===last?`${shortDate(first)} ${time(first)}`:shortDate(first)===shortDate(last)?`${shortDate(first)} ${time(first)}–${time(last)}`:`${shortDate(first)}–${shortDate(last)}`;
    return `Recorded lock ${range} UTC${values.length<runs.length?' · incomplete':''}`;
  }
  function reportedTime(t){const value=str(t.result?.first_public_at);return calendarDay(value.slice(0,10))&&Number.isFinite(Date.parse(value))?Date.parse(value):0;}
  function calendarGroups(available){
    if(state.period==='reported')return [{key:'reported',label:'Published result records',targets:[...available].sort((a,b)=>reportedTime(b)-reportedTime(a)||str(a.trial_name).localeCompare(str(b.trial_name)))}];
    const groups=new Map();
    available.forEach(t=>{const window=readoutWindow(t),program=displayReadout(t).source_granularity==='drug_program',key=!window.valid?'unconfirmed':program?'program':String(window.year);if(!groups.has(key))groups.set(key,{key,label:key==='unconfirmed'?'Readout unconfirmed':key==='program'?'Program guidance':`${window.year} sponsor windows`,targets:[]});groups.get(key).targets.push(t);});
    return [...groups.values()].sort((a,b)=>{const rank=k=>k==='unconfirmed'?Infinity:k==='program'?9999:Number(k);return rank(a.key)-rank(b.key);}).map(group=>({...group,targets:group.targets.sort((a,b)=>readoutWindow(a).end.localeCompare(readoutWindow(b).end)||str(a.trial_name).localeCompare(str(b.trial_name))||str(a.id).localeCompare(str(b.id)))}));
  }
  function schedule(available){
    const strip=$('arena-fixtures');if(!strip)return;
    strip.hidden=!available.length;
    const nctCounts=new Map();available.forEach(t=>nctCounts.set(t.nct_id,(nctCounts.get(t.nct_id)||0)+1));
    const card=t=>{const current=currentReadout(t),guidance=displayReadout(t),window=readoutWindow(t),runs=targetRuns(t),counts={Success:0,Failure:0,Uncertain:0};runs.forEach(r=>counts[safeDirection(r)]++);const reported=isReported(t),date=calendarDay(day(t.result?.first_public_at)),label=reported?(date?date.key:'Announcement date unconfirmed'):window.label,stage=pilot(t)?'Independent pilot':reported?historical(t)?'Historical replay':agreementPending(t)?'Review pending':'Result recorded':guidance.source_granularity==='drug_program'?'Program guidance':current?'Reviewed calendar':'Recorded guidance',votes=runs.length?`Success ${counts.Success} · Failure ${counts.Failure} · Uncertain ${counts.Uncertain} / ${runs.length} assigned`:'No assigned model records';
      return `<article class="arena-calendar-card" data-calendar-target="${esc(t.id)}" data-window-kind="${esc(reported?'reported':!window.valid?'unconfirmed':guidance.source_granularity==='drug_program'?'program':str(guidance.precision))}"${window.valid?` data-window-end="${esc(window.end)}"`:''}><button type="button" class="arena-match-chip" data-arena-target="${esc(t.id)}" aria-label="${esc([t.trial_name,t.drug,t.nct_id,t.endpoint,label,votes,storedLock(runs)].filter(Boolean).join(' · '))}" aria-pressed="${t.id===state.targetId}"><span class="arena-calendar-window">${esc(label)}<small>${esc(stage)}</small></span><span class="arena-calendar-trial"><strong>${esc(t.trial_name||t.nct_id||'Unnamed trial')}${nctCounts.get(t.nct_id)>1?' · '+esc(short(t.endpoint,40)):''}</strong><span class="arena-calendar-drug" title="${esc(t.drug||'Drug unavailable')}">${esc(t.drug||'Drug unavailable')}</span></span><span class="arena-calendar-votes" aria-label="${esc(votes)}">${runs.length?['Success','Failure','Uncertain'].map(k=>`<span class="arena-vote-${k.toLowerCase()}" data-vote="${k}" data-count="${counts[k]}">${k[0]} ${counts[k]}</span>`).join('')+`<small> / ${runs.length}${counts.Success===0&&counts.Failure===0?' · No directional call':counts.Success&&counts.Failure?' · Directions differ':''}</small>`:'No assigned models'}</span><span class="arena-calendar-lock">${esc(storedLock(runs))}</span></button><div class="arena-calendar-source">${reported?link(t.result?.source_url,'Result source'):link(guidance.source_url,window.valid?'Guidance source':'Timing source')}<span title="${esc(reported?(historical(t)?'Historical replay; not a prospective hit':accepted(t)?'Independently accepted outcome':'Independent review pending'):window.original)}">${esc(reported?(historical(t)?'Historical replay; not a prospective hit':accepted(t)?'Independently accepted outcome':'Independent review pending'):short(window.original||'No guidance label recorded',90))}</span></div>${current&&!reported?`<p class="arena-calendar-original">Frozen: ${esc(short(t.readout?.label||'Timing unconfirmed',65))} · forecast unchanged</p>`:''}</article>`;
    };
    strip.innerHTML=`<div class="arena-calendar-caption"><strong>${state.period==='reported'?'RESULTS DESK':'READOUT CALENDAR'}</strong><span>${state.period==='reported'?'Official announcement dates · historical records stay separate':'Recorded guidance · overlapping windows are not exact readout order'}</span></div><div class="arena-schedule-track">${calendarGroups(available).map(group=>`<section class="arena-window-group" data-window-group="${esc(group.key)}"><h2>${esc(group.label)} <span>${group.targets.length}</span></h2><div class="arena-window-cards">${group.targets.map(card).join('')}</div></section>`).join('')}</div>`;
    centerSchedule();
  }
  function centerSchedule(){
    requestAnimationFrame(()=>{const track=$('arena-fixtures')?.querySelector('.arena-schedule-track'),active=track?.querySelector('[aria-pressed="true"]');if(track&&active)track.scrollLeft=Math.max(0,track.scrollLeft+active.getBoundingClientRect().left-track.getBoundingClientRect().left-track.clientWidth/2+active.clientWidth/2);});
  }
  function controls(){
    $('public-controls').hidden=false;let available=availableTargets();const explicit=arr(state.data?.targets).find(t=>t.id===state.targetId);
    if(explicit&&!available.some(t=>t.id===explicit.id))available=[explicit,...available];
    schedule(available.filter(t=>state.period==='reported'?state.reported.some(r=>r.id===t.id):[...state.current,...state.pilots].some(r=>r.id===t.id)));
    const nctCounts=new Map();available.forEach(t=>nctCounts.set(t.nct_id,(nctCounts.get(t.nct_id)||0)+1));
    $('public-trial').innerHTML=available.map(t=>`<option value="${esc(t.id)}">${esc(t.trial_name||t.nct_id||'Unnamed trial')}${nctCounts.get(t.nct_id)>1?' · '+esc(short(t.endpoint,65)):''}${!state.current.some(c=>c.id===t.id)?' · '+recordLabel(t):''}</option>`).join('');$('public-trial').value=state.targetId;
    document.querySelectorAll('[data-public-period]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.publicPeriod===state.period)));
    $('public-count').textContent=state.period==='reported'?`${state.reported.length} reported study questions`:`${state.current.length} current formal trials${state.pilots.length?' · '+state.pilots.length+' independent research pilot'+(state.pilots.length===1?'':'s'):''}`;
  }
  function banner(t){
    const current=state.current.some(c=>c.id===t.id),latest=state.current.find(c=>c.nct_id&&c.nct_id===t.nct_id&&c.id!==t.id);
    if(pilot(t))return '<aside class="public-record-banner" data-research-pilot="true"><strong>Independent research pilot · Separate from the formal season</strong><span>Clinical-only controls; patent-added research remains separate. Original times preserved. Uncalibrated judgments; no ranking before an independently accepted outcome.</span></aside>';
    const replay=t.scope==='archived_research'||t.scope==='retrospective';if(current)return '';
    const label=inactive(t)?'Original record · Withdrawn before execution':replay?'Historical replay · Not a prospective prediction':'Earlier record · Original target preserved';
    return `<aside class="public-record-banner" data-original-record="true"><strong>${label}</strong><span>${replay?'This analysis may postdate the public result and is not evidence of a prospective hit.':inactive(t)?'Registration did not result in model execution.':'This is the exact record in the selected link; it has not been replaced by a later prediction.'}</span>${latest?`<br><a class="public-latest-link" href="${esc(urlFor(latest.id))}">View the latest formal record for this trial →</a>`:''}</aside>`;
  }
  const registryLabels={RANDOMIZED:'Randomized',NON_RANDOMIZED:'Non-randomized',PARALLEL:'Parallel group',CROSSOVER:'Crossover',SINGLE_GROUP:'Single group',FACTORIAL:'Factorial',SEQUENTIAL:'Sequential',NONE:'Open label',SINGLE:'Single masked',DOUBLE:'Double masked',TRIPLE:'Triple masked',QUADRUPLE:'Quadruple masked',TREATMENT:'Treatment',PREVENTION:'Prevention',ACTIVE_NOT_RECRUITING:'Active, not recruiting',RECRUITING:'Recruiting',NOT_YET_RECRUITING:'Not yet recruiting',COMPLETED:'Completed',TERMINATED:'Terminated',SUSPENDED:'Suspended',WITHDRAWN:'Withdrawn',ENROLLING_BY_INVITATION:'Enrolling by invitation',UNKNOWN:'Unknown'};
  const registryText=value=>registryLabels[value]||str(value);
  function designText(d){if(typeof d==='string')return d||'—';if(!d||typeof d!=='object')return '—';return [d.allocation,d.intervention_model,d.masking,d.primary_purpose].map(v=>registryText(typeof v==='string'?v:typeof v?.masking==='string'?v.masking:'')).filter(Boolean).join(' · ')||'—';}
  function facts(t){
    const current=state.current.some(c=>c.id===t.id),phase=(Array.isArray(t.phase)?t.phase.join(' / '):str(t.phase)).replace(/\bPHASE([1-4])\b/g,'Phase $1'),r=displayReadout(t),update=currentReadout(t);
    const registry=safeURL(t.registry_url)||(/^NCT\d{8}$/.test(t.nct_id||'')?'https://clinicaltrials.gov/study/'+t.nct_id:'');
    const cells=[['Drug',t.drug],['Indication',t.indication],['Phase',phase],['Trial Status',registryText(t.registry_status)],['Study Design',designText(t.design)],['Primary Endpoint',t.endpoint,'public-fact-endpoint'],[isReported(t)&&t.scope==='archived_research'?'Actual Readout':update?'Current readout guidance':'Expected Readout',r.label,'public-fact-readout']];
    return `<section class="public-module public-trial-module" aria-label="Trial overview"><div class="public-trial-top"><div><p class="arena-match-kicker">${current?'CURRENT MATCH · FORMAL SEASON':pilot(t)?'INDEPENDENT RESEARCH PILOT':t.scope==='archived_research'?'HISTORICAL REPLAY':'PRESERVED MATCH RECORD'}</p><h1>${esc(t.trial_name||'Trial title unavailable')}</h1><div class="public-trial-meta">${registry?link(registry,t.nct_id||'NCT registry'):'<span>NCT: '+esc(t.nct_id||'—')+'</span>'}<span>${esc(t.sponsor||'')}</span></div></div><span class="public-trial-label">${pilot(t)?'Independent research pilot':current?'Current formal record':t.scope==='archived_research'?'Historical replay':'Earlier record'}</span></div><dl class="public-facts">${cells.map(([label,value,cls])=>`<div class="public-fact ${cls||''}"><dt>${label}</dt><dd title="${esc(str(value))}">${esc((label==='Primary Endpoint'?str(value):short(value,160))||'—')}${cls==='public-fact-readout'?readoutSource(r):''}${cls==='public-fact-readout'&&r.source_granularity==='drug_program'?'<small>Program-level guidance; not a trial-specific promised date.</small>':''}</dd></div>`).join('')}</dl>${update?`<p class="public-readout-update"><strong>Calendar update only.</strong> Frozen prediction window: <span class="public-frozen-window">${esc(t.readout?.label||'Timing unconfirmed')}</span>${readoutSource(t.readout)}. Model inputs, information cutoff and original prediction times are unchanged; this update was not model input.<br>Source published: ${esc(update.published_at)} · Retrieved: ${utc(update.retrieved_at)} · Approved: ${utc(update.approved_at)}. Server clock; no independent timestamp proof.</p>`:''}</section>`;
  }
  function safeDirection(run){return ['failed','timed_out','abstained','abstain','pending','withdrawn','unavailable'].includes(run.status)?'Uncertain':outcome(run.predicted_outcome);}
  function matchStatus(t,run){
    const r=t.result||{},adjudicated=accepted(t)&&r.scoring_eligible===true,direction=safeDirection(run);
    const published=Date.parse(r.first_public_at||r.published_at),generated=Date.parse(run.generated_at_utc),locked=Date.parse(run.locked_at_utc);
    if(!adjudicated||!['success','failure'].includes(r.label)||run.target_id!==t.id||run.status!=='predicted'||direction==='Uncertain'||[t.scope,run.scope].some(s=>['archived_research','retrospective'].includes(s))||![published,generated,locked].every(Number.isFinite)||generated>locked||generated>=published||locked>=published)return '';
    const correct=direction.toLowerCase()===r.label;
    if(correct&&run.match===true&&run.match_status==='matched')return 'matched';
    if(!correct&&run.match===false&&run.match_status==='mismatched')return 'mismatched';
    return '';
  }
  function modelIdentity(run){
    const name=safeModel(run),key=/^kimi/i.test(name)?'kimi':/^glm/i.test(name)?'glm':/^deepseek.*pro/i.test(name)?'deepseek-pro':/^deepseek/i.test(name)?'deepseek':'other';
    return {key,initial:key==='kimi'?'K':key==='glm'?'G':key==='deepseek-pro'?'D+':key==='deepseek'?'D':'AI'};
  }
  function faceoff(runs){
    const groups=['Success','Failure','Uncertain'].map(direction=>({direction,runs:runs.filter(r=>safeDirection(r)===direction)}));
    const split=groups[0].runs.length>0&&groups[1].runs.length>0;
    const decided=groups[0].runs.length+groups[1].runs.length;
    const label=!runs.length?'No recorded entries':split?'Models disagree':decided===0?'No directional call':decided===runs.length?'Directional agreement':'Partial directional calls';
    return `<div class="arena-faceoff" data-divergence="${split?'split':decided?'one-sided':'undetermined'}"><div class="arena-faceoff-title"><strong>${label}</strong><span>Recorded judgments · ${runs.length} assigned · no ranking</span></div><div class="arena-positions">${groups.map(g=>`<div class="arena-position arena-position-${g.direction.toLowerCase()}" data-direction="${g.direction}" data-count="${g.runs.length}"><span>${g.direction}</span><strong>${g.runs.length}<small> / ${runs.length}</small></strong><div>${g.runs.map(r=>`<span class="arena-position-model">${esc(safeModel(r))}</span>`).join('')||'<span class="arena-position-empty">No model</span>'}</div></div>`).join('')}</div></div>`;
  }
  function reveal(t){
    const reported=isReported(t),adjudicated=accepted(t),replay=historical(t),waiting=agreementPending(t);
    const stage=!reported?'pending':replay?'historical':adjudicated?'resolved':'review';
    const label=!reported?'Awaiting the reveal':replay?'Historical result record':adjudicated?'Independently reviewed result':waiting?'Source agreement recorded · Independent review pending':'Announcement under review';
    const note=replay?'Historical replay · excluded from prospective standings':!reported?'Forecasts recorded. Rankings await verified outcomes.':adjudicated?'Exact endpoint result · independently accepted for comparison':waiting?'Independent review pending · excluded from scoring':'No scores until the exact endpoint is independently reviewed.';
    return `<div class="arena-reveal arena-reveal-${stage}" data-reveal-stage="${stage}"><span class="arena-reveal-symbol" aria-hidden="true">${!reported?'◷':adjudicated?'✓':'◌'}</span><div><strong>${label}</strong><span>${note}</span></div></div>`;
  }
  function recordedReferences(run){
    const sources=arr(run.sources).filter(source=>source&&typeof source==='object'&&!Array.isArray(source));
    return `<div class="public-model-references"><h3>Recorded references</h3><p class="public-reference-note">References recorded with this model response; individual claims have not been independently verified.</p>${sources.length?`<ul>${sources.map(source=>{const links=[['Original source',source.source_url],['Captured snapshot',source.source_snapshot_url]].filter(([,url])=>safeURL(url));return `<li><span class="public-reference-title">${esc(str(source.title)||str(source.id)||'Untitled reference')}</span><span class="public-reference-links">${links.length?links.map(([label,url])=>link(url,label)).join(' · '):'No public link recorded.'}</span></li>`;}).join('')}</ul>`:'<p class="public-reference-empty">No references recorded for this response.</p>'}</div>`;
  }
  function forecastTable(runs){
    const rows=[...runs].sort((a,b)=>safeModel(a).localeCompare(safeModel(b))),replay=state.detail?.target?.scope==='archived_research';
    return `<section class="public-module public-model-module" aria-label="AI model predictions"><div class="public-model-heading"><div><span class="arena-section-kicker">AI CONTENDERS · SAME CLINICAL QUESTION</span><h2>AI Clinical Trial Predictions</h2><p class="public-model-subtitle">Compare predictions from multiple AI models for the same clinical trial.</p></div><p><span class="arena-evidence-tag">${rows.length} assigned model${rows.length===1?'':'s'} · ${replay?'historical replay':'same evidence'}</span></p></div>${faceoff(rows)}${rows.length?`<table class="public-table"><thead><tr><th scope="col">Model / Version</th><th scope="col">Predicted Outcome</th><th scope="col">Success Probability</th><th scope="col">Confidence</th></tr></thead><tbody>${rows.map((r,i)=>{const direction=safeDirection(r),reasonId='public-reason-'+i,match=matchStatus(state.detail.target,r),version=str(r.model_version),identity=modelIdentity(r);return `<tr class="public-model-row arena-contender arena-contender-${identity.key}" data-run-id="${esc(r.id)}"><td data-label="Model / Version"><div class="arena-model-heading"><span class="arena-model-avatar" aria-hidden="true">${identity.initial}</span><div class="public-model-id">${esc(safeModel(r))}</div></div>${version&&version!==safeModel(r)?`<span class="public-model-version">Version: ${esc(version)}</span>`:''}<div class="public-model-meta"><time class="public-prediction-time"${str(r.generated_at_utc)?` datetime="${esc(r.generated_at_utc)}"`:''}>${r.status==='predicted'?'Prediction time:':'Recorded:'} ${utc(r.generated_at_utc)}</time><button class="public-reason-toggle" type="button" data-reason-id="${reasonId}" aria-controls="${reasonId}" aria-expanded="false">Rationale &amp; sources</button></div></td><td data-label="Predicted Outcome"><span class="public-outcome public-outcome-${direction.toLowerCase()}">${direction}</span>${match?`<span class="public-match public-match-${match}">${match==='matched'?'✓ Matched':'✕ Mismatched'}</span>`:''}${displayStatus(r)?`<span class="public-run-status">${esc(displayStatus(r))}</span>`:''}</td><td class="public-probability" data-label="Success Probability"><span class="public-value-na">N/A</span><span class="public-empty-meter" role="img" aria-label="No calibrated success probability available"></span></td><td class="public-confidence" data-label="Confidence"><span class="public-value-na">N/A</span></td></tr><tr id="${reasonId}" class="public-reason-row" hidden><td colspan="4"><p>${esc(short(r.brief_reason||r.reason,650)||'No short rationale is available for this record.')}</p>${recordedReferences(r)}</td></tr>`;}).join('')}</tbody></table>`:'<p class="public-summary-note">No same-evidence model assignments are available for this exact record. No example models or estimates are substituted.</p>'}<p class="public-calibration-note"><strong>Probability and confidence are not established.</strong> Clinical calibration has not been demonstrated; direction labels are model judgments, not validated clinical probabilities.</p></section>`;
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
    const c=consensusData(runs),r=t.result||{},adjudicated=accepted(t),reported=isReported(t),replay=historical(t),waiting=agreementPending(t),showLabel=adjudicated||replay&&['adjudicated','verified'].includes(t.status);
    const actual=reported?(waiting?'Awaiting independent review':!showLabel?'Awaiting adjudication':{success:'Success',failure:'Failure',mixed:'Mixed',inconclusive:'Inconclusive',not_adjudicable:'Inconclusive'}[r.label]||'Inconclusive'):'Awaiting results';
    const endpoint=showLabel&&r.label==='success'?'Met':showLabel&&r.label==='failure'?'Not Met':'Not Yet Determined';
    const binary=adjudicated&&['success','failure'].includes(r.label);
    const matched=runs.filter(x=>matchStatus(t,x)==='matched').length,mismatched=runs.filter(x=>matchStatus(t,x)==='mismatched').length;
    const comparison=matched+mismatched?`${matched} matched / ${mismatched} mismatched (${matched+mismatched} evaluable)`:'Not yet evaluable';
    const agreement=c?`${Number((c.agreement*100).toFixed(1))}% (${Math.max(c.counts.Success,c.counts.Failure,c.counts.Uncertain)} / ${c.assigned} assigned)`:'N/A';
    return `<section class="public-module public-summary-module" aria-label="Consensus and actual result"><div class="public-consensus"><h2><span class="arena-panel-index" aria-hidden="true">01</span> AI Consensus${c&&c.counts.Success>0&&c.counts.Failure>0?'<span class="public-divergence">Directions differ</span>':''}</h2>${c?`<svg class="public-agreement-bar" role="img" viewBox="0 0 100 5" preserveAspectRatio="none" aria-label="${c.counts.Success} Success, ${c.counts.Failure} Failure, ${c.counts.Uncertain} Uncertain out of ${c.assigned} assigned models">${['Success','Failure','Uncertain'].map((k,i,ks)=>c.counts[k]>0?`<rect class="public-agreement-${k.toLowerCase()}" x="${ks.slice(0,i).reduce((n,key)=>n+c.counts[key],0)/c.assigned*100}" y="0" width="${Math.max(0,c.counts[k]/c.assigned*100-0.3)}" height="5"><title>${k}: ${c.counts[k]} / ${c.assigned}</title></rect>`:'').join('')}</svg>`:''}<div class="public-summary-row"><span>Majority Prediction</span><span class="public-consensus-outcome">${c?outcome(c.predicted_outcome):'Uncertain'}</span></div><div class="public-summary-row"><span>Models Predicting Success</span><span class="public-success-count">${c?`${c.counts.Success} / ${c.assigned} assigned`:'N/A'}</span></div><div class="public-summary-row"><span>Model agreement</span><span class="public-agreement">${agreement}</span></div><div class="public-summary-row"><span>Average success probability</span><span>N/A</span></div><p class="public-summary-note">Strict majority of all assigned models. Missing responses count as Uncertain. Agreement is not confidence or statistical significance.</p></div><div class="public-actual" data-outcome-status="${esc(t.status||'pending')}"><h2><span class="arena-panel-index" aria-hidden="true">02</span> Actual clinical result</h2>${reveal(t)}<div class="public-summary-row"><span>Actual outcome</span><span class="public-actual-outcome">${actual}</span></div><div class="public-summary-row"><span>Primary endpoint</span><span class="public-endpoint-met">${endpoint}</span></div>${!reported?`<div class="public-summary-row public-pending-readout"><span>Expected Readout</span><span>${esc(displayReadout(t).label||'—')}${readoutSource(displayReadout(t))}</span></div>`:''}<div class="public-summary-row"><span>Official announcement</span><span>${reported?esc(day(r.first_public_at||r.published_at)):'—'}${reported&&r.source_url?' · '+link(r.source_url,'Source'):''}</span></div><div class="public-summary-row"><span>Prediction vs Reality</span><span class="public-reality-match">${comparison}</span></div><p class="public-summary-note">${!reported?'The target outcome has not been resolved. Unpublished is not failure.':replay?'Historical reported result; this replay does not count as a prospective hit.':waiting?'Source checks agree; independent scientific review is pending. No match or score is assigned.':!adjudicated?'Public information awaits independent review against the exact registered target.':!binary?'Mixed or inconclusive outcomes do not produce a right-or-wrong score.':'Match labels require an exact adjudicated binary outcome and a prediction made before its public release.'}</p>${str(r.verification_note)?`<p class="public-summary-note public-result-verification">${esc(r.verification_note)}</p>`:''}</div></section>`;
  }
  function render(){
    const t=state.detail.target,runs=ownRuns();controls();$('public-body').innerHTML=banner(t)+facts(t)+forecastTable(runs)+summary(t,runs)+`<a class="public-record-link" href="/api/arena/public/targets/${encodeURIComponent(t.id)}/brief/download" download="predome-clinical-brief.zip">Download clinical brief ↓</a>`;
    $('arena-status').textContent=`公开记录已读取 · ${recordLabel(t)} · ${t.nct_id||'NCT unavailable'}`;
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
  document.addEventListener('click',e=>{const match=e.target.closest('[data-arena-target]');if(match){state.targetId=match.dataset.arenaTarget;load(false);}const period=e.target.closest('[data-public-period]');if(period){state.period=period.dataset.publicPeriod;state.targetId='';load(false);}const toggle=e.target.closest('[data-reason-id]');if(toggle){const row=$(toggle.dataset.reasonId);row.hidden=!row.hidden;toggle.setAttribute('aria-expanded',String(!row.hidden));}if(e.target.id==='public-retry')load(true);});
  window.addEventListener('resize',centerSchedule,{passive:true});
  load();
})();
