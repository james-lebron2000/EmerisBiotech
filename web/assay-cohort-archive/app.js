'use strict';
const $=s=>document.querySelector(s),esc=v=>String(v).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const COLORS=['#bd654b','#596f78'];let templates={},current,timer=null,evidenceKey=null,frameUpdates=[];
const DAY_MS=1500;
// Display-only interpolation between immutable daily model outputs. No model is fitted here.
function sample(values,day){const a=values[Math.floor(day)],b=values[Math.min(Math.ceil(day),values.length-1)];if(!a)return null;const t=day-Math.floor(day),p={...a,day};for(const k of ['viability_percent','median','q25','q75'])if(typeof a[k]==='number')p[k]=a[k]+(b[k]-a[k])*t;return p;}
function visible(values,day){const points=values.filter(p=>p.day<=day);if(day%1&&values.length)points.push(sample(values,day));return points;}
function drawFrame(){const day=+$('#day').value,cutoff=current.cutoff_day;$('#day-value').textContent=Number(day.toFixed(2));$('#phase').textContent=day>cutoff?'Extrapolated · unvalidated':([0,1,3,5,7].includes(day)?'Synthetic measurement':'Interpolated display');for(const update of frameUpdates)update(day);}
function play(){if(timer!==null)return;if(+$('#day').value>=+$('#day').max)$('#day').value=0;let previous=null;$('#play').textContent='Pause timeline';$('#play').setAttribute('aria-pressed','true');const tick=now=>{if(previous!==null)$('#day').value=Math.min(+$('#day').max,+$('#day').value+(now-previous)/DAY_MS);previous=now;drawFrame();if(+$('#day').value>=+$('#day').max){stop();return;}timer=requestAnimationFrame(tick);};timer=requestAnimationFrame(tick);}
const sha=async s=>Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(s))),v=>v.toString(16).padStart(2,'0')).join('');
function stop(){if(timer!==null)cancelAnimationFrame(timer);timer=null;$('#play').textContent='Play timeline';$('#play').setAttribute('aria-pressed','false');}
function fail(e){stop();$('#export-csv').hidden=true;$('#error').hidden=false;$('#error').textContent=e.message;for(const id of ['play','replay','day','cutoff','population','focus','export'])$('#'+id).disabled=true;}
function render(){
 frameUpdates=[];
 const activePatient=document.activeElement.closest?.('[data-patient]')?.dataset.patient;
 const day=+$('#day').value,pop=$('#population').value,focus=$('#focus').value,cutoff=current.cutoff_day;
 const molecules=Object.keys(current.molecules),paired=new Set(current.paired_patient_ids);
 const included=row=>pop==='available'||paired.has(row.patient.patient_id);
 $('#export-csv').href='trajectories-cutoff-'+cutoff+'.csv';$('#export-csv').hidden=false;
 $('#denominator').textContent=`${current.total_patients} patients total · ${paired.size} complete pairs · ${current.total_patients-paired.size} patients with missing pairs. `+(pop==='paired'?'Both summaries use the same complete-pair patients.':'Available-case summaries use different patient sets; no causal comparison.');
 const maxDay=cutoff+3,maxY=Math.max(120,Math.ceil(Math.max(...current.patients.flatMap(r=>molecules.flatMap(m=>r.responses[m].trajectory.map(p=>p.viability_percent))))/20)*20);
 $('#summary').innerHTML=molecules.map((m,i)=>{const s=sample(current.summaries[pop][m],day);return '<section class="stage" style="border-top:3px solid '+COLORS[i]+'"><h2>'+esc(current.molecules[m].label)+'</h2><span class="muted">Median viability · assay day '+day+'</span><strong class="value">'+(s.median===null?'Unavailable':s.median.toFixed(1)+'%')+'</strong><small>n = '+s.n+' / '+current.total_patients+' patients · IQR '+(s.q25===null?'unavailable':s.q25.toFixed(1)+'–'+s.q75.toFixed(1)+'%')+'</small></section>';}).join('');
 $('#plots').innerHTML=molecules.map((m,i)=>{
  const x=d=>42+d/maxDay*492,y=v=>260-v/maxY*235,pts=values=>values.map((p,j)=>(j?'L':'M')+x(p.day).toFixed(2)+','+y(p.viability_percent??p.median).toFixed(2)).join(' ');
  const summaries=current.summaries[pop][m],seen=current.patients.filter(included).filter(r=>r.responses[m].status==='estimable');
  const bindings=[];
  const paths=(values,color,width,opacity)=>{
   const id='curve-'+i+'-'+bindings.length;bindings.push({id,values});
   return '<path id="'+id+'-solid" class="trajectory-segment" data-phase="observed" fill="none" stroke="'+color+'" stroke-width="'+width+'" opacity="'+opacity+'"/><path id="'+id+'-tail" class="trajectory-segment" data-phase="extrapolated" fill="none" stroke="'+color+'" stroke-width="'+width+'" opacity="'+opacity+'" stroke-dasharray="5 4"/><circle id="'+id+'-tip" class="trajectory-tip" r="'+(width>1?3.5:1.6)+'" fill="'+color+'" opacity="'+opacity+'"/>';
  };
  let svg='<svg class="chart" viewBox="0 0 550 295" role="img" aria-label="'+esc(current.molecules[m].label)+' patient trajectories"><title>'+esc(current.molecules[m].label)+' paired assay trajectories</title>';
  svg+=[0,Math.round(maxY/2),maxY].map(v=>'<line class="gridline" x1="42" x2="534" y1="'+y(v)+'" y2="'+y(v)+'"/><text x="0" y="'+(y(v)+4)+'">'+v+'%</text>').join('');
  svg+='<path id="band-'+i+'" class="patient-iqr" fill="'+COLORS[i]+'" opacity=".10"/>';
  svg+=seen.filter(r=>r.patient.patient_id!==focus).map(r=>paths(r.responses[m].trajectory,COLORS[i],1,.26)).join('');
  svg+=paths(summaries,COLORS[i],3,1);
  const selected=current.patients.find(r=>r.patient.patient_id===focus);
  if(selected&&selected.responses[m].status==='estimable')svg+=paths(selected.responses[m].trajectory,'#302d29',3,1);
  svg+='<line class="timeline-cursor" x1="'+x(day)+'" x2="'+x(day)+'" y1="20" y2="260" stroke="#283f4b" stroke-dasharray="3 3"/>';
  svg+=[...new Set([0,cutoff,maxDay])].map(d=>'<text x="'+x(d)+'" y="282">'+d+'</text>').join('')+'</svg>';
  frameUpdates.push(time=>{
   const cursor=$('#plot-'+i+' .timeline-cursor');cursor.setAttribute('x1',x(time));cursor.setAttribute('x2',x(time));
   for(const {id,values} of bindings){const revealed=visible(values,time),tip=revealed.at(-1);for(const [suffix,points] of [['solid',revealed.filter(p=>p.day<=cutoff)],['tail',revealed.filter(p=>p.day>=cutoff)]]){const node=$('#'+id+'-'+suffix);node.setAttribute('d',points.length>1?pts(points):'');node.toggleAttribute('hidden',points.length<2);}
    const node=$('#'+id+'-tip');node.setAttribute('cx',x(tip.day));node.setAttribute('cy',y(tip.viability_percent??tip.median));
   }
   const v=visible(summaries,time);$('#band-'+i).setAttribute('d',v.length>1?v.map((p,j)=>(j?'L':'M')+x(p.day)+','+y(p.q75)).join(' ')+v.slice().reverse().map(p=>'L'+x(p.day)+','+y(p.q25)).join(' ')+'Z':'');
   const s=sample(summaries,time),card=$('#summary').children[i];card.querySelector('.muted').textContent='Median viability · assay day '+time.toFixed(2);card.querySelector('.value').textContent=s.median.toFixed(1)+'%';card.querySelector('small').textContent='n = '+s.n+' / '+current.total_patients+' patients · IQR '+s.q25.toFixed(1)+'–'+s.q75.toFixed(1)+'%';
  });
  return '<section id="plot-'+i+'" class="stage"><h2>'+esc(current.molecules[m].label)+'</h2><p class="muted">'+seen.length+' summarized patients'+(focus?' · highlighted: '+esc(focus):'')+'</p>'+svg+'</section>';
 }).join('');
 $('#patients').innerHTML=current.patients.map(row=>'<button class="patient-card" data-patient="'+row.patient.patient_id+'" aria-pressed="'+(focus===row.patient.patient_id)+'"><strong>'+row.patient.patient_id+'</strong><span class="muted">'+(paired.has(row.patient.patient_id)?'Complete pair':'Incomplete pair · excluded from paired summary')+'</span>'+molecules.map((m,i)=>{const r=row.responses[m],p=sample(r.trajectory,day);return '<span class="muted '+(p?'':'missing')+'">'+esc(current.molecules[m].label)+' · '+(p?p.viability_percent.toFixed(1)+'%':'Not estimable')+'</span><div class="bar-track"><div class="bar-fill" style="width:'+(p?Math.min(100,p.viability_percent/maxY*100):0)+'%;background:'+COLORS[i]+'"></div></div>';}).join('')+'</button>').join('');
 frameUpdates.push(time=>{for(const row of current.patients){const card=document.querySelector('[data-patient="'+row.patient.patient_id+'"]');molecules.forEach((m,i)=>{const p=sample(row.responses[m].trajectory,time);card.querySelectorAll('.muted')[i+1].textContent=current.molecules[m].label+' · '+(p?p.viability_percent.toFixed(1)+'%':'Not estimable');card.querySelectorAll('.bar-fill')[i].style.width=(p?Math.min(100,p.viability_percent/maxY*100):0)+'%';});}});
 drawFrame();
 if(activePatient)document.querySelector('[data-patient="'+activePatient+'"]')?.focus({preventScroll:true});
 const row=current.patients.find(r=>r.patient.patient_id===focus);
 const nextEvidenceKey=current.input_sha256+'/'+focus;
 if(nextEvidenceKey!==evidenceKey){
 evidenceKey=nextEvidenceKey;
 $('#evidence').innerHTML=row?'<p><strong>'+esc(focus)+'</strong> · '+esc(row.patient.sample_id)+' · Synthetic functional assay</p>'+molecules.map(m=>{const r=row.responses[m];return '<details><summary>'+esc(current.molecules[m].label)+' · '+(r.status==='estimable'?'paired evidence':'missing pair')+'</summary>'+(r.status==='estimable'?'<p class="muted">'+esc(r.assay.exposure)+'</p>'+r.assay.rows.map(p=>'<p class="muted">'+esc(p.source_id)+'<br>Day '+p.day+' · drug: '+p.drug_counts.join(', ')+' · vehicle: '+p.vehicle_counts.join(', ')+'</p>').join(''):'<p>'+esc(r.reason)+'</p>')+'</details>';}).join('')+'<p class="muted">Resistance fraction and clinical outcomes remain unresolved.</p>':'<p class="muted">Choose a patient to inspect paired source records.</p>';
 }
}
async function start(){
 const response=await fetch('scenarios.json');if(!response.ok)throw Error('Unable to load cohort assets.');const bundle=await response.json();
 if(bundle.schema!=='patient-cohort-public-v1'||bundle.locale!=='en'||bundle.clinical_use!==false)throw Error('Unexpected cohort bundle.');
 for(const cutoff of ['3','5','7']){const t=bundle.templates[cutoff];if(await sha(t.canonical_result)!==t.sha256)throw Error('Cohort template checksum failed.');const result=JSON.parse(t.canonical_result);if(result.cutoff_day!==+cutoff||result.clinical_use!==false||result.source_kind!=='synthetic'||result.total_patients!==24)throw Error('Cohort template mismatch.');templates[cutoff]={...t,result};}
 current=templates['7'].result;$('#focus').innerHTML+=current.patients.map(r=>'<option>'+r.patient.patient_id+'</option>').join('');
 for(const id of ['play','replay','day','cutoff','population','focus','export'])$('#'+id).disabled=false;
 $('#cutoff').addEventListener('change',()=>{stop();current=templates[$('#cutoff').value].result;$('#day').max=current.cutoff_day+3;$('#day').value=0;render();});
 $('#population').addEventListener('change',render);$('#focus').addEventListener('change',render);$('#day').addEventListener('input',()=>{stop();drawFrame();});
 $('#patients').addEventListener('click',e=>{const button=e.target.closest('[data-patient]');if(button){$('#focus').value=$('#focus').value===button.dataset.patient?'':button.dataset.patient;render();}});
 $('#play').addEventListener('click',()=>{if(timer!==null)stop();else play();});
 $('#replay').addEventListener('click',()=>{stop();$('#day').value=0;drawFrame();play();});
 document.addEventListener('visibilitychange',()=>{if(document.hidden)stop();});
 $('#export').addEventListener('click',()=>{const t=templates[$('#cutoff').value],payload={run_id:crypto.randomUUID(),created_at:new Date().toISOString(),view:{assay_day:+$('#day').value,population:$('#population').value,highlight_patient:$('#focus').value||null},canonical_template:t.canonical_result,template_sha256:t.sha256,checksum_scope:'canonical_template UTF-8 only; metadata is not signed',result:current};const url=URL.createObjectURL(new Blob([JSON.stringify(payload,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='virtual-cohort-'+payload.run_id+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
 render();
 if(!matchMedia('(prefers-reduced-motion: reduce)').matches)play();
}
start().catch(fail);
