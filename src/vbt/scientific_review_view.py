"""Comparison, provenance and explicitly attributed review forms."""
import json
from urllib.parse import quote
from .scientific_review import DOMAINS, JUDGMENTS, PRIORITIES, view

STATUSES = {'numerically_compatible_inferred_choices': '数值相容；原始选择未确认',
            'confirmed_unit_difference': '独立单位有差异', 'matched': '计数一致',
            'not_reconstructed': '未重建', 'not_same_population': '分析人群不同',
            'partial_support': '部分支持', 'targeted_direction_supported': '仅目标基因方向获支持',
            'source_lookup_matched': '来源查询一致，非重新分析', 'unresolved_difference': '差异未解决',
            'source_matched': '来源信息一致', 'current_record_differs': '当前记录有差异',
            'not_reproduced': '未复现', 'not_validated': '未验证', 'not_replayed': '未重放'}
DIFFERENCES = {'sample_unit': '样本／独立单位', 'test_choice': '检验选择', 'compatible': '候选口径相容',
               'endpoint_choice': '终点选择', 'count': '计数', 'phenotype': '结局标签', 'selection': '人群选择',
               'estimand_and_multiplicity': '估计目标与多重比较', 'testing_family': '检验家族',
               'source_or_model_version_unknown': '来源／模型版本未知', 'source_timing': '来源时点',
               'missing_selection_manifest': '缺少选择清单', 'evidence_boundary': '证据适用边界',
               'missing_original_session': '缺少原始会话'}


def render(app, store, p):
    from .program_view import e, field, form
    state = view(store, p['id']); comp = state['comparison']
    body = '<section class="section-head" id="scientific-review"><h2>原版对照与风险审阅</h2><span>差异、来源完整性和科学判断分别记录</span></section>'
    body += '<p class="data-note">原版栏保留封存报告记载，未重新核对正式发表版。数值相容不证明作者选择已恢复。每次审阅均为记录人的本地声明，不自动改变证据审阅、实验或研发决定。</p>'
    rows = comp['payload']['rows'] if comp else []
    if comp:
        body += '<p>绑定文件完整性：' + ('哈希核对一致' if state['source_integrity'] == 'verified' else '来源缺失或变化') + ' · 完整复现：' + ('部分完成' if comp['payload']['full_replication'] == 'partial' else e(comp['payload']['full_replication'])) + '</p>'
        source_url = '/program/comparison-source?id=' + quote(p['id']) + '&file='
        from .scientific_review import REPORT
        body += '<p><a href="' + source_url + quote(REPORT) + '">查看封存原版对照报告</a> · <a href="' + source_url + 'claim_comparison.csv">查看原始逐项表</a></p>'
        body += '<p><a href="/program/comparison-report?id=' + quote(p['id']) + '&format=md">导出逐项对照 Markdown</a> · <a href="/program/comparison-report?id=' + quote(p['id']) + '&format=csv">导出机器可读差异 CSV</a></p>'
        body += '<div class="table-wrap"><table style="table-layout:fixed;width:100%"><colgroup><col style="width:18%"><col style="width:18%"><col style="width:29%"><col style="width:25%"><col style="width:10%"></colgroup><thead><tr><th>项目</th><th>封存原版记载</th><th>独立重建</th><th>差异与状态</th><th style="white-space:nowrap">科学审阅</th></tr></thead><tbody>'
        for row in rows:
            records = [r for r in state['reviews'] if r['subject_kind'] == 'difference' and r['subject'] == row['item'] and r['comparison_id'] == comp['id']]
            review_label = ('依据已变化，需重审' if records[-1]['stale'] else '有本地审阅声明') if records else '待审阅'
            body += '<tr><td><a href="#difference-' + e(row['item']) + '">' + e(row['item']) + '</a></td><td>' + e(row['original']) + '</td><td>' + e(row['independent']) + '</td><td>' + e(DIFFERENCES.get(row['difference_class'], row['difference_class'])) + '<br>' + e(STATUSES.get(row['status'], row['status'])) + '</td><td style="white-space:nowrap">' + review_label + '</td></tr>'
        body += '</tbody></table></div>'
        for row in rows:
            body += '<details class="card" id="difference-' + e(row['item']) + '"><summary>' + e(row['item']) + ' · 来源与审阅</summary><p>' + e(row['explanation']) + '</p>'
            body += '<p class="ref">来源定位：claim_comparison.csv · item=' + e(row['item']) + '<br>' + e('; '.join(row['source_files'])) + '</p>'
            body += '<p>' + ' · '.join('<a href="' + source_url + quote(name) + '">' + e(name) + '</a>' for name in row['source_files']) + '</p>'
            if row['analysis_context']:
                body += '<p>结构化分析口径（仅转录已记录字段）</p><pre>' + e(json.dumps(row['analysis_context'], ensure_ascii=False, indent=2)) + '</pre>'
            else:
                body += '<p class="data-note">本条未自动补定样本单位、终点或患者／探针映射；需查看关联来源并在审阅中明确范围与缺口。</p>'
            body += history(state, 'difference', row['item'], comp['id'])
            if state['source_integrity'] == 'verified':
                body += assessment_form(app, p, state, 'difference', row['item'], row['item'])
            body += '</details>'
    else:
        body += '<p class="data-note">尚未导入封存逐项对照。管理员可用本地命令导入已有报告；此页面不接受任意文件路径或下载数据。</p>'
    body += '<h3>八类风险与下一项验证</h3><p>优先级由实际记录人说明。系统不把 AI 草案自动当作审阅结果。</p>'
    for domain, label in DOMAINS.items():
        body += '<details class="card"><summary>' + e(label) + '</summary>' + history(state, 'risk', domain)
        if not comp or state['source_integrity'] == 'verified':
            body += assessment_form(app, p, state, 'risk', domain, label, rows)
        body += '</details>'
    if comp and state['source_integrity'] != 'verified':
        body += '<p class="notice">绑定来源缺失或变化，仍可查看保存的对照及历史。当前范围的审阅提交已停止，请恢复原来源或导入新版本。</p>'
    return body


def history(state, kind, subject, comparison_id=None):
    from .program_view import e
    records = [r for r in state['reviews'] if r['subject_kind'] == kind and r['subject'] == subject
               and (kind != 'difference' or r['comparison_id'] == comparison_id)]
    if not records:
        return '<p class="data-note">待科学审阅，尚无实际审阅记录。</p>'
    body = ''
    for r in reversed(records):
        body += '<section class="card"><span class="badge">' + ('依据已变化 · 需重审' if r['stale'] else '本地审阅声明') + '</span><h4>' + e(JUDGMENTS[r['judgment']]) + ' · 优先级' + e(PRIORITIES[r['priority']]) + '</h4>'
        for k, label in [('reason', '判断依据'), ('scope', '适用范围'), ('missing', '缺失与分歧'), ('next_action', '下一项验证'), ('success', '成功判据'), ('stop', '停止条件')]:
            body += '<p><b>' + label + '</b> ' + e(r[k]) + '</p>'
        body += '<p class="ref">' + e(r['author']) + ' · ' + e(r['created']) + '<br>引用 ' + e(', '.join(r['references']) or '无可用引用，保留未知') + '<br>依据版本 ' + e(r['basis_hash']) + '</p></section>'
    return body


def assessment_form(app, p, state, kind, subject, title, rows=()):
    from .program_view import e, field, form
    body = form(app, p, 'scientific_assessment', '追加审阅：' + e(title))
    for key, value in [('subject_kind', kind), ('subject', subject), ('basis_hash', state['basis_hash'])]:
        body += '<input type="hidden" name="' + key + '" value="' + e(value) + '">'
    body += '<label>判断<select name="judgment" required><option value="" selected disabled>请选择判断</option>' + ''.join('<option value="' + k + '">' + v + '</option>' for k, v in JUDGMENTS.items()) + '</select></label>'
    body += '<label>验证优先级<select name="priority" required><option value="" selected disabled>请选择优先级</option>' + ''.join('<option value="' + k + '">' + v + '</option>' for k, v in PRIORITIES.items()) + '</select></label>'
    for key, label in [('reason', '判断依据（至少 10 字符）'), ('scope', '人群、干预、比较、终点与时间范围'), ('missing', '缺失资料、分歧与尚未核实项'), ('next_action', '下一项最可能改变决定的核查或验证'), ('success', '什么结果支持下一步'), ('stop', '什么结果应停止或修订')]:
        body += field(key, label)
    if kind == 'risk':
        body += '<fieldset><legend>支持／反证／差异引用（非未知判断至少一项）</legend>'
        refs = [(r['event_id'], r['claim']) for r in p['evidence']]
        refs += [(r['event_id'], r['metric']) for r in p['results'] if r['current_scope']]
        refs += [('difference:' + r['item'], r['item']) for r in rows]
        for key, label in refs:
            body += '<label><input type="checkbox" name="evidence_ids" value="' + e(key) + '"> ' + e(label) + '</label>'
        body += '</fieldset>'
    return body + field('author', '实际审阅记录人（本地声明）', 'text') + '<p><button>追加审阅与验证优先级</button></p></form>'
