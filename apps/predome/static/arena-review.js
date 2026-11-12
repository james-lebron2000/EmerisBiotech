'use strict';
(() => {
  const api = '/api/arena/review', byId = id => document.getElementById(id);
  const state = {access: null, detail: null, candidate: null, inspected: false, authorized: false, submitting: false, loading: false, epoch: 0, sourceEpoch: 0, drafts: new Map()};
  const confirms = ['exact_trial', 'primary_endpoint', 'prespecified_analysis', 'first_public_release', 'source_inspected'];
  const hash = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
  const text = value => typeof value === 'string' ? value : '';
  const sourceKind = value => ({official_announcement_candidate: '官方公告候选', reported_results_candidate: '已发布结果候选', registry_update: '注册信息更新', pubmed_literature_candidate: '文献索引候选'}[value] || '其他候选来源');
  const login = new URL('/data', location.href); login.protocol = 'https:'; login.port = ''; login.username = ''; login.password = '';
  for (const id of ['review-login', 'review-account-link']) byId(id).href = login.href;
  function status(message, error = false, retry = false) {
    byId('review-status').textContent = message;
    byId('review-status').parentElement.dataset.state = error ? 'error' : 'ready';
    byId('review-retry').hidden = !retry;
  }
  function submission(message, mode = '') { byId('review-submission-status').textContent = message; byId('review-submission-status').dataset.state = mode; }
  function controls() {
    byId('review-fields').disabled = !state.authorized || !state.detail || !state.candidate?.reviewable || !state.inspected || state.submitting || state.loading;
    byId('review-target').disabled = state.submitting || state.loading;
    byId('review-source').disabled = !state.authorized || !state.detail || state.submitting || state.loading;
    byId('review-refresh').disabled = state.submitting || state.loading;
    byId('review-retry').disabled = state.submitting || state.loading;
    byId('review-inspect').disabled = !state.authorized || !state.candidate?.reviewable || state.submitting || state.loading;
    byId('review-workspace').setAttribute('aria-busy', String(state.loading || state.submitting));
  }
  async function request(path, options = {}) {
    const response = await fetch(api + path, {credentials: 'same-origin', cache: 'no-store', ...options});
    if (!response.ok) { const error = new Error('HTTP ' + response.status); error.status = response.status; throw error; }
    return response.json();
  }
  function failure(error, action) {
    const code = Number.isInteger(error.status) ? '（HTTP ' + error.status + '）' : '';
    if (error.status === 401 || error.status === 403 || error.status === 409) {
      state.authorized = false;
      byId('review-login').hidden = error.status === 409;
      const reason = error.status === 401 ? '现有登录已失效' : error.status === 403 ? '当前账号没有该目标的有效核验授权' : '授权或核验版本已变化';
      status(reason + code + '。当前标签页草稿保留；请重新读取并核对，系统不会自动重试提交。', true, true);
    } else status(action + '失败' + code + '。没有自动提交或更改结果；请重试读取。', true, true);
    controls();
  }
  function readDraft() {
    return {candidate_id: state.candidate?.id || '', candidate_sha256: state.candidate?.sha256 || '', binding: state.detail?.target_binding_sha256,
      label: byId('review-label').value, publication: byId('review-publication-date').value, first: byId('review-first-public').value,
      quote: byId('review-quote').value, note: byId('review-note').value,
      confirmations: Object.fromEntries(confirms.map(name => [name, document.querySelector('[data-confirm="' + name + '"]').checked]))};
  }
  function keepDraft() { if (state.detail && !state.submitting) state.drafts.set(state.detail.target.id, readDraft()); }
  function restoreDraft(draft, confirmations = false) {
    byId('review-label').value = draft.label; byId('review-publication-date').value = draft.publication;
    byId('review-first-public').value = draft.first; byId('review-quote').value = draft.quote; byId('review-note').value = draft.note;
    for (const name of confirms) document.querySelector('[data-confirm="' + name + '"]').checked = confirmations && draft.confirmations[name] === true;
  }
  function resetForm() { byId('review-form').reset(); for (const input of byId('review-form').querySelectorAll('input,textarea,select')) input.setCustomValidity(''); }
  function sourcePath(suffix = '') { return '/targets/' + encodeURIComponent(state.detail.target.id) + '/sources/' + encodeURIComponent(state.candidate.id) + suffix; }
  function targetFacts(target) {
    const host = byId('review-target-details'); host.replaceChildren();
    const heading = document.createElement('div'); heading.className = 'review-target-heading';
    const title = document.createElement('h2'); title.textContent = text(target.trial_name) || text(target.id); heading.append(title);
    if (/^NCT\d{8}$/.test(target.nct_id || '')) { const link = document.createElement('a'); link.textContent = target.nct_id + ' ↗'; link.href = 'https://clinicaltrials.gov/study/' + target.nct_id; link.target = '_blank'; link.rel = 'noopener noreferrer'; heading.append(link); }
    host.append(heading); const list = document.createElement('dl'); list.className = 'review-target-facts';
    for (const [name, value] of [['药物', target.drug], ['主要终点', target.endpoint], ['研究人群', target.population], ['比较', target.comparison], ['成功判定标准', target.success_definition], ['预设分析时点', target.analysis_timepoint]]) {
      const row = document.createElement('div'), term = document.createElement('dt'), definition = document.createElement('dd');
      term.textContent = name; definition.textContent = text(value) || '未提供，须先核对注册目标'; row.append(term, definition); list.append(row);
    }
    host.append(list);
  }
  function safeSource(value) { try { const url = new URL(value); return url.protocol === 'https:' && !url.username && !url.password ? url : null; } catch { return null; } }
  function selectSource(id, draft = null) {
    state.sourceEpoch++; state.inspected = false; state.candidate = state.detail.candidates.find(item => item.id === id && item.reviewable === true && hash(item.sha256)) || null;
    resetForm(); byId('review-inspection').hidden = true; byId('review-source-meta').hidden = !state.candidate;
    byId('review-source-text').textContent = ''; byId('review-publication-basis').textContent = '';
    if (!state.candidate) { byId('review-source-note').textContent = state.detail.candidates.length ? '请选择可用于核验的来源；禁用项尚不具备提交条件。' : '暂无已获取来源。不会用模拟资料填充。'; controls(); return; }
    const item = state.candidate; byId('review-source').value = item.id;
    byId('review-source-note').textContent = '先读取正文并核对原件，再独立填写结果。来源准入不等于科学裁定。';
    byId('review-source-kind').textContent = sourceKind(item.kind); byId('review-source-time').textContent = text(item.retrieved_at) || '未提供'; byId('review-source-hash').textContent = text(item.sha256);
    const urlHost = byId('review-source-url'); urlHost.replaceChildren(); const url = safeSource(item.url);
    if (url) { const link = document.createElement('a'); link.href = url.href; link.textContent = url.href; link.target = '_blank'; link.rel = 'noopener noreferrer'; urlHost.append(link); } else urlHost.textContent = '缺少可安全打开的 HTTPS 原始链接';
    byId('review-download').href = api + sourcePath();
    if (draft?.candidate_id === item.id && draft.candidate_sha256 === item.sha256 && draft.binding === state.detail.target_binding_sha256) restoreDraft(draft);
    controls();
  }
  async function loadTarget(id, {preserve = true} = {}) {
    if (preserve) keepDraft(); const epoch = ++state.epoch; state.sourceEpoch++; state.loading = true; state.detail = null; state.candidate = null; state.inspected = false;
    byId('review-target-details').replaceChildren(); byId('review-source').replaceChildren(new Option('选择来源', '')); byId('review-source-meta').hidden = true; byId('review-inspection').hidden = true; controls();
    try {
      const detail = await request('/targets/' + encodeURIComponent(id)); if (epoch !== state.epoch) return;
      if (detail.target?.id !== id || !hash(detail.target_binding_sha256) || !hash(detail.enrollment_sha256) || !Array.isArray(detail.candidates)) throw new Error('Invalid worksheet');
      state.detail = detail; targetFacts(detail.target); byId('review-target').value = id; resetForm();
      for (const item of detail.candidates) { const option = new Option(sourceKind(item.kind) + ' · ' + text(item.id) + (item.reviewable === true ? '' : ' · 不可提交：' + (text(item.reason) || '待核验')), text(item.id)); option.disabled = item.reviewable !== true || !hash(item.sha256); byId('review-source').append(option); }
      byId('review-own-state').textContent = detail.own_review_sha256 ? '已有本人核验记录。本次提交作为更正保留历史；仍须重新检查原件。' : '提交后保留原始记录；更正不会覆盖历史。';
      const draft = state.drafts.get(id); selectSource(draft?.candidate_id || '', draft);
      status(draft ? '注册目标已读取。当前标签页草稿已保留；请重新查看来源并逐项确认。' : '授权目标已读取。请选择公开原件，独立填写核验结果。');
    } catch (error) { if (epoch === state.epoch) failure(error, '读取目标'); }
    finally { if (epoch === state.epoch) { state.loading = false; controls(); } }
  }
  async function initialize() {
    if (state.submitting || state.loading) return; keepDraft(); state.loading = true; controls();
    const selected = state.detail?.target.id || byId('review-target').value;
    try {
      const access = await request('/access');
      if (!text(access.csrf) || !hash(access.enrollment_sha256) || !Array.isArray(access.target_ids)) throw new Error('Invalid access response');
      state.access = access; state.authorized = true; byId('review-login').hidden = true; byId('review-account-name').textContent = text(access.name) || '现有账户';
      const response = await request('/targets'); if (!Array.isArray(response.targets)) throw new Error('Invalid target list');
      const targets = response.targets.filter(target => typeof target.id === 'string' && access.target_ids.includes(target.id));
      byId('review-target').replaceChildren(); for (const target of targets) byId('review-target').append(new Option((text(target.trial_name) || target.id) + ' · ' + text(target.nct_id), target.id));
      byId('review-workspace').hidden = targets.length === 0; byId('review-empty').hidden = targets.length !== 0;
      if (!targets.length) { state.detail = null; status('现有账户已核对，暂无分配的核验目标。'); return; }
      const id = targets.some(target => target.id === selected) ? selected : targets[0].id;
      state.loading = false; await loadTarget(id, {preserve: false});
    } catch (error) { failure(error, '读取授权或目标'); }
    finally { state.loading = false; controls(); }
  }
  async function inspectSource() {
    if (!state.candidate || !state.authorized || state.loading) return;
    const epoch = ++state.sourceEpoch, targetEpoch = state.epoch, candidate = state.candidate;
    byId('review-inspect').disabled = true; byId('review-inspect').textContent = '正在读取…'; state.inspected = false; controls(); byId('review-inspect').disabled = true;
    try {
      const result = await request(sourcePath('/inspect')); if (epoch !== state.sourceEpoch || targetEpoch !== state.epoch) return;
      if (result.source_sha256 !== candidate.sha256 || !text(result.text)) throw new Error('Source integrity mismatch');
      byId('review-source-text').textContent = result.text; byId('review-inspection').hidden = false; state.inspected = true;
      const date = /^\d{4}-\d{2}-\d{2}$/.test(result.source_publication_date || '') ? result.source_publication_date : '';
      if (!byId('review-publication-date').value && date) byId('review-publication-date').value = date;
      const basis = {document_metadata: '原件元数据', registry_results_post_date: '注册库结果首次公开日期', reviewer_provided_not_independently_verified: '需由本人填写，尚未独立核验'}[result.publication_date_basis] || '未提供可核验日期依据';
      const stamp = text(result.source_publication_timestamp_utc);
      const knownTime = result.publication_time_precision === 'timestamp' && stamp.length <= 80
        && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|\+00:00)$/.test(stamp) && Number.isFinite(Date.parse(stamp));
      const timeNote = knownTime ? ' 原件元数据发布时间（UTC）：' + stamp + '。首次结果公开时间不能晚于这一已知时刻；仍须独立核对，不自动填入。'
        : date ? ' 原件只有日期，时刻未核验；不得编造首次公开时刻。'
        : ' 原件未提供可核验公开时刻；不得编造首次公开时刻。';
      byId('review-publication-basis').textContent = '原件公开日期依据：' + basis + (date ? '（' + date + '）。可核对后修正；该日期不自动替代目标结果的首次公开时间。' : '。请人工核对并填写；该日期不会自动生成。') + timeNote;
      status('已读取本次来源原文。结果、首次公开时间和逐项确认仍由你独立填写。');
    } catch (error) { if (epoch === state.sourceEpoch && targetEpoch === state.epoch) failure(error, '读取原件正文'); }
    finally { if (epoch === state.sourceEpoch && targetEpoch === state.epoch) { byId('review-inspect').textContent = '重新读取原件正文'; controls(); } }
  }
  function utcTime(value) {
    if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value)) return null;
    const date = new Date(value + ':00Z'); return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 16) === value ? date.toISOString() : null;
  }
  async function submit(event) {
    event.preventDefault(); if (state.submitting || !state.authorized || !state.inspected || !state.candidate?.reviewable || !state.detail) return;
    const form = byId('review-form'), first = utcTime(byId('review-first-public').value);
    byId('review-first-public').setCustomValidity(first ? '' : '请明确填写有效的 UTC 日期和时间。');
    byId('review-note').setCustomValidity(byId('review-note').value.trim() ? '' : '请填写自己的核验说明。');
    byId('review-quote').setCustomValidity(byId('review-quote').value.trim().length >= 20 ? '' : '请提供至少20字符的连续原文。');
    if (!form.reportValidity() || !confirms.every(name => document.querySelector('[data-confirm="' + name + '"]').checked)) return;
    keepDraft(); const targetId = state.detail.target.id, payload = {
      enrollment_sha256: state.detail.enrollment_sha256, target_binding_sha256: state.detail.target_binding_sha256,
      candidate_id: state.candidate.id, candidate_sha256: state.candidate.sha256, label: byId('review-label').value,
      first_public_at: first, source_publication_date: byId('review-publication-date').value,
      source_quote: byId('review-quote').value, review_note: byId('review-note').value,
      confirmations: Object.fromEntries(confirms.map(name => [name, true])),
      supersedes_result_sha256: state.detail.supersedes_result_sha256 || null, supersedes_review_sha256: state.detail.own_review_sha256 || null
    };
    state.submitting = true; controls(); submission('正在保存本人核验记录，请等待…');
    try {
      await request('/targets/' + encodeURIComponent(targetId) + '/reviews', {method: 'POST', headers: {'Content-Type': 'application/json', 'X-CSRF-Token': state.access.csrf}, body: JSON.stringify(payload)});
      state.drafts.delete(targetId); state.submitting = false; resetForm();
      await loadTarget(targetId, {preserve: false});
      submission('审核记录已保存。科研资质与审核独立性仍待核验；本次保存不等于临床结果已完成独立裁定。', 'saved');
    } catch (error) {
      failure(error, '保存核验记录'); submission('未确认保存成功' + (error.status ? '（HTTP ' + error.status + '）' : '') + '。当前草稿保留；请重新读取授权与来源后核对，勿将此状态视为已保存。', 'error');
    } finally { state.submitting = false; controls(); }
  }
  byId('review-target').addEventListener('change', () => { submission(''); loadTarget(byId('review-target').value); });
  byId('review-source').addEventListener('change', () => { keepDraft(); submission(''); selectSource(byId('review-source').value); });
  byId('review-form').addEventListener('input', event => { if (typeof event.target.setCustomValidity === 'function') event.target.setCustomValidity(''); keepDraft(); });
  byId('review-form').addEventListener('change', keepDraft);
  byId('review-form').addEventListener('submit', submit);
  byId('review-inspect').addEventListener('click', inspectSource);
  byId('review-refresh').addEventListener('click', initialize);
  byId('review-retry').addEventListener('click', initialize);
  initialize();
})();
