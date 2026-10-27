# 患者治疗情景展示

2026-10-27。状态：内置合成病例的可运行展示，非真实患者预测服务。

## 打开与完整操作

研究首页及工作台导航增加“患者情景”，路由 `/patient-lab`。匿名患者编号为 **P001**；编号没有 DEMO 前缀，页面和导出仍明确标识合成来源。

按既有 README 启动工作台即可进入。也可独立启动同一工作台内的展示入口，不启动后台资料采集 worker：

```sh
uv sync --extra dev --frozen
uv run python scripts/serve_patient_lab.py \
  --state-dir "$HOME/Library/Application Support/VirtualBiotech/patient-lab-showcase" \
  --port 18766
```

打开 <http://127.0.0.1:18766/patient-lab>。这是本机单用户研究入口，不是公网或医院系统部署。进程不自动重启；Ctrl-C 停止，再用同一状态目录启动可读取之前的运行。

1. 查看宏观临床、展开微观生物与缺失资料。
2. 选择20%或45%的初始耐药假设，点击“运行并保存新推演”。生成三种虚构治疗的24周情景。
3. 切换A、B、A＋B及时间滑块，点击“更新视图”查看该时点；查看“疗效与预后”和“证据与缺口”。这些查看操作不修改已存结果。
4. 导出JSON，包含输入快照、参数、患者/输入/结果SHA-256、曲线、父运行及状态。源码公开不包含实际运行数据库。
5. 加载第6周合成随访，生成新版本；随后可加载第12周。原运行与未接受方案的预测均保留，不回填修改。
6. 重开旧运行或重启服务核对历史。最近30次运行可从页面进入，更早运行保留在本机数据库且可用已知ID访问。

## 模拟范围

`src/vbt/patient_lab.py` 是明确的演练引擎，与现有临床试验级Predome及 `tumor_model`/`disease_world_model` 分开，不导入它们的内部实现。

每条方案包含敏感群体抑制系数 k 和耐药群体增长系数 g。当前负荷 B、初始耐药比例 r、时间 t 下：

- 敏感群体为 `B * (1-r) * exp(-k*t)`；
- 耐药群体为 `B * r * exp(g*t)`；
- 负荷指数为两者之和；
- 绘制的范围为负荷乘以 `1 ± (0.04 + 0.012*t)`，只是人为参数跨度，不是置信区间或校准预测区间。

全部系数、单位和数学规则用于虚构情景。肝肺部位、年龄、癌种与组学字段仅用于演示资料组织，没有参与计算；当前引擎没有多病灶、真实PK/PD、真实药物、剂量、分子校准或个体因果效应能力。

“疗效与预后”中的固定曲线为 `100 * exp(-(month/scale)^1.28)`，三个scale为21、25、29个月。该数学示意与患者状态和病灶轨迹无推导关系，也不随耐药假设或随访而校准。真实影像疗效、PFS、OS、毒性及生活质量均为JSON null，页面明确显示不可估计，不给出最优方案或患者寿命。

## 合成随访

预设基线指数100、第6周82、第12周69。每个输入快照只包含截止时点已可见的观测；完整演练序列公开在合成fixture中，不作为盲法模型评价。演练中记录的实际治疗为虚构A。

新随访仅重置当前负荷、沿用原耐药比例，从新截止时点向后推演24周。它不是重新训练、后验校准或多组学状态识别。页面对照上一版A的6周情景与合成观测，只证明数据链路可运行，不计性能；B和联合方案没有对应的实际观测。

## 状态与边界

- 独立 `patient-lab.sqlite3` 位于工作台本机状态目录，不改变既有研究、市场或Predome数据库结构。
- 每次基线运行追加记录；随访记录唯一绑定父运行，同一父运行重复提交或并发提交返回同一个子运行。基线重复提交会创建独立运行，不声称所有请求恰好执行一次。
- 读取校验结果SHA-256；错误或缺失记录显式拒绝。哈希不是独立签名，不能认证作者或防止拥有数据库写权限的人同时修改数据与哈希。
- 新页面与POST沿用现有Host、Origin、CSRF、长度限制和严格CSP；没有JavaScript执行、第三方脚本、外部请求或付费模型调用。
- 请求只接受内置P001、synthetic来源和两种假设；额外患者、真实来源、剂量或任意模型参数均拒绝。页面不接受真实病例上传，也不自动开具治疗或执行临床动作。
- `execution=succeeded`、`scientific_review=not_performed`、`clinical_use=false` 分别保存，不以工程通过替代科学审核。

## 检查与视觉证据

```sh
uv run pytest -q tests/test_patient_lab.py tests/test_patient_lab_http.py \
  tests/test_workbench.py tests/test_ui_shell.py tests/test_dashboard_view.py
uv run pytest -q tests
```

真实HTTP浏览器检查使用临时工作区/SQLite、动态端口及实际生产HTML/CSP；没有API替身。测试自行启动、结束隔离服务。安装与CI相同的Playwright后，在仓库根目录运行：

```sh
npm install --no-save --package-lock=false playwright@1.56.1
npx playwright install --with-deps chromium
node tests/ui_patient_lab.cjs
```

`VBT_PYTHON` 可指定已安装项目的Python，默认 `.venv/bin/python`；`PLAYWRIGHT_MODULE` 与 `CHROMIUM_EXECUTABLE` 可指定本机已有浏览器工具，`PATIENT_LAB_QA_DIR` 可指定不纳入Git的截图/回执输出目录。CI在临时目录安装浏览器工具，不混入应用依赖锁。

本轮完整工作台回归 **1638 passed, 1 skipped**（106.90秒），跳过项为缺少被忽略的官方披露原始留档，未下载补齐。随后完善输入参数指纹和窄屏图形，相关复验 **42 passed**。最终源码另由PR CI验证，不将这些数字当作临床验证。

浏览器完整流程检查：建立保存运行、方案/时间切换、预后与证据范围、两次随访、重开和原记录不变、390px/320px布局；零页面错误、零外部请求。回执见[浏览器检查](images/patient-lab/browser.json)，截图见[桌面](images/patient-lab/desktop.png)与[窄屏](images/patient-lab/mobile.png)。

## 下一阶段

先选定癌种/亚型、治疗线及短期终点，以获准的纵向数据进行研究影子评价；分别落实实际影像测量、真实药物、分子数据、预后及风险模型。未完成适用范围与独立验证前，不把本展示解锁为真实个体治疗推荐。
