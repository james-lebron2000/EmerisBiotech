'use strict';
window.IS_ZH=document.documentElement.lang.startsWith('zh');
// Translate presentation text only; canonical data, exports and source URLs stay unchanged.
const TRANSLATIONS={
 'The same 120 authored patients are simulated under both sequential pathways. No randomization or causal treatment effect is estimated.':'同一批 120 位合成患者分别接受两条序贯路径的模拟。未进行临床随机分组，也不估计因果治疗效应。',
 'Illustrative burden index, baseline 100. Not lesion measurements, RECIST, response probability, PFS or OS.':'展示指标为示意性负荷指数，基线为 100；不是病灶实测值、RECIST、响应概率、无进展生存期或总生存期。',
 'At an 8-week review, switch 1L to 2L or 2L to 3L when index rises at least 30% and 15 index units above the current-line nadir. This toy rule is not RECIST. No further line decision is modeled after 3L.':'每 8 周进行一次情景评估：指数较当前治疗线次的最低值同时升高至少 30% 和 15 个指数单位时，由一线转入二线或由二线转入三线。这是示意性规则，并非 RECIST；三线之后不再模拟进一步的换线决策。',
 'Authored synthetic inputs; no clinical or assay calibration.':'人工构建的合成输入，未经过临床或实验测量校准。',
 'Regimen names and sequencing have clinical references; every trajectory and effect coefficient is fictional and uncalibrated.':'方案名称与选定序贯顺序具有临床来源；每条响应轨迹和作用系数均为未校准的虚构情景。',
 'Scope: fit adults, initially unresectable mCRC, MSS/pMMR, RAS mutant, BRAF V600E wild-type; assume no anti-VEGF contraindication.':'适用情景：适合治疗的成人，初始不可切除的转移性结直肠癌，MSS/pMMR、RAS 突变、BRAF V600E 野生型；假定不存在抗 VEGF 治疗禁忌证。',
 'Age and ECOG describe the fixture; they are not fitted treatment modifiers. All effect coefficients are visible in patient details.':'年龄与 ECOG 仅描述合成病例，并非经拟合的治疗影响因素；所有作用系数均可在患者详情中查看。',
 'No dosing, PK/PD bridge, maintenance, surgery, treatment interruption, adverse-event model or mortality model.':'未建立剂量、药代与药效桥接、维持治疗、手术、治疗中断、不良反应或死亡模型。',
 'Later-line options and real-world eligibility require individual clinical review. This catalogue is not exhaustive.':'后线治疗选项与真实患者的适用性需要个体化临床审核；此目录并不穷尽全部治疗方案。',
 'Animated intermediate values use linear display interpolation between frozen weekly outputs.':'动画中间帧使用已固定周度输出之间的线性显示插值。',
 'Growth is derived from authored pre-treatment indices. Drug-backbone coefficients and escape rates are authored inputs. The anti-VEGF components are not separately calibrated. Switching resets the modeled escape clock; this is an illustrative assumption. No dose or drug efficacy is inferred from the earlier cell-assay module.':'指数增长率由合成的治疗前观察值推导；化疗骨架作用系数与衰减速率为人工构建输入。抗 VEGF 成分未单独校准；换线时重置作用衰减计时，属于示意性假设。未从早期细胞测量模块推导人体剂量或药物疗效。',
 'JSON exports include exact canonical weekly outputs and checksum; the offline verifier reproduces the installed fixture. CSV exports contain all 120 patients × 2 pathways × 73 weekly timepoints. Line counts use the last completed week; intermediate index values are display interpolation. A–B gap sorting is applied only when requested, so tiles do not move during playback.':'JSON 导出包含完整的规范周度结果与校验值，离线校验器可独立复现当前版本。CSV 包含 120 位患者 × 2 条路径 × 73 个周度时间点。线次人数按最近完成的整周显示；中间帧指数为显示插值。差异排序仅在手动应用后生效，播放期间患者位置保持固定。',
 'These are selected clinical options, not an exhaustive treatment guideline. Local approval, eligibility and clinician review still apply.':'这些是选定的临床方案选项，并非完整治疗指南；实际使用仍需考虑当地批准范围、适用条件及临床审核。',
 'BRAF V600E wild-type. All patient attributes are synthetic.':'BRAF V600E 野生型。所有患者属性均为合成数据。',
 '120 / 120 patients · A and B per patient':'120 / 120 位患者 · 每位患者均展示 A / B 路径',
 'Trifluridine/tipiracil + bevacizumab':'曲氟尿苷替匹嘧啶 + 贝伐珠单抗',
 'FOLFIRI + ziv-aflibercept':'FOLFIRI + 阿柏西普',
 'FOLFOX + bevacizumab':'FOLFOX + 贝伐珠单抗',
 'FOLFIRI + bevacizumab':'FOLFIRI + 贝伐珠单抗',
 'Source-backed regimen catalogue':'有临床来源的方案目录',
 'Fictional model and review rule':'虚构情景模型与评估规则',
 'Scenario inputs and limits':'情景输入与适用边界',
 'ASCO mCRC guideline (2022)':'ASCO 转移性结直肠癌指南（2022）',
 'ESMO mCRC guideline (2026)':'ESMO 转移性结直肠癌指南（2026）',
 'NCI Colon Cancer Treatment PDQ':'NCI 结肠癌治疗 PDQ',
 'FDA FTD/TPI + bevacizumab approval (2023)':'FDA 联合方案批准信息（2023）',
 'No manual resistance selection is used.':'不使用人工选择的耐药程度。',
 'Oxaliplatin first':'奥沙利铂起始',
 'Irinotecan first':'伊立替康起始',
 'under pathways A and B':'在 A / B 路径下的模拟响应',
 'illustrative burden through week':'示意性负荷指数，截至周次',
 'Unable to load treatment cohort.':'无法加载治疗情景队列。',
 'Treatment cohort integrity check failed.':'治疗队列完整性校验失败。',
 'Unexpected treatment cohort.':'治疗队列格式不符合预期。',
 'Use browser full screen':'请使用浏览器全屏功能',
 'Sources checked ':'来源核对日期：',
 'from baseline':'相对基线',
 'Pathway ':'路径 ',
 'pathway ':'路径 ',
 'Week ':'周次 ',
 'Age ':'年龄 ',
 'RAS mutant':'RAS 突变',
 'Regimen':'治疗方案',
 'Components':'组成药物',
 'References':'参考来源',
 'Inspect ':'查看 ',
 'peritoneum':'腹膜',
 'liver':'肝脏',
 'lung':'肺部',
 'Unable to load cohort overview.':'无法加载总体队列响应。',
 'Cohort overview integrity check failed.':'总体队列完整性校验失败。',
 'Cohort overview mismatch.':'总体队列与患者数据不匹配。',
 'Pause':'暂停',
 'Play':'播放',
 '1L':'1线','2L':'2线','3L':'3线'
};
const translationEntries=Object.entries(TRANSLATIONS).sort((a,b)=>b[0].length-a[0].length);
function translateText(value){let text=String(value);for(const [en,zh] of translationEntries)text=text.split(en).join(zh);return text;}
function tr(value){
 if(!window.IS_ZH)return value;
 const text=String(value);if(!text.includes('<'))return translateText(text);
 const template=document.createElement('template');template.innerHTML=text;
 const walker=document.createTreeWalker(template.content,NodeFilter.SHOW_TEXT);let node;
 while((node=walker.nextNode()))if(!node.parentElement?.closest('pre'))node.nodeValue=translateText(node.nodeValue);
 for(const element of template.content.querySelectorAll('[aria-label],[title]'))for(const name of ['aria-label','title'])if(element.hasAttribute(name))element.setAttribute(name,translateText(element.getAttribute(name)));
 return template.innerHTML;
}
function drugLabel(name){return window.IS_ZH?({'fluorouracil':'氟尿嘧啶','leucovorin':'亚叶酸','oxaliplatin':'奥沙利铂','irinotecan':'伊立替康','bevacizumab':'贝伐珠单抗','ziv-aflibercept':'阿柏西普','trifluridine':'曲氟尿苷','tipiracil':'替匹嘧啶'}[name]||name):name;}
