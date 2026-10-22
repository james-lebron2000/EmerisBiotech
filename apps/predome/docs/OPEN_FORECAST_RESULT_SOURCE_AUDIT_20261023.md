# 首季结果来源复核，2026-10-23

本轮围绕已登记的12项正式目标检索申办方公告、官方研究站点和公开文件。以下是有范围限制的来源发现记录，不是临床结果裁定，没有修改预测、模型输入、真实结果标签或评分。检索未发现可确证的目标读出，不能证明全网尚无公告；注册库未提交结果同样不能证明申办方未披露。

## 需要排除的相近公告

| 正式目标 | 本轮看到的公开资料 | 排除或保留原因 |
| --- | --- | --- |
| MEVPRO-1 / NCT06551324 | Pfizer的ESMO 2026安排列出10月23日的mevrometostat单药海报；MEVPRO站点显示MEVPRO-1招募结束 | 单药海报不对应已登记的mevrometostat加enzalutamide的III期比较；招募结束也不是主要终点结果。不能把第三方的会议日期当作该III期读出日期。 |
| TOPAZ-1 / NCT04895241 | Biogen于10月2日披露AMETHYST的II期52周CLE数据 | 研究、人群与目标SLE试验不同，不能据此裁定TOPAZ-1。 |
| EMBER-4 / NCT05514054 | Lilly于9月18日公布基于EMBER-3的晚期/转移性乳腺癌联合用药批准 | EMBER-4研究早期乳腺癌辅助治疗，同文仍预期2027年初步结果；批准不是该目标结果。 |
| VITALIZE / NCT06290141 | Sanofi于6月10日披露MOBILIZE / NCT06290128停止 | MOBILIZE为标准治疗难治人群，VITALIZE为IVIg治疗人群；同文称将评估后续研究，不构成VITALIZE确切终点裁定。 |
| pionERA / NCT06065748 | Roche的3月persevERA公告和10月evERA公告 | 同药物的另两项试验，不能将其方向迁移到pionERA。 |

原文入口：

- [Pfizer ESMO 2026安排，2026-09-28](https://www.pfizer.com/news/press-release/press-release-detail/pfizer-advances-todays-cancer-care-and-tomorrows-potential)，mevrometostat海报2296P；[MEVPRO官方站点](https://www.pfizerclinicaltrials.com/mevproprostatecancerclinicaltrials)。
- [Biogen AMETHYST官方PDF，2026-10-02](https://investors.biogen.com/node/31031/pdf)，第1页说明II期/CLE；HTML入口本次返回403，PDF可读取。
- [Lilly批准公告及EMBER-4说明，2026-09-18](https://investor.lilly.com/news-releases/news-release-details/us-fda-approves-inluriyo-imlunestrant-combination-verzenio)。
- [Sanofi MOBILIZE公告，2026-06-10](https://www.sanofi.com/en/media-room/press-releases/2026/2026-06-10-05-00-00-3309444)。
- [Roche persevERA公告，2026-03-09](https://www.roche.com/media/releases/med-cor-2026-03-09)；[evERA公告，2026-10-01](https://www.roche.com/media/releases/med-cor-2026-10-01)。

## 其余目标和可用来源的范围

| 正式目标 | 来源与限制 |
| --- | --- |
| AVANZAR / NCT05687266 | [AZ管线](https://www.astrazeneca.com/our-therapy-areas/pipeline.html)列为研究项目；原始H2 2026指引保留。未在本轮取得可裁定其TROP2阳性、非鳞人群BICR PFS的官方读出。其他Datroway适应症批准不能替代。 |
| INAVO121 / NCT05646862 | [Roche Q3原始文件](https://assets.roche.com/f/176343/x/214d9ddfff/irp261022.pdf)已在冻结来源中保存；本轮网页工具无法读取该URL。原2026指引保留；不能由INAVO120批准或来源访问失败推定结果。 |
| SKYGLO / NCT06047080 | [Genentech官方研究页](https://www.genentech-medinfo.com/pipeline-and-clinical-trials/oncology/glofitamab/skyglo.html)对应确切NCT，注明信息截至10月20日，并列研究设计/PFS。研究页状态不能作为结果公告；原2027指引保留。 |
| SUCCESSOR-1 / NCT05519085 | [BMS Q1 2026演示材料](https://www.bms.com/assets/bms/us/en-us/pdf/investor-info/doc_presentations/2026/BMY-2026-Q1-Results-Investor-Presentation-with-Appendix.pdf)为已登记的指引来源。搜索返回的7月mezigdomide申报公告直接页本轮无法读取，不据搜索摘要裁定，也不将SUCCESSOR-2记录迁入本目标。 |
| CAMBRIA-1 / NCT05774951 | [AZ管线](https://www.astrazeneca.com/our-therapy-areas/pipeline.html)与已保存的年度管线文件为研究/时间指引；本轮未确证该目标的官方主要终点读出。其他camizestrant试验不替代本研究。 |
| OCEAN / NCT05263934 | [GSK官方研究页217102](https://www.gsk-studyregister.com/trials/217102)明确52周、depemokimab对mepolizumab、非劣设计。页面的Results summary导航或完成日期不能当作已公布分析；不将哮喘或鼻息肉研究结果迁入EGPA目标。 |
| eVOLVE-Cervical / NCT06079671 | [AZ管线](https://www.astrazeneca.com/our-therapy-areas/pipeline.html)和登记资料用于核对研究身份；本轮未取得符合已登记人群/终点的官方决定性读出。其他volrustomig研究不替代。 |

## 采集配置和后续裁定

本轮代码检查发现，先前正式队列中只有AVANZAR和OCEAN两项拥有官方采集配置，另外十项虽已有注册库资料，仍缺官方源配置。1.2.5从已核验的正式登记读取官方指引URL，并扩展精确域名及第一层新闻列表。配置完整不等于来源获取成功，更不等于结果完整覆盖；真实下载报告分别列出尝试、成功读取、延期队列和访问失败。

本次网页检索属于审阅者发现记录，未将网页工具文本当作已归档原始响应，未虚构文件SHA-256或下载收据。需要裁定的新公告必须通过现有采集器保存原始响应和获取清单，再走确切目标的独立审核流程。当前没有据此新增正式裁定，也没有据此补算命中率。
