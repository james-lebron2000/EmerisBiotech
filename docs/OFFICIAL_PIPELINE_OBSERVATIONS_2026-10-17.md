# 官方年报管线披露观察包验收记录

本次仅使用已归档的公开文件，未下载新文件、调用模型、接入生产数据库或提交代码。共转录两个选定管线图的 **28 个适应症/研究行**，由本次助手阅读真实 PDF 渲染页逐行核对；覆盖所选图的全部行，并非对整份年报或公司全部管线做全量审查。此项是披露转录与证据完整性检查，`human_sign_off=false`，科学审查仍为 `pending`。

| 发行人及原始文件 | 物理 PDF 页 / 印刷页 | 所选图范围 | 内容日期 |
|---|---|---|---|
| [Ascentage 2025 年报](https://ir.ascentage.com/static-files/cc6eb0ea-f444-4765-9efb-bb117b570f7b) | 17 / 16 | 7 个药物单元格，10 个适应症行 | 图前原文明确 `as of December 31, 2025`；记录为 2025-12-31 |
| [CARsgen 2024 年报](https://www.carsgen.com/media/3vsj00ph/2024-annual-report.pdf) | 10 / 9 | 10 个药物单元格，18 个适应症/研究行 | 原文仅 `as of the date of this report`；准确日期未解，保留未知 |

两份年报均属于历史披露，公开日期均未独立确立。下载时间不能替代披露日期；2024 报告年度不能替代 CARsgen 图表的准确截至日期。

## 交付与恢复

- `examples/ascentage_pipeline_2025_observations.json`：药物、适应症、阶段原文及每行来源 URL、SHA、页码、行号、PDF 点坐标。原文阶段包含 `Clinical POC`、`Registrational Trial`、`Marketed`，均未改写为标准临床分期。
- `examples/carsgen_pipeline_2024_observations.json`：保留研究/地域标签、`Pre-clinical`、`Undisclosed`、合并列 `Phase II/III`、`BLA/\nNDA` 及原文 `launched` 注记。`Phase II/III` 未转成三期；`Undisclosed` 未补写靶点。
- `src/vbt/official_pipeline_observations.py`：只读离线检验与兼容投影，不执行采集或入库。冻结观察包 digest；检查相对原件路径、大小、PDF magic、SHA、页码/坐标与原文关联。原件缺失、篡改、路径越界或未审定转录均返回 blocked，不产生可用观察行。
- `tests/test_official_pipeline_observations.py`：小型离线契约和证据门控测试；合成 PDF bytes 仅用于工程完整性测试，不作为临床证据。

原件恢复到工作区内以下既有路径后才能获得 `verified`；仅复制代码与 JSON 到云端会明确受阻，不能报告为零管线：

| 相对原件路径 | SHA-256 |
|---|---|
| `artifacts/market/china_expansion_v2/2026-10-17/ascentage5-official.pdf` | `6733d1ec4042e66e9c8f76e7040b345b75d7abd4bd514b4186cd698c420e3068` |
| `artifacts/market/china_expansion_v2/2026-10-17/carsgen2-official.pdf` | `d3c0673f20bf5864ecfcb4ae3e6fda1a71c682617ee4b97585755da8c05fb036` |

在安装本项目环境中，可用以下只读方式检查，或取得尚未入库的兼容行：

```python
from pathlib import Path
from vbt.official_pipeline_observations import inspect_bundle, disclosure_rows

workspace = Path.cwd()
path = workspace / 'examples/ascentage_pipeline_2025_observations.json'
checked = inspect_bundle(path, workspace)
assert checked['integrity_status'] == 'verified', checked['errors']
rows, metadata = disclosure_rows(path, workspace)
```

## 视觉与工程验收

使用 Poppler 仅渲染上述两页并阅读实际图片，28/28 行逐行核对，未采用抽样。当前可读行 28，因列归属不明而阻塞的行 0；这只描述所选图的转录，不能推及未审阅页面。CARsgen CT041 的 IIT 行记录其横条终点所在的 `Phase I` 列，未推断阶段完成或升级。合并药物单元格跨行沿用明确可见药名；同一格逗号分隔的适应症保留原文，不拆成额外资产。

本地原件检验结果为 Ascentage 10 行 verified、CARsgen 18 行 verified。定向工程测试命令：

```sh
.venv/bin/python -m pytest -q tests/test_official_pipeline_observations.py
```

结果：**16 passed**。覆盖冻结 digest、原文与未知日期、同大小篡改/截断/缺原件、转录变更、绝对路径/目录越界/符号链接、失效坐标、行 SHA 不匹配、越界分期/发布日期推断，以及阻塞行保留。没有运行大规模测试。

渲染图片保存在本地 `output/pdf/official_pipeline_ascentage_2025/`，每份 JSON 记录相应图片 SHA。图片是年报原始页面的 QA 副本，不是产品图，不随本次公开源码提交；恢复原件后可重新渲染核对。

## 后续接入边界

尚未编辑 disclosures、UI 或核心入库逻辑，未发布新观察。兼容投影保留 `original_observation`、历史日期精度、未知字段和阻塞行，供后续独立接入审查。任何来源的历史阶段、图中 Marketed/launched、注释中的权利描述，都不构成当前权益核定、最高阶段推断、临床成功验证或投资结论。两份观察包均保留 `drug_ownership_verified=false`、`clinical_phase_code=null` 和 `scientific_review=pending`。
