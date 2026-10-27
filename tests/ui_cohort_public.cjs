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
  await page.goto(base);await collectHostingHashes(page,new URL(base).origin,hostingHashes);assert.equal(await page.locator('html').getAttribute('lang'),'en');
  assert.equal(await page.locator('#resistance').count(),0);assert(await page.getByRole('link',{name:'Open Virtual Trial Cohort',exact:true}).isVisible());
  assert(await page.getByText('MNC customer value',{exact:true}).isVisible());
  await page.goto(base.replace(/\/$/,'')+'/cohort-lab/');await page.locator('#play:enabled').waitFor();await collectHostingHashes(page,new URL(base).origin,hostingHashes);
  assert.equal(await page.locator('html').getAttribute('lang'),'en');
  assert(!/[\u3400-\u9fff]/.test(await page.locator('body').innerText()));
  assert.equal(await page.locator('.patient-card').count(),24);
  assert.equal(await page.locator('#plots svg').count(),2);
  assert((await page.locator('#denominator').innerText()).includes('22 complete pairs'));
  assert((await page.locator('#summary').innerText()).includes('n = 22 / 24'));
  await page.locator('#play').click();await page.waitForFunction(()=>+document.querySelector('#day-value').textContent>=2);await page.locator('#play').click();
  assert.equal(await page.locator('#play').getAttribute('aria-pressed'),'false');
  const paused=await page.locator('#day-value').innerText();await page.waitForTimeout(800);assert.equal(await page.locator('#day-value').innerText(),paused);
  await page.locator('#day').focus();await page.locator('#day').press('End');
  assert.equal(await page.locator('#day-value').innerText(),'10');
  assert.equal(await page.locator('#phase').innerText(),'Extrapolated · unvalidated');
  const lastCard=await page.locator('[data-patient=P001]').innerText();
  await page.locator('#day').press('Home');assert.notEqual(await page.locator('[data-patient=P001]').innerText(),lastCard);
  await page.locator('[data-patient=P003]').click();assert.equal(await page.locator('#focus').inputValue(),'P003');
  assert((await page.locator('#evidence').innerText()).includes('missing pair'));
  await page.locator('[data-patient=P001]').focus();await page.locator('[data-patient=P001]').press('Enter');
  assert.equal(await page.evaluate(()=>document.activeElement.dataset.patient),'P001');
  await page.locator('#evidence summary').first().click();
  await page.locator('#day').fill('2');
  assert.equal(await page.locator('#evidence details[open]').count(),1);
  await page.locator('#play').click();await page.waitForFunction(()=>+document.querySelector('#day-value').textContent>=3);await page.locator('#play').click();
  assert.equal(await page.locator('#evidence details[open]').count(),1);
  await page.locator('#population').selectOption('available');assert.equal(await page.locator('#evidence details[open]').count(),1);assert((await page.locator('#summary').innerText()).includes('n = 23 / 24'));
  await page.locator('#cutoff').selectOption('3');assert.equal(await page.locator('#day').getAttribute('max'),'6');
  await page.locator('#day').focus();await page.locator('#day').press('End');
  const dl=page.waitForEvent('download');await page.locator('#export').click();const exported=JSON.parse(fs.readFileSync(await (await dl).path(),'utf8'));
  assert.equal(exported.view.assay_day,6);assert.equal(exported.result.cutoff_day,3);assert.equal(exported.result.total_patients,24);
  assert.equal(exported.result.clinical_outcomes.os,null);
  assert(exported.result.patients.every(r=>Object.values(r.patient.assays).every(a=>a.rows.every(p=>p.day<=3))));
  const csvDownload=page.waitForEvent('download');await page.locator('#export-csv').click();
  const downloadedCsv=fs.readFileSync(await (await csvDownload).path(),'utf8');
  const verification=fs.mkdtempSync(path.join(os.tmpdir(),'cohort-export-'));
  try{
   fs.writeFileSync(path.join(verification,'export.json'),JSON.stringify(exported));
   const root=path.resolve(__dirname,'..'),python=process.env.VBT_PYTHON||path.join(root,'.venv','bin','python');
   const checked=spawnSync(python,['-m','vbt.patient_exports',path.join(verification,'export.json'),'--csv',path.join(verification,'expected.csv')],{cwd:root,encoding:'utf8',env:{PATH:process.env.PATH,PYTHONPATH:path.join(root,'src')}});
   assert.equal(checked.status,0,checked.stderr);
   assert.equal(JSON.parse(checked.stdout).record_integrity,'verified');
   assert.equal(downloadedCsv,fs.readFileSync(path.join(verification,'expected.csv'),'utf8'));
   exported.result.total_patients=999;fs.writeFileSync(path.join(verification,'tampered.json'),JSON.stringify(exported));
   const rejected=spawnSync(python,['-m','vbt.patient_exports',path.join(verification,'tampered.json')],{cwd:root,env:{PATH:process.env.PATH,PYTHONPATH:path.join(root,'src')}});assert.notEqual(rejected.status,0);
  }finally{fs.rmSync(verification,{recursive:true,force:true});}

  await page.locator('#cutoff').selectOption('7');await page.locator('#population').selectOption('paired');await page.locator('#focus').selectOption('P002');
  await page.locator('#day').focus();await page.locator('#day').press('End');
  await page.locator('#play').click();await page.waitForFunction(()=>document.querySelector('#day-value').textContent==='1');await page.locator('#play').click();
  const out=process.env.PATIENT_LAB_QA_DIR;if(out){fs.mkdirSync(out,{recursive:true});await page.screenshot({path:path.join(out,'cohort-desktop.png'),fullPage:true});}
  for(const width of [390,320]){await page.setViewportSize({width,height:1100});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));if(out&&width===390){await page.locator('#summary').scrollIntoViewIfNeeded();await page.screenshot({path:path.join(out,'cohort-mobile.png')});}}
  await page.route('**/cohort-lab/scenarios.json',async route=>{const response=await route.fetch();const bundle=await response.json();bundle.templates['7'].sha256='0'.repeat(64);await route.fulfill({json:bundle});});
  await page.reload();await page.locator('#error').waitFor();assert(await page.locator('#play').isDisabled());assert(await page.locator('#export').isDisabled());
  await collectHostingHashes(page,new URL(base).origin,hostingHashes);
  const {applicationErrors,hostingWarnings}=classifyConsole(consoleMessages,hostingHashes);
  assert.deepEqual(errors,[]);assert.deepEqual(applicationErrors,[]);
  const receipt={checks:['keyboard focus retained','evidence stays open during playback','24 patient cards','two simultaneous molecule panels','same paired denominators','available case denominators','play/pause/restart','synchronized timeline and patient values','missing pair and highlight','cutoff-limited export','offline verifier accepts real browser export and rejects tampering','download CSV equals independently reproduced CSV','English only','desktop/390px/320px','corrupt template fails closed','unknown console/script/resource failures remain fatal'],errors,application_errors:applicationErrors,hosting_warnings:hostingWarnings};
  if(out)fs.writeFileSync(path.join(out,'cohort-browser.json'),JSON.stringify(receipt,null,2));console.log(JSON.stringify(receipt));await context.close();
 }finally{await browser.close();if(service){service.kill('SIGTERM');await new Promise(resolve=>service.exitCode!==null||service.signalCode?resolve():service.once('exit',resolve));}if(temporary)fs.rmSync(temporary,{recursive:true,force:true});}
})().catch(e=>{console.error(e);process.exit(1)});
