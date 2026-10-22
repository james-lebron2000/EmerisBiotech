'use strict';
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const fs=require('fs'),path=require('path'),assert=require('assert');
const {execFileSync}=require('child_process');
const root=path.resolve(__dirname,'..'),base=(process.env.ARENA_LIVE_BASE||'http://127.0.0.1:19326').replace(/\/$/,'');
const surfaces=[['dashboard','/'],['track-record','/track-record'],['trials','/trials'],['research','/research'],['index','/data'],['predome','/predome'],['guide','/guide'],['analytics','/analytics'],['predict','/predict'],['trial-detail','/trial/NCT05687266'],['reviews','/reviews']];
(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.CHROMIUM_EXECUTABLE?{executablePath:process.env.CHROMIUM_EXECUTABLE}:{})});
 const context=await browser.newContext({viewport:{width:1440,height:1000},ignoreHTTPSErrors:process.env.ARENA_IGNORE_TLS_ERRORS==='true'}),page=await context.newPage(),checks=[],errors=[];
 const output=path.join(root,'verification/arena-navigation');fs.mkdirSync(output,{recursive:true});page.on('pageerror',e=>errors.push(e.message));
 try{
  for(const [name,route] of surfaces){
   const source=fs.readFileSync(path.join(root,'static',name+'.html'),'utf8');
   // Detect accidental deletion of any existing body, control, script or link.
   if(process.env.ARENA_VERIFY_NAV_ONLY==='true'){
    const previous=execFileSync('git',['show','HEAD:apps/predome/static/'+name+'.html'],{cwd:root,encoding:'utf8'}),anchor='<a href="/arena">公开挑战</a>';
    assert.strictEqual(previous.includes(anchor)?source:source.replace(anchor,''),previous,name+' changed beyond the navigation addition');
   }
   const response=await page.goto(base+route);assert(response.ok(),route+' HTTP status');
   const nav=page.locator('nav a[href="/arena"]');assert.strictEqual(await nav.count(),1,name+' arena navigation count');
   await nav.click();await page.waitForFunction(()=>document.getElementById('arena-status')?.textContent.includes('公开记录已读取'));assert.strictEqual(new URL(page.url()).pathname,'/arena');
   await page.setViewportSize({width:375,height:812});await page.goto(base+route);await page.locator('nav a[href="/arena"]').click();await page.waitForFunction(()=>document.getElementById('arena-status')?.textContent.includes('公开记录已读取'));await page.setViewportSize({width:1440,height:1000});
   checks.push(name+': '+(process.env.ARENA_VERIFY_NAV_ONLY==='true'?'only navigation changed; ':'')+'desktop and mobile link opens real arena');
  }
  await page.goto(base+'/');await page.waitForFunction(()=>!document.getElementById('export-calendar').disabled);await page.locator('#horizon').selectOption('all');await page.locator('#search').fill('AVANZAR');await page.locator('[data-view="table"]').click();assert(await page.locator('#trial-table').isVisible());assert((await page.locator('#trial-table').innerText()).includes('AVANZAR'));await page.locator('#search').fill('');
  await page.screenshot({path:path.join(output,'home-desktop.png'),fullPage:true});await page.setViewportSize({width:375,height:812});await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'Homepage mobile overflow');await page.screenshot({path:path.join(output,'home-mobile.png'),fullPage:true});checks.push('Homepage calendar filters and table view still work; no mobile overflow');
  await page.setViewportSize({width:1440,height:1000});await page.goto(base+'/trials');await page.waitForFunction(()=>!document.getElementById('catalogDownload').disabled);await page.locator('#search').fill('PACIFIC');assert((await page.locator('#cards').innerText()).includes('PACIFIC'));await page.locator('#search').fill('');assert(await page.locator('#scenarioForm').count()===1);assert(await page.locator('#reviewTitle').count()===1);await page.screenshot({path:path.join(output,'trials-desktop.png'),fullPage:true});await page.setViewportSize({width:375,height:812});await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'Trial workbench mobile overflow');await page.screenshot({path:path.join(output,'trials-mobile.png'),fullPage:true});checks.push('Trial catalog search works and worklist/design controls survive; no mobile overflow');
  assert.deepStrictEqual(errors,[]);fs.writeFileSync(path.join(output,'receipt.json'),JSON.stringify({at:new Date().toISOString(),base,scope:'Read-only navigation and existing control smoke checks; no model calls or data writes',checks,errors},null,2));console.log(JSON.stringify({checks:checks.length,errors,receipt:output}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
