'use strict';
// Isolated browser transport fixtures; no server, credentials, provider calls,
// clinical calculation, or live ledger writes. Both HTTPS ports are intercepted.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const fs=require('fs'),path=require('path'),assert=require('assert');
const root=path.resolve(__dirname,'..');
const html=fs.readFileSync(path.join(root,'static/trials.html'));
const script=fs.readFileSync(path.join(root,'static/trials.js'));
const catalog={catalog:{as_of:'2026-10-23',candidates:[],excluded:[],sources:[]},source_sha256:'a'.repeat(64)};
const response={mode:'scenario',summary:{conditional_power_at_median:.7,assurance:.65,
  probability_hr_below_1:.9,probability_hr_below_clinical_threshold:.6},result_sha256:'isolated-fixture'};
(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.CHROMIUM_EXECUTABLE?{executablePath:process.env.CHROMIUM_EXECUTABLE}:{})});
 const checks=[];
 try{
  for(const port of ['18443','443']){
   const ctx=await browser.newContext(),page=await ctx.newPage(),requests=[],errors=[];
   page.on('pageerror',error=>errors.push(error.message));
   await ctx.route('**/*',async route=>{
    const request=route.request(),u=new URL(request.url());requests.push({method:request.method(),path:u.pathname,body:request.postData()});
    assert.strictEqual(u.hostname,'predome-port-fixture.test','Unexpected external request');
    if(u.pathname==='/trials')return route.fulfill({contentType:'text/html',body:html});
    if(u.pathname==='/static/trials.js')return route.fulfill({contentType:'application/javascript',body:script});
    if(u.pathname==='/static/trials.css')return route.fulfill({contentType:'text/css',body:''});
    // Other legacy modules are outside this bounded scenario-form regression.
    if(['/static/forecasts.js','/static/review.js'].includes(u.pathname))return route.fulfill({contentType:'application/javascript',body:''});
    if(u.pathname==='/api/trials/catalog')return route.fulfill({json:catalog});
    if(u.pathname==='/api/trials/execute')return route.fulfill({json:response});
    return route.abort();
   });
   await page.goto('https://predome-port-fixture.test:'+port+'/trials');
   await page.waitForFunction(()=>document.getElementById('catalogDownload').disabled===false);
   const posts=()=>requests.filter(r=>r.method==='POST');
   if(port==='18443'){
    assert.strictEqual(await page.locator('#scenarioForm').getAttribute('aria-disabled'),'true');
    const controls=await page.locator('#scenarioForm input,#scenarioForm select,#scenarioForm button').evaluateAll(es=>es.map(e=>e.disabled));
    assert(controls.length>=9&&controls.every(Boolean),'Every scenario control must be disabled');
    assert((await page.locator('#scenarioReadOnlyNotice').innerText()).includes('内测端口仅供查看，情景计算请使用已登录研究工作台'));
    assert.strictEqual(await page.locator('#scenarioReadOnlyNotice a').getAttribute('href'),'https://8.148.145.28/research');
    assert((await page.locator('#scenarioResult').innerText()).includes('不计算默认示例'));
    assert(await page.locator('#resultDownload').isDisabled());
    assert(await page.locator('#reportDownload').isDisabled());
    assert.strictEqual(posts().length,0,'Internal load must not auto-submit the default scenario');
    // Disabled controls and a handler guard both prevent form submits.
    await page.locator('#scenarioForm').evaluate(e=>e.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true})));
    await page.evaluate(()=>runScenario());
    assert.strictEqual(posts().length,0,'Internal direct dispatch cannot issue POST');
    assert(!(await page.locator('#search').isDisabled()));
    assert(!(await page.locator('#catalogDownload').isDisabled()));
    checks.push('18443: disabled controls, clear notice and absolute workbench link, no initial or dispatched POST, evidence reads/downloads retained');
   }else{
    await page.waitForFunction(()=>document.getElementById('resultDownload').disabled===false);
    assert.strictEqual(await page.locator('#scenarioReadOnlyNotice').count(),0);
    assert.strictEqual(await page.locator('#scenarioForm').getAttribute('aria-disabled'),null);
    assert.strictEqual(posts().length,1,'Existing public default scenario remains intact');
    await page.locator('#hr').fill('0.82');
    assert(await page.locator('#resultDownload').isDisabled());
    await page.locator('#calculate').click();
    await page.waitForFunction(()=>document.getElementById('resultDownload').disabled===false);
    assert.strictEqual(posts().length,2,'Public form still submits to existing execute API');
    const submitted=JSON.parse(posts()[1].body);
    assert(Math.abs(submitted.prior_log_hr_mean-Math.log(.82))<1e-12);
    assert.strictEqual(submitted.mode,'scenario');
    assert(!(await page.locator('#calculate').isDisabled()));
    checks.push('443: original default example, input invalidation, explicit submission and existing parameters retained');
   }
   assert.deepStrictEqual(errors,[]);await ctx.close();
  }
  console.log(JSON.stringify({scope:'Isolated port-specific UI only; no clinical validation',passed:checks.length,checks,errors:[]}));
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1)});
