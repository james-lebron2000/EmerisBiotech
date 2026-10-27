"""Source-bound comparison snapshots and explicit local scientific declarations.

No download, analysis, model call, or inferred scientific approval occurs here.
"""
import csv
import hashlib
import html
import io
import json
import re
from pathlib import Path, PurePosixPath

from .programs import Conflict, canonical, text

DOMAINS = {'mechanism': '机制因果', 'biomarker': '患者分层', 'functional': '功能性药理',
           'clinical': '临床疗效', 'safety': '安全性', 'developability': '可开发性与 CMC',
           'rights': '材料与资产权利', 'differentiation': '差异化'}
JUDGMENTS = {'supported': '所审范围内有支持', 'contested': '存在反证或分歧', 'unknown': '仍不能判断'}
PRIORITIES = {'high': '高', 'medium': '中', 'low': '低'}
REPORT = 'UC_独立复现_原版对照报告_20261025.md'
BASE_FILES = ('claim_comparison.csv', 'replication_status.json', REPORT,
              'published_compatible_reconstruction.csv', 'sample_flow.csv')
COLUMNS = ('item', 'original', 'independent', 'status', 'difference_class', 'explanation', 'evidence')
BASIS_KINDS = {'created', 'profile', 'evidence', 'evidence_review', 'result', 'result_review',
               'asset_import', 'import', 'comparison_import'}
LIMIT = 1024 * 1024


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def read_file(root, name):
    relative = PurePosixPath(name)
    if (not name or relative.is_absolute() or '..' in relative.parts or '\\' in name
            or str(relative) != name):
        raise ValueError('比较来源路径不安全')
    path = (root / name).resolve()
    if not path.is_relative_to(root):
        raise ValueError('比较来源越出绑定目录')
    with path.open('rb') as stream:
        raw = stream.read(LIMIT + 1)
    if len(raw) > LIMIT:
        raise ValueError('比较来源超过 1MB 限制')
    return raw


def table(raw):
    reader = csv.DictReader(io.StringIO(raw.decode('utf-8-sig')))
    rows = list(reader)
    if not rows or len(rows) > 100:
        raise ValueError('比较表须含 1–100 行')
    return reader.fieldnames, rows


def import_comparison(store, pid, root, author, revision):
    """Import a bounded projection of an existing sealed UC report, not raw data."""
    p = store.get(pid)
    if revision != p['revision']:
        raise Conflict('项目已有更新，请刷新后导入')
    root = Path(root).resolve()
    manifest_raw = read_file(root, 'ARTIFACT_MANIFEST.json')
    manifest = json.loads(manifest_raw)
    if manifest.get('run_id') != 'uc_independent_20261025':
        raise ValueError('不是指定的 UC 独立复现封存运行')
    files = manifest.get('files')
    if not isinstance(files, list) or len(files) > 300:
        raise ValueError('来源清单格式错误')
    hashes = {}
    for row in files:
        name = row['path']
        if name in hashes or not re.fullmatch('[a-f0-9]{64}', row['sha256']):
            raise ValueError('来源清单包含重复路径或无效哈希')
        hashes[name] = row['sha256']
    raw_files = {}

    def bind(name):
        if name not in raw_files:
            if name not in hashes:
                raise ValueError('比较来源未列入封存清单')
            raw = read_file(root, name)
            if digest(raw) != hashes[name]:
                raise ValueError('比较来源哈希不一致')
            raw_files[name] = raw
            if len(raw_files) > 40 or sum(map(len, raw_files.values())) > 8 * LIMIT:
                raise ValueError('比较来源总量超限')
        return raw_files[name]

    for name in BASE_FILES:
        bind(name)
    columns, rows = table(raw_files['claim_comparison.csv'])
    if columns != list(COLUMNS):
        raise ValueError('比较表字段不匹配')
    _, context_rows = table(raw_files['published_compatible_reconstruction.csv'])
    clean, seen = [], set()
    for row in rows:
        item = row['item']
        if not isinstance(item, str) or not re.fullmatch('[A-Za-z0-9_-]{1,100}', item) or item in seen:
            raise ValueError('比较项目编号重复或无效')
        seen.add(item)
        data = {k: text(row[k], k, 1) for k in COLUMNS}
        names = [n.strip() for n in row['evidence'].split(';')]
        for name in names:
            bind(name)
        data['source_files'] = names
        # Copy recorded fields only. Do not infer unknown endpoints or patient IDs.
        data['analysis_context'] = [
            {k: r[k] for k in ('analysis_id', 'cohort', 'endpoint_rule', 'unit', 'scale',
                               'probes', 'n', 'n_responder', 'n_nonresponder',
                               'compatible_test', 'original_choice_status')}
            for r in context_rows if item == r.get('cohort', '') + '_OSMR' and r.get('gene') == 'OSMR'
        ]
        clean.append(data)
    status = json.loads(raw_files['replication_status.json'])
    if status.get('claims') != rows or status.get('scientific_review') != 'pending':
        raise ValueError('封存状态与逐项对照不一致，需独立核查')
    payload = dict(schema_version=1, root=str(root), run_id=manifest['run_id'],
                   manifest_sha256=digest(manifest_raw), files={n: digest(b) for n, b in raw_files.items()},
                   rows=clean, reference=status.get('reference'), original_author_commit=status.get('author_commit'),
                   full_replication=status.get('full_original_UC_case_replication'),
                   original_claim_scope='封存报告记载的 bioRxiv v1 主张；本次导入未重新核对原文或正式发表版',
                   scientific_review='pending')
    content_hash = digest(canonical({k: v for k, v in payload.items() if k != 'root'}).encode())
    prior = [e for e in p['events'] if e['kind'] == 'comparison_import']
    if prior and prior[-1]['payload']['content_hash'] == content_hash and prior[-1]['payload']['root'] == str(root):
        return prior[-1]['id']
    payload['content_hash'] = content_hash
    store._append(pid, 'comparison_import', payload, author, revision)
    return store.events(pid)[-1]['id']


def basis_hash(events):
    return digest(canonical([e['digest'] for e in events if e['kind'] in BASIS_KINDS]).encode())


def view(store, pid):
    events = store.events(pid)
    imports = [e for e in events if e['kind'] == 'comparison_import']
    latest = imports[-1] if imports else None
    integrity = 'not_imported'
    if latest:
        integrity = 'verified'
        try:
            source = latest['payload']; root = Path(source['root']).resolve()
            if digest(read_file(root, 'ARTIFACT_MANIFEST.json')) != source['manifest_sha256']:
                raise ValueError('changed manifest')
            for name, sha in source['files'].items():
                if digest(read_file(root, name)) != sha:
                    raise ValueError('changed source')
        except (OSError, ValueError, TypeError):
            integrity = 'unavailable_or_changed'
    basis = basis_hash(events)
    sources = {e['id']: e['payload'] for e in events if e['kind'] in ('evidence', 'result')}
    checked = {}
    reviews = []
    for ev in events:
        if ev['kind'] == 'scientific_assessment':
            refs_current = True
            for ref in ev['payload']['references']:
                source = sources.get(ref)
                if source and source['source'].startswith('artifact:'):
                    if ref not in checked:
                        try:
                            _, sha = store.attachment(pid, source['source'].removeprefix('artifact:'), max_bytes=LIMIT)
                            checked[ref] = sha == source.get('source_sha256')
                        except (OSError, ValueError):
                            checked[ref] = False
                    refs_current = refs_current and checked[ref]
            reviews.append(dict(ev['payload'], event_id=ev['id'], author=ev['author'], created=ev['created'],
                                stale=ev['payload']['basis_hash'] != basis or
                                not refs_current or
                                (ev['payload'].get('comparison_id') is not None and integrity != 'verified')))
    return dict(basis_hash=basis, comparison=latest, source_integrity=integrity, reviews=reviews)


def assess(store, pid, payload, author, revision):
    p = store.get(pid); state = view(store, pid)
    if revision != p['revision']:
        raise Conflict('项目已有更新，请刷新后审阅')
    if payload.get('basis_hash') != state['basis_hash']:
        raise Conflict('所审项目与来源范围已变化，请重新核对')
    kind, subject = payload.get('subject_kind'), payload.get('subject')
    comp = state['comparison']
    rows = comp['payload']['rows'] if comp else []
    if kind == 'difference':
        if subject not in {r['item'] for r in rows}:
            raise ValueError('差异不属于当前比较版本')
    elif kind == 'risk':
        if subject not in DOMAINS:
            raise ValueError('未知风险类别')
    else:
        raise ValueError('未知审阅对象')
    # A bound source becoming unavailable cannot silently keep an old declaration current.
    if comp and state['source_integrity'] != 'verified':
        raise ValueError('比较来源缺失或变化，停止提交当前范围审阅')
    refs = payload.get('references', [])
    known = {r['event_id'] for r in p['evidence']} | {r['event_id'] for r in p['results'] if r['current_scope']}
    known |= {'difference:' + r['item'] for r in rows}
    if (not isinstance(refs, list) or len(refs) > 40 or any(not isinstance(r, str) for r in refs)
            or not set(refs) <= known):
        raise ValueError('审阅引用不属于当前项目与比较版本')
    judgment = payload.get('judgment'); priority = payload.get('priority')
    if judgment not in JUDGMENTS or priority not in PRIORITIES:
        raise ValueError('需要明确审阅判断与优先级')
    if kind == 'risk' and judgment != 'unknown' and not refs:
        raise ValueError('非未知风险判断必须引用证据或差异')
    # Verify all artifact references at declaration time, including the source's
    # original binding. An import rebound to different bytes must not bless old evidence.
    for row in p['evidence'] + p['results']:
        if row['event_id'] in refs and row['source'].startswith('artifact:'):
            _, sha = store.attachment(pid, row['source'].removeprefix('artifact:'), max_bytes=LIMIT)
            if sha != row.get('source_sha256'):
                raise ValueError('引用来源与记录版本不一致')
    data = {k: text(payload.get(k), k, 10) for k in ('reason', 'scope', 'missing', 'next_action', 'success', 'stop')}
    data.update(schema_version=1, subject_kind=kind, subject=subject, judgment=judgment, priority=priority,
                references=list(dict.fromkeys(refs)), basis_hash=state['basis_hash'],
                comparison_id=comp['id'] if comp else None, identity='self_declared_not_authenticated',
                declaration_scope='本地审阅声明；不认证人员资格，不改变原始证据、实验或决定状态')
    store._append(pid, 'scientific_assessment', data, author, revision)


def context_summary(store, pid):
    state = view(store, pid)
    return dict(basis_hash=state['basis_hash'], source_integrity=state['source_integrity'],
                comparison_hash=state['comparison']['payload']['content_hash'] if state['comparison'] else None,
                assessments=[{k: r[k] for k in ('event_id', 'subject_kind', 'subject', 'judgment', 'priority',
                                               'reason', 'scope', 'missing', 'next_action', 'success', 'stop',
                                               'references', 'stale')} for r in state['reviews']])


def comparison_source(store, pid, name):
    state = view(store, pid); comp = state['comparison']
    if not comp or name not in comp['payload']['files']:
        raise ValueError('来源不属于当前比较的允许清单')
    if state['source_integrity'] != 'verified':
        raise ValueError('比较来源缺失或变化')
    raw = read_file(Path(comp['payload']['root']).resolve(), name)
    if digest(raw) != comp['payload']['files'][name]:
        raise ValueError('比较来源在读取期间变化')
    return raw


def export_report(store, pid, format):
    state = view(store, pid); comp = state['comparison']
    if not comp:
        raise ValueError('尚未导入逐项比较')
    rows = []
    for row in comp['payload']['rows']:
        reviews = [r for r in state['reviews'] if r['subject_kind'] == 'difference'
                   and r['subject'] == row['item'] and r['comparison_id'] == comp['id']]
        latest = reviews[-1] if reviews else None
        rows.append(dict(row, source_integrity=state['source_integrity'],
                         review_state=('needs_review' if latest['stale'] else 'local_declaration') if latest else 'pending',
                         review_history=reviews))
    if format == 'csv':
        out = io.StringIO(newline=''); fields = list(rows[0])
        writer = csv.DictWriter(out, fieldnames=fields); writer.writeheader()
        for row in rows:
            # Spreadsheet formula injection is neutralized in exported text;
            # exact source values remain in the immutable comparison event.
            def cell(value):
                value = canonical(value) if isinstance(value, (list, dict)) else str(value)
                return "'" + value if value.lstrip().startswith(('=', '+', '-', '@')) else value
            writer.writerow({k: cell(v) for k, v in row.items()})
        return out.getvalue().encode('utf-8-sig')
    if format != 'md':
        raise ValueError('未知报告格式')
    def escape(value):
        return html.escape(str(value)).replace('|', '&#124;').replace('\n', '<br>')
    lines = ['# UC 原版逐项对照与本地审阅', '',
             '原版范围：' + comp['payload']['original_claim_scope'], '',
             '完整复现：' + str(comp['payload']['full_replication']) + '；来源完整性：' + state['source_integrity'], '',
             '数值相容不证明恢复了作者选择；本地署名不认证审阅资格。缺失或过期审阅不能作为当前通过。', '',
             '| 项目 | 封存原版记载 | 独立重建 | 差异与解释 | 来源定位 | 审阅状态 |',
             '| --- | --- | --- | --- | --- | --- |']
    for row in rows:
        lines.append('| ' + ' | '.join(escape(v) for v in (row['item'], row['original'], row['independent'],
                     row['difference_class'] + ': ' + row['explanation'], '; '.join(row['source_files']), row['review_state'])) + ' |')
    lines += ['', '## 分析口径与全部审阅历史', '']
    for row in rows:
        lines += ['### ' + escape(row['item']), '', '```json', canonical(row).replace('`', '\\u0060'), '```', '']
    lines += ['## 八类风险审阅记录', '', '```json', canonical([r for r in state['reviews'] if r['subject_kind'] == 'risk']).replace('`', '\\u0060'), '```', '']
    return '\n'.join(lines).encode()
