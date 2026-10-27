# EmerisBiotech

面向临床资产研究的本地工作台与私有云部署准备版本。支持研究历史浏览、问题与笔记、证据追溯、快照校验及临床预测记录准入。

**状态：** 可运行的研究软件；尚未建立经过验证的二期/三期预测能力或投资收益优势。本地容器已完成隔离运行验收，远端 HTTPS、客户账号与上线验收尚未完成。

## Patient Scenario Lab

Open `/patient-lab` for the English-only patient showcase. Anonymous patient **P001** is synthetic. Compare 24-week scenarios for fictional A, B and A+B; save runs, export JSON and create new versions from week-6/week-12 synthetic follow-ups. Earlier forecasts remain unchanged. Survival curves are independent mathematical illustrations; clinical response, PFS, OS and toxicity remain unavailable. See the [English guide](docs/PATIENT_LAB.md).

## 安装与启动

需要 Python 3.11，推荐使用 uv。

```sh
uv sync --extra dev --frozen
uv run vbt --workspace "$PWD" workbench --port 18764
```

访问 http://127.0.0.1:18764/ 。这是本地单用户入口，提供研究首页、可搜索的公司与资产入口、案例证据卡、演示判断时间线、历史记录、完整性校验和问题笔记。市场采集任务位于覆盖进度页。全新仓库可直接体验固定回顾案例，无需模型密钥；私人历史记录不随仓库发布。详见 [六分钟演示指南](docs/DEMO_GUIDE.md)。

创建公开资料研究快照：

```sh
uv run vbt --workspace "$PWD" portfolio build examples/portfolio_mvp.json --catalog docs/intelligence/2026-09-25-mnc/assets.json
```

原始研究数据由独立流程获取，本项目不会自动下载。公开示例包含有日期及来源的研究草案；不代表已验证的管线、资产权利或投资建议。示例引用的历史本地运行不随此仓库发布。

## 项目目标

用可追溯、可校准的二期／三期临床结果预测，寻找并验证扣除成本后的投资优势。先验证临床预测相对简单基准的增益，再独立验证投资收益；软件可运行不等于上述能力成立。详见 [项目目标与当前阶段](docs/PROJECT_GOALS.md)。

工作台新增“目标与里程碑”：验收要求、下一步、进展和阻塞历史，支持关联已有研究记录，不自动作出科学审批。

## 已有功能

- 版本化数据契约、输入校验、分析记录、证据引用及审查状态。
- 原分析工作流的 macOS Seatbelt 沙箱；不提供不受限的 Linux 执行回退。
- 工作台历史检索、报告/日志详情、追加笔记、后台校验状态。
- 研究快照的哈希校验、反证保留、备份与恢复。
- 临床资产页的公司采集上下文、角色快捷切换、阶段原文与批次筛选、可移除条件和逐项本地证据入口；历史列表显示明确保存的时间，缺失时间保留未知。
- 二期/三期前瞻预测的时间与标签准入检查及基础评分；不包含已训练的商业预测模型。
- 美股、A股、港股证券目录筛选、申办方关联、临床登记查询及持久采集队列。
- 公司官网管线披露、保守的同公司登记核对、可校验 CSV 导出与覆盖缺口。
- 私有云部署配置：HTTPS 认证网关、独立采集 worker 与持久卷；分析执行仍受原沙箱平台限制。

## 文档

- [市场与管线雷达](docs/MARKET_RADAR.md)
- [客户试用流程](docs/CUSTOMER_PILOT.md)
- [本轮真实采集与界面验收](docs/MARKET_DISCLOSURES_ACCEPTANCE_2026-10-17.md)
- [工作台 UI 迭代验收](docs/UI_REFRESH_2026-10-17.md)
- [临床资产与研究历史 UI 验收](docs/UI_RESEARCH_WORKFLOW_2026-10-17.md)
- [公司搜索、逐试验证据与历史可用性验收](docs/UI_WORKBENCH_USABILITY_2026-10-17.md)
- [临床资产研究工作台与实机验收（2026-10-18）](docs/UI_RELEASE_2026-10-18.md)
- [macOS 本地受管服务](docs/LOCAL_MANAGED_SERVICE_2026-10-18.md)
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

2026-10-17：历史 UI 发布候选完整测试 **435 项通过**；后续市场接入后端完整测试 **831 项通过**。本轮公司搜索、逐试验证据与历史 UI 的聚焦回归 **269 项通过**，独立 UI 复审回归 **101 项通过**；没有将聚焦回归报告为重跑完整套件。先前容器构建通过 **22 项隔离运行检查**，后续源码尚未重建容器镜像。远端 HTTPS、认证网关及客户生产部署仍待验收。

2026-10-18：资产研究、历史、共享界面、市场采集/查询及本地服务的联合回归 **422 项通过**。已在本地受管服务中核对桌面和窄屏页面、组合筛选、逐项来源、本地 NCT 查询和历史时间显示；这是相关范围的回归和本地实机验收，本轮未重跑完整套件或重建云端镜像。

本仓库不包含原始研究数据、实际运行输出、SQLite 数据库、个人笔记、访问密码或本机备份。工程测试通过不代表科学有效性、临床成功预测或投资盈利。

## License

保留仓库原有 [MIT License](LICENSE)。外部论文、数据、模型与服务的权利需独立核验，本仓库许可证不授予第三方资产的商业使用权。

## 市场与管线雷达

新增 `/market`：美股、A股、港股证券名录抓取，公司与申办方映射、临床登记分页采集、字段变更历史和定时更新。支持药物、适应症、市场、阶段和状态查询。当前没有达到全药企全管线覆盖。运行方式、来源与验收边界见 [市场雷达说明](docs/MARKET_RADAR.md)。

## 扩展药企识别与覆盖进度

`/coverage` 展示三地候选识别、申办方映射、采集进度和缺口；`/assets` 展示公司候选药物与逐项试验的角色及来源。持久采集队列支持有限并发、取消、重试、进程恢复及原子快照发布。参见 [客户试用流程](docs/CUSTOMER_PILOT.md) 与 [覆盖接口](docs/MARKET_COVERAGE_API.md)。

## Predome 临床读出产品

[apps/predome](apps/predome/README.md) 纳入 Predome 1.1.8 的应用源码、日历与预测界面、锁定/核验账本和测试。原 `src/vbt` 功能及CLI保持不变，两套应用使用独立环境和状态目录。第三方证据与实际运行档案通过哈希清单管理，不随源码公开复制。

- [Predome 产品与启动](apps/predome/README.md)
- [经过版本和哈希检查的部署流程](apps/predome/DEPLOYMENT.md)
- [两套应用的CI](.github/workflows/ci.yml)

源码整合不等于临床预测效能已得到验证，也不等于两套应用的用户和数据库已合并。

### Patient × Molecule Lab (development branch)

Patient-specific paired functional assays replace manual resistance settings in the new workflow.
Run `/molecule-lab` in the workbench or open `molecule-lab/` in the public site build.
Three synthetic patients, two named molecules, explicit missing-pair abstention and traceable assay forecasts.
Clinical response and survival remain unavailable. See [design and validation gates](docs/PATIENT_MOLECULE_LAB.md).
