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
 let browser,logs='';service.stderr.on('data',d=>logs+=d.toString());
 try{
  const base=await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(new Error('Server startup timed out: '+logs)),20000);service.on('error',reject);service.on('exit',code=>{clearTimeout(timer);reject(new Error('Server exited '+code+': '+logs));});service.stdout.on('data',d=>{const m=d.toString().match(/http:\/\/127\.0\.0\.1:\d+/);if(m){clearTimeout(timer);resolve(m[0]);}});});
  browser=await chromium.launch({headless:true,...(process.env.CHROMIUM_EXECUTABLE?{executablePath:process.env.CHROMIUM_EXECUTABLE}:{})});
  const page=await browser.newPage({viewport:{width:1440,height:1100}}),errors=[],external=[],checks=[];
  page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(!r.url().startsWith(base))external.push(r.url());});
  const start=await page.goto(base+'/patient-lab');assert(!start.headers()['content-security-policy'].includes('script-src'));
  assert(await page.getByText('当前未执行推演。',{exact:false}).isVisible());assert(!await page.locator('.pl-id').innerText().then(t=>t.includes('DEMO')));
  await page.getByRole('button',{name:'运行并保存新推演'}).click();await page.waitForURL(/id=/);const originalURL=page.url(),originalId=new URL(originalURL).searchParams.get('id');
  const before=await (await page.request.get(base+'/patient-lab/export?id='+originalId)).json();assert.equal(before.result.patient.patient_id,'P001');assert.equal(before.result.clinical_use,false);checks.push('create persisted synthetic run');
  await page.locator('.pl-plan').filter({hasText:'方案 B'}).click();assert.equal(await page.locator('.pl-plan[aria-current=true]').innerText().then(t=>t.includes('方案 B')),true);await page.locator('#scenario-week').focus();await page.locator('#scenario-week').press('End');await page.getByRole('button',{name:'更新视图'}).click();assert(await page.getByText('方案 B · 第 24 周负荷指数').isVisible());checks.push('plan and time selection');
  await page.getByRole('link',{name:'疗效与预后',exact:true}).click();assert(await page.getByText('不可估计；没有适用的个体模型').isVisible());await page.getByRole('link',{name:'证据与缺口',exact:true}).click();assert(await page.getByText('年龄、癌种、部位与分子字段未参与计算',{exact:false}).isVisible());checks.push('prognosis and evidence boundaries');
  await page.getByRole('button',{name:/加载第 6 周/}).click();await page.waitForURL(u=>u.searchParams.get('id')!==originalId);const childURL=page.url();assert(await page.getByText('当前资料截止：第 6 周',{exact:false}).isVisible());await page.getByRole('button',{name:/加载第 12 周/}).click();assert(await page.getByText('两次预设随访均已加载',{exact:false}).isVisible());checks.push('two followup versions');
  const frozen=await (await page.request.get(base+'/patient-lab/export?id='+originalId)).json();assert.deepEqual(frozen,before);await page.goto(childURL);assert(await page.getByText('当前资料截止：第 6 周',{exact:false}).isVisible());checks.push('reload and original immutability');
  await page.goto(originalURL);const out=process.env.PATIENT_LAB_QA_DIR;if(out){fs.mkdirSync(out,{recursive:true});await page.screenshot({path:path.join(out,'desktop.png'),fullPage:true});}
  for(const width of [390,320]){await page.setViewportSize({width,height:1100});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));await page.locator('summary').filter({hasText:'微观生物与缺失资料'}).click();assert(await page.getByText('未提供',{exact:true}).first().isVisible());await page.locator('summary').filter({hasText:'微观生物与缺失资料'}).click();if(out&&width===390)await page.screenshot({path:path.join(out,'mobile.png'),fullPage:true});}checks.push('390px and 320px real layout');
  assert.deepEqual(errors,[]);assert.deepEqual(external,[]);const receipt={scope:'synthetic fixture; real loopback HTTP, no provider or production access',checks,errors,external_requests:external};if(out)fs.writeFileSync(path.join(out,'browser.json'),JSON.stringify(receipt,null,2));console.log(JSON.stringify(receipt));
 }finally{if(browser)await browser.close();service.kill('SIGTERM');await new Promise(resolve=>service.exitCode!==null?resolve():service.once('exit',resolve));fs.rmSync(temporary,{recursive:true,force:true});}
})().catch(e=>{console.error(e);process.exit(1)});
