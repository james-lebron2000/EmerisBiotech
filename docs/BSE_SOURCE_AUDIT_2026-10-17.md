# 北交所上市股票目录接入验收

2026 年 10 月 17 日 14:59（Asia/Taipei），对北交所官方公开目录完成独立全量采集验收。本验收没有修改运行中的市场数据库，也没有下载研究数据集。

## 官方来源与请求

- 目录页面：[北交所股票列表](https://www.bse.cn/nq/listedcompany.html)。
- 数据端点：`POST https://www.bse.cn/nqxxController/nqxxCnzq.do`。
- 表单固定为 `page=0&typejb=T&xxfcbj%5B%5D=2&xxzqdm=&sortfield=xxzqdm&sorttype=asc`；只允许 `page` 在 0–99 内变化。
- `Referer` 指向官方目录页面，用户代理为 `EmerisBiotechResearch/0.2`。TLS 校验开启；不跟随重定向，不处理挑战，不使用非官方镜像。
- 初始直接目录 GET、无 Referer 的 POST 返回 HTTP 403。采用公开目录的正常表单及页面 Referer 后，官方端点返回 HTTP 200。未尝试绕过访问控制；后续 403/429 将保留原始响应并失败关闭。

## 实测结果

| 项目 | 实际结果 |
|---|---:|
| 官方 `totalElements` | 351 |
| 官方 `totalPages` | 18 |
| 官方 `size` | 20 |
| 末页条数 | 11 |
| 解析并去重的证券代码 | 351 |
| 原始页面捕获 | 19，含采集结束时再次读取首页 |
| 官方行业为“医药制造业” | 20 |

全部 18 页的数量、页码、`firstPage`/`lastPage`、总量及每页大小一致，代码全局严格递增、无重复，结束时的首页内容与首次完全一致。当前观测到的代码均为 `920xxx`；程序保留来源中的六位代码，不按旧代码数字猜测新代码。工程测试另覆盖旧 `4xxxxx`/`8xxxxx` 代码原样保留。

“医药制造业”只是交易所行业标签，其中可能包括诊断、动物药品等业务；20 条行业记录不代表 20 家已核实的人用创新药公司，更不代表已关联临床管线。

实际分页信封含 `content`、`firstPage`、`lastPage`、`number`、`numberOfElements`、`size`、`sort`、`totalElements`、`totalPages`。证券使用明确命名字段 `xxzqdm`（代码）、`xxzqjc`（简称）、`xxhyzl`（行业），并核对 `xxfcbj="2"`、`xxzqjb="T"`。不使用字段位置映射。

## 证据与复查

原始证据保存在项目忽略目录 `artifacts/market/bse_source_audit/2026-10-17/`：

- `00.bin`–`17.bin` 为所有真实分页原始响应；`18.bin` 为末次首页复查。
- `audit.json` 含完整 URL、请求方法和表单、页面、UTC 抓取时间、HTTP 状态、字节数及每页 SHA-256。
- `rows.json` 为 351 条解析目录记录。

首页首次及复查 SHA-256 均为 `41287b09f2b1e551b2114b78f099d134a70aa1e746523467fbf42cb77fcfe80d`；末页为 `aee26f43a997dd5f93c93a3a04dd3a9078a11f9beeba5faf5eaa9d5054ebf32f`。原始证据不提交到公共 Git 仓库。

连接器位于 `src/vbt/bse_sources.py`；`tests/test_bse_sources.py` 的 31 项工程测试通过，涵盖固定来源、分页总量与截断、重复和乱序、页码错位、来源变化、安全 JSONP 解析、名称字段缺失、范围异常、取消及 HTTP 失败证据保留。工程测试使用明确的合成样例，与本节真实全量来源验收分别记录。

端点没有提供事务快照 token。全页一致、全局顺序及首页复查能够检测常见采集中变化，不能证明源站提供数据库级事务快照。连接器只有全部检查通过才返回可发布的 `rows`；调用方必须继续保持原子的整批替换，失败或取消不能清空既有目录。

## 集成接口

`fetch_universe(fetch=fetch_page, *, cancelled=..., on_capture=...)` 返回 `rows`、`total`、`captures`、`total_pages`、`page_size` 和来源范围。替换的 `fetch` 接口为 `fetch(ENDPOINT, data=request_body(page))`，采用固定 POST 表单。

`on_capture` 在解析前执行；收到 HTTP 失败响应时也会先保存捕获，再抛出 `BSEFetchError`。捕获记录含 `raw`、`url`、`method`、`request_body`、`status`、`page`、`retrieved_at`、`sha256`、`bytes`。调用方应为 POST 表单另存来源元数据，避免只存端点 URL 而丢失页码与请求范围。

## 工作台集成

北交所已加入固定来源名单、持久采集队列及 24 小时证券目录更新计划。`Market.refresh_universe('BSE', bse_fetch=...)` 单独使用固定 POST 路径，逐个归档全部捕获及请求元数据，只有检查完整通过才在同一租约保护的数据库事务中发布目录与成功运行记录。中断、HTTP 失败、解析异常、租约丢失或发布中取消均保留既有快照。

新增 `universe_snapshots` 为当前发布目录记录运行编号、行数及规范化内容哈希，并与目录替换同一事务提交。覆盖页仅在北交所当前目录与成功运行及哈希一致时移除“无已核对目录”缺口；最近刷新失败时显示保留的成功目录及新的失败信息。没有成功捕获依据的手工目录、被改动的记录或空目录均不能消除该缺口。药企分母及管线完整性仍独立标为未知。

`tests/test_bse_integration.py` 的 15 项合成集成测试覆盖队列和计划调度、完整捕获、原子发布、数据库写入失败回滚、捕获前后及发布中取消、租约替换、目录哈希变化、失败状态展示与行业候选识别。真实生产入库由主任务另行执行和验收。
