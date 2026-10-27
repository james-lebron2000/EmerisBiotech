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
  await page.goto(base);await collectHostingHashes(page,new URL(base).origin,hostingHashes);assert(await page.getByRole('link',{name:'Open Virtual Trial Cohort',exact:true}).isVisible());
  await page.setViewportSize({width:1440,height:900});
  await page.goto(base.replace(/\/$/,'')+'/cohort-lab/');await page.locator('#play:enabled').waitFor();await collectHostingHashes(page,new URL(base).origin,hostingHashes);
  assert.equal(await page.locator('html').getAttribute('lang'),'en');assert(!/[\u3400-\u9fff]/.test(await page.locator('main').innerText()));
  assert.equal(await page.locator('.cohort-median').count(),2);assert.equal(await page.locator('.cohort-band').count(),2);assert((await page.locator('.cohort-overview').innerText()).includes('120'));
  assert.equal(await page.locator('.patient').count(),20);assert.equal(await page.locator('.sparkline').count(),40);
  assert.equal(await page.locator('.story-section').count(),3);
  assert.deepEqual(await page.locator('.patient').evaluateAll(nodes=>nodes.map(n=>n.dataset.patient)),Array.from({length:20},(_,i)=>'P'+String(i+1).padStart(3,'0')));
  assert.equal(await page.locator('.line-badge,.overline,.section-index,.boundary,.stage-note').count(),0);
  const assertCopy=async selector=>assert(!/not represent|not.*validated|still require|fictional|uncalibrated|不代表|仍需|尚未临床验证|未校准|样例|示例|并非/i.test(await page.locator(selector).innerText()));
  await assertCopy('main');
  await page.locator('#simulation').evaluate(e=>e.scrollIntoView({behavior:'instant',block:'start'}));
  await page.waitForFunction(()=>document.querySelector('#play').getAttribute('aria-pressed')==='true');
  assert.equal(await page.locator('#play').getAttribute('aria-pressed'),'true');await page.waitForFunction(()=>+document.querySelector('#week').value>0);
  await page.locator('#play').click();const paused=await page.locator('#week').inputValue();await page.waitForTimeout(200);assert.equal(await page.locator('#week').inputValue(),paused);
  const fit=async()=>page.locator('.patient').evaluateAll(nodes=>({visible:nodes.filter(n=>{const b=n.getBoundingClientRect();return b.top>=0&&b.bottom<=innerHeight&&b.left>=0&&b.right<=innerWidth}).length,total:nodes.length,overflow:document.documentElement.scrollWidth>innerWidth+1}));
  assert.deepEqual(await fit(),{visible:20,total:20,overflow:false});
  await page.setViewportSize({width:1366,height:768});await page.locator('#simulation').evaluate(e=>e.scrollIntoView({behavior:'instant',block:'start'}));assert.deepEqual(await fit(),{visible:20,total:20,overflow:false});
  await page.setViewportSize({width:1440,height:900});await page.locator('#simulation').evaluate(e=>e.scrollIntoView({behavior:'instant',block:'start'}));await page.locator('#week').fill('12.1');
  await page.evaluate(()=>window.firstTile=document.querySelector('.patient'));await page.locator('#play').click();
  const samples=await page.evaluate(()=>new Promise(resolve=>{const values=[];function capture(){values.push({time:+document.querySelector('#week').value,clip:+document.querySelector('.patient svg rect').getAttribute('width')});if(values.length<3)requestAnimationFrame(capture);else resolve(values);}requestAnimationFrame(capture);}));
  assert(samples[1].time>samples[0].time&&samples[1].time-samples[0].time<1,'continuous sub-week frame advance');for(const sample of samples)assert(Math.abs(sample.clip-sample.time/72*100)<.005,JSON.stringify(sample));
  assert(await page.evaluate(()=>window.firstTile===document.querySelector('.patient')));await page.locator('#play').click();
  const snapshot=await page.evaluate(()=>({time:+document.querySelector('#week').value,clip:+document.querySelector('.patient svg rect').getAttribute('width')}));assert(Math.abs(snapshot.clip-snapshot.time/72*100)<.005,JSON.stringify(snapshot));
  await page.locator('#week').fill('0');assert((await page.locator('#counts-A').innerText()).includes('1L 120'));
  await page.locator('#week').fill('48');assert.equal(await page.locator('#cohort-median-A').innerText(),'-32%');assert.equal(await page.locator('#cohort-median-B').innerText(),'-33%');
  await page.locator('#week').fill('72');assert((await page.locator('#counts-A').innerText()).includes('3L 26'));assert((await page.locator('#counts-B').innerText()).includes('3L 26'));
  await page.locator('[data-patient=P010]').click();assert(await page.locator('#details').isVisible());assert((await page.locator('#detail-content').innerText()).includes('P010'));assert((await page.locator('#detail-content').innerText()).includes('Patient inputs'));assert.equal(await page.locator('.detail-chart').count(),2);await page.keyboard.press('Escape');
  await page.locator('[data-patient=P020]').click();assert((await page.locator('#detail-content').innerText()).includes('P020'));await assertCopy('#detail-content');await page.keyboard.press('Escape');
  await page.locator('[data-patient=P010]').click();await page.keyboard.press('Escape');
  await page.locator('#week').fill('48.375');
  await page.locator('#order').selectOption('difference');
  const download=page.waitForEvent('download');await page.locator('#export').click();const exported=JSON.parse(fs.readFileSync(await (await download).path(),'utf8'));
  assert.equal(exported.view.order,'id','unapplied order selection must not alter exported view');assert.equal(exported.view.week,48.375);assert.equal(exported.view.highlight_patient,'P010');assert.equal(exported.result.total_patients,120);assert.equal(exported.result.clinical_outcomes.os,null);
  const csvDownload=page.waitForEvent('download');await page.locator('#export-csv').click();const downloadedCsv=fs.readFileSync(await (await csvDownload).path(),'utf8');
  const verification=fs.mkdtempSync(path.join(os.tmpdir(),'regimen-export-'));
  try{fs.writeFileSync(path.join(verification,'export.json'),JSON.stringify(exported));const root=path.resolve(__dirname,'..'),python=process.env.VBT_PYTHON||path.join(root,'.venv','bin','python'),env={PATH:process.env.PATH,PYTHONPATH:path.join(root,'src')};
   const checked=spawnSync(python,['-m','vbt.patient_exports',path.join(verification,'export.json'),'--csv',path.join(verification,'expected.csv')],{cwd:root,encoding:'utf8',env});assert.equal(checked.status,0,checked.stderr);assert.equal(JSON.parse(checked.stdout).record_integrity,'verified');assert.equal(downloadedCsv,fs.readFileSync(path.join(verification,'expected.csv'),'utf8'));
   exported.result.patients[0].pathways.A.trajectory[3].burden_index=999;fs.writeFileSync(path.join(verification,'tampered.json'),JSON.stringify(exported));assert.notEqual(spawnSync(python,['-m','vbt.patient_exports',path.join(verification,'tampered.json')],{cwd:root,env}).status,0);
  }finally{fs.rmSync(verification,{recursive:true,force:true});}
  await page.locator('#order').selectOption('difference');const original=await page.locator('.patient').first().getAttribute('data-patient');await page.locator('#reorder').click();assert.notEqual(await page.locator('.patient').first().getAttribute('data-patient'),original);
  await page.locator('#methods').click();assert(await page.locator('#method-dialog').isVisible());assert((await page.locator('#method-content').innerText()).includes('FDA'));assert((await page.locator('#method-content').innerText()).includes('Every 8 weeks'));await assertCopy('#method-content');await page.locator('#method-dialog [data-close]').click();
  const out=process.env.PATIENT_LAB_QA_DIR;if(out){fs.mkdirSync(out,{recursive:true});await page.screenshot({path:path.join(out,'cohort-desktop.png')});}
  await page.locator('#replay').click();assert.equal(await page.locator('#play').getAttribute('aria-pressed'),'true');assert(+await page.locator('#week').inputValue()<2);await page.locator('#play').click();
  for(const width of [390,320]){await page.setViewportSize({width,height:900});assert.equal(await page.locator('.patient').count(),20);assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));if(out&&width===390)await page.screenshot({path:path.join(out,'cohort-mobile.png')});}
  const reduced=await browser.newContext({reducedMotion:'reduce'}),rp=await reduced.newPage();await rp.goto(base.replace(/\/$/,'')+'/cohort-lab/');await rp.locator('#play:enabled').waitFor();assert.equal(await rp.locator('#week').inputValue(),'0');assert.equal(await rp.locator('#play').getAttribute('aria-pressed'),'false');await rp.locator('#play').click();await rp.waitForFunction(()=>+document.querySelector('#week').value>0);await reduced.close();
  await page.setViewportSize({width:1440,height:900});await page.goto(base.replace(/\/$/,'')+'/cohort-lab/zh.html');await page.locator('#play:enabled').waitFor();await collectHostingHashes(page,new URL(base).origin,hostingHashes);
  assert.equal(await page.locator('html').getAttribute('lang'),'zh-CN');assert((await page.locator('#company').innerText()).includes('生涌科技'));await assertCopy('main');assert.equal(await page.locator('#play').getAttribute('aria-pressed'),'false');assert.equal(await page.locator('#week').inputValue(),'0');
  const proportions=await page.locator('.story-section').evaluateAll(nodes=>nodes.map(n=>n.getBoundingClientRect().height));assert(Math.max(...proportions)/Math.min(...proportions)<1.25,JSON.stringify(proportions));
  if(out)await page.screenshot({path:path.join(out,'zh-company.png')});
  await page.locator('#product').evaluate(e=>e.scrollIntoView({behavior:'instant',block:'start'}));if(out)await page.screenshot({path:path.join(out,'zh-product.png')});
  await page.locator('#simulation').evaluate(e=>e.scrollIntoView({behavior:'instant',block:'start'}));await page.waitForFunction(()=>document.querySelector('#play').getAttribute('aria-pressed')==='true');await page.locator('#play').click();await page.locator('#week').fill('48');assert.deepEqual(await fit(),{visible:20,total:20,overflow:false});
  assert((await page.locator('#pathways').innerText()).includes('贝伐珠单抗'));assert((await page.locator('#counts-A').innerText()).includes('2线'));if(out)await page.screenshot({path:path.join(out,'zh-simulation.png')});
  await page.locator('[data-patient=P010]').click();assert((await page.locator('#detail-content').innerText()).includes('患者输入与模型参数'));await page.keyboard.press('Escape');
  await page.locator('#methods').click();assert((await page.locator('#method-content').innerText()).includes('氟尿嘧啶'));assert((await page.locator('#method-content').innerText()).includes('每 8 周'));await assertCopy('#method-content');
  assert((await page.locator('#method-content a[href*="fda.gov"]').getAttribute('href')).includes('trifluridine-and-tipiracil-bevacizumab'));await page.locator('#method-dialog [data-close]').click();
  const zhDownload=page.waitForEvent('download');await page.locator('#export').click();const zhExport=JSON.parse(fs.readFileSync(await(await zhDownload).path(),'utf8'));assert.equal(zhExport.canonical_template,exported.canonical_template);assert.equal(zhExport.template_sha256,exported.template_sha256);
  for(const width of [390,320]){await page.setViewportSize({width,height:900});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));if(out&&width===390){await page.locator('#company').evaluate(e=>e.scrollIntoView({behavior:'instant',block:'start'}));await page.screenshot({path:path.join(out,'zh-mobile.png')});}}
  await page.locator('.language').click();await page.locator('#play:enabled').waitFor();assert.equal(await page.locator('html').getAttribute('lang'),'en');await collectHostingHashes(page,new URL(base).origin,hostingHashes);
  await page.route('**/cohort-lab/scenarios.json',async route=>{const response=await route.fetch(),value=await response.json();value.sha256='0'.repeat(64);await route.fulfill({json:value});});await page.reload();await page.locator('#error').waitFor();assert(await page.locator('#play').isDisabled());assert(await page.locator('#export').isDisabled());assert(await page.locator('#export-csv').isHidden());await collectHostingHashes(page,new URL(base).origin,hostingHashes);
  await page.unroute('**/cohort-lab/scenarios.json');
  await page.route('**/cohort-lab/overview.json',async route=>{const response=await route.fetch(),envelope=await response.json(),overview=JSON.parse(envelope.canonical_overview);overview.source_result_sha256='0'.repeat(64);envelope.canonical_overview=JSON.stringify(overview);envelope.sha256=require('node:crypto').createHash('sha256').update(envelope.canonical_overview).digest('hex');await route.fulfill({json:envelope});});
  await page.reload();await page.locator('#error').waitFor();assert((await page.locator('#error').innerText()).includes('mismatch'));assert(await page.locator('#play').isDisabled());assert(await page.locator('#export').isDisabled());await collectHostingHashes(page,new URL(base).origin,hostingHashes);
  const {applicationErrors,hostingWarnings}=classifyConsole(consoleMessages,hostingHashes);assert.deepEqual(errors,[]);assert.deepEqual(applicationErrors,[]);
  const receipt={checks:['20 fixed individual patients from the 120-patient fixture','all-120 cohort median and IQR curves','cohort overview animates with individual responses','three balanced company/product/simulation sections','Chinese and English language switch','Chinese controls / clinical catalogue / details / methods','localization preserves canonical exports and external source links','simulation autoplay on first entry, not while reading introduction','20 patients / 40 paired individual trajectories','all 20 patients plus cohort overview visible on desktop','autoplay / continuous sub-week time / stable DOM','pause / replay / fractional scrub','model line counts and source-backed regimen catalogue','patient detail and treatment history','source methods dialog','explicit stable ordering','real browser JSON verified independently','17520-row CSV equals Python reproduction','tampering rejected','reduced-motion starts paused','English content with language switch','390px and 320px responsive','corrupt bundle and valid-hash overview from a different cohort fail closed'],errors,application_errors:applicationErrors,hosting_warnings:hostingWarnings};if(out)fs.writeFileSync(path.join(out,'cohort-browser.json'),JSON.stringify(receipt,null,2));console.log(JSON.stringify(receipt));await context.close();
 }finally{await browser.close();if(service){service.kill('SIGTERM');await new Promise(resolve=>service.exitCode!==null||service.signalCode?resolve():service.once('exit',resolve));}if(temporary)fs.rmSync(temporary,{recursive:true,force:true});}
})().catch(e=>{console.error(e);process.exit(1)});
