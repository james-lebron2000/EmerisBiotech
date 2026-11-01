"""A single admission and run control for bounded research tickets."""
from urllib.parse import quote
from .programs import Programs
from .research_mission import view
from .research_model import configuration

LABELS={'ready':'可运行或续跑','delivered_unreviewed':'报告已交付，待科学审阅','needs_input':'需要资料或前置条件','budget_exhausted':'预算已达边界','scope_changed':'来源范围变化，需重新授权','model_outcome_unknown':'模型结果尚未确认，需核查','unknown_model_outcome':'模型结果未知，未重放','cancelled':'已停止'}


def render(app,p):
    from .program_view import e,field,form
    store=Programs(app.state)
    body='<section class="section-head" id="research-missions"><h2>持续研究任务</h2><span>一次确定范围与预算，运行到报告或明确缺口</span></section><p class="data-note">既有证据核对运行固定主机流程；模型研究将工具观察送入下一轮协调／方法检查请求。两者都不代表独立人工科学审阅。调用次数、工具次数和执行窗口有上限；金额预算尚未实现。</p>'
    for event in reversed(p['events']):
        if event['kind']!='research_mission':continue
        s=view(store,p['id'],event['id']);m=s['mandate']
        body+='<article class="card"><span class="badge">'+e(LABELS.get(s['status'],s['status']))+'</span><h3>'+e(m['goal'])+'</h3><p>'+e(m['criteria'])+'</p><p>'+('固定核对流程，未调用模型' if m['mode']=='existing_evidence' else '模型研究：协调与方法检查使用分离请求')+'</p><p>模型请求 '+str(len(s['model_requests']))+'/'+str(m['limits']['max_model_calls'])+' · 工具请求 '+str(len(s['tool_requests']))+'/'+str(m['limits']['max_tool_attempts'])+' · 执行窗口 '+str(m['limits']['max_seconds'])+' 秒</p>'
        if s['stale']:body+='<p class="notice">本任务依据已变化，历史报告不能直接作为当前研究结论。</p>'
        if s['report']:
            report=s['report'];body+='<p>'+e(report['answer'])+'</p><ul>'+''.join('<li>'+e(f['claim'])+'<br><span class="ref">'+e(', '.join(f['references']))+'</span></li>' for f in report['findings'])+'</ul>'
            if report['missing']:body+='<p><b>仍缺什么</b></p><ul>'+''.join('<li>'+e(x)+'</li>' for x in report['missing'])+'</ul>'
        body+='<p><a href="/program/research-mission?id='+quote(p['id'])+'&amp;mission='+quote(event['id'])+'">导出任务、观察与报告 JSON</a></p>'
        if not s['report'] and not s['stopped']:
            body+=form(app,p,'mission_run','运行或续跑本任务')+'<input type="hidden" name="mission_id" value="'+e(event['id'])+'">'+field('author','操作记录人（本地声明）','text')+'<p><button>运行到报告或明确受阻</button></p></form>'
            body+=form(app,p,'mission_stop','停止后续研究')+'<input type="hidden" name="mission_id" value="'+e(event['id'])+'">'+field('reason','停止原因（至少 10 字符）')+field('author','操作记录人','text')+'<button>停止授权任务</button></form>'
        body+='</article>'
    _,config=configuration()
    body+='<details class="card"><summary>授权一个有界研究任务</summary>'+form(app,p,'mission_create','研究目标与交付条件')
    body+='<label>运行模式<select name="mode"><option value="existing_evidence">固定流程：既有来源核对与结果提取</option>'
    if config['status']=='ready':body+='<option value="model_research">模型研究：计划、观察和方法检查循环</option>'
    body+='</select></label>'
    if config['status']!='ready':body+='<p class="data-note">研究模型未就绪，模型模式不可用；固定核对不会生成替代模型回答。</p>'
    for key,label in [('trigger_id','触发编号：重复编号不会重复授权'),('goal','要回答的研究问题（至少 10 字符）'),('criteria','本次报告必须覆盖什么（至少 10 字符）')]:body+=field(key,label)
    body+=field('max_model_calls','模型请求上限：固定核对填 0；模型研究填 2–8','number','0')+field('max_tool_attempts','工具请求上限：1–16','number','4')+field('max_seconds','执行窗口：1–600 秒','number','120')
    if config['status']=='ready':
        body+='<p>模型目标 '+e(config['config']['endpoint'])+' · '+e(config['config']['configured_model'])+'</p><input type="hidden" name="config_hash" value="'+e(config['config_hash'])+'"><label><input type="checkbox" name="allow_model_send" value="yes">授权本任务在上述预算内，向所示模型发送项目上下文、工具观察和前轮结果；自由文本可能含私有资料。</label>'
    body+=field('author','授权记录人（本地声明，非身份认证）','text')+'<p><button>记录范围与预算</button></p></form></details>'
    return body
