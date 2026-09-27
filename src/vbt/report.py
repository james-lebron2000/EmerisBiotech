import html, json
from pathlib import Path
from .io import load

def render(run_dir):
    r=Path(run_dir);s=load(r/'status.json');plan=load(r/'inputs'/'plan.json')
    claims=load(r/'claims.json');review=load(r/'review.json')
    esc=html.escape
    def panel(title, obj): return '<section><h2>'+esc(title)+'</h2><pre>'+esc(json.dumps(obj,ensure_ascii=False,indent=2))+'</pre></section>'
    artifacts=[]
    for p in sorted(r.rglob('*')):
        if p.is_file() and not p.is_symlink() and p.name not in ('audit.html','manifest.json'):
            rel=str(p.relative_to(r));artifacts.append(f'<li><a href="{esc(rel,quote=True)}">{esc(rel)}</a></li>')
    rows=''.join(f'<tr><td>{esc(c["claim_id"])}</td><td>{esc(c["direction"])}</td><td>{esc(c["text"])}</td><td><a href="{esc(c["artifact"],quote=True)}">{esc(c["row_id"])}</a></td></tr>' for c in claims)
    body=f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Virtual Biotech audit</title>
<style>body{{font:16px/1.6 system-ui,sans-serif;background:#f4f6f9;color:#162638;max-width:1120px;margin:36px auto;padding:0 24px}}header,section{{background:white;padding:24px;border-radius:12px;margin:18px 0}}h1{{font-size:30px}}h2{{font-size:20px}}.badge{{display:inline-block;padding:8px 12px;background:#edf2fa;margin:4px;border-radius:7px}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px}}table{{border-collapse:collapse;width:100%}}td,th{{padding:10px;text-align:left;border-bottom:1px solid #ddd}}a{{color:#205db0}}.notice{{border-left:5px solid #da8a22;padding:12px;background:#fff5e7}}</style>
<header><small>VIRTUAL BIOTECH · RESEARCH AUDIT</small><h1>{esc(plan['question'])}</h1><p>{esc(s['run_id'])}</p><div class="badge">执行: {esc(s['execution'])}</div><div class="badge">记录: {esc(s['record_integrity'])}</div><div class="badge">科学审查: {esc(s['scientific_review'])}</div><p class="notice">证据类型：{esc(plan['evidence_kind'])}。工程运行和记录完整性不代表科学结论已验证。临床发布：关闭。人工签署：未完成。</p></header>
<section><h2>结论与证据定位</h2><table><tr><th>编号</th><th>方向</th><th>主张</th><th>结果行</th></tr>{rows}</table>{'<p>没有可支持的科学主张；查看缺口与执行记录。</p>' if not claims else ''}</section>'''
    body+=panel('冻结的研究计划',plan)+panel('独立审查记录',review)
    if (r/'review_checklist.json').exists():body+=panel('方法与证据审查清单',load(r/'review_checklist.json'))
    if plan['workflow']=='geo_uc' and (r/'work'/'data_gaps.json').exists():body+=panel('待补数据与注释',load(r/'work'/'data_gaps.json'))
    if plan['workflow']=='geo_uc' and (r/'work'/'sample_flow.json').exists():body+=panel('样本流转',load(r/'work'/'sample_flow.json'))
    body+=panel('执行信息',s)+panel('输入数据版本',load(r/'inputs'/'datasets.json'))
    body+='<section><h2>可检查文件</h2><ul>'+''.join(artifacts)+'</ul></section><footer>运行 verify 重新核对磁盘内容；本页是封存时快照。后续人工审查记录保存在独立 reviews 目录。</footer></html>'
    (r/'audit.html').write_text(body)
