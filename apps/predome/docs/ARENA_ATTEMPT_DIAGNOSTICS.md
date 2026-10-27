# Arena 单次请求的安全诊断合同

当前源码候选为未来新登记执行增加可选的内部 `attempts[n].diagnostics`，用于区分传输、响应解析和输出校验的失败边界。诊断版本为 `arena-attempt-diagnostics-1`，Provider 为 `arena-providers-1.2.0`，Agent 为 `arena-agents-1.1.0`。这些版本不升级旧运行，也不改变预测 Prompt、严格解析规则、模型、预算、临床终点或评分方法。

诊断改造提交 `8f6cf3f` 的范围为 **GitHub candidate only**，当时固定基线部署清单包含 **70 个文件、23 个 runtime 模块**，已纳入共享诊断模块、Agent 和 operator CLI；该轮未正式打包、stage 或部署上线。后续清单可能随独立功能更新，以对应提交为准。源码与测试不代表生产已经运行新版本，也不授权新模型调用。

## 合同归属与接线

- [arena_attempt_diagnostics.py](../emeris_live/arena_attempt_diagnostics.py)仅依赖标准库，拥有版本、固定枚举、数值范围、初始状态和 `sanitize_diagnostics()`。
- [arena_providers.py](../emeris_live/arena_providers.py)在实际请求边界收集已观察的有限元数据，不保存响应正文或异常文字来补充诊断。
- [arena_agents.py](../emeris_live/arena_agents.py)在第二层 attempt allowlist 中使用同一 sanitizer，将合规诊断复制到阶段 trace 和展开后的 attempts。
- [arena_admin.py](../scripts/arena_admin.py)在私有 receipt、嵌套 trace 和正常账本写入前检查已有诊断；合同拒绝时仍保存已发生请求的合规元数据、用量及费用。

`sanitize_diagnostics()`只返回完整、类型正确且不超过 **2,048 bytes** 的独立对象，或返回 `null`。字段缺失、未知字段、错误版本、任意异常文字、非法枚举、布尔值冒充整数、越界数字和非有限数值均不能通过。它不从任意字典中挑选少数看似安全的字段来修复诊断，也不让诊断缺失或无效改变主请求的终态、有效输出和 accounting。

## 字段与未知值

下列八个顶层字段及其嵌套字段必须精确匹配。未知值保留 `null`，不把未知 HTTP 状态、正文长度、JSON 位置、用量或成本填成零。

| 字段 | 实际合同 |
|---|---|
| `schema_version` | 固定为 `arena-attempt-diagnostics-1`。 |
| `failure_stage` | 下表中的固定枚举或 `null`；描述该 attempt 观察到的边界，不代替运行终态。 |
| `http_status` | 100–599 的整数或 `null`，拒绝布尔值和浮点数；收到响应头后立即记录。没有响应时保持 `null`。 |
| `content_type_class` | `json`、`html`、`plain`、`other`、`unknown` 或 `null`。实际头字段仅作有界分类，不保存原始 MIME 字符串或参数。 |
| `body_read` | 精确包含 `state` 与 `observed_bytes`，范围见下节。 |
| `json_error` | `null`，或精确包含 `kind`、`line`、`column`、`position` 的对象。分类及位置范围见下节。 |
| `visible_format` | 精确包含 `empty`、`starts_with_object`、`outer_code_fence`、`top_level_type`。前三项为布尔值或 `null`；顶层类型为 `object`、`array`、`string`、`number`、`boolean`、`null` 这些固定字符串之一，或 JSON 的 `null` 表示尚未知。 |
| `json_mode_requested` | 布尔值或 `null`，根据实际 request format 选择记录。它不强迫提供商启用 JSON 模式，也不改变请求内容。 |

初始状态为 HTTP 状态与分类未知、正文未读、JSON 错误与可见格式未知；`json_mode_requested`仅在实际请求格式已确定时填写。可见格式检查只查看解码后的可见输出，不查看隐藏推理；空内容、对象开头和外层代码围栏标志均不触发提取、去围栏、补全或修复。只有 JSON 已解析时才记录已知顶层类型。提供商 envelope 未成功解码时，不推断可见输出格式。

`content_type_class`对最多 256 字符的 Content-Type 值去除参数并分类：`application/json`及 `+json` 为 `json`，`text/html`及 `application/xhtml+xml`为 `html`，`text/plain`为 `plain`，其他合法 MIME 为 `other`，缺失、过长或非法值为 `unknown`。尚未取得响应头时仍为 `null`。不记录 Content-Length 头来冒充已读正文长度。

### 失败阶段

| `failure_stage` | 所属边界 |
|---|---|
| `transport` | 发起请求、传输或 200 响应体读取。收到响应前失败时 HTTP 状态未知；读取途中失败可保留已观察的状态及部分字节数。 |
| `http_response` | 已收到响应头，按既有非 200 规则拒绝。 |
| `provider_envelope_json` | 提供商响应 envelope 的 JSON 解析。 |
| `provider_envelope_shape` | envelope 结构或解码边界。 |
| `visible_output_guard` | 可见输出完整性、缺失、大小或凭据回显等既有 guard。 |
| `forecast_json` / `forecast_schema` | 可见预测 JSON 解析／既有预测 schema 校验。 |
| `evidence_review_json` / `evidence_review_schema` | 可见证据评阅 JSON 解析／既有评阅 schema 校验。 |
| `workflow_guard` | 请求已返回后触发的总工作流超时或跨阶段返回模型版本变化 guard。 |

一个 attempt 成功校验后阶段为 `null`。若工作流在请求前或两次请求之间失败，不补造 provider attempt、HTTP 状态或解析位置；此前已完成 attempt 的诊断也不能用来推断整条工作流成功。运行的 `status`和固定 `error_code`继续保留原来的意义。

### 正文读取与长度

响应读取上限为 **524,288 bytes**。诊断计数来自现有 `response.iter_bytes()`读取路径，不声称它是网络线上未解码的传输字节数，也不新增完整正文保存。

| `body_read.state` | `observed_bytes` |
|---|---|
| `unread` | 必须为 `null`，表示没有读取正文，不能写成 0。 |
| `complete` | 0–524,288 的精确整数，表示本次读取完整结束。 |
| `partial` | 0–524,288 的精确整数，表示已观察的部分，不能当成完整响应长度。 |
| `limit_exceeded` | 必须为 524,289，即 cap+1 下界哨兵；只表示至少超过上限，不表示完整正文恰好为这个长度。 |

已有 attempt 字段 `response_bytes`、`visible_output_bytes`及响应 hash 继续沿用原语义；部分、超限或未读取响应不伪造完整正文 hash。**非 200 响应不为诊断而读取或排空错误正文**，保持 `unread / null`。重定向仍拒绝，不跟随重定向，不自动重试。

### JSON 错误分类与位置

`json_error.kind`仅允许 `syntax_error`、`unicode_error`、`type_error`、`recursion_limit`、`duplicate_key`、`nonfinite_constant`。不记录异常的 `.msg`、`.doc`、`str(exc)`、键名、片段或任意错误正文；并非所有固定 `invalid_json` 都能获得一个已知分类。

只有真实 `JSONDecodeError`提供的语法位置才填写：`line`和 `column`为 1-based，取值 1–524,289；`position`为 0-based 字符位置，取值 0–524,288。位置针对对应 parser 输入，**不是响应字节偏移**。未知位置可为 `null`；非 `syntax_error`分类的三个位置均必须为 `null`，不从异常文字猜测位置。

## 冻结、兼容与私有边界

新的 `provider_implementation_sha256()`以 provider 文件和共享诊断 helper 文件各自的 raw SHA 构造 composite digest。Admin 继续使用原有顶层合同字段；Agent 冻结值再包含 Agent 文件 hash 和该 provider composite。只改 helper 文件也会使已登记合同不匹配，并在启动或模型派发前拒绝。未来执行必须新登记，不能沿用旧冻结合同来悄悄升级代码。

私有 receipt 外层保持 `arena-result-receipt-v1`。历史 attempts 没有 `diagnostics`时仍不加这个键；旧 receipt、封存包、事件 hash、生成／锁定时间及 TSA 覆盖保持原字节与原记录。旧 Agent 1.0 spec 可继续读取，当前执行器不执行该旧 spec。非法新诊断只变为 `null`，不回写历史档案，不使已发生费用消失，也不补造一次 provider attempt。runner 的广义异常仍以固定 `runner_exception`结束，账单状态未知，无自动重复调用。

这些字段属于内部 attempt 与私有 receipt／trace。公开总览、单试验详情、JSON record、Clinical Brief 和 campaign ZIP 不发布诊断、HTTP 细节、用量、费用、Prompt、响应正文、隐藏推理或凭据。公开下载边界也拒绝注入的内部字段，而不是信任调用方声称它们已脱敏。此变更没有新增公开调试界面。

## 旧失败与验证范围

[TRIUMPH-5 assays-v2 记录](PATENT_PAIR_ASSAY_V2.md)原来的两条 `invalid_json`和一条 `provider_http_error`继续保持原样。旧记录没有解析位置、响应正文或具体 HTTP 状态，就不能推测失败原因；`finish_reason=stop`和低于 token 上限也不能解释成已知 JSON 语法原因。本合同不回填它们、不放宽解析、不补跑失败槽，也不把操作失败计为临床疗效失败。

该记录中 Flash 临床臂的用量未知，四槽的所有货币成本均未知；其他三槽有报告的 token 用量只形成已知小计，未知不等于零。新的诊断能力不改善旧预测本身，也不构成模型能力、专利增益或临床预测效度的证明。

已有生产实现的隔离回归覆盖见：

- [共享 schema、类型、数值界限与 canary](../tests/test_arena_attempt_diagnostics.py)。
- [真实 adapter 的 HTTP 传输 fixture、解析阶段与读取状态](../tests/test_arena_providers.py)。
- [真实 Agent、临时 receipt／SQLite、合同拒绝 accounting 与 helper-only 冻结守卫](../tests/test_arena_admin_agents.py)，及[Agent 执行合同](../tests/test_arena_agents.py)。
- [实际公开 API／brief／campaign 排除内部字段，旧 v1 receipt 字节兼容](../tests/test_arena_diagnostics_public.py)。
- [固定清单与受限 runtime import](../tests/test_deploy_review_workspace.py)，实现见[部署入口](../scripts/deploy_review_workspace.py)。

这些测试使用合成 canary、临时真实文件／SQLite、实际生产代码与隔离 HTTP／系统边界，不需要模型凭据、付费调用、生产状态或 SSH。最终提交、命令、结果和部署状态以各自检查及部署收据为准，本页不把局部回归汇总成最终全项目通过次数。
