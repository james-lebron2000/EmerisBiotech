/* Sanitized public clinical projection only. Never fetch the internal arena API. */
(() => {
  'use strict';
  const $=id=>document.getElementById(id),arr=x=>Array.isArray(x)?x:[],str=x=>typeof x==='string'?x:'',esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const short=(x,n=180)=>str(x).length>n?str(x).slice(0,n)+'…':str(x),query=new URLSearchParams(location.search);
  const state={data:null,detail:null,targetId:query.get('target')||'',period:query.get('period')==='reported'?'reported':'upcoming',current:[],pilots:[],research:[],reported:[],request:0,comparisons:null};
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
  function recordLabel(t){return pilot(t)?'Research pilot record':state.current.some(c=>c.id===t?.id)?'Current formal record':state.research.some(c=>c.id===t?.id)?'Earlier research record':'Earlier record';}
  function ownRuns(){const t=state.detail?.target;return arr(state.detail?.runs).filter(r=>r.target_id===t?.id&&r.condition==='clinical_only'&&(r.track==='same_evidence'||(t?.scope==='archived_research'&&r.track==='archived'&&r.scope==='archived_research')));}
  function displayStatus(run){return {failed:'No usable response',timed_out:'Timed out',abstained:'Abstained',abstain:'Abstained',pending:run.execution_state==='running'?'Running':'Awaiting run',withdrawn:'Withdrawn before execution',unavailable:'Unavailable'}[run.status]||'';}
  function availableTargets(){return state.period==='reported'?state.reported:[...state.current,...state.pilots,...state.research].filter(t=>!isReported(t));}
  function prepare(data){
    state.data=data;
    const cohorts=arr(data.cohorts).filter(c=>c&&(c.kind==='registered_12_project_season'||c.protocol?.season_kind==='registered_12_project_season')&&!inactive(c));
    const latest=cohorts.find(c=>c.id===data.current_cohort_id)||cohorts.sort((a,b)=>(Date.parse(b.registered_at)||0)-(Date.parse(a.registered_at)||0))[0];
    state.current=data.targets.filter(t=>latest&&t.cohort_id===latest.id&&!inactive(t)).sort((a,b)=>str(a.trial_name).localeCompare(str(b.trial_name)));
    const pilotCohorts=new Set(arr(data.cohorts).filter(c=>pilot({cohort_id:c.id})&&!inactive(c)).map(c=>c.id));
    state.pilots=data.targets.filter(t=>pilotCohorts.has(t.cohort_id)&&!inactive(t)).sort((a,b)=>str(a.trial_name).localeCompare(str(b.trial_name))||(Date.parse(b.registered_at)||0)-(Date.parse(a.registered_at)||0));
    const activeCohorts=new Set(arr(data.cohorts).filter(c=>!inactive(c)).map(c=>c.id)),visible=new Set([...state.current,...state.pilots].map(t=>t.id));
    state.research=data.targets.filter(t=>!visible.has(t.id)&&t.scope==='prospective_challenge'&&!historical(t)&&!inactive(t)&&activeCohorts.has(t.cohort_id)).sort((a,b)=>str(a.trial_name).localeCompare(str(b.trial_name))||str(b.registered_at).localeCompare(str(a.registered_at))||str(a.id).localeCompare(str(b.id)));
    // Native records retain exact target/cohort versions, including workflow
    // records whose registered kind is unknown. Never merge them by NCT.
    const ranked=data.targets.filter(t=>isReported(t)&&!inactive(t)).sort((a,b)=>Number(state.current.some(c=>c.id===b.id))-Number(state.current.some(c=>c.id===a.id))||(Date.parse(b.registered_at)||0)-(Date.parse(a.registered_at)||0));
    const groups=new Map();ranked.forEach(t=>{const key=JSON.stringify([t.nct_id,t.endpoint,t.population,t.comparison,t.success_definition,historical(t)?null:t.cohort_id,historical(t)?null:t.id]);if(!groups.has(key))groups.set(key,t);});state.reported=[...groups.values()];
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
    const card=t=>{const current=currentReadout(t),guidance=displayReadout(t),window=readoutWindow(t),runs=targetRuns(t),counts={Success:0,Failure:0,Uncertain:0};runs.forEach(r=>counts[safeDirection(r)]++);const reported=isReported(t),date=calendarDay(day(t.result?.first_public_at)),label=reported?(date?date.key:'Announcement date unconfirmed'):window.label,stage=pilot(t)?'Research pilot record':reported?historical(t)?'Historical replay':agreementPending(t)?'Review pending':'Result recorded':state.research.some(r=>r.id===t.id)?'Earlier research record':guidance.source_granularity==='drug_program'?'Program guidance':current?'Reviewed calendar':'Recorded guidance',votes=runs.length?`Success ${counts.Success} · Failure ${counts.Failure} · Uncertain ${counts.Uncertain} / ${runs.length} assigned`:'No assigned model records';
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
    schedule(available.filter(t=>state.period==='reported'?state.reported.some(r=>r.id===t.id):[...state.current,...state.pilots,...state.research].some(r=>r.id===t.id)));
    const nctCounts=new Map();available.forEach(t=>nctCounts.set(t.nct_id,(nctCounts.get(t.nct_id)||0)+1));
    $('public-trial').innerHTML=available.map(t=>{
      const repeated=available.some(other=>other.id!==t.id&&other.nct_id===t.nct_id&&other.endpoint===t.endpoint),recorded=utc(t.registered_at);
      return `<option value="${esc(t.id)}">${esc(t.trial_name||t.nct_id||'Unnamed trial')}${repeated&&recorded!=='—'?' · Recorded '+esc(recorded.slice(0,16))+' UTC':''}${nctCounts.get(t.nct_id)>1?' · '+esc(short(t.endpoint,65)):''}${!state.current.some(c=>c.id===t.id)?' · '+recordLabel(t):''}</option>`;
    }).join('');$('public-trial').value=state.targetId;
    document.querySelectorAll('[data-public-period]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.publicPeriod===state.period)));
    $('public-count').textContent=state.period==='reported'?`${state.reported.length} reported study questions`:`${state.current.length} current formal trials${state.pilots.length?' · '+state.pilots.length+' research pilot record'+(state.pilots.length===1?'':'s'):''}${state.research.length?' · '+state.research.length+' earlier research record'+(state.research.length===1?'':'s'):''}`;
  }
  function banner(t){
    const current=state.current.some(c=>c.id===t.id),latest=state.current.find(c=>c.nct_id&&c.nct_id===t.nct_id&&c.id!==t.id);
    if(pilot(t))return '<aside class="public-record-banner" data-research-pilot="true"><strong>Research pilot record · Separate from the formal season</strong><span>Clinical-only controls; patent-added research remains separate. Original times preserved. Uncalibrated judgments; no ranking before an independently accepted outcome.</span></aside>';
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
    return `<section class="public-module public-trial-module" aria-label="Trial overview"><div class="public-trial-top"><div><p class="arena-match-kicker">${current?'CURRENT MATCH · FORMAL SEASON':pilot(t)?'RESEARCH PILOT RECORD':t.scope==='archived_research'?'HISTORICAL REPLAY':'PRESERVED MATCH RECORD'}</p><h1>${esc(t.trial_name||'Trial title unavailable')}</h1><div class="public-trial-meta">${registry?link(registry,t.nct_id||'NCT registry'):'<span>NCT: '+esc(t.nct_id||'—')+'</span>'}<span>${esc(t.sponsor||'')}</span></div></div><span class="public-trial-label">${pilot(t)?'Research pilot record':current?'Current formal record':t.scope==='archived_research'?'Historical replay':'Earlier record'}</span></div><dl class="public-facts">${cells.map(([label,value,cls])=>`<div class="public-fact ${cls||''}"><dt>${label}</dt><dd title="${esc(str(value))}">${esc((label==='Primary Endpoint'?str(value):short(value,160))||'—')}${cls==='public-fact-readout'?readoutSource(r):''}${cls==='public-fact-readout'&&r.source_granularity==='drug_program'?'<small>Program-level guidance; not a trial-specific promised date.</small>':''}</dd></div>`).join('')}</dl>${update?`<p class="public-readout-update"><strong>Calendar update only.</strong> Frozen prediction window: <span class="public-frozen-window">${esc(t.readout?.label||'Timing unconfirmed')}</span>${readoutSource(t.readout)}. Model inputs, information cutoff and original prediction times are unchanged; this update was not model input.<br>Source published: ${esc(update.published_at)} · Retrieved: ${utc(update.retrieved_at)} · Approved: ${utc(update.approved_at)}. Server clock; no independent timestamp proof.</p>`:''}</section>`;
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
    return `<div class="arena-faceoff" data-divergence="${split?'split':decided?'one-sided':'undetermined'}"><div class="arena-faceoff-title"><strong>${label}</strong><span>Assigned records · ${runs.length} slots · no ranking</span></div><div class="arena-positions">${groups.map(g=>`<div class="arena-position arena-position-${g.direction.toLowerCase()}" data-direction="${g.direction}" data-count="${g.runs.length}"><span>${g.direction}</span><strong>${g.runs.length}<small> / ${runs.length}</small></strong><div>${g.runs.map(r=>`<span class="arena-position-model">${esc(safeModel(r))}</span>`).join('')||'<span class="arena-position-empty">No model</span>'}</div></div>`).join('')}</div></div>`;
  }
  function reveal(t){
    const reported=isReported(t),adjudicated=accepted(t),replay=historical(t),waiting=agreementPending(t);
    const stage=!reported?'pending':replay?'historical':adjudicated?'resolved':'review';
    const label=!reported?'Awaiting the reveal':replay?'Historical result record':adjudicated?'Independently reviewed result':waiting?'Source agreement recorded · Independent review pending':'Announcement under review';
    const note=replay?'Historical replay · excluded from prospective standings':!reported?'Forecasts recorded. Rankings await verified outcomes.':adjudicated?'Exact endpoint result · independently accepted for comparison':waiting?'Independent review pending · excluded from scoring':'No scores until the exact endpoint is independently reviewed.';
    return `<div class="arena-reveal arena-reveal-${stage}" data-reveal-stage="${stage}"><span class="arena-reveal-symbol" aria-hidden="true">${!reported?'◷':adjudicated?'✓':'◌'}</span><div><strong>${label}</strong><span>${note}</span></div></div>`;
  }
  function referenceText(value,limit){
    return typeof value==='string'&&value.trim()&&[...value].length<=limit&&!/[\p{Cc}\p{Cf}\p{Cs}\u2028\u2029]/u.test(value)&&!/(?<![A-Za-z])sk-[A-Za-z0-9_-]{24,}|-----BEGIN .*PRIVATE KEY-----/.test(value)?value:'';
  }
  function referenceMetadata(source){
    const pages=source.page_references,validPages=Array.isArray(pages)&&pages.length<=100&&pages.every(p=>Number.isInteger(p)&&p>=1&&p<=10000)&&new Set(pages).size===pages.length;
    const recordedPages=validPages&&pages.length?pages.join(', '):'',page=Number.isInteger(source.page)&&source.page>0?source.page:null;
    const section=referenceText(source.source_anchor,1000),version=referenceText(source.document_version,200),date=referenceText(source.publication_date,80),items=[];
    if(recordedPages)items.push(['pages','Recorded pages: '+recordedPages]);
    if(page!==null)items.push(['page','Recorded page: '+page]);
    if(section)items.push(['section','Recorded section: '+section]);
    if(!recordedPages&&page===null&&!section)items.push(['location-empty','Location not recorded']);
    items.push([version?'version':'version-empty',version?'Document version: '+version:'Version not recorded']);
    if(date)items.push(['date','Recorded date: '+date]);
    return `<span class="public-reference-metadata">${items.map(([key,value])=>`<span data-reference-meta="${key}">${esc(value)}</span>`).join('')}</span>`;
  }
  function recordedReferences(run){
    const sources=arr(run.sources).filter(source=>source&&typeof source==='object'&&!Array.isArray(source));
    return `<div class="public-model-references"><h3>Recorded references</h3><p class="public-reference-note">References and locations recorded with this response; individual claims have not been independently verified. Location metadata is not proof that the model read the original.</p>${sources.length?`<ul>${sources.map(source=>{const links=[['Original source',source.source_url],['Captured snapshot',source.source_snapshot_url]].filter(([,url])=>safeURL(url));return `<li><span class="public-reference-title">${esc(referenceText(source.title,300)||referenceText(source.id,200)||'Untitled reference')}</span>${referenceMetadata(source)}<span class="public-reference-links">${links.length?links.map(([label,url])=>link(url,label)).join(' · '):'No public link recorded.'}</span></li>`;}).join('')}</ul>`:'<p class="public-reference-empty">No references recorded for this response.</p>'}</div>`;
  }
  const comparisonReasons={missing_control:'No unique clinical baseline',ambiguous_control:'No unique clinical baseline',registration_metadata_unverified:'Frozen conditions unverified',clinical_packet_changed:'Clinical input differs',no_added_patents:'No patent addition recorded',model_version_unconfirmed:'Model version unconfirmed',model_version_changed:'Model version differs',actual_prompt_unverified:'Execution input unverified',actual_config_unverified:'Execution configuration unverified',actual_provider_input_unverified:'Execution input unverified',blocked_input:'Input evidence blocked',incomplete_judgments:'No complete pair of returned judgments',missing_baseline:'No baseline recorded',clinical_input_changed:'Clinical input differs',clinical_input_unverified:'Clinical input unverified',budget_cap_changed:'Configured budget cap differs',budget_cap_unverified:'Configured budget cap unverified',incomplete_judgment:'No usable returned judgment'};
  const comparisonReason=code=>Object.hasOwn(comparisonReasons,code)?comparisonReasons[code]:'Comparison not established';
  function checkedComparisons(value,target){
    const keys=(v,names)=>v&&typeof v==='object'&&!Array.isArray(v)&&Object.keys(v).length===names.length&&names.every(k=>Object.hasOwn(v,k));
    const boolOrNull=v=>v===null||typeof v==='boolean';
    const reasons=v=>Array.isArray(v)&&v.every(s=>typeof s==='string');
    const run=v=>v&&typeof v==='object'&&!Array.isArray(v)&&typeof v.id==='string'&&v.target_id===target.id&&v.cohort_id===target.cohort_id&&v.probability===null&&v.confidence===null&&v.calibration_status==='not_established'&&['Success','Failure','Uncertain'].includes(v.predicted_outcome)&&typeof v.status==='string'&&Array.isArray(v.sources)&&v.sources.every(s=>s&&typeof s==='object'&&!Array.isArray(s));
    if(!keys(value,['schema_version','target_id','patent_comparisons','agent_comparisons','public_policy'])||value.schema_version!=='predome-public-trial-comparisons-1'||value.target_id!==target.id||!Array.isArray(value.patent_comparisons)||!Array.isArray(value.agent_comparisons))throw Error('Invalid public comparison scope');
    const policy=value.public_policy;
    if(!keys(policy,['probability','confidence','clinical_prediction_validated','interpretation'])||policy.probability!==null||policy.confidence!==null||policy.clinical_prediction_validated!==false||typeof policy.interpretation!=='string')throw Error('Invalid public comparison policy');
    for(const p of value.patent_comparisons){
      if(!keys(p,['control','with_patents','registration_matched','model_version_matched','comparison_status','judgment_changed','reasons','added_sources'])||!(p.control===null||run(p.control))||!run(p.with_patents)||typeof p.registration_matched!=='boolean'||!boolOrNull(p.model_version_matched)||!['comparable_judgments','incomplete_judgments','not_comparable'].includes(p.comparison_status)||!boolOrNull(p.judgment_changed)||!reasons(p.reasons)||!Array.isArray(p.added_sources)||!p.added_sources.every(s=>s&&typeof s==='object'&&!Array.isArray(s)))throw Error('Invalid public patent comparison');
      if(p.comparison_status!=='comparable_judgments'&&p.judgment_changed!==null||p.comparison_status==='comparable_judgments'&&(!p.control||p.registration_matched!==true||p.model_version_matched!==true||typeof p.judgment_changed!=='boolean'))throw Error('Unestablished patent comparison');
    }
    for(const a of value.agent_comparisons){
      if(!keys(a,['run','workflow','baseline_run_ids','clinical_input_matched','common_budget_cap','component_models','reasons'])||!run(a.run)||!['shared_agent','open_agent'].includes(a.workflow)||!Array.isArray(a.baseline_run_ids)||!a.baseline_run_ids.every(s=>typeof s==='string')||!boolOrNull(a.clinical_input_matched)||!boolOrNull(a.common_budget_cap)||!reasons(a.reasons)||!Array.isArray(a.component_models)||!a.component_models.every(c=>keys(c,['role','requested_model'])&&['evidence_review','forecast'].includes(c.role)&&typeof c.requested_model==='string'))throw Error('Invalid public Agent comparison');
    }
    return value;
  }
  function comparisonRun(run){
    if(!run)return '<p class="public-comparison-empty">No matching clinical control is recorded.</p>';
    const direction=safeDirection(run),status=displayStatus(run),version=referenceText(run.model_version,300)||'Not recorded',name=referenceText(safeModel(run),300)||'Model not recorded';
    return `<div class="public-comparison-run" data-comparison-run="${esc(run.id)}"><strong>${esc(name)}</strong><span class="public-outcome public-outcome-${direction.toLowerCase()}">${direction}</span>${status?`<span class="public-comparison-status">${esc(status)}</span>`:''}<span class="public-comparison-version">Recorded model version: ${esc(version)}</span><time datetime="${esc(str(run.generated_at_utc))}">Recorded: ${utc(run.generated_at_utc)}</time><time datetime="${esc(str(run.locked_at_utc))}">Locked: ${utc(run.locked_at_utc)}</time><details class="public-comparison-rationale"><summary>Rationale &amp; sources</summary><p>${esc(short(referenceText(str(run.brief_reason||run.reason).replace(/[\r\n\t]+/g,' '),1600),650)||'No short rationale is available for this record.')}</p>${recordedReferences(run)}</details></div>`;
  }
  const comparisonFlag=(value,yes,no)=>value===true?yes:value===false?no:'Not established';
  function comparisonContent(value){
    const patents=value.patent_comparisons,agents=value.agent_comparisons;
    const notes=codes=>codes.length?`<p class="public-comparison-reasons">${[...new Set(codes.map(comparisonReason))].map(esc).join(' · ')}</p>`:'';
    return `<p class="public-comparison-scope">Same exact registered question · separate research records. Clinical-only baseline consensus is unchanged. Probability and confidence: N/A.</p><h3>Added patent evidence</h3><p class="public-comparison-note">Categorical judgments only. A change is not evidence of improved prediction; an incomplete pair cannot show an evidence effect.</p>${patents.length?`<div class="public-patent-comparisons">${patents.map(p=>`<article class="public-patent-comparison" data-comparison-status="${esc(p.comparison_status)}"><div class="public-comparison-pair"><div><h4>Clinical evidence</h4>${comparisonRun(p.control)}</div><div><h4>Clinical + patent evidence</h4>${comparisonRun(p.with_patents)}</div></div><p class="public-comparison-verdict"><strong>${p.comparison_status==='comparable_judgments'?(p.judgment_changed?'Direction changed':'Direction unchanged'):p.comparison_status==='incomplete_judgments'?'Comparison unavailable · incomplete judgments':'Comparison not established'}</strong><span>Frozen registration: ${comparisonFlag(p.registration_matched,'Matched','Not matched')} · Model version: ${comparisonFlag(p.model_version_matched,'Matched','Not matched')}</span></p>${notes(p.reasons)}${p.added_sources.length?`<details class="public-comparison-added"><summary>Added recorded sources · ${p.added_sources.length}</summary>${recordedReferences({sources:p.added_sources})}</details>`:''}</article>`).join('')}</div>`:'<p class="public-comparison-empty">No patent-added assignments are recorded for this exact trial record.</p>'}<h3>Agent workflow records</h3><p class="public-comparison-note">System observations, not a causal test of Agent benefit. A common configured budget cap does not establish equal actual computation. These records do not add votes to baseline consensus.</p>${agents.length?`<div class="public-agent-comparisons">${agents.map(a=>`<article class="public-agent-comparison" data-agent-workflow="${esc(a.workflow)}"><h4>${a.workflow==='shared_agent'?'Shared evidence-review workflow':'Two-model Agent system'}</h4>${a.component_models.length?`<p class="public-comparison-components">${a.component_models.map(c=>`${c.role==='evidence_review'?'Declared reviewer':'Declared forecaster'}: ${esc(referenceText(c.requested_model,300)||'Model not recorded')}`).join(' · ')}</p>`:''}${comparisonRun(a.run)}<p class="public-comparison-flags">Clinical input: ${comparisonFlag(a.clinical_input_matched,'Matched baseline','Differs from baseline')} · Configured budget cap: ${comparisonFlag(a.common_budget_cap,'Common cap','Different cap')} · ${a.baseline_run_ids.length} baseline record${a.baseline_run_ids.length===1?'':'s'} linked</p>${notes(a.reasons)}</article>`).join('')}</div>`:'<p class="public-comparison-empty">No Agent workflow assignments are recorded for this exact trial record.</p>'}<p class="public-comparison-note">Unresolved outcomes have no correctness score or ranking. Frozen registration matches are metadata checks, not scientific approval. Underlying model weights and calibration are not independently established.</p>`;
  }
  async function loadComparisons(){
    const target=state.detail?.target,seq=state.request,box=$('public-comparisons');if(!target||!box)return;
    const request={targetId:target.id,seq};state.comparisons=request;box.setAttribute('aria-busy','true');box.innerHTML='<p class="public-comparison-empty" role="status">Loading recorded comparisons…</p>';
    try{
      const value=checkedComparisons(await api('/api/arena/public/targets/'+encodeURIComponent(target.id)+'/comparisons'),target);
      if(state.request!==seq||state.detail?.target?.id!==target.id||state.comparisons!==request)return;
      box.innerHTML=comparisonContent(value);request.loaded=true;
    }catch{
      if(state.request!==seq||state.detail?.target?.id!==target.id||state.comparisons!==request)return;
      box.innerHTML='<p class="public-comparison-empty" role="status">Recorded comparisons are unavailable. The clinical baseline remains unchanged.</p><button type="button" class="public-comparison-retry">Retry comparisons</button>';
    }finally{if(state.request===seq&&state.comparisons===request)box.setAttribute('aria-busy','false');}
  }
  function toggleComparisons(){
    const box=$('public-comparisons'),button=$('public-comparison-toggle');if(!box||!button)return;
    box.hidden=!box.hidden;button.setAttribute('aria-expanded',String(!box.hidden));if(!box.hidden&&!state.comparisons)loadComparisons();
  }
  function forecastTable(runs){
    const rows=[...runs].sort((a,b)=>safeModel(a).localeCompare(safeModel(b))),replay=state.detail?.target?.scope==='archived_research';
    return `<section class="public-module public-model-module" aria-label="AI model predictions"><div class="public-model-heading"><div><span class="arena-section-kicker">AI CONTENDERS · SAME CLINICAL QUESTION</span><h2>AI Clinical Trial Predictions</h2><p class="public-model-subtitle">Compare predictions from multiple AI models for the same clinical trial.</p></div><div class="public-model-actions"><span class="arena-evidence-tag">${rows.length} assigned model${rows.length===1?'':'s'} · ${replay?'historical replay':'same evidence'}</span><button type="button" id="public-comparison-toggle" aria-expanded="false" aria-controls="public-comparisons">Agent &amp; patent comparisons <span aria-hidden="true">+</span></button></div></div>${faceoff(rows)}${rows.length?`<table class="public-table"><thead><tr><th scope="col">Model / Version</th><th scope="col">Predicted Outcome</th><th scope="col">Success Probability</th><th scope="col">Confidence</th></tr></thead><tbody>${rows.map((r,i)=>{const direction=safeDirection(r),reasonId='public-reason-'+i,match=matchStatus(state.detail.target,r),version=str(r.model_version),identity=modelIdentity(r);return `<tr class="public-model-row arena-contender arena-contender-${identity.key}" data-run-id="${esc(r.id)}"><td data-label="Model / Version"><div class="arena-model-heading"><span class="arena-model-avatar" aria-hidden="true">${identity.initial}</span><div class="public-model-id">${esc(safeModel(r))}</div></div>${version&&version!==safeModel(r)?`<span class="public-model-version">Version: ${esc(version)}</span>`:''}<div class="public-model-meta"><time class="public-prediction-time"${str(r.generated_at_utc)?` datetime="${esc(r.generated_at_utc)}"`:''}>${r.status==='predicted'?'Prediction time:':'Recorded:'} ${utc(r.generated_at_utc)}</time><button class="public-reason-toggle" type="button" data-reason-id="${reasonId}" aria-controls="${reasonId}" aria-expanded="false">Rationale &amp; sources</button></div></td><td data-label="Predicted Outcome"><span class="public-outcome public-outcome-${direction.toLowerCase()}">${direction}</span>${match?`<span class="public-match public-match-${match}">${match==='matched'?'✓ Matched':'✕ Mismatched'}</span>`:''}${displayStatus(r)?`<span class="public-run-status">${esc(displayStatus(r))}</span>`:''}</td><td class="public-probability" data-label="Success Probability"><span class="public-value-na">N/A</span><span class="public-empty-meter" role="img" aria-label="No calibrated success probability available"></span></td><td class="public-confidence" data-label="Confidence"><span class="public-value-na">N/A</span></td></tr><tr id="${reasonId}" class="public-reason-row" hidden><td colspan="4"><p>${esc(short(r.brief_reason||r.reason,650)||'No short rationale is available for this record.')}</p>${recordedReferences(r)}</td></tr>`;}).join('')}</tbody></table>`:'<p class="public-summary-note">No same-evidence model assignments are available for this exact record. No example models or estimates are substituted.</p>'}<p class="public-calibration-note"><strong>Probability and confidence are not established.</strong> Clinical calibration has not been demonstrated; direction labels are model judgments, not validated clinical probabilities.</p><div id="public-comparisons" class="public-comparisons" hidden aria-busy="false" aria-label="Agent and patent comparison records"></div></section>`;
  }
  function consensusData(runs){
    const source=state.detail?.consensus||state.detail?.target?.consensus;
    if(!source||!Number.isInteger(source.assigned)||source.assigned!==runs.length||source.assigned<0)return null;
    const counts=source.counts||{},sum=['Success','Failure','Uncertain'].reduce((n,k)=>n+(Number.isInteger(counts[k])&&counts[k]>=0?counts[k]:NaN),0);
    if(Object.keys(counts).length!==3||sum!==source.assigned)return null;
    const expected=['Success','Failure','Uncertain'].find(k=>counts[k]>source.assigned/2)||'Uncertain';
    const usable=runs.filter(r=>['predicted','abstain','abstained'].includes(r.status)).length;
    if(source.predicted_outcome!==expected||source.agreement_denominator!==usable)return null;
    // Agreement is a producer-derived metric among returned judgments. Technical
    // failures still count as assigned Uncertain, but cannot become agreement.
    if(usable<2?source.agreement!==null:typeof source.agreement!=='number'||!Number.isFinite(source.agreement)||source.agreement<=0||source.agreement>1)return null;
    return source;
  }
  function summary(t,runs){
    const c=consensusData(runs),r=t.result||{},adjudicated=accepted(t),reported=isReported(t),replay=historical(t),waiting=agreementPending(t),showLabel=adjudicated||replay&&['adjudicated','verified'].includes(t.status);
    const actual=reported?(waiting?'Awaiting independent review':!showLabel?'Awaiting adjudication':{success:'Success',failure:'Failure',mixed:'Mixed',inconclusive:'Inconclusive',not_adjudicable:'Inconclusive'}[r.label]||'Inconclusive'):'Awaiting results';
    const endpoint=showLabel&&r.label==='success'?'Met':showLabel&&r.label==='failure'?'Not Met':'Not Yet Determined';
    const binary=adjudicated&&['success','failure'].includes(r.label);
    const matched=runs.filter(x=>matchStatus(t,x)==='matched').length,mismatched=runs.filter(x=>matchStatus(t,x)==='mismatched').length;
    const comparison=matched+mismatched?`${matched} matched / ${mismatched} mismatched (${matched+mismatched} evaluable)`:'Not yet evaluable';
    const agreement=c&&c.agreement!==null?`${Number((c.agreement*100).toFixed(1))}% (${c.agreement_denominator} usable judgments)`:'N/A';
    const usable=c?`${c.agreement_denominator} / ${c.assigned} assigned`:'N/A';
    return `<section class="public-module public-summary-module" aria-label="Consensus and actual result"><div class="public-consensus"><h2><span class="arena-panel-index" aria-hidden="true">01</span> AI Consensus${c&&c.counts.Success>0&&c.counts.Failure>0?'<span class="public-divergence">Directions differ</span>':''}</h2>${c&&c.agreement!==null?`<svg class="public-agreement-bar" role="img" viewBox="0 0 100 5" preserveAspectRatio="none" aria-label="Model agreement ${Number((c.agreement*100).toFixed(1))}% among ${c.agreement_denominator} usable judgments; ${c.agreement_denominator} of ${c.assigned} assigned models"><rect class="public-agreement-track" x="0" y="0" width="100" height="5"></rect><rect class="public-agreement-fill" x="0" y="0" width="${c.agreement*100}" height="5"><title>${agreement}</title></rect></svg>`:'<p class="public-agreement-empty">Agreement requires at least 2 usable judgments.</p>'}<div class="public-summary-row"><span>Majority Prediction</span><span class="public-consensus-outcome">${c?outcome(c.predicted_outcome):'Uncertain'}</span></div><div class="public-summary-row"><span>Models Predicting Success</span><span class="public-success-count">${c?`${c.counts.Success} / ${c.assigned} assigned`:'N/A'}</span></div><div class="public-summary-row"><span>Model agreement</span><span class="public-agreement">${agreement}</span></div><div class="public-summary-row"><span>Usable judgments</span><span class="public-usable-count">${usable}</span></div><div class="public-summary-row"><span>Average success probability</span><span>N/A</span></div><p class="public-summary-note">Majority uses all assigned; missing responses count as Uncertain. Agreement uses usable judgments, including abstentions; failures are excluded. It is not confidence.</p></div><div class="public-actual" data-outcome-status="${esc(t.status||'pending')}"><h2><span class="arena-panel-index" aria-hidden="true">02</span> Actual clinical result</h2>${reveal(t)}<div class="public-summary-row"><span>Actual outcome</span><span class="public-actual-outcome">${actual}</span></div><div class="public-summary-row"><span>Primary endpoint</span><span class="public-endpoint-met">${endpoint}</span></div>${!reported?`<div class="public-summary-row public-pending-readout"><span>Expected Readout</span><span>${esc(displayReadout(t).label||'—')}${readoutSource(displayReadout(t))}</span></div>`:''}<div class="public-summary-row"><span>Official announcement</span><span>${reported?esc(day(r.first_public_at||r.published_at)):'—'}${reported&&r.source_url?' · '+link(r.source_url,'Source'):''}</span></div><div class="public-summary-row"><span>Prediction vs Reality</span><span class="public-reality-match">${comparison}</span></div><p class="public-summary-note">${!reported?'The target outcome has not been resolved. Unpublished is not failure.':replay?'Historical reported result; this replay does not count as a prospective hit.':waiting?'Source checks agree; independent scientific review is pending. No match or score is assigned.':!adjudicated?'Public information awaits independent review against the exact registered target.':!binary?'Mixed or inconclusive outcomes do not produce a right-or-wrong score.':'Match labels require an exact adjudicated binary outcome and a prediction made before its public release.'}</p>${str(r.verification_note)?`<p class="public-summary-note public-result-verification">${esc(r.verification_note)}</p>`:''}</div></section>`;
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
    const seq=++state.request;state.detail=null;state.comparisons=null;$('public-body').innerHTML='';$('public-body').setAttribute('aria-busy','true');$('arena-status').textContent='Loading public clinical records…';
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
  document.addEventListener('click',e=>{const match=e.target.closest('[data-arena-target]');if(match){state.targetId=match.dataset.arenaTarget;load(false);}const period=e.target.closest('[data-public-period]');if(period){state.period=period.dataset.publicPeriod;state.targetId='';load(false);}const toggle=e.target.closest('[data-reason-id]');if(toggle){const row=$(toggle.dataset.reasonId);row.hidden=!row.hidden;toggle.setAttribute('aria-expanded',String(!row.hidden));}if(e.target.closest('#public-comparison-toggle'))toggleComparisons();if(e.target.closest('.public-comparison-retry'))loadComparisons();if(e.target.id==='public-retry')load(true);});
  window.addEventListener('resize',centerSchedule,{passive:true});
  load();
})();
