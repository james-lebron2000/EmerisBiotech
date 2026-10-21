const {chromium}=require('/Users/lijinming/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const http=require('http'),fs=require('fs'),path=require('path'),assert=require('assert');
(async()=>{
 const root=path.resolve(__dirname,'..'),id='NCT05687266';let calls=[],mode='success',userOnly=false;
 const trial={nct_id:id,trial_name:'QA AVANZAR',company:'QA sponsor',indication:'Non-Small Cell Lung Cancer',targets:[],inputs:{},window:{start:'2026-01-01',end:'2026-12-31',precision:'year'},sources:[]};
 const published={record:{snapshot_id:'qa-public',target:{endpoint:'OS',population:'Private-like population must not seed query',comparison:'Comparator',success_definition:'Prespecified endpoint'},forecasts:{},evidence_sources:[],model_version:'qa',pipeline_version:'qa'},sha256:'b'.repeat(64)};
 const result={records:[{nct_id:'NCT12345678',trial_name:'<img src=x onerror="window.xss=1">QA study',sponsor:'Source sponsor',conditions:['NSCLC'],phase:['PHASE3'],study_design:{allocation:'RANDOMIZED',interventionModel:'PARALLEL',primaryPurpose:'TREATMENT',maskingInfo:{masking:'DOUBLE'}},enrollment:{count:320,type:'ACTUAL'},primary_endpoints:[{measure:'Progression-free survival',timeFrame:'24 months'}],registry_updated_at:'2026-09-01',source_url:'https://clinicaltrials.gov/study/NCT12345678'},{nct_id:'NCT87654321',trial_name:'Missing-data study',primary_endpoints:[],source_url:'javascript:alert(1)'}],source:'https://clinicaltrials.gov/api/v2/studies',retrieved_at:'2026-10-20T12:00:00Z',response_sha256:'a'.repeat(64),total_registry_matches:850};
 const server=http.createServer((req,res)=>{
  const u=new URL(req.url,'http://localhost');
  if(u.pathname==='/api/product/trials/'+id){res.setHeader('Content-Type','application/json');res.end(JSON.stringify({upcoming:userOnly?null:trial,historical:[],public_predictions:userOnly?[published]:[],public_verified_outcomes:[],comparables:{records:[]}}));return;}
  if(u.pathname==='/api/predict/comparables'){
   calls.push(Object.fromEntries(u.searchParams));const current=mode;
   const answer=()=>{res.setHeader('Content-Type','application/json');if(current==='error'){res.statusCode=502;res.end('{"detail":"source unavailable"}');}else res.end(JSON.stringify({...result,records:current==='empty'?[]:result.records}));};
   if(current==='delay')setTimeout(answer,750);else answer();return;
  }
  const file=u.pathname.startsWith('/trial/')?'static/trial-detail.html':u.pathname.slice(1);
  if(!file.startsWith('static/') || file.includes('..')){res.statusCode=404;res.end();return;}
  try{res.setHeader('Content-Type',file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':file.endsWith('.svg')?'image/svg+xml':'text/html');res.end(fs.readFileSync(path.join(root,file)));}catch{res.statusCode=404;res.end();}
 });
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));const base='http://127.0.0.1:'+server.address().port;
 const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'}),page=await browser.newPage({viewport:{width:1280,height:900}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
 try{
  await page.goto(base+'/trial/'+id);await page.waitForSelector('#comparable-form');assert.strictEqual(calls.length,0);assert.strictEqual(await page.locator('#comparable-condition').inputValue(),trial.indication);
  await page.locator('#comparable-condition').fill('NSCLC');mode='delay';await page.locator('#comparable-submit').click();await page.waitForFunction(()=>document.querySelector('#comparable-results').getAttribute('aria-busy')==='true');assert(await page.locator('#comparable-submit').isDisabled());
  await page.waitForSelector('.comparable-card');assert.deepStrictEqual(calls[0],{condition:'NSCLC',exclude_nct:id});assert.strictEqual(await page.locator('.comparable-card').count(),2);
  const text=await page.locator('#comparable-results').innerText();assert(text.includes('320（ACTUAL）'));assert(text.includes('RANDOMIZED'));assert(text.includes('Source sponsor'));assert(text.includes('未抽取 / 未报告'));assert(text.includes('不是读出日期'));assert(text.includes('a'.repeat(64)));
  await page.locator('.comparable-card details summary').first().click();assert((await page.locator('.comparable-card').first().innerText()).includes('24 months'));assert.strictEqual(await page.evaluate(()=>window.xss),undefined);assert.strictEqual(await page.locator('#comparable-results a[href^="javascript:"]').count(),0);
  assert((await page.locator('.comparable-card').first().innerText()).includes('<img src=x'));assert.strictEqual(await page.locator('#comparable-results img').count(),0);
  mode='empty';await page.locator('#comparable-submit').click();await page.waitForFunction(()=>document.querySelector('#comparable-status').textContent.includes('未返回'));assert.strictEqual(await page.locator('.comparable-card').count(),0);
  mode='error';await page.locator('#comparable-submit').click();await page.waitForFunction(()=>document.querySelector('#comparable-status').textContent.includes('暂未完成'));assert(!(await page.locator('#comparable-submit').isDisabled()));assert.strictEqual(await page.locator('#comparable-results').innerText(),'');
  mode='success';await page.locator('#comparable-condition').fill('lung cancer');await page.locator('#comparable-condition').press('Enter');await page.waitForSelector('.comparable-card');assert.strictEqual(calls.at(-1).condition,'lung cancer');
  await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));await page.locator('#theme-toggle').click();assert.strictEqual(await page.locator('html').getAttribute('data-theme'),'dark');
  const before=calls.length;userOnly=true;await page.reload();await page.waitForSelector('#comparable-form');assert.strictEqual(await page.locator('#comparable-condition').inputValue(),'');assert.strictEqual(calls.length,before);assert((await page.locator('#comparable-help').innerText()).includes('不会从用户目标'));
  assert.deepStrictEqual(errors,[]);console.log(JSON.stringify({status:'passed',checks:['no_automatic_query','editable_public_indication','explicit_condition_and_exclusion','loading','actual_enrollment_and_design','endpoint_window','source_date_hash','missing_values','xss_escaped','empty','error_and_retry','keyboard_submit','mobile','dark','public_user_no_inference'],query_calls:calls.length}));
 }finally{await browser.close();await new Promise(resolve=>server.close(resolve));}
})().catch(e=>{console.error(e.stack);process.exit(1);});
