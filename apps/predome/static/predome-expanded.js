(() => {
  'use strict';
  const el = id => document.getElementById(id);
  if (!el('expanded-status')) return;
  const node = (tag, text, cls) => {
    const n = document.createElement(tag); n.textContent = text == null ? '未提供' : String(text);
    if (cls) n.className = cls; return n;
  };
  const list = v => Array.isArray(v) ? v : [];
  const object = v => v && typeof v === 'object' && !Array.isArray(v) ? v : {};
  const display = v => v == null ? '未提供' : typeof v === 'object' ? JSON.stringify(v) : String(v);
  const finite = v => typeof v === 'number' && Number.isFinite(v);
  const num = v => finite(v) ? v.toFixed(3) : '无法估计';
  const probability = v => finite(v) && v >= 0 && v <= 1 ? (100 * v).toFixed(1) + '%' : '拒答 / 未提供';
  const names = {design_logistic:'设计特征 · L2 逻辑回归', design_extra_trees:'设计特征 · Extra Trees', prior_augmented_logistic:'设计 + 前期证据 · L2 逻辑回归', prior_augmented_extra_trees:'设计 + 前期证据 · Extra Trees', beta_pooled:'统计 · 合并贝叶斯基准', beta_area:'统计 · 适应症收缩基准', constant_0_5:'固定 50% 基准', logistic_l2:'设计特征 · L2 逻辑回归', extra_trees:'设计特征 · Extra Trees', prior_logistic_l2:'设计 + 前期证据 · L2 逻辑回归', prior_extra_trees:'设计 + 前期证据 · Extra Trees', 'deepseek-flash':'DeepSeek Flash', 'deepseek-v4-pro':'DeepSeek Pro'};
  const label = k => names[k] || k;
  const paragraph = (target, text, cls) => el(target).append(node('p', text, cls));
  const sourceLink = (parent, raw, title) => {
    if (typeof raw !== 'string') return;
    try {
      const url = new URL(raw);
      if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password) return;
      const link = node('a', title || url.hostname); link.href = url.href;
      link.target = '_blank'; link.rel = 'noopener noreferrer'; parent.append(link);
    } catch (_) { /* A source without a valid public URL remains plain text. */ }
  };
  const metricRows = (target, metrics, prefix, intervals) => {
    const body = el(target).tBodies[0];
    for (const [method, raw] of Object.entries(object(metrics))) {
      const m = object(raw), row = document.createElement('tr');
      const values = [prefix + label(method), num(m.brier), num(m.log_loss), num(m.auroc), display(m.n)];
      values.forEach((value, i) => {
        const td = node('td', value); td.dataset.label = ['方法', 'Brier', 'Log loss', 'AUROC', 'n'][i];
        const ci = object(intervals)[method];
        if (i === 1 && Array.isArray(ci) && ci.length === 2) td.append(node('span', '95% 条件重抽样区间 ' + ci.map(num).join('–')));
        row.append(td);
      }); body.append(row);
    }
  };
  fetch('/api/predome/expanded', {cache:'no-store'}).then(response => {
    if (!response.ok) throw Error('API 暂不可用或完整性校验未通过');
    return response.json();
  }).then(envelope => {
    if (envelope.integrity_verified !== true || !envelope.record) throw Error('未获得已校验记录');
    const r = object(envelope.record), b = object(r.benchmark), c = object(r.data_counts);
    const counts = object(b.counts), lc = object(r.llm_comparison), cv = object(b.training_cv);
    const trainCount = counts.train ?? c.train, testCount = counts.test ?? c.test;
    el('expanded-status').textContent = '记录与清单文件 SHA-256 校验通过 · ' + (b.exploratory_training_completed === true ? '探索性扩展训练已完成' : '训练状态未确认') + ' · 8例已见历史重放，不是新增验证';
    for (const [value, title] of [[trainCount,'训练试验'],[counts.train_assets ?? c.train_asset_groups,'训练资产组'],[testCount,'已见测试重放'],[counts.test_assets ?? c.test_asset_groups,'测试资产组']]) {
      const box = node('div', '', 'metric'); box.append(node('b', display(value)), node('span', title)); el('expanded-summary').append(box);
    }
    for (const split of ['train','test']) {
      const x = object(object(b.label_counts)[split]);
      paragraph('expanded-findings', (split === 'train' ? '训练' : '已见测试') + '成功 / 失败：' + display(x.success) + ' / ' + display(x.failure) + '。', 'small');
    }
    paragraph('expanded-findings', '本轮扩展有监督训练不等于 LLM 微调。LLM 比较来自同一证据包的独立调用，历史预训练知识污染不能排除；新增阴性样本不改变正式 200 / 50 / 50 及人工双审门槛。', 'notice');
    metricRows('expanded-metrics', b.metrics, '全重放集 · ', object(b.uncertainty).brier_percentile_95_intervals);
    const common = lc.common_answered;
    const coverageAudit = object(r.coverage_audit);
    if (coverageAudit.common_subset_labels) paragraph('expanded-findings', '共同回答子集：阳性 ' + display(object(coverageAudit.common_subset_labels).success) + '、阴性 ' + display(object(coverageAudit.common_subset_labels).failure) + '；拒答病例中阳性 ' + display(object(coverageAudit.abstained_labels).success) + '、阴性 ' + display(object(coverageAudit.abstained_labels).failure) + '。本轮两项拒答恰为阳性，AUROC=1仅一个阳性支持，不能当作前瞻命中率。', 'notice');
    if (finite(object(object(b.metrics).constant_0_5).brier) && Object.entries(object(b.metrics)).every(([k,v]) => k === 'constant_0_5' || !finite(object(v).brier) || v.brier >= b.metrics.constant_0_5.brier)) paragraph('expanded-findings','本轮全8项重放中，统计与机器学习方法均未胜过固定50%基线的Brier。增加前期证据尚未证明预测增益，保留所有结果。','notice');
    if (Object.keys(object(lc.metrics)).length) metricRows('expanded-metrics', lc.metrics, '共同回答集 · ', object(lc.uncertainty).brier_percentile_95_intervals);
    el('expanded-metric-note').textContent = '全重放集 n=' + display(testCount) + '；LLM 共同回答 n=' + display(common) + '。两组分别展示，不能跨组排名。LLM 共同回答表仅在模型实际返回后出现；缺失不补为 0 或 50%。所有测试例均已在前轮使用。';
    for (const [model, coverage] of Object.entries(object(lc.coverage))) paragraph('expanded-findings', label(model) + ' 回答覆盖：' + (finite(coverage) ? probability(coverage) : display(coverage)), 'small');
    metricRows('expanded-cv', cv.metrics, '', null);
    paragraph('expanded-cv-note', '方案：' + display(cv.scheme) + '；OOF ' + display(cv.n_oof) + ' / ' + display(cv.total) + '。用于模型选择：' + (cv.used_for_model_selection === false ? '否' : cv.used_for_model_selection === true ? '是' : '未提供') + '。预处理在每折训练集拟合，未把测试结果用于选模型。', 'small');
    if (cv.temporal_forward_validation === false) paragraph('expanded-cv-note','这是按资产分组的内部开发交叉验证，不是时间向前验证；某折可使用训练期内更晚的试验。', 'notice');
    for (const fold of list(cv.folds)) paragraph('expanded-cv-note', '留出资产 ' + display(fold.held_asset) + ' · ' + display(fold.status) + ' · 留出 ' + list(fold.held_ncts).length + ' 例', 'small');
    for (const [split, raw] of Object.entries(object(b.prior_coverage))) {
      const coverage = object(raw);
      paragraph('expanded-coverage', split + ' 前期数值证据覆盖 ' + display(coverage.eligible_numeric) + ' / ' + display(coverage.total) + '；A级 ' + display(coverage.A) + '；B级 ' + display(coverage.B) + '；直接 assurance 回答 ' + display(coverage.assurance_answered) + '。');
    }
    const semanticIssues = list(object(r.reasoning_review).semantic_issues);
    paragraph('expanded-findings', 'DeepSeek 解释独立核对发现 ' + semanticIssues.length + ' 项事实／措辞问题；原始概率保留，解释未全部通过。引用ID存在不等于陈述有证据。', 'notice');
    for (const issue of semanticIssues) paragraph('expanded-gates', display(issue.model) + ' · ' + display(issue.case_id) + ' · ' + display(issue.severity) + '：' + display(issue.finding), 'small');
    const train = new Set(list(b.training_ncts)), test = new Map(list(b.test_predictions).map(row => [row.nct_id, row]));
    const replay = new Map();
    list(r.llm_runs).forEach(envelope => {
      const run = object(object(envelope).record || envelope);
      list(run.forecasts).forEach(forecast => {
        const ix = /^case-(\d+)$/.exec(forecast.case_id || '');
        const id = forecast.nct_id || (ix ? list(b.test_predictions)[Number(ix[1])]?.nct_id : null);
        if (!id) return; if (!replay.has(id)) replay.set(id, []);
        replay.get(id).push({model:run.model_requested || run.model, forecast});
      });
    });
    const draw = query => {
      const body = el('expanded-cases').tBodies[0]; body.replaceChildren();
      for (const row of list(r.dataset_rows)) {
        const split = train.has(row.nct_id) ? 'train' : test.has(row.nct_id) ? 'test · 已见重放' : 'excluded';
        if (![row.nct_id,row.asset_group,row.trial_name,split].join(' ').toLowerCase().includes(query)) continue;
        const tr = document.createElement('tr');
        const cells = [node('td',row.trial_name || row.nct_id), node('td',display(row.features_as_of) + ' → ' + display(row.first_result_at)), node('td',(row.label === 1 ? '主要终点达成' : row.label === 0 ? '主要终点未达成' : '未裁定') + ' · ' + split),node('td','')];
        cells[0].append(node('span',display(row.nct_id) + ' · ' + display(row.asset_group)));
        if (row.label_target) cells[2].append(node('span',row.label_target));
        for (const outcome of list(row.historical_primary_outcomes)) cells[2].append(node('span','历史 primary：' + display(outcome.measure) + '；' + display(outcome.time_frame)));
        for (const [method,p] of Object.entries(object(test.get(row.nct_id)?.probabilities))) cells[3].append(node('span',label(method) + ' ' + probability(p)));
        for (const item of replay.get(row.nct_id) || []) cells[3].append(node('span',label(item.model) + ' ' + probability(item.forecast.probability)));
        for (const [key,title] of [['feature_source','设计来源'],['label_source','结果来源']]) {const p = node('p','', 'small'); sourceLink(p,object(row[key]).url,title); if(p.childNodes.length) cells[3].append(p);}
        if (row.prior_features) cells[3].append(node('span','前期证据级别 ' + display(row.prior_features.prior_tier)));
        const reject = list(b.rejected).find(x => x.nct_id === row.nct_id); if (reject) cells[3].append(node('span','排除：' + list(reject.reasons).join('；')));
        if (row.forecast_as_of) cells[1].append(node('span','预测截止 ' + display(row.forecast_as_of)));
        if (row.prior_selection) cells[3].append(node('span','已选前期证据 ' + display(row.prior_selection.record_id)));
        if (row.review_notes) cells[3].append(node('span',row.review_notes));
        cells.forEach((td,i) => {td.dataset.label = ['试验','日期','标签 / 用途','概率 / 来源'][i];tr.append(td);}); body.append(tr);
      }
    }; draw(''); el('expanded-search').addEventListener('input',event => draw(event.target.value.trim().toLowerCase()));
    const priorContainer = r.prior_evidence_records;
    const priorObject = object(priorContainer), priors = Array.isArray(priorContainer) ? priorContainer : list(priorObject.records);
    const sources = new Map(Object.entries(object(priorObject.sources)));
    if (!priors.length) paragraph('expanded-priors','暂无可展示的前期疗效记录；未知不等于无效。');
    for (const prior of priors) {
      const article = node('article',''); article.append(node('h3',prior.prior_trial || prior.record_id || '前期证据'));
      article.append(node('p',display(prior.record_id) + ' · ' + display(prior.prior_nct_id) + ' · 随机对照：' + (prior.randomized === true ? '是' : prior.randomized === false ? '否' : '未知'),'small'));
      article.append(node('p',display(prior.effect_measure) + ' = ' + display(prior.effect_estimate) + '；CI ' + display(prior.confidence_interval) + '；置信水平 ' + probability(prior.confidence_level) + '；SE（' + (prior.effect_transform === 'identity' ? '原始效应尺度' : 'log比值尺度') + '）' + display(prior.se_transformed_effect ?? prior.se_log_effect)));
      article.append(node('p','人群：' + display(prior.population) + '；剂量：' + display(prior.dose) + '；对照：' + display(prior.comparator)));
      article.append(node('p','终点：' + display(prior.endpoint) + '；时间：' + display(prior.followup_or_endpoint_time) + '；分析集：' + display(prior.analysis_population)));
      article.append(node('p','样本量 ' + display(prior.sample_sizes) + '；SE 推导 ' + display(prior.se_derivation),'small'));
      for (const app of list(prior.applicability)) article.append(node('p',display(app.target_nct_id) + ' · ' + display(app.evidence_grade) + ' · 间接数值特征候选：' + (app.eligible_as_indirect_numeric_feature_candidate === true ? '是，仍须日期与选择门槛' : '否 / 未确认') + '；直接 assurance：' + (app.direct_assurance_eligible === true ? '具备候选条件，仍须审核' : '不具备') + '；差异：' + list(app.differences).join('；'),'small'));
      if (prior.curation_note) article.append(node('p',prior.curation_note,'notice'));
      article.append(node('p','审查：' + display(prior.review_status) + '；人工审查 ' + display(prior.human_review_count),'small'));
      const source = object(prior.source); if(Object.keys(source).length) sources.set(prior.source_id || prior.record_id, source);
      const p = node('p','来源日期 ' + display(source.publication_date) + ' · ', 'small'); sourceLink(p,source.url,'原始来源'); article.append(p); el('expanded-priors').append(article);
    }
    for (const [id, raw] of sources) {
      const source = object(raw), p = node('p', id + ' · ' + display(source.publication_date) + ' · ', 'small'); sourceLink(p,source.url,source.url);
      p.append(node('span',' · ' + display(source.retrieval_method) + ' · SHA-256 ' + display(source.sha256)));
      if (source.date_note) p.append(node('span',source.date_note)); el('expanded-sources').append(p);
    }
    paragraph('expanded-gates','正式训练门槛：训练 200、校准 50、测试 50，且须人工双审。当前仍是研究性开发重放，无临床预测验证。','notice');
    paragraph('expanded-gates','方案 ' + display(object(object(r.protocol)['protocol.json'] || r.protocol).id) + '；正式门槛状态 ' + display(r.formal_training_gate), 'small');
    for (const text of list(b.limitations)) paragraph('expanded-gates',text);
    if (!list(b.limitations).length) paragraph('expanded-gates','未提供完整局限列表；请读取冻结报告，不能视为已通过正式验证。');
    el('expanded-hash').textContent = '扩展记录 SHA-256 ' + display(envelope.sha256) + '。哈希验证表示文件一致性，不构成临床有效性验证或第三方可信时间戳。';
  }).catch(error => {
    el('expanded-status').textContent = '扩展记录未能展示：' + error.message + '。未加载的结果不表示零准确率或零疗效。';
  });
})();
