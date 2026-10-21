/* Browser contract tests. Generate fixtures with ui_public_results_fixtures.py,
 * serve repository root on 19338, then run: node tests/ui_public_results.cjs.
 * All outcome fixtures are synthetic and are never written to the live store. */
const {chromium}=require('/Users/lijinming/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs=require('fs'),path=require('path'),assert=require('assert');
const root=path.resolve(__dirname,'..'),fixtures='/tmp/predome-public-ui',out=path.join(root,'verification/upgrade-public-results');
fs.mkdirSync(out,{recursive:true});
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
 const context=await browser.newContext({viewport:{width:1440,height:1000}}),page=await context.newPage(),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 let empty=false;
 await page.route('**/api/product/dashboard',r=>r.fulfill({contentType:'application/json',body:fs.readFileSync(path.join(fixtures,empty?'empty.json':'data.json'),'utf8')}));
 await page.route('**/api/product/trials/*',r=>r.fulfill({contentType:'application/json',body:fs.readFileSync(path.join(fixtures,r.request().url().split('/').pop()+'.json'),'utf8')}));
 await page.route('**/api/analytics?*',r=>r.fulfill({contentType:'application/json',body:fs.readFileSync(path.join(fixtures,'analytics.json'),'utf8')}));
 await page.route(/^http:\/\/127\.0\.0\.1:19338\/trial\/NCT\d{8}$/,r=>r.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(root,'static/trial-detail.html'),'utf8')}));
 await page.goto('http://127.0.0.1:19338/static/track-record.html');
 await page.waitForSelector('#verified-results-body tr');
 assert.equal(await page.locator('#verified-results-body tr').count(),2);
 assert.equal(await page.locator('#history-body tr').count(),8);
 assert((await page.locator('#verified-results').innerText()).includes('0.01'));
 assert((await page.locator('#verified-results').innerText()).includes('Retrospective · 事后记录'));
 assert(!(await page.locator('body').innerText()).includes('SECRET'));
 const downloadPromise=page.waitForEvent('download');await page.click('#export-verified');const download=await downloadPromise;
 await download.saveAs(path.join(out,'synthetic-verified-export.csv'));
 assert(fs.readFileSync(path.join(out,'synthetic-verified-export.csv'),'utf8').includes('NCT01234567'));
 await page.screenshot({path:path.join(out,'track-record-light.png'),fullPage:true});
 await page.click('#theme-toggle');await page.screenshot({path:path.join(out,'track-record-dark.png'),fullPage:true});
 await page.goto('http://127.0.0.1:19338/trial/NCT01234567');await page.waitForSelector('#trial-page h1');
 assert.equal(await page.locator('#trial-page h1').innerText(),'NCT01234567');
 assert.equal(await page.locator('#trial-page > section').count(),8);
 let text=await page.locator('#trial-page').innerText();
 assert(text.includes('输入由用户提供')&&text.includes('未满足前瞻绩效条件'));
 assert(text.includes('0.01')&&text.includes('0.9'));
 assert(!text.includes('undefined')&&!text.includes('SECRET'));
 assert(!text.includes('95% CI')); // The fixture has a 90% interval; never relabel it.
 await page.screenshot({path:path.join(out,'user-detail-dark.png'),fullPage:true});
 await page.setViewportSize({width:390,height:844});
 assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 await page.screenshot({path:path.join(out,'user-detail-mobile.png'),fullPage:true});
 await page.setViewportSize({width:1440,height:1000});
 await page.goto('http://127.0.0.1:19338/trial/NCT05687266');await page.waitForSelector('#trial-page h1');
 text=await page.locator('#trial-page').innerText();assert(text.includes('Partially Verified'));
 assert.equal(await page.locator('#trial-page > section').nth(2).locator('.badge').filter({hasText:'本目标已核验'}).count(),1);
 assert.equal(await page.locator('#trial-page > section').nth(2).locator('.badge').filter({hasText:'本目标待核验'}).count(),3);
 await page.goto('http://127.0.0.1:19338/trial/NCT04964934');await page.waitForSelector('#trial-page h1');
 assert((await page.locator('#trial-page').innerText()).includes('历史回测不能计入前瞻表现'));
 empty=true;await page.goto('http://127.0.0.1:19338/static/track-record.html');
 await page.waitForSelector('#verified-results .empty');assert(await page.locator('#export-verified').isDisabled());
 assert.equal(await page.locator('#history-body tr').count(),8);
 assert.deepEqual(errors,[]);
 const receipt={status:'passed',test_groups:9,page_errors:errors,fixtures:'Synthetic approved results in isolated temporary database; not real clinical outcomes',checks:['approved outcomes separate from 8 historical replays','per-model prospective eligibility','safe export','light and dark','new public-only NCT detail','90% interval not labeled 95%','mobile no horizontal overflow','one of four targets verified','empty state and export disabled']};
 fs.writeFileSync(path.join(out,'receipt.json'),JSON.stringify(receipt,null,2));console.log(JSON.stringify(receipt));await browser.close();
})().catch(e=>{console.error(e);process.exit(1);});
