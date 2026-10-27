'use strict';
const $=s=>document.querySelector(s),esc=v=>String(v).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const COLORS=['#14716e','#8961b0'];let templates={},current,timer=null,evidenceKey=null;
const sha=async s=>Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(s))),v=>v.toString(16).padStart(2,'0')).join('');
function stop(){clearInterval(timer);timer=null;$('#play').textContent='Play timeline';$('#play').setAttribute('aria-pressed','false');}
function fail(e){stop();$('#error').hidden=false;$('#error').textContent=e.message;for(const id of ['play','day','cutoff','population','focus','export'])$('#'+id).disabled=true;}
function render(){
 const activePatient=document.activeElement.closest?.('[data-patient]')?.dataset.patient;
 const day=+$('#day').value,pop=$('#population').value,focus=$('#focus').value,cutoff=current.cutoff_day;
 const molecules=Object.keys(current.molecules),paired=new Set(current.paired_patient_ids);
 const included=row=>pop==='available'||paired.has(row.patient.patient_id);
 $('#day-value').textContent=day;$('#phase').textContent=day>cutoff?'Extrapolated · unvalidated':([0,1,3,5,7].includes(day)?'Synthetic measurement':'Interpolated');
 $('#denominator').textContent=`${current.total_patients} patients total · ${paired.size} complete pairs · ${current.total_patients-paired.size} patients with missing pairs. `+(pop==='paired'?'Both summaries use the same complete-pair patients.':'Available-case summaries use different patient sets; no causal comparison.');
 const maxDay=cutoff+3,maxY=Math.max(120,Math.ceil(Math.max(...current.patients.flatMap(r=>molecules.flatMap(m=>r.responses[m].trajectory.map(p=>p.viability_percent))))/20)*20);
 $('#summary').innerHTML=molecules.map((m,i)=>{const s=current.summaries[pop][m][day];return '<section class="stage" style="border-top:3px solid '+COLORS[i]+'"><h2>'+esc(current.molecules[m].label)+'</h2><span class="muted">Median viability · assay day '+day+'</span><strong class="value">'+(s.median===null?'Unavailable':s.median.toFixed(1)+'%')+'</strong><small>n = '+s.n+' / '+current.total_patients+' patients · IQR '+(s.q25===null?'unavailable':s.q25.toFixed(1)+'–'+s.q75.toFixed(1)+'%')+'</small></section>';}).join('');
 $('#plots').innerHTML=molecules.map((m,i)=>{
  const x=d=>42+d/maxDay*492,y=v=>260-v/maxY*235,pts=values=>values.map((p,j)=>(j?'L':'M')+x(p.day).toFixed(2)+','+y(p.viability_percent??p.median).toFixed(2)).join(' ');
  const summaries=current.summaries[pop][m],seen=current.patients.filter(included).filter(r=>r.responses[m].status==='estimable');
  const band=summaries.map((p,j)=>(j?'L':'M')+x(p.day)+','+y(p.q75)).join(' ')+summaries.slice().reverse().map(p=>'L'+x(p.day)+','+y(p.q25)).join(' ')+'Z';
  const paths=(values,color,width,opacity)=>'<path d="'+pts(values.filter(p=>p.day<=cutoff))+'" fill="none" stroke="'+color+'" stroke-width="'+width+'" opacity="'+opacity+'"/><path d="'+pts(values.filter(p=>p.day>=cutoff))+'" fill="none" stroke="'+color+'" stroke-width="'+width+'" opacity="'+opacity+'" stroke-dasharray="5 4"/>';
  let svg='<svg class="chart" viewBox="0 0 550 295" role="img" aria-label="'+esc(current.molecules[m].label)+' patient trajectories"><title>'+esc(current.molecules[m].label)+' paired assay trajectories</title>';
  svg+=[0,Math.round(maxY/2),maxY].map(v=>'<line class="gridline" x1="42" x2="534" y1="'+y(v)+'" y2="'+y(v)+'"/><text x="0" y="'+(y(v)+4)+'">'+v+'%</text>').join('');
  svg+='<path d="'+band+'" fill="'+COLORS[i]+'" opacity=".12"/>';
  svg+=seen.filter(r=>r.patient.patient_id!==focus).map(r=>paths(r.responses[m].trajectory,COLORS[i],1,.26)).join('');
  svg+=paths(summaries,COLORS[i],3,1);
  const selected=current.patients.find(r=>r.patient.patient_id===focus);
  if(selected&&selected.responses[m].status==='estimable')svg+=paths(selected.responses[m].trajectory,'#b26718',3,1);
  svg+='<line x1="'+x(day)+'" x2="'+x(day)+'" y1="20" y2="260" stroke="#283f4b" stroke-dasharray="3 3"/>';
  svg+=[...new Set([0,cutoff,maxDay])].map(d=>'<text x="'+x(d)+'" y="282">'+d+'</text>').join('')+'</svg>';
  return '<section class="stage"><h2>'+esc(current.molecules[m].label)+'</h2><p class="muted">'+seen.length+' summarized patients'+(focus?' · gold: '+esc(focus):'')+'</p>'+svg+'</section>';
 }).join('');
 $('#patients').innerHTML=current.patients.map(row=>'<button class="patient-card" data-patient="'+row.patient.patient_id+'" aria-pressed="'+(focus===row.patient.patient_id)+'"><strong>'+row.patient.patient_id+'</strong><span class="muted">'+(paired.has(row.patient.patient_id)?'Complete pair':'Incomplete pair · excluded from paired summary')+'</span>'+molecules.map((m,i)=>{const r=row.responses[m],p=r.trajectory[day];return '<span class="muted '+(p?'':'missing')+'">'+esc(current.molecules[m].label)+' · '+(p?p.viability_percent.toFixed(1)+'%':'Not estimable')+'</span><div class="bar-track"><div class="bar-fill" style="width:'+(p?Math.min(100,p.viability_percent/maxY*100):0)+'%;background:'+COLORS[i]+'"></div></div>';}).join('')+'</button>').join('');
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
 for(const id of ['play','day','cutoff','population','focus','export'])$('#'+id).disabled=false;
 $('#cutoff').addEventListener('change',()=>{stop();current=templates[$('#cutoff').value].result;$('#day').max=current.cutoff_day+3;$('#day').value=Math.min(+$('#day').value,current.cutoff_day+3);render();});
 $('#population').addEventListener('change',render);$('#focus').addEventListener('change',render);$('#day').addEventListener('input',()=>{stop();render();});
 $('#patients').addEventListener('click',e=>{const button=e.target.closest('[data-patient]');if(button){$('#focus').value=$('#focus').value===button.dataset.patient?'':button.dataset.patient;render();}});
 $('#play').addEventListener('click',()=>{if(timer){stop();return;}if(+$('#day').value>=+$('#day').max)$('#day').value=0;$('#play').textContent='Pause timeline';$('#play').setAttribute('aria-pressed','true');render();timer=setInterval(()=>{$('#day').value=+$('#day').value+1;render();if(+$('#day').value>=+$('#day').max)stop();},700);});
 document.addEventListener('visibilitychange',()=>{if(document.hidden)stop();});
 $('#export').addEventListener('click',()=>{const t=templates[$('#cutoff').value],payload={run_id:crypto.randomUUID(),created_at:new Date().toISOString(),view:{assay_day:+$('#day').value,population:$('#population').value,highlight_patient:$('#focus').value||null},canonical_template:t.canonical_result,template_sha256:t.sha256,checksum_scope:'canonical_template UTF-8 only; metadata is not signed',result:current};const url=URL.createObjectURL(new Blob([JSON.stringify(payload,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='virtual-cohort-'+payload.run_id+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
 render();
}
start().catch(fail);
