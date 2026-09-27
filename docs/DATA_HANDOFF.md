# 与下载任务交接

平台不下载、移动或修改研究数据。下载任务继续写自己的目录；平台只写 platform 和本机应用状态目录。

## 直接接入

已适配当前下载任务的 `manifest.json` 与 `platform_annotation_manifest.json`。支持 JSON 数组或 datasets/files/downloads 包装，以及 CSV/TSV。

必需字段：dataset_id、version、source_url、local_path、size_bytes、sha256、download_status。status 仅接受明确完成状态（complete/completed/downloaded/verified/ok），不以文件存在推定完成。建议 transfer 使用 .part，完成后原子发布并最后写清单。

可选字段：format、license、evidence_kind、required_columns。evidence_kind 默认 public_observational，合成样例必须为 synthetic。无商业许可说明时记录 unverified。

绝对路径优先；相对路径仅在清单目录、data-root、data-root 父目录三个候选中唯一存在时解析，多解拒绝。解析后的真实路径必须在 data-root 内；符号链接不能越界。

## GEO 样本映射

`samples.csv` 每个矩阵样本一行（包括排除样本）：

```
sample_id,patient_id,baseline,response,include,exclusion_reason,raw_response,source_ref
```

baseline 为 true/false/unknown，response 为 responder/nonresponder/unknown，include 为 true/false。未知不归为 nonresponder。任何重复患者基线样本均阻塞，必须事先明确技术重复的处理。

`probes.csv`：

```
probe_id,gene_symbol,platform_id,source_ref
```

拒绝重复 probe_id、多个基因符号和平台不匹配。读 GEO 平台注释时只选择明确匹配 OSMR/OSM 的基因符号；不将基因名称近似匹配当成确定映射。

`prepare-geo` 使用显式 rules JSON，按每个样本的 characteristic 字段名解析，不假定每一行在所有样本中具有相同含义。输出映射来源、原始字段、规则和父输入哈希。它执行机器来源核对，不等于人工签署。

## 当前仍需关闭的科学缺口

- GSE12251：从源资料确认每个样本的患者身份和基线时间点。
- GSE16879：核对患者标识解析规则、UC 子集及响应的协议定义。
- GSE92415：可用 subject、Week 0、wk6response 和 golimumab 字段开展源标签探索；仍需独立核对协议级 response 定义，才能声明论文复现一致。
- GSE206285：区分 mucosal healing 与 clinical remission，按原论文明确选择终点并处理 placebo，不能擅自合并。
- GSE73661：纵向数据需要确定患者-治疗组唯一键、随访评估窗口及基线响应映射；不能从基线疾病活动度推定后续疗效。

这些科学缺口由研究侧关闭；不要要求下载 agent 编造临床标签。缺少的明确源文件再通过缺口清单交接。
