"""Complete a retrospective falsification cycle with a fresh, verified rerun."""
import argparse,json,hashlib,html,shutil
from pathlib import Path
from vbt.io import load,write,digest,now
from vbt.engine import verify_run
from vbt.registry import Registry
from vbt.forecast import score_forecasts
p=argparse.ArgumentParser();p.add_argument('--workspace',type=Path,required=True);p.add_argument('--rerun',required=True);a=p.parse_args()
w=a.workspace.resolve();parent='2026-09-25T085153-545299+0000-515a3584';runs=w/'artifacts/runs'
if not (runs/a.rerun).resolve().is_relative_to(runs):raise ValueError('Path escape')
r=Registry(w)
for rid in [parent,a.rerun]:
 path=runs/rid;v=verify_run(path)
 if not v['integrity_ok'] or not v['evidence_ok']:raise ValueError(v)
 anchor=r.db.execute('SELECT payload FROM runs WHERE run_id=?',(rid,)).fetchone()
 if not anchor or json.loads(anchor[0]).get('manifest_sha256')!=digest(path/'manifest.json'):raise ValueError('Run anchor mismatch')
 if load(path/'status.json')['execution']!='succeeded':raise ValueError('Run not successful')
results=load(runs/a.rerun/'work/results.json')
if results!=load(runs/parent/'work/results.json'):raise ValueError('Rerun differs: investigate before decision')
bygene={x['gene']:x for x in results}
if set(bygene)!={'OSM','OSMR'} or any(x['n_nonresponder']!=27 or x['n_responder']!=32 for x in results):raise ValueError('Unexpected cohort: this case review does not generalize')
folder=w/'artifacts/cycles'/('osmr-'+a.rerun);folder.mkdir(parents=True,exist_ok=False)
source={'url':'https://forpatients.roche.com/content/dam/patient-platform/lps/global/ga44839/LPS_GA44839_final-results_November2025_English.pdf','checked_on':'2026-09-25','document_date':'2025-11-05','trial':'NCT06137183','locator':'pages 1 and 5','phase':2,'remission':[{'arm':'q2w','events':3,'total':27},{'arm':'q4w','events':1,'total':26},{'arm':'placebo','events':3,'total':26}],'interpretation':'Sponsor reports no benefit on remission and early closure; does not refute every OSMR modality, population or indication.','role':'counterevidence, never a pre-result predictor','independent_human_adjudication':False}
cycle={'cycle_id':folder.name,'created_at':now(),'design':'retrospective falsification case, outcome already known; not preregistered or prospective','hypothesis':'Baseline OSM/OSMR response association alone is sufficient evidence to predict efficacy of OSMR blockade in UC.','analysis':{'parent_run':parent,'rerun':a.rerun,'identical_results':True,'results':results,'multiplicity':'BH across two planned genes in one cohort','unit':'59 baseline patients, 27 nonresponders and 32 responders','limitation':'GEO week-6 response label lacks independent protocol-level verification; no adjusted causal analysis or external validation'},'counterevidence':source,'decision':{'action':'terminate','scope':'Terminate the use of this association alone as a sufficient clinical-success predictor or investment signal.','retained_work':'Revise and retain OSM/OSMR as exploratory response biomarkers; require covariate adjustment and external validation.','reviewer_type':'automated_research_synthesis','human_signoff':None,'rationale':['Different intervention: golimumab observational response versus vixarelimab randomized trial.','OSMR median-difference CI includes zero; Mann-Whitney P tests a different distributional estimand.','Clinical counterexample defeats the sufficiency rule; does not prove absence of every target effect.'],'investment_action':'abstain; no probability or trade recommendation'},'forecast_readiness':score_forecasts([]),'complete_research_cycle':True,'clinical_prediction_validated':False,'investment_edge_validated':False}
write(folder/'cycle.json',cycle);write(folder/'counterevidence.json',source);write(folder/'results.json',results)
shutil.copyfile(__file__,folder/'complete_osmr_cycle.py')
rows=''.join('<tr>'+''.join('<td>'+html.escape(str(x[k]))+'</td>' for k in ['gene','median_difference','ci_low','ci_high','p_value','q_value'])+'</tr>' for x in results)
page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>研究循环 001</title><style>body{max-width:1000px;margin:40px auto;padding:24px;font:17px/1.8 system-ui;color:#173b3c}table{border-collapse:collapse;width:100%;font-size:14px}td,th{padding:9px;border-bottom:1px solid #ccc}.notice{background:#fff1df;padding:18px}a{color:#087d75}</style><h1>研究循环 001：终止一个过强的推断</h1><p class="notice">回顾性反证研究。已知临床结果，不作为事前预测成绩。研究综合判断由系统生成，未获独立人工签署。</p><h2>提出假设</h2><p>基线 OSM/OSMR 表达关联是否足以预测 OSMR 阻断在 UC 的临床成功？</p><h2>检查反证</h2><p>Roche vixarelimab UC 二期研究报告缓解人数为 3/27、1/26，与安慰剂 3/26 对照；报告称无获益并提前结束。不能推广为所有 OSMR 干预无效。</p><h2>重新分析</h2><p>59 名患者（27 无响应、32 响应）；固定原始计划及随机种子重跑，结果完全一致。GEO 第六周标签尚待协议级核对。中位数差为无响应减响应；CI 为 10,000 次 bootstrap；P 为双侧 Mann–Whitney，Q 为两个基因的 BH 校正。</p><table><tr><th>基因</th><th>中位数差</th><th>CI 下限</th><th>CI 上限</th><th>P</th><th>Q</th></tr>'''+rows+'''</table><p>OSMR 中位数差 CI 跨零，不能以秩检验 P 值替代中位数差的精度判断。</p><h2>终止与修订</h2><p><b>终止：</b>将该关联单独作为临床成功预测或买入信号。<b>保留并修订：</b>探索性响应标志物，后续需要混杂调整与独立队列。</p><h2>投资验证状态</h2><p>合格前瞻预测 0 条；二期／三期准确率、校准和投资收益均不可估计。没有交易授权。</p><p><a href="cycle.json">完整研究与决策记录</a> · <a href="counterevidence.json">反证来源定位</a> · <a href="manifest.json">文件校验清单</a></p></html>'''
(folder/'index.html').write_text(page)
write(folder/'manifest.json',{'cycle_id':folder.name,'files':{x.name:digest(x) for x in folder.iterdir() if x.is_file()}})
print(json.dumps({'cycle':str(folder),'decision':cycle['decision'],'rerun_identical':True},ensure_ascii=False,indent=2));r.close()
