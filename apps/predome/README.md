# Predome

本分支提供 **1.2.8候选版本**，修复专利原响应追溯、原币费用比较及Agent阶段故障复盘；实际运行版本与提交以健康接口、部署清单及收据为准。公开入口是[单试验预测对比页](https://8.148.145.28/arena)，内测工作区使用同主机18443端口及既有登录权限。公开版本展示真实模型运行记录，未校准的概率和Confidence保持N/A。

[1.2.8数据链路说明](docs/OPEN_FORECAST_RELIABILITY_1_2_8.md)记录本轮行为、测试与未关闭的科学／授权条件。已部署的1.2.7前端与本分支代码版本分别核对，不以源码更新代替上线验收。

[1.2.5完整目标审计快照](docs/OPEN_FORECAST_STATUS_1_2_5.md)记录已交付能力、实际来源覆盖及剩余外部门槛；它区分软件可用与科学效度。新前端说明见[1.2.6竞技场展示](docs/ARENA_PRESENTATION_1_2_6.md)。原始时间凭证、预测失败和历史回放均保留。官方专利授权连接、真实科学审核、前瞻效度及活动实际执行尚未完成。

以下安装与证据恢复说明保留1.1.8基础导入包的版本、哈希和兼容路径。Open Forecast新增的冻结输入、专利原件及运行状态另有独立清单，不由MIT源码许可证授予其数据权利。

临床读出日历、结构化输入、模型研究估计、不可覆盖预测快照、实际结果审核与表现分析。此目录保留当前 Predome 应用；仓库根目录的 `src/vbt` 研究工作台保持原有入口和依赖，不共用数据库。

概率尚未经前瞻临床验证；历史回放与前瞻记录分开。模型服务未配置时明确不可用，不生成替代概率。当前线上入口为 https://8.148.145.28/ 。

## 安装与测试

使用 Python 3.11 和 Node 22（用于静态 JavaScript 语法检查），为本应用创建独立虚拟环境：

```sh
cd apps/predome
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock.txt -r requirements-research-v4.txt
.venv/bin/python scripts/check.py
```

默认检查包含静态检查和源码fixture测试，不需要真实证据、模型密钥或生产数据库。源码测试使用干净检出，排除受控本地冻结材料，保留Git公开的Arena和专利配置。`scripts/test_source.py`保留为兼容入口。

```sh
.venv/bin/python scripts/check.py --suite static
.venv/bin/python scripts/check.py --browser
```

`--suite static`仅运行静态检查；其中的AST依赖检查针对显式Python导入，不是任意动态执行的沙箱。`--browser`追加7份完全拦截API的浏览器fixture，需预装Node 22、Playwright 1.56.1及Chromium，检查入口不会自动安装。GitHub Actions同时在macOS跑原工作台完整测试，在Linux跑Predome源码测试及这7份浏览器测试。测试数量与范围以实际Actions结果为准。

完整命令、前置条件和工程／科学验证的边界见[开发与验证工作流](docs/AGENT_WORKFLOW.md)。原工作台仍从仓库根目录运行`uv run pytest -q tests`，与Predome包内检查分开记录。

## 证据包与完整运行

第三方论文/公告原文、冻结输入和原运行档案不随 MIT 源码再发布。`evidence-files-1.1.8.json` 记录735个证据文件的来源相对路径、字节数和SHA-256；它不是数据授权。

有权访问原本地/服务器发布包的维护者可离线恢复。该命令不访问外网，不改写已有不同内容，不导入任何生产SQLite或密钥：

```sh
.venv/bin/python scripts/install_evidence.py /absolute/path/predome-1.1.8.tar.gz
.venv/bin/python scripts/check.py --suite full
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

既有产品页面共用 [Editorial 01 设计系统](docs/DESIGN_SYSTEM.md)。当前Arena公开页采用试验信息、模型对比、共识与实际结果三个模块；内部研究和调试页面使用独立访问控制。历史1.1.8前端发布说明不代表当前生产后端版本。
