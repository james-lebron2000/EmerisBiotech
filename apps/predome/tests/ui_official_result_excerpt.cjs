'use strict';
const {chromium}=require('/Users/lijinming/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs=require('fs'),path=require('path'),assert=require('assert');
(async()=>{
 const base='http://127.0.0.1:19343',out=path.resolve('verification/upgrade-official-result-excerpt');fs.mkdirSync(out,{recursive:true});
 const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'}),ctx=await browser.newContext({viewport:{width:1280,height:900}}),page=await ctx.newPage();
 const errors=[],requests=[],checks=[],excerpt='NCT12345678 reported its primary outcome. <img src=x onerror="window.xss=1"> <script>window.xss=2</script> Exact source text & symbols remain visible.';let basis='document_metadata',legacy=false;
 page.on('pageerror',e=>errors.push(e.message));
 await ctx.route('**/*',async route=>{
  const u=new URL(route.request().url());requests.push({url:u.href,method:route.request().method()});assert.strictEqual(u.origin,base,'No external request permitted');assert.strictEqual(route.request().method(),'GET','Read-only test');
  if(u.pathname==='/api/product/trials/NCT12345678'){
   const target={endpoint:'Primary PFS',population:'ITT',comparison:'Drug versus placebo',success_definition:'Prespecified superiority'},forecasts={'deepseek-v4-pro':{status:'predicted',probability:.7}};
   const source={url:'https://example.org/approved-announcement',published_at:'2026-10-20',retrieved_at:'2026-10-21',sha256:'original-source-hash'};
   if(!legacy)Object.assign(source,{excerpt,excerpt_sha256:'e'.repeat(64),publication_date_basis:basis});
   const outcome={target,label:1,readout_date:'2026-10-18',verified_at:'2026-10-21T01:00:00Z',forecasts,source,effect_size:null,confidence_interval:null,p_value:null,eligible_for_prospective:true,assessments:[{model:'correct',model_version:'1',probability:.7,score_eligible:true,coverage_eligible:true},{model:'incorrect',model_version:'1',probability:.2,score_eligible:true,coverage_eligible:true},{model:'refused',model_version:'1',probability:null,score_eligible:false,coverage_eligible:true},{model:'late',model_version:'1',probability:.8,score_eligible:false,coverage_eligible:false}],snapshot_id:'ps_fixture',outcome_sha256:'outcome-hash'};
   return route.fulfill({contentType:'application/json',body:JSON.stringify({upcoming:null,historical:[],public_predictions:[{sha256:'snapshot-hash',record:{target,forecasts,snapshot_id:'ps_fixture'}}],public_verified_outcomes:[outcome]})});
  }
  const file=u.pathname==='/trials/NCT12345678'?'static/trial-detail.html':u.pathname.replace(/^\//,'');
  if(!file.startsWith('static/')||!fs.existsSync(file))return route.fulfill({status:404,body:''});
  return route.fulfill({body:fs.readFileSync(file),contentType:file.endsWith('.html')?'text/html':file.endsWith('.js')?'application/javascript':'text/css'});
 });
 try{
  await page.goto(base+'/trials/NCT12345678');await page.waitForSelector('.approved-source-excerpt');
  const detail=page.locator('.approved-source-excerpt');assert.strictEqual(await detail.evaluate(d=>d.open),false);await detail.locator('summary').click();assert(await detail.evaluate(d=>d.open));assert.strictEqual(await detail.locator('blockquote').innerText(),excerpt);assert.strictEqual(await detail.locator('img,script').count(),0);assert(!(await page.evaluate(()=>window.xss)));assert((await detail.innerText()).includes('摘录 SHA-256：'+'e'.repeat(64)));checks.push('approved excerpt expands, exact original text and hash; XSS inert');
  const rows=await page.locator('.results-table tbody tr').allInnerTexts();assert(rows[0].includes('Correct · 阈值方向一致'));assert(rows[1].includes('Incorrect · 阈值方向不一致'));assert(rows[2].includes('Indeterminate · 未回答')&&rows[2].includes('不计概率评分')&&!rows[2].includes('0%'));assert(rows[3].includes('Not Evaluable'));checks.push('threshold direction distinct from nonanswer coverage and excluded records');
  const article=detail.locator('..');assert((await article.innerText()).includes('实际读出：2026-10-18'));assert((await detail.innerText()).includes('本公告发布日期：2026-10-20'));assert((await detail.innerText()).includes('从原公告元数据读取'));assert((await detail.innerText()).includes('公告日期与首次结果读出日期分别保留'));checks.push('document publication date separated from first actual readout');
  basis='reviewer_provided_not_independently_verified';await page.reload();await page.waitForSelector('.approved-source-excerpt');await page.locator('.approved-source-excerpt summary').click();assert((await page.locator('.approved-source-excerpt').innerText()).includes('由提交者依据原文填写'));assert(!(await page.locator('.approved-source-excerpt').innerText()).includes('从原公告元数据读取'));checks.push('reviewer-provided publication provenance remains distinct');
  await page.setViewportSize({width:390,height:844});await page.locator('#theme-toggle').click();assert.strictEqual(await page.locator('html').getAttribute('data-theme'),'dark');await page.locator('.approved-source-excerpt').scrollIntoViewIfNeeded();assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));await page.screenshot({path:path.join(out,'approved-excerpt-mobile-dark.png')});checks.push('expanded excerpt fits mobile dark layout');
  legacy=true;await page.reload();await page.waitForSelector('#trial-page > section');assert.strictEqual(await page.locator('.approved-source-excerpt').count(),0);assert((await page.locator('#trial-page').innerText()).includes('实际读出：2026-10-18'));assert((await page.locator('#trial-page').innerText()).includes('example.org'));assert.strictEqual(await page.locator('#trial-page > section').count(),8);checks.push('legacy source without excerpt keeps result and eight sections');
  assert.deepStrictEqual(errors,[]);assert(requests.every(r=>r.method==='GET'&&r.url.startsWith(base+'/')));fs.writeFileSync(path.join(out,'receipt.json'),JSON.stringify({status:'passed',scope:'fully intercepted local static and API responses; no production access or writes',checks,browser_errors:errors},null,2));console.log(JSON.stringify({status:'passed',checks}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e.stack);process.exit(1)});
