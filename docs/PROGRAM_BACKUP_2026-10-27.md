# UC 项目记录的一致性备份与只读恢复

关联 [Issue #32](https://github.com/james-lebron2000/EmerisBiotech/issues/32) 与 [PR #7](https://github.com/james-lebron2000/EmerisBiotech/pull/7)。本轮实现项目记录的隔离恢复核对；没有完成整工作台迁移、认证角色、云端部署、执行所有权移交或生产长期恢复验收。

## 范围和版本

备份仅含 `programs.sqlite3` 和 `manifest.json`。项目、假说、证据、验证计划与结果、研发／BD 决定、原版对照、科学／资产审阅、AI 草案、模型请求和工具批次回执都在原有追加事件表中，不为恢复重写事件或补署名。

活动源数据库通过 SQLite backup API 生成一致性快照，支持 WAL 和并发追加。副本封存前切换为独立 DELETE journal 数据库，不依赖源 WAL／SHM。读取备份、核对事件或导出时使用 SQLite 只读连接。新清单版本为 1；当前支持现有事件表结构／SQLite user_version=0，未知表结构拒绝，不执行自动迁移。

清单记录数据库字节数与 SHA-256、结构摘要、事件数量、序号、每项目末端版本与完整导出 SHA-256、已核对引用及未完成请求。引用检查覆盖已实现的项目证据／计划／结果、资产资料／审阅、原版对照、AI 草案／请求／回执和批次关系。恢复核对检查完整事件链、引用类型和所属项目，不能据此认证科学内容或历史人员身份。

发布提交为操作人指定的 40 位 Git SHA；清单明确保留其声明性质，并非数字签名或发布方身份认证。数据库上限 256 MB、清单 2 MB、1000 个项目／100000 个事件；一致性复制超过 60 秒停止，不生成完成清单。首版未提供加密或签名。

## 创建、验证与恢复

先确认当前服务的源码、状态目录、端口及活动任务。下面变量由使用者指向已经存在的源目录和全新的输出路径；输出父目录需存在。

```sh
uv run python scripts/program_backup.py create \
  --state-dir "$VBT_UC_STATE" --output "$VBT_PROGRAM_BACKUP" \
  --release "$VBT_RELEASE_SHA"

uv run python scripts/program_backup.py verify "$VBT_PROGRAM_BACKUP"

uv run python scripts/program_backup.py restore "$VBT_PROGRAM_BACKUP" \
  --state-dir "$VBT_RESTORED_STATE"

uv run python scripts/program_backup.py sources --state-dir "$VBT_RESTORED_STATE"
```

已有目标、符号链接目标、错误版本、未知文件、数据库大小／哈希不符、事件链或引用不符均拒绝。恢复验证通过后才创建新目标；复制前先写只读隔离标记。中断时目标保留供核查，不覆盖旧环境，不把半成品当成功；缺失完成清单或恢复回执时如实报告未完成。目录权限 0700、输出文件 0600。

源状态不必为只读，备份不会停掉或改写源项目记录。实际切换／升级仍需单独安排全部写者、执行所有权和回退；在线备份通过不能证明两个实例可同时运行同一研究任务。

## 恢复副本的写入与执行边界

恢复目标固定为 `read_only_restore`。`Programs` 自动检测 `PROGRAMS_RESTORE_READ_ONLY`，用只读数据库连接及 query_only 拒绝写入；正常 `_append` 和直接事务调用都不能修改事件。模型请求必须先保存请求才能发送，工具必须先记录派发才能启动，恢复副本因此不会因新请求而外发或重放任务。不会复制原 worker 锁、PID 或推定原子进程已停止；没有回执的历史请求保持 unknown_no_receipt。

界面显示恢复范围和只读提示、禁用表单；HTTP POST 全部拒绝，包括非项目写入路径。历史作者与人工声明原样保留，未补成已认证身份。这个保护服务于隔离核对，不是认证或多用户权限系统；本版本不提供写入激活命令，手动删除标记不构成执行所有权移交验收。

确认端口未占用后可启动独立核对视图：

```sh
uv run vbt --workspace "$PWD" --state-dir "$VBT_RESTORED_STATE" workbench --port 18771
```

打开项目 `/programs?id=osmr-uc-independent`。完整项目 JSON 应与备份清单对应的原快照一致；停止／重启只影响该独立实例。`/program/recovery-status?check_sources=1` 分别报告当前事件／导出核对与外部绑定来源情况，不启动分析。

## 外部来源与其他资料

备份不复制原始研究队列、封存输入、外部绑定文件、主机凭据／配置、其他工作台数据库或锁文件。所有原始绑定路径与哈希都保留在历史事件中，没有为迁移改写事件。其他主机或未挂载外置盘可能找不到来源；恢复记录仍可核对与导出，来源显示 unavailable／changed／unsafe_or_oversized，不能当作科学通过。

来源检查只读取现有绑定摘要与清单，每文件最多 4 MB，不联网或下载。无绑定来源也不报全部核对通过；`sources` 命令在缺失、变化、超限或无可核对来源时返回退出码 2。该检查覆盖历史导入，不能推定其他地区、时间或网页当前内容已核实。

数据库保留完整研究自由文本和历史；主机秘密配置文件未复制不等于对研究文本完成了秘密扫描。备份不是公开交付包，不加入 Git，也不上传任何服务器。本地验收的公开说明／测试输出不含数据库或凭据。

旧市场、笔记、命题工作流与其他数据库须沿用各自配套备份方案，本轮不宣称已恢复这些模块。访问控制、HTTPS、跨主机来源解析、写入激活／进程所有权、整工作台一致迁移与长期运行仍属 Issue #32 未完成项。

## 工程检查

本轮本地全量 1831 passed / 1 skipped（未分发原始披露材料），七文件定向 173 passed。备份损坏、WAL 并发写入、中断请求和来源丢失情景使用合成工程数据。真实 UC 的独立目录恢复、停止／重启及对应提交远端 CI 另以实际回执和 PR 检查记录，不将合成故障测试称为生产长期恢复证据。
