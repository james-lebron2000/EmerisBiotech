# TRIUMPH-5 官方浏览器原件接入

本轮已从官方 CTIS 的 **Download clinical trial** 入口取得 TRIUMPH-5 文件包。此前“已列出、尚未捕获原件”的记录保留为当时观察；本轮另存新来源和验收，不改写旧预测输入。

官方[下载说明](https://euclinicaltrials.eu/search-tips-and-guidance/?lang=en)解释整项下载方式。[实际试验页](https://euclinicaltrials.eu/ctis-public/view/2024-511450-49-00)显示 EUCT `2024-511450-49-00` 与 protocol code `J1I-MC-GZBP`。页面显示 Download completed，但工具没有捕获 download event 或原始 HTTP 响应。可见状态、本机字节与来源认证分别记录。

## 实际文件与内容边界

ZIP 为 25,121,521 bytes、45 个条目。本轮仅选方案、英文 synopsis 及详情 HTML，不自动把其他附件纳入预测。

| 原件 | 字节 | 页索引 | SHA-256 |
|---|---:|---:|---|
| Protocol | 1,937,394 | 186 | `adb16ec19a47ad8be5716f3475d775f96fa1503cdfb461de042f74057e6cb96f` |
| English synopsis | 650,829 | 7 | `7fc736e90f412cf99d405065ce64fcf65590a49a1210fa05068fd7a128d2f21a` |

两份文件均通过新 CLI，原件、观察 JSON、manifest、回执和逐页提取读回一致。193 页都有机械文字索引；这不证明科学内容完整。封面可核对 amendment (c) 与批准日期 19 Feb 2025 GMT。局部统计信息及页码见[元数据记录](records/triumph5-ctis-acquisition-20261027.json)；大量 CCI 遮挡、最终 SAP、完整成功判定规则和最新适用修订仍未核实。

原件、完整提取文字、ZIP 和协议截图保留在本地受控目录，不随 MIT 源码再发布。批准日期、文件 mtime 或本轮获取不能建立旧截止时间资格。

## 本地登记命令

`emeris_live/local_trial_documents.py` 登记工作区原件与操作者观察，不联网，不为 CTIS 文件伪造 CT.gov CDN 链。从 `apps/predome` 运行，使用实际原件路径和全新输出目录：

```sh
python3.11 -B scripts/register_trial_document.py \
  --pdf verification/triumph5-ctis-20261027/browser-acquisition/protocol.pdf \
  --observation verification/triumph5-ctis-20261027/browser-acquisition/protocol-observation.json \
  --output-dir verification/triumph5-protocol-next-001 \
  --extract
```

输入原件不随 Git 分发，须先经官方入口取得。父目录须存在，每次用新目录；PDF 上限 16 MiB，必须是工作区内普通、非符号链接、单硬链接文件。输入全部校验后才建目录，逐字复制与 SHA 读回检查不覆盖旧文件。

观察 JSON 是闭合九字段声明：`schema_version` 固定 `predome.browser-trial-document-observation-1`；另有 `origin_page_url`、`nct_id`、`eu_ct_number`、`protocol_code`、`listed_document_title`、`document_type`、`observation_at_utc`、`browser_download_completion_observed`。URL 须为无凭据、无查询或 fragment 的 HTTPS 公开详情页；文档类型为 protocol／synopsis／sap，时间须带时区且非未来，完成状态须为布尔值。重复键、非有限值、额外字段、非法元数据或路径被拒绝。

字段格式有效不认证声明。完成状态只表示操作者看到 UI 提示，不是捕获下载事件或 PDF 响应。ZIP 成员关系由独立验收保存，登记器不自动认证。

## 输出和时间

新目录保存 `original.pdf`、原始 `observation.json`、`manifest.json` 与 `registration-receipt.json`。回执绑定 manifest 原字节哈希，不是独立签名或来源真实性证明。

- 方法为 `local_file_registration_from_browser_observation`。
- `registered_at_utc` 与 `available_at` 为本次登记时间，不从观察、文档日期或 mtime 回填。
- 未观察的 `download_url`、`http_status`、`retrieved_at_utc` 均为 `null`。
- 身份为 `unverified_operator_observation`，审核为 `needs_human_review`，`eligible_for_forecast=false`。

`--extract` 复用既有 `extract_pdf_pages()`，独立保存 `document-extractions/original.pdf.pages.json` 和 `extraction-manifest.json`，绑定原件及登记 manifest SHA。`partial` 或 `failed` 非零退出并保留产物，不改原登记。400 页／2 MiB 文字上限、既有 CT.gov 和 OCR 路径保持原合同。

## 未来输入前

CCI 内容保留未知，不恢复遮挡。PDF 顺序与印刷页分开，例如 synopsis PDF 1–7 对应印刷页 13–19；协议 PDF114 的印刷页号不可见，不能编造。机械提取 `complete` 不能推出完整多重性路径或整体成功规则。

本轮来源阅读是 AI 辅助抽查，人工科学审核为 0。精确试验、适用版本、摘录、未知项及成功事件须单独审核，之后才能准备新卡片、截止时间和运行。旧 cohort 不补新附件、不重试失败槽、不改旧预测或时间凭证。

实现及验收见 [Issue #37](https://github.com/james-lebron2000/EmerisBiotech/issues/37) 与 [PR #3](https://github.com/james-lebron2000/EmerisBiotech/pull/3)。本轮只更新 GitHub，不操作服务器、不部署、不调用新模型；资料取得和软件检查不建立临床预测效度。
