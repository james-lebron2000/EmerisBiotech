"""Operator-configured model target, explicit send choice and request receipts."""
import json
from .research_model import configuration

STATUSES = {'ready': '主机模型配置可用', 'not_configured': '尚未配置模型地址与名称',
            'missing_credential': '主机缺少研究模型凭据', 'invalid_config': '主机模型配置不符合契约'}


def render(app, store, p, context):
    from .program_view import e, field, form
    _, config = configuration()
    body = '<section class="card"><h3>产品内模型调用</h3><p>' + STATUSES[config['status']] + '</p>'
    if config['status'] == 'ready':
        public = config['config']
        body += '<dl class="asset-facts"><dt>发送目标</dt><dd>' + e(public['endpoint']) + '</dd><dt>配置模型</dt><dd>' + e(public['configured_model']) + '</dd><dt>调用上限</dt><dd>' + str(public['timeout_seconds']) + ' 秒网络超时，最多 ' + str(public['max_output_tokens']) + ' 个输出 token；不自动重试</dd></dl>'
        body += '<p>将发送项目定义、' + str(len(context['evidence'])) + ' 条证据、' + str(len(context['results'])) + ' 条结果、' + str(len(context['assets'])) + ' 项参考资产，以及 ' + str(len(context['scientific_review']['comparisons'])) + ' 项对照与本地审阅摘要。自由文本可能含私人信息，请先核对具体内容。</p>'
        body += '<details><summary>查看本次将发送的完整冻结上下文</summary><pre>' + e(json.dumps(context, ensure_ascii=False, indent=2)) + '</pre></details>'
        body += form(app, p, 'ai_generate', '生成一份待审阅研究草案')
        for name, value in [('packet_hash', context['packet_hash']), ('config_hash', config['config_hash'])]:
            body += '<input type="hidden" name="' + name + '" value="' + e(value) + '">'
        body += '<label><input type="checkbox" name="send_context" value="yes" required>将上方冻结上下文发送至所示模型服务，单次调用可能产生费用</label>'
        body += field('author', '调用记录人（本地声明）', 'text') + '<p><button>发送并生成待审阅草案</button></p></form>'
        body += '<p class="data-note">成本按主机配置费率和提供方报告用量估算；未知用量／费率保持未知，实际账单另行核对。保存草案不执行其中任务。</p>'
    else:
        body += '<p class="data-note">模型生成不可用。管理员需在主机配置研究模型服务；页面不接收密钥或任意模型地址。</p>'
    body += '</section>'
    events = store.events(p['id'])
    receipts = [e for e in events if e['kind'] == 'ai_model_receipt']
    completed = {e['payload']['request_id'] for e in receipts}
    for event in events:
        if event['kind'] == 'ai_model_request' and event['id'] not in completed:
            body += '<p class="notice">有一项模型请求尚无完成回执，状态未知；没有生成成功草案，也不会自动重试。</p>'
    for event in reversed(receipts):
        r = event['payload']
        label = {'succeeded': '调用及草案校验完成 · 待科学审阅', 'failed': '模型调用或草案校验失败', 'stale': '上下文已变化 · 未接纳为当前草案'}[r['status']]
        body += '<section class="card"><h3>' + label + '</h3><p>配置模型：' + e(r['configured_model'] or '未配置') + '；提供方返回模型：' + e(r['returned_model'] or '未提供／未接受') + '</p>'
        body += '<p class="ref">请求 ' + e(r['request_id']) + ' · ' + e(event['created']) + '</p>'
        if r['packet_hash'] != context['packet_hash']:
            body += '<p class="data-note">此回执属于历史项目范围；调用与成本记录保留，不能直接用于当前判断。</p>'
        body += '<details><summary>查看调用、用量和成本回执</summary><pre>' + e(json.dumps({k: v for k, v in r.items() if k != 'draft'}, ensure_ascii=False, indent=2)) + '</pre></details></section>'
    return body
