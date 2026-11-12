# Predome 受控多模型 Agent 执行器

实现模块：`emeris_live/arena_agents.py`，当前版本 `arena-agents-1.2.0`。新增版本仅用于未来登记；旧规格、运行和结果保持原样。

这是可以执行真实模型调用的固定流程：确定性读取已冻结证据 → 模型 A 做结构化证据审查 → 模型 B 结合相同证据与审查给出预测或拒答。它不自主联网，也不由模型选择工具。专利下载、公共来源检索及来源审核在登记前完成；登记后的执行只能使用已经冻结的资料。

它是开放系统比较轨道的一种明确实现。比较的是整套模型组合与工作流，不把差异全部归因于某个基础 LLM。代码可运行、真实请求成功、结果经时间证明、临床预测有效性需要分别核验。本实现和单元测试本身不证明预测改善。

## 两个可登记的系统

使用配置中的精确模型 ID 创建两个组合，例如：

```python
from emeris_live.arena_agents import make_agent_spec

first = make_agent_spec(
    'agent-kimi-deepseek', 'kimi-k3', 'deepseek-v4.1-flash',
    tier='review4096-forecast8192-v1',
)
second = make_agent_spec(
    'agent-deepseek-kimi', 'deepseek-v4.1-flash', 'kimi-k3',
    tier='review4096-forecast8192-v1',
)
```

这些名字不是已经完成调用的宣告。先从 `arena_providers.list_models()` 读取实际配置；组件必须存在、具有精确请求版本和配置哈希，两个组件不得指向同一提供方下的同一请求版本。组件不可用会保留失败状态。

## 登记与执行契约

```python
from emeris_live import arena_providers
from emeris_live.arena_agents import freeze_agent_contract, run_agent

budget = {
    'max_requests': 2,
    'max_output_tokens': 8192,
    'timeout_seconds': 120,
    'max_input_chars': 100000,
    'budget_policy': 'token_only_unpriced',
    'stage_budgets': {
        'evidence_review': {'max_output_tokens': 4096, 'timeout_seconds': 120},
        'forecast': {'max_output_tokens': 8192, 'timeout_seconds': 120},
    },
    'max_total_output_tokens': 12288,
    'max_total_timeout_seconds': 240,
}
models = arena_providers.list_models()
contract = freeze_agent_contract(first, frozen_packet, budget, models)

# 在第一次模型调用前，把完整 contract 保存到登记槽的 agent_contract，
# 并保存模型组合、预算和 packet。登记、外部时间证明由现有 operator 负责。
result = run_agent(
    first, frozen_packet, budget=budget,
    models=arena_providers.list_models(), frozen_contract=contract,
)
```

上例中 `frozen_packet` 是登记前准备并核对的输入，不是实时搜索结果。两个组合应绑定同一目标定义、证据截止时间、证据包、预算及预算层级。不能仅凭相同 `tier` 名称断言预算相同；应核对 `execution_budget_sha256` 和完整阶段配置。

契约包含以下字段，可接入现有账本：

| 字段 | 用途 |
| --- | --- |
| `model_id` / `requested_model` | Agent 身份与确定性的请求组合标识 |
| `provider` | 固定为 `multi_model_agent` |
| `config_sha256` | Agent 规格、组件公开配置和 provider 版本的组合哈希 |
| `components` | 每一角色的精确配置模型 ID、请求版本、提供方及配置哈希 |
| `expected_provider_input_sha256` | Provider 实际使用的冻结资料投影哈希 |
| `expected_prompt_sha256` | 审查与预测两阶段提示词哈希的有序组合 |
| `provider_implementation_sha256` | Agent 与 provider 实现文件哈希的组合 |
| `agent_implementation_sha256` / `component_implementation_sha256` | 分别定位两个实现文件 |
| `execution_budget_sha256` | 完整登记预算哈希 |

`run_agent` 在调用前重新计算完整契约。组件、版本、实现代码、提示词、目标、截止、证据或预算变化均会阻止执行。运营者必须创建新的登记槽，不可替换旧记录。该模块不直接写账本；外层 operator 仍负责一次性开始、完成、失败保留和禁止重试。

Provider 的组合实现哈希绑定 Provider 文件、内部诊断合同和[来源位置合同](RECORDED_EVIDENCE_LOCATIONS.md)。两阶段均保留显式提供的页码、章节和文档版本；这只是输入元数据，不代表 Agent 取得完整原件、核实定位真实性或完成科学审核。

## 工具与模型边界

只允许两种工具：

- `frozen_evidence.list`：列出已登记证据 ID，采用固定排序。
- `frozen_evidence.lookup`：按精确 ID 返回登记包中的原记录副本。

工具不访问 URL、不读任意路径、不执行 shell 或 Python，也不解释证据里的操作指令。证据条数上限为 100，工具调用上限最多 101，调用顺序为一次列举及逐条查阅。查阅使用排序；传给模型的证据仍保持原包顺序。每次工具调用保存参数、返回内容哈希和剩余额度，轨迹可重复核对。

第一阶段仅调用 `review_evidence`，只接受绑定既有证据 ID 的简洁观察和未知项，不能输出概率。第二阶段调用 `forecast(..., evidence_review=review)`；审查视为不可信的辅助数据，并以哈希绑定其实际输入，不传递其他模型的预测概率。两个阶段都只通过既有 provider 适配器访问已配置的模型服务。

## 预算、失败与费用

总模型请求数固定为两次，各阶段最多一次。支持分别冻结审查与预测的输出上限及超时；外层总上限必须能容纳预定两个阶段。提供方配置的更小上限仍然生效。对于不同系统的公平比较，应检查实际组件配置及请求回执，而非只看预算名称。

如果第一阶段失败、拒绝契约或产生不合法审查，就停止，不调用第二阶段。第二阶段失败时保留第一阶段的回执、用量与已知费用。任何异常都不会自动重试、修复输出 JSON 或从另一模型补一个概率。失败、不可用和超时的 `probability` 保持 `null`；有效的 0% 则仍为 0。

超时通过 provider 请求期限及阶段间总时间检查执行，不宣称具有硬实时进程终止能力。设置货币预算时，每阶段获得固定的一半预算；未知价格不能被视为零费用。费用沿用 provider 原币估算，不能当作账单；不同币种只提供分项，不换算求和。抛出异常而无法恢复回执时，完整费用及相关用量保持未知，保留可核对的部分费用。

## 可公开的输出

输出保持当前账本的原生字典结构：`status`、`probability`、精确目标、简洁理由、引用 ID、关键假设、未知项、原币费用、token、请求/输出/提示词哈希。成功运行额外提供确定性的组合 `returned_model`，并在 `agent_trace.stages` 中列出各组件实际返回的版本；组合标识不能被展示成模型厂商认证。

`agent_trace` 还包含工具轨迹、每阶段输入及审查哈希、预算、组件版本和请求回执。只导出白名单字段；不返回 API key、请求头、原始异常、原始模型响应或隐藏思维链。审查陈述和最终理由必须是简洁的可引用结论。

审查→预测流程是否优于单模型，需要新登记的可比前瞻任务与独立裁定结果。历史冻结记录保持不变。任何输出概率变化、拒答转预测或一次调用成功，都不能单独称为性能提升。

## 验证

`tests/test_arena_agents.py` 使用隔离夹具、注入函数及 `httpx.MockTransport`。测试覆盖两种模型组合、冻结契约漂移、证据与审查绑定、工具白名单、阶段预算、失败保留、非法概率/引用、混合币种、不泄漏私有字段，以及真实 provider 接口的两阶段联调。测试不会产生真实网络或付费模型请求。
