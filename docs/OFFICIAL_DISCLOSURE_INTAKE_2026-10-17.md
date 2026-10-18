# 冻结官方年报观察的离线接入

本入口将已经逐行视觉核对的 Ascentage 2025 与 CARsgen 2024 两份官方年报观察接入现有 disclosure capture/observation 契约。它只读取既有证据，不执行网络请求、模型调用或生产迁移。两份图共有 **28 个药物—适应症/研究—原文阶段行**，不是 28 个独立药物，也不是当前完整公司管线。

## 固定来源与边界

| source_id | 唯一证券绑定 | 内容日期 | 原文申办方绑定 |
|---|---|---|---|
| `ascentage_annual_pipeline_2025` | `HK:06855`，当前 HKEX 名称 `ASCENTAGE` | `2025-12-31` / `day` | `Ascentage Pharma Group Inc.` |
| `carsgen_annual_pipeline_2024` | `HK:02171`，当前 HKEX 名称 `CARSGEN-B` | 未解 / `unresolved_report_date` | `CARsgen Therapeutics Co., Ltd.` |

只接受代码中固定的 source、URL、bundle 相对路径、证券、发行人和精确申办方配置。事务内再次检查已配置公司与当前 HKEX 证券记录；代码复用、名称/别名改变和来源不符会阻塞。这里的当前证券检查仅针对本地最新已存上市目录，不宣称外部实时刷新。CARsgen 的关系保留 `consolidated_affiliated_entity`，不改写成股权子公司。

两份数据均显式保留 `historical_disclosure=true`、`human_sign_off=false`、`drug_ownership_verified=false`、`scientific_review=pending`。`published_at=null`，旧报告不成为本次导入日期的披露；原抓取时间 `original_retrieved_at` 与 `imported_at` 分开。报告年度不补写成截至日期。阶段原文不统一转换为最高阶段或成功结论。

## 入口与公开契约

```python
from vbt.official_disclosure_intake import ingest_offline, verify_capture, intake_history

capture = ingest_offline(market, 'ascentage_annual_pipeline_2025')
checked = verify_capture(market, capture['id'])
events = intake_history(market, 'ascentage_annual_pipeline_2025')
```

`ingest_offline(market, source_id, *, cancelled=..., on_start=...)` 返回现有 capture 形状，额外提供 `idempotent` 和 `version_id`；已有同来源 lease 时返回 `None`。失败记为 `parse_blocked`，取消为 `cancelled`，失去执行权为 `superseded`，均不发布新观察或删除旧成功快照。

`verify_capture(market, capture_id)` 是纯只读证据门控，返回 `integrity_status=verified|blocked`、`errors`、版本及日期精度。它不初始化表、不改 capture 状态、不修复文件，也不依赖旧输入目录；仅封存归档、冻结印章、不可变 ledger、真实 PDF 字节和数据库行全部一致时通过。公开读取端应调用此门控，不能仅信任 `status=succeeded`。

`intake_history` 读取独立追加的事件。重复调用先重新核查输入、封存证据与当前证券绑定，再返回原 capture；不增加重复行或新版本，同时记录 `verified_existing` 事件。失败尝试仍保留在共享 capture 历史中。历史最新失败不会抹掉之前的准确封存版本。

主模块已注册两个固定 `SOURCES` 条目，使用 `intake_mode=offline_reviewed_bundle`、`parser=frozen_official_pdf_observations_v1`、各自固定 `bundle_relative`，并通过公开 `disclosures.initialize_storage(market)` 初始化共享表。公开 `disclosures.ingest` 在网络入口之前分流至离线适配；测试不再提供初始化兼容 stub。网络抓取不允许请求这两个 URL。

## 发布与恢复

新 namespace 为 `artifacts/market/disclosures/captures/<capture_id>/`。目录包括 PDF `source.bin`、冻结 `observation_bundle.json`、兼容投影 `observations.json`、`provenance.json` 及 `manifest.json`。Manifest 为前四个文件记录实际大小和 SHA；manifest 本身的 SHA、版本与 capture 关联写入 `official_disclosure_versions`，并在 capture metadata 中引用。所有文件 flush/fsync，重新校验归档实际字节后，使用同一 lease 和 `BEGIN IMMEDIATE` 事务一次插入全部观察、完成 capture、写入版本和发布事件。

`official_disclosure_versions`、审计事件与已发布 capture/rows 均有不可变触发器。一个固定来源只能对应一个封存版本；重新审定的改动需要单独来源版本，不能原地覆盖。进程中断留下 running capture 或部分文件时不构成发布，lease 释放/过期后标记 interrupted，重试生成新的尝试，只有完整事务可见。

2026-10-18 的独立审查进一步验证 SQLite 默认 `recursive_triggers=0` 下的普通 SQL 行为。仅拦截 UPDATE/DELETE 不足以禁止 `INSERT OR REPLACE`：本次增加版本表所有唯一键、事件 ID、已发布 capture/row ID 的 INSERT 冲突守卫，并拦截 UPDATE 将其他记录移入已发布 capture 或通过 ID 冲突替换其记录。审计事件不能通过 REPLACE 改写为人工已签署。触发器采用新增名称，已初始化库再次初始化时也可加固；不要求开启全局 recursive triggers。独立读取 gate 仍负责发现越过写入防护后的原件或行篡改，二者分开验证。

当前接入印章 `_APPROVED` 与明确保留的历史审定白名单 `_REVIEWED_SEALS` 分开。新接入必须同时匹配当前印章与源码白名单；读取封存历史版本必须匹配该来源的已知历史印章，再核对 ledger 登记的准确 digest。更换当前接入指针不会自动撤销旧审定历史，ledger 自报 digest 也不能替代源码中的审定许可。真实源码目前只允许两份已视觉核对的原始固定 digest，没有动态学习或任意历史印章放行。

封存文件缺失或损坏会阻止读取，重复接入也不能偷偷覆盖已封存目录。恢复 manifest 所声明的**准确原字节**后，可重新只读验证。仅恢复旧输入目录不能修复封存版本；只有代码和 JSON 的云端部署也不能通过原件验收。恢复必须包括相关归档 namespace、数据库中的不可变 ledger/观察记录，以及执行新接入时需要的原始 input captures。

## 验证

```sh
.venv/bin/python -m pytest -q tests/test_official_disclosure_intake.py tests/test_official_pipeline_observations.py
```

本地结果 **54 passed**，包括 38 项离线接入测试和 16 项原观察包测试；与现有 `test_disclosures.py`、`test_disclosure_view.py` 联合回归 **127 passed**。真实来源回放使用已有两份 PDF 和已冻结 SHA 的 HKEX 目录快照，经公开 `disclosures.ingest` 在小型隔离数据库中发布 2 captures / 28 rows / 2 versions；10 与 18 行均通过封存检查，重复接入未增加行或 capture。公开 Python 读取、界面行选择及独立临时 HTTP `/disclosures` 均返回 28 行，HTTP 200；未触及实际运行中的本地服务。该回放没有复制生产数据库。缺少 ignored 原件的环境会明确跳过这项真实来源回放，绝不自动下载或以合成测试冒充。

其余测试覆盖截断/同大小篡改/缺原件、转录变更、五类封存文件缺失与精确恢复、当前证券/申办方不匹配、事务内身份变化、取消/lease 丢失、中断后恢复、SQL 故障整体回滚、不可变版本、数据库行篡改与只读门控。五类缺失分别是 PDF、观察 bundle、观察投影、provenance、manifest；每类均使只读完整性核验、公开观察及界面行选择阻塞，准确恢复后才恢复可读。额外回归证明新当前指针不撤销已审定历史，但仅有 ledger 或当前指针、没有显式已审定白名单时仍拒绝。合成文件均明确标为工程 fixture；以上结论只证明记录和接入机制，不构成临床验证。

本次子任务未写生产、重启、下载新文件或提交代码。来源原始页面视觉核对与 SHA 详情见 `OFFICIAL_PIPELINE_OBSERVATIONS_2026-10-17.md`。
