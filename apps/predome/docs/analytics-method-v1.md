# Prediction Analytics v1

本模块不训练模型、不检索或裁定临床结果。`build_analytics` 只聚合已核验的归档数据或审核账本记录。

## 数据范围

默认前瞻范围为空：没有真实核验结果时，没有绩效曲线或准确率数字。历史回测读取 `dashboard.build_dashboard()` 的 8 项 v3 归档记录，DeepSeek 两模型各有 6 项回答、2 项拒答；不排除预训练记忆，不计入前瞻绩效。

前瞻输入来自 `PlatformStore.evaluation_rows(public_only=True)`，须已经经过审核与时间边界检查；本模块再次要求 `eligible_for_prospective=true`、`scope=prospective`、合法概率、二元结果、生成 ≤ 锁定 < 结果公布 ≤ 核验。只有最早符合条件的同一 trial/endpoint/model 锁定版本计分，后续版本不重复增加分母。缺少精确时间且不能证明早于结果公布的记录不计前瞻。

## 指标与区间

Brier = mean((p−y)²)，log loss 用 1e-15 边界防止无穷值。方向准确率采用固定阈值 p≥0.5。拒答不补零、不当失败。校准图使用预先固定的 5 个概率箱，边界归右箱，p=1 保留最后一箱；显示箱内平均概率、实际频率、样本数、已知资产组数与 Wilson 95% 二项区间。

Wilson 区间仅为描述性比例区间，未调整同资产不同试验的相关性。页面明确标注，不以此宣称临床验证或精准外部校准。分组少于20条可评分记录或5个已知资产组时隐藏分数；未知资产不作为独立资产计数。

## 时间和子组

累计记录先按真实结果公布日分组，不依赖同日试验排列。滚动窗口默认最近5条可评分记录，并包含边界日期的所有同日记录，因此实际滚动n可能大于请求n。历史曲线只是事后重排，不表明模型在过去日期已经生成预测。

支持分期、治疗领域、药物类型、研究设计、申办方类别、预测提前时间。原字段缺失保留未知；不凭药物名字或当前资料补造历史特征。

## 基线比较

只使用归档的 beta_pooled、beta_area、constant_0_5、design_logistic 预测值。基线和所选LLM比较限定在所有展示方法都有数值的共同样本交集，保持相同终点与归档信息截止。绝不用测试集标签重新拟合成功基础率。设计基线与LLM的证据内容不相同，比较不能证明因果技术优越性。前瞻记录若未同时归档基线，则显示缺少基线，不能事后补造。

## 接口

`build_analytics(scope='prospective', model='deepseek-v4-pro', rolling=5, prospective_records=None, dashboard=None)`。

返回 schema_version、summary、calibration_bins、distribution、cumulative、subgroups、baselines、records、excluded 与 provenance。返回结果为研究用途，不构成临床验证。UI `/analytics` 默认前瞻空态，允许切换历史回测，图表配备数据表、交互样本提示和 JSON 下载。
