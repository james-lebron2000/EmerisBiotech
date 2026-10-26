# Predome

截至 2026-10-26，本分支保留 **1.1.8 基础导入版本**。公开 [Arena](https://8.148.145.28/arena) 健康接口本次读取为 **1.2.7**，对应实现位于尚未合并的 [PR #3](https://github.com/james-lebron2000/EmerisBiotech/pull/3)。当前正式赛季 12 项目标、0 项正式裁定；历史回放不计入前瞻成绩。

详见[项目进展与代码位置](../../docs/PROJECT_STATUS_2026-10-26.md)及[本次只读核对](../../docs/verification/project-sync-20261026.json)。下述依赖、证据包哈希和启动说明适用于本分支的 1.1.8 基础应用，不能据此声称已经取得 Arena 1.2.7 的完整运行环境。

## 1.1.8 基础应用

临床读出日历、结构化输入、模型研究估计、不可覆盖预测快照、实际结果审核与表现分析。此目录保留当前 Predome 应用；仓库根目录的 `src/vbt` 研究工作台保持原有入口和依赖，不共用数据库。

概率尚未经前瞻临床验证；历史回放与前瞻记录分开。模型服务未配置时明确不可用，不生成替代概率。当前线上入口为 https://8.148.145.28/ 。

## 安装与测试

使用 Python 3.11，为本应用创建独立虚拟环境：

```sh
cd apps/predome
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock.txt -r requirements-research-v4.txt
.venv/bin/python scripts/test_source.py
```

公开源码测试使用隔离fixture，不需要真实证据、模型密钥或生产数据库。GitHub Actions 同时在 macOS 跑原工作台完整测试，在 Linux 跑 Predome 源码测试及两份完全拦截API的浏览器测试。测试数量与范围以实际 Actions 结果为准。

## 证据包与完整运行

第三方论文/公告原文、冻结输入和原运行档案不随 MIT 源码再发布。`evidence-files-1.1.8.json` 记录735个证据文件的来源相对路径、字节数和SHA-256；它不是数据授权。

有权访问原本地/服务器发布包的维护者可离线恢复。该命令不访问外网，不改写已有不同内容，不导入任何生产SQLite或密钥：

```sh
.venv/bin/python scripts/install_evidence.py /absolute/path/predome-1.1.8.tar.gz
.venv/bin/python -m pytest tests -q
EMERIS_DATA_DIR=/absolute/private/state .venv/bin/python run.py
```

冻结证据必须来自发布包 `4c6eceadf49f7b0de7d19fb6c02aab05a11c6f1547896e0110cf1a073c8fe6e9`。安装前核对文件，不从不可信输入生成新的预期哈希。启动后访问 `http://127.0.0.1:19320`。完整页面需要这个证据包；只有公开Git源码的检出不应被描述为包含完整真实档案的在线演示。

`source-files-1.1.8.json` 是导入时原文件清单。测试工具后续可作可移植性修改；部署脚本严格核对运行时代码、静态页面与依赖文件在Git提交、原发布包和在线manifest中的一致性。

## 部署与回滚

见 [部署操作流程](DEPLOYMENT.md)。发布先跑服务器完整测试，创建可验证数据库备份，原子切换版本目录；健康或旧档案核对失败自动回滚。GitHub源码测试不访问生产，也不持有服务器root密钥。

## 产品文档

- [输入、输出与持续迭代契约](docs/upgrade/产品交付与持续迭代规范.md)
- [分析方法](docs/analytics-method-v1.md)
- [预测锁定与核验](docs/ledger_预测锁定与核验.md)

旧工作台与本产品仍是独立运行模块。共享登录、数据迁移与跨产品导航尚未实现，不将源码同仓管理说成数据已经合并。

## 前端设计规范

主要产品页面共用 [Editorial 01 设计系统](docs/DESIGN_SYSTEM.md)，包含配色、字体、组件、响应式与深色模式约定。本次界面修订使用独立静态发布流程，保留1.1.8后端及冻结数据。
