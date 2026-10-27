# 产品内研究模型调用

对应 [Issue #29](https://github.com/james-lebron2000/EmerisBiotech/issues/29)。`research_model.py` 为 UC 工作台增加一个主机配置、显式触发的 Chat Completions 兼容适配器；旧的本地草案导入和只读工具继续可用。

请求字段与返回结构核对依据为 [OpenAI 官方 Chat Completions 契约](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)。本实现使用非流式请求、输出 token 上限及可配置 JSON object 模式；JSON 模式不替代本地主机对来源、风险和任务的严格校验。不预设具体模型，也不保证任意第三方网关都兼容。

## 主机配置

仅从以下主机环境变量读取。网页和模型输出都不能修改地址、凭据或调用上限。

| 变量 | 作用／范围 |
| --- | --- |
| `VBT_RESEARCH_MODEL_URL` | 完整 HTTPS endpoint，路径以 `/chat/completions` 结尾；拒绝 URL 用户密码、查询串和片段 |
| `VBT_RESEARCH_MODEL` | 操作者指定模型 ID；回执另记提供方返回模型，不推定二者相同 |
| `VBT_RESEARCH_MODEL_API_KEY` | 仅主机注入；不写入项目事件、Git、页面或导出；不从其他应用搜集或复用凭据 |
| `VBT_RESEARCH_TIMEOUT_SECONDS` | 默认 60，范围 5–120；网络操作超时并对响应读取检查剩余时限，不构成操作系统 DNS 调用的严格墙钟保证 |
| `VBT_RESEARCH_MAX_OUTPUT_TOKENS` | 默认 8192，范围 512–16384 |
| `VBT_RESEARCH_TOKEN_PARAMETER` | 默认 `max_completion_tokens`；网关需要旧字段时显式配置 `max_tokens`，没有自动探测或重试回退 |
| `VBT_RESEARCH_JSON_MODE` | 默认 `json_object`；明确配置 `none` 可省略该请求字段，本地主机严格校验保持不变 |
| `VBT_RESEARCH_INPUT_USD_PER_MTOK`／`VBT_RESEARCH_OUTPUT_USD_PER_MTOK` | 可选，必须成对提供有限非负数；主机指定的统一费率，不代表当前实际价格或账单 |

主机配置示例仅设置非秘密字段：

```sh
export VBT_RESEARCH_MODEL_URL="https://your-model-service.example/v1/chat/completions"
export VBT_RESEARCH_MODEL="your-configured-model-id"
```

凭据由实际使用者通过本机密钥管理或受限服务环境注入。重启的是需要使用该配置的实例；先核对现有实例，不隐式重启其他服务。未配置地址／名称、缺失凭据或配置无效时，界面明确显示模型生成不可用，不创建替代模型输出。

## 单次调用流程

页面展示实际发送目标、模型、上限、数据类别和完整冻结上下文。记录人主动勾选发送后提交；保存本地草案或查看页面本身不调用模型。冻结包省略署名与本机绑定路径，但自由文本仍可能有私人信息，应在发送前核对。

提交同时绑定项目 revision、packet hash 和非秘密配置指纹。地址／模型／上限发生变化、上下文过期或页面未选择发送，均在请求前拒绝。上下文最多 256KB；每次调用最多读取 1MB 响应，草案仍限 64KB。操作在主机执行，不向分析 worker 继承凭据。

先保存 `ai_model_request` 再发起一次 HTTPS 请求。直接使用标准库，不使用 SDK 的自动重试、代理或重定向机制；非 200 响应保留状态码但不读取错误正文。请求不提供模型工具权限，`stream=false`、`store=false`，只发送所示上下文和版本化提示。第三方服务的实际保留策略须按该服务约定核对。

响应仅接纳一项 assistant 可见 JSON 且正常停止的输出。拒绝围栏包裹、重复键、非有限值、额外任务参数、未知引用、截断、拒答及工具调用；不修复、不截取、不补发模型请求。隐藏推理不进入事件，原始响应不保存；记录有边界的完整响应哈希或部分读取状态。明文及 JSON 解码后的凭据回显被拒绝。

`ai_model_receipt` 一次写入调用结果与有效待审阅草案。它保留配置模型／返回模型、请求与响应摘要、版本、时间、HTTP 状态、固定失败代码及提供方报告用量。来源变化时记录 stale 并保留用量，但不接纳当前草案。回执本地写入发生并发冲突时仅重试存储，不重复模型请求；极端连续冲突会留下未完成请求，不能推定免费、未发出或成功。

响应未提供的用量保持 null。只有完整且一致的报告 token 数以及成对费率才能形成成本估算；缓存／推理明细不重复相加。估算基于统一主机费率，不能替代实际账单；`actual_billed_cost` 保持 null。本轮没有新增按美元总预算调度，此部分由 #30 继续建设。

进程在网络调用后中断时，请求仍存在、完成状态未知；页面不会补造成功回执或自动重试。新的人工请求保留独立历史。

## CLI

先导出上下文并查看配置／指纹，均不发送：

```sh
uv run python scripts/ai_research.py --state-dir "$VBT_UC_STATE" \
  --program osmr-uc-independent packet --output /tmp/uc-model-context-new.json

uv run python scripts/ai_research.py --state-dir "$VBT_UC_STATE" \
  --program osmr-uc-independent model-preview
```

实际使用者核对内容和主机目标后，采用预览中实际给出的哈希：

```sh
uv run python scripts/ai_research.py --state-dir "$VBT_UC_STATE" \
  --program osmr-uc-independent model-generate \
  --packet-hash ACTUAL_PACKET_HASH --config-hash ACTUAL_CONFIG_HASH \
  --send-context --author "实际调用记录人"
```

失败或过期返回非零退出码，回执如实保留。CLI 不读取模型返回的命令，不执行生成任务。

## 当前验收与未完成项

传输 fixtures 使用实际适配器、临时项目库和合成响应，覆盖成功、HTTP／超时失败、部分读取、大小与格式边界、上下文及配置变化、并发写入、来源与工具契约、CSRF 和凭据回显。这些结果属于工程验收，不是实际提供方生成或模型质量评价。

实际本机研究模型地址、名称和专用凭据均未配置。独立 UC 预览显示不可用；真实模型服务请求数为零。既有助手会话草案继续按原来源保留，没有被改标为产品内模型输出。真实 UC 调用、实际用量／费用、建议质量和独立科学审阅仍待完成；#29 保持打开。模型生成不自动改变证据审阅、实验、研发或 BD 决定。

工程验证记录：最终工作台全量 **1751 passed / 1 skipped**（80.02 秒），八文件定向 **175 passed**。跳过项依赖未随仓库分发的原始公司披露材料，没有下载回退。本机实际 UC 配置检查、页面及程序库读回均确认 0 项模型请求、0 份模型服务草案；三条证据仍待审阅，无新实验／结果／决定。
