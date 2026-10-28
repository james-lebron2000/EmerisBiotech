'use strict';
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const {collectHostingHashes,classifyConsole}=require('./hosting_console.cjs');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const {spawn,spawnSync}=require('node:child_process');
(async()=>{
 let base=process.env.COHORT_PUBLIC_URL,service,temporary;
 if(!base){
  const root=path.resolve(__dirname,'..'),python=process.env.VBT_PYTHON||path.join(root,'.venv','bin','python');
  temporary=fs.mkdtempSync(path.join(os.tmpdir(),'patient-public-'));
  const env={PATH:process.env.PATH,PYTHONPATH:path.join(root,'src'),PYTHONUNBUFFERED:'1'};
  const built=spawnSync(python,['scripts/build_patient_site.py','--out',temporary],{cwd:root,env,encoding:'utf8'});
  if(built.status!==0)throw Error('Public build failed: '+built.stderr);
  const code="from http.server import ThreadingHTTPServer,SimpleHTTPRequestHandler\nfrom functools import partial\nimport sys\nwith ThreadingHTTPServer(('127.0.0.1',0),partial(SimpleHTTPRequestHandler,directory=sys.argv[1])) as server:\n print('http://127.0.0.1:'+str(server.server_port),flush=True)\n server.serve_forever()";
  service=spawn(python,['-u','-c',code,temporary],{env,stdio:['ignore','pipe','pipe']});
  let output='',logs='';service.stderr.on('data',d=>logs+=d);
  base=await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('Preview startup timed out: '+logs)),60000);service.on('error',reject);service.on('exit',()=>{clearTimeout(timer);reject(Error('Preview exited: '+logs));});service.stdout.on('data',d=>{output+=d;const m=output.match(/http:\/\/127\.0\.0\.1:\d+/);if(m){clearTimeout(timer);resolve(m[0]);}});});
 }


 const browser=await chromium.launch({headless:true,...(process.env.CHROMIUM_EXECUTABLE?{executablePath:process.env.CHROMIUM_EXECUTABLE}:{})});
 try{
  const context=await browser.newContext({viewport:{width:1440,height:1100}}),page=await context.newPage(),errors=[];
  const consoleMessages=[],hostingHashes=new Set();
  page.on('pageerror',e=>errors.push(e.message));
  page.on('console',m=>{if(m.type()==='error')consoleMessages.push(m.text());});

  const out=process.env.PATIENT_LAB_QA_DIR;if(out)fs.mkdirSync(out,{recursive:true});
  const url=base.replace(/\/$/,'')+'/cohort-lab/';
  await page.goto(url+'#simulation');await page.locator('#play:enabled').waitFor({state:'attached'});await page.locator('[data-scene=immune]:enabled').waitFor();
  await collectHostingHashes(page,new URL(base).origin,hostingHashes);
  await page.waitForFunction(()=>+document.querySelector('#week').value>0);await page.locator('#play').click();
  assert.equal(await page.locator('#atlas-chemo .atlas-cell').count(),120);
  assert.equal(await page.locator('#atlas-chemo .atlas-cell path').count(),240);
  const fit=async selector=>page.locator(selector).evaluateAll(ns=>({visible:ns.filter(n=>{const b=n.getBoundingClientRect();return b.top>=0&&b.bottom<=innerHeight&&b.left>=0&&b.right<=innerWidth}).length,total:ns.length,overflow:document.documentElement.scrollWidth>innerWidth+1}));
  for(const size of [{width:1440,height:900},{width:1366,height:768}]){await page.setViewportSize(size);await page.locator('#simulation').evaluate(e=>e.scrollIntoView({behavior:'instant',block:'start'}));assert.deepEqual(await fit('#atlas-chemo .atlas-cell'),{visible:120,total:120,overflow:false});}
  await page.setViewportSize({width:1440,height:900});await page.locator('#week').fill('24');
  assert((await page.locator('#atlas-chemo .atlas-paired').innerText()).includes('A 49 · B 57'));
  await page.locator('#atlas-chemo [data-atlas-patient=P120]').click();assert((await page.locator('#detail-content').innerText()).includes('P120'));await page.keyboard.press('Escape');
  await page.locator('[data-scene=immune]').click();assert(await page.locator('#chemo-scene').isHidden());assert(await page.locator('#immune-scene').isVisible());
  await page.waitForFunction(()=>+document.querySelector('#immune-week').value>0);await page.locator('#immune-play').click();
  assert.equal(await page.locator('#play').getAttribute('aria-pressed'),'false');
  assert.equal(await page.locator('#atlas-immune .atlas-cell').count(),120);assert.equal(await page.locator('#atlas-immune .atlas-cell path').count(),240);
  assert((await page.locator('#immune-eligibility').innerText()).includes('US FDA'));assert((await page.locator('#immune-pathways').innerText()).includes('Pembrolizumab'));
  const e=await page.evaluate(async()=>await (await fetch('immune-scenarios.json')).json()),d=JSON.parse(e.canonical_result);
  await page.locator('#immune-week').fill('24.375');
  const p120=d.patients[119],sample=(r,k)=>{const ps=r.pathways[k].trajectory;return ps[24].burden_index+(ps[25].burden_index-ps[24].burden_index)*.375;};
  const a=sample(p120,'A'),b=sample(p120,'B');const tip=await page.locator('#atlas-immune [data-atlas-patient=I120]').getAttribute('title');assert(tip.includes('A '+(a>=100?'+':'')+(a-100).toFixed(0)+'%'));assert(tip.includes('B '+(b>=100?'+':'')+(b-100).toFixed(0)+'%'));
  await page.locator('#immune-play').click();const frames=await page.evaluate(()=>new Promise(resolve=>{const xs=[];window.originalAtlasTile=document.querySelector('#atlas-immune .atlas-cell');function capture(){xs.push({w:+document.querySelector('#immune-week').value,clip:+document.querySelector('#atlas-immune .atlas-cell rect').getAttribute('width')});if(xs.length<4)requestAnimationFrame(capture);else resolve(xs);}requestAnimationFrame(capture);}));
  assert(frames.at(-1).w>frames[0].w&&frames.at(-1).w-frames[0].w<1);for(const f of frames)assert(Math.abs(f.clip-f.w/72*100)<.005);assert(await page.evaluate(()=>window.originalAtlasTile===document.querySelector('#atlas-immune .atlas-cell')));
  await page.locator('#immune-play').click();const paused=await page.locator('#immune-week').inputValue();await page.waitForTimeout(150);assert.equal(await page.locator('#immune-week').inputValue(),paused);
  await page.locator('#immune-week').fill('24');
  const gaps=d.patients.map(r=>r.pathways.A.trajectory[24].burden_index-r.pathways.B.trajectory[24].burden_index);
  const paired=await page.locator('#atlas-immune .atlas-paired').innerText();assert(paired.includes('A '+gaps.filter(v=>v < -5).length+' · B '+gaps.filter(v=>v > 5).length));assert((await page.locator('#immune-status').innerText()).includes('A: index <95 without rebound '+d.patients.filter(r=>r.pathways.A.trajectory[24].display_status==='below_baseline').length));
  for(const size of [{width:1440,height:900},{width:1366,height:768}]){await page.setViewportSize(size);await page.locator('#simulation').evaluate(e=>e.scrollIntoView({behavior:'instant',block:'start'}));assert.deepEqual(await fit('#atlas-immune .atlas-cell'),{visible:120,total:120,overflow:false});}
  await page.setViewportSize({width:1440,height:900});await page.locator('#simulation').evaluate(e=>e.scrollIntoView({behavior:'instant',block:'start'}));if(out)await page.screenshot({path:path.join(out,'immune-desktop.png')});
  await page.locator('#atlas-immune [data-atlas-patient=I120]').click();assert((await page.locator('#atlas-dialog').innerText()).includes('I120'));assert.equal(await page.locator('#atlas-dialog svg').count(),2);assert((await page.locator('#atlas-dialog').innerText()).includes('scenario_coefficients'));await page.keyboard.press('Escape');
  await page.locator('#immune-pattern').selectOption('delayed');assert.equal(await page.locator('#atlas-immune .atlas-cell').count(),30);assert((await page.locator('#atlas-immune').innerText()).includes('n = 30'));assert.deepEqual(await page.locator('#atlas-immune .atlas-cell').evaluateAll(ns=>ns.map(n=>n.dataset.atlasPatient)),d.patients.filter(r=>r.patient.input_pattern==='delayed').map(r=>r.patient.patient_id));
  const dl=page.waitForEvent('download');await page.locator('#immune-export').click();const record=JSON.parse(fs.readFileSync(await(await dl).path(),'utf8'));assert.equal(record.result.total_patients,120);assert.equal(record.view.pattern,'delayed');assert.equal(record.view.highlight_patient,'I120');
  const csv=page.waitForEvent('download');await page.locator('#immune-csv').click();const csvText=fs.readFileSync(await(await csv).path(),'utf8');assert.equal(csvText.trim().split('\n').length,17521);
  const tmp=fs.mkdtempSync(path.join(os.tmpdir(),'immune-export-'));try{fs.writeFileSync(path.join(tmp,'record.json'),JSON.stringify(record));const root=path.resolve(__dirname,'..'),python=process.env.VBT_PYTHON||path.join(root,'.venv/bin/python');const checked=spawnSync(python,['-m','vbt.patient_exports',path.join(tmp,'record.json'),'--csv',path.join(tmp,'expected.csv')],{cwd:root,env:{PATH:process.env.PATH,PYTHONPATH:path.join(root,'src')},encoding:'utf8'});assert.equal(checked.status,0,checked.stderr);assert.equal(csvText,fs.readFileSync(path.join(tmp,'expected.csv'),'utf8'));}finally{fs.rmSync(tmp,{recursive:true,force:true});}
  await page.locator('#methods').click();assert((await page.locator('#atlas-dialog').innerText()).includes('No head-to-head'));await page.keyboard.press('Escape');
  await page.locator('#immune-methods').click();assert((await page.locator('#atlas-dialog').innerText()).includes('No head-to-head'));assert.equal(await page.locator('#atlas-dialog a').count(),2);await page.keyboard.press('Escape');
  await page.locator('#immune-pattern').selectOption('all');await page.locator('#immune-replay').click();assert(+await page.locator('#immune-week').inputValue()<2);await page.locator('#immune-play').click();
  for(const width of [390,320]){await page.setViewportSize({width,height:900});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));assert.equal(await page.locator('#atlas-immune .atlas-cell').count(),120);if(out&&width===390){await page.locator('#simulation').evaluate(e=>e.scrollIntoView({behavior:'instant',block:'start'}));await page.screenshot({path:path.join(out,'immune-mobile.png')});}}
  await page.setViewportSize({width:1440,height:900});await page.goto(url+'zh.html?scene=immune#simulation');await page.locator('[data-scene=immune]:enabled').waitFor();assert(await page.locator('#immune-scene').isVisible());await page.waitForFunction(()=>+document.querySelector('#immune-week').value>0);await page.locator('#immune-play').click();await collectHostingHashes(page,new URL(base).origin,hostingHashes);assert((await page.locator('#immune-eligibility').innerText()).includes('符合范围'));assert.equal(await page.locator('#atlas-immune .atlas-cell').count(),120);if(out)await page.screenshot({path:path.join(out,'immune-zh.png')});
  const reducedContext=await browser.newContext({reducedMotion:'reduce'}),rp=await reducedContext.newPage();await rp.goto(url+'#simulation');await rp.locator('[data-scene=immune]:enabled').waitFor();await rp.locator('[data-scene=immune]').click();assert.equal(await rp.locator('#immune-week').inputValue(),'0');assert.equal(await rp.locator('#immune-play').getAttribute('aria-pressed'),'false');await reducedContext.close();
  await page.route('**/immune-scenarios.json',async route=>{const res=await route.fetch(),b=await res.json();b.sha256='0'.repeat(64);await route.fulfill({json:b});});await page.reload();await page.locator('[data-scene=immune]:enabled').waitFor();await page.locator('[data-scene=immune]').click();assert(await page.locator('#immune-error').isVisible());assert(await page.locator('#immune-play').isDisabled());assert(await page.locator('#immune-export').isDisabled());assert(await page.locator('#immune-csv').isHidden());
  await collectHostingHashes(page,new URL(base).origin,hostingHashes);const {applicationErrors,hostingWarnings}=classifyConsole(consoleMessages,hostingHashes);assert.deepEqual(errors,[]);assert.deepEqual(applicationErrors,[]);
  const receipt={checks:['120 patients / 240 curves in each scene','all 120 visible at 1440x900 and 1366x768','separate MSI-H/dMMR population and eligibility','autoplay / continuous fractional drawing / stable DOM / pause / replay','chemotherapy stops on scene switch','independent paired counts and patient values','I120 detail includes inputs and both curves','input filter uses all 30 matching patients','exports retain all 120 and filter metadata','17520-row CSV independently reproduced','English and Chinese scene controls','390px and 320px no overflow','reduced-motion paused','corrupt immune bundle fails closed'],errors,application_errors:applicationErrors,hosting_warnings:hostingWarnings};if(out)fs.writeFileSync(path.join(out,'immune-browser.json'),JSON.stringify(receipt,null,2));console.log(JSON.stringify(receipt));await context.close();
 }finally{await browser.close();if(service)service.kill();if(temporary)fs.rmSync(temporary,{recursive:true,force:true});}
})().catch(e=>{console.error(e);process.exitCode=1;});
