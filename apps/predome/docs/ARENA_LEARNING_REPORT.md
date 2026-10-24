# Open Forecast 离线复盘导出

`scripts/arena_learning_report.py` 从现有 `arena.sqlite3` 导出可审核 JSON。它用 SQLite `mode=ro` 和 `query_only` 读取并验证事件哈希链，不初始化账本、不调用模型或下载资料、不修改预测，也不会更新线上权重或提示词。

在 `apps/predome` 目录运行（输出文件必须尚不存在）：

```sh
python scripts/arena_learning_report.py \
  --ledger /absolute/runtime/path/arena.sqlite3 \
  --cohort example-preregistered-cohort \
  --output /absolute/reports/path/arena-review-unique-id.json
```

省略 `--cohort` 可导出全部队列，分组仍保留队列、赛道与证据条件。输出以 `O_EXCL` 新建、权限为 `0600`；原文件、符号链接和账本不会被覆盖。报告附输入账本末尾哈希、事件数和报告内容哈希，可与冻结档案核对。它不重新访问原始来源，也不替代对 TSA 签名的独立验证。

只有已经完成双人裁定、成功/失败标签明确、预测锁定早于首次公开结果、未超过登记期限、概率合法、模型版本及证据合同完整的记录进入 Brier、log loss 和方向误差复盘。外部 TSA 若晚于结果公布，该记录不进入评分；只有服务器时间的记录会单独注明时间依据。未揭晓、单人裁定、不可裁定、拒答和调用失败均不填充概率或准确率。

报告同时保留所有已分配槽位及覆盖率。工程问题分别列为来源缺失、引用错误、证据时间不合格、超时、拒答、服务/预算/输出合同故障。它们不被解释为药物机制判断错误。模型分组保留请求及返回版本、工作流和预算哈希；工作流与项目汇总仅作描述，不声称因果优势。多个模型对同一药物的预测仍只对应一个独立项目。不同参与者或证据条件不混成总准确率。

`version_proposals` 是待人工审核队列，默认 `candidate_version=null`、`applied=false`、`training_performed=false`。未揭晓时只可提出有证据的工程复核，不会生成“模型已经学会”或准确率优化结论。后续迭代必须登记假设、候选版本、基线、预算、独立留出项目和停止规则，先按项目及时间隔离做离线评估，再以新 run ID 做 shadow 运行，最后进入新注册的前瞻队列。任何新版本均不覆盖旧预测、旧失败或原比较分母。

测试全部使用隔离的临时账本与固定样例，不读取线上数据。通过测试仅说明只读导出、时间/裁定门槛及误差归类按合同工作，不构成临床预测有效性的证据。
