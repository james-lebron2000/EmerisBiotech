# Market traversal gap audit and offline English-name receipts

本报告使用生产 SQLite 的 `mode=ro` 连接及一次只读事务。固定观察时间为 **2026-10-17 17:37:14 UTC / 2026-10-18 01:37:14 UTC+8**，文件名沿用本轮 2026-10-17 发布批次。后台采集继续运行，以下数字是快照，不代表以后不变。没有联网请求、生产写入、排队、重启或完整数据库复制。

生产数据源为本机应用目录中的 `f782bd484715/market.sqlite3`。机器可读报告与独立清单保存在被 Git 忽略的 `artifacts/market/gap_audit/2026-10-17/`；该目录不随源码发布，应与原始来源归档一起在交付环境保留。

## 结论与优先级

最高价值的下一步是使用**已经归档的交易所英文法人全称**生成可追溯的查询种子。现有 SZ/SH/STAR 解析只把中文证券简称写入 `listings.name`，丢掉了同一原件中明确列出的英文全称。剩余 282 个 A 股候选中，原简称查询全部返回未知，全部未尝试；这并不意味着 282 家都属于药物开发公司。

本轮已实现独立模块 `src/vbt/issuer_directory_names.py`，不改中文展示名、候选分类、公司映射或试验。root 负责后续 resolver/traversal 接线及生产验收。

按价值排序的后续工作：

1. 接入本模块的已验证英文全称，查询返回**待审建议**；冻结查询名称、receipt 和归一化结果。不能自动接受 A/HK 映射。
2. 对已被保守门禁保留的 US 建立业务类型审查：63 条 exact 建议因 `uncertain` 未获自动接入。这一批混有生物药企业、器械和服务主体，不能整体放行。
3. 处理 27 个未映射的歧义查询主体，先区分法律后缀、具体子公司、合作医院和无关命中。4 条现有 pending 建议因非 exact/歧义被保留，不能通过放宽 fuzzy 阈值解决。
4. 对完整法人与简称筛选冲突的 A 股、HK 缩写主体补充结构化身份和业务证据；保留当前 unknown，而不是机械重试同一个简称查询。
5. 中国境内管线仍需官方披露和 CDE 等来源。已有 CDE 来源审计记录访问验证页限制，CTGov 零结果不能代表零管线。

## 固定快照中的质量缺口

已配置公司 336 条证券主体记录，其中 A 股 34、HK 43、US 259。不同证券可能属于同一集团，不能把这些记录数称为独立生物科技公司或完整管线数。

| 范围 | 未映射候选及阻点 | 实际含义 |
|---|---|---|
| A 股 | 110 candidate + 172 uncertain，282 条中文简称均不能生成当前英文 query | 解析丢字段，不是 282 次查询失败，更不是没有临床管线 |
| HK | 17 candidate + 23 uncertain；21 个已查零结果、1 个歧义、18 个无法形成简称 query | 官方证券简称常为缩写；零结果和没有身份不能合并 |
| US | 63 个 uncertain 主体有 pending normalized_exact 建议 | exact 名称不能证明药物开发业务；AEMD 等混入说明不能直接把 uncertain 放行 |
| US | 14 candidate + 12 uncertain 查询歧义；另 HK 1 个 | 共 27 个主体，需要对关系与不同 alias 单独审查 |
| US | 当前待自动接入且通过完整 `_gate` 的未映射建议为 0 | 本快照自动队列已消化可接受的 exact 候选；不能靠重复排队产生新的证据 |
| 历史人工来源批次 | v1 23 条 + v2 25 条，48 条全部已映射 | 重新安装已接受批次不会扩大覆盖 |

当前 candidate 分类分布以及 resolve/identity/company 历史任务分布均在 `report.json`。任务累计成功数包含同一主体重复采集，不能当成公司覆盖分母。报告没有把历史失败任务解释为当前 worker 死亡，也没有以空 running 状态证明 worker 健康。

## 已归档、可直接用于查询种子的英文主体清单

本轮只重放 **已成功 run** 的归档：SZ 2,906 行、SH 1,702 行、STAR 618 行，共 5,226 行。原始字节 SHA、metadata SHA/bytes/URL、解析数量、sealed snapshot digest，以及当前对应来源的完整 listings digest 均通过校验。原件三者都存在命名明确的英文全称字段，无需翻译。

| 来源 | 明确字段 | 用途限制 |
|---|---|---|
| SZSE XLSX | `A股代码`、`A股简称`、`公司全称`、`英文名称`、`公司网址` | 网站仅保留 reference，本模块不打开网站 |
| SSE SH/STAR JSON | `A_STOCK_CODE`、`COMPANY_ABBR`、`FULL_NAME`、`FULL_NAME_IN_ENGLISH` | 不用 `COMPANY_ABBR_EN` 冒充法人全称 |
| BSE 原始目录 | `xxywjc` | 只有英文简称，独立列出，不送入本模块的法人全称门禁 |

从未映射主体中，独立清单 `english_legal_name_candidates.json` 提取 **338** 条有完整英文全称的优先核对记录，约 490 KB。其中 **252** 条已在当前候选范围，另外 **86** 条因原简称无关键词而为 unclassified，但全称/行业存在药业关键词。它们仅是重新筛查建议，不是经过验证的药物开发主体。每条包含当前 candidate hash、精确 ticker/简称、原始 URL/SHA、文件路径及 row locator。

下列例子均直接来自交易所原件：

| 证券 | 当前简称 | 目录英文全称原文 | 未证明项 |
|---|---|---|---|
| A:300122 | 智飞生物 | `Chongqing Zhifei Biological Products Co.,Ltd.` | exact lead sponsor spelling、运营子公司及完整管线 |
| A:002007 | 华兰生物 | `Hualan Biological Engineering Inc.` | exact lead sponsor spelling、疫苗/血制品子公司关系 |
| A:300009 | 安科生物 | `ANHUI ANKE BIOTECHNOLOGY (GROUP)CO.,LTD.` | 注册申办方 alias、具体权益 |
| A:688578 | 艾力斯 | `SHANGHAI ALLIST PHARMACEUTICALS CO., LTD` | 历史 bounded query 返回医院 lead，不能据此归属其管线 |
| A:300765 | 石药创新 | `CSPC Innovation Pharmaceutical Co.,Ltd.` | 不能将 CSPC Zhongqi 或整个石药集团管线直接复制给这个上市主体 |
| A:603087 | 甘李药业 | `Gan & Lee Pharmaceuticals.` | 已验证名字仍可能被原简称 query 防猜测规则拒绝；不能改写为自猜 subsidiary |

原 query 归一化函数可对 338 条中的 332 条生成查询，其余 6 条必须作为明确边界测试。其中 `Gan & Lee` 两个短姓触发最短 distinctive token 限制；`Hunan Warrant Pharmaceutical Co., Ltd.` 的 `Warrant` 被证券尾缀清理规则截断。不能为了这些已验证全称而全局放宽未经证明的短简称。建议 root 对 receipt 支持的英文全称设置独立的保守 tokenizer，不做翻译、品牌展开或证券尾缀猜测，再保存原文和归一化 query 两者。

## False-positive 风险与漏网主体

完整中文名使 10 条当前 uncertain 候选显露明确的“医疗器械”名称证据，见独立清单中的 `device_keyword_in_full_metadata`。例如 A:300314 戴维医疗对应 `宁波戴维医疗器械股份有限公司 / Ningbo David Medical Device Co.,Ltd.`，A:688236 春立医疗对应 `北京市春立正达医疗器械股份有限公司`。这些适合进入业务范围复核，不能计为药物开发覆盖。**也不能按一个器械关键词自动永久排除混合业务集团**；如需归属其制药子公司，必须另有精确关系证据。

农业、批发零售、科研服务行业同样需要分层复核；行业代码能用于优先级，不能独自证明公司没有药物资产。HK `MEDICAL`、`HEALTHCARE`、`BIOTECH` 缩写本身不足以证明药物开发，本轮没有为缺少完整法人/业务来源的 HK 主体编造英文名或做自动接入。

既有来源审计已明确记录的六个缺口另存 `known_china_source_gaps.json`。其中 CMS（HK:00867）、JW THERAP-B（HK:02126）、MABPHARM-B（HK:02181）、艾力斯（A:688578）、石药创新（A:300765）当前仍是 **unclassified**，不会因已知待核事项自动进入关键词候选。这证明只遍历现有 keyword candidate 无法宣称全市场完成。亿帆（A:002019）为 uncertain，其 broad 查询出现个人、医院等 lead，尚无可接受的 subsidiary 关系。

## 模块接口与版本策略

```python
from vbt.issuer_directory_names import enrich, query_evidence, query_name, query_names

# 显式离线建账；root 决定在何时调用。不会查询网络或改 companies/trials。
result = enrich(market, source='all')  # all / SZ / SH / STAR

# candidates 应为当前数据库行，至少有 id、content_hash；自报英文 payload 无效。
batch_names = query_names(market, candidates)  # {candidate_id: English原文}
proof = query_evidence(market, candidate)     # 同一次校验的完整 receipt 或 None
raw_name = query_name(market, candidate)      # 委托 query_evidence
```

`query_evidence` 至少返回 `english_name`、`receipt_id`/`ledger_id`、`source`、`source_run_id`、`raw_sha256`、`candidate_hash`，并包含 metadata SHA、来源 URL、原件位置、中文全称与网站 reference。root 可将该 JSON 连同归一化 query 冻结到 `sponsor_resolution_runs.query_evidence_json`。实际发起请求时应使用同一份 proof，不在两次独立查询间混合名称与证据版本。

最小接线为：原短名 query 不可用时，调用 `query_evidence`；通过验证的英文全称可生成 bounded registry query。遍历 readiness 使用 `query_names` 一次批量查询；每个参与的 source 在一次调用中只解析重放一次。`query_*` 不创建 schema，不写数据库，也不调用 `enrich`。

新增表只有 `issuer_directory_name_receipts` 与 `issuer_directory_name_audits`，均有禁止 UPDATE/DELETE 的触发器。receipt ID 为 canonical payload SHA；每份凭据绑定 candidate hash、完整当前 listing/snapshot、成功 run、原始字节与 metadata。重复 enrich 相同版本为 unchanged。

**重建策略：** candidate hash 改变、证券简称改变、原始字节/metadata 改变、当前 snapshot 切换或 run 不再成功时，旧 receipt 保留历史但不再用于查询。重新 `enrich` 会对当前证据追加新凭据，不更新旧记录；没有可重新验证的来源时保持未知，不重新翻译或迁移旧 alias。新 capture 即使英文名字相同，也需要新的 source-bound receipt。

## 工程与真实归档验收

专属测试 **46 项通过（2.04 秒）**，验证三个来源、重复建账、source/timestamp/count/hash/bytes/URL 校验、当前候选版本、名字冲突、缺字段、目录截断、路径与 symlink、伪造自报 payload、伪造 ledger、只读查询、追加审计及批量每来源只校验一次。合成 fixture 不构成药物研发或预测验证。

另外，在临时小 SQLite 中放入真实已归档的三个目录及各一条候选（智飞、甘李、艾力斯），完成实际 `enrich → query_names/query_evidence → 再次 enrich`：新增 3 条 receipt，重复新增 0 / unchanged 3；公司 0、试验 0。临时 workspace 已删除。小型报告为 `issuer-directory-names-acceptance.json`，其记录了真实来源 run/SHA 和 receipt，**未将其计入生产覆盖**。

机器可读交付：

- `report.json`：固定观察时点、全部统计分母及范围。
- `english_legal_name_candidates.json`：338 条优先核对的原目录法人名。
- `bse_english_short_name_only.json`：32 条仅英文简称，不冒充法人名。
- `held_unmapped_proposals.json`：67 条保留建议与具体 gate 原因。
- `unmapped_ambiguous_resolution.json`：27 个歧义主体和来源 run。
- `known_china_source_gaps.json`：6 个已有审计缺口及当前分类/hash。
- `source_capture_integrity.json`：本轮检查的 22 份原始 capture（SZ/SH/STAR 各 1，BSE 19）。
- `issuer-directory-names-acceptance.json`：三个真实归档目录的隔离模块验收。

以上工程结果没有证明申办方集团关系、药物权益、全市场完整性、临床结局预测能力或投资回报。

## 2026-10-18 接线与接受保护

官方英文法人全称现使用独立的 `query_name_from_directory`：仅对已重验 receipt 的原文运行 `_tokens`，输出长度 4–200 的 `[a-z0-9 ]` 查询。不运行证券尾缀裁剪或短品牌猜测规则；`Gan & Lee`、`Hunan Warrant`、`PKU HealthCare`、`C.Q.`、`KPC`、`Apt Medical` 六条均有边界测试。原 `query_name_from_listing` 的保守规则没有放松。A 股显示简称无法生成查询时，才使用已验证英文全称；缺少当前有效 receipt 时保留 `needs_name`/`blocked`。

新增 `candidate_query_contexts(market, candidates, *, db=None)` 以一次批量校验取得查询原文、规范化 query、来源类型和同一份 proof。`query_evidences(market, candidates, *, db=None)` 返回 `{candidate_id: proof}`，供批量筛查和事务内审查使用。提供 `db` 时，两者都不另开事务、不修改 query_only、也不提交调用者事务；查询只按已有账本与当前来源验证，不创建 schema 或联网。遍历与候选卡片均使用批量接口，每个涉及的目录在一次调用中最多重放一次。

每个 `sponsor_resolution_runs` 冻结 `query_evidence_json`，包含原文、query、候选 hash、来源类型及英文名 receipt。发布建议前在当前写事务中重新检查候选版本与可验证查询；失败保留原始 registry capture/观察，但不发布新建议。接受建议前也在同一写事务内重新验证，避免检查后名称输入变化仍写入公司。

查询失效与候选 hash 变化分开报告。当前可验证 query 与历史 query 不一致、proof 不再可用或 frozen context 不能绑定查询时，旧建议为 `query_input_stale`，界面隐藏接受入口，后端拒绝接受。显式排除建议仍可记录。完整目录重新抓取并重建 receipt 后，若候选 hash 与规范化 query 相同，仅 receipt ID 变化不会迫使已核对建议无端失效。历史空 `{}` context 明确显示“历史记录未保存”；它不会被伪装为已有来源凭据，且接受仍要求当前查询相符。

调度 signature 同时绑定名字模块、英文名筛查模块与查询归一化版本。仅源范围或版本变化时执行 `enrich → expand → enrich`；新增 receipt 时再做一遍筛查与最终 hash 的 enrich，支持首次没有候选的数据库。被阻塞的目录记为显式 partial 事件并保存已尝试 signature，不在每次 tick 无限重放。新来源/代码版本会触发重试，修复原件后的主动重试可用 `market directory-names SH|SZ|STAR|all`；该命令仅离线建账，blocked/partial 返回退出码 2。手动 `market expand` 本身不承诺完成名字 bootstrap；应核对有效 receipt 或使用完整调度链。

名字账本模块 `VERSION=issuer-directory-names-2` 为 receipt/audit 两表增加 INSERT 冲突门禁，实际测试 `recursive_triggers=OFF` 时的 `INSERT OR REPLACE` 也无法覆盖旧行。重复建账在写事务中先读取并比对旧 payload，成功时报告 unchanged；发现冲突则阻塞，不利用 IGNORE 掩盖异常。旧版本 receipt 保留历史，迁移后需为当前来源/候选重新 enrich。

本节是代码与合成工程验收范围。本轮未写生产数据库、未重启服务，也未抓取网络来源；不能据此增加生产覆盖数字或声明药物权益已经证明。

接线联合回归 **295 项通过（14.93 秒）**：名字 receipt、独立英文名筛查、检索/建议、事务内接受、遍历与调度、申办方界面、自动身份采集/队列和 curated reconciliation。首次无候选表的 bootstrap 已实际贯通；再次 tick 不重放目录，目录时间刷新后候选 hash 稳定且最终 receipt 可重验。全部为隔离合成工程测试，和上文三个真实归档的小样本验收分别记录。
