'use strict';
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs=require('fs'),assert=require('assert');
(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.CHROMIUM_EXECUTABLE ? {executablePath:process.env.CHROMIUM_EXECUTABLE} : {})});
 const context=await browser.newContext({viewport:{width:390,height:844}}),page=await context.newPage(),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 const data={schema_version:'predome.analytics.v1',scope:'prospective',model:'deepseek-v4-pro',models:['deepseek-v4-pro'],rolling:5,
 summary:{answered:0,total_records:3,abstained:1,failed:1,unavailable:1,asset_groups:0,distinct_trials:3,accuracy:null,brier:null,coverage:0,performance_status:'insufficient_verified_predictions'},
 calibration_bins:[],distribution:[],cumulative:[],subgroups:{},baselines:[],baseline_common_n:0,excluded:[],provenance:{dashboard:{}},
 records:['abstain','failed','unavailable'].map((status,i)=>({trial_name:'<img src=x onerror="window.xss=1">',trial_id:'NCT0000000'+i,endpoint:'PFS',prediction_status:status,probability:null,label:1,model:'deepseek-v4-pro',model_version:'<script>bad</script>',features_as_of:'2025-01-01',prediction_at:'2025-01-02',locked_at:'2025-01-03'}))};
 await context.route('**/*',route=>{const u=new URL(route.request().url());assert.strictEqual(route.request().method(),'GET');if(u.origin!=='http://127.0.0.1:19339')return route.abort();
 if(u.pathname==='/api/analytics')return route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
 if(u.pathname.startsWith('/api/'))return route.fulfill({status:503,body:'unavailable'});
 const file=u.pathname==='/analytics'?'static/analytics.html':u.pathname.slice(1);
 if(!file.startsWith('static/')||!fs.existsSync(file))return route.fulfill({status:404,body:''});
 return route.fulfill({body:fs.readFileSync(file),contentType:file.endsWith('.js')?'application/javascript':file.endsWith('.html')?'text/html':'text/css'});});
 try{await page.goto('http://127.0.0.1:19339/analytics');await page.waitForFunction(()=>document.getElementById('status').textContent.includes('已计算'));
 const summary=await page.locator('#summary').innerText();assert(summary.includes('0 / 3'));assert(summary.includes('拒答 1 · 失败 1 · 不可用 1'));assert(summary.includes('0.0%'));
 const rows=await page.locator('#records tbody tr').allInnerTexts();assert(rows[0].includes('模型拒答'));assert(rows[1].includes('运行失败'));assert(rows[2].includes('模型不可用'));assert(rows.every(x=>!x.includes('0.0%')));assert(rows.every(x=>x.includes('2025-01-01')&&!x.includes('2025-01-03')));
 assert.strictEqual(await page.locator('#records img, #records script').count(),0);assert(!(await page.evaluate(()=>window.xss)));
 assert((await page.locator('#method').textContent()).includes('跨后续版本也不替换最初拒答或失败'));
 assert.strictEqual(await page.locator('#calibration svg').count(),0);assert.strictEqual(await page.locator('#baselines tbody tr').count(),0);
 assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));await page.locator('#theme-toggle').click();assert.strictEqual(await page.locator('html').getAttribute('data-theme'),'dark');assert.deepStrictEqual(errors,[]);
 console.log(JSON.stringify({status:'passed',checks:['three explicit nonanswer statuses','coverage zero with denominator three','null never shown as zero probability','no fabricated scores','evidence cutoff distinct from lock','escaped text','mobile dark'],browser_errors:errors}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e.stack);process.exit(1)});
