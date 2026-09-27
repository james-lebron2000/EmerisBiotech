# 资产研究与决策工作台 MVP

此版本将已冻结的 27 条 MNC 资产/项目/事件记录与三个可检验假设接入现有可信研究平台。部署范围为本地单用户。资料截止日期为 2026-09-25；构建不联网刷新来源、不下载研究数据。网页只读，编辑计划并生成新快照通过 CLI 完成。

## 使用

在 `/Volumes/Lenovo/VirtualBiotech/platform` 执行：

```sh
./vbt portfolio build examples/portfolio_mvp.json --catalog docs/intelligence/2026-09-25-mnc/assets.json
./vbt portfolio status
./vbt portfolio verify SNAPSHOT_ID
./vbt portfolio serve SNAPSHOT_ID --port 18764
```

浏览器打开 http://127.0.0.1:18764 。页面支持搜索假设和资产、展开来源/反证/实验草案；所有 JSON 和代码副本均冻结在快照中。服务器只监听本机回环地址，Ctrl-C 停止；机器重启后重新执行 serve。无 API 密钥也能构建、验证、审查和恢复。

计划修改后用 `build ... --parent SNAPSHOT_ID` 生成新版本。原快照保持不变，同一假设不得静默删除或改标反证。快照 SHA-256 清单由本机 SQLite 锚定；检测意外修改，不声称具备对抗本机管理员的密码学身份保证。

## 实际人工审查

```sh
./vbt portfolio review SNAPSHOT_ID --hypothesis H_UC_OSMR --action request_revision --reviewer '实际审查人' --reason '填写对具体方法及证据的审查意见' --signature '实际本人签署说明'
```

动作可选 continue_research、request_revision、stop、authorize_experiment。最后一项在结构门槛未满足时拒绝。门槛满足仅允许记录人工自我声明，不自动证明科学有效性、伦理或合同有效性。静态网页不自动更新后续审查记录，使用 status 查询事件链。

## 三个研究入口

- H_UC_OSMR：检验各队列基线 OSM 关联及混杂因素，保留 OSMR 临床阴性证据。作为复现和反证基准；不得由表达关联直接提出药物疗效结论。
- H_AUTOIMMUNE_BCMA：区分致病抗体是否依赖 BCMA 阳性细胞。已有交易背景，缺独立科学证据与可用材料。
- H_ADC_NAPI2B：区分抗原表达、内化、载荷敏感性与正常组织毒性。药物形式和材料未落实，无自有候选分子。

每项均记录备择解释、统计单位、对照、主终点及停止条件草案。样本量依据、效果阈值、实验资源、权利、预算和负责人均需实际补齐。三项当前均不能获得实验授权。

## 备份与恢复

```sh
./vbt portfolio backup --output artifacts/portfolio/backup.json
```

保存返回的 SHA-256 和备份到独立本机位置。备份包含全部快照文件和审查事件。原始研究数据不包含其中。用空目录及独立 state-dir 恢复：

```sh
PYTHONPATH=src "$HOME/Library/Application Support/VirtualBiotech/venv/bin/python" -m vbt.cli --workspace /ABS/RECOVERY --state-dir /ABS/LOCAL_STATE portfolio restore /ABS/backup.json --sha256 RECORDED_HASH
```

恢复逐项检查路径白名单、包清单、哈希、事件链及授权合法性，全部通过后才注册。活动 SQLite 不可放在 /Volumes。构建中断的 staging 保留用于诊断；重新 build 生成新版本，不把未完成目录当成成功。

## 完成边界

这是可执行的研究记录、审查和决策 MVP。它与已有数据验证、沙箱分析、模型草拟接口共存。此版本采用确定性工作流；没有执行付费模型调用、模型质量评测、湿实验或临床研究。来源全文没有在本次构建中归档或重新核验。自动门槛检查不能替代独立科学审查；人名与权利引用属于本地录入声明。

下一步优先人工审查 OSMR 阴性证据及现有 UC 分析的样本/终点，锁定一份可反驳的研究计划。之后按可获取材料选择 BCMA 或 ADC 中的一项补科学证据和实验资源。不得同时把三份草案包装成三条已立项管线。
