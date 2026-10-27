'use strict';
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const {spawn,spawnSync}=require('node:child_process');
(async()=>{
 let base=process.env.PATIENT_PUBLIC_URL,service,temporary;
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
 base=base.replace(/\/$/,'')+'/scenario-archive/';
 const browser=await chromium.launch({headless:true,...(process.env.CHROMIUM_EXECUTABLE?{executablePath:process.env.CHROMIUM_EXECUTABLE}:{})});
 try{
  const context=await browser.newContext({viewport:{width:1440,height:1100}}),page=await context.newPage(),errors=[],external=[];
  page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(!r.url().startsWith(new URL(base).origin))external.push(r.url());});
  await page.goto(base);await page.locator('#run:enabled').waitFor();assert.equal(await page.locator('html').getAttribute('lang'),'en');assert(!/[\u3400-\u9fff]/.test(await page.locator('body').innerText()));
  assert(await page.getByText('Preview · not saved',{exact:true}).isVisible());
  await page.locator('#run').click();await page.getByText('Baseline saved in this browser.',{exact:false}).waitFor();
  const original=await page.evaluate(()=>JSON.parse(localStorage.getItem('emeris-patient-lab-v2-en'))[0]);
  await page.locator('[data-plan=b]').click();await page.locator('#week').focus();await page.locator('#week').press('End');assert(await page.getByText('Plan B · week 24 burden index',{exact:true}).isVisible());
  await page.locator('[data-view=prognosis]').click();assert(await page.getByText('Unavailable: no applicable individual model.',{exact:true}).isVisible());await page.locator('[data-view=evidence]').click();assert(await page.getByText('Authoritative model',{exact:true}).isVisible());
  await page.locator('#followup').click();assert(await page.getByText('Data cutoff: week 6',{exact:false}).isVisible());await page.locator('#followup').click();assert(await page.getByText('Both synthetic follow-ups are saved.',{exact:false}).isVisible());
  const download=page.waitForEvent('download');await page.locator('#export').click();const file=await (await download).path();const exported=JSON.parse(fs.readFileSync(file,'utf8'));assert.equal(exported.result.patient.patient_id,'P001');assert.equal(exported.result.clinical_use,false);assert.equal(exported.result.patient.cutoff_week,12);assert(!/[\u3400-\u9fff]/.test(JSON.stringify(exported)));
  await page.reload();await page.locator('#run:enabled').waitFor();assert(await page.getByText('Both synthetic follow-ups are saved.',{exact:false}).isVisible());
  const saved=await page.evaluate(()=>JSON.parse(localStorage.getItem('emeris-patient-lab-v2-en')));assert.equal(saved.length,3);assert.deepEqual(saved[0],original);
  await page.locator(`[data-run="${original.run_id}"]`).click();await page.locator('#followup').click();assert.equal(await page.locator('#history button').count(),3);
  await page.locator('[data-view=response]').click();const out=process.env.PATIENT_LAB_QA_DIR;if(out){fs.mkdirSync(out,{recursive:true});await page.screenshot({path:path.join(out,'public-desktop.png'),fullPage:true});}
  for(const width of [390,320]){await page.setViewportSize({width,height:1100});await page.waitForTimeout(200);assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));if(out&&width===390)await page.screenshot({path:path.join(out,'public-mobile.png'),fullPage:true});}
  const second=await browser.newContext();const p2=await second.newPage();await p2.goto(base);await p2.locator('#run:enabled').waitFor();assert(await p2.getByText('No saved runs in this browser.',{exact:true}).isVisible());await second.close();
  assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
  const receipt={url:base,checks:['English interface and export','verified templates','save and compare','two follow-ups','export','reload and original preserved','duplicate follow-up','visitor isolation','desktop/390px/320px'],errors,external_requests:external};
  if(out)fs.writeFileSync(path.join(out,'public-browser.json'),JSON.stringify(receipt,null,2));console.log(JSON.stringify(receipt));await context.close();
 }finally{await browser.close();if(service){service.kill('SIGTERM');await new Promise(resolve=>service.exitCode!==null||service.signalCode?resolve():service.once('exit',resolve));}if(temporary)fs.rmSync(temporary,{recursive:true,force:true});}
})().catch(e=>{console.error(e);process.exit(1)});
