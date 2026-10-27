# 已完成预测的可核验同步

独立试跑账本可以在已有 Predome 服务中展示，同时保留原调用、输入、失败和签名原件。同步不运行模型，不重新登记历史预测，也不导入账号权限、临床结果或科学接纳。

## 事件与时间

宿主账本只追加一个 `sealed_cohort_imported` 事件。wrapper 内保留完整源事件的 ID、序号、时间、前序哈希及事件哈希；`events()` 仍返回真实宿主物理链。源事件在独立序列中折叠，之后才叠加宿主的来源问题、引用更正、结果审核和科学接纳。

原登记、开始、生成和锁定时间保持原值。`record_origin.synced_at_utc` 单独表示同步时间。源预测证明只覆盖其原始前缀，不能以宿主的新序号扩展；宿主时间证明也不替换源预测证明。

导入前核验整包 SHA-256、完整源链、完整分配分母、原始执行合同、当时的筛查与截止时间、全部来源字节及两份原签名。OpenSSL 分别核验数据和原请求，同时明确要求 SHA-256 imprint、非空且相同的请求／响应 nonce。只接受完整完成的原队列，包括失败与拒答；原截止时间已过并不会变成新的一次登记。

同一包重复同步返回原导入回执，不增加事件。源／宿主实体、事件 ID 或哈希冲突会被拒绝。导入运行不能重新执行、替换完成结果或撤回；后来发现的问题追加为新事实。

## 操作入口

在 `apps/predome` 中使用现有 Python 3.11 环境及已安装的 OpenSSL。显式资产清单采用 `predome-arena-source-assets-1`：`assets` 数组逐项提供 `path`、`sha256`、`content_type` 和 `source_url`，路径相对于 `--source-root`，只列原冻结输入引用的全部公开来源版本。

```sh
python scripts/sync_arena_records.py prepare \
  --source-data-dir /path/to/isolated-source-runtime \
  --registration-proof /path/to/original-registration-proof \
  --predictions-proof /path/to/original-prediction-proof \
  --source-manifest /path/to/source-assets.json \
  --source-root /path/to/source-files \
  --output /path/to/new-cohort-bundle.tar.gz

python scripts/sync_arena_records.py verify \
  --bundle /path/to/new-cohort-bundle.tar.gz --sha256 EXPECTED_SHA256

python scripts/sync_arena_records.py import \
  --data-dir /path/to/existing-host-runtime \
  --bundle /path/to/new-cohort-bundle.tar.gz --sha256 EXPECTED_SHA256
```

prepare 读取稳定的源 DB＋WAL 字节副本，只在临时目录打开 SQLite；不在源目录创建共享内存文件，不联网、不请求时间签名。输出使用新文件且不能覆盖原包。import 要求已有宿主账本，并在写事务中检验冲突、存放私有原件、追加单事件。不能用数据库覆盖或备份还原来替代同步。

包和签名原件位于数据目录下按整包哈希命名的 `arena_imports/`，目录 0700、文件 0600。读取时再次核对归档字节与 wrapper；缺失或损坏不能继续展示为有凭证的记录。部署代码包与记录包分别传输、分别核对哈希；本源码仓库不包含私有原始事件或完整签名 manifest。

## 公开与内部展示

公开 v1.1 合同保持原字段，只比较同一目标的 `same_evidence / clinical_only` 条件。`registered_patent_pair_pilot` 在 Upcoming 中独立标识，不能替换正式赛季或把两臂当作更多模型。无校准概率保持 N/A，失败保留为 Uncertain，结果未揭晓不计命中或排名。登记库主要完成日期不自动充当结果公布日期。

内部保留全部配对条件，可查看同步时间、源链、签名覆盖范围和后续未覆盖事件。原件下载路由为 `/api/arena/lab/imports/{bundle_sha256}/proofs/{registration|predictions}/download`，使用现有团队登录权限；ZIP 保留原七文件字节，另附单独的 `import-receipt.json`，不把导入回执写入原签名文件。包含导入记录的内部 overview、详情、报告、原始账本和输入包也要求团队权限。

已核对哈希的公开来源原件可按 SHA 下载，仍与原冻结 URL、NCT 及来源类型对应。网页阅读器响应应继续标为阅读器捕获，不能改称原始 HTTP 文件。

后续结果流程通过统一解析器识别导入目标，但权限生效时间仍是宿主上的实际授权事件。导入的 AI 引用审核不授予账号身份、人工审核或科学接纳。结果需继续遵守确切试验／终点、原始公告、双人账号审核、独立科学接纳及更正／撤销规则。

## TRIUMPH-5 真实记录核对

本次输入是既有 `of-retatrutide-pair-20261027-v1`：NCT06662383，2 个模型、2 个证据条件，共 4 次原请求。3 个结构化输出和 1 个 invalid-JSON 失败完整保留，另有 3 条 AI 引用审核；人工审核为 0。原始值没有变成校准后的临床成功概率。

源链共 19 个事件，末端 SHA 为 `9217ac9c830b97c53b3ad5d2d6473285464dd48eae4575d2a26c87d5db220ca4`。登记证明于 2026-10-26 17:51:10 UTC 覆盖源事件 1–6；预测证明于 17:55:46 UTC 覆盖 1–15。事件 17–19 的 AI 引用审核不属于旧预测签名覆盖。完整同步包 SHA 为 `759777b945b611ee0631f6924d602678405ddb5c49e49597d9978f362e8c4848`。

代码测试使用临时账本和测试 CA；真实包另在旧宿主账本的副本中验证原前缀逐事件一致、330→331 单次追加、公开 2 条 control、内部 4 条记录、来源原件可下载及未登录不能取私有材料。源码检查、真实记录完整性和本地页面验收均不等于线上部署、独立科学审核或临床预测效度。
