# AI 研究草案与只读工具

本轮解决 [Issue #4](https://github.com/james-lebron2000/EmerisBiotech/issues/4)：AI 建议需要绑定项目证据，成为可检查、可执行的有界任务。与此一起发布此前本地的 UC 项目、验证结果和独立 BD 工作流。

## 已实现的路径

项目内容 → 冻结研究上下文 → 带引用草案 → 主机校验 → 白名单只读工具 → 执行回执 → 待科学审阅。

`src/vbt/ai_research.py` 不调用模型、shell、任意脚本或外部 URL。只接受主机定义的数据契约与工具。`/programs` 提供上下文导出、草案录入、风险与任务展示、只读工具运行和回执查看。新增 `research_model.py` 在主机配置后提供单次显式模型服务调用，独立保存请求／回执，见 [模型调用配置](RESEARCH_MODEL_2026-10-27.md)。本机未配置，真实调用待验收。

上下文 schema v3（新增比较版本与本地审阅摘要，见 [UC 科学审阅](UC_SCIENTIFIC_REVIEW_2026-10-27.md)）保留项目定义、证据范围与反证、结果、参考资产来源摘要及导入绑定清单的版本，省略完整事件历史、署名与本机路径。导出到本地不会发送给模型服务；自由文本仍可能含私人信息，需要由使用者决定适合哪个模型或环境。

草案覆盖机制、患者分层、功能性药理、临床疗效、安全、可开发性/CMC、权利、差异化八个维度。所有非未知判断和所有任务必须引用本项目已有证据、结果或资产记录。引用校验只验证存在与版本，不认证语义正确性。草案来源明确记录为助手会话、其他模型或人工导入，模型标识是录入者声明而非独立认证。

## 实际工具

| 工具 | 输入 | 输出与边界 |
| --- | --- | --- |
| `audit_bound_sources` | 已绑定的摘要附件与临床参考来源 | 实际读取并验证源哈希；不评价科学正确性 |
| `extract_interaction_evidence` | 已绑定的 `interaction_results.csv` | 提取所有行的 n、OR、CI、P/q 和区间是否包含 1；不重算模型、不从非显著推断无交互 |
| `manual_research` | 草案中的资料或验证需求 | 仅显示任务，不提供自动执行权限 |

工具在读取前限制附件不超过 1MB。任务拒绝未知工具名、额外命令/参数、过期上下文和未知引用。请求先于实际执行保存；没有完成回执的请求不被推定成功。缺失、变化或格式错误的输入产生受阻回执。执行期间项目内容变化则不能把输出用于当前范围。

只读完成不更改证据审阅状态，不自动创建湿实验、预算、BD 决定或交易。模型文本和来源文本均不能授予执行权限。

## 本地命令

在仓库根目录、已安装项目的环境中：

```sh
uv run python scripts/ai_research.py --state-dir "$VBT_UC_STATE" \
  --program osmr-uc-independent packet --output /tmp/uc-research-context.json

uv run python scripts/ai_research.py --state-dir "$VBT_UC_STATE" \
  --program osmr-uc-independent import --proposal /path/to/proposal.json \
  --author "实际录入人" --origin assistant_session --model-label "如实填写会话或模型来源"

uv run python scripts/ai_research.py --state-dir "$VBT_UC_STATE" \
  --program osmr-uc-independent run --proposal-id PROPOSAL_EVENT_ID \
  --task TASK_ID --author "实际执行记录人"
```

上下文输出文件须为新文件。草案顶层字段为 `packet_hash`、`summary`、`risks`、`tasks`。风险含 `domain/status/reason/references`；任务含 `id/tool/question/rationale/next_action/success_criterion/stop_criterion/references`。契约与拒绝用例见 `tests/test_ai_research.py`。草案 JSON 限 64KB；网页表单以编码请求的独立大小上限接收。

## 本轮真实运行与当前限制

本次 Codex 会话基于冻结 UC 内容生成并录入一份待审阅研究草案，涵盖八类风险和五个任务。随后实际执行两项只读工具：核对三个已绑定来源并提取四行交互结果；运行回执保持待科学审阅。其余三项为人工落实的资料、功能验证准备及 BD 权利需求，不自动执行。

这是真实会话生成的草案，不是产品后台自动调用模型的运行。未发送研究资料到外部模型，未证明模型优于基准。真实 UC 项目没有被填入湿实验完成、预算落实、人工审批或投资收益。

产品内单次显式模型调用适配器已加入，实际提供方调用尚待配置与验收。目前仍缺任务调度与预算、跨任务学习、研究者审阅/采纳评估及前瞻独立比较。自动执行只覆盖上述只读工具，不能声称已形成自主研发 biotech。

## 与 Predome 的关系

远端现有 `apps/predome` 保留临床读出、预测与账本产品。该应用与 `src/vbt` 仍是独立环境、入口和数据库。本轮不改写 Predome、不迁移生产状态，也不把同仓管理说成两套数据已贯通。后续共享时点化证据与项目 ID 需单独设计迁移和验证。
