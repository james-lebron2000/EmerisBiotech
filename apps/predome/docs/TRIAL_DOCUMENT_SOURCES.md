# 试验方案与 SAP 原件接入

本入口把官方登记中的附件清单、实际取得的原件、逐页提取和科学审核分开。它为后续新版本的 `trial_protocol` 证据卡提供资料，不自动补全试验参数、创建预测或写入审核票。当前交付范围是 GitHub 源码；没有服务器部署或新的付费模型调用。

## 为什么需要单独的资料路径

本轮实际取得 INAVO120（NCT04191499）的 Protocol v8 和 SAP v2。方案为 8,767,559 bytes、208 页，超出现有通用来源解析器的 6 MiB／200 页限制。既有用户上传入口另有 5 MiB／100 页限制。新入口不改变这两个入口，而是为指定的公开方案／SAP 提供独立的原件采集和逐页索引。

可核对官方 [Protocol 原件](https://cdn.clinicaltrials.gov/large-docs/99/NCT04191499/Prot_000.pdf)和 [SAP 原件](https://cdn.clinicaltrials.gov/large-docs/99/NCT04191499/SAP_001.pdf)，文件版本与获取时间分别保留。

普通文字提取不能保证读取整份科学资料。真实命令建立了全部 241 页的索引，但方案有 8 页、SAP 有 3 页未提取出文字。抽查 SAP 的 PDF 第 9 页可见主要 estimand 表，方案第 159 页可见访视表，均有真实内容。因此原提取保持 `partial`；不能把没有文字层当成空白或没有证据。

## 登记清单与既有候选

`emeris_live/trial_documents.py` 的 `document_inventory()` 只投影 `/documentSection/largeDocumentModule`，输出 `predome.registry-documents-1`。未列出、清单不可读取和已有列表分别保留；`noSap`、未知布尔值和坏条目也不被悄悄改成否定结论。文件日期和原始 `uploadDate` 是描述性元数据，没有时区的时间不会自动变成 UTC。

IPD sharing 中承诺将来提供 Protocol／SAP，不等于当前存在可下载的附件。没有清单也只说明本次登记快照未列出，不能证明其他官方站点没有资料。

`intake.registry_summary()` 为新候选保存附件库存。对已有同 URL、同 SHA 的候选，`list_candidates()` 和 `get_candidate()` 从保存的原始字节重新核对 SHA、登记来源和精确 NCT，只在返回的 `structured` 中派生 `study_documents` 与 `study_documents_projection`。原 payload、来源、时间和已锁定记录保持原样，读取不会重新访问网络。

原件缺失、损坏或身份不符时，库存为 `null`，状态为 `source_unavailable`。它不会退化成“没有附件”。现有内部审核页的“查看抽取字段”可直接展示这两个嵌套对象，无需新增公开调试入口。

## 显式采集

从 `apps/predome` 运行。输出的父目录须已存在，每次使用新的目录名。仅查看当前登记和附件清单：

```sh
python3.11 scripts/acquire_trial_documents.py \
  --nct NCT04191499 \
  --output-dir verification/inavo120-inventory-001
```

明确选择登记实际列出的文件，并另外建立逐页文本索引：

```sh
python3.11 scripts/acquire_trial_documents.py \
  --nct NCT04191499 \
  --document Prot_000.pdf --document SAP_001.pdf \
  --extract \
  --output-dir verification/inavo120-originals-001
```

没有 `--document` 时不下载 PDF；`--extract` 要求显式选择文件。下载仅使用精确 NCT 的官方 API 和该清单文件对应的官方 CDN 地址，不探测猜测的文件名，不跟随重定向或自动重试。只允许标记为 Protocol 或 SAP 的文件；ICF 和共享承诺不会成为默认下载任务。

登记响应上限 6 MiB，单份原件上限 16 MiB。实际获取时间同时作为这次来源的 `available_at`；它不授予旧截止时间资格。失败保存固定类别及实际观察到的 `error_http_status`，没有响应时保持未知，不读取 HTTP 错误正文或保存异常文字。

输出分为：

- `registry.json`、`inventory.json`、`documents/` 和 `manifest.json`：来源 URL、实际字节、SHA、获取时间和采集状态。
- `document-extractions/*.pages.json` 和 `extraction-manifest.json`：独立提取版本、原件绑定、零基索引、一基 PDF 页号、每页文字及缺失状态。

提取前重新核验保存的 registry 和 PDF 原件。专用解析子进程最多处理 400 页和 2 MiB 文字；空页、失败及超限页仍有索引。原件完整取得可以是 `complete`，文字提取仍可以是 `partial`；命令在提取不完整时以非零状态退出，并保留可检查的资料。不能把退出码改为零来代替缺页处理。

## 无文字层页面的 OCR 补录

OCR 是显式的离线操作，依赖已安装的 Poppler `pdftoppm` 和 Tesseract 英文包，不使用模型 API。它只处理操作者指定、所供提取对象中没有文字的页，最多一次 20 页。入口核对 PDF 与提取对象的来源绑定和页号，并记录所供提取对象的 canonical JSON SHA；不自动验证外部提取清单的原始文件签名。本轮实际验收另核对了原提取文件与保存清单的字节哈希。示例选择真实 SAP 的三个非文字页：

```sh
python3.11 scripts/ocr_trial_document_pages.py \
  --pdf verification/inavo120-originals-001/documents/SAP_001.pdf \
  --extraction verification/inavo120-originals-001/document-extractions/SAP_001.pdf.pages.json \
  --page 9 --page 10 --page 11 \
  --output-dir verification/inavo120-sap-ocr-001
```

新目录保存每页渲染图、OCR TXT 和完整 TSV（位置及引擎分值），连同原件 SHA、页号、工具版本、语言和参数。OCR 输出始终为 `needs_human_review`，不改原逐页记录的 `partial`，不把识别分值变成准确率、临床概率或科学接纳。表格布局、数字、单位、上下标和主次终点对应关系须回到原图核对；补录不会自动并入模型输入。

本轮已用实际安装的 Poppler 25.09.1 和 Tesseract 5.5.2 完成上述 11 页的独立补录，并逐一校验 33 个图片／TXT／TSV 产物哈希。工程状态为 `ocr_complete`，人工核对仍未开始；它不改变原提取状态或构成整份资料的逐字准确性证明。

## 本轮来源观察

| 研究 | 已核对范围 | 当前限制 |
|---|---|---|
| TRIUMPH-5 · NCT06662383 | 当前登记快照；CTIS 实际可见的 MTD 优效目标和英文方案／synopsis 列表 | CT.gov 快照未列附件；CTIS 原件尚未取得，PDF SHA 为空；不能填入完整 estimand、alpha 或多重性规则 |
| AVANZAR · NCT05687266 | 当前登记、AZ 页面和旧 DE EudraCT 公开字段 | 旧欧洲登记的主要／次要终点层级与当前版本有差异，不能替代现行方案／SAP |
| INAVO121 · NCT05646862 | 当前登记和 Roche 页面；主研究 BICR-PFS 与 DDI 子研究分别识别 | 患者摘要及其 PDF 聚焦 DDI，不能用来补全主随机研究的 SAP |
| INAVO120 · NCT04191499 | 官方实际附件：Protocol v8／208 页，SAP v2／33 页；原件哈希与独立下载一致 | 是通用接入和间接资料样本，不是 INAVO121 的统计方案；OCR 尚需人工核对 |

来源、获取时间、哈希、页数和边界见[本轮记录](records/trial-document-sources-20261027.json)。原始第三方 PDF、完整提取文字和 OCR 不随 MIT 源码再发布；获授权维护者可在本地资料目录核对或用上述命令重新采集。重新获取的资料可能因官网更新而具有不同哈希，应保留为新版本。

## 进入未来模型输入前

采集、文字提取和 OCR 都不是科学批准。需要先核对精确试验、研究人群、治疗与对照、适用 amendment、主要终点和分析时间，再确认 estimand、分析集、alpha、期中和多重性规则。INAVO120 的方法不能移植为 INAVO121 的参数，旧 AVANZAR 记录也不能覆盖当前冻结目标。

既有 `prepare_arena_season.py` 支持 `trial_protocol` 卡片，并要求来源获取时间不晚于新的信息截止时间。操作者仍须单独撰写有原文位置的证据卡。新模型版本可保留卡片显式提供的 `page_references`、`source_anchor` 和 `document_version`，见[定位字段合同](RECORDED_EVIDENCE_LOCATIONS.md)；它不会自动把 PDF 附件、完整逐页索引或 OCR 文本送入模型。试验编号、适用范围、已核对摘录、关键条件和未知项仍须在卡片正文写清楚，定位元数据不能替代科学内容或审核。

新资料只能进入单独准备的新输入与截止时间，不回填旧预测、失败、事件或时间凭证。正式科学接纳、前瞻校准、独立模型／Agent／专利增益比较仍需各自证据。
