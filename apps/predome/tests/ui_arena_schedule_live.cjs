'use strict';
// Explicitly opt-in, read-only actual public-data acceptance. Never call a model.
const fs=require('fs'),path=require('path'),assert=require('assert');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const base=(process.env.ARENA_PUBLIC_LIVE_BASE||'').replace(/\/$/,'');
if(!base.startsWith('https://'))throw Error('Explicit HTTPS public base required');
const root=path.resolve(__dirname,'..'),local=process.env.ARENA_PUBLIC_LOCAL_ASSETS==='true';
const out=path.join(root,'verification/arena-faceoff-ui',local?'preview':'production');
(async()=>{
 fs.mkdirSync(out,{recursive:true});const browser=await chromium.launch({headless:true,...(process.env.CHROMIUM_EXECUTABLE?{executablePath:process.env.CHROMIUM_EXECUTABLE}:{})});
 try{
  const ctx=await browser.newContext({viewport:{width:1440,height:900}}),p=await ctx.newPage(),errors=[],requests=[];
  p.on('pageerror',e=>errors.push(e.message));p.on('request',r=>requests.push({method:r.method(),path:new URL(r.url()).pathname}));
  if(local)await ctx.route('**/*',async route=>{const u=new URL(route.request().url());if(u.origin!==base)return route.continue();const relative=['/arena','/'].includes(u.pathname)?'static/arena-public.html':u.pathname.startsWith('/static/')?u.pathname.slice(1):null;if(!relative)return route.continue();const f=path.resolve(root,relative);if(!f.startsWith(root+path.sep)||!fs.existsSync(f))return route.continue();return route.fulfill({body:fs.readFileSync(f),headers:{'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:"},contentType:f.endsWith('.html')?'text/html':f.endsWith('.js')?'application/javascript':f.endsWith('.svg')?'image/svg+xml':'text/css'});});
  const response=await ctx.request.get(base+'/api/arena/public');assert(response.ok());const data=await response.json(),targets=data.targets.filter(t=>t.cohort_id===data.current_cohort_id);
  assert.strictEqual(targets.length,12);await p.goto(base+'/arena');const settled=()=>p.waitForFunction(()=>document.getElementById('public-body')?.getAttribute('aria-busy')==='false');await settled();
  assert.strictEqual(await p.locator('#arena-fixtures [data-arena-target]').count(),targets.length);const cases=[];
  for(const t of targets){
   await p.locator(`[data-arena-target="${t.id}"]`).click();await settled();
   const expected=data.runs.filter(r=>r.target_id===t.id&&r.track==='same_evidence'&&r.condition==='clinical_only');
   assert.strictEqual(await p.locator('h1').innerText(),t.trial_name);assert.strictEqual(await p.locator('#public-trial').inputValue(),t.id);assert.strictEqual(new URL(p.url()).searchParams.get('target'),t.id);
   assert.strictEqual(await p.locator('#arena-fixtures [aria-pressed="true"]').getAttribute('data-arena-target'),t.id);
   assert.deepStrictEqual((await p.locator('.public-model-row').evaluateAll(es=>es.map(e=>e.dataset.runId))).sort(),expected.map(r=>r.id).sort());
   assert((await p.locator('.public-probability,.public-confidence').allTextContents()).every(x=>x.trim()==='N/A'));
   for(const direction of ['Success','Failure','Uncertain']){
    const count=expected.filter(r=>(['failed','timed_out','abstained','abstain','pending','withdrawn','unavailable'].includes(r.status)?'Uncertain':['Success','Failure','Uncertain'].includes(r.predicted_outcome)?r.predicted_outcome:'Uncertain')===direction).length;
    assert.strictEqual(Number(await p.locator(`.arena-position[data-direction="${direction}"]`).getAttribute('data-count')),count);
   }
   assert.strictEqual(await p.locator('[data-reveal-stage="pending"]').count(),1);assert.strictEqual(await p.locator('.public-match').count(),0);
   assert(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
   const bottom=await p.locator('.public-module').evaluateAll(es=>Math.max(...es.map(e=>e.getBoundingClientRect().bottom)));
   assert(bottom<=1000,'More than minimal desktop scroll: '+t.trial_name+' '+bottom);
   cases.push({id:t.id,trial:t.trial_name,model_rows:expected.length,core_bottom:bottom});
   if(t.trial_name==='MEVPRO-1')await p.screenshot({path:path.join(out,'mevpro-desktop.png'),fullPage:true});
  }
  const mevpro=targets.find(t=>t.trial_name==='MEVPRO-1');assert(mevpro);await p.locator(`[data-arena-target="${mevpro.id}"]`).click();await settled();await p.setViewportSize({width:375,height:812});
  await p.waitForFunction(()=>{const t=document.querySelector('.arena-schedule-track'),a=t?.querySelector('[aria-pressed="true"]');if(!t||!a)return false;const x=a.getBoundingClientRect(),y=t.getBoundingClientRect();return x.left>=y.left-1&&x.right<=y.right+1;});
  assert(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));assert(await p.locator('.public-model-id').evaluateAll(es=>es.every(e=>parseFloat(getComputedStyle(e).fontSize)>=13)));
  await p.screenshot({path:path.join(out,'mevpro-mobile.png'),fullPage:true});
  await p.setViewportSize({width:1440,height:900});await p.locator('[data-public-period="reported"]').click();await settled();
  assert((await p.locator('.public-record-banner').innerText()).includes('Historical replay'));assert.strictEqual(await p.locator('.public-match').count(),0);
  assert.strictEqual(await p.locator('[data-reveal-stage="resolved"]').count(),1);await p.screenshot({path:path.join(out,'reported-desktop.png'),fullPage:true});
  assert.deepStrictEqual(errors,[]);assert(requests.every(r=>r.method==='GET'));assert(requests.filter(r=>r.path.startsWith('/api/')).every(r=>r.path.startsWith('/api/arena/public')));
  const receipt={at:new Date().toISOString(),base,local_assets:local,ordinary_tls:true,formal_match_cases:cases,mobile_width:375,page_errors:errors,only_public_get:true,provider_calls:0,ledger_writes:0,scope:'Frontend and existing recorded-output correspondence; not clinical validation'};
  fs.writeFileSync(path.join(out,'receipt.json'),JSON.stringify(receipt,null,2));console.log(JSON.stringify({status:'passed',cases:cases.length,max_core_bottom:Math.max(...cases.map(c=>c.core_bottom)),local_assets:local,receipt:path.join(out,'receipt.json')}));await ctx.close();
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
