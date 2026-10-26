# 开发与验证

本仓库包含两套独立 Python 应用。开始修改前读取根目录 [AGENTS.md](AGENTS.md)，再读取对应模块的约定。先确认代码分支、依赖环境与状态目录；本地数据库、封存证据和其他任务的未提交改动应保留。

## 从正确的代码开始

新工作以最新 `origin/main` 为基线，PR 指向 `main`；继续既有 PR 时，以该 PR 的最新提交为基线。先查看 `git status --short`，再决定是否需要独立检出。不要用较旧的本地工作副本整目录覆盖较新的分支。

分支使用简短描述，例如 `agent-workflow`、`forecast-recovery`。提交和 PR 标题采用 `type(scope): summary`，如 `fix(predome): preserve cutoff dates`；可用类型包括 `feat`、`fix`、`docs`、`chore`、`refactor` 和 `test`。既有分支和历史提交无需为命名规则重写。

| 修改范围 | 约定与环境 | 验证入口 |
| --- | --- | --- |
| 研究工作台 `src/vbt` | [模块约定](src/vbt/AGENTS.md)，根目录 `pyproject.toml` / `uv.lock` | 根目录 pytest；完整分析执行需 macOS |
| Predome `apps/predome` | [模块约定](apps/predome/AGENTS.md)，应用内独立虚拟环境和锁文件 | 应用内 `scripts/test_source.py` 与浏览器检查 |
| 共享 CI、部署边界 | [.github/workflows/ci.yml](.github/workflows/ci.yml) 与所属应用的部署文档 | 两套应用的检查；部署验收另行执行 |
| 纯文档 | 涉及的链接、命令、版本和来源 | `git diff --check`，核实变更链接与事实；无需为文字改动添加实现镜像测试 |

## 研究工作台

在仓库根目录，使用 Python 3.11：

```sh
uv sync --extra dev --frozen
uv run pytest -q tests/test_workbench.py tests/test_cloud.py
```

定向开发时，替换或补充为对应模块的测试文件。数据、分析、执行和存储变更应验证实际行为、失败路径及已有记录兼容性。完整工作台回归在 macOS 执行：

```sh
uv run pytest -q tests
```

这条命令只覆盖根目录 `tests`，不能据此声称 Predome 通过测试。其他平台可执行相关界面和配置检查，但不能把缺少 Seatbelt 执行环境记为分析测试通过。

## Predome 公开源码

从仓库根目录进入应用，在独立环境安装已有锁定依赖：

```sh
cd apps/predome
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock.txt -r requirements-research-v4.txt
.venv/bin/python scripts/test_source.py
```

脚本按 `ci-tests.txt` 运行不依赖真实证据的测试，并拒绝包含证据目录的检出。若本地已安装证据包，使用另一份干净检出，不删除现有资料。新增适合公开源码的行为回归时，同步检查该选择清单；完整证据测试仍依照 [Predome 说明](apps/predome/README.md)单独执行。

源码检查和浏览器检查使用合成输入、临时状态及外部边界替身，不需要生产数据库、模型密钥或真实模型调用。测试产品域逻辑与实际页面，不复制一套算法或组件作为测试对象。

与当前 CI 一致的浏览器检查，在 `apps/predome` 中使用 Node.js 22：

```sh
npm install --no-save --package-lock=false playwright@1.56.1
npx playwright install --with-deps chromium
node tests/ui_predict_context.cjs
node tests/ui_prospective_coverage.cjs
```

这些检查读取实际静态页面并拦截请求。界面变更还应覆盖受影响的宽／窄屏、输入、错误、未知和异步响应状态；使用该应用已有的共享样式和状态语义。浏览器截图或检查记录是界面证据，不等于科学或临床验证。

## 数据和状态边界

外部输入在 API、导入或存储边界验证，内部使用所属模型；不要为同一字段创建多套互不一致的规则。旧记录如未经明确契约验证，应保留未知或格式错误状态。队列接收、模型执行完成、记录完整和科学审核分别记录；恢复进程不能把未知结果自动标记为成功。

活动 SQLite 放在本机状态目录，测试使用临时目录。保留原连接管理方式，按现有事务边界处理持久化。封存输入、运行记录和证据哈希不因重构改写；新分析创建新运行。运行源码、证据安装、发布切换和生产验收是独立步骤。

不把付费模型调用、研究数据下载或生产写入夹带进测试。缺少证据、平台能力或访问权限时，记录具体未验证范围，不用默认值补成通过。

## 交付前

运行 `git diff --check`，确认提交只包含本次改动，排除数据库、密钥、原件和测试实际输出。PR 写清触发问题、修改后的行为、实际完成的检查和仍未验证的范围。涉及 UI 行为时附相应截图或浏览器检查记录；无需为每个 PR 制作装饰性动画。

GitHub CI 分别运行 macOS 工作台回归、Linux Predome 公开源码及隔离浏览器检查。完整命令以 [.github/workflows/ci.yml](.github/workflows/ci.yml) 为准；本仓库没有统一的 Bun 检查入口，也未配置独立的全仓 lint／类型检查命令。

源码推送、CI 通过、发布上线、人工科学审核和临床验证分别报告。依赖或部署变化须遵守所属应用已有的锁文件和发布流程。

## 参考与适配

本约定参考 [OpenCode AGENTS.md 的固定版本](https://github.com/anomalyco/opencode/blob/ed74ec82ce1fe3a36da6c0815a935473ef4bb0fb/AGENTS.md)，借鉴模块边界、边界验证、克制的抽象、真实实现测试和明确检查入口。源文件 blob SHA 为 `0971dbb1c03fa9abbf59d2dfc2a004bcfa66b0a8`。

Bun、TypeScript、TUI、生成 SDK、Effect 和 Session 内核规则属于 OpenCode 的实现，本仓库采用现有 Python／JavaScript 和两套应用的工作方式。这里没有引入这些依赖，也没有把它的会话所有权或重试保证当作本项目已有能力。
