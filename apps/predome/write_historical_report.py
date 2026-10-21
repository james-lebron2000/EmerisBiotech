"""Generate evidence-bounded Markdown from the frozen numerical benchmark."""
from pathlib import Path
import json
ROOT=Path(__file__).resolve().parent;D=ROOT/'predome_data/historical_v2';run=D/'runs/pilot-v1'
b=json.loads((run/'report.json').read_text());c=json.loads((run/'comparison.json').read_text());rows=json.loads((D/'dataset.json').read_text())
train=set(b.get('training_ncts',[]));test={r['nct_id'] for r in b['test_predictions']};names={'beta_pooled':'合并基率（Jeffreys平滑）','beta_area':'领域收缩统计基准','logistic_l2':'L2逻辑回归','extra_trees':'Extra Trees','deepseek-flash':'DeepSeek Flash零样本回放','deepseek-v4-pro':'DeepSeek Pro零样本回放'}
fmt=lambda v:'无法估计' if v is None else f'{v:.4f}'
lines=['# Predome Agent System：历史训练与时间留出评测','', '版本：2026-10-17。状态：探索性研发评测；没有临床预测验证，也没有前瞻命中率。','', '## 本轮实际完成','',f"历史候选 {len(rows)} 条。通过公开前快照、主要终点版本、来源哈希及独立agent复核的记录 {b['accepted']} 条；训练 {b['counts']['train']} 条，测试 {b['counts']['test']} 条。人工双审 0 条，不能称为人工金标准。",'',f"训练成功/失败 {b.get('label_counts',{}).get('train',{})}；测试 {b.get('label_counts',{}).get('test',{})}。这是一批定向收集病例，不是全行业随机样本。",'', '## 输入、训练和输出','', '| 部分 | 本轮实际内容 | 尚未具备 |','|---|---|---|', '| 输入 | AACT真实2019/2020/2023月度注册快照；历史计划入组、分期、随机化、主要measure行数、历史conditions领域、主要终点家族与明确placebo对照线索 | 完整SAP、事件数、同药前序效应量与SE、患者IPD、完整多组学 |','| 标签 | 已定位的首轮官方指定主要终点结果；需与公布前的目标版本一致 | 人工双审；对所有更早公开披露的全球穷尽证明 |','| 训练 | 固定L2逻辑回归C=0.1；Extra Trees 300棵/深度3/叶最小4；仅离线拟合 | 大样本校准、机制模型、患者疗效模型、外部前瞻验证 |','| 统计比较 | Jeffreys平滑合并基率；按领域8个先验等效样本收缩 | 不能替代药物专属assurance，更不能称完整虚拟人体 |','| DeepSeek | 两次真实API调用；匿名设计特征；零样本历史回放；未微调；不见测试标签和同伴结果 | 不能排除预训练记忆；两型号不等于两家独立厂商 |','| 输出 | 每例概率/拒答、同批Brier/log loss/AUROC、覆盖率、资产组重抽样区间、排除记录和来源 | 对未来MNC试验可靠性的临床保证 |','', '## 评测方法','', '方案在首轮模型拟合前保存。训练结果时间早于2023年，测试2023–2025年；测试资产从训练集剔除。缺失值处理、缩放、one-hot只在训练集拟合。模型和超参数固定，没有用测试表现挑参。小样本不拟合校准器。', '', '主要终点条目数是注册库primary measure行数，不等于独立假设检验数，不能据此自动给alpha。领域及终点家族分类为冻结的正则提取，不是临床人工裁定。原始字段和每字段定位可以追溯。', '', '原正式训练200/校准50/测试50及人工双审门槛保持不变。本轮独立的12/6探索门槛仅用于工程试训，不是模型可用性的统计论证。', '', '## 同批比较结果','']
metrics=c['metrics'] if c['status']=='completed' else b['metrics']
lines+=['| 方法 | n | Brier↓ | Log loss↓ | AUROC↑ | Accuracy@0.5 |','|---|---:|---:|---:|---:|---:|']
for k,m in metrics.items():lines.append(f"| {names.get(k,k)} | {m['n']} | {fmt(m['brier'])} | {fmt(m['log_loss'])} | {fmt(m['auroc'])} | {fmt(m['accuracy_threshold_0_5'])} |")
lines+=['',f"LLM共同回答集：{c.get('common_answered',0)}/{c.get('test_total',0)}。回答覆盖率：{c.get('coverage',{})}。表内同批n完全一致；若没有共同回答集，仅列统计与训练方法，不能给LLM虚构分数。",'', 'LLM仅收到匿名测试设计特征；监督模型额外学习训练标签。比较是同一个测试任务，训练条件并不相同。匿名化不等于消除了预训练记忆。回顾性分数不能称为前瞻预测准确率。','', 'Brier区间用2000次按资产组重抽样，固定seed20261017；它们只反映已拟合模型在这批选定测试记录上的条件性变化，不包含训练不确定性或标签裁定误差。样本与资产组很少，区间和点估计都不稳定。','']
if b['metrics']:
 best=min((k for k in ['logistic_l2','extra_trees']),key=lambda k:b['metrics'][k]['brier'])
 lines += [f"完整时间留出集上，最佳训练方法的Brier点估计为 {b['metrics'][best]['brier']:.4f}，合并统计基准为 {b['metrics']['beta_pooled']['brier']:.4f}。"+('点估计有所降低，但不构成外部或前瞻验证。' if b['metrics'][best]['brier']<b['metrics']['beta_pooled']['brier'] else '训练模型未优于该基准，暂不升级为正式候选预测模型。'), '']
lines+=['## 字段覆盖与信息边界','', '| 字段 | 训练实值/总数 | 训练不同实值 | 测试实值/总数 |','|---|---:|---:|---:|']
for k,q in b['feature_quality']['train'].items():lines.append(f"| {k} | {q['nonmissing']}/{b['counts']['train']} | {q['unique_nonmissing']} | {b['feature_quality']['test'][k]['nonmissing']}/{b['counts']['test']} |")
lines+=['', '缺失或常量字段不能给模型提供区分能力。尚缺的药物机制、前序疗效、剂量和SAP信息不能靠增加Agent数量补足。', '', '## 排除原则与具体问题','', '- 中期futility停试不自动当作最终主要检验失败；LOTUS、AMEERA5、TrilynX、KEYNOTE641隔离。','- TULIP2和NEPTUNE已知主要目标发生更改，旧快照无法证明精确匹配。','- GRADUATE旧快照104周与116周读出不一致；没有更晚且公布前的匹配快照，不能配成同一目标。','- KEYNOTE859主要终点层级的变化被单独隔离；不能用当前实际人数替代2020年的计划780人。','- 混合、部分成熟、源未核实或缺少历史主要终点文本均不补成二分类训练样本。','', '## 逐记录状态','', '| Trial | NCT | 快照 | 已定位首轮结果 | 标签 | 用途 | 官方结果来源 |','|---|---|---|---|---|---|']
for r in rows:
 split='train' if r['nct_id'] in train else 'test' if r['nct_id'] in test else 'excluded'
 label='成功' if r['label']==1 else '失败' if r['label']==0 else '未知/隔离'
 url=r.get('label_source',{}).get('url');source=f'[官方来源]({url})' if url else '未核实'
 lines.append(f"| {r['trial_name']} | {r['nct_id']} | {r.get('features_as_of') or '缺失'} | {r['first_result_at']} | {label} | {split} | {source} |")
lines+=['', '各条排除的机器可读原因、源文件哈希、模型文件哈希和DeepSeek原始研究判断保留于JSON。旧前瞻预测保持冻结，没有依据本次回放改写。','', '## 复现与发布','', '本地使用Python3.11和scikit-learn1.8.0离线训练；服务器只读取冻结结果，不反序列化模型、不在公共请求中触发API。数据、提示词、协议和模型分别有版本和哈希。','', '已归档原始选行与完整表SHA/ZIP CRC。公开月度归档可重获；为节省本地磁盘，不把全库解压表及临时字节缓存放进发布包。完整表哈希不是可信第三方时间戳，也不是来源签名。','', '```bash', 'python predome_data/historical_v2/history_probe/build_snapshot_features.py', 'python assemble_historical_dataset.py --review predome_data/historical_v2/review_v3.json --output NEW_DATASET.json', 'python predome_pilot.py --data NEW_DATASET.json --output NEW_RUN_DIRECTORY', '# DeepSeek密钥仅由进程环境提供；输出目录必须是新版本，禁止覆盖旧记录。', 'python predome_pilot_llm.py --data NEW_DATASET.json --run NEW_RUN_DIRECTORY', '```', '', '详细优化路线见《Predome_项目优化路线_20261017.md》。下一步优先建设结果无关的采样框、独立失败资产和同适应症前序效应数据，再开展新的、从未用于模型选择的时间留出及真实前瞻评测。','']
reasoning=json.loads((D/'reviews/independent_pilot_llm_reasoning_review.json').read_text())
lines += ['## LLM事实审查与事后诊断','',f"独立审查记录 {len(reasoning['issues'])} 项推理问题，原始API响应和概率未修改。包括无来源的历史成功率、将未知对照断言为非安慰剂、未经SAP确认的多重性规则，以及把时间事件结局一概理解为死亡生存难度。实际调用成功不等于证据合规。",'', '两型号的AUROC均低于0.5，不能把较低的Brier解释为成功的疗效排序。为解释这一点，在看到输出后透明补充固定50%概率的诊断参考：Brier=0.2500、log loss=0.6931；两种DeepSeek均未优于它。这个参考不是方案预设基准，不作为独立优选证据。','', '训练集11/12成功、测试集3/8成功，使高先验基准在本批明显偏乐观。LLM接近中性的输出可能仅因更接近测试比例而降低Brier；不能据此归因于机制推理或泛化。','']
for split,ncts in [('train',train),('test',test)]:
 subset=[r for r in rows if r['nct_id'] in ncts];unknown=sum(r['features'].get('endpoint_family') in [None,'unknown'] for r in subset)
 lines += [f"{split}终点家族存在 {unknown}/{len(subset)} 个缺失或字面unknown。100%缺失的modality/comparator_type、恒定的phase/randomized均没有新增区分信息；有字段不代表已经有药物机制数据。",'']
queue=json.loads((D/'next_dataset_queue.json').read_text())
lines += ['## 下一批数据队列','',f"另有 {queue['candidate_count']} 个已核官方阴性结果候选、{queue['asset_count']} 个资产组，其中新增独立资产 {queue['new_asset_count_vs_frozen44']} 个。全部queue_not_trained；当前registry文件只核身份，不作为历史特征。",'', '| Trial | NCT | 首次官方候选结果日 | 状态 |','|---|---|---|---|']
for q in queue['candidates']:lines.append(f"| {q['trial_name']} | {q['nct_id']} | {q['first_official_result_date_candidate']} | 未训练，需历史目标匹配 |")
lines += ['','此队列为定向补阴性，不能当代表性成功率样本，也不能直接反复用于旧测试集优选。','']

path=ROOT/'docs/Predome_历史训练与验证_20261017.md'
with path.open('x') as f:f.write('\n'.join(lines))
print(path)
