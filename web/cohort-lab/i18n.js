'use strict';
window.IS_ZH=document.documentElement.lang.startsWith('zh');
// Translate presentation text only; canonical data, exports and source URLs stay unchanged.
const TRANSLATIONS={
 'Patient inputs and model parameters':'患者输入与模型参数',
 'Every 8 weeks, review the index against the current-line nadir. An increase of at least 30% and 15 index units advances treatment from 1L to 2L or 2L to 3L.':'每 8 周比较当前指数与本线最低值；同时增加至少 30% 和 15 个指数单位时，治疗从一线转入二线，或从二线转入三线。',
 'Growth is derived from pre-treatment observations. Patient-specific coefficients drive backbone response and escape over time.':'由治疗前观察值推导增长率，以患者个体参数驱动治疗响应及其随时间的变化。',
 'Response is expressed as a modeled burden index with baseline 100.':'响应以模型负荷指数呈现，基线为 100。',
 'The same 120 synthetic virtual patients are simulated through two sequential treatment pathways over 72 weeks.':'使用同一批 120 位合成虚拟患者，模拟两条序贯治疗路径下的 72 周响应。',
 'Response model and review rule':'响应模型与评估规则',
 'Growth is derived from synthetic pre-treatment observations. Patient-specific backbone coefficients and escape rates determine weekly change. A treatment-line transition resets the escape clock.':'增长率由合成的治疗前观察值推导，患者个体的治疗作用系数与衰减速率共同决定每周变化，换线后重新计算作用衰减时间。',

 'Scope: fit adults, initially unresectable mCRC, MSS/pMMR, RAS mutant, BRAF V600E wild-type; assume no anti-VEGF contraindication.':'适用情景：适合治疗的成人，初始不可切除的转移性结直肠癌，MSS/pMMR、RAS 突变、BRAF V600E 野生型；假定不存在抗 VEGF 治疗禁忌证。',
 'Animated intermediate values use linear display interpolation between frozen weekly outputs.':'动画中间帧使用已固定周度输出之间的线性显示插值。',
 'JSON exports include exact canonical weekly outputs and checksum; the offline verifier reproduces the installed fixture. CSV exports contain all 120 patients × 2 pathways × 73 weekly timepoints. Line counts use the last completed week; intermediate index values are display interpolation. A–B gap sorting is applied only when requested, so tiles do not move during playback.':'JSON 导出包含完整的规范周度结果与校验值，离线校验器可独立复现当前版本。CSV 包含 120 位患者 × 2 条路径 × 73 个周度时间点。线次人数按最近完成的整周显示；中间帧指数为显示插值。差异排序仅在手动应用后生效，播放期间患者位置保持固定。',
 'BRAF V600E wild-type. All patient attributes are synthetic.':'BRAF V600E 野生型。所有患者属性均为合成数据。',
 '120 / 120 patients · A and B per patient':'120 / 120 位患者 · 每位患者均展示 A / B 路径',
 'Trifluridine/tipiracil + bevacizumab':'曲氟尿苷替匹嘧啶 + 贝伐珠单抗',
 'FOLFIRI + ziv-aflibercept':'FOLFIRI + 阿柏西普',
 'FOLFOX + bevacizumab':'FOLFOX + 贝伐珠单抗',
 'FOLFIRI + bevacizumab':'FOLFIRI + 贝伐珠单抗',
 'Source-backed regimen catalogue':'有临床来源的方案目录',
 'ASCO mCRC guideline (2022)':'ASCO 转移性结直肠癌指南（2022）',
 'ESMO mCRC guideline (2026)':'ESMO 转移性结直肠癌指南（2026）',
 'NCI Colon Cancer Treatment PDQ':'NCI 结肠癌治疗 PDQ',
 'FDA FTD/TPI + bevacizumab approval (2023)':'FDA 联合方案批准信息（2023）',
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
