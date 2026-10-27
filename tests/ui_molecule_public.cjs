'use strict';
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const {spawn,spawnSync}=require('node:child_process');
(async()=>{
 let base=process.env.MOLECULE_PUBLIC_URL,service,temporary;
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
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base.replace(/\/$/,'')+'/molecule-lab/');
  await page.locator('#patient:enabled').waitFor();
  assert.equal(await page.locator('html').getAttribute('lang'),'en');
  assert(!/[\u3400-\u9fff]/.test(await page.locator('body').innerText()));
  assert.equal(await page.locator('#resistance').count(),0);
  assert(await page.getByText('29.0%',{exact:true}).first().isVisible());
  await page.locator('#save').click();
  const original=await page.evaluate(()=>JSON.parse(localStorage.getItem('emeris-patient-molecule-v1'))[0]);
  await page.locator('#patient').selectOption('P002');
  assert(await page.getByText('85.0%',{exact:true}).first().isVisible());
  await page.locator('#molecule').selectOption('oxaliplatin');
  assert(await page.getByText('20.0%',{exact:true}).first().isVisible());
  await page.locator('#save').click();
  await page.locator('#patient').selectOption('P003');
  assert(await page.getByText('Not estimable',{exact:true}).isVisible());
  assert(await page.locator('#save').isDisabled());
  assert(await page.getByText('No paired assay for this patient and molecule.',{exact:true}).isVisible());
  await page.locator('#patient').selectOption('P001');
  await page.locator('#molecule').selectOption('fluorouracil');
  await page.locator('#cutoff').selectOption('3');
  assert(await page.getByText('56.0%',{exact:true}).first().isVisible());
  await page.locator('#save').click();
  const dl=page.waitForEvent('download');await page.locator('#export').click();
  const exported=JSON.parse(fs.readFileSync(await (await dl).path(),'utf8'));
  assert.equal(exported.result.patient.patient_id,'P001');
  assert.equal(exported.result.molecule_id,'fluorouracil');
  assert.equal(exported.result.cutoff_day,3);
  assert.equal(exported.result.response.resistant_cell_fraction,null);
  assert.equal(exported.result.clinical_outcomes.os,null);
  assert(exported.result.response.assay.rows.every(r=>r.day<=3));
  await page.reload();await page.locator('#patient:enabled').waitFor();
  assert.equal(await page.locator('#history button').count(),3);
  assert.deepEqual(await page.evaluate(()=>JSON.parse(localStorage.getItem('emeris-patient-molecule-v1'))[0]),original);
  await page.locator(`[data-run="${original.run_id}"]`).click();
  assert(await page.getByText('29.0%',{exact:true}).first().isVisible());
  const out=process.env.PATIENT_LAB_QA_DIR;if(out){fs.mkdirSync(out,{recursive:true});await page.screenshot({path:path.join(out,'molecule-desktop.png'),fullPage:true});}
  for(const width of [390,320]){await page.setViewportSize({width,height:1100});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));if(out&&width===390){await page.screenshot({path:path.join(out,'molecule-mobile.png')});await page.locator('#state').scrollIntoViewIfNeeded();await page.screenshot({path:path.join(out,'molecule-mobile-response.png')});}}
  const second=await browser.newContext(),p2=await second.newPage();await p2.goto(base.replace(/\/$/,'')+'/molecule-lab/');await p2.locator('#patient:enabled').waitFor();assert(await p2.getByText('No saved runs in this browser.',{exact:true}).isVisible());await second.close();
  assert.deepEqual(errors,[]);
  // Corrupted source assets must never unlock controls or offer a fallback result.
  await page.route('**/molecule-lab/scenarios.json',async route=>{const response=await route.fetch();const bundle=await response.json();bundle.templates.P001.fluorouracil['7'].sha256='0'.repeat(64);await route.fulfill({json:bundle});});
  await page.reload();await page.locator('#error').waitFor();assert(await page.locator('#patient').isDisabled());assert(await page.locator('#save').isDisabled());
  const receipt={checks:['English only','no manual resistance','patient and molecule paired response','missing pair abstention','cutoff excludes future data','immutable browser runs','JSON export','visitor isolation','desktop/390px/320px','corrupt templates fail closed'],errors};
  if(out)fs.writeFileSync(path.join(out,'molecule-browser.json'),JSON.stringify(receipt,null,2));console.log(JSON.stringify(receipt));await context.close();
 }finally{await browser.close();if(service){service.kill('SIGTERM');await new Promise(resolve=>service.exitCode!==null||service.signalCode?resolve():service.once('exit',resolve));}if(temporary)fs.rmSync(temporary,{recursive:true,force:true});}
})().catch(e=>{console.error(e);process.exit(1)});
