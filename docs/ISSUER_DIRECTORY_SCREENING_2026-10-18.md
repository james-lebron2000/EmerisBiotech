# 交易所英文法定名称补充筛查

本模块弥补中文证券简称不含药物词、但交易所英文法定名称含相关业务词的候选发现缺口。版本为 `issuer-directory-screening-1`，入口为 `issuer_directory_screening.supplement_candidates(market, base_candidates)`。返回值是分类观察，不是药物开发公司名录、已确认申办方、资产权益或临床成功预测。

## 接入与证据约束

- 仅处理 A 股同证券、同当前证券简称、base 分类为 `unclassified` / `uncertain` 的记录。现有种子、种子主体冲突、`excluded`、`candidate`、`drug_developer` 与非 A 股 base 记录保持原样。
- 只消费 `issuer_directory_names.query_evidences` 在同一 SQLite 只读快照内独立重验后的 name receipt。一次调用批量验证候选，来源回放按 source 复用。无回执、当前 hash 变化、证券冲突、来源 metadata / 原件 / 目录快照失效时不增加线索。
- 复用 `issuer_discovery` 的药物、生物医疗、CRO/CDMO、器械及流通关键词规则。英文法定名称有药物词且无混合负面线索时为 `candidate`；生物医疗词或 CRO/CDMO / 器械等混合线索为 `uncertain`。不排除混合集团，不从关键词推出自有药物。英文法定名称中的 `Warrant` 不应用证券权证规则。
- 原始 listing evidence 完整保留。补充 evidence 保存英文与中文法定名称原文、固定来源 URL、raw SHA-256、字节数、源内位置、命中词和方法版本。完整 name receipt 仍由其独立台账保存。
- 补充 evidence 明确 `review_status=pending`、`scientific_review=pending`、`human_sign_off=False`、`registry_alias_proven=False`、`drug_ownership_verified=False`。不生成公司映射、申办方别名或试验记录。
- candidate-bound 的 `candidate_hash`、`receipt_id`、`ledger_id` 及易变的 `source_run_id` / 时间戳不复制进 candidate evidence，避免回执与候选 hash 互相依赖。可独立追溯的出处采用 source URL + raw SHA + locator。

## 写入与 bootstrap

helper 不创建表、不写候选、不联网。`market_expansion.expand` 在 `refresh_candidates` 之前调用 helper；全部候选仍通过现有统一 refresh 写入并保留历史观察与人工 review 状态。

初次没有候选表时，首次 expand 建立 base 候选，随后 enrich 建立 name receipt，再 expand 得到补充分类。因为新 evidence 改变 candidate hash，必须再次 enrich 新 hash。后续有当前回执时应先 enrich、再 expand，不能先把已补充的候选覆盖为 base payload。scheduler 使用的序列由 `market_discovery_pipeline` 管理，并将本模块 VERSION 纳入发现版本签名。

同一 raw 字节的新目录 capture 会生成新 source receipt，但分类 evidence 不含 source run 和候选回执标识，因此在原文与规则不变时语义 candidate hash 稳定。来源后来失效时，本轮不会声称有可用回执；旧的不可变观察和人工决定仍保留可审查。

## 离线工程验证

`tests/test_issuer_directory_screening.py` 使用明确合成的 SSE directory JSON，通过实际 universe capture / snapshot / name receipt 流程验收，不使用真实网络、生产状态或研究数据。覆盖：

- 只读快照中一次批量 proof 验证、无 DDL / 写入，以及输入对象不变。
- `unclassified` / `uncertain` 新增线索；混合 CRO/CDMO / 器械保持 uncertain；法定名称 Warrant 不误判权证。
- 原始证据保留，旧 hash 回执不能授权新候选，重新 enrich 后下一次 payload/hash 稳定。
- 原件截断、来源 URL 篡改、运行状态失败、当前证券名称变化、候选 hash 变化、缺回执均不增加线索。
- 人工 accepted / rejected 决定及其原 review 记录保留，新增观察不冒充人工签署。
- expand 通过统一 refresh 更新分类，但不因关键词新增 companies / trials。

这些结果只证明本地工程契约与失败边界，不证明全市场识别召回率、资产权益、管线完整性或投资收益。

运行方式（复用已有隔离依赖，不安装下载）：

```sh
'/Users/lijinming/Library/Application Support/VirtualBiotech/venv/bin/python' -S /tmp/emeris_ui_test_bootstrap.py -q tests/test_issuer_directory_screening.py
```

源码在本轮仅写入上述 helper、expansion 接线、新测试和本说明；未操作生产、Git 或服务。
