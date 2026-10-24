'use strict';
// Authorized lab UI regression. Fixtures never enter production.
// Optional live mode consumes an existing operator-supplied storageState read-only;
// it never registers users, calls models, prints credentials, or exports cookies.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const fs=require('fs'),path=require('path'),assert=require('assert');
const root=path.resolve(__dirname,'..'),out=path.join(root,'verification/arena-lab-ui');
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
 const checks=[],errors=[],apiRequests=[],requestMethods=[];let fixture=clone(baseFixture),apiFailed=false,patentFailed=false,apiFailureStatus=503;
 const context=await browser.newContext({viewport:{width:1440,height:1000}}),page=await context.newPage();
 page.on('pageerror',e=>errors.push(e.message));
 const origin='http://predome-ui.test';
 await context.route('**/*',async route=>{
  requestMethods.push(route.request().method());const u=new URL(route.request().url());if(u.origin!==origin)return route.abort();if(route.request().method()!=='GET')return route.abort();
  if(u.pathname.startsWith('/api/arena')){apiRequests.push(u.pathname+u.search);
   if(apiFailed||patentFailed&&u.pathname.startsWith('/api/arena/patents/'))return route.fulfill({status:apiFailureStatus,contentType:'application/json',body:'{"detail":"isolated unavailable or forbidden"}'});
   if(u.pathname==='/api/arena/ledger')return route.fulfill({contentType:'application/json',headers:{'Content-Disposition':'attachment; filename=isolated-ledger.json'},body:JSON.stringify({runs:fixture.runs,models:fixture.models,scope:'Isolated downloadable metadata fixture'})});
   const packetMatch=u.pathname.match(/^\/api\/arena\/runs\/([^/]+)\/packet$/);if(packetMatch){const r=fixture.runs.find(x=>x.id===decodeURIComponent(packetMatch[1]));return route.fulfill({status:r?200:404,contentType:'application/json',headers:{'Content-Disposition':'attachment; filename=isolated-packet.json'},body:JSON.stringify(r?{run_id:r.id,input_sha256:r.input_sha256,packet:r.packet||{evidence:[]}}:{detail:'Unknown fixture run'})});}
   if(!['/api/arena/lab/overview'].includes(u.pathname)&&!u.pathname.startsWith('/api/arena/lab/targets/')&&!u.pathname.startsWith('/api/arena/patents/'))return route.fulfill({status:404,contentType:'application/json',body:'{"detail":"Unexpected fixture API route"}'});
   const body=u.pathname.startsWith('/api/arena/lab/targets/')?{target:fixture.targets[0],runs:fixture.runs,evidence:[],patents:fixture.patents,result:fixture.targets[0].result}:u.pathname.startsWith('/api/arena/patents/')?fixture.patents[0]:fixture;
   return route.fulfill({contentType:'application/json',body:JSON.stringify(body)});
  }
  const relative=u.pathname==='/arena/lab'?'static/arena-lab.html':u.pathname.slice(1),file=path.resolve(root,relative);
  if(!relative.startsWith('static/')||!file.startsWith(root+path.sep)||!fs.existsSync(file))return route.fulfill({status:404,body:''});
  return route.fulfill({body:fs.readFileSync(file),contentType:file.endsWith('.html')?'text/html':file.endsWith('.js')?'application/javascript':file.endsWith('.svg')?'image/svg+xml':'text/css'});
 });
 async function go(query=''){await page.goto(origin+'/arena/lab'+query);await page.waitForFunction(()=>document.getElementById('arena-status').textContent.includes('内部调试记录已读取'));}
 async function noOverflow(label){assert(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+1),label);}
 async function downloadJson(locator){const [download]=await Promise.all([page.waitForEvent('download'),locator.click()]);assert.strictEqual(await download.failure(),null);return JSON.parse(fs.readFileSync(await download.path(),'utf8'));}
 async function noTransportBrand(label){assert(!/openai_compatible|Semantix|ensureok\.ai/i.test(await page.locator('body').innerText()),label);}
 try{
  await go();assert.strictEqual(await page.locator('.arena-target').count(),1);assert((await page.locator('.arena-window').innerText()).includes('半年'));assert((await page.locator('.arena-summary').innerText()).includes('4'));checks.push('Window precision and registered denominator survive rendering');
  assert(apiRequests.includes('/api/arena/lab/overview'));
  assert.strictEqual(await page.locator('.arena-target h3 a').getAttribute('href'),'/arena/lab?target='+target.id);
  assert.strictEqual(await page.locator('h1').innerText(),'内部测试与调试');
  assert((await page.locator('[aria-label="内部调试范围"]').innerText()).includes('只读'));
  assert((await page.locator('[aria-label="内部调试范围"]').innerText()).includes('operator CLI'));
  assert.strictEqual(await page.getByRole('link',{name:'查看公开研究视图'}).getAttribute('href'),'https://8.148.145.28/arena');
  assert.strictEqual(await page.getByRole('link',{name:'账户与登录'}).getAttribute('href'),'https://8.148.145.28/data');
  assert((await page.locator('.arena-tabs [data-view]').evaluateAll(xs=>xs.map(x=>x.getAttribute('href')))).every(h=>h.startsWith('/arena/lab')));
  assert.strictEqual(await page.locator('.ledger-download').getAttribute('href'),'/api/arena/ledger');
  checks.push('Lab context and internal navigation are explicit; public-view/login links leave the internal port while ledger keeps its audit download path');
  await page.locator('#arena-search').fill('missing');assert.strictEqual(await page.locator('.arena-target').count(),0);assert((await page.locator('.arena-summary').innerText()).includes('4'));checks.push('Filtering hides targets without changing registered denominator');
  await go('?target='+target.id);
  assert(apiRequests.includes('/api/arena/lab/targets/'+target.id));assert.strictEqual(await page.getByRole('link',{name:'← 返回全部目标'}).getAttribute('href'),'/arena/lab');assert.strictEqual(await page.getByRole('link',{name:'下载中文研究报告'}).getAttribute('href'),'/api/arena/targets/'+target.id+'/report');
  assert.strictEqual(await page.locator('.arena-prob-row[data-run-id="fixture-null"] .arena-prob-dot').count(),0);
  assert.strictEqual(await page.locator('.arena-prob-row[data-run-id="fixture-failed"] .arena-prob-dot').count(),0);
  assert.strictEqual(await page.locator('.arena-prob-row[data-run-id="fixture-zero"] .arena-prob-value').innerText(),'0%');checks.push('Null abstention and failed status have no dot; legitimate zero remains zero');
  assert(await page.locator('.arena-prob-row[data-run-id="fixture-predicted"]').evaluate(el=>{const track=el.querySelector('.arena-prob-track').getBoundingClientRect(),dot=el.querySelector('.arena-prob-dot').getBoundingClientRect();return Math.abs(((dot.left+dot.width/2)-track.left)/track.width-.6)<.01;}));checks.push('Probability point position matches the displayed value without inline-style dependency');
  assert.strictEqual(await page.locator('#arena-body img, #arena-body script').count(),0);assert.strictEqual(await page.locator('#arena-body a[href^="javascript:"]').count(),0);assert((await page.locator('.arena-run').first().innerText()).includes('<img'));checks.push('Evidence is escaped and unsafe source links are rejected');
  assert((await page.locator('.arena-run').first().innerText()).includes('CNY 0.0100（按配置单价估计）'));await page.locator('.arena-run').first().locator('summary').click();assert((await page.locator('.arena-run').first().innerText()).includes('不等于外部时间公证'));checks.push('Native-currency estimated cost and absent external timestamp stay explicit');
  await page.locator('#run-track').selectOption('archived');assert.strictEqual(await page.locator('.arena-run').count(),1);assert((await page.locator('.arena-run').innerText()).includes('不计入新赛季'));checks.push('Archive filter retains old scope rather than presenting it as new competition');
  await go('?view=leaderboard');assert.strictEqual(await page.locator('.arena-score').innerText(),'—\n尚无共同裁定结果');checks.push('Even a supplied score is hidden before common adjudicated results exist');
  await page.locator('select[name=track]').selectOption('shared_agent');await page.locator('form button[type=submit]').click();await page.waitForFunction(()=>document.getElementById('arena-status').textContent.includes('内部调试记录已读取'));assert(apiRequests.some(u=>u.startsWith('/api/arena/lab/overview?')&&u.includes('track=shared_agent')));assert.strictEqual(new URL(page.url()).pathname,'/arena/lab');assert.strictEqual(await page.locator('form').getAttribute('action'),'/arena/lab');checks.push('Comparison form requests a separate track from the API rather than pooling scores');
  fixture.targets[0]={...target,status:'announced',result:{label:'success',first_public_at:'2026-02-01T00:00:00Z'}};await go('?target='+target.id);assert((await page.locator('.arena-result').innerText()).includes('独立裁定完成前不进入正式成绩'));assert(!(await page.locator('.arena-result').innerText()).includes('达到预设标准'));checks.push('Announced outcomes remain unscored before adjudication');
  fixture.metrics={...fixture.metrics,adjudicated:1,common_tasks:1,by_model:[{...fixture.metrics.by_model[0],common_tasks:1,common_brier:.16}]};fixture.targets[0]={...target,status:'adjudicated',result:{label:'failure',first_public_at:'2026-02-01T00:00:00Z',source_url:'https://example.org/result'}};
  await go('?view=leaderboard');assert((await page.locator('.arena-score').innerText()).startsWith('0.160'));assert((await page.locator('.arena-result').innerText()).includes('未达到预设标准'));assert((await page.locator('.arena-result').innerText()).includes('2026-02-01'));checks.push('Adjudicated common-set score and actual outcome display with source date');
  fixture=clone(baseFixture);fixture.targets[0]={...target,status:'announced',result:{label:'mixed',verification_status:'conflicting_reviews',review_provenance:{basis:'ai_only',review_counts:{ai:2,human:0,unspecified:0},typed_conflict:true}}};await go('?target='+target.id);const reviewText=await page.locator('.arena-result').innerText();assert(reviewText.includes('AI 2'));assert(reviewText.includes('人工 0'));assert(reviewText.includes('未由系统独立认证'));checks.push('Internal result provenance distinguishes declared AI reviews from unverified human identities');
  fixture=clone(baseFixture);fixture.runs.push({...run,id:'fixture-patent',condition:'with_patents',track:'patent_ablation',probability:.7,paired_run_id:run.id,pair_verified:false});
  await go('?target='+target.id);assert((await page.locator('.arena-table').innerText()).includes('待核验 / 不可比较'));
  fixture.runs.at(-1).pair_verified=true;await go('?target='+target.id);assert((await page.locator('.arena-table').innerText()).includes('+10.0 个百分点'));assert((await page.locator('.arena-table').innerText()).includes('不是疗效或预测改善结论'));checks.push('Unverified patent pairs do not compute change; verified change is not called improvement');
  await go('?view=patents');await page.locator('[data-load-patent]').click();await page.waitForFunction(()=>document.querySelector('[data-load-patent]').getAttribute('aria-expanded')==='true');const patentText=await page.locator('.arena-patent-detail').innerText();assert(patentText.includes('"amount":2'));assert(patentText.includes('未经人工科学审核'));assert(!patentText.includes('[object Object]'));assert.strictEqual(await page.locator('.arena-patent-detail script').count(),0);checks.push('Patent experiment objects, source pages and independent review state render without invented fields');
  patentFailed=true;await go('?view=patents');await page.locator('[data-load-patent]').click();await page.waitForFunction(()=>document.querySelector('.arena-patent-detail').textContent.includes('无法读取'));assert.strictEqual(await page.locator('[data-load-patent]').isEnabled(),true);patentFailed=false;checks.push('Patent detail failure remains visible and retryable');
  fixture=clone(baseFixture);fixture.models=['kimi-k3','glm-5.2'].map(id=>({id,name:id+' · Semantix 网关',provider:'openai_compatible',model_version:id,available:true}));fixture.runs=fixture.models.map((m,i)=>({...clone(run),id:'fixture-exact-model-'+i,model_id:m.id,model_version:m.id,requested_model:m.id,returned_model:m.id,provider:m.provider,provider_config_snapshot:{provider:m.provider,endpoint_host:'sem.ensureok.ai',config_sha256:hash},packet:{evidence:[{id:'e1',source_sha256:hash,text:'Isolated frozen source'}]}}));fixture.metrics.by_model=fixture.models.map(m=>({...baseFixture.metrics.by_model[0],model_id:m.id,model_version:m.id}));
  await go();assert.deepStrictEqual(await page.locator('.arena-model h3').allTextContents(),['kimi-k3','glm-5.2']);await noTransportBrand('Overview must use exact model IDs without transport branding');await go('?target='+target.id);assert.deepStrictEqual(await page.locator('.arena-run h3').allTextContents(),['kimi-k3','glm-5.2']);assert((await page.locator('.arena-model-label').first().innerText()).startsWith('kimi-k3'));await noTransportBrand('Target detail must use exact model IDs');const metadata=await downloadJson(page.locator('.ledger-download'));assert.strictEqual(metadata.runs[0].provider,'openai_compatible');assert.strictEqual(metadata.runs[0].provider_config_snapshot.endpoint_host,'sem.ensureok.ai');assert.strictEqual(metadata.runs[0].requested_model,'kimi-k3');assert.strictEqual(metadata.runs[0].input_sha256,hash);await page.locator('.arena-run').first().locator('details').filter({has:page.getByText('核对版本、输入与锁定凭证',{exact:true})}).locator('summary').click();const packet=await downloadJson(page.locator('.arena-run').first().getByRole('link',{name:'下载冻结证据包 JSON'}));assert.strictEqual(packet.input_sha256,hash);assert.deepStrictEqual(packet.packet,fixture.runs[0].packet);await go('?view=leaderboard');assert.deepStrictEqual(await page.locator('.arena-table tbody tr td:first-child strong').allTextContents(),['kimi-k3','glm-5.2']);await noTransportBrand('Leaderboard must not reintroduce provider labels');await go('?view=methodology');await noTransportBrand('Methodology must keep gateway brand out of visible text');assert.strictEqual((await downloadJson(page.getByRole('link',{name:'下载公开记录与方法元数据 JSON'}))).runs[0].provider,'openai_compatible');checks.push('Exact kimi-k3/glm-5.2 IDs appear across overview, detail and comparison; provider branding is absent from visible text while ledger and frozen input downloads retain audit metadata');
  fixture=clone(baseFixture);fixture.targets[0].execution_status='withdrawn_before_execution';fixture.runs=[{...clone(run),id:'fixture-withdrawn',status:'withdrawn',probability:null,withdrawn_before_execution:true,reason:'Input source correction before any model call'}];fixture.metrics={...fixture.metrics,assigned:1,answered:0,abstained:0,failed:0,pending:0,withdrawn:1,by_model:[{model_id:model.id,assigned:1,answered:0,abstained:0,failed:0,pending:0,withdrawn:1,common_tasks:0,common_brier:null}]};await go();assert((await page.locator('.arena-target-head').innerText()).includes('执行前撤回'));await go('?target='+target.id);const withdrawn=page.locator('.arena-run[data-run-id="fixture-withdrawn"]');assert((await withdrawn.innerText()).includes('执行前撤回'));assert(!(await withdrawn.innerText()).includes('运行失败'));assert((await withdrawn.locator('.arena-run-prob').innerText()).startsWith('未提供概率'));assert.strictEqual(await page.locator('.arena-prob-row[data-run-id="fixture-withdrawn"] .arena-prob-dot,.arena-prob-row[data-run-id="fixture-withdrawn"] .arena-prob-value').count(),0);await go('?view=leaderboard');const withdrawnCells=page.locator('.arena-table tbody tr').first().locator('td');assert.strictEqual(await withdrawnCells.nth(1).innerText(),'0 / 1');assert.strictEqual(await withdrawnCells.nth(3).innerText(),'0 / 0 / 0 / 1');assert((await withdrawnCells.nth(4).innerText()).startsWith('—'));checks.push('Withdrawn slots remain in assigned denominator, show unexecuted correction state, and become neither failed forecasts nor 0% predictions');
  fixture=clone(baseFixture);fixture.runs[0].input_evidence_issues=[{severity:'blocking',evidence_id:'e1',source_sha256:hash,note:'Incorrect denominator <img src=x onerror=alert(1)>',reference_url:'https://example.org/correction'}];fixture.runs.find(r=>r.id==='fixture-zero').input_evidence_issues=[{severity:'warning',evidence_id:'e2',note:'A nonblocking source limitation',reference_url:'javascript:alert(1)'}];await go('?target='+target.id);const blocked=page.locator('.arena-run[data-run-id="fixture-predicted"]'),blockedRow=page.locator('.arena-prob-row[data-run-id="fixture-predicted"]');assert((await blockedRow.innerText()).includes('输入需更正'));assert.strictEqual(await blockedRow.locator('.arena-prob-dot,.arena-prob-value').count(),0);assert((await blocked.locator('.arena-run-prob').innerText()).startsWith('未提供概率'));assert(!(await blocked.innerText()).includes('60%'));await blocked.getByText('原始冻结值（保留审计）',{exact:true}).click();assert((await blocked.innerText()).includes('60%；原输入与输出哈希保持不变'));assert((await blocked.innerText()).includes('暂停正式比较与评分'));assert.strictEqual(await blocked.getByRole('link',{name:'更正来源'}).getAttribute('href'),'https://example.org/correction');assert.strictEqual(await page.locator('.arena-run img,.arena-run script,.arena-run a[href^="javascript:"]').count(),0);assert.strictEqual(await page.locator('.arena-prob-row[data-run-id="fixture-zero"] .arena-prob-value').innerText(),'0%');const correctedArchive=await downloadJson(page.locator('.ledger-download')),frozen=correctedArchive.runs.find(r=>r.id==='fixture-predicted');assert.strictEqual(frozen.probability,.6);assert.strictEqual(frozen.input_sha256,hash);assert.strictEqual(frozen.output_sha256,hash);assert.strictEqual(frozen.input_evidence_issues[0].severity,'blocking');checks.push('Blocking source issue hides plotted probability but exposes original 60% only in audit details and JSON, preserves hashes, escapes correction text, and leaves a nonblocking legitimate zero intact');
  fixture=clone(baseFixture);
  for(const status of [401,403,503]){
   apiFailureStatus=status;apiFailed=true;const start=apiRequests.length;
   await page.goto(origin+'/arena/lab?target='+target.id);await page.locator('#arena-retry').waitFor();
   assert((await page.locator('.arena-error').innerText()).includes('HTTP '+status));
   assert.strictEqual(await page.locator('.arena-prob-dot,.arena-prob-value').count(),0);
   assert.deepStrictEqual(apiRequests.slice(start),['/api/arena/lab/overview']);
   apiFailed=false;await page.locator('#arena-retry').click();
   await page.waitForFunction(()=>document.getElementById('arena-status').textContent.includes('内部调试记录已读取'));
  }
  apiFailureStatus=503;
  assert(apiRequests.every(u=>u!=='/api/arena'&&!u.startsWith('/api/arena?')&&!u.startsWith('/api/arena/public')));
  checks.push('401/403/503 fail closed without probabilities or public API fallback; explicit read retry recovers through gated routes');
  await page.setViewportSize({width:375,height:812});for(const q of ['', '?target='+target.id,'?view=leaderboard','?view=patents','?view=methodology']){await go(q);await noOverflow('Mobile overflow at '+q);}
  await page.locator('#theme-toggle').click();assert.strictEqual(await page.locator('html').getAttribute('data-theme'),'dark');checks.push('All five mobile views fit the viewport and theme control works');
  for(const q of ['', '?target='+target.id,'?view=leaderboard','?view=patents','?view=methodology']){
   await go(q);
   assert.strictEqual(await page.locator('form:not([method="get"]),[data-run],[data-execute],[data-billing],a[href="/predict"]').count(),0);
   const buttons=await page.locator('button').allTextContents();
   assert(!buttons.some(t=>/运行模型|生成预测|执行预测|充值|支付|开始调用|重试模型/.test(t)));
  }
  checks.push('All five lab views expose only read/filter/theme/download controls; every observed browser request is GET');
  assert.deepStrictEqual(errors,[]);assert(requestMethods.every(method=>method==='GET'));
  fs.writeFileSync(path.join(out,'fixture-receipt.json'),JSON.stringify({at:new Date().toISOString(),scope:'Isolated UI contract tests only; not real model forecasts or clinical validation',checks,errors},null,2));
  await context.close();
  if(process.env.ARENA_LAB_LIVE_BASE){
   // Do not create an authenticated state here. The local operator must supply it.
   const statePath=process.env.ARENA_LAB_STORAGE_STATE;
   assert(statePath,'ARENA_LAB_STORAGE_STATE is required for the optional authorized live check');
   assert(fs.existsSync(statePath),'The supplied lab QA storageState file is unavailable');
   if(process.platform!=='win32')assert.strictEqual(fs.statSync(statePath).mode&0o077,0,'Lab QA storageState must be private to its owner');
   const liveURL=new URL(process.env.ARENA_LAB_LIVE_BASE);
   assert(['http:','https:'].includes(liveURL.protocol)&&!liveURL.username&&!liveURL.password,'Live base must be a credential-free HTTP(S) origin');
   assert(liveURL.pathname==='/'&&!liveURL.search&&!liveURL.hash,'Live base must not contain a path, query or fragment');
   const liveBase=liveURL.origin;
   const live=await browser.newContext({viewport:{width:1440,height:1000},storageState:statePath,ignoreHTTPSErrors:process.env.ARENA_LAB_IGNORE_TLS_ERRORS==='true'});
   const p=await live.newPage(),liveErrors=[],liveChecks=[],liveMethods=[],liveApiPaths=[],blockedWrites=[];
   p.on('pageerror',e=>liveErrors.push(e.message));
   await live.route('**/*',async route=>{
    const r=route.request(),u=new URL(r.url());liveMethods.push(r.method());
    if(r.method()!=='GET'){blockedWrites.push(r.method()+' '+u.pathname);return route.abort();}
    if(u.origin!==liveBase)return route.abort();
    if(u.pathname.startsWith('/api/arena'))liveApiPaths.push(u.pathname);
    return route.continue();
   });
   try{
    const response=await live.request.get(liveBase+'/api/arena/lab/overview');
    assert(response.ok(),'Authorized lab overview is unavailable (HTTP '+response.status()+')');
    const actual=await response.json();assert(Array.isArray(actual.targets)&&Array.isArray(actual.runs)&&Array.isArray(actual.models));
    const ledgerResponse=await live.request.get(liveBase+'/api/arena/ledger');
    assert(ledgerResponse.ok());assert((ledgerResponse.headers()['content-disposition']||'').includes('attachment'));
    const ledger=await ledgerResponse.json();assert(Array.isArray(ledger.events)&&Array.isArray(ledger.runs));
    liveChecks.push('Authorized overview and existing ledger are readable without calls or mutations');
    const first=actual.targets.find(t=>actual.runs.some(r=>r.target_id===t.id))||actual.targets[0];
    assert(first,'At least one real target is required to verify the live detail view');
    const selected=await live.request.get(liveBase+'/api/arena/lab/targets/'+encodeURIComponent(first.id));
    assert(selected.ok());assert.strictEqual((await selected.json()).target.id,first.id);
    for(const [name,query] of [['overview',''],['detail','?target='+encodeURIComponent(first.id)],['leaderboard','?view=leaderboard'],['patents','?view=patents'],['methodology','?view=methodology']]){
     await p.goto(liveBase+'/arena/lab'+query);
     await p.waitForFunction(()=>document.getElementById('arena-status')?.textContent.includes('内部调试记录已读取'));
     const visible=await p.locator('body').innerText();assert(!visible.includes('[object Object]'));
     assert(!/openai_compatible|Semantix|ensureok\.ai/i.test(visible),'Transport labels must not appear as model names: '+name);
     assert.strictEqual(await p.locator('form:not([method="get"]),[data-run],[data-execute],[data-billing]').count(),0);
     assert(!(await p.locator('button').allTextContents()).some(t=>/运行模型|生成预测|执行预测|充值|支付|开始调用|重试模型/.test(t)));
     if(name==='overview'){
      const expected=actual.models.map(m=>Array.isArray(m.components)&&m.components.length?m.components.map(c=>c.id).join(' → '):m.id);
      assert.deepStrictEqual(await p.locator('.arena-model h3').allTextContents(),expected);
      const hrefs=await p.locator('.arena-target h3 a').evaluateAll(xs=>xs.map(x=>x.getAttribute('href')));
      assert(hrefs.every(h=>h.startsWith('/arena/lab?target=')));
     }
     for(const dot of await p.locator('.arena-prob-dot').all())assert(await dot.evaluate(el=>{const track=el.closest('.arena-prob-track').getBoundingClientRect(),box=el.getBoundingClientRect();return Math.abs(((box.left+box.width/2)-track.left)/track.width-parseFloat(el.getAttribute('cx'))/100)<.01;}),'Probability point misplaced in '+name);
     if(name==='detail')assert.strictEqual(await p.getByRole('link',{name:'← 返回全部目标'}).getAttribute('href'),'/arena/lab');
     if(name==='leaderboard')assert.strictEqual(await p.locator('form').getAttribute('action'),'/arena/lab');
     if(name==='patents'&&actual.patents?.length){await p.locator('[data-load-patent]').first().click();await p.waitForFunction(()=>document.querySelector('[data-load-patent]')?.getAttribute('aria-expanded')==='true');assert(!(await p.locator('.arena-patent-detail').first().innerText()).includes('[object Object]'));}
     await p.evaluate(()=>window.scrollTo(0,0));
     await p.screenshot({path:path.join(out,'live-'+name+'-desktop.png'),fullPage:true});
     await p.setViewportSize({width:375,height:812});
     await p.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
     assert(await p.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+1),'Live mobile overflow in '+name);
     await p.screenshot({path:path.join(out,'live-'+name+'-mobile.png'),fullPage:true});
     await p.setViewportSize({width:1440,height:1000});
     liveChecks.push(name+': actual authorized data, correct lab navigation, no execution controls or horizontal overflow at desktop/mobile widths');
    }
    const previous=await p.locator('html').getAttribute('data-theme');await p.locator('#theme-toggle').click();
    assert.notStrictEqual(await p.locator('html').getAttribute('data-theme'),previous);
    await p.screenshot({path:path.join(out,'live-methodology-theme.png'),fullPage:true});
    assert.deepStrictEqual(liveErrors,[]);assert.deepStrictEqual(blockedWrites,[]);assert(liveMethods.every(m=>m==='GET'));
    assert(liveApiPaths.includes('/api/arena/lab/overview')&&liveApiPaths.some(x=>x.startsWith('/api/arena/lab/targets/')));
    assert(liveApiPaths.every(x=>x!=='/api/arena'&&!x.startsWith('/api/arena/public')));
    liveChecks.push('All observed browser requests are GET; no public overview fallback; theme control works');
    fs.writeFileSync(path.join(out,'live-receipt.json'),JSON.stringify({at:new Date().toISOString(),base:liveBase,version:actual.version,scope:'Read-only authorized live backend rendering; no calls, mutations, cookie exports or clinical validation.',targets:actual.targets.length,runs:actual.runs.length,patents:actual.patents?.length,checks:liveChecks,errors:liveErrors,methods:[...new Set(liveMethods)]},null,2));
   }finally{await live.close();}
  }
  console.log(JSON.stringify({fixture_checks:checks.length,errors,live_read_only:!!process.env.ARENA_LAB_LIVE_BASE,receipts:out}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
