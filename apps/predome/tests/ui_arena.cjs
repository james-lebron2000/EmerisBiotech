'use strict';
// UI contract fixtures stay in tests. Optional live mode is read-only and
// deliberately saves screenshots separately from fixture verification receipts.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const fs=require('fs'),path=require('path'),assert=require('assert');
const root=path.resolve(__dirname,'..'),out=path.join(root,'verification/arena-ui');
const clone=x=>JSON.parse(JSON.stringify(x));
const hash='a'.repeat(64);
const model={id:'fixture-model',name:'Isolated Test Model',provider:'test-only',model_version:'fixture-v1',available:false,reason:'credential_missing'};
const target={id:'fixture-target',trial_name:'Isolated contract test',nct_id:'NCT00000000',drug:'Fixture compound',sponsor:'Fixture sponsor',indication:'Fixture condition',project_id:'fixture-project',status:'pending',endpoint:'Endpoint A',population:'Registered population',comparison:'A versus B',success_definition:'Registered primary endpoint',data_cutoff:'2026-01-01T00:00:00Z',readout:{label:'2027 H1',start:'2027-01-01',end:'2027-06-30',precision:'half_year',source_url:'https://example.org/fixture-window',source_title:'Isolated fixture source'}};
const run={id:'fixture-predicted',target_id:target.id,model_id:model.id,track:'same_evidence',condition:'clinical_only',scope:'prospective_challenge',status:'predicted',probability:.6,reason:'Fixture reasoning',supporting_evidence:[{text:'Evidence text <img src=x onerror=alert(1)>',source_id:'e1',source_url:'javascript:alert(1)'}],counter_evidence:[],assumptions:['Assumption'],unknowns:['Unknown'],model_version:'fixture-v1',workflow_version:'fixture-workflow',input_sha256:hash,output_sha256:hash,generated_at_utc:'2026-01-02T00:00:00Z',locked_at_utc:'2026-01-02T00:01:00Z',timestamp_proof:{status:'server_record_only'},cost_amount:.01,cost_currency:'CNY',cost_basis:'configured_rates_estimate',latency_seconds:1};
const patent={id:'TEST20260001',publication_number:'TEST20260001',title:'Isolated test patent',publication_date:'2025-01-01',source_url:'https://example.org/fixture-patent',pdf_url:'https://example.org/fixture.pdf',sha256:hash,pages:2,drug:'Fixture compound',linkage_status:'candidate',review_status:'needs_review',included_in_predictions:false,extraction:{status:'full_ocr_completed_requires_scientific_review'},family:{id:null,status:'official_family_lookup_pending'},page_references:[{pdf_page_1_based:2,text_sha256:hash,source_url:'https://example.org/fixture.pdf#page=2',status:'machine_ocr_unreviewed'}],experiment_cards:[{title:'Fixture experiment',experiment_system:'Mouse',sample_size:{n:5},dose:{amount:2,unit:'mg/kg'},result:{value:3},unit:'mg',pdf_page_1_based:2,review:{status:'ai_page_visual_check_not_human_scientific_review'},eligible_for_forecast:false,missing_fields:['Human evidence'],source_excerpt:'Fixture excerpt <script>bad()</script>',interpretation_limit:'Not human evidence'}]};
const baseFixture={version:'isolated-ui-fixture',models:[model],targets:[target],runs:[run,{...run,id:'fixture-null',status:'abstained',probability:null,reason:'Insufficient evidence'},{...run,id:'fixture-failed',status:'failed',probability:.95,reason:'Failed request must not be a forecast'},{...run,id:'fixture-zero',probability:0},{...run,id:'fixture-old',track:'archived',scope:'archived_research',probability:.4}],patents:[patent],metrics:{assigned:4,answered:2,adjudicated:0,common_tasks:0,ranking_allowed:false,reason:'Fixture results pending',by_model:[{model_id:model.id,assigned:4,answered:2,abstained:1,failed:1,common_tasks:0,common_brier:.01,cost_usd:null}]},capabilities:{notes:['Isolated browser contract fixtures are not scientific validation.']}};
(async()=>{
 fs.mkdirSync(out,{recursive:true});
 const browser=await chromium.launch({headless:true,...(process.env.CHROMIUM_EXECUTABLE?{executablePath:process.env.CHROMIUM_EXECUTABLE}:{})});
 const checks=[],errors=[],apiRequests=[];let fixture=clone(baseFixture),apiFailed=false,patentFailed=false;
 const context=await browser.newContext({viewport:{width:1440,height:1000}}),page=await context.newPage();
 page.on('pageerror',e=>errors.push(e.message));
 const origin='http://predome-ui.test';
 await context.route('**/*',async route=>{
  const u=new URL(route.request().url());if(u.origin!==origin)return route.abort();
  if(u.pathname.startsWith('/api/arena')){apiRequests.push(u.pathname+u.search);
   if(apiFailed||patentFailed&&u.pathname.startsWith('/api/arena/patents/'))return route.fulfill({status:503,contentType:'application/json',body:'{"detail":"isolated unavailable"}'});
   const body=u.pathname.startsWith('/api/arena/targets/')?{target:fixture.targets[0],runs:fixture.runs,evidence:[],patents:fixture.patents,result:fixture.targets[0].result}:u.pathname.startsWith('/api/arena/patents/')?fixture.patents[0]:fixture;
   return route.fulfill({contentType:'application/json',body:JSON.stringify(body)});
  }
  const relative=u.pathname==='/arena'?'static/arena.html':u.pathname.slice(1),file=path.resolve(root,relative);
  if(!relative.startsWith('static/')||!file.startsWith(root+path.sep)||!fs.existsSync(file))return route.fulfill({status:404,body:''});
  return route.fulfill({body:fs.readFileSync(file),contentType:file.endsWith('.html')?'text/html':file.endsWith('.js')?'application/javascript':file.endsWith('.svg')?'image/svg+xml':'text/css'});
 });
 async function go(query=''){await page.goto(origin+'/arena'+query);await page.waitForFunction(()=>document.getElementById('arena-status').textContent.includes('公开记录已读取'));}
 async function noOverflow(label){assert(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+1),label);}
 try{
  await go();assert.strictEqual(await page.locator('.arena-target').count(),1);assert((await page.locator('.arena-window').innerText()).includes('半年'));assert((await page.locator('.arena-summary').innerText()).includes('4'));checks.push('Window precision and registered denominator survive rendering');
  await page.locator('#arena-search').fill('missing');assert.strictEqual(await page.locator('.arena-target').count(),0);assert((await page.locator('.arena-summary').innerText()).includes('4'));checks.push('Filtering hides targets without changing registered denominator');
  await go('?target='+target.id);
  assert.strictEqual(await page.locator('.arena-prob-row[data-run-id="fixture-null"] .arena-prob-dot').count(),0);
  assert.strictEqual(await page.locator('.arena-prob-row[data-run-id="fixture-failed"] .arena-prob-dot').count(),0);
  assert.strictEqual(await page.locator('.arena-prob-row[data-run-id="fixture-zero"] .arena-prob-value').innerText(),'0%');checks.push('Null abstention and failed status have no dot; legitimate zero remains zero');
  assert(await page.locator('.arena-prob-row[data-run-id="fixture-predicted"]').evaluate(el=>{const track=el.querySelector('.arena-prob-track').getBoundingClientRect(),dot=el.querySelector('.arena-prob-dot').getBoundingClientRect();return Math.abs(((dot.left+dot.width/2)-track.left)/track.width-.6)<.01;}));checks.push('Probability point position matches the displayed value without inline-style dependency');
  assert.strictEqual(await page.locator('#arena-body img, #arena-body script').count(),0);assert.strictEqual(await page.locator('#arena-body a[href^="javascript:"]').count(),0);assert((await page.locator('.arena-run').first().innerText()).includes('<img'));checks.push('Evidence is escaped and unsafe source links are rejected');
  assert((await page.locator('.arena-run').first().innerText()).includes('CNY 0.0100（按配置单价估计）'));await page.locator('.arena-run').first().locator('summary').click();assert((await page.locator('.arena-run').first().innerText()).includes('不等于外部时间公证'));checks.push('Native-currency estimated cost and absent external timestamp stay explicit');
  await page.locator('#run-track').selectOption('archived');assert.strictEqual(await page.locator('.arena-run').count(),1);assert((await page.locator('.arena-run').innerText()).includes('不计入新赛季'));checks.push('Archive filter retains old scope rather than presenting it as new competition');
  await go('?view=leaderboard');assert.strictEqual(await page.locator('.arena-score').innerText(),'—\n尚无共同裁定结果');checks.push('Even a supplied score is hidden before common adjudicated results exist');
  await page.locator('select[name=track]').selectOption('shared_agent');await page.locator('form button[type=submit]').click();await page.waitForFunction(()=>document.getElementById('arena-status').textContent.includes('公开记录已读取'));assert(apiRequests.some(u=>u.includes('track=shared_agent')));checks.push('Comparison form requests a separate track from the API rather than pooling scores');
  fixture.targets[0]={...target,status:'announced',result:{label:'success',first_public_at:'2026-02-01T00:00:00Z'}};await go('?target='+target.id);assert((await page.locator('.arena-result').innerText()).includes('独立裁定完成前不进入正式成绩'));assert(!(await page.locator('.arena-result').innerText()).includes('达到预设标准'));checks.push('Announced outcomes remain unscored before adjudication');
  fixture.metrics={...fixture.metrics,adjudicated:1,common_tasks:1,by_model:[{...fixture.metrics.by_model[0],common_tasks:1,common_brier:.16}]};fixture.targets[0]={...target,status:'adjudicated',result:{label:'failure',first_public_at:'2026-02-01T00:00:00Z',source_url:'https://example.org/result'}};
  await go('?view=leaderboard');assert((await page.locator('.arena-score').innerText()).startsWith('0.160'));assert((await page.locator('.arena-result').innerText()).includes('未达到预设标准'));assert((await page.locator('.arena-result').innerText()).includes('2026-02-01'));checks.push('Adjudicated common-set score and actual outcome display with source date');
  fixture=clone(baseFixture);fixture.runs.push({...run,id:'fixture-patent',condition:'with_patents',track:'patent_ablation',probability:.7,paired_run_id:run.id,pair_verified:false});
  await go('?target='+target.id);assert((await page.locator('.arena-table').innerText()).includes('待核验 / 不可比较'));
  fixture.runs.at(-1).pair_verified=true;await go('?target='+target.id);assert((await page.locator('.arena-table').innerText()).includes('+10.0 个百分点'));assert((await page.locator('.arena-table').innerText()).includes('不是疗效或预测改善结论'));checks.push('Unverified patent pairs do not compute change; verified change is not called improvement');
  await go('?view=patents');await page.locator('[data-load-patent]').click();await page.waitForFunction(()=>document.querySelector('[data-load-patent]').getAttribute('aria-expanded')==='true');const patentText=await page.locator('.arena-patent-detail').innerText();assert(patentText.includes('"amount":2'));assert(patentText.includes('未经人工科学审核'));assert(!patentText.includes('[object Object]'));assert.strictEqual(await page.locator('.arena-patent-detail script').count(),0);checks.push('Patent experiment objects, source pages and independent review state render without invented fields');
  patentFailed=true;await go('?view=patents');await page.locator('[data-load-patent]').click();await page.waitForFunction(()=>document.querySelector('.arena-patent-detail').textContent.includes('无法读取'));assert.strictEqual(await page.locator('[data-load-patent]').isEnabled(),true);patentFailed=false;checks.push('Patent detail failure remains visible and retryable');
  apiFailed=true;await page.goto(origin+'/arena');await page.locator('#arena-retry').waitFor();assert.strictEqual(await page.locator('.arena-prob-dot').count(),0);apiFailed=false;await page.locator('#arena-retry').click();await page.waitForFunction(()=>document.getElementById('arena-status').textContent.includes('公开记录已读取'));checks.push('API failure produces no probabilities and successful retry recovers');
  await page.setViewportSize({width:375,height:812});for(const q of ['', '?target='+target.id,'?view=leaderboard','?view=patents','?view=methodology']){await go(q);await noOverflow('Mobile overflow at '+q);}
  await page.locator('#theme-toggle').click();assert.strictEqual(await page.locator('html').getAttribute('data-theme'),'dark');checks.push('All five mobile views fit the viewport and theme control works');
  assert.deepStrictEqual(errors,[]);
  fs.writeFileSync(path.join(out,'fixture-receipt.json'),JSON.stringify({at:new Date().toISOString(),scope:'Isolated UI contract tests only; not real model forecasts or clinical validation',checks,errors},null,2));
  await context.close();
  if(process.env.ARENA_LIVE_BASE){
   const liveBase=process.env.ARENA_LIVE_BASE.replace(/\/$/,''),live=await browser.newContext({viewport:{width:1440,height:1000},ignoreHTTPSErrors:process.env.ARENA_IGNORE_TLS_ERRORS==='true'}),p=await live.newPage(),liveErrors=[],liveChecks=[];
   p.on('pageerror',e=>liveErrors.push(e.message));
   const response=await live.request.get(liveBase+'/api/arena');assert(response.ok());const actual=await response.json();
   const first=actual.targets?.[0];
   for(const [name,query] of [['overview',''],['detail',first?'?target='+encodeURIComponent(first.id):''],['leaderboard','?view=leaderboard'],['patents','?view=patents'],['methodology','?view=methodology']]){
    await p.goto(liveBase+'/arena'+query);await p.waitForFunction(()=>document.getElementById('arena-status').textContent.includes('公开记录已读取'));
    assert(!(await p.locator('#arena-body').innerText()).includes('[object Object]'));
    for(const dot of await p.locator('.arena-prob-dot').all()){assert(await dot.evaluate(el=>{const track=el.closest('.arena-prob-track').getBoundingClientRect(),box=el.getBoundingClientRect();return Math.abs(((box.left+box.width/2)-track.left)/track.width-parseFloat(el.getAttribute('cx'))/100)<.01;}),'Live probability point displaced under CSP');}
    if(name==='patents'&&actual.patents?.length){await p.locator('[data-load-patent]').first().click();await p.waitForFunction(()=>document.querySelector('[data-load-patent]').getAttribute('aria-expanded')==='true');assert(!(await p.locator('.arena-patent-detail').first().innerText()).includes('[object Object]'));}
    await p.evaluate(()=>window.scrollTo(0,0));await p.screenshot({path:path.join(out,'live-'+name+'-desktop.png'),fullPage:true});
    await p.setViewportSize({width:375,height:812});await p.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));assert(await p.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+1),'Live mobile overflow '+name+' '+JSON.stringify(await p.evaluate(()=>Array.from(document.querySelectorAll('body *')).filter(el=>el.getBoundingClientRect().right>innerWidth+1).slice(0,12).map(el=>({tag:el.tagName,cls:el.className,width:el.getBoundingClientRect().width,text:el.textContent.slice(0,70)})))));await p.screenshot({path:path.join(out,'live-'+name+'-mobile.png'),fullPage:true});await p.setViewportSize({width:1440,height:1000});liveChecks.push(name+' loaded from actual API; no object-string rendering or mobile overflow');
   }
   assert.deepStrictEqual(liveErrors,[]);fs.writeFileSync(path.join(out,'live-receipt.json'),JSON.stringify({at:new Date().toISOString(),base:liveBase,version:actual.version,scope:'Read-only live backend rendering. No provider execution, writes, or validation claims.',targets:actual.targets?.length,runs:actual.runs?.length,patents:actual.patents?.length,checks:liveChecks,errors:liveErrors},null,2));await live.close();
  }
  console.log(JSON.stringify({fixture_checks:checks.length,errors,live_read_only:!!process.env.ARENA_LIVE_BASE,receipts:out}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
