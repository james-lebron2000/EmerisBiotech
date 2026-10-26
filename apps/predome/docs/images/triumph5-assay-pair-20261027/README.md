# TRIUMPH-5 assays-v2 本地真实记录验收截图

这些截图来自 **loopback 本地候选应用与真实封存研究记录**，地址为 `http://127.0.0.1:51161`，公共数据合同为 `arena-clinical-public-1.3`。**未部署到生产，未获临床预测验证，人工科学审核 0。** 每个文件以 `local-real-records` 命名；不得把脱离本说明的裁图作为线上产品或临床效能证明。

实际 HTTP 页面和 API 响应没有 fixture 替换，使用原始 CSP 与实际 shipped 静态资产，没有注入样式或改写预测。截图由 `verification/patent-assay-pair-20261027/http-acceptance-v2/capture.cjs` 在主 agent 将 v2 sealed bundle 导入隔离 runtime-copy 后取得。全部九张图片已读屏检查。

## 真实结果与展示范围

四个 first-attempt 槽完整保留：Kimi clinical、Kimi patent 均为 invalid_json 失败；Flash clinical 为 provider_http_error 失败；Flash patent 有原始未校准研究值 0.71。

公开页按现有合同只展示两个 clinical-only 控制槽，均为 No usable response / Uncertain，校准成功概率与 confidence 均为 N/A。两个技术失败没有返回可用判断，因此 **Model agreement 为 N/A，Usable judgments 为 0 / 2 assigned**，不显示一致性进度条。分配数、方向计数与多数方向仍包含全部分配槽；一致性仅使用实际返回判断（含明确弃权），至少两份判断才计算。相同的技术失败不能解释为模型共识。

内部页显示全部四槽，唯一的 71% 是 Flash patent 原始未校准研究判断，不能解释为校准的临床成功概率。实际临床结果仍 pending，不计分、不标命中、不排名。v1 与 v2 是同一 retatrutide 项目的两份 research pilot records，页面使用真实记录时间区分，不代表两个独立药物项目。

精确配对表只有 Kimi 一行，两臂失败所以变化不可比较。Flash control 没有 returned model，未满足现有精确配对身份规则，不生成可核验配对行；其两次实际 attempt 仍在四槽列表中。没有补造第二行或用旧 control 替代。

## 截图索引

- [公开页 1440px](local-real-records-public-desktop.png)
- [公开页 375px 顶部](local-real-records-public-mobile.png)
- [公开页 375px 模型结果](local-real-records-public-predictions-mobile.png)
- [公开页 375px 共识与待揭晓结果](local-real-records-public-result-mobile.png)
- [内部四槽 1440px](local-real-records-lab-forecasts-desktop.png)
- [内部四槽 375px](local-real-records-lab-forecasts-mobile.png)
- [内部配对表 1440px](local-real-records-lab-pair-desktop.png)
- [内部配对表 375px 左侧](local-real-records-lab-pair-mobile-left.png)
- [内部配对表 375px 右侧](local-real-records-lab-pair-mobile-right.png)

375px 内部六列表格按现有产品行为局部横向滚动，因此分别保存左右侧；不声称六列在手机一屏同时可读。公开手机顶部截图没有覆盖模型模块，另附完整模型和结果区域截图。桌面公开页允许极少滚动，保留判断分母与来源文字的可读性。

## 核验边界

[provenance.json](provenance.json) 记录实际截图 SHA、四个原件下载 SHA、两份原始时间凭证下载、served 资产 bytes、console/page 错误及不变性结果。旧 144 条公开 run DTO 完全一致；47 个旧目标除有意修正的 consensus 派生块外，临床事实、来源、时间、原始预测和结果保持一致；旧 331 个 host events 前缀完全一致。合同版本与派生共识字段的修正不改写原始预测或账本。HTTP 验收没有追加账本事件。

当前 host 为 332 事件，新增导入事件来自主 agent。source 17 事件中，registration TSA 覆盖前 6、prediction TSA 覆盖前 15；后 2 个源事件不在原 prediction TSA 覆盖内，不把 AI 选定审核说成已由原预测凭证覆盖。HTTP 验收确认凭证可以下载，没有替代主 agent 的签名验证。

本轮截图请求均为 GET；浏览器 console errors、page errors、unexpected requests 及 server 外连均为 0。先前只通过正常 register/login 接口创建合成本地 QA 会话，session 仅存在 ignored 目录的 0600 文件中，没有进入本目录。曾暴露 100% 假共识的旧 1.2 截图和 receipt 已保留在 ignored 的 `before-agreement-fix` 历史目录，本目录仅保留修复后截图。
