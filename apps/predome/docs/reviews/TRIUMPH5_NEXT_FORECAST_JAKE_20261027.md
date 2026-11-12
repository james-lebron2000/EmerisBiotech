# TRIUMPH-5 下一次预测：Jake 科学审核工作包

**状态：已指定责任人，待科学审核；`human_reviews_completed=0`，`eligible_for_forecast=false`。** 用户指定的 Predome 登录用户名为 **Jake**。这份材料是 GitHub 中可追溯的任务分配与工作包，不是账号登记、来源认证、科学批准或新预测。

本轮没有核验或更改 Jake 的账号认证、稳定账号 ID、人员主体、审核授权、科学资格或独立性；没有执行登记 CLI、登录服务器或写入审核账本。`account_id`、`principal_id` 和授权事件哈希均保持未知。既有调试权限不自动获得科学审核权限。

机器可读分配记录见 [assignment JSON](../records/triumph5-review-assignment-20261027.json)。获取经过和限制见 [CTIS 原件接入说明](../TRIUMPH5_CTIS_ACQUISITION.md)及[已获取资料元数据](../records/triumph5-ctis-acquisition-20261027.json)。本工作包未复制 PDF、完整提取文字或私有身份资料到 Git。

## 本次需要审核的确切资料

| 项目 | 当前记录 | 审核边界 |
|---|---|---|
| Trial | TRIUMPH-5 | 不代表已审核任何未来临床结果 |
| NCT | [NCT06662383](https://clinicaltrials.gov/study/NCT06662383) | 沿用获取记录的 declared NCT，Jake 仍须核对同一性 |
| EUCT | [2024-511450-49-00](https://euclinicaltrials.eu/ctis-public/view/2024-511450-49-00) | 官方详情页的既有观察；本工作包未重新联网核实 |
| Protocol code | `J1I-MC-GZBP` | 与 NCT、EUCT 及适用人群一并核对 |
| 已见方案版本 | amendment **(c)**；`VV-CLIN-132835`；封面 `19 Feb 2025 GMT` | Protocol PDF 第 1 页；尚未证明这是下一次预测适用的最新全球修订 |
| 最终 SAP | 未取得 | 未取得不等于不存在，不能视为通过 |
| 新预测目标／截止时间 | 尚未登记 | 不复用旧截止时间，也不回填新附件 |

官方[整项下载说明](https://euclinicaltrials.eu/search-tips-and-guidance/?lang=en)解释 Download clinical trial 入口。已保存 ZIP 为 25,121,521 bytes、45 项，SHA-256 为 `b481c6d5a8dcb0eaeee9cfcce888e5fd83575ea1b60fd6f0ebf63b83c4d0a7d9`。此前观察到官方 UI 的 Download completed；未捕获浏览器下载事件或原始 HTTP 响应。PDF 来自该 ZIP 的已记录条目；本地字节哈希不独立认证来源。

| 原件 | 字节／页数 | 原件 SHA-256 | 当前登记 `available_at` |
|---|---|---|---|
| Redacted protocol | 1,937,394 bytes／186 页 | `adb16ec19a47ad8be5716f3475d775f96fa1503cdfb461de042f74057e6cb96f` | `2026-10-27T02:40:45.625378Z` |
| English synopsis | 650,829 bytes／7 页 | `7fc736e90f412cf99d405065ce64fcf65590a49a1210fa05068fd7a128d2f21a` | `2026-10-27T02:40:45.623678Z` |

本工作包按 assignment JSON 中的工作区相对路径，重新读取并核对上述原件、登记 manifest／回执、提取 manifest 和页索引的字节哈希；页号分别连续覆盖 1–186 和 1–7。页索引 SHA 分别为 `8940e6c02ffe7ea83b3f367a5a44d7af571ecc6a9d8413f2262eff7e876587fd`、`85cc26d2e75c75c799e9a7218716bce065825ea8b2e8aa1649dd80f7c24330f7`。这是本地完整性读回，没有新增人工科学审核。

`available_at` 是本次登记时间，不是首次公开时间。批准日期、文件 mtime、观察时间或本地 SHA 不能证明资料在旧信息截止前可用。`download_url`、`http_status`、`retrieved_at_utc` 保持 `null`；需要历史资格时必须另有证据。

## Jake 的阅读顺序与待决事项

下表页号明确指本次原件 **PDF 从 1 开始的页序**，不自动等同于印刷页。导航沿用已保存的 AI 辅助来源抽查，尚未获人工确认，不能当作模型已阅读或科学接纳证明。Synopsis PDF 1–7 对应印刷页 13–19；Protocol PDF114 的印刷页号未见，保持未知。

| 次序 | 需要作出的决定 | 原件导航 | 当前状态 |
|---|---|---|---|
| 1 | NCT／EUCT／protocol code 是否确属同一试验；amendment (c) 是否为下一次目标适用的现行修订 | 两个官方详情页；Protocol PDF1 | 未解决，未通过 |
| 2 | 明确目标终点、人群、剂量／治疗臂、对照及确切分析时点 | Protocol PDF13、52；Synopsis PDF1–7 | 已有抽查提示 Week80 体重变化，仍不足以固定完整假设 |
| 3 | 选择预设的主 estimand，列清 intercurrent events 和缺失数据策略 | Protocol PDF16、17、55、56 | 可读到 treatment-regimen／efficacy 两类定义，不能自行任选 |
| 4 | 取得并确认最终 SAP 的版本、适用性与可用时间；核对完整多重性、α 分配、层级、期中／最终边界、所需比较及 estimand 组合 | Protocol PDF114、115、118、120；最终 SAP 待取得 | 总体双侧 0.05 或“estimands 之间不计划调整”不等于完整成功规则 |
| 5 | 逐项记录仍被 CCI 遮挡的决定性内容，判断是否必须补充公开授权原件 | Protocol PDF13、114、115、118、120 | 剂量／模型／缺失数据及完整成功规则仍有未知；不复原遮挡 |
| 6 | 形成有来源、版本、页码、未知项和本人日期的结论，决定是否允许准备一个新输入版本 | 本工作包及 acquisition metadata | 尚无人工结论，不冻结新预测输入 |

## 交回方式：Approve、Return 或 Request document

当前 **没有选择任何决定，所有必需项均未通过**。Jake 应保留自己的独立原文核对记录；每个确认都须写明所据文件 SHA、PDF 页序、适用版本和剩余限制。未知不能填作已确认，也不得回填更早审核时间。

- **Approve**：仅在同一性、现行方案／SAP、确切假设、estimand、完整成功判定及来源时间等必需事项都有支持时，提交针对这个指定新证据输入的明确书面决定。若有不能取得但被认为不影响目标的项目，须逐项论证并保留责任结论，不能把未知项改成 passed。审核结论本身不授予账号权限、不调用模型、不裁定临床结果；后续新输入仍须满足现有证据准入门槛。
- **Return**：资料冲突、错版本、错试验、错误页码、无法支持的摘录或目标定义不完整时退回，列出问题位置、影响和所需修正；旧原件及预测保持原样。
- **Request document**：优先请求适用的最终 SAP、现行全球修订／修订历史，以及能合法公开澄清关键 CCI 遮挡规则的材料。未得到决定性资料前保持 unresolved／不具备新预测资格；不得推断遮挡内容。

每份交回材料至少记录决定、确切审核对象、逐项结果与来源、未解决问题及其影响、本人实际完成时间。没有答复或仅提供用户名均不算完成审核。GitHub 回复可以承担工作交接，不代替运行时认证票或人员资格材料；私有身份、资格或利益冲突文件不上传 Git。

## 与账号审核及后续运行的衔接

现有[账号绑定的结果来源核验工作流](../RESULT_REVIEW_WORKSPACE_1_2_9.md)要求稳定账号／人员主体关联和逐目标授权；本轮只记录用户指定的 `Jake`，未执行该流程。该界面是结果来源核验工作区，不是本文件所需的前预测输入科学审核登记器；不能把本工作包当成已创建认证审核或自动开通权限。

未来实际结果的[逐结果科学接纳](../SCIENTIFIC_RESULT_ACCEPTANCE.md)还要求适用的真实人员、独立审核及相应材料，Jake 单独的一份资料审核不满足两位独立主体的结果接纳要求。公开命中和计分资格仍受原政策控制。

只有后续明确完成科学审核和既有准入门槛，才可另建输入版本、确切目标、时间截止和新 cohort／run。不得把本次新附件补进旧冻结输入，不重试旧失败槽，不改变旧预测、生成时间或签名证明。本包没有批准新预算、触发模型调用或验证预测能力。
