'use strict';
// Shipped review assets with fully intercepted, explicitly SYNTHETIC transport.
// No server, live credentials, provider calls, original source download or ledger writes.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const fs=require('fs'),path=require('path'),assert=require('assert');
const root=path.resolve(__dirname,'..'),output=path.join(root,'verification/readout-review-ui');
const origin='http://predome-readout-fixture.test:19338',clone=x=>JSON.parse(JSON.stringify(x));
const quoteExact='SYNTHETIC company text: topline results for NCT01234567 will be reported on May 12, 2027.';
const quoteWindow='SYNTHETIC company text: topline results for NCT01234567 are expected in the first half of 2027.';
const quoteUnknown='SYNTHETIC company text: the official readout announcement timing for NCT01234567 is not yet confirmed.';
const candidates=Array.from({length:25},(_,i)=>({id:'source-'+i,nct_id:i===22?'NCT76543210':'NCT01234567',kind:i>=20&&i<=23?'official_announcement_candidate':i===0?'reported_results_candidate':'registry_update_candidate',source:{url:'https://example.org/synthetic-announcement-'+i,sha256:'a'.repeat(64),retrieved_at:'2026-10-21T01:00:00Z',published_at:'2026-10-20'},structured:{title:'SYNTHETIC official source '+i,text:i===20?quoteExact:i===23?quoteUnknown:quoteWindow,published_at:'2026-10-20',trial_name:'SYNTHETIC trial'},review_status:'pending_review'}));
const outcomes=Array.from({length:25},(_,i)=>({id:'outcome-'+i,snapshot_id:'snapshot-'+i,status:'pending_review',outcome:{trial_id:'NCT01234567',endpoint:'SYNTHETIC primary endpoint '+i,label:1,readout_date:'2026-10-20',source:{url:'https://example.org/synthetic-result'}}}));
const target=(id,nct,endpoint,cohort='formal-current')=>({id,nct_id:nct,cohort_id:cohort,trial_name:'SYNTHETIC '+id,endpoint,population:'SYNTHETIC registered analysis population',comparison:'SYNTHETIC intervention versus comparator',success_definition:'SYNTHETIC exact prespecified success rule',analysis_timepoint:'SYNTHETIC prespecified analysis',status:'pending'});
const firstTarget=target('formal-pfs','NCT01234567','SYNTHETIC BICR PFS <exact analysis>');
const otherTarget=target('formal-other','NCT76543210','SYNTHETIC second-source primary endpoint');
const arena={current_cohort_id:'formal-current',cohorts:[{id:'formal-current',kind:'registered_12_project_season',registered_at:'2026-10-21T01:00:00Z'},{id:'formal-old',kind:'registered_12_project_season',registered_at:'2026-10-20T01:00:00Z'},{id:'pilot',kind:'registered_patent_pair_pilot',registered_at:'2026-10-22T01:00:00Z'}],targets:[firstTarget,target('formal-os','NCT01234567','SYNTHETIC overall survival hypothesis'),otherTarget,target('old-pfs','NCT01234567','SYNTHETIC earlier question','formal-old'),target('pilot-pfs','NCT01234567','SYNTHETIC independent pilot','pilot'),{...target('withdrawn-pfs','NCT01234567','SYNTHETIC withdrawn question'),execution_status:'withdrawn_before_execution'}]};
let proposals=Array.from({length:25},(_,i)=>({id:'readout-'+i,created_at:'2026-10-21',status:'pending_review',can_review:i!==0,proposal:{nct_id:'NCT01234567',candidate_id:'source-21',date_kind:'estimated_window',start:'2027-01-01',end:'2027-06-30',precision:'half_year',label:'SYNTHETIC 2027 H1',quoted_evidence:quoteWindow,reason:'SYNTHETIC source-based date proposal',source:candidates[21].source},decision:null}));
Object.assign(proposals[2].proposal,{arena_target_id:firstTarget.id,arena_target_binding:clone(firstTarget),arena_target_binding_sha256:'b'.repeat(64),source_granularity:'named_trial',applicability:'exact_registered_target'});
Object.assign(proposals[3].proposal,{arena_target_id:firstTarget.id,arena_target_binding:clone(firstTarget),arena_target_binding_sha256:'d'.repeat(64),source_granularity:'exact_analysis',applicability:'exact_registered_target',date_kind:'readout_time_unconfirmed',start:null,end:null,precision:'unknown',label:'SYNTHETIC timing unconfirmed',quoted_evidence:quoteUnknown});
const calls=[],errors=[],unexpected=[],requests=[],checks=[];
let arenaFailure=false,holdNextArena=false,arenaRelease=null,holdNextPost=false,postRelease=null,capabilities={authenticated:true,reviewer:true,reviewer_configured:true};
(async()=>{
 fs.mkdirSync(output,{recursive:true});
 const browser=await chromium.launch({headless:true,...(process.env.CHROMIUM_EXECUTABLE?{executablePath:process.env.CHROMIUM_EXECUTABLE}:{})});
 try {
  const context=await browser.newContext({viewport:{width:1440,height:1000}}),page=await context.newPage();
  page.on('pageerror',e=>errors.push(e.message));
  await context.route('**/*',async route=>{
   const request=route.request(),url=new URL(request.url()),pathname=url.pathname,method=request.method();requests.push({path:pathname,method});
   if(url.origin!==origin){unexpected.push(request.url());return route.abort();}
   const respond=(data,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(data)});
   const paged=rows=>{const offset=Number(url.searchParams.get('offset')||0),limit=Number(url.searchParams.get('limit')||20);return {records:rows.slice(offset,offset+limit),pagination:{limit,offset,has_more:offset+limit<rows.length,next_offset:offset+limit<rows.length?offset+limit:null}};};
   if(method==='POST'){
    const body=request.postDataJSON();calls.push({pathname,body,csrf:request.headers()['x-csrf-token']});
    if(holdNextPost){holdNextPost=false;await new Promise(resolve=>postRelease=resolve);}
    if(pathname==='/api/readout-updates'){
     const source=candidates.find(c=>c.id===body.candidate_id),record={id:'created-'+calls.length,status:'pending_review',can_review:false,created_at:'2026-10-21',proposal:{...body,source:source.source},decision:null};
     if(body.arena_target_id)Object.assign(record.proposal,{arena_target_binding:clone(arena.targets.find(t=>t.id===body.arena_target_id)),arena_target_binding_sha256:'c'.repeat(64)});
     proposals.unshift(record);return respond(record);
    }
    if(pathname.startsWith('/api/readout-updates/')){const id=pathname.split('/')[3],record=proposals.find(p=>p.id===id);record.status=body.decision;record.can_review=false;record.decision={decision:body.decision,created_at:'2026-10-21'};return respond(record);}
    if(pathname.startsWith('/api/platform/reviews/'))return respond({status:'saved'});
    if(pathname.includes('/link'))return respond({status:'needs_independent_review'});
   }
   if(method==='GET'){
    if(pathname==='/api/intake/status')return respond({latest:null,scheduled_read_only:false});
    if(pathname==='/api/platform/capabilities')return respond(capabilities);
    if(pathname==='/api/state')return respond({csrf:'synthetic-isolated-csrf'});
    if(pathname==='/api/intake/candidates')return respond(paged(candidates));
    if(pathname==='/api/platform/reviews')return respond(paged(outcomes));
    if(pathname==='/api/readout-updates')return respond(paged(proposals));
    if(pathname==='/api/arena/public'){
     const failed=arenaFailure,data=clone(arena);
     if(holdNextArena){holdNextArena=false;await new Promise(resolve=>arenaRelease=resolve);}
     return respond(failed?{detail:'SYNTHETIC target source unavailable'}:data,failed?503:200);
    }
    const file=pathname==='/reviews'?'static/reviews.html':pathname.slice(1);
    if(file.startsWith('static/')&&fs.existsSync(path.join(root,file))){const contentType=file.endsWith('.html')?'text/html':file.endsWith('.js')?'application/javascript':file.endsWith('.css')?'text/css':'image/svg+xml';return route.fulfill({contentType,body:fs.readFileSync(path.join(root,file))});}
   }
   unexpected.push(method+' '+pathname);return route.abort();
  });
  async function openSource(id){await page.locator(`[data-readout-propose="${id}"]`).click();await page.waitForFunction(()=>document.getElementById('readout-arena-status').textContent.includes('找到同 NCT'));}
  async function fields(quote=quoteExact){await page.fill('#readout-label','SYNTHETIC timing for offline UI test');await page.fill('#readout-quote',quote);await page.fill('#readout-reason','SYNTHETIC independent source wording is retained for review.');}
  async function save(){await page.click('#readout-propose-submit');await page.waitForFunction(()=>document.getElementById('readout-propose-feedback').textContent.includes('已保存'));}
  async function untilHeld(kind){for(let i=0;i<100&&!(kind==='arena'?arenaRelease:postRelease);i++)await new Promise(resolve=>setTimeout(resolve,10));assert(kind==='arena'?arenaRelease:postRelease,'Expected controlled pending '+kind+' request');}
  function release(kind){const resume=kind==='arena'?arenaRelease:postRelease;if(kind==='arena')arenaRelease=null;else postRelease=null;resume();}
  async function switchSource(id){await page.evaluate(value=>document.querySelector(`[data-readout-propose="${value}"]`).click(),id);}
  const defaultKeys=['readout_not_completion','source_quote_supports_date','trial_identity_confirmed','primary_source_checked','precision_correct'];
  await page.goto(origin+'/reviews');await page.waitForSelector('#review-content:not([hidden])');
  for(const group of ['intake','outcome','readout']){
   await page.waitForFunction(id=>document.getElementById(id+'-list').children.length===20,group);
   await page.click('#'+group+'-more');await page.waitForFunction(id=>document.getElementById(id+'-list').children.length===25,group);
   assert(await page.locator('#'+group+'-more').isHidden());
  }
  checks.push('Three actual paginated lists reach all 25 SYNTHETIC records');
  await openSource('source-20');
  assert.equal(await page.inputValue('#readout-arena-target'),'');assert.equal(await page.inputValue('#readout-start'),'');assert(await page.locator('#readout-end-label').isHidden());
  assert(await page.locator('#readout-kind option[value="readout_time_unconfirmed"]').evaluate(e=>e.disabled));
  assert((await page.locator('#readout-source-detail').innerText()).includes(quoteExact));
  assert.deepStrictEqual(await page.locator('#readout-arena-target option').evaluateAll(es=>es.map(e=>e.value)),['','formal-pfs','formal-os']);
  checks.push('Only same-NCT active current formal targets appear; pilot, earlier and withdrawn records are excluded and none is auto-selected');
  await page.fill('#readout-start','2027-05-12');await fields();await save();
  let last=calls.at(-1);assert.equal(last.body.date_kind,'official_exact_date');assert.equal(last.body.start,last.body.end);assert.equal(last.body.precision,'day');
  for(const key of ['source','arena_target_id','source_granularity','applicability','arena_target_binding','arena_target_binding_sha256'])assert(!(key in last.body));
  checks.push('Legacy exact-date payload stays unchanged and sends neither source provenance nor Arena fields');
  await page.click('#close-readout-propose');await openSource('source-21');await page.selectOption('#readout-kind','estimated_window');
  assert.equal(await page.inputValue('#readout-start'),'');assert.equal(await page.inputValue('#readout-end'),'');
  await page.fill('#readout-start','2027-01-01');await page.fill('#readout-end','2026-06-30');await page.selectOption('#readout-precision','half_year');await fields(quoteWindow);
  const before=calls.length;await page.click('#readout-propose-submit');assert.equal(calls.length,before);assert((await page.locator('#readout-propose-feedback').innerText()).includes('不能早于'));
  await page.fill('#readout-end','2027-06-30');await save();last=calls.at(-1);assert.equal(last.body.date_kind,'estimated_window');assert.equal(last.body.precision,'half_year');assert.equal(last.body.end,'2027-06-30');
  checks.push('Legacy half-year contract, blank dates and reversed-window prevention remain intact');
  await page.click('#close-readout-propose');await page.click('[data-readout-review="created-2"]');
  assert(await page.locator('#readout-review-submit').isDisabled());assert((await page.locator('#readout-review-feedback').innerText()).includes('提交者不可自审'));
  const selfBefore=calls.length;await page.evaluate(()=>document.getElementById('readout-review-form').dispatchEvent(new Event('submit',{cancelable:true,bubbles:true})));assert.equal(calls.length,selfBefore);
  await page.screenshot({path:path.join(output,'self-review-blocked.png')});await page.click('#close-readout-review');
  checks.push('Existing submitting-account self-review gate is preserved even for a forced form event');
  await page.click('[data-readout-review="readout-1"]');assert.equal(await page.locator('#readout-confirmations input').count(),5);assert((await page.locator('#readout-review-detail').innerText()).includes('旧版 NCT'));
  await page.selectOption('#readout-decision','approved');await page.fill('#readout-review-reason','SYNTHETIC independent source identity and precision review.');for(const key of defaultKeys)await page.check(`#readout-confirmations [name="${key}"]`);
  await page.click('#readout-review-submit');await page.waitForFunction(()=>document.getElementById('readout-review-feedback').textContent.includes('已保存'));
  last=calls.at(-1);assert.equal(last.pathname,'/api/readout-updates/readout-1/review');assert.equal(Object.keys(last.body.confirmations).length,5);assert(Object.values(last.body.confirmations).every(Boolean));assert(await page.locator('#readout-review-submit').isDisabled());await page.click('#close-readout-review');
  checks.push('Legacy independent approval retains exactly the existing five confirmation keys and immutable UI state');
  await openSource('source-20');await page.selectOption('#readout-arena-target','formal-pfs');
  assert((await page.locator('#readout-arena-reference').innerText()).includes(firstTarget.endpoint));assert.equal(await page.locator('#readout-arena-reference img').count(),0);
  await page.selectOption('#readout-source-granularity','named_trial');await page.fill('#readout-start','2027-05-12');await fields();await save();
  last=calls.at(-1);assert.equal(last.body.arena_target_id,'formal-pfs');assert.equal(last.body.source_granularity,'named_trial');assert.equal(last.body.applicability,'exact_registered_target');assert(!('arena_target_binding' in last.body));assert(!('arena_target_binding_sha256' in last.body));
  checks.push('Explicit Arena selection shows exact escaped target and sends only target ID, source granularity and applicability; server owns binding and hash');
  await page.click('#close-readout-propose');await openSource('source-23');await page.selectOption('#readout-arena-target','formal-pfs');await page.selectOption('#readout-source-granularity','exact_analysis');
  await page.fill('#readout-start','2027-04-02');await page.selectOption('#readout-kind','readout_time_unconfirmed');
  assert.equal(await page.inputValue('#readout-start'),'');assert.equal(await page.inputValue('#readout-end'),'');assert(await page.locator('#readout-start').isDisabled());assert(await page.locator('#readout-end').isDisabled());assert.equal(await page.inputValue('#readout-precision'),'unknown');
  assert.equal(await page.locator('#readout-start').getAttribute('required'),null);assert.equal(await page.locator('#readout-end').getAttribute('required'),null);
  await fields(quoteUnknown);await save();last=calls.at(-1);assert.equal(last.body.start,null);assert.equal(last.body.end,null);assert.equal(last.body.precision,'unknown');assert.equal(last.body.source_granularity,'exact_analysis');
  checks.push('Unknown Arena readout clears/disables dates, removes required and submits null dates with unknown precision');
  await page.click('#close-readout-propose');await openSource('source-23');await page.selectOption('#readout-arena-target','formal-pfs');await page.selectOption('#readout-kind','readout_time_unconfirmed');await page.selectOption('#readout-arena-target','');
  assert.equal(await page.inputValue('#readout-kind'),'estimated_window');assert(await page.locator('#readout-kind option[value="readout_time_unconfirmed"]').evaluate(e=>e.disabled));assert.equal(await page.inputValue('#readout-start'),'');assert.notEqual(await page.locator('#readout-start').getAttribute('required'),null);
  const unknownBefore=calls.length;await page.evaluate(()=>{document.getElementById('readout-kind').value='readout_time_unconfirmed';document.getElementById('readout-propose-form').dispatchEvent(new Event('submit',{cancelable:true,bubbles:true}));});assert.equal(calls.length,unknownBefore);assert((await page.locator('#readout-propose-feedback').innerText()).includes('只有显式选择'));
  checks.push('Returning to legacy removes unknown mode; forced unknown-without-Arena cannot POST');await page.click('#close-readout-propose');
  await page.click('[data-readout-review="readout-2"]');assert.equal(await page.locator('#readout-confirmations input').count(),6);
  const detail=await page.locator('#readout-review-detail').innerText();for(const value of [firstTarget.id,firstTarget.endpoint,firstTarget.population,firstTarget.comparison,firstTarget.success_definition,firstTarget.analysis_timepoint,'同名试验指引','exact_registered_target','b'.repeat(64)])assert(detail.includes(value));
  await page.locator('#readout-review-dialog').evaluate(e=>e.scrollTop=0);await page.screenshot({path:path.join(output,'arena-exact-target-binding.png')});
  await page.selectOption('#readout-decision','approved');await page.fill('#readout-review-reason','SYNTHETIC exact target applicability was independently checked.');for(const key of defaultKeys)await page.check(`#readout-confirmations [name="${key}"]`);
  const approvalBefore=calls.length;await page.click('#readout-review-submit');assert.equal(calls.length,approvalBefore);assert((await page.locator('#readout-review-feedback').innerText()).includes('精确目标'));
  await page.check('#readout-confirmations [name="exact_target_applicability_confirmed"]');await page.click('#readout-review-submit');await page.waitForFunction(()=>document.getElementById('readout-review-feedback').textContent.includes('已保存'));await page.screenshot({path:path.join(output,'arena-exact-target-review.png')});
  last=calls.at(-1);assert.equal(Object.keys(last.body.confirmations).length,6);assert.equal(last.body.confirmations.exact_target_applicability_confirmed,true);await page.click('#close-readout-review');
  checks.push('Arena review exposes server binding and scope and requires the sixth explicit exact-applicability confirmation');
  await page.click('[data-readout-review="readout-3"]');assert((await page.locator('#readout-review-detail').innerText()).includes('未确认；未填日期'));assert(!(await page.locator('#readout-review-detail').innerText()).includes('null'));assert((await page.locator('#readout-confirmations').innerText()).includes('未检到公告'));assert.equal(await page.locator('#readout-confirmations input').count(),6);await page.click('#close-readout-review');
  checks.push('Unknown-date review retains empty dates and explicitly separates source-confirmed uncertainty from search silence');
  arenaFailure=true;await page.click('[data-readout-propose="source-21"]');await page.waitForFunction(()=>document.getElementById('readout-arena-status').textContent.includes('读取失败'));
  assert(await page.locator('#readout-arena-target').isDisabled());assert.equal(await page.inputValue('#readout-arena-target'),'');assert.equal(await page.locator('#readout-arena-target option').count(),1);assert(await page.locator('#readout-arena-scope').isHidden());
  await page.fill('#readout-start','2027-05-12');await fields();
  const failedBefore=calls.length;await page.evaluate(()=>{const option=new Option('SYNTHETIC stale choice','formal-pfs');document.getElementById('readout-arena-target').add(option);document.getElementById('readout-arena-target').value='formal-pfs';document.getElementById('readout-propose-form').dispatchEvent(new Event('submit',{cancelable:true,bubbles:true}));});assert.equal(calls.length,failedBefore);assert((await page.locator('#readout-propose-feedback').innerText()).includes('目标未载入'));
  checks.push('Public target-load failure clears prior selections and even a forced stale choice cannot POST');await page.click('#close-readout-propose');arenaFailure=false;
  holdNextArena=true;await page.click('[data-readout-propose="source-20"]');await untilHeld('arena');await switchSource('source-22');await page.waitForFunction(()=>document.getElementById('readout-nct').value==='NCT76543210'&&!document.getElementById('readout-arena-target').disabled);
  assert.deepStrictEqual(await page.locator('#readout-arena-target option').evaluateAll(es=>es.map(e=>e.value)),['','formal-other']);release('arena');await page.waitForTimeout(40);
  assert.equal(await page.inputValue('#readout-nct'),'NCT76543210');assert.deepStrictEqual(await page.locator('#readout-arena-target option').evaluateAll(es=>es.map(e=>e.value)),['','formal-other']);
  await page.selectOption('#readout-arena-target','formal-other');await page.selectOption('#readout-source-granularity','exact_analysis');await page.fill('#readout-start','2027-05-12');await fields(quoteExact.replace('NCT01234567','NCT76543210'));await save();last=calls.at(-1);assert.equal(last.body.candidate_id,'source-22');assert.equal(last.body.nct_id,'NCT76543210');assert.equal(last.body.arena_target_id,'formal-other');
  checks.push('Late target fetch for source A cannot overwrite source B or attach the old NCT/target to B');await page.click('#close-readout-propose');
  await openSource('source-20');await page.selectOption('#readout-arena-target','formal-pfs');await page.selectOption('#readout-source-granularity','named_trial');await page.fill('#readout-start','2027-05-12');await fields();holdNextPost=true;await page.click('#readout-propose-submit');await untilHeld('post');
  assert(await page.locator('#readout-arena-target').isDisabled());assert(await page.locator('#readout-kind').isDisabled());
  await switchSource('source-22');await page.waitForFunction(()=>document.getElementById('readout-nct').value==='NCT76543210'&&!document.getElementById('readout-arena-target').disabled);release('post');await page.waitForTimeout(40);
  assert.equal(await page.inputValue('#readout-arena-target'),'');assert.equal(await page.inputValue('#readout-start'),'');assert(!(await page.locator('#readout-propose-feedback').innerText()).includes('已保存'));assert(!(await page.locator('#readout-propose-submit').isDisabled()));
  checks.push('Late submit acknowledgement for A does not mark B saved, restore A fields or lock B form');await page.click('#close-readout-propose');
  await page.click('[data-readout-review="readout-4"]');await page.selectOption('#readout-decision','rejected');await page.fill('#readout-review-reason','SYNTHETIC first source review pending transport acknowledgement.');holdNextPost=true;await page.click('#readout-review-submit');await untilHeld('post');
  await page.evaluate(()=>document.querySelector('[data-readout-review="readout-5"]').click());release('post');await page.waitForTimeout(40);assert(!(await page.locator('#readout-review-submit').isDisabled()));assert.equal(await page.inputValue('#readout-review-reason'),'');assert(!(await page.locator('#readout-review-feedback').innerText()).includes('已保存'));await page.click('#close-readout-review');
  checks.push('Late review acknowledgement cannot approve, disable or mark a different selected review saved');
  await page.click('[data-review="outcome-21"]');await page.fill('#reason','SYNTHETIC existing result review remains independently accessible.');await page.selectOption('#decision','needs_review');await page.click('#review-form button[type="submit"]');await page.waitForFunction(()=>document.getElementById('review-feedback').textContent.includes('已保存'));assert.equal(calls.at(-1).pathname,'/api/platform/reviews/outcome-21');await page.click('#close-review');
  await page.click('[data-link="source-0"]');await page.fill('#link-snapshot','synthetic-snapshot');await page.fill('#link-endpoint','SYNTHETIC primary endpoint');await page.fill('#link-population','SYNTHETIC ITT');await page.fill('#link-comparison','SYNTHETIC drug vs placebo');await page.fill('#link-date','2026-10-20');await page.selectOption('#link-label','1');await page.click('#link-form button');await page.waitForFunction(()=>document.getElementById('link-feedback').textContent.includes('已保存'));assert.equal(calls.at(-1).pathname,'/api/intake/candidates/source-0/link');await page.click('#close-link');
  assert(calls.every(c=>c.csrf==='synthetic-isolated-csrf'));checks.push('Existing outcome review, source link and authenticated CSRF headers remain intact');
  await page.setViewportSize({width:390,height:844});await openSource('source-23');await page.selectOption('#readout-arena-target','formal-pfs');await page.selectOption('#readout-source-granularity','named_trial');await page.selectOption('#readout-kind','readout_time_unconfirmed');
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));assert(await page.locator('#readout-propose-dialog').evaluate(e=>e.scrollWidth<=e.clientWidth+1));await page.screenshot({path:path.join(output,'arena-readout-proposal-mobile.png')});await page.locator('#readout-kind').scrollIntoViewIfNeeded();await page.screenshot({path:path.join(output,'arena-unknown-date-mobile.png')});await page.click('#close-readout-propose');checks.push('Mobile exact-target unknown-date form remains inside page and dialog bounds');
  capabilities={authenticated:false,reviewer:false,reviewer_configured:true};await page.goto(origin+'/reviews');await page.waitForFunction(()=>document.getElementById('review-status').textContent.includes('登录'));assert(await page.locator('#review-content').isHidden());
  capabilities={authenticated:true,reviewer:false,reviewer_configured:true};await page.goto(origin+'/reviews');await page.waitForFunction(()=>document.getElementById('review-status').textContent.includes('没有独立审核权限'));assert(await page.locator('#review-content').isHidden());checks.push('Unauthenticated and unconfigured reviewer access stays closed');
  assert.deepStrictEqual(errors,[]);assert.deepStrictEqual(unexpected,[]);
  const receipt={status:'passed',tested_at_utc:new Date().toISOString(),test_groups:checks.length,checks,page_errors:errors,unexpected_requests:unexpected,post_calls:calls.map(c=>({path:c.pathname,date_kind:c.body.date_kind,arena_target_id:c.body.arena_target_id||null})),scope:'Actual shipped assets; fully intercepted SYNTHETIC transport. No live credentials, source acquisition, model calls, original records or production ledger writes.'};
  fs.writeFileSync(path.join(output,'receipt.json'),JSON.stringify(receipt,null,2));console.log(JSON.stringify({status:receipt.status,checks:checks.length,page_errors:errors,unexpected_requests:unexpected}));
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
