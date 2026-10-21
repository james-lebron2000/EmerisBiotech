"""Build a transparent cohort/readiness + real frozen forecast release.
This builder never fits a model, calls an API, or turns pending labels negative.
"""
from pathlib import Path
from datetime import datetime,timezone
import argparse,json
from continuous_audit import ROOT,DATA,CONTRACTS,digest,contract_hashes
from continuous_model import partition
from predome_pilot import sha,write_new

REPORT=ROOT/'docs/Predome_连续队列回测与前瞻_20261019.md'
PLAN=ROOT/'docs/Predome_70目标与优化实验方案_20261019.md'

def build(dataset_dir,benchmark_dir,seen_path):
    dataset_dir=Path(dataset_dir);benchmark_dir=Path(benchmark_dir)
    rows=json.loads((dataset_dir/'dataset.json').read_text());audit=json.loads((dataset_dir/'source_audit.json').read_text())
    benchmark=json.loads((benchmark_dir/'report.json').read_text())
    if benchmark['dataset_sha256']!=sha(rows) or audit['dataset_sha256']!=sha(rows):raise ValueError('Benchmark/dataset binding mismatch')
    seen=json.loads(Path(seen_path).read_text());groups,_=partition(rows,set(seen['nct_ids']),set(seen.get('asset_groups',[]))|set(seen.get('asset_components',[])))
    split={r['nct_id']:s for s,rs in groups.items() for r in rs}
    forward_path=DATA/'prospective/frozen_forecasts_v1.json';envelope=json.loads(forward_path.read_text())
    if sha(envelope['record'])!=envelope['sha256']:raise ValueError('Forward freeze failed')
    prospective=envelope['record'];annual=json.loads((DATA/'annual/union_flow.json').read_text())
    total=len(rows);label_n=sum(r['label_status']=='source_adjudicated_primary' for r in rows)
    flow={'universe_n':total,'definitive_label_n':label_n,'unknown_outcome_n':total-label_n,
          'not_qualifying_primary_label_n':total-label_n,'label_ascertainment_coverage':label_n/total,
          'meaning':'Not-qualifying includes pending, unknown, out-of-window, mismatched and non-efficacy statuses; not all are unreported trials.',
          'prior_RCT_numeric_n':sum(r['selected_prior_record'] is not None for r in rows)}
    annual_flow=[]
    for year in range(2021,2026):
        sub=[r for r in rows if r['planned_primary_completion_date'].startswith(str(year))]
        known=sum(r['label_status']=='source_adjudicated_primary' for r in sub)
        annual_flow.append({'year':year,'planned_n':len(sub),'definitive_results_n':known,'unknown_n':len(sub)-known,
                            'role':'按实际首次决定性结果年分组；不由本列计划年推断'})
    ncts={s:[r['nct_id'] for r in v] for s,v in groups.items()}
    count=benchmark['counts'];cal=count['calibration'];train=count['train']
    percent=lambda v:'拒答' if v is None else f'{v*100:.1f}%'
    lines=['# Predome Agent System：连续队列、模型就绪检查与前瞻预测',
           '',f'生成时间：{datetime.now(timezone.utc).isoformat()}。发布版本0.10.0。',
           '',f'已形成{total}项按固定规则纳入的年度存量并集，目前{label_n}项合格主要终点来源标签；未形成合格标签的{total-label_n}项保留具体状态。不能把队列规模称作已训练样本数。',
           '',f'本次实际运行状态：`{benchmark["status"]}`。资产隔离后2021–2022训练保留{train["n"]}项（阳性{train["success"]}、阴性{train["failure"]}，{train["assets"]}资产），2023独立校准保留{cal["n"]}项，2024–2025新独立测试{count["test"]["n"]}项。',
           '', '**本次尚未完成新模型训练、校准及2021–2025新保留测试回测，无法报告新准确率，更未证明70%。** 入口实际执行来源/时钟检查并触发预设门槛；没有调低门槛或用未知补阴性。旧历史重放仍保留在网站原分区，不能当作新的独立验证。',
           '', '## 数据与输入',
           '', '固定15家MNC、随机III期、基线计划主要完成日期在2021–2025年的AACT年度存量。原2020队列338项，新增639项，总977项；逐项取最早合格快照。2022-01-01固定归档404，未替换、未计为零。该并集不代表全行业五年所有读出。',
           '', f'已实际抓取全部977项当前注册资料，596项包含主要结局统计分析条目；这些条目是待审证据，自动最终标签为0。原始数据和完整检索/裁定队列位于独立研究空间。',
           '',f'当前历史前期RCT数值输入覆盖{flow["prior_RCT_numeric_n"]}项；缺失不是零疗效。输入只能来自各项最早基线之前。来源日期、效应单位、CI水平、随机性、研究角色和A/B适用性分别核实。',
           '', '历史设计输入包括计划入组、随机性、主要终点数、疾病/终点类别及可核实的对照信息；不使用当前实际入组、结果文本、药价、股价或后来的结论。研究药物角色可能与注册armType相反，未经确认的modality保持未知。',
           '', '## 70%目标与训练方法',
           '', '保留四个预设设计/前期证据Logistic与Extra Trees模型。唯一新增主候选为训练集中按药物资产留一产生OOF预测后拟合的非负凸组合，权重按Brier，分类阈值按训练OOF平衡准确率固定网格学习。组合层在相同OOF标签上的分数不是独立验证。',
           '', '2023仅校准概率，不决定模型、权重或阈值。预设最低50项、各类20项、10资产及每类5资产等条件不足则拒绝校准。测试方向在模型/阈值/概率冻结后读取一次。准确率、平衡准确率、基线、多数类参考、Brier和资产95%区间同时报告。',
           '', '更详细的输入字段、assurance/监督学习/LLM三路径、增益消融、自进化和达标规则见同包《Predome_70目标与优化实验方案_20261019.md》。',
           '', '## 已实际冻结的未来预测',
           '', f'输入证据截至{prospective["evidence_as_of_utc"]}，预测冻结于{prospective["frozen_at_utc"]}。{prospective["trial_count"]}项试验拆为{prospective["target_count"]}个目标假设；两个模型同厂商，不能视为独立生物学重复。概率未校准，尚无真实结果命中率。',
           '', '| 试验/目标 | NCT | 读出窗口 | DeepSeek Flash | DeepSeek Pro |', '|---|---|---|---:|---:|']
    for c in prospective['cases']:
        label=c['trial_name']+' / '+c['case_id']
        lines.append(f'| {label} | {c["nct_id"]} | {c["readout_window"]} | {percent(c["forecasts"]["deepseek-flash"]["probability"])} | {percent(c["forecasts"]["deepseek-v4-pro"]["probability"])} |')
    lines.extend(['', 'AVANZAR的四个主要人群/终点假设相关，不能相乘或转换成整项研究的成功概率。H2/全年/H1指导是公司披露区间，非确定公告日；部分H1 2027区间可能越过六个月边界，OS成熟也不能由首次读出指导推断。',
                  '', 'OCEAN为EGPA对mepolizumab非劣效目标；VAYHIT1为SROT12；BEYOND为104周eGFR变化。所有当前目标、未知α/NI/SAP/精度与迁移假设保留原证据和原输出。所有统计assurance保持拒答，未将间接B级前期效应冒充目标精度。',
                  '', '两次原始API调用及一次事先限定的事实修复均保留。独立Agent逐条复核，剩余major事实问题为0；Pro对OCEAN、BEYOND的拒答原样保留。事实审查通过不等于预测有效。IRIS强化期/CRR0.5限定另有小注。',
                  '', '## 尚未完成的工作与验证边界',
                  '', '继续按原队列、同等检索规则完成主要终点标签与前期RCT证据；保留终点变更、首次部分披露、NI/多重性和不确定资产。不得为了提高分数挑选阳性、阴性或容易核实的研究后称全体能力。',
                  '', '临床预测validated=false；独立人工双审0。本次软件与数据来源QA只验证各自层级。共享工作区封存/本地UTC和SHA不构成外部访问隔离、第三方双盲或可信时间戳；公共发布回执另外记录。',
                  '', f'前瞻记录SHA-256：`{envelope["sha256"]}`。完整连续队列记录与依赖文件哈希见JSON导出。'])
    REPORT.parent.mkdir(exist_ok=True)
    if REPORT.exists():raise FileExistsError('Do not overwrite release report')
    REPORT.write_text('\n'.join(lines)+'\n')
    manifest={}
    def add(path,expected=None):
        path=Path(path);path=path if path.is_absolute() else ROOT/path
        rel=str(path.resolve().relative_to(ROOT))
        if path.is_symlink():raise ValueError('No symlink release artifacts')
        h=digest(path)
        if expected and expected!=h:raise ValueError('Dependency hash changed: '+rel)
        if rel in manifest and manifest[rel]['sha256']!=h:raise ValueError('Conflicting artifact')
        manifest[rel]={'path':rel,'sha256':h,'bytes':path.stat().st_size}
    for name in CONTRACTS:add(DATA/name)
    for name in ['union_universe.json','universe_source_manifest.json','annual/union_source_manifest.json','annual/verification_receipt.json','annual/union_flow.json','seen_inventory.json','derived_seen_component_manifest.json','derived_seen_component_source_manifest.json','derived_seen_component_verification.json','historical_prior_selections_v1.json','historical_prior_selections_v1.md']:add(DATA/name)
    for file in ['universe_source_manifest.json','annual/union_source_manifest.json']:
        for item in json.loads((DATA/file).read_text())['artifacts']:add(item['path'],item['sha256'])
    for item in json.loads((DATA/'derived_seen_component_source_manifest.json').read_text())['artifacts']:add(item['path'],item['sha256'])
    for name in ['reviews/prior_selection_independent_review_v1.json','reviews/union_scope_independent_review.json','reviews/seen_component_independent_review_v1.json']:
        if (DATA/name).exists():add(DATA/name)
    for path in [dataset_dir/'dataset.json',dataset_dir/'source_audit.json',dataset_dir/'flow.json',benchmark_dir/'report.json',forward_path,REPORT,PLAN,Path(seen_path)]:add(path)
    add(audit['scaffold']['path'],audit['scaffold']['sha256'])
    for item in prospective['artifact_manifest']:add(item['path'],item['sha256'])
    sources=json.loads((DATA/'prospective/source_manifest.json').read_text())['sources']
    for item in sources.values():add(item['path'],item['sha256'])
    for name in ['continuous_dataset.py','continuous_audit.py','continuous_model.py','continuous_ensemble.py','continuous_time.py','build_effective_seen.py','freeze_continuous_forecasts.py','build_continuous_release.py','requirements-research-v4.txt']:add(ROOT/name)
    for r in rows:
        prior=r.get('selected_prior_record')
        if prior:add(prior['source']['local_path'],prior['source']['sha256'])
    # Only source-adjudicated labels; never publish pending stewardship directions.
    scaffold=json.loads((ROOT/audit['scaffold']['path']).read_text())
    for r in scaffold['records']:
        if r.get('eligible'):
            for item in r.get('source_descriptors',[]):
                if item.get('path') and not Path(item['path']).is_absolute():add(item['path'],item['sha256'])
    record={'id':'predome-continuous-20261019-release1','version':'0.10.0','created_at_utc':datetime.now(timezone.utc).isoformat(),
            'release_scope':'continuous_cohort_and_model_readiness_plus_frozen_prospective_research','clinical_prediction_validated':False,
            'protocol':{'effective_contract_hashes':contract_hashes(),'sampling':'fixed_annual_earliest_baseline_union','annual_flow':annual},
            'flow':flow,'benchmark':benchmark,'annual_flow':annual_flow,'prospective':prospective,
            'dataset_rows':[{k:r.get(k) for k in ['nct_id','trial_name','sponsor_group','asset_group','investigational_components','planned_primary_completion_date','features_as_of','decisive_primary_at','decisive_primary_interval','label','label_status','adjudication_status','prior_evidence_status']}|{'split':split.get(r['nct_id'],'excluded')} for r in rows],
            'artifact_manifest':list(manifest.values()),'labeling_is_incomplete':True,'all_universe_records_retained':True,
            'not_a_completed_2021_2025_backtest':not benchmark['model_training_completed']}
    write_new(DATA/'release.json',{'record':record,'sha256':sha(record)})
    print(json.dumps({'sha256':sha(record),'universe':total,'source_adjudicated':label_n,'status':benchmark['status'],'artifacts':len(manifest),'forecast_hash':envelope['sha256']}))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('dataset_dir');p.add_argument('benchmark_dir');p.add_argument('seen');a=p.parse_args();build(a.dataset_dir,a.benchmark_dir,a.seen)
