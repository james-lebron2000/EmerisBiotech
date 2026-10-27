'use strict';
// Real HTTP and shipped HTML/CSP, isolated SQLite, no API fixtures or providers.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const {spawn}=require('node:child_process');
const fs=require('node:fs'),os=require('node:os'),path=require('node:path'),assert=require('node:assert/strict');
(async()=>{
 const root=path.resolve(__dirname,'..'),temporary=fs.mkdtempSync(path.join(os.tmpdir(),'patient-lab-'));
 const python=process.env.VBT_PYTHON || path.join(root,'.venv','bin','python');
 const env={PATH:process.env.PATH,PYTHONPATH:path.join(root,'src'),PYTHONUNBUFFERED:'1'};
 const service=spawn(python,['scripts/serve_patient_lab.py','--workspace',path.join(temporary,'workspace'),'--state-dir',path.join(temporary,'state'),'--port','0'],{cwd:root,env,stdio:['ignore','pipe','pipe']});
 let browser,logs='',startupOutput='';service.stderr.on('data',d=>logs+=d.toString());
 try{
  const base=await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(new Error('Server startup timed out: '+startupOutput+'\n'+logs)),60000);service.on('error',reject);service.on('exit',code=>{clearTimeout(timer);reject(new Error('Server exited '+code+': '+logs));});service.stdout.on('data',d=>{startupOutput+=d.toString();const m=startupOutput.match(/http:\/\/127\.0\.0\.1:\d+/);if(m){clearTimeout(timer);resolve(m[0]);}});});
  browser=await chromium.launch({headless:true,...(process.env.CHROMIUM_EXECUTABLE?{executablePath:process.env.CHROMIUM_EXECUTABLE}:{})});
  const page=await browser.newPage({viewport:{width:1440,height:1100}}),errors=[],external=[],checks=[];
  page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(!r.url().startsWith(base))external.push(r.url());});
  const start=await page.goto(base+'/patient-lab');assert(!start.headers()['content-security-policy'].includes('script-src'));
  assert(await page.getByText('No scenario has been run yet.',{exact:false}).isVisible());assert(!await page.locator('.pl-id').innerText().then(t=>t.includes('DEMO')));
  await page.getByRole('button',{name:'Run and save a new scenario'}).click();await page.waitForURL(/id=/);const originalURL=page.url(),originalId=new URL(originalURL).searchParams.get('id');
  const before=await (await page.request.get(base+'/patient-lab/export?id='+originalId)).json();assert.equal(before.result.patient.patient_id,'P001');assert.equal(before.result.clinical_use,false);checks.push('create persisted synthetic run');
  await page.locator('.pl-plan').filter({hasText:'Plan B'}).click();assert.equal(await page.locator('.pl-plan[aria-current=true]').innerText().then(t=>t.includes('Plan B')),true);await page.locator('#scenario-week').focus();await page.locator('#scenario-week').press('End');await page.getByRole('button',{name:'Update view'}).click();assert(await page.getByText('Plan B · 24-week burden index').isVisible());checks.push('plan and time selection');
  await page.getByRole('link',{name:'Response and prognosis',exact:true}).click();assert(await page.getByText('Unavailable; no applicable individual model').isVisible());await page.getByRole('link',{name:'Evidence and gaps',exact:true}).click();assert(await page.getByText('Age, diagnosis, anatomy and molecular fields are not computational inputs',{exact:false}).isVisible());checks.push('prognosis and evidence boundaries');
  await page.getByRole('button',{name:/Load 6 week/}).click();await page.waitForURL(u=>u.searchParams.get('id')!==originalId);const childURL=page.url();assert(await page.getByText('Data cutoff: week 6',{exact:false}).isVisible());await page.getByRole('button',{name:/Load 12 week/}).click();assert(await page.getByText('Both synthetic follow-ups are loaded',{exact:false}).isVisible());checks.push('two followup versions');
  const frozen=await (await page.request.get(base+'/patient-lab/export?id='+originalId)).json();assert.deepEqual(frozen,before);await page.goto(childURL);assert(await page.getByText('Data cutoff: week 6',{exact:false}).isVisible());checks.push('reload and original immutability');
  await page.goto(originalURL);const out=process.env.PATIENT_LAB_QA_DIR;if(out){fs.mkdirSync(out,{recursive:true});await page.screenshot({path:path.join(out,'desktop.png'),fullPage:true});}
  for(const width of [390,320]){await page.setViewportSize({width,height:1100});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));await page.locator('summary').filter({hasText:'Biology and missing information'}).click();assert(await page.getByText('Not provided',{exact:true}).first().isVisible());await page.locator('summary').filter({hasText:'Biology and missing information'}).click();if(out&&width===390)await page.screenshot({path:path.join(out,'mobile.png'),fullPage:true});}checks.push('390px and 320px real layout');
  assert.deepEqual(errors,[]);assert.deepEqual(external,[]);const receipt={scope:'synthetic fixture; real loopback HTTP, no provider or production access',checks,errors,external_requests:external};if(out)fs.writeFileSync(path.join(out,'browser.json'),JSON.stringify(receipt,null,2));console.log(JSON.stringify(receipt));
 }finally{if(browser)await browser.close();service.kill('SIGTERM');await new Promise(resolve=>service.exitCode!==null?resolve():service.once('exit',resolve));fs.rmSync(temporary,{recursive:true,force:true});}
})().catch(e=>{console.error(e);process.exit(1)});
