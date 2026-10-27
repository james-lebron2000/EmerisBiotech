'use strict';
(() => {
  const q = selector => document.querySelector(selector);
  const escape = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const colors = {a:'#16858a', b:'#8961b0', ab:'#bd7728'};
  const labels = {a:'Plan A', b:'Plan B', ab:'Plan A+B'};
  const storageKey = 'emeris-patient-lab-v2-en';
  const state = {bundle:null, records:[], current:null, plan:'a', week:12, view:'response'};
  const template = () => {
    const record = state.records.find(r => r.run_id === state.current);
    const resistance = record ? record.resistance : q('#resistance').value;
    const cutoff = record ? record.cutoff : 0;
    return {record, resistance, cutoff, entry:state.bundle.templates[resistance][String(cutoff)]};
  };
  const flash = text => { q('#message').textContent = text; q('#message').hidden = false; };
  const fail = error => {q('#error').textContent = error.message || String(error);q('#error').hidden = false;};
  async function checksum(text) {
    const bytes = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
    return [...new Uint8Array(bytes)].map(v => v.toString(16).padStart(2,'0')).join('');
  }
  function validRecords(records) {
    if (!Array.isArray(records) || records.length > 200) throw Error('Invalid local scenario history. Saved data have not been replaced.');
    const ids = new Set();
    for (const record of records) {
      if (!record || !/^[a-f0-9-]{36}$/.test(record.run_id) || ids.has(record.run_id) ||
          !['low','high'].includes(record.resistance) || ![0,6,12].includes(record.cutoff) ||
          !Number.isFinite(Date.parse(record.created_at)) ||
          record.template_sha256 !== state.bundle.templates[record.resistance][String(record.cutoff)].sha256) {
        throw Error('A local record failed validation. Existing browser data have not been changed.');
      }
      if (record.cutoff === 0 && record.parent_run_id !== null) throw Error('Invalid baseline parent.');
      if (record.cutoff > 0) {
        const parent = records.find(r => r.run_id === record.parent_run_id);
        if (!parent || parent.cutoff + 6 !== record.cutoff || parent.resistance !== record.resistance) throw Error('Invalid local follow-up link.');
      }
      ids.add(record.run_id);
    }
    return records;
  }
  function persist(next) {
    validRecords(next);
    // Write first. Storage failure must not be presented as a saved run.
    localStorage.setItem(storageKey, JSON.stringify(next));
    state.records = next;
  }
  function saveRun() {
    if (state.records.length >= 200) throw Error('This browser has reached the 200-run showcase limit. Existing records are preserved.');
    const resistance = q('#resistance').value;
    const record = {run_id:crypto.randomUUID(), created_at:new Date().toISOString(), resistance, cutoff:0,
      parent_run_id:null, template_sha256:state.bundle.templates[resistance]['0'].sha256};
    persist([...state.records, record]); state.current = record.run_id;
    flash('Baseline saved in this browser. All three fictional plans share the same input snapshot.');render();
  }
  function followup() {
    const {record} = template();
    if (!record || record.cutoff >= 12) throw Error('No further synthetic follow-up is available.');
    const previous = state.records.find(r => r.parent_run_id === record.run_id);
    if (previous) {state.current = previous.run_id;flash('Reopened the existing follow-up version.');render();return;}
    if (state.records.length >= 200) throw Error('The local showcase limit has been reached. Earlier records are preserved.');
    const cutoff = record.cutoff + 6;
    const child = {run_id:crypto.randomUUID(), created_at:new Date().toISOString(), resistance:record.resistance, cutoff,
      parent_run_id:record.run_id, template_sha256:state.bundle.templates[record.resistance][String(cutoff)].sha256};
    persist([...state.records, child]);state.current = child.run_id;
    flash(`Week-${cutoff} synthetic follow-up saved. The previous forecast is unchanged.`);render();
  }
  function chart(curves, survival=false) {
    const width = Math.max(240, Math.round(q('#results').getBoundingClientRect().width));
    const height = width < 400 ? 235 : 260, left = 42, right = 12, top = 30, bottom = 40;
    const metric = survival ? 'illustrative_percent' : 'burden_index';
    const max = survival ? 100 : Math.ceil(Math.max(...Object.values(curves).flat().map(v => v.scenario_high))/20)*20;
    const x = t => left+t/24*(width-left-right), y = v => height-bottom-v/max*(height-top-bottom);
    let svg = `<svg class="chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="${survival?'Independent mathematical survival illustration, not patient prognosis':'Synthetic fictional-treatment burden scenarios'}"><title>${survival?'Mathematical illustration only':'Synthetic burden trajectories'}</title><text x="${left}" y="16">${survival?'Illustrative survival (%)':'Burden index (baseline 100)'}</text>`;
    for (const value of [0,max/2,max]) svg += `<line class="gridline" x1="${left}" x2="${width-right}" y1="${y(value)}" y2="${y(value)}"/><text x="${left-7}" y="${y(value)+4}" text-anchor="end">${value}</text>`;
    for (const t of [0,8,16,24]) svg += `<text x="${x(t)}" y="${height-20}" text-anchor="${t===0?'start':t===24?'end':'middle'}">${t}</text>`;
    const rows = curves[state.plan];
    if (!survival) {
      const points = rows.map((v,i)=>`${x(i)},${y(v.scenario_high)}`).concat([...rows].reverse().map((v,i)=>`${x(24-i)},${y(v.scenario_low)}`)).join(' ');
      svg += `<polygon points="${points}" fill="${colors[state.plan]}" opacity=".12"/>`;
    }
    for (const key of ['a','b','ab']) {
      const path = curves[key].map((v,i)=>`${i?'L':'M'}${x(i)},${y(v[metric])}`).join(' ');
      svg += `<path d="${path}" fill="none" stroke="${colors[key]}" stroke-width="${key===state.plan?3:1.8}" stroke-dasharray="${key==='b'?'7 4':key==='ab'?'2 4':''}"/>`;
    }
    if (!survival) svg += `<line x1="${x(state.week)}" x2="${x(state.week)}" y1="${top}" y2="${height-bottom}" stroke="#7c909a" stroke-dasharray="3 4"/><circle cx="${x(state.week)}" cy="${y(rows[state.week][metric])}" r="4" fill="${colors[state.plan]}"/>`;
    return svg+`<text x="${width-right}" y="${height-2}" text-anchor="end">${survival?'Scenario time (months)':'Weeks after current cutoff'}</text></svg>`;
  }
  function fields(values) {return '<dl class="facts">'+Object.entries(values).map(([k,v])=>`<div><dt>${escape(k)}</dt><dd>${escape(v)}</dd></div>`).join('')+'</dl>';}
  function render() {
    if (!state.bundle) return;
    const {record, entry, cutoff} = template(), data = entry.result, patient = data.patient;
    q('#profile').innerHTML = `<h2>${escape(patient.patient_id)}</h2><p>Anonymous identifier · Synthetic data<br>Data cutoff: week ${cutoff}</p><h3>Clinical context</h3>${fields(patient.clinical)}<details><summary>Biology &amp; missing information</summary>${fields(patient.molecular)}</details><h3>Available synthetic observations</h3>${patient.observations.map(r=>`<p>Week ${r.week} · index ${r.burden_index}<br>${escape(r.source_id)}</p>`).join('')}<p>Anatomy and omics are context only. Missing does not mean negative.</p>`;
    document.querySelectorAll('[data-plan]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.plan===state.plan)));
    document.querySelectorAll('[data-view]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.view===state.view)));
    q('#week-value').textContent = `week ${state.week}`;
    q('#mode').textContent = record ? `Saved run · cutoff ${cutoff}w` : 'Preview · not saved';
    let content;
    if (state.view === 'response') {
      const curves = Object.fromEntries(Object.entries(data.forecasts).map(([k,v])=>[k,v.trajectory]));
      const p = curves[state.plan][state.week], resistant = p.resistant_fraction*100;
      content = `<h2>Future burden trajectories</h2><p class="muted">Shared starting state. The band is an illustrative span, not a confidence interval.<br>Solid A · Dashed B · Dotted A+B</p>${chart(curves)}<div class="metrics" aria-live="polite"><div><span class="muted">${labels[state.plan]} · week ${state.week} burden index</span><strong class="value">${p.burden_index.toFixed(1)}</strong><span class="muted">Illustrative span ${p.scenario_low.toFixed(1)}–${p.scenario_high.toFixed(1)}</span></div><div><span class="muted">Simulated composition, not assay results</span><div class="cellbar"><span style="width:${100-resistant}%;background:var(--a)"></span><span style="width:${resistant}%;background:var(--ab)"></span></div><span class="muted">Sensitive ${(100-resistant).toFixed(0)}% · Resistant ${resistant.toFixed(0)}%</span></div></div><table><thead><tr><th>Fictional plan</th><th>Burden index</th><th>Resistant</th></tr></thead><tbody>${['a','b','ab'].map(k=>`<tr><td>${labels[k]}</td><td>${curves[k][state.week].burden_index.toFixed(1)}</td><td>${(curves[k][state.week].resistant_fraction*100).toFixed(0)}%</td></tr>`).join('')}</tbody></table><p class="muted">No optimal-treatment recommendation or clinical response classification is generated.</p>`;
    } else if (state.view === 'prognosis') {
      content = `<h2>Prognosis illustration</h2><p class="muted">Fixed mathematical curves, independent of this patient's burden and follow-up. Not PFS/OS predictions.</p>${chart(data.survival_illustration.curves,true)}<table><tr><th>Endpoint</th><th>Current availability</th></tr><tr><td>Imaging PR/PD</td><td>Unavailable: the burden index is not a RECIST measurement.</td></tr><tr><td>PFS / OS</td><td>Unavailable: no applicable individual model.</td></tr><tr><td>Toxicity / quality of life</td><td>Unavailable: requires independent data and models.</td></tr></table>`;
    } else {
      content = `<h2>Sources, assumptions &amp; scope</h2><dl class="evidence"><dt>Patient context</dt><dd>Built-in synthetic patient P001. Only observations available by the snapshot cutoff are included.</dd><dt>Computational inputs</dt><dd>Current burden, an assumed initial resistant fraction and fictional growth/suppression parameters. Diagnosis, age, anatomy and molecular fields are not model inputs.</dd><dt>Authoritative model</dt><dd>All six scenario templates are generated by the repository's Python engine. This page selects and displays verified templates; it does not implement a second clinical model.</dd><dt>Follow-up</dt><dd>Predetermined synthetic observations are 82 at week 6 and 69 at week 12, with fictional A recorded as received treatment. Updating resets burden, retains the resistance assumption and preserves previous runs. It does not retrain or validate the model.</dd><dt>Prognosis</dt><dd>Fixed mathematical illustrations remain independent. Clinical response, PFS, OS, toxicity and quality of life are unavailable.</dd><dt>Storage and review</dt><dd>Records stay in this browser. Execution success is separate from scientific review, which has not been performed. Checksums verify template consistency, not authorship or clinical validity.</dd></dl>`;
    }
    q('#results').innerHTML = content;
    q('#export').disabled = !record;
    q('#followup').hidden = !record || cutoff >= 12;
    q('#followup').textContent = `Add week-${cutoff+6} synthetic follow-up`;
    if (record) {
      const parent = state.records.find(r=>r.run_id===record.parent_run_id);
      q('#run-info').innerHTML = `Run <code>${escape(record.run_id)}</code><br>Created ${escape(record.created_at)}<br>Model ${escape(data.model_version)} · Scientific review not performed<details><summary>Template and input fingerprints</summary><p>Template <code>${entry.sha256}</code><br>Input <code>${data.input_sha256}</code></p></details>${parent?`<p>Parent <code>${escape(parent.run_id)}</code>. Earlier forecasts are unchanged. Unreceived treatments have no observed outcomes.</p>`:''}${cutoff===12?'<p>Both synthetic follow-ups are saved. The full demonstration workflow is complete.</p>':''}`;
    } else q('#run-info').textContent = 'Preview only. Save a baseline scenario to start a versioned record.';
    q('#history').innerHTML = state.records.length ? [...state.records].reverse().slice(0,30).map(r=>`<button data-run="${r.run_id}" aria-pressed="${r.run_id===state.current}">P001 · ${r.cutoff}w · ${r.run_id.slice(0,8)}</button>`).join('') : '<span class="muted">No saved runs in this browser.</span>';
    q('#history').querySelectorAll('[data-run]').forEach(b=>b.addEventListener('click',()=>{state.current=b.dataset.run;render();}));
  }
  function guarded(fn) {return (...args)=>{try {fn(...args);} catch(error) {fail(error);}};}
  q('#run').addEventListener('click', guarded(saveRun));q('#followup').addEventListener('click', guarded(followup));
  q('#resistance').addEventListener('change',()=>{if(!state.current)render();else flash('Resistance selection applies to a new baseline run; the saved run remains unchanged.');});
  q('#week').addEventListener('input',e=>{state.week=Number(e.target.value);render();});
  document.querySelectorAll('[data-plan]').forEach(b=>b.addEventListener('click',()=>{state.plan=b.dataset.plan;render();}));
  document.querySelectorAll('[data-view]').forEach(b=>b.addEventListener('click',()=>{state.view=b.dataset.view;render();}));
  q('#export').addEventListener('click', guarded(()=>{
    const {record, entry} = template();if(!record)throw Error('Save a run first.');
    const payload = {schema_version:'patient-browser-run-v1', storage:'visitor_browser_only', ...record,
      canonical_template:entry.canonical_result, result:entry.result, clinical_use:false};
    const url=URL.createObjectURL(new Blob([JSON.stringify(payload,null,2)],{type:'application/json'}));
    const link=document.createElement('a');link.href=url;link.download=`patient-P001-${record.run_id}.json`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }));
  let lastWidth=0;new ResizeObserver(entries=>{const width=Math.round(entries[0].contentRect.width);if(width!==lastWidth){lastWidth=width;if(state.bundle)render();}}).observe(q('.workspace'));
  (async()=>{
    const response=await fetch('scenarios.json',{cache:'no-cache'});if(!response.ok)throw Error('Scenario assets could not be loaded.');
    const data=await response.json();if(data.schema!=='patient-public-bundle-v1'||data.locale!=='en'||data.clinical_use!==false)throw Error('Unsupported scenario bundle.');
    for(const resistance of ['low','high'])for(const cutoff of ['0','6','12']) {
      const entry=data.templates[resistance][cutoff];if(await checksum(entry.canonical_result)!==entry.sha256)throw Error('Scenario template checksum mismatch.');
      entry.result=JSON.parse(entry.canonical_result);
      if(entry.result.patient.source_kind!=='synthetic'||entry.result.patient.patient_id!=='P001'||entry.result.clinical_use!==false)throw Error('Only the built-in synthetic patient is supported.');
    }
    state.bundle=data;
    const saved=localStorage.getItem(storageKey);if(saved){state.records=validRecords(JSON.parse(saved));state.current=state.records.at(-1)?.run_id||null;}
    q('#run').disabled=false;render();
  })().catch(fail);
})();
