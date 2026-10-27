'use strict';
const $=s=>document.querySelector(s), esc=v=>String(v).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const KEY='emeris-patient-molecule-v1';
let bundle,templates={},records=[],active=null,current;
const selection=()=>({patient:$('#patient').value,molecule:$('#molecule').value,cutoff:$('#cutoff').value});
const key=s=>[s.patient,s.molecule,s.cutoff].join('/');
const fail=e=>{$('#error').hidden=false;$('#error').textContent=e.message;};
const sha=async text=>Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(text))),v=>v.toString(16).padStart(2,'0')).join('');
function loadRecords(){
 const raw=localStorage.getItem(KEY);if(raw===null)return [];
 const rows=JSON.parse(raw);if(!Array.isArray(rows)||rows.length>200)throw Error('Invalid saved run collection. Export browser storage before repair.');
 const ids=new Set();
 for(const r of rows){const template=templates[key(r)];if(!template||!/^[-a-f0-9]{36}$/.test(r.run_id)||ids.has(r.run_id)||!Number.isFinite(Date.parse(r.created_at))||r.template_sha256!==template.sha256)throw Error('Saved run integrity check failed.');ids.add(r.run_id);}
 return rows;
}
function render(){
 const s=selection();current=templates[key(s)].result;const r=current.response;
 $('#patient-title').textContent=s.patient;
 $('#profile').innerHTML='<p>Synthetic patient · '+esc(current.patient.sample_id)+'</p><h3>Clinical context</h3><dl>'+Object.entries(current.patient.clinical).map(([k,v])=>'<div><dt>'+esc(k)+'</dt><dd>'+esc(v)+'</dd></div>').join('')+'</dl><h3>Microscopic context</h3><dl>'+Object.entries(current.patient.molecular).map(([k,v])=>'<div><dt>'+esc(k)+'</dt><dd>'+esc(v)+'</dd></div>').join('')+'</dl><h3>Selected molecule</h3><p>'+esc(current.molecule.label)+'</p><a target="_blank" rel="noopener noreferrer" href="'+esc(current.molecule.identity_source)+'">NCI molecule identity</a><p>Identity reference only; no source for simulated effect sizes.</p>';
 $('#state').innerHTML='<div class="metrics"><div><span class="muted">Day '+s.cutoff+' residual viability</span><strong class="value">'+(r.status==='estimable'?r.terminal_viability_percent.toFixed(1)+'%':'Not estimable')+'</strong><span class="muted">'+(r.status==='estimable'?'Vehicle- and baseline-normalized':'No paired patient–molecule measurement')+'</span></div><div><span class="muted">Resistance status</span><strong class="value" style="font-size:22px">Unresolved</strong><span class="muted">No resistant-cell fraction measured.</span></div></div>';
 if(r.status!=='estimable'){$('#chart').innerHTML='<p role="status">'+esc(r.reason)+'</p>';$('#measurements').innerHTML='';}
 else{
  const rows=r.trajectory,w=600,h=270,left=50,top=15,bottom=235,x=d=>left+d/10*(w-left-15),y=v=>bottom-v/120*(bottom-top);
  const line=values=>values.map((p,i)=>(i?'L':'M')+x(p.day).toFixed(2)+','+y(p.viability_percent).toFixed(2)).join(' ');
  $('#chart').innerHTML='<svg class="chart" viewBox="0 0 '+w+' '+h+'" role="img" aria-label="Selected molecule assay viability, observed and extrapolated"><title>Matched assay response with a three-day extrapolation</title>'+[0,30,60,90,120].map(v=>'<line class="gridline" x1="50" x2="585" y1="'+y(v)+'" y2="'+y(v)+'"/><text x="3" y="'+(y(v)+4)+'">'+v+'%</text>').join('')+[0,3,5,7,10].map(d=>'<text x="'+x(d)+'" y="260">'+d+'</text>').join('')+'<path d="'+line(rows.filter(p=>p.day<=+s.cutoff))+'" fill="none" stroke="#14716e" stroke-width="3"/><path d="'+line(rows.filter(p=>p.day>=+s.cutoff))+'" fill="none" stroke="#8961b0" stroke-width="3" stroke-dasharray="6 5"/>'+r.measured.map(p=>'<circle cx="'+x(p.day)+'" cy="'+y(p.mean_percent)+'" r="4" fill="#14716e"/>').join('')+'</svg>';
  $('#measurements').innerHTML='<p class="muted">Exposure: '+esc(r.assay.exposure)+'. Technical replicate range only; no predictive confidence interval.</p><table><thead><tr><th>Day</th><th>Viability</th><th>Technical range</th></tr></thead><tbody>'+r.measured.map(p=>'<tr><td>'+p.day+'</td><td>'+p.mean_percent.toFixed(1)+'%</td><td>'+p.replicate_min.toFixed(1)+'–'+p.replicate_max.toFixed(1)+'%</td></tr>').join('')+'</tbody></table><details><summary>Raw paired measurements &amp; provenance</summary>'+r.assay.rows.map(p=>'<p class="muted">'+esc(p.source_id)+'<br>Drug counts: '+p.drug_counts.join(', ')+'<br>Vehicle counts: '+p.vehicle_counts.join(', ')+'</p>').join('')+'</details>';
 }
 $('#run-info').innerHTML=(active?'Saved run: '+esc(active.run_id)+' · '+esc(active.created_at):'Preview · not saved')+'<br>Data cutoff: assay day '+s.cutoff+' · '+esc(current.model_version)+'<br>Input SHA-256: <code>'+current.input_sha256+'</code>';
 $('#history').innerHTML=records.length?records.map(r=>'<button data-run="'+r.run_id+'" aria-pressed="'+(active?.run_id===r.run_id)+'">'+esc(r.patient)+' · '+esc(bundle.molecules[r.molecule].label)+' · day '+r.cutoff+'</button>').join(''):'<p class="muted">No saved runs in this browser.</p>';
 $('#export').disabled=!active;
 $('#save').disabled=r.status!=='estimable'||records.length>=200;
}
async function start(){
 const response=await fetch('scenarios.json');if(!response.ok)throw Error('Unable to load simulation assets.');bundle=await response.json();
 if(bundle.schema!=='patient-molecule-public-v1'||bundle.locale!=='en'||bundle.clinical_use!==false)throw Error('Unexpected simulation bundle.');
 for(const patient of bundle.patients)for(const molecule of Object.keys(bundle.molecules))for(const cutoff of ['3','5','7']){
  const t=bundle.templates[patient][molecule][cutoff];if(await sha(t.canonical_result)!==t.sha256)throw Error('Simulation template checksum failed.');
  const result=JSON.parse(t.canonical_result);if(result.patient.patient_id!==patient||result.molecule_id!==molecule||result.cutoff_day!==+cutoff||result.clinical_use!==false||result.source_kind!=='synthetic')throw Error('Patient–molecule template mismatch.');
  templates[key({patient,molecule,cutoff})]={...t,result};
 }
 records=loadRecords();
 $('#patient').innerHTML=bundle.patients.map(p=>'<option>'+esc(p)+'</option>').join('');
 $('#molecule').innerHTML=Object.entries(bundle.molecules).map(([k,v])=>'<option value="'+esc(k)+'">'+esc(v.label)+'</option>').join('');
 for(const id of ['patient','molecule','cutoff']){$('#'+id).disabled=false;$('#'+id).addEventListener('change',()=>{active=null;$('#message').hidden=true;render();});}
 $('#save').addEventListener('click',()=>{try{
  const s=selection(),next=[...records,{...s,run_id:crypto.randomUUID(),created_at:new Date().toISOString(),template_sha256:templates[key(s)].sha256}];
  localStorage.setItem(KEY,JSON.stringify(next));records=next;active=records.at(-1);render();$('#message').hidden=false;$('#message').textContent='Paired simulation saved in this browser.';
 }catch(e){fail(e);}});
 $('#history').addEventListener('click',e=>{const button=e.target.closest('[data-run]');if(!button)return;active=records.find(r=>r.run_id===button.dataset.run);for(const id of ['patient','molecule','cutoff'])$('#'+id).value=active[id];render();});
 $('#export').addEventListener('click',()=>{const t=templates[key(active)],data={record:active,checksum_scope:'canonical_template UTF-8 only; metadata is not signed',canonical_template:t.canonical_result,template_sha256:t.sha256,result:t.result};const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='patient-molecule-'+active.run_id+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
 render();
}
start().catch(fail);
