(() => {
  'use strict';
  const el = id => document.getElementById(id), node = (tag, value, cls) => {
    const n = document.createElement(tag); n.textContent = value; if (cls) n.className = cls; return n;
  };
  const names = {beta_pooled:'统计 · 合并贝叶斯基准',beta_area:'统计 · 适应症收缩基准',logistic_l2:'训练 · L2 逻辑回归',extra_trees:'训练 · Extra Trees', 'deepseek-flash':'deepseek-flash','deepseek-v4-pro':'deepseek-v4-pro'};
  const num = x => x == null ? '无法估计' : Number(x).toFixed(3);
  fetch('/api/predome/historical').then(r => { if (!r.ok) throw Error('历史评测 API 不可用'); return r.json(); }).then(envelope => {
    if (!envelope.integrity_verified) throw Error('完整性校验失败');
    const r=envelope.record, b=r.benchmark, c=r.data_counts, lc=r.llm_comparison;
    el('history-status').textContent = '历史资料与评测哈希核验通过 · ' + (b.exploratory_training_completed ? '探索性训练已完成，正式模型门槛未达到' : '数据已归档，合格样本仍不足以试训');
    for (const [value,label] of [[c.candidate_rows,'历史候选记录'],[c.independent_agent_accepted_rows,'源文件及 agent 复核通过'],[c.train,'训练试验'],[c.test,'时间留出试验']]) {
      const box=node('div','','metric');box.append(node('b',value),node('span',label));el('history-summary').append(box);
    }
    const result=el('history-findings');
    result.append(node('p','二分类结果候选 '+c.binary_label_candidate_rows+' 条；有历史主要终点原文 '+c.primary_snapshot_available_rows+' 条；通过全部对齐门槛 '+c.independent_agent_accepted_rows+' 条。','small'));
    result.append(node('p','训练：2022 年及以前；测试：2023–2025 年。测试资产从训练集剔除 '+b.purged_train_asset_overlap.length+' 条；训练 / 测试资产组 '+c.train_asset_groups+' / '+c.test_asset_groups+'。没有依据测试结果调参，也未拟合小样本校准器。'));
    if(b.label_counts)result.append(node('p','主要终点标签：训练成功 / 失败 '+b.label_counts.train.success+' / '+b.label_counts.train.failure+'；测试 '+b.label_counts.test.success+' / '+b.label_counts.test.failure+'。类别比例和适应症构成的差异会影响方法比较，不能据此推定全行业成功率。','notice'));
    const common=lc.status==='completed';
    const metrics=common?lc.metrics:b.metrics;
    for (const [key,m] of Object.entries(metrics)) {
      const tr=document.createElement('tr'); const ci=(common?lc.uncertainty:b.uncertainty)?.brier_percentile_95_intervals?.[key];
      for (const [label,value] of [['方法',names[key]||key],['Brier',num(m.brier)],['Log loss',num(m.log_loss)],['AUROC',num(m.auroc)],['n',m.n]]) {
        const td=node('td',value);td.dataset.label=label;if(label==='Brier'&&ci)td.append(node('span','95% 重抽样区间 '+ci.map(num).join('–')));tr.append(td);
      } el('history-metrics').tBodies[0].append(tr);
    }
    el('history-metric-note').textContent=common?'表内所有方法使用同一批 '+lc.common_answered+' / '+lc.test_total+' 个可比较测试试验；区间按资产组重抽样。DeepSeek 隐去名称、NCT、结果标签和其他模型输出，但历史知识污染仍无法排除，因此不能据此声称前瞻准确率。LLM 只收到匿名测试设计特征，有监督模型使用了训练标签，训练条件并不相同。':'尚无完整 LLM 共同回答集；当前表仅列统计和训练方法的同批时间测试指标。';
    if(r.reasoning_review){const diagnostic=r.reasoning_review.posthoc_diagnostic;result.append(node('p','解释审查发现 '+r.reasoning_review.issues.length+' 项问题，含无来源历史成功率与未知对照被当成否定。原始概率保留，但不作为通过证据审查的候选预测。','notice'));if(diagnostic)result.append(node('p','透明事后诊断：固定 50% 概率的 Brier 为 '+num(diagnostic.metrics.brier)+'；两种 DeepSeek 均未优于它，AUROC 也低于 0.5。此参考在看到输出后补充，不属于方案预设基准。','notice'));}
    if(common)for(const [model,coverage] of Object.entries(lc.coverage))result.append(node('p',(names[model]||model)+'回答覆盖率 '+(100*coverage).toFixed(1)+'%。','small'));
    if(b.exploratory_training_completed){const best=Object.entries(b.metrics).filter(([k])=>['logistic_l2','extra_trees'].includes(k)).sort((a,z)=>a[1].brier-z[1].brier)[0];if(best)result.append(node('p',best[1].brier<b.metrics.beta_pooled.brier?'在完整时间留出集上，训练模型的 Brier 点估计较低；仍需更大、结果无关采样的外部留出集确认。':'在完整时间留出集上，训练模型的 Brier 点估计未优于合并统计基准，暂不升级为正式候选预测模型。','notice'));}
    const train=new Set(b.training_ncts||[]), test=new Set((b.test_predictions||[]).map(x=>x.nct_id));
    const predictions=new Map((b.test_predictions||[]).map(x=>[x.nct_id,x.probabilities]));
    const replayByNct=new Map((b.test_predictions||[]).map((row,i)=>[row.nct_id,(r.llm_runs||[]).map(e=>({model:e.record.model_requested,forecast:(e.record.forecasts||[]).find(f=>f.case_id==='case-'+String(i).padStart(3,'0'))}))]));
    const draw = query => {
      const body=el('history-cases').tBodies[0];body.replaceChildren();
      for(const row of r.dataset_rows){const split=train.has(row.nct_id)?'train':test.has(row.nct_id)?'test':'excluded';if(!(row.nct_id+' '+row.asset_group+' '+split+' '+(row.trial_name||'')).toLowerCase().includes(query))continue;
        const tr=document.createElement('tr');const cells=[node('td',row.trial_name||row.nct_id),node('td',(row.features_as_of||'未核实')+' → '+(row.first_result_at||'未核实')),node('td',row.label===1?'主要终点达成':row.label===0?'主要终点未达成':'含混 / 未裁定'),node('td',split)];
        cells[0].append(node('span',row.nct_id+' · '+(row.asset_group||'待分组')));
        for(const [key,label]of[['feature_source','快照'],['label_source','结果']])if(row[key]?.url){const a=node('a',label+' ');a.href=row[key].url;a.target='_blank';a.rel='noopener noreferrer';cells[3].append(a);}
        if(row.label_target)cells[2].append(node('span',row.label_target));
        for(const primary of row.historical_primary_outcomes||[])cells[2].append(node('span','历史 primary：'+primary.measure+'；'+(primary.time_frame||'时间未填')));
        const rejected=b.rejected.find(x=>x.nct_id===row.nct_id);if(rejected)cells[3].append(node('span','排除原因：'+rejected.reasons.join('；')));if(b.purged_train_asset_overlap.includes(row.nct_id))cells[3].append(node('span','同资产进入测试集，训练剔除'));
        if(row.review_notes)cells[3].append(node('span',row.review_notes));
        const p=predictions.get(row.nct_id);if(p)cells[3].append(node('span','统计 / LR / ET：'+[p.beta_pooled,p.logistic_l2,p.extra_trees].map(x=>(100*x).toFixed(1)+'%').join(' / ')));
        for(const replay of replayByNct.get(row.nct_id)||[]){const f=replay.forecast;cells[3].append(node('span',(names[replay.model]||replay.model)+'：'+(f?.probability==null?'拒答 / 未完成':(100*f.probability).toFixed(1)+'%')));if(f?.reasoning)cells[3].append(node('span',f.reasoning));}
        cells.forEach((td,i)=>{td.dataset.label=['试验','时间','标签','用途'][i];tr.append(td)});body.append(tr);
      }
    };draw('');el('history-search').addEventListener('input',e=>draw(e.target.value.trim().toLowerCase()));
    for(const [field,q] of Object.entries(b.feature_quality?.train||{}))el('history-gates').append(node('p',field+'：训练实值 '+q.nonmissing+' / '+c.train+'；不同实值 '+q.unique_nonmissing+(q.unique_nonmissing<=1?'（缺失或常量，不能提供独立预测信息）':''),'small'));
    for(const text of ['正式模型门槛保持训练 200、校准 50、测试 50，并要求人工双审。本批人工双审 '+c.human_double_adjudicated_rows+' 条；agent 复核不能替代人工。','已保留排除记录 '+b.rejected.length+' 条，拒绝原因随 JSON 导出。','历史样本由定向收集产生，存在选择偏差；尚无真实前瞻读出准确率。',...b.limitations])el('history-gates').append(node('p',text));
    for(const issue of r.reasoning_review?.issues||[])el('history-gates').append(node('p',(names[issue.model]||issue.model)+' '+issue.case_id+'：'+issue.kind+'；'+issue.assessment,'small'));
    for(const [split,fields] of Object.entries(r.semantic_unknown_or_missing||{}))el('history-gates').append(node('p',split+' endpoint_family 中缺失或 unknown：'+fields.endpoint_family+'；modality / comparator_type：'+fields.modality+' / '+fields.comparator_type,'small'));
    if(r.next_dataset_queue)el('history-gates').append(node('p','下一批已核 '+r.next_dataset_queue.candidate_count+' 个候选 / '+r.next_dataset_queue.asset_count+' 个资产组；仍未训练，需要历史快照与目标匹配。'));
    el('history-gates').append(node('p','历史评测 SHA-256 '+envelope.sha256,'small'));
  }).catch(error=>{el('history-status').textContent='历史评测尚未加载完成：'+error.message;});
})();
