'use strict';
// Actual review assets, fully intercepted HTTP, synthetic UI fixtures only.
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs = require('fs'), path = require('path'), assert = require('assert');
const root = path.resolve(__dirname, '..'), out = path.join(root, 'verification/arena-review-ui');
const origin = 'http://predome-review.test:18443', base = '/api/arena/review';
const sha = n => n.repeat(64), clone = value => JSON.parse(JSON.stringify(value));
const target = {id: 'fixture-trial', nct_id: 'NCT00000001', trial_name: 'Fixture Trial · 主要终点核验', drug: 'Fixture intervention', endpoint: 'Prespecified primary endpoint', population: 'Registered study population', comparison: 'Experimental intervention versus active comparator', success_definition: 'The registered multiplicity-adjusted primary test', analysis_timepoint: 'Prespecified final analysis'};
const candidate = {id: 'source-ready', kind: 'official_announcement_candidate', url: 'https://example.org/official-result', sha256: sha('a'), retrieved_at: '2026-10-25T12:00:00Z', reviewable: true, reason: ''};
const fixtureBase = {access: {name: 'Fixture Reviewer', csrf: 'fixture-session-csrf', enrollment_sha256: sha('b'), target_ids: [target.id], identity_assurance: 'authenticated_account_operator_enrolled', scientific_credentials_verified: false, independent_human_review_verified: false},
  targets: [target, {id: 'not-assigned', trial_name: 'Must not appear', nct_id: 'NCT00000002'}],
  detail: {target, target_binding_sha256: sha('c'), enrollment_sha256: sha('b'), supersedes_result_sha256: null, own_review_sha256: null,
    candidates: [candidate, {id: 'registry-no-result', kind: 'registry_update', url: 'https://clinicaltrials.gov/study/NCT00000001', sha256: sha('e'), retrieved_at: '2026-10-25T12:00:00Z', reviewable: false, reason: 'No published result'}, {...candidate, id: 'unsafe-link', url: 'javascript:alert(1)', kind: '<img src=x onerror=alert(1)>'}],
    peer_reviews: [{label: 'DO_NOT_RENDER_PEER_RESULT'}], predictions: [{model: 'DO_NOT_RENDER_MODEL_RESULT'}]},
  inspection: {text: 'Fixture original source text. The registered primary endpoint did not meet its prespecified statistical criterion. This is a synthetic browser fixture, not an actual clinical result.', source_publication_date: '2026-10-24', source_publication_timestamp_utc: null, publication_time_precision: 'date', publication_date_basis: 'document_metadata', source_sha256: sha('a')}};
(async () => {
  fs.mkdirSync(out, {recursive: true});
  const browser = await chromium.launch({headless: true, ...(process.env.CHROMIUM_EXECUTABLE ? {executablePath: process.env.CHROMIUM_EXECUTABLE} : {})});
  const context = await browser.newContext({acceptDownloads: true, viewport: {width: 1440, height: 1000}}), page = await context.newPage();
  const requests = [], unexpected = [], errors = [], checks = [], posts = [];
  let fixture = clone(fixtureBase), failures = {}, postStatus = 200;
  page.on('pageerror', error => errors.push(error.message));
  await context.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url()); requests.push({method: request.method(), path: url.pathname});
    if (url.origin !== origin) {unexpected.push(request.url()); return route.abort();}
    if (url.pathname.startsWith('/api/')) {
      if (!url.pathname.startsWith(base + '/')) {unexpected.push(url.pathname); return route.abort();}
      const relative = url.pathname.slice(base.length);
      if (request.method() === 'POST') {
        if (relative !== '/targets/' + target.id + '/reviews') {unexpected.push(relative); return route.abort();}
        posts.push({body: request.postDataJSON(), headers: request.headers()});
        await new Promise(resolve => setTimeout(resolve, 45));
        if (postStatus === 200) {fixture.detail.own_review_sha256 = sha('f'); fixture.detail.supersedes_result_sha256 = sha('d');}
        return route.fulfill({status: postStatus, contentType: 'application/json', body: JSON.stringify(postStatus === 200 ? {status: 'announced', scientific_credentials_verified: false, independent_human_review_verified: false} : {detail: 'Fixture failure; not a saved review'})});
      }
      if (request.method() !== 'GET') {unexpected.push(request.method() + ' ' + relative); return route.abort();}
      if (failures[relative]) return route.fulfill({status: failures[relative], contentType: 'application/json', body: '{"detail":"Fixture unavailable"}'});
      let body;
      if (relative === '/access') body = fixture.access;
      else if (relative === '/targets') body = {targets: fixture.targets};
      else if (relative === '/targets/' + target.id) body = fixture.detail;
      else if (relative === '/targets/' + target.id + '/sources/source-ready/inspect' || relative === '/targets/' + target.id + '/sources/unsafe-link/inspect') body = fixture.inspection;
      else if (relative === '/targets/' + target.id + '/sources/source-ready') return route.fulfill({contentType: 'text/plain', headers: {'Content-Disposition': 'attachment; filename=fixture-original.txt'}, body: fixture.inspection.text});
      else {unexpected.push(relative); return route.abort();}
      return route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
    }
    const relative = url.pathname === '/arena/lab/review' ? 'static/arena-review.html' : url.pathname.slice(1), file = path.resolve(root, relative);
    if (!relative.startsWith('static/') || !file.startsWith(root + path.sep) || !fs.existsSync(file)) {unexpected.push(url.pathname); return route.abort();}
    return route.fulfill({body: fs.readFileSync(file), contentType: file.endsWith('.html') ? 'text/html' : file.endsWith('.js') ? 'application/javascript' : file.endsWith('.svg') ? 'image/svg+xml' : 'text/css', headers: {'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:"}});
  });
  async function go() {await page.goto(origin + '/arena/lab/review'); await page.waitForFunction(() => !document.getElementById('review-status').textContent.includes('正在核对'));}
  async function ready() {await page.waitForFunction(() => document.getElementById('review-target-details').querySelector('h2') && document.getElementById('review-workspace').getAttribute('aria-busy') === 'false');}
  async function inspect() {await page.locator('#review-source').selectOption('source-ready'); await page.locator('#review-inspect').click(); await page.waitForFunction(() => !document.getElementById('review-fields').disabled);}
  async function fill(all = true) {
    await page.locator('#review-label').selectOption('failure'); await page.locator('#review-first-public').fill('2026-10-24T09:30');
    await page.locator('#review-publication-date').fill('2026-10-24'); await page.locator('#review-quote').fill('The registered primary endpoint did not meet its prespecified statistical criterion.');
    await page.locator('#review-note').fill('Fixture review: the original statement addresses the exact registered primary analysis.');
    const names = ['exact_trial', 'primary_endpoint', 'prespecified_analysis', 'first_public_release', 'source_inspected'];
    for (const name of names.slice(0, all ? 5 : 4)) await page.locator('[data-confirm="' + name + '"]').check();
  }
  async function noOverflow(label) {assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), label);}
  async function retry() {await page.locator('#review-retry').click(); await ready();}
  try {
    await go(); await ready();
    assert.strictEqual(await page.locator('#review-target option').count(), 1); assert.strictEqual(await page.locator('h1').count(), 1);
    assert((await page.locator('#review-target-details').innerText()).includes(target.endpoint));
    assert.strictEqual(await page.locator('#review-label').inputValue(), ''); assert.strictEqual(await page.locator('#review-first-public').inputValue(), '');
    assert.strictEqual(await page.locator('[data-confirm]:checked').count(), 0); assert.strictEqual(await page.locator('#review-fields').evaluate(element => element.disabled), true);
    assert(!(await page.locator('body').innerText()).includes('DO_NOT_RENDER')); assert(!requests.some(row => /overview|predictions|ledger|models|public/.test(row.path)));
    assert.strictEqual(await page.locator('#review-account-link').getAttribute('href'), 'https://predome-review.test/data');
    checks.push('Assigned exact target only; endpoint, population and analysis displayed; no peer judgments or predictions requested; label/time/confirmations begin empty; login leaves internal port');
    assert.strictEqual(await page.locator('#review-source option[value="registry-no-result"]').isDisabled(), true);
    await page.locator('#review-source').evaluate(element => {element.value = 'registry-no-result'; element.dispatchEvent(new Event('change', {bubbles: true}));});
    assert.strictEqual(await page.locator('#review-fields').evaluate(element => element.disabled), true); assert.strictEqual(await page.locator('#review-source-meta').isVisible(), false);
    checks.push('Non-reviewable registry source stays disabled even when a DOM change is forced');
    await inspect();
    assert.strictEqual(await page.locator('#review-publication-date').inputValue(), '2026-10-24'); assert.strictEqual(await page.locator('#review-first-public').inputValue(), '');
    assert.strictEqual(await page.locator('#review-label').inputValue(), ''); assert.strictEqual(await page.locator('[data-confirm]:checked').count(), 0);
    assert((await page.locator('#review-publication-basis').innerText()).includes('原件元数据'));
    assert((await page.locator('#review-publication-basis').innerText()).includes('只有日期，时刻未核验；不得编造'));
    assert.strictEqual(await page.locator('#review-download').getAttribute('href'), base + '/targets/' + target.id + '/sources/source-ready');
    const [download] = await Promise.all([page.waitForEvent('download'), page.locator('#review-download').click()]);
    assert.strictEqual(await download.failure(), null, JSON.stringify({unexpected, recent: requests.slice(-5)})); assert.strictEqual(fs.readFileSync(await download.path(), 'utf8'), fixture.inspection.text);
    checks.push('Inspection verifies candidate hash and renders the original text; only publication metadata prefills; exact original download uses the scoped route');
    await fill(false); await page.locator('#review-submit').click(); assert.strictEqual(posts.length, 0);
    assert.strictEqual(await page.locator('[data-confirm="source_inspected"]').isChecked(), false);
    checks.push('A missing explicit confirmation prevents POST');
    await page.locator('[data-confirm="source_inspected"]').check();
    await noOverflow('Desktop review worksheet overflow'); await page.screenshot({path: path.join(out, 'fixture-desktop.png'), fullPage: true});
    await page.setViewportSize({width: 375, height: 812}); await noOverflow('Mobile review worksheet overflow'); await page.screenshot({path: path.join(out, 'fixture-mobile.png'), fullPage: true});
    await page.setViewportSize({width: 1440, height: 1000});
    await page.locator('#review-form').evaluate(form => {form.dispatchEvent(new Event('submit', {bubbles: true, cancelable: true})); form.dispatchEvent(new Event('submit', {bubbles: true, cancelable: true}));});
    await page.waitForFunction(() => document.getElementById('review-submission-status').dataset.state === 'saved');
    assert.strictEqual(posts.length, 1); const sent = posts[0];
    assert.strictEqual(sent.headers['x-csrf-token'], fixture.access.csrf); assert.strictEqual(sent.body.first_public_at, '2026-10-24T09:30:00.000Z');
    assert.deepStrictEqual(Object.keys(sent.body).sort(), ['candidate_id', 'candidate_sha256', 'confirmations', 'enrollment_sha256', 'first_public_at', 'label', 'review_note', 'source_publication_date', 'source_quote', 'supersedes_result_sha256', 'supersedes_review_sha256', 'target_binding_sha256'].sort());
    assert(Object.values(sent.body.confirmations).every(value => value === true)); assert.strictEqual(sent.body.supersedes_review_sha256, null);
    assert.strictEqual(await page.locator('#review-label').inputValue(), ''); assert.strictEqual(await page.locator('#review-first-public').inputValue(), '');
    assert((await page.locator('#review-own-state').innerText()).includes('已有本人')); assert((await page.locator('#review-submission-status').innerText()).includes('独立性仍待核验'));
    checks.push('One valid POST despite duplicate submit, exact contract with CSRF and manual UTC; no client identity fields; success reloads own worksheet without claiming independent adjudication');
    await inspect(); await fill(); postStatus = 409; await page.locator('#review-submit').click();
    await page.waitForFunction(() => document.getElementById('review-submission-status').dataset.state === 'error');
    assert.strictEqual(posts.length, 2); assert.strictEqual(posts[1].body.supersedes_review_sha256, sha('f'));
    assert((await page.locator('#review-submission-status').innerText()).includes('未确认保存成功')); assert.strictEqual(await page.locator('#review-note').inputValue(), 'Fixture review: the original statement addresses the exact registered primary analysis.');
    assert.strictEqual(await page.locator('#review-fields').evaluate(element => element.disabled), true);
    fixture.access.enrollment_sha256 = sha('d'); fixture.detail.enrollment_sha256 = sha('d'); postStatus = 200;
    await retry(); assert.strictEqual(await page.locator('#review-label').inputValue(), 'failure'); assert.strictEqual(await page.locator('#review-first-public').inputValue(), '2026-10-24T09:30');
    assert.strictEqual(await page.locator('[data-confirm]:checked').count(), 0); assert.strictEqual(await page.locator('#review-fields').evaluate(element => element.disabled), true);
    assert.strictEqual(posts.length, 2);
    checks.push('Stale enrollment/version never appears saved; draft text and manual time survive explicit reauthorization, confirmations reset, and no POST is automatically retried');
    failures['/targets/' + target.id + '/sources/source-ready/inspect'] = 503;
    await page.locator('#review-inspect').click(); await page.waitForFunction(() => document.getElementById('review-status').textContent.includes('HTTP 503'));
    assert.strictEqual(await page.locator('#review-fields').evaluate(element => element.disabled), true); delete failures['/targets/' + target.id + '/sources/source-ready/inspect'];
    await page.locator('#review-inspect').click(); await page.waitForFunction(() => !document.getElementById('review-fields').disabled);
    checks.push('Failed source inspection blocks submit; explicit source reread recovers without losing draft');
    fixture = clone(fixtureBase); fixture.detail.target.trial_name = '<img src=x onerror="window.injected=true">'; fixture.access.name = '<script>window.injected=true</script>';
    fixture.inspection.text += '\n<script>window.injected=true</script><img src=x onerror="window.injected=true">';
    await go(); await ready(); await inspect();
    assert.strictEqual(await page.locator('main img,main script,.review-account script,.review-account img').count(), 0);
    assert((await page.locator('#review-source-text').innerText()).includes('<script>')); assert.strictEqual(await page.evaluate(() => window.injected), undefined);
    await page.locator('#review-source').selectOption('unsafe-link'); assert.strictEqual(await page.locator('#review-source-url a').count(), 0);
    assert.strictEqual(await page.locator('a[href^="javascript:"]').count(), 0); await noOverflow('Escaped source and target overflow');
    checks.push('Hostile target, account, source kind and original text remain text; unsafe external URLs never become links');
    fixture = clone(fixtureBase); fixture.inspection.source_sha256 = sha('e'); await go(); await ready(); await page.locator('#review-source').selectOption('source-ready'); await page.locator('#review-inspect').click();
    await page.waitForFunction(() => document.getElementById('review-status').textContent.includes('读取原件正文失败'));
    assert.strictEqual(await page.locator('#review-fields').evaluate(element => element.disabled), true); assert.strictEqual(await page.locator('#review-inspection').isVisible(), false);
    checks.push('A candidate/source hash mismatch never enables submission or exposes it as verified');
    fixture = clone(fixtureBase); fixture.inspection.source_publication_timestamp_utc = '2026-10-24T09:30:00+00:00'; fixture.inspection.publication_time_precision = 'timestamp';
    await go(); await ready(); await inspect();
    assert((await page.locator('#review-publication-basis').innerText()).includes('原件元数据发布时间（UTC）：2026-10-24T09:30:00+00:00'));
    assert((await page.locator('#review-publication-basis').innerText()).includes('不能晚于这一已知时刻'));
    assert.strictEqual(await page.locator('#review-first-public').inputValue(), ''); assert.strictEqual(await page.locator('#review-label').inputValue(), ''); assert.strictEqual(await page.locator('[data-confirm]:checked').count(), 0);
    checks.push('A timezone-aware original timestamp is displayed with its upper-bound meaning; it never prefills the first-public time, label or confirmations');
    fixture.inspection.source_publication_timestamp_utc = '<img src=x onerror="window.injected=true">';
    await go(); await ready(); await inspect(); assert((await page.locator('#review-publication-basis').innerText()).includes('只有日期，时刻未核验'));
    assert.strictEqual(await page.locator('#review-publication-basis img').count(), 0); assert.strictEqual(await page.evaluate(() => window.injected), undefined);
    delete fixture.inspection.source_publication_timestamp_utc; delete fixture.inspection.publication_time_precision;
    await go(); await ready(); await inspect(); assert((await page.locator('#review-publication-basis').innerText()).includes('只有日期，时刻未核验；不得编造'));
    assert.strictEqual(await page.locator('#review-first-public').inputValue(), '');
    checks.push('Malformed optional timestamp is not promoted to a verified time; older/date-only registry responses retain the no-invented-time warning');
    fixture = clone(fixtureBase); fixture.inspection.source_publication_date = null; fixture.inspection.publication_date_basis = 'reviewer_provided_not_independently_verified';
    fixture.inspection.publication_time_precision = 'unknown';
    await go(); await ready(); await inspect(); assert.strictEqual(await page.locator('#review-publication-date').inputValue(), ''); assert.strictEqual(await page.locator('#review-first-public').inputValue(), '');
    assert((await page.locator('#review-publication-basis').innerText()).includes('人工核对')); assert((await page.locator('#review-publication-basis').innerText()).includes('未提供可核验公开时刻；不得编造')); checks.push('Missing publication metadata remains blank with an explicit manual-verification basis and no invented time');
    fixture = clone(fixtureBase); fixture.detail.candidates = fixture.detail.candidates.filter(item => !item.reviewable); await go(); await ready();
    assert.strictEqual(await page.locator('#review-fields').evaluate(element => element.disabled), true); assert((await page.locator('#review-source-note').innerText()).includes('禁用项'));
    fixture.detail.candidates = []; await go(); await ready(); assert((await page.locator('#review-source-note').innerText()).includes('暂无已获取来源')); assert.strictEqual(await page.locator('#review-fields').evaluate(element => element.disabled), true);
    checks.push('All-unreviewable and absent-source states never fabricate a usable source or enable a review');
    fixture = clone(fixtureBase); fixture.access.target_ids = []; fixture.targets = []; await go(); await page.locator('#review-empty').waitFor(); assert.strictEqual(await page.locator('#review-workspace').isVisible(), false); checks.push('No assigned targets has a clear empty state and no mutation controls');
    for (const code of [401, 403]) {
      fixture = clone(fixtureBase); failures = {'/access': code}; const before = requests.length;
      await go(); await page.waitForFunction(status => document.getElementById('review-status').textContent.includes('HTTP ' + status), code);
      assert.strictEqual(await page.locator('#review-workspace').isVisible(), false); assert.strictEqual(await page.locator('#review-login').getAttribute('href'), 'https://predome-review.test/data');
      assert.deepStrictEqual(requests.slice(before).filter(row => row.path.startsWith('/api/')).map(row => row.path), [base + '/access']);
    }
    checks.push('Expired session and ordinary unauthorized user receive login guidance, request no scoped data and expose no review form');
    fixture = clone(fixtureBase); failures = {'/targets': 503}; await go(); await page.waitForFunction(() => document.getElementById('review-status').textContent.includes('HTTP 503'));
    failures = {}; await retry(); await inspect(); await fill(); postStatus = 403; await page.locator('#review-submit').click();
    await page.waitForFunction(() => document.getElementById('review-submission-status').dataset.state === 'error');
    assert.strictEqual(await page.locator('#review-label').inputValue(), 'failure'); assert.strictEqual(await page.locator('#review-fields').evaluate(element => element.disabled), true);
    assert.strictEqual(await page.locator('#review-login').isVisible(), true); checks.push('Target API failure can be retried; authorization loss on submit preserves the draft without reporting success');
    assert.deepStrictEqual(errors, []); assert.deepStrictEqual(unexpected, []);
    assert(requests.every(row => row.method === 'GET' || row.method === 'POST' && row.path === base + '/targets/' + target.id + '/reviews'));
    fs.writeFileSync(path.join(out, 'fixture-receipt.json'), JSON.stringify({at: new Date().toISOString(), scope: 'Actual review UI assets with synthetic fully intercepted HTTP fixtures; no real review, model call, deployment or clinical validation', checks, errors, unexpected, post_count: posts.length, screenshots: ['fixture-desktop.png', 'fixture-mobile.png']}, null, 2));
    console.log(JSON.stringify({fixture_checks: checks.length, errors, unexpected, receipts: out}));
  } finally {await browser.close();}
})().catch(error => {console.error(error); process.exit(1);});
