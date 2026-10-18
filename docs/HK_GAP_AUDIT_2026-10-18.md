# 港股主体名称与关联缺口审计

本次生产检查仅使用 SQLite `mode=ro` 的读事务，没有构造生产 Market、写生产状态、操作队列、接受映射、重启服务或下载研究数据。计数属于 2026-10-18 15:30–15:55 UTC 的快照，后台任务仍可改变状态。

## 已有规模与未关联范围

- HKEX 当前证券快照 2,833 条，不能将证券数当成药企数。
- 已配置港股公司 43 家，43 家均有登记观察，共 2,778 条。最近一次公司采集 39 家成功、4 家失败；旧观察不能代表本次刷新成功。
- 活跃未关联候选 40 家，其中 `candidate` 17、`uncertain` 23，没有未关联的 `drug_developer`。
- 17 家 candidate 中，11 家当前名称查询为 `zero`、6 家没有绑定当前 hash 的查询运行。23 家 uncertain 中，10 家 zero、1 家 ambiguous、12 家无当前运行。
- HK 没有 pending sponsor proposal；既有一条 AIM 的 accepted 提案采用 `curated_official_source_review`。未将既有公司配置普遍重新认证为自动身份审查。

## HKEX 现有原件不能提供法定全称

当前成功 run `ba4ea2e80f234db6b203137b377d450a` 的原件为 runtime 下的 `artifacts/market/captures/<run>/0.bin`：1,358,498 bytes，SHA-256 `43900020cb53d80d3cf90ad67819e7edc9aa9a8cbc2081afe1395531b5db7906`，抓取时间 `2026-10-17T17:20:18.658855+00:00`。

原件大小、SHA、2,833 条规范化记录与当前 listings、成功 run、snapshot 行数和 rows SHA 全部重放一致。18 个原始字段包含 Stock Code、Name of Securities、Category、ISIN 等，没有独立公司法定英文全称。不得把证券简称扩写、翻译或拼接成法人名。

原件 `sheet1!A2` 为 `Updated as at 20/10/2026`，内容日期晚于本次审计日。该事实与字节完整性分开：日期异常不删除既有 HK 记录，也不能用 retrieved_at 替代内容日期。新增 `directory_audit` 会把此情况返回为 `verified_bytes_date_anomaly` / `future_dated`。

## 可执行新来源

使用 HKEXnews 官方[南向持股页面](https://www3.hkexnews.hk/sdw/search/mutualmarket.aspx?t=hk)的服务器端 HTML，严格读取 `#mutualmarket-result` 中的 Stock Code、Name 和页面 Shareholding Date。它是已观察持股子集，官网也明确这不是南向合资格证券全名单；不能声称覆盖全部港股。

2026-10-18T15:34:02.580655+00:00 的有界真实 GET 返回 HTTP 200，645,231 bytes，SHA-256 `e472b088ffb8ef6307537afa861d9bc4cc50588ccee87fabf90a5b31530ec080`，内容日 2026-10-16，完整解析 1,018 行。系统 TLS 验证保留，不跟随重定向，不登录、不绕访问限制。原件、响应头及 HTTP/curl 状态保存在 ignored `artifacts/market/hk_name_source_audit/2026-10-18/`。

17 家未关联 candidate 中有 10 家直接提供英文主体名称；23 家 uncertain 中有 11 家提供名称，但业务分类保持 uncertain。Name 字段作为 `hkex_reported_english_issuer_name` 原文使用，不升级为公司注册证明或临床申办方关系。

| 证券 | 官方 Name 原文 | 本次能证明的范围 |
|---|---|---|
| 00512 | GRAND PHARMACEUTICAL GROUP LIMITED | 精确证券到官方名称；registry 未在本次重查 |
| 01244 | 3D MEDICINES INC. | 同上 |
| 01681 | CONSUN PHARMACEUTICAL GROUP LIMITED | 同上 |
| 02335 | SHAANXI MICOT PHARMACEUTICAL TECHNOLOGY CO., LTD. | 同上 |
| 02410 | TYK MEDICINES, INC | 已另外核对一条 canonical NCT 的 exact lead 原文 |
| 02607 | SHANGHAI PHARMACEUTICALS HOLDING CO., LTD. | 与已有 A:601607 alias exact，需处理跨证券同主体关系 |
| 02637 | FUJIAN HAIXI PHARMACEUTICALS CO., LTD. | 精确证券到官方名称；registry 未在本次重查 |
| 02659 | SHANGHAI BAO PHARMACEUTICALS CO., LTD. | 同上 |
| 03320 | CHINA RESOURCES PHARMACEUTICAL GROUP LIMITED | 同上 |
| 06887 | SUNSHINE LAKE PHARMA CO., LTD. | 同上 |

其余 7 家 candidate（00455、00876、01011、02348、02561、02633、06833）未出现在该持股页，本次仍未知。不得将未出现解释为未上市、非药企或没有管线。VISEN 与 TYK 的[官方发行人披露](https://www.hkexnews.hk/listedco/listconews/sehk/2025/0327/2025032702333.pdf)等可作为另一路逐件有界接入，不能凭搜索片段直接建账。

## 一条真实来源可行性链

TYK 的 HKEX Name 原文为 `TYK MEDICINES, INC`，locator 为 `#mutualmarket-result tbody tr[614] td.col-stock-name`。独立新取 [NCT07104643 canonical API](https://clinicaltrials.gov/api/v2/studies/NCT07104643) 于 `2026-10-18T15:38:25.918632+00:00` 返回 HTTP 200，18,793 bytes，SHA-256 `bd6c3a9c43ae66139cbb0b3a1621e6c768ca323e88ead429f1980fd371f203e2`，NCT ID 正确，leadSponsor 原文为 `TYK Medicines, Inc`，现有标准化匹配结果 `normalized_exact`。这一检查不证明药物权益、成功概率、完整管线或该提案已获接受。

隔离实际原件回放在本机 QA 小数据库完成，源目录和候选从生产读事务按表导出，原件仅复制到 QA，没有大 SQLite 备份。10 candidate / 11 uncertain 的当前名称 proof 可重新读取，TYK 两个官方来源 exact。QA 路径与每条 receipt、HTTP 状态、SHA 见 ignored `report.json`。生产新增映射、生产覆盖增量均为 0。

## 新模块契约与接线门禁

`src/vbt/hk_issuer_names.py` 只提供独立接口，不改变 A/US 接受协议。

- `directory_audit(market, db=None)`：纯 SELECT 当前源审计，schema_version=1；status 为 verified / verified_bytes_date_anomaly / blocked；date_status 为 known / future_dated / unknown。失配或缺失时 count=None，不报告 0。自有连接为 mode=ro，调用方连接不提交、不关闭。
- `parse_southbound(raw)`：4 MB 原件上限、明确表 ID/字段/完整闭合、唯一 ticker、有界日期；不运行 HTML/脚本，保留名称原文。交易类别后缀不被静默删除。原件部分行百分比字段为空，本模块不利用持股量或比例推断身份与权益。
- `seal_southbound(market, raw, metadata)`：纯离线封存，只写 `hk_issuer_name_captures`、`hk_issuer_name_receipts` 及独立原件目录。metadata 必须绑定固定 URL、GET、整数 HTTP200、bytes、SHA、含时区 retrieved_at。抓取和内容日各有 7 天窗口，拒绝未来/过期输入。同字节和 metadata 幂等复用 capture；错误保留在调用方观察记录，不伪造成功。
- `query_evidences(market,candidates,db=None)`、`query_evidence`、`query_names`：每个批次重放当前 HK 目录一次，每个名称 capture 一次；不创建表，不信任调用方自报名字。当前候选 hash/name/ticker、当前 listings、receipt SHA、原件和目录版本必须一致；冲突名称、变更、拒绝状态和错误原件返回未知。

两张账本均限制 UPDATE、DELETE，以及 SQLite 默认 recursive_triggers=OFF 时可绕删除触发器的 INSERT OR REPLACE；唯一 id 和 payload SHA 都受冲突 guard 保护。名称 proof 保留全部否定边界，human_sign_off、registry_alias_proven、drug_developer_status_verified、drug_ownership_verified、full_pipeline_verified 都是 false。

查询核心现已优先使用已验证 HK 原文生成查询（因为原有简称通常也能生成 query，仅在 None 时 fallback 会继续漏掉它们），冻结 `verified_hk_official_name` 查询上下文与 receipt。接受前仍需独立身份核对；本模块不放行 HK 自动接受。原 HKEX 未来内容日期也必须先独立解决。跨市场同主体如 HK02607/A601607 不能当成两个新增药企；现有接受流程中的同证券别名冲突检查，不等于全市场法人归并或跨证券全局别名冲突检查，此项仍是后续明确缺口。

名称 query reader 有新鲜度限制，不是永久历史附件 reader。后续历史下载应通过 run、冻结 ledger、manifest 与原件 SHA 独立复核，不应因当前候选或目录更新使既往证据不可查看。

## 查询接线与操作

`market-hk-names [seal] --raw <已抓取HTML> --metadata <真实响应元数据JSON>` 可对已有原件离线封存。命令要求既存、本机、无路径别名的市场数据库；不构造 Market、不恢复其他任务、不联网，也不改变公司、试验或业务分类。来源和元数据使用有界读取及严格 JSON 校验。

查询上下文版本为 `verified-directory-query-3-hk`。旧 zero 查询只有名称查询语义改变才重新开放遍历；相同语义的新 receipt 不使旧建议失效。当前 proof 丢失、原件损坏、UUID/内容哈希不符、候选变化或冻结历史凭据失效均保留明确阻塞。HK 名称只提供检索依据，US 与 A 的身份接受适配器不因此扩大范围。

独立接线及既有 A/US 回归为 189 passed，包含真实 readiness 与离线 CLI；root 另补 JSON list/null 损坏账本的可读错误门禁后，HK 接线测试为 24 passed。这些工程检查不相加作为独立实验数。生产接入及最终完整回归的实际状态另见本轮发布记录。
