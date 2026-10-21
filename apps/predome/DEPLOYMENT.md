# Predome 1.1.8 发布流程

该流程面向已部署1.1.7的现有Linux主机。它不创建全新服务器，也不自动安装模型服务或审核员账号。源码CI和生产发布分离，生产密钥仅留在操作员本机，GitHub Actions无需生产凭据。

## 发布门槛

1. 在独立分支改动，两个CI任务通过后合入主分支。保留 `src/vbt` 的完整回归。
2. 维护者本机准备有权使用的已封存发布包，核对SHA-256。冻结数据、预测和来源文件不能在代码发布中改写。
3. 服务器已验证的主机密钥存在于本机 `known_hosts`；使用指定私钥，保持严格主机校验。
4. 提交所有运行时代码。部署脚本将逐项比较Git提交、工作区、发布包和线上manifest，拒绝未提交或不一致的源码。

## 实际部署命令

在 `apps/predome` 中执行：

```sh
python3.11 scripts/deploy_release.py \
  --host 8.148.145.28 \
  --identity /absolute/path/to/authorized-key \
  --archive /absolute/path/predome-1.1.8.tar.gz \
  --apply \
  --receipt /absolute/private/receipts/predome-1.1.8.json
```

`--apply` 从 `20261021-platform9` 升到 `20261021-platform10`；已在目标版本时只核验，不重启。`--verify` 只核验已上线版本。此脚本故意绑定1.1.8；下一版本须建立新的清单、测试和部署目标，不能只修改版本字符串绕过校验。

服务器过程：校验旧/新manifest及冻结数据 → 在隔离QA存储运行完整pytest → SQLite一致备份及哈希核对 → 幂等导入既有档案 → 原子切换current → 重启应用 → 健康、档案哈希、快照数和基线核对。失败恢复旧current。生产运行数据在 `/var/lib/emeris-live`，不在Git检出中。

成功后生成包含Git提交、发布包SHA、运行时代码绑定数量及线上健康状态的独立回执，保存在操作员指定路径及服务器 `/opt/emeris-live/platform10-verification-20261021/`。不修改封存版本的manifest。再通过公开HTTPS检查首页、比较、详情、历史页和移动端。

## 连接恢复策略

显式指定密钥与 `IdentitiesOnly=yes`，使用 `-F /dev/null` 避开本机无关SSH配置，保留严格主机校验；通过私有临时控制socket复用连接，配置存活探测。仅在建立连接时最多重试3次；变更命令发生不明传输中断时不盲目重跑，先检查current和回执。

2026-10-21实际恢复了密钥登录，使用复用连接完成发布。之前两次断连的唯一根因尚未证实，没有为排障关闭服务器认证或主机密钥校验。

## 回滚

保留原 `20261021-platform9` 和发布前备份。部署脚本自带失败回滚。上线后若需人工回滚，先确认当前版本与活动数据写入，再将current原子指回已有完整旧版本并重启 `emeris-live`。不要用旧数据库覆盖新业务记录；需要数据恢复时另外进行停写、一致性校验与恢复演练。

主机上的Node、Redis、MySQL等其他服务不属于此应用，升级不得停止它们。
