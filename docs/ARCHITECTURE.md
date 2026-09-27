# 可信研究平台 v0.1

## 信任与执行边界

数据下载在外部任务完成。`datasets.py` 只打开、校验和解析清单指定的本地文件，不提供研究数据下载能力。下载方的 `complete` 只是输入声明，平台还要验证 SHA-256、大小、结构、字段及验证前后的文件状态。

SQLite WAL 数据库位于本机 `~/Library/Application Support/VirtualBiotech/<workspace-hash>/registry.sqlite3`，不允许放到 `/Volumes`。项目目录存放注册表 JSON 导出与运行工件，可以独立恢复。

进程分工：

1. CLI / 编排器：读取研究计划、注册表，固定输入身份；模型只能起草待检查计划。
2. 分析子进程：macOS Seatbelt 默认拒绝；只读冻结代码、输入、Python 及系统运行库；只能写本次 `work`；禁止网络。不支持的系统拒绝执行，不退回无隔离运行。
3. 审查器：读取分析工件；验证证据行与结论覆盖。自动审查不代替人工科学判断。
4. 人工审查：以独立追加记录绑定原始运行 manifest SHA-256，不改写原始运行。输入的署名是本地自我声明，不是密码学签名或身份认证。

macOS Python 启动器需要读取父目录条目，因此 profile 对已知父目录授予 literal 读取权限，不递归开放用户目录。运行环境仅保留 PATH、HOME、临时目录、线程参数及 Python 启动参数，不继承模型密钥。解释器与依赖目录视为可信安装，不应在这些目录保存密钥。

资源控制：CPU 限时、单文件 1 GiB 上限、文件描述符 128；父进程监测进程组 RSS、总输出大小和文件数量，并实施墙钟超时。RSS/总输出是监测式限额，短暂超限可能发生；不等同 VM/cgroup 硬内存隔离。此版是本地单用户研究执行器，不用于运行恶意多租户代码。外部模型代码必须走 `custom` 入口，其输出不会自动获得科学证据资格。

## 三个相互独立的状态

- `execution`：pending / running / succeeded / blocked / failed / interrupted。
- `record_integrity`：记录是否已封存；`verify` 会重新检查当前磁盘内容。失败或阻塞运行也可以具有完整的失败记录。
- `scientific_review`：pending / pass / revise / insufficient_evidence。自动检查最多推进到 revise；科学 pass 必须由人工记录。

`clinical_release` 永远为 false。`verify` 的哈希和证据覆盖检查不是科学正确性证明。HTML 是封存时快照，修改文件后应重新运行 verify。

## 数据与代码谱系

数据标识为 dataset_id@version，同一版本不允许变更路径、字节或来源；重复注册幂等。下载清单的版本若只是描述文字，适配器保留 source_version，并用 sha256 前 16 位生成本地内容版本，绝不伪装成历史发布版本。

每次运行保存源代码快照、依赖锁、已安装版本、计划、输入校验值、stdout/stderr、样本流转、结果、证据和 manifest。resume 生成新的子运行，保留 parent_run；重新从头计算，不宣称恢复到某个未保存的统计步骤。使用原运行源代码，依赖版本漂移时拒绝重跑。

哈希可检测意外修改；本机注册表保存 manifest 摘要，恢复时对照导出中的摘要。若工件与受信导出同时被恶意改写，本方案不具备外部签名系统的防篡改保证。

## 扩展点

- `ModelProvider.complete(role, prompt)` 可替换；当前提供关闭模式和 OpenAI-compatible HTTPS adapter。未实测付费模型认证。
- `DatasetVersion / ResearchPlan / AnalysisRun / EvidenceClaim / DecisionRecord` 是公共 Pydantic 契约。
- `TargetIndicationHypothesis` 表示靶点、干预方向、药物形式、首发和扩展适应症及反证。
- 当前正式统计工作流只有 GEO UC；临床试验和 B7-H3 将通过相同数据契约接入，尚无可用专用分析实现。
