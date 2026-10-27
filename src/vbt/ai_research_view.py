"""Project-bound AI drafts and actual tool receipts, under the shared CSP."""
import json
from urllib.parse import quote
from .ai_research import DOMAINS,TOOLS,proposals,packet

def render(app,store,p):
    from .program_view import e,field,form
    body='<section class="section-head" id="ai-research"><h2>AI 研究草案与工具</h2><span>依据冻结证据提出任务，执行权限由主机限定</span></section><p class="data-note">草案是待审阅建议。不自动调用外部模型；主机配置完成且记录人选择发送后，才单次提交所示冻结上下文。可继续导入会话或其他来源草案；录入模型标记不构成独立认证。</p><p><a class="button secondary" href="/program/research-packet?id='+quote(p['id'])+'">导出冻结研究上下文 JSON</a></p>'
    from .research_model_view import render as model_section
    body+=model_section(app,store,p,packet(store,p['id']))
    drafts=proposals(store,p['id'])
    for draft in reversed(drafts):
        proposal=draft['proposal']
        body+='<section class="card"><span class="badge">'+('项目内容已变化 · 草案过期' if draft['stale'] else '待科学审阅草案')+'</span><h3>'+e(proposal['summary'])+'</h3><p class="ref">'+e(draft['origin']['model_label'])+' · '+e(draft['created'])+'</p><div class="source-grid">'
        for risk in proposal['risks']:
            refs=risk['references']
            body+='<section class="card source-card"><h3>'+e(DOMAINS[risk['domain']])+'</h3><span class="badge">'+e({'supported':'支持线索','contested':'存在反证或限制','unknown':'尚不明确'}[risk['status']])+'</span><p>'+e(risk['reason'])+'</p>'
            if refs:body+='<details><summary>查看 '+str(len(refs))+' 项引用</summary><p class="ref">'+e(', '.join(refs))+'</p></details>'
            else:body+='<p class="data-note">未提供可用证据</p>'
            body+='</section>'
        body+='</div>'
        for task in proposal['tasks']:
            body+='<section class="card"><h3>'+e(task['question'])+'</h3><p>'+e(task['rationale'])+'</p><p><b>下一步</b> '+e(task['next_action'])+'</p><p><b>成功判据</b> '+e(task['success_criterion'])+'</p><p><b>停止判据</b> '+e(task['stop_criterion'])+'</p><p class="ref">引用 '+e(', '.join(task['references']))+'</p>'
            if task['tool'] in TOOLS and not draft['stale']:
                body+=form(app,p,'ai_run','运行有界工具')+'<input type="hidden" name="proposal_id" value="'+e(draft['event_id'])+'"><input type="hidden" name="task_id" value="'+e(task['id'])+'"><p>'+e(TOOLS[task['tool']])+' · 只读既有附件</p>'+field('author','执行记录人','text')+'<p><button>运行此只读工具</button></p></form>'
            elif task['tool']=='manual_research':body+='<p class="data-note">需要人工落实的研究任务；不会自动执行实验或发送资料请求。</p>'
            body+='</section>'
        body+='<p class="ref">依据内容哈希 '+e(proposal['packet_hash'])+'</p></section>'
    if not drafts:body+='<p class="data-note">尚无 AI 研究草案。不会把规则输出或示例伪装成模型推理。</p>'
    events=store.events(p['id']);receipts=[x for x in events if x['kind']=='ai_tool_receipt'];finished={x['payload']['request_id'] for x in receipts}
    for x in events:
        if x['kind']=='ai_tool_request' and x['id'] not in finished:body+='<p class="notice">有一项工具请求尚无完成回执，可能执行中或被中断；不计作成功。</p>'
    current_hash=packet(store,p['id'])['packet_hash']
    for event in reversed(receipts):
        r=event['payload'];status={'succeeded':'工具执行完成','blocked':'输入或版本受阻','stale':'上下文变化，结果不适用于当前项目'}[r['status']]
        historical='<p class="notice">此回执属于历史上下文，不直接用于当前项目判断。</p>' if r['packet_hash']!=current_hash else ''
        body+='<section class="card"><h3>'+e(TOOLS[r['tool']])+' · '+status+'</h3>'+historical+'<p class="data-note">待科学审阅。执行回执不代表草案成立、靶点有效或临床验证。</p><details><summary>查看输出、输入哈希与执行回执</summary><pre>'+e(json.dumps(r,ensure_ascii=False,indent=2))+'</pre></details></section>'
    body+='<details class="card"><summary>录入结构化 AI 研究草案</summary>'+form(app,p,'ai_import','绑定当前项目证据')+'<label for="ai-origin">草案来源（录入者声明）</label><select id="ai-origin" name="origin_kind"><option value="assistant_session">当前助手会话</option><option value="external_model">其他模型输出</option><option value="human_import">人工整理导入</option></select>'+field('model_label','模型或会话来源标记','text')+field('proposal_json','草案 JSON（包含上下文哈希、风险和带引用任务）',maximum=64000)+field('author','录入记录人','text')+'<p><button>保存为待审阅草案</button></p></form></details>'
    return body
