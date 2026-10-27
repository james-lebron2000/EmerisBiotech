# Predome

本分支提供 **1.2.9候选版本**，新增账号认证的结果核验工作区，并保留1.2.8的来源、费用及Agent故障追溯修复；实际运行版本与提交以健康接口、部署清单及收据为准。公开入口是[单试验预测对比页](https://8.148.145.28/arena)，内测工作区使用同主机18443端口及既有登录权限。公开版本展示真实模型运行记录，未校准的概率和Confidence保持N/A。

[1.2.9核验工作区](docs/RESULT_REVIEW_WORKSPACE_1_2_9.md)记录授权、原件、时间与更正轮次。

[科学结果接纳](docs/SCIENTIFIC_RESULT_ACCEPTANCE.md)将来源核对一致与正式命中／评分分开，提供保留原件和可撤销的人工接纳路径。账号认证、两票一致或AI审核本身不会获得正式成绩。

[1.2.8数据链路说明](docs/OPEN_FORECAST_RELIABILITY_1_2_8.md)记录本轮行为、测试与未关闭的科学／授权条件。已部署的1.2.7前端与本分支代码版本分别核对，不以源码更新代替上线验收。

[TRIUMPH-5 专利配对研究](docs/PATENT_PAIR_PILOT.md)记录新的离线准备入口、四次真实首轮调用、失败保留、AI 来源评注和本地截图。原始模型值未校准，目标结果待定；隔离研究记录尚未部署。

[完整实验卡v2配对](docs/PATENT_PAIR_ASSAY_V2.md)新增四次真实首轮请求与独立时间签名：1条有效输出、3条操作失败。两临床对照均失败，无法计算专利增益；公开概率保持N/A。封存数据已在临时HTTP服务追加导入，原预测和失败保留，尚未上线。

[单次请求安全诊断](docs/ARENA_ATTEMPT_DIAGNOSTICS.md)说明未来新登记 Provider 1.2.0／Agent 1.1.0 的内部类型化元数据、旧 receipt 兼容与公私隔离。旧三条失败不回填、不猜原因、不重试；本轮仅更新 GitHub 候选，未正式打包、stage 或部署，不操作服务器后台。

[方案与 SAP 原件接入](docs/TRIAL_DOCUMENT_SOURCES.md)提供官方附件清单、显式原件采集、逐页索引与待人工审核的 OCR 补录。真实 INAVO120 原件用于验证采集路径；资料获取、完整文字提取和精确预测目标资格分别记录，不回填旧运行。

[逐模型引用报告](docs/launch/model-references-20261027/README.md)将已有公开摘要、每个模型自己的原文／快照引用与空状态一起展示。公开页面在 `Rationale & sources` 内查看；[实际记录截图](docs/images/model-references-20261027/provenance.json)来自本地候选版本，不代表线上部署或引用已获独立科学支持。新专利候选的名称／别名合同见[专利资料说明](patent_data/README.md#new-candidate-identifiers)，候选标签不授予药物身份或冻结资格。

[公开读出日历](docs/READOUT_CALENDAR.md)按已记录窗口组织赛程，每项显示原模型方向分布、锁定时间和来源。项目级指引及未确认公告时间单列；历史回放不计为事前命中，未公布结果不排名。

[独立日程版本](docs/CURRENT_READOUT_UPDATES.md)将精确目标的获批当前日程与预测时的冻结窗口并列保留。AVANZAR 的[新官方指引观察](docs/AVANZAR_READOUT_OBSERVATION_20261027.md)仍待原件采集和独立审核，不作为已批准日程或临床结果发布。

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

默认检查包含静态检查和源码fixture测试，不需要真实证据、模型密钥或生产数据库。源码测试使用干净检出，排除默认目录及版本化专利目录中的受控本地冻结材料，保留Git公开的Arena和专利配置。`scripts/test_source.py`保留为兼容入口。

独立账本中的既有预测可按[已完成预测的可核验同步](docs/SEALED_RECORD_SYNC.md)接入当前服务：保留原时间、失败和签名，在宿主仅追加一条导入事件；公开与内部视图使用不同权限及字段。

公开页面的 [Clinical Brief 下载](docs/PUBLIC_CLINICAL_BRIEF.md)将所选试验的同条件记录、公开来源及结果状态保存为可离线阅读的报告和机器可读快照。资料生成时间与原预测时间分开，下载不会调用模型、写入账本或产生结果裁定。

[2026-10-27 结果来源检查](docs/records/outcome-source-check-20261027.json)记录五项试验的官方登记响应及有限公告检索范围。登记库未贴结果、未找到确切公告及试验失败分别记录；新监测资料不回填原冻结预测输入。

```sh
.venv/bin/python scripts/check.py --suite static
.venv/bin/python scripts/check.py --browser
```

`--suite static`仅运行静态检查；其中的AST依赖检查针对显式Python导入，不是任意动态执行的沙箱。`--browser`追加9份受控离线浏览器fixture：页面/API使用拦截样例，附件点击另用临时loopback HTTP传输样例，不访问生产、模型或外部服务。需预装Node 22、Playwright 1.56.1及Chromium，检查入口不会自动安装。GitHub Actions同时在macOS跑原工作台完整测试，在Linux跑Predome源码测试及这9份浏览器测试。测试数量与范围以实际Actions结果为准。

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
