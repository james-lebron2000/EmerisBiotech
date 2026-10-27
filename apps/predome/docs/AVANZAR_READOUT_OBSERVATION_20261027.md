# AVANZAR 官方读出日程观察 · 2026-10-27

状态为 **`pending_source_acquisition_and_independent_review`**。这是待取得原件、待独立审核的日程候选资料，不是已批准的日历更新、真实结果或新预测。线上日历和冻结记录均未改变。

机器可读资料见 [readout-observation-20261027-avanzar.json](records/readout-observation-20261027-avanzar.json)。记录组装时间为 2026-10-26 21:42 UTC，对应 Asia/Taipei 2026-10-27；这个时间不冒充原 PDF 的获取时间或独立时间凭证。

## 核对的精确目标

AVANZAR，[NCT05687266](https://clinicaltrials.gov/study/NCT05687266)，本项目 target ID 为 `of-season-20261023-corrected-avanzar`。冻结问题是此前未治疗、无可靶向基因改变的晚期非鳞 NSCLC 中，TROP2 阳性非鳞分析人群的 BICR PFS：Dato-DXd＋durvalumab＋carboplatin 对照 pembrolizumab＋pemetrexed＋铂类化疗。

其他人群的 PFS、OS、其他 Dato 试验或药物获批不能代替这个终点。新日程材料属于试验级指引，尚未分别确认每个主要假设的公布时间。

## 新官方证据与保守候选窗口

阿斯利康[官方结果与演示资料列表](https://www.astrazeneca.com/investor-relations/results-and-presentations.html)列出 **2026-10-26 ESMO Investor Event**。该活动另有[官方页面](https://www.astrazeneca.com/investor-relations/meet-the-management-event-at-esmo-2026.html)。

活动的[官方 presentation PDF](https://www.astrazeneca.com/content/dam/az/Investor_Relations/events/AZN-2026-ESMO-presentation.pdf)封面日期为 2026-10-26。PDF 第 **50 页（从 1 开始计页）／印刷 slide 51** 的 2027 读出章节列出 AVANZAR 及一线 NSQ／NSQ TROP2 阳性 NSCLC。通过本次官方 PDF 文本观察，可以形成 **2027 年**的日程候选。

| 字段 | 当前候选值 |
|---|---|
| Start / End | 2027-01-01 / 2027-12-31 |
| Precision | `year` |
| Guidance as of | 2026-10-26 |
| Source granularity | `named_trial` |
| Approval | `pending` |
| 可用于更新公开日历 | 否 |

原材料文本层出现 H1／H2 栏位，但本次没有核验原页版面中它们与 AVANZAR 的对应关系，因此不填半年或季度。该窗口也不代表确切公告日。候选数据不采用第三方季度推断。

## 原件获取和审核限制

本次通过 web reader 读取了官网列表和官方 PDF 文本。直接 HTTPS 获取该 PDF 返回 **HTTP 403**；web PDF 截图工具不可用。没有绕过访问限制，也没有把文本读取得到的内容冒充原 PDF 文件。

- `original_pdf_captured=false`。
- `source_sha256=null`，因为未取得可计算原件哈希的字节。
- `screenshot_verified=false`，`half_year_layout_verified=false`。
- 独立审核完成数为 0。

因此目前不能声称完成了带原件哈希的证据入库或日程审批。版本化保存本说明，只让后续研究者看见发现了什么、尚缺什么。

## 原记录如何保留

[冻结 season spec](../arena_data/season_corrected_spec.json)保留 **H2 2026** 和当时的输入版本；其[原官方来源](https://www.astrazeneca.com/content/dam/az/PDF/2026/h1q2/H1-and-Q2-2026-results-clinical-trials-appendix.pdf)日期为 2026-07-27。不得为使页面显得更新而修改旧输入、旧预测、模型失败状态、锁定时间或账本。

后续应先合法取得新官方原 PDF，记录真实获取时间、原始字节及 SHA-256，再核对对应页版面并独立审核精确试验与时间范围。通过现有审批流程建立单独的新日历版本，同时保留旧窗口与原预测输入。本观察资料本身不能授权或自动执行这些更新。

读出延迟和事件积累速度不能被标为本目标疗效失败；当前 `outcome_label=null`，没有裁定或计分。

## 其他四项的有限检查

以下是本次有界官方来源检查，不是对所有公告渠道的穷尽检索。没有确立新结果，不等于不存在公告。

| 精确研究与终点 | 官方来源及观察边界 |
|---|---|
| INAVO121／NCT05646862，主随机研究的 BICR PFS，inavolisib＋fulvestrant 对照 alpelisib＋fulvestrant | [2026-10-22 Roche deck](https://assets.roche.com/f/176343/x/214d9ddfff/irp261022.pdf)仍将 INAVO121 列于 2026 临床结果展望；INAVO120 独立分栏。没有据此产生新窗口或结果标签。 |
| MEVPRO-1／NCT06551324，BICR rPFS，mevrometostat＋enzalutamide 对照医生选择治疗 | [Pfizer 2026 catalyst 页](https://www.pfizer.com/pfizer-pipeline-key-anticipated-2026-catalysts)列出 MEVPRO-1；[招募页](https://www.pfizerclinicaltrials.com/mevproprostatecancerclinicaltrials)写招募完成。页面未标明可确认的文档发布日期；招募状态不能作为结果或公布日。 |
| TOPAZ-1／NCT04895241，SLE 高剂量 litifilimab 对照 placebo 的 Week-52 SRI-4 | [Biogen IR 页](https://investors.biogen.com/)和[财报活动页](https://investors.biogen.com/events/event-details/q3-2026-earnings-call-webcast)列出 2026-10-28 公司沟通活动。它不是试验读出承诺日期；AMETHYST、TOPAZ-2 不能代替本目标。 |
| OCEAN／NCT05263934，Week 36 及 Week 52 缓解的非劣效目标，depemokimab 对照 mepolizumab | [2026-07-28 GSK Q2 公告](https://www.gsk.com/media/jayh41pz/q2-2026-results-announcement.pdf)区分 H2 2026 数据读出与 2027 注册申报／决定，不能把注册时间当读出。没有据此产生新窗口或结果标签。 |

本资料的模型调用、冻结 spec 修改、预测修改、账本写入、生产日历更新和结果裁定均为 **0**。工程记录及日程观察不构成临床预测验证。
