# EmerisBiotech

面向临床资产研究的本地工作台与私有云部署准备版本。支持研究历史浏览、问题与笔记、证据追溯、快照校验及临床预测记录准入。

**状态：** 可运行的研究软件；尚未建立经过验证的二期/三期预测能力或投资收益优势。云部署配置尚未完成容器及远端上线验收。

## 安装与启动

需要 Python 3.11，推荐使用 uv。

```sh
uv sync --extra dev --frozen
uv run vbt --workspace "$PWD" workbench --port 18764
```

访问 http://127.0.0.1:18764/ 。这是本地单用户入口，提供概览、历史记录、任务观测、问题与笔记。全新仓库没有私人历史记录；不会伪造演示运行。

创建公开资料研究快照：

```sh
uv run vbt --workspace "$PWD" portfolio build examples/portfolio_mvp.json --catalog docs/intelligence/2026-09-25-mnc/assets.json
```

原始研究数据由独立流程获取，本项目不会自动下载。公开示例包含有日期及来源的研究草案；不代表已验证的管线、资产权利或投资建议。示例引用的历史本地运行不随此仓库发布。

## 已有功能

- 版本化数据契约、输入校验、分析记录、证据引用及审查状态。
- 原分析工作流的 macOS Seatbelt 沙箱；不提供不受限的 Linux 执行回退。
- 工作台历史检索、报告/日志详情、追加笔记、后台校验状态。
- 研究快照的哈希校验、反证保留、备份与恢复。
- 二期/三期前瞻预测的时间与标签准入检查及基础评分；不包含已训练的商业预测模型。
- 私有云部署配置：HTTPS 认证网关与持久卷。云端首版仅开放历史/笔记和快照校验。

## 文档

- [云部署与记录迁移](deploy/cloud/README.md)
- [工作台说明](docs/WORKBENCH.md)
- [研究快照、审查和恢复](docs/PORTFOLIO_MVP.md)
- [平台架构](docs/ARCHITECTURE.md)
- [数据交接契约](docs/DATA_HANDOFF.md)

工作台与快照的 SQLite 位于本机应用数据目录；可用 `--state-dir` 指定受控本地存储。云端显式使用 `/state`。原有使用说明中的绝对路径是开发环境示例，需要替换为自己的路径。记录迁移应单独核验来源、完整性和上传范围。

## 测试

```sh
uv run pytest -q
```

完整分析执行测试需要 macOS 沙箱环境。云端可单独执行界面及配置边界测试：

```sh
uv run pytest tests/test_workbench.py tests/test_cloud.py -q
```

2026-09-27 同步验收：按依赖锁在独立 Python 3.11 环境安装，macOS 上完整测试 **74 项通过**。Docker 容器构建与云端上线尚未验收。

本仓库不包含原始研究数据、实际运行输出、SQLite 数据库、个人笔记、访问密码或本机备份。工程测试通过不代表科学有效性、临床成功预测或投资盈利。

## License

保留仓库原有 [MIT License](LICENSE)。外部论文、数据、模型与服务的权利需独立核验，本仓库许可证不授予第三方资产的商业使用权。
