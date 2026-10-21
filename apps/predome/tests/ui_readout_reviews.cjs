/* Isolated browser regression: every API call is intercepted; no live store writes.
 * Serve the repository root on 19338, then run node tests/ui_readout_reviews.cjs. */
const {chromium}=require('/Users/lijinming/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs=require('fs'),path=require('path'),assert=require('assert');
const output=path.resolve(__dirname,'../verification/upgrade-readout-review');fs.mkdirSync(output,{recursive:true});
const quoteExact='The company will report topline results for NCT01234567 on May 12, 2027.';
const quoteWindow='We expect topline results for NCT01234567 in the first half of 2027.';
const candidates=Array.from({length:25},(_,i)=>({id:'source-'+i,nct_id:'NCT01234567',kind:i===20||i===21?'official_announcement_candidate':i===0?'reported_results_candidate':'registry_update_candidate',source:{url:'https://example.org/announcement-'+i,sha256:'a'.repeat(64),retrieved_at:'2026-10-21T01:00:00Z',published_at:'2026-10-20'},structured:{title:'Synthetic official source '+i,text:i===20?quoteExact:quoteWindow,published_at:'2026-10-20',trial_name:'Synthetic trial'},review_status:'pending_review'}));
const outcomes=Array.from({length:25},(_,i)=>({id:'outcome-'+i,snapshot_id:'snapshot-'+i,status:'pending_review',outcome:{trial_id:'NCT01234567',endpoint:'Primary endpoint '+i,label:1,readout_date:'2026-10-20',source:{url:'https://example.org/result'}}}));
let proposals=Array.from({length:25},(_,i)=>({id:'readout-'+i,created_at:'2026-10-21',status:'pending_review',can_review:i!==0,proposal:{nct_id:'NCT01234567',candidate_id:'source-21',date_kind:'estimated_window',start:'2027-01-01',end:'2027-06-30',precision:'half_year',label:'2027 H1',quoted_evidence:quoteWindow,reason:'Synthetic source based date proposal',source:candidates[21].source},decision:null}));
const calls=[],errors=[];
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
 const page=await browser.newPage({viewport:{width:1280,height:900}});page.on('pageerror',e=>errors.push(e.message));
 await page.route('**/api/**',async route=>{
  const request=route.request(),url=new URL(request.url()),pathname=url.pathname,method=request.method();
  const respond=data=>route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
  const paged=rows=>{const offset=Number(url.searchParams.get('offset')||0),limit=Number(url.searchParams.get('limit')||20);return {records:rows.slice(offset,offset+limit),pagination:{limit,offset,has_more:offset+limit<rows.length,next_offset:offset+limit<rows.length?offset+limit:null}};};
  if(method==='POST'){
   const body=request.postDataJSON();calls.push({pathname,body,csrf:request.headers()['x-csrf-token']});
   if(pathname==='/api/readout-updates'){const record={id:'created-'+calls.length,status:'pending_review',can_review:false,created_at:'2026-10-21',proposal:{...body,source:candidates.find(c=>c.id===body.candidate_id).source},decision:null};proposals.unshift(record);return respond(record);}
   if(pathname.startsWith('/api/readout-updates/')){const id=pathname.split('/')[3];const record=proposals.find(p=>p.id===id);record.status=body.decision;record.can_review=false;record.decision={decision:body.decision,created_at:'2026-10-21'};return respond(record);}
   if(pathname.startsWith('/api/platform/reviews/'))return respond({status:'saved'});
   if(pathname.includes('/link'))return respond({status:'needs_independent_review'});
  }
  if(pathname==='/api/intake/status')return respond({latest:null,scheduled_read_only:false});
  if(pathname==='/api/platform/capabilities')return respond({authenticated:true,reviewer:true,reviewer_configured:true});
  if(pathname==='/api/state')return respond({csrf:'isolated-test-csrf'});
  if(pathname==='/api/intake/candidates')return respond(paged(candidates));
  if(pathname==='/api/platform/reviews')return respond(paged(outcomes));
  if(pathname==='/api/readout-updates')return respond(paged(proposals));
  throw Error('Unexpected request '+method+' '+pathname);
 });
 await page.goto('http://127.0.0.1:19338/static/reviews.html');await page.waitForSelector('#review-content:not([hidden])');
 for(const group of ['intake','outcome','readout']){
  await page.waitForFunction(id=>document.getElementById(id+'-list').children.length===20,group);
  await page.click('#'+group+'-more');await page.waitForFunction(id=>document.getElementById(id+'-list').children.length===25,group);
  assert(await page.locator('#'+group+'-more').isHidden());
 }
 await page.click('[data-readout-propose="source-20"]');
 assert.equal(await page.inputValue('#readout-start'),'');assert(await page.locator('#readout-end-label').isHidden());
 assert((await page.locator('#readout-source-detail').innerText()).includes(quoteExact));
 await page.fill('#readout-start','2027-05-12');await page.fill('#readout-label','2027-05-12 official');await page.fill('#readout-quote',quoteExact);await page.fill('#readout-reason','Official source explicitly names this readout date.');
 await page.click('#readout-propose-submit');await page.waitForFunction(()=>document.getElementById('readout-propose-feedback').textContent.includes('已保存'));
 let last=calls.at(-1);assert.equal(last.body.date_kind,'official_exact_date');assert.equal(last.body.start,last.body.end);assert.equal(last.body.precision,'day');assert(!('source' in last.body));
 await page.click('#close-readout-propose');await page.click('[data-readout-propose="source-21"]');await page.selectOption('#readout-kind','estimated_window');
 assert.equal(await page.inputValue('#readout-start'),'');assert.equal(await page.inputValue('#readout-end'),'');
 await page.fill('#readout-start','2027-01-01');await page.fill('#readout-end','2026-06-30');await page.selectOption('#readout-precision','half_year');await page.fill('#readout-label','2027 H1');await page.fill('#readout-quote',quoteWindow);await page.fill('#readout-reason','Preserve source half-year precision without inferring a day.');
 const before=calls.length;await page.click('#readout-propose-submit');assert.equal(calls.length,before);assert((await page.locator('#readout-propose-feedback').innerText()).includes('不能早于'));
 await page.fill('#readout-end','2027-06-30');await page.click('#readout-propose-submit');await page.waitForFunction(()=>document.getElementById('readout-propose-feedback').textContent.includes('已保存'));
 last=calls.at(-1);assert.equal(last.body.date_kind,'estimated_window');assert.equal(last.body.precision,'half_year');assert.equal(last.body.end,'2027-06-30');
 await page.click('#close-readout-propose');await page.click('[data-readout-review="created-2"]');
 assert(await page.locator('#readout-review-submit').isDisabled());assert((await page.locator('#readout-review-feedback').innerText()).includes('提交者不可自审'));
 const selfBefore=calls.length;await page.evaluate(()=>document.getElementById('readout-review-form').dispatchEvent(new Event('submit',{cancelable:true,bubbles:true})));assert.equal(calls.length,selfBefore);
 await page.screenshot({path:path.join(output,'self-review-blocked.png')});await page.click('#close-readout-review');
 await page.click('[data-readout-review="readout-1"]');
 await page.selectOption('#readout-decision','approved');await page.fill('#readout-review-reason','Independently checked source wording, identity and precision.');
 for(const key of ['readout_not_completion','source_quote_supports_date','trial_identity_confirmed','primary_source_checked','precision_correct'])await page.check(`#readout-confirmations [name="${key}"]`);
 await page.click('#readout-review-submit');await page.waitForFunction(()=>document.getElementById('readout-review-feedback').textContent.includes('已保存'));
 last=calls.at(-1);assert.equal(last.pathname,'/api/readout-updates/readout-1/review');assert.equal(Object.keys(last.body.confirmations).length,5);assert(Object.values(last.body.confirmations).every(Boolean));assert(await page.locator('#readout-review-submit').isDisabled());
 await page.click('#close-readout-review');
 await page.click('[data-review="outcome-21"]');await page.fill('#reason','Existing result review remains independently accessible.');await page.selectOption('#decision','needs_review');await page.click('#review-form button[type="submit"]');await page.waitForFunction(()=>document.getElementById('review-feedback').textContent.includes('已保存'));assert.equal(calls.at(-1).pathname,'/api/platform/reviews/outcome-21');await page.click('#close-review');
 await page.click('[data-link="source-0"]');await page.fill('#link-snapshot','snapshot-test');await page.fill('#link-endpoint','Primary endpoint');await page.fill('#link-population','ITT');await page.fill('#link-comparison','Drug vs placebo');await page.fill('#link-date','2026-10-20');await page.selectOption('#link-label','1');await page.click('#link-form button');await page.waitForFunction(()=>document.getElementById('link-feedback').textContent.includes('已保存'));assert.equal(calls.at(-1).pathname,'/api/intake/candidates/source-0/link');await page.click('#close-link');
 assert(calls.every(c=>c.csrf==='isolated-test-csrf'));
 await page.setViewportSize({width:390,height:844});await page.click('[data-readout-propose="source-21"]');
 assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 await page.screenshot({path:path.join(output,'readout-proposal-mobile.png')});
 assert.deepEqual(errors,[]);const receipt={status:'passed',test_groups:9,page_errors:errors,post_calls:calls.map(c=>({path:c.pathname,date_kind:c.body.date_kind})),scope:'All API requests mocked in isolated browser; no production writes',checks:['three paginated lists reach record 21','official date exact contract','no inferred or prefilled date','window half-year contract and invalid range prevention','source provenance not supplied by client','self review UI gate','independent confirmation keys and immutable saved state','existing outcome review and link preserved','CSRF and mobile no page overflow']};fs.writeFileSync(path.join(output,'receipt.json'),JSON.stringify(receipt,null,2));console.log(JSON.stringify(receipt));await browser.close();
})().catch(e=>{console.error(e);process.exit(1);});
