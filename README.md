# EmerisBiotech · Virtual Biotech

AI Native 研发方向新增 [AI 研究草案与只读工具](docs/AI_NATIVE_RESEARCH.md)：冻结项目上下文、验证结构化草案引用、记录八类研发风险和有停止条件的任务，并由主机执行来源核对与交互结果证据卡提取。当前会话可生成并录入真实草案；产品内自动模型调用尚未接入，工具完成不等于科学审阅通过。相关问题见 [Issue #4](https://github.com/james-lebron2000/EmerisBiotech/issues/4)。

面向研发团队与投资／BD 团队的证据研究与项目决策工作台。围绕同一个药物项目，连接目标患者、研究假说、可复核分析、验证计划和资产尽调，保留每次判断的来源、版本和修订。

当前以 **UC（溃疡性结肠炎）项目贯通两类工作**：研发侧决定下一项值得开展的验证；投资／BD 侧判断还缺哪些科学、权利与交易资料。两类判断共用证据，分别记录。

**状态（2026-10-26）：** 已有可运行的本地研究软件和独立 UC 预览。结果回写、原计划门槛比对及资产尽调记录已实现；真实 UC 验证、药物开发完整工作流和团队价值验证仍待完成。尚未证明药物研发效果、临床预测增益或投资收益优势。

## 产品目标

帮助团队从患者未满足需求形成可证伪的项目，在明确证据、不确定性和资源约束的情况下，选择下一项验证、修订假设或停止投入。最终目标是支持创建和运营高质量 biotech；软件功能与测试通过不能独立证明这一目标达成。

临床预测、资产尽调和投资评价作为独立工作流继续建设。研究关联不直接转换成药物成功概率，临床预测也不代替投资收益验证。

完整范围与完成判据见 [产品总目标](docs/BIOTECH_PRODUCT_OBJECTIVE.md)；此前临床预测与投资方向见 [项目目标与当前阶段](docs/PROJECT_GOALS.md)。

## 核心工作流

| 工作环节 | 当前能力 | 保留的边界 |
| --- | --- | --- |
| 定义项目 | 记录患者、机制、药物形式、假说、比较对象、终点、差异化及材料／权利状态 | 项目定义不代表已有分子、样本或权利 |
| 核对证据 | 支持、反证和未知分别记录，关联日期、来源、适用范围与局限 | 计算完成、记录完整性和科学审阅分别记录 |
| 制定验证 | 保存设计、成功与停止条件、拟议负责人、周期、估算预算；可冻结数值判据 | 不自动支付、预约或启动实验 |
| 回写结果 | 绑定原计划，记录独立单位、效应量、所报 95% 区间、方法与来源时间 | 区间由来源报告，本模块不重新计算或认证 |
| 对照原计划 | 区分达到所录门槛、触及停止门槛和区间仍不足以判定 | 数值比对不等于科学审阅通过；旧文字计划不事后补造数值门槛 |
| 保存研发决定 | 推进、修订、暂缓或停止，绑定当时的证据和结果版本 | 推进需面对全部当前结果；假说修订后旧准备状态需重新核对 |
| 资产与 BD 尽调 | 同一项目关联有来源的外部参考资产，记录机会解释、风险及下一项资料需求 | 登记申办方不等于所有权、许可可得性或投资结论 |
| 追溯与导出 | 追加历史、哈希链校验、完整 JSON 决策包及旧页面并发冲突提示 | 本地署名是记录人的声明，不是身份认证或数字签名 |

## 安装与启动

需要 Python 3.11，推荐使用 uv。在仓库根目录执行：

```sh
uv sync --extra dev --frozen
uv run vbt --workspace "$PWD" workbench --port 18764
```

打开 <http://127.0.0.1:18764/>。这是本地单用户入口。新安装可访问固定回顾演示并创建研发项目，无需模型密钥；真实 UC 导入、私人历史和本机数据库不随仓库分发。已有服务运行时，应先确认工作区、状态目录和端口，避免启动第二个实例。

主要入口：

| 路径 | 用途 |
| --- | --- |
| `/programs` | 研发项目、验证结果、决定及资产／BD 尽调 |
| `/assets` | 公司、登记用药、适应症、阶段与逐试验来源 |
| `/market` | 证券目录与临床登记查询 |
| `/disclosures` | 公司披露与登记名称核对 |
| `/coverage` | 数据覆盖、持久采集队列与缺口 |
| `/mapping` | 申办方关联与待核对建议 |
| `/records` | 历史分析、研究循环和冻结快照 |
| `/jobs` | 后台完整性校验 |
| `/notes` | 研究问题与笔记 |
| `/mission` | 目标、进展及阻塞记录 |

市场功能保留证券目录筛选、申办方关联、临床登记分页采集、变更历史、失败重试与持久队列；当前没有达到全药企、全管线覆盖。

## 导入既有 UC 复现资料

本项目不自动下载研究数据。UC 导入脚本读取既有封存目录，验证清单中的 SHA-256 后导入三条待审阅研究证据；可另外关联已封存的临床登记快照。

以下是本机开发环境示例。外部使用者需提供相同结构的既有资料并替换路径；这些资料不是新克隆仓库自带的文件。

```sh
export VBT_UC_STATE="$HOME/Library/Application Support/VirtualBiotech/product-iteration-20261026"
export VBT_UC_SOURCE="/Volumes/Lenovo/VirtualBiotech/platform/artifacts/uc_optimization_20261026"
export VBT_UC_CLINICAL_SOURCE="/Volumes/Lenovo/VirtualBiotech/platform/artifacts/uc_independent_20261025/source/NCT06137183_current.json"

uv run python scripts/import_uc_program.py \
  --state-dir "$VBT_UC_STATE" \
  --source "$VBT_UC_SOURCE" \
  --clinical-source "$VBT_UC_CLINICAL_SOURCE"
```

服务未运行且端口可用时，用同一状态目录启动：

```sh
uv run vbt --workspace "$PWD" --state-dir "$VBT_UC_STATE" workbench --port 18765
```

打开 [UC 项目](http://127.0.0.1:18765/programs?id=osmr-uc-independent)，切换“研发验证与结果”和“资产与 BD 尽调”。同一源重复导入保持幂等，源文件变更或之前导入未完成时显式拒绝。导入不会生成审阅通过、实验完成或人工决定。

当前本机预览使用独立状态目录和后台进程，尚未配置自动重启；原 `18764` 受管工作台与该预览分别运行。预览状态不自动合并到原工作台。

### UC 证据的实际范围

- 五队列 OSMR 数值可按明确记录的候选口径重建；完整 UC 案例仍为部分复现。
- UNIFI 的正式治疗交互分析未提供 OSMR 区分 UST 相对安慰剂获益的统计证据；这不等于证明不存在交互，也不能用于推断 OSMR 阻断药物疗效。
- Vixarelimab 为外部临床参考，不是自有候选药。已关联的 NCT06137183 快照日期为 2026-10-25，来源登记更新为 2026-02-24；试验状态不等于整个资产当前全球状态。
- 三条研究证据仍待人工科学审阅。真实项目没有被补入虚构的验证计划、预算、负责人、实验结果或尽调决定。

结果软件闭环已通过工程测试；真实 UC 实验闭环尚未完成。详见 [UC 双角色工作流与验收](docs/UC_DUAL_ROLE_2026-10-26.md)。

## 其他研究能力

保留版本化数据契约、输入校验、分析记录、研究快照哈希校验、反证、备份与恢复。原分析工作流使用 macOS Seatbelt 沙箱，不提供不受限的 Linux 执行回退。临床预测模块提供时点／标签准入及基础评分，尚无已验证的商业预测模型。

创建公开资料研究快照：

```sh
uv run vbt --workspace "$PWD" portfolio build examples/portfolio_mvp.json \
  --catalog docs/intelligence/2026-09-25-mnc/assets.json
```

公开示例是有日期和来源的研究草案；示例引用的历史本地运行不随仓库发布。

## 存储与部署

活动 SQLite 位于本机应用数据目录，可用全局选项 `--state-dir` 指定本地目录；不放在 `/Volumes`。封存分析和版本化输入保持不变，新分析使用新运行目录。研发项目导出包含完整历史与哈希链；不是独立签名或跨设备恢复验收。

已有私有云配置包括 HTTPS 认证网关、独立采集 worker 和持久卷。历史容器曾完成隔离验收，当前新增研发模块尚未重建镜像；远端 HTTPS、客户账号与生产上线验收仍待完成。详见 [部署与记录迁移](deploy/cloud/README.md) 和 [macOS 受管服务](docs/LOCAL_MANAGED_SERVICE_2026-10-18.md)。

Git 交付范围不包含原始研究数据、本机实际运行输出、SQLite、个人笔记、密码或备份；本机忽略目录中可以存在这些文件。

## 测试与已验证范围

AI 研究扩展已完成工作台全量回归 **1678 项通过、2 项跳过**；最终展示调整后的七文件定向复验 **106 项通过**。两项跳过及前后测试范围见 [Issue #4 验收记录](docs/AI_NATIVE_ACCEPTANCE_2026-10-26.md)。本轮未本地重跑独立 Predome 套件；远端 CI 状态须另行核对。

研发项目及共用界面定向回归：

```sh
uv run pytest -q \
  tests/test_program_results.py tests/test_program_diligence.py \
  tests/test_programs.py tests/test_workbench.py \
  tests/test_ui_shell.py tests/test_dashboard_view.py
```

2026-10-26 UC 双角色基础记录为 **90 项通过**，覆盖原计划门槛、结果回写／审阅／决定引用、时间与单位校验、停止条件、避免只选有利结果、BD 与研发记录独立、来源变更拒绝，以及版本、CSRF、HTTP 导出和界面回归。另已在实际浏览器核对 UC 页面及本地源记录。此数字是六个相关测试文件的结果，不是完整套件或临床验证。

运行完整测试：

```sh
uv run pytest -q
```

完整分析执行测试需要 macOS 沙箱环境。云端可单独运行界面与配置边界测试：

```sh
uv run pytest tests/test_workbench.py tests/test_cloud.py -q
```

此前市场、界面与容器验收保留在各日期文档，不与本轮结果相加，也不代表新增模块已完成云部署或长期运行验收。

## 后续重点

1. 在 UC 项目中拆解机制、患者富集、功能性药理、安全及资产可得性风险，关联证据与下一项验证。
2. 落实材料、权利、负责人和实际验证，记录失败、成本及对后续决定的影响。
3. 增加药物开发关键风险、资源受限的组合比较及 BD 可交易性评价。
4. 通过真实团队试用与冻结策略、同等资源的独立比较，验证产品是否改善决策质量、成本和效率。

## 文档索引

- [产品总目标与完成判据](docs/BIOTECH_PRODUCT_OBJECTIVE.md)
- [UC 双角色工作流与最新验收](docs/UC_DUAL_ROLE_2026-10-26.md)
- [研发项目首轮实现记录](docs/RD_PROGRAMS_2026-10-26.md)
- [市场与管线雷达](docs/MARKET_RADAR.md) · [覆盖接口](docs/MARKET_COVERAGE_API.md)
- [客户试用流程](docs/CUSTOMER_PILOT.md) · [六分钟历史演示](docs/DEMO_GUIDE.md)
- [工作台说明](docs/WORKBENCH.md) · [研究快照、审查和恢复](docs/PORTFOLIO_MVP.md)
- [平台架构](docs/ARCHITECTURE.md) · [数据交接契约](docs/DATA_HANDOFF.md)
- [市场披露历史验收](docs/MARKET_DISCLOSURES_ACCEPTANCE_2026-10-17.md)
- [界面历史验收](docs/UI_RELEASE_2026-10-18.md) · [本地容器历史验收](docs/CONTAINER_ACCEPTANCE_2026-10-17.md)

## License

[MIT License](LICENSE)。第三方论文、数据、模型和服务的权利需独立核验；本仓库许可证不授予第三方资产的商业使用权。

## Predome 临床读出产品

[apps/predome](apps/predome/README.md) 纳入 Predome 1.1.8 的应用源码、日历与预测界面、锁定/核验账本和测试。原 `src/vbt` 功能及CLI保持不变，两套应用使用独立环境和状态目录。第三方证据与实际运行档案通过哈希清单管理，不随源码公开复制。

- [Predome 产品与启动](apps/predome/README.md)
- [经过版本和哈希检查的部署流程](apps/predome/DEPLOYMENT.md)
- [两套应用的CI](.github/workflows/ci.yml)

源码整合不等于临床预测效能已得到验证，也不等于两套应用的用户和数据库已合并。
