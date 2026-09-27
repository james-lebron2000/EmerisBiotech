# 市场与管线雷达

目标是建立覆盖美股、A股、港股药企的可追溯资产情报层。本版本完成证券名录采集、公司与申办方映射、ClinicalTrials.gov 登记采集、变更日志、页面查询及定时更新。**尚未完成全市场药企识别和全药物管线覆盖。**

## 使用

启动工作台后进入 `/market`：

1. 抓取名录，按证券代码或名称查找公司。
2. 添加公司及准确英文申办方别名，并写明映射依据。支持母公司多个申办方别名；子公司关系仍需核对。
3. 点击“抓取临床登记”。任务在后台执行，刷新页面查看状态；查询市场、公司、药物、适应症、阶段、试验状态。
4. 开启自动更新。工作台进程运行时每分钟检查到期任务，名录间隔 24 小时，已配置公司的登记间隔 6 小时。关闭工作台后暂停，重新启动后继续检查到期任务。不是全天在线云服务，也不是源站事件推送。

批量命令：

```sh
uv run vbt --workspace "$PWD" market universe all
uv run vbt --workspace "$PWD" market import-companies examples/market_companies.json
uv run vbt --workspace "$PWD" market sync all
uv run vbt --workspace "$PWD" market sync US:PFE --max-pages 40
```

公司导入是新增操作，重复代码会拒绝，不会静默修改归属。批量任务失败、部分分页或已有任务占用返回非零状态，详情输出 JSON。环境需要系统 curl，TLS 校验开启。研究原始数据目录不参与这些采集。

## 已接入来源

| 来源 | 内容 | 局限 |
|---|---|---|
| [Nasdaq Trader Symbol Directory](https://www.nasdaqtrader.com/trader.aspx?id=symboldirdefs) | Nasdaq 及其他美国交易所证券目录 | 排除明确 ETF 和测试代码；其他非普通股类型仍可能存在，无全市场药企分类 |
| [SEC ticker/exchange](https://www.sec.gov/files/company_tickers_exchange.json) | 备用美国证券目录 | 本机首次请求 403；失败可见，不影响其他来源。必要时配置真实 `VBT_SOURCE_USER_AGENT` 联系信息，不能保证访问获准 |
| [HKEX 证券名单](https://www.hkex.com.hk/Services/Trading/Securities/Securities-Lists?sc_lang=en) | 官方表格 Equity 类证券 | 不包含完整行业分类；证券不能等同公司或药企 |
| [上交所股票列表](https://www.sse.com.cn/assortment/stock/list/share/) | 主板 A 股及科创板，分别采集 | 未覆盖北交所 |
| [深交所股票列表](https://www.szse.cn/market/product/stock/list/index.html) | A 股官方 xlsx 导出及行业字段 | JSON 接口实测只返回第一页，已弃用该采集路径 |
| [ClinicalTrials.gov API](https://clinicaltrials.gov/data-api/api) | 申办方、药物/生物制品干预、阶段、适应症、登记状态及源更新日期 | 不覆盖全部临床前、中国境内试验及公司管线；源更新存在延迟 |

抓取仅使用固定白名单来源，不接受任意远程 URL。对 429/临时错误有限重试，失败保留旧快照。原响应及 URL、SHA-256、抓取时间保存在 `artifacts/market/captures/<run_id>/`；只选择非联系人字段。源站名录接口可能变化，解析失败不清空旧名录。

## 证据语义

- `listings` 是证券目录，不是已鉴定的药企清单。多地上市与重复来源可能重复。
- `companies` 是显式配置的证券到申办方映射；当前示例只有三家，不能当作全市场已覆盖。
- `trials` 按公司与 NCT 编号去重。精确匹配 lead sponsor；不把 collaborator 查询命中自动归属给公司。
- 干预项可能是安慰剂、标准治疗、联合药物。没有把每个干预项宣称为公司自有药物，也没有按同名药物自动合并资产。
- COMPLETED 不代表试验成功；主要完成日期不是结果读出日期。历史记录的首次抓取时间不能倒填为历史可用证据。
- `changes` 记录首次观察及字段变化的前后内容与任务编号。无变化不重复创建变更；不把源站遗漏当作管线终止。
- `succeeded` 仅指配置别名的 API 查询遍历完成；`partial` 指达到页数上限。都不证明公司完整管线已收齐。
- 显示源更新日期与抓取时间。旧记录在失败时保留，不能据此推断状态仍然有效。

## 存储与恢复

活动数据库为工作区对应本机状态目录下 `market.sqlite3`，与研究审计数据库分开。备份应使用 SQLite backup API，恢复应在停止服务后将备份放回同一状态目录，并保留 captures。此数据库尚未接入原研究注册表的 `vbt restore` 命令。抓取租约避免同一公司并发重复任务；异常进程退出后租约最多两小时过期，届时记录标记 interrupted 并可重试。任务分页上限时可以提高 `--max-pages` 重跑，现有观察去重；尚不支持跨进程分页游标续传。

## 全市场产品仍需完成的工作

1. 证券 → 法人 → 母子公司 → 申办方实体主表，加入历史更名、退市与多地上市合并；完整药企分类需核对或接入授权行业目录。
2. 公司 IR 管线页、年报、公告以及中国药物临床试验登记、CDE/监管来源适配。临床前项目和撤回项目不能只靠 ClinicalTrials.gov。
3. 药物别名、适应症、试验、授权地域与所有权分层建模；自动提取作为候选，由可追溯审查决定归属。
4. 按公司、来源、阶段定义覆盖分母，抽样核对年报中的漏检/误归属；未通过前不宣称“全量”。
5. 部署独立常驻云采集服务、来源级频率限制、监测告警和容量指标，再承诺更新 SLA。

公开网页与 API 的可访问性不自动赋予批量再分发权；外部发行产品前需要核对所采用来源的许可。本仓库只发布代码与少量来源配置，抓取内容不提交到 Git。
