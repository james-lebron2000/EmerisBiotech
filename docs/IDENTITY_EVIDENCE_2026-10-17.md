# 2026-10-17 证券主体与申办方身份核对

本批核对原有 14 条 pending 建议，依据为 Nasdaq Trader 当前官方证券目录与逐项重新读取的 ClinicalTrials.gov API。14 条均满足限定的主体名称核对条件。本文件为自动化来源审查，未产生人工签署，也未更改真实数据库。

## 判定范围

`verified` 仅表示证券目录主体名称与指定 NCT 的 exact leadSponsor 名称能够在限定归一化规则下关联。它不证明所有同名试验均归属当前上市主体，不确认原研/许可权益、药物所有权、当前管线状态或临床成功概率。旧试验、公司更名、收购、子公司关系和授权链仍需逐项核对。

Nasdaq Trader 官方目录抓取于 2026-10-17T05:52:17.443956+00:00；来源页尾 File Creation Time 原值为 `1016202621:31`。目录 SHA-256 为 `9f5c42346e6ab594a9e16bed99757e38cda89645c776b987626c9f51813bc99b`。该时间字段按来源原文保留，不猜测交易所时区。所有核对证券均为 `Test Issue=N`、`ETF=N`。ACTU、ADIL 的 `Financial Status` 为 `D`，仅记录原值；身份核对没有将此转换为退市或财务结论。

## 核对清单

| 证券 | 目录/IR 主体名 | CTG leadSponsor | 判定 | 来源 |
|---|---|---|---|---|
| AARD | Aardvark Therapeutics, Inc. | Aardvark Therapeutics, Inc. | 已核对主体名称 | [交易所](https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt) · [CTG](https://clinicaltrials.gov/study/NCT05121441) · [IR](https://ir.aardvarktherapeutics.com/ir-resources/investor-faqs) |
| ABCL | AbCellera Biologics Inc. | AbCellera Biologics Inc. | 已核对主体名称 | [交易所](https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt) · [CTG](https://clinicaltrials.gov/study/NCT07108894) · [IR](https://investors.abcellera.com/overview/default.aspx) |
| ABEO | Abeona Therapeutics Inc. | Abeona Therapeutics, Inc | 已核对主体名称 | [交易所](https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt) · [CTG](https://clinicaltrials.gov/study/NCT00385502) · [IR](https://investors.abeonatherapeutics.com/) |
| ABOS | Acumen Pharmaceuticals, Inc. | Acumen Pharmaceuticals | 已核对主体名称 | [交易所](https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt) · [CTG](https://clinicaltrials.gov/study/NCT04931459) · [IR](https://investors.acumenpharm.com/node/9876/ixbrl-viewer) |
| ABUS | Arbutus Biopharma Corporation | Arbutus Biopharma Corporation | 已核对主体名称 | [交易所](https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt) · [CTG](https://clinicaltrials.gov/study/NCT00927459) · [IR](https://investor.arbutusbio.com/investor-faq) |
| ACAD | ACADIA Pharmaceuticals Inc. | ACADIA Pharmaceuticals Inc. | 已核对主体名称 | [交易所](https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt) · [CTG](https://clinicaltrials.gov/study/NCT00087542) · [IR](https://ir.acadia.com/overview/default.aspx) |
| ACHV | Achieve Life Sciences, Inc. | Achieve Life Sciences | 已核对主体名称 | [交易所](https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt) · [CTG](https://clinicaltrials.gov/study/NCT00034151) · [IR](https://ir.achievelifesciences.com/news-events/press-releases/detail/257/achieve-life-sciences-announces-up-to-354-million-private-placement) |
| ACRS | Aclaris Therapeutics, Inc. | Aclaris Therapeutics, Inc. | 已核对主体名称 | [交易所](https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt) · [CTG](https://clinicaltrials.gov/study/NCT01986920) · [IR](https://investor.aclaristx.com/shareholder-services/investor-faqs) |
| ACRV | Acrivon Therapeutics, Inc. | Acrivon Therapeutics | 已核对主体名称 | [交易所](https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt) · [CTG](https://clinicaltrials.gov/study/NCT05548296) · [IR](https://ir.acrivon.com/ir-resources/faqs) |
| ACTU | Actuate Therapeutics, Inc. | Actuate Therapeutics Inc. | 已核对主体名称 | [交易所](https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt) · [CTG](https://clinicaltrials.gov/study/NCT03678883) · [IR](https://actuatetherapeutics.com/investors/faqs/) |
| ACXP | Acurx Pharmaceuticals, Inc. | Acurx Pharmaceuticals Inc. | 已核对主体名称 | [交易所](https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt) · [CTG](https://clinicaltrials.gov/study/NCT04247542) · [IR](https://ir.acurxpharma.com/all-sec-filings/xbrl_doc_only/864) |
| ADIL | Adial Pharmaceuticals, Inc. | Adial Pharmaceuticals | 已核对主体名称 | [交易所](https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt) · [CTG](https://clinicaltrials.gov/study/NCT04101227) · [IR](https://www.adial.com/investors/) |
| ADMA | ADMA Biologics, Inc. | ADMA Biologics, Inc. | 已核对主体名称 | [交易所](https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt) · [CTG](https://clinicaltrials.gov/study/NCT00473824) · [IR](https://ir.admabiologics.com/company-info/faqs) |
| ADRX | ADARx Pharmaceuticals, Inc. | ADARx Pharmaceuticals, Inc. | 已核对主体名称 | [交易所](https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt) · [CTG](https://clinicaltrials.gov/study/NCT05691361) · [IR](https://investors.adarx.com/shareholder-services/investor-faqs) |

## 留存证据与访问边界

机器可读清单位于 [`examples/identity_evidence_2026-10-17.json`](../examples/identity_evidence_2026-10-17.json)，顶层为 14 条记录的数组。每条包括原 candidate/proposal 标识、原候选 hash、来源 URL、抓取时间、源文件 hash、原始目录字段与限定审查类型。

本地留存目录为 `artifacts/market/identity_review/2026-10-17/`，沿用项目 artifacts 忽略规则，不将抓取的完整网页提交到公共 Git。`exchange_manifest.json` 包含 14 条原始证券目录记录；`capture_manifest.json` 包含 14 个 CTG API 的 HTTP 200、leadSponsor 与 SHA-256。`alternative_manifest.json` 记录替代 IR 页的访问结果。

初次直接抓取 14 个公司 IR 页时，7 个返回 HTTP 200，7 个返回 HTTP 403；7 个替代页也返回 403。浏览工具对部分官方页面提供了可读摘要，另存 `web_tool_extracts.json` 与 `indexed_primary_source_extracts.json`，明确标记其并非 raw HTTP 页面。主体判定以已成功抓取的官方交易所目录和 CTG 双源为基础，受限的 IR 访问不会伪装为成功下载。

## 本批验收

- 真实数据库只读连接，未写入 pending 提议、已关联主体或试验记录。
- 14/14 proposal alias 在重新取得的 CTG API leadSponsor.name 中字面一致。
- 14/14 ticker 在当前 Nasdaq Trader 目录存在，非测试、非 ETF。
- 14/14 证券名称经现有保守归一化后与 alias 为 normalized_exact。
- 14/14 法定名展示经相同规则与 alias 为 normalized_exact；标点与后缀差异已显式归一化。
- 源文件完整性以本地验证 SHA-256 为准。JSON 中 status=verified 不能直接替代人工签署或药物权益审查。

## 后续接入建议

平台应将本批证据按 candidate_hash 与 proposal_id 固定后追加存储，保留 reviewer_type=automated_source_review 与 human_sign_off=false。若候选 hash 或申办方 proposal 后续改变，应重新核对。自动关联产生的是公开临床登记的公司入口，展示时仍需明确覆盖缺口、登记历史与权益未知。
