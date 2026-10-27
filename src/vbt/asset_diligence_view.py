"""Server-rendered asset claim, conflict and missing-material workflow."""
from urllib.parse import quote

from .asset_diligence import DIRECTIONS, JUDGMENTS, TYPES, matrix
from .programs import Programs


def select(name, label, choices):
    from .program_view import e
    return '<label>' + e(label) + '<select name="' + name + '" required><option value="" disabled selected>请选择</option>' + ''.join('<option value="' + e(k) + '">' + e(v) + '</option>' for k, v in choices) + '</select></label>'


def render_matrix(app, p):
    from .program_view import e, field, form
    state = matrix(Programs(app.state), p['id'])
    body = '<section class="card" id="asset-matrix"><h3>资产资料与缺失矩阵</h3><p>按分子、地区、用途、权利类型和覆盖时间记录。所有来源并存；未知截止日期不表示当前有效。支持、反证与未知表示资料对研究或资产假说的方向，来源内容另行审阅。</p><p class="data-note">本地文件哈希核对与外部网页内容核实分别记录。此处不重新访问外部网页，不认证所有权、许可可得性或法律有效性。</p><div class="page-actions">'
    for fmt in ('md', 'csv', 'json'):
        body += '<a class="button secondary" href="/program/asset-matrix?id=' + quote(p['id']) + '&amp;format=' + fmt + '">导出 ' + fmt.upper() + '</a>'
    body += '</div><p>'+str(len(state['rows']))+' 项资料 · '+str(sum(r['review_state']=='pending' for r in state['rows']))+' 项待审阅 · '+str(sum(not r['current'] for r in state['rows']))+' 项依据变化</p><details><summary>查看矩阵版本</summary><p class="ref">'+e(state['matrix_hash'])+'</p></details></section>'
    if state['rows']:
        body += '<div class="table-wrap"><table><thead><tr><th>资产／资料</th><th>地区与用途</th><th>资料方向与覆盖时间</th><th>审阅</th></tr></thead><tbody>'
        for row in state['rows']:
            label = '依据变化，需重审' if not row['current'] else '待审阅' if row['review_state']=='pending' else JUDGMENTS[row['review_state']]
            body += '<tr><td><a href="#asset-claim-'+e(row['event_id'])+'">'+e(row['asset_identity']+' · '+TYPES[row['category']])+'</a></td><td>'+e(row['territory'])+'<br>'+e(row['indication'])+'</td><td>'+e(DIRECTIONS[row['direction']])+'<br>'+e(row['coverage_start']+' 至 '+row['coverage_end'])+'</td><td>'+e(label)+'</td></tr>'
        body += '</tbody></table></div>'
    for row in state['rows']:
        body += '<details class="card" id="asset-claim-'+e(row['event_id'])+'"><summary>'+e(row['asset_identity']+' · '+TYPES[row['category']])+' · 来源与审阅</summary><span class="badge">' + ('依据变化，需重审' if not row['current'] else '待审阅' if row['review_state'] == 'pending' else e(JUDGMENTS[row['review_state']])) + '</span><h4>' + e(row['asset_identity'] + ' · ' + TYPES[row['category']]) + '</h4><p>' + e(row['claim']) + '</p><dl class="asset-facts">'
        for k, label in [('territory', '地区'), ('indication', '用途／适应症'), ('rights_type', '权利类型'), ('coverage_start', '覆盖起始'), ('coverage_end', '覆盖截止'), ('limitations', '来源限制'), ('missing', '尚缺资料'), ('next_action', '下一项核查')]:
            body += '<dt>' + label + '</dt><dd>' + e('未知' if row[k]=='unknown' else row[k]) + '</dd>'
        body += '</dl><p>' + e(DIRECTIONS[row['direction']]) + '</p>'
        source = row['source']
        if source:
            # Only safe schemes entered by existing source validators; artifact refs are text.
            url = source['url']
            body += '<p>来源：' + ('<a href="' + e(url) + '" target="_blank" rel="noopener">' + e(url) + '</a>' if url.startswith(('https://', 'http://')) else e(url)) + '<br>来源日期 ' + e(source['source_date']) + '；快照日期 ' + e(source['snapshot_date'] or '未绑定本地网页快照') + '<br>完整性 ' + e({'local_bytes_verified':'本地来源哈希一致','record_bound_remote_not_checked':'仅绑定记录，未重新核验网页','changed':'来源记录已变化','unavailable_or_changed':'来源缺失或变化'}.get(row['source_integrity'],row['source_integrity'])) + '</p>'
        body += '<p class="ref">记录人 ' + e(row['author']) + ' · ' + e(row['created']) + '<br>记录编号 ' + e(row['event_id']) + '</p>'
        for review in row['reviews']:
            body += '<details><summary>' + ('当前依据的本地审阅声明' if review['current'] and row['current'] else '历史审阅，不能作为当前通过') + ' · ' + e(JUDGMENTS[review['judgment']]) + '</summary>'
            for k, label in [('reason', '依据'), ('scope', '审阅范围'), ('counterevidence', '反证与分歧'), ('next_action', '后续核查')]:
                body += '<p><b>' + label + '</b> ' + e(review[k]) + '</p>'
            body += '<p class="ref">' + e(review['author']) + ' · ' + e(review['created']) + '</p></details>'
        if row['current']:
            body += '<details><summary>追加本地资料审阅</summary>' + form(app, p, 'asset_claim_review', '审阅资料的解释与范围')
            for key, value in [('claim_id', row['event_id']), ('matrix_hash', state['matrix_hash'])]:
                body += '<input type="hidden" name="' + key + '" value="' + e(value) + '">'
            body += select('judgment', '判断', JUDGMENTS.items())
            for k, label in [('reason', '判断依据（至少 10 字符）'), ('scope', '实际审阅的资产、地区、用途和时间范围'), ('counterevidence', '已面对的反证、冲突和未解决问题'), ('next_action', '下一项需补充的核查')]:
                body += field(k, label)
            body += field('author', '实际审阅人（本地声明）', 'text') + '<button>追加审阅声明</button></form></details>'
        body += '</details>'
    body += '<section class="card"><h4>仍需补充的资料与核查动作</h4><p>以下按已录入的范围列出，不能据此推定其他地区、用途或时间已覆盖。冲突不按录入时间自动裁决。</p><ul>'
    for gap in state['gaps']:
        body += '<li><b>' + e(gap['asset_identity'] + ' · ' + TYPES[gap['category']]) + '</b>：' + e(gap['reason']) + '。下一步：' + e(gap['next_action']) + '</li>'
    body += '</ul></section>'
    # Per-asset forms keep the selected source and exact molecule identity aligned without JS.
    for asset in p['assets']:
        body += '<details class="card"><summary>追加 ' + e(asset['asset_name']) + ' 的资料或未知项</summary>' + form(app, p, 'asset_claim', '记录精确范围与来源')
        for key, value in [('asset_id', asset['event_id']), ('asset_identity', asset['asset_name'])]:
            body += '<input type="hidden" name="' + key + '" value="' + e(value) + '">'
        body += select('category', '资料领域', TYPES.items()) + select('direction', '来源对主张的方向', DIRECTIONS.items())
        for k, label in [('territory', '地区（未知请明确填写）'), ('indication', '用途／适应症'), ('rights_type', '权利类型及限制；非权利资料请写不适用'), ('coverage_start', '资料覆盖起始：YYYY-MM-DD 或 unknown'), ('coverage_end', '资料覆盖截止：YYYY-MM-DD 或 unknown'), ('claim', '来源具体支持、限制或尚不能判断的主张'), ('limitations', '来源不支持哪些外推'), ('missing', '仍缺哪些材料'), ('next_action', '下一项可执行的核查动作')]:
            body += field(k, label, 'text' if k in ('coverage_start', 'coverage_end') else 'textarea')
        choices = [('', '无来源，仅能记录未知'), ('asset:' + asset['event_id'], '该资产绑定的登记快照')]
        choices += [('evidence:' + r['event_id'], r['claim']) for r in p['evidence']]
        body += '<label>引用已有来源<select name="source_ref">' + ''.join('<option value="' + e(k) + '">' + e(v) + '</option>' for k, v in choices) + '</select></label>'
        body += field('author', '资料记录人（本地声明）', 'text') + '<p><button>追加资料，保留全部历史</button></p></form></details>'
    return body, state
