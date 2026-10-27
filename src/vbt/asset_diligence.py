"""Scoped, append-only asset claims. No inferred ownership or remote verification."""
import csv
import hashlib
import io
from datetime import date
from pathlib import Path

from .programs import Conflict, canonical, text

TYPES = {'sponsor': '试验申办', 'developer': '开发主体', 'patent_application': '专利申请',
         'patent_grant': '专利授权', 'license': '许可范围', 'ownership': '资产所有权',
         'scientific': '科学差异化', 'development': '开发风险', 'transaction': '交易假设与可得性'}
DIRECTIONS = {'support': '支持或背景资料', 'counter': '反证或限制', 'unknown': '尚无依据判断'}
JUDGMENTS = {'supported': '所审范围内有支持', 'contested': '存在分歧', 'unknown': '仍不能判断'}
BASIS_KINDS = {'created', 'profile', 'evidence', 'evidence_review', 'result', 'result_review',
               'import', 'asset_import', 'comparison_import'}
LIMIT = 1024 * 1024


def sha(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def iso(value, label):
    if not isinstance(value, str):
        raise ValueError(label + '须为 YYYY-MM-DD')
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise ValueError(label + '须为 YYYY-MM-DD') from None
    if parsed.isoformat() != value:
        raise ValueError(label + '须为 YYYY-MM-DD')
    return value


def asset_for(p, asset_id):
    asset = next((a for a in p['assets'] if a['event_id'] == asset_id), None)
    if not asset:
        raise ValueError('资料须关联本项目精确参考资产')
    return asset


def context(p):
    return sha([e['digest'] for e in p['events'] if e['kind'] in BASIS_KINDS])


def asset_source(store, pid, asset_id):
    asset = asset_for(store.get(pid), asset_id)
    with Path(asset['source_path']).open('rb') as stream:
        raw = stream.read(LIMIT + 1)
    if len(raw) > LIMIT or hashlib.sha256(raw).hexdigest() != asset['source_sha256']:
        raise ValueError('参考资产来源已变化或超出限制')
    return raw


def source_binding(store, p, asset, ref):
    """Bind existing records only. External URLs are not fetched or authenticated."""
    if ref == 'asset:' + asset['event_id']:
        asset_source(store, p['id'], asset['event_id'])
        return dict(reference=ref, record_hash=sha(asset), content_sha256=asset['source_sha256'],
                    url=asset['source_url'], source_date=asset['last_update'],
                    snapshot_date=asset['snapshot_date'], integrity='local_bytes_verified')
    evidence = next((e for e in p['evidence'] if ref == 'evidence:' + e['event_id']), None)
    if not evidence:
        raise ValueError('来源须为该资产登记或本项目证据')
    content_sha = None
    if evidence['source'].startswith('artifact:'):
        _, content_sha = store.attachment(p['id'], evidence['source'].removeprefix('artifact:'), max_bytes=LIMIT)
        if content_sha != evidence.get('source_sha256'):
            raise ValueError('证据来源版本已变化')
    return dict(reference=ref, record_hash=sha(evidence), content_sha256=content_sha,
                url=evidence['source'], source_date=evidence['source_date'], snapshot_date=None,
                integrity='local_bytes_verified' if content_sha else 'record_bound_remote_not_checked')


def prepare_claim(store, pid, payload, revision):
    p = store.get(pid)
    if p['revision'] != revision:
        raise Conflict('项目已有更新，请刷新后记录资料')
    asset = asset_for(p, payload.get('asset_id'))
    if payload.get('asset_identity') != asset['asset_name']:
        raise ValueError('分子身份须与所选资产精确一致；别名对应需另行核实')
    category, direction = payload.get('category'), payload.get('direction')
    if category not in TYPES or direction not in DIRECTIONS:
        raise ValueError('资料领域或证据方向错误')
    data = {k: text(payload.get(k), k, 1 if k in ('territory', 'indication', 'rights_type') else 3) for k in
            ('territory', 'indication', 'rights_type', 'claim', 'limitations', 'missing', 'next_action')}
    start, end = payload.get('coverage_start'), payload.get('coverage_end')
    start = 'unknown' if start == 'unknown' else iso(start, '覆盖起始日期')
    end = 'unknown' if end == 'unknown' else iso(end, '覆盖截止日期')
    if start != 'unknown' and end != 'unknown' and start > end:
        raise ValueError('覆盖起始日期不能晚于截止日期')
    ref = payload.get('source_ref', '')
    if not isinstance(ref, str) or (not ref and direction != 'unknown'):
        raise ValueError('非未知主张须引用明确来源')
    binding = source_binding(store, p, asset, ref) if ref else None
    data.update(asset_id=asset['event_id'], asset_identity=asset['asset_name'], category=category,
                direction=direction, coverage_start=start, coverage_end=end, source=binding,
                context_hash=context(p), rights_status='not_established')
    return data


def add_claim(store, pid, payload, author, revision):
    data = prepare_claim(store, pid, payload, revision)
    store._append(pid, 'asset_claim', data, author, revision)
    return store.events(pid)[-1]['id']


def matrix(store, pid):
    p = store.get(pid)
    rows = []
    for event in p['events']:
        if event['kind'] != 'asset_claim':
            continue
        row = dict(event['payload'], event_id=event['id'], author=event['author'], created=event['created'])
        current = row['context_hash'] == context(p)
        integrity = 'no_source'
        if row['source']:
            try:
                binding = source_binding(store, p, asset_for(p, row['asset_id']), row['source']['reference'])
                current = current and binding == row['source']
                integrity = binding['integrity'] if binding == row['source'] else 'changed'
            except (ValueError, OSError):
                current, integrity = False, 'unavailable_or_changed'
        row.update(current=current, source_integrity=integrity)
        rows.append(row)
    basis = sha(dict(context=context(p), claims=[(r['event_id'], r['current'], r['source_integrity']) for r in rows]))
    reviews = [dict(e['payload'], event_id=e['id'], author=e['author'], created=e['created'],
                    current=e['payload']['basis_hash'] == basis)
               for e in p['events'] if e['kind'] == 'asset_claim_review']
    for row in rows:
        row['reviews'] = [r for r in reviews if r['claim_id'] == row['event_id']]
        latest = next((r for r in reversed(row['reviews']) if r['current']), None)
        row['review_state'] = latest['judgment'] if latest and row['current'] else 'pending'
    gaps = []
    for asset in p['assets']:
        for category, label in TYPES.items():
            relevant = [r for r in rows if r['asset_id'] == asset['event_id'] and r['category'] == category]
            # Every unreviewed, stale or conflicting row remains visible; no last-write wins.
            if not relevant:
                gaps.append(dict(asset_id=asset['event_id'], asset_identity=asset['asset_name'], category=category,
                                 reason='未记录资料，不能推定不存在或已核实', next_action='补充' + label + '的范围、来源与待核实项'))
            for row in relevant:
                # Declared missing material is retained even after a supported local review.
                gaps.append(dict(asset_id=asset['event_id'], asset_identity=asset['asset_name'], category=category,
                                     reason=('来源或项目范围变化，需重审；' if not row['current'] else '') + ('待审阅；' if row['review_state'] == 'pending' else '') + row['missing'],
                                     next_action=row['next_action']))
    matrix_hash = sha(dict(basis=basis, reviews=[e['digest'] for e in p['events'] if e['kind'] == 'asset_claim_review']))
    return dict(schema_version=1, program_id=pid, matrix_hash=matrix_hash, basis_hash=basis, rows=rows, gaps=gaps,
                asset_status='external_reference_rights_not_established',
                scope='本地资料与审阅声明；非法律意见、权利认证、许可可得性或交易承诺')


def review_claim(store, pid, payload, author, revision):
    p = store.get(pid)
    if p['revision'] != revision:
        raise Conflict('项目已有更新，请刷新后审阅')
    state = matrix(store, pid)
    row = next((r for r in state['rows'] if r['event_id'] == payload.get('claim_id')), None)
    if not row or not row['current'] or payload.get('matrix_hash') != state['matrix_hash']:
        raise ValueError('资料或审阅依据已变化，请刷新并重新记录资料')
    if payload.get('judgment') not in JUDGMENTS:
        raise ValueError('未知资料审阅判断')
    if payload['judgment'] == 'supported' and (not row['source'] or row['direction'] == 'unknown'):
        raise ValueError('缺少来源或未知主张不能声明有支持')
    data = {k: text(payload.get(k), k, 10) for k in ('reason', 'scope', 'counterevidence', 'next_action')}
    data.update(claim_id=row['event_id'], judgment=payload['judgment'], matrix_hash=state['matrix_hash'], basis_hash=state['basis_hash'],
                declaration='本地署名，未认证审阅资格或权利有效性')
    store._append(pid, 'asset_claim_review', data, author, revision)


def bd_binding(store, pid, payload, asset_id):
    state = matrix(store, pid)
    rows = [r for r in state['rows'] if r['asset_id'] == asset_id]
    if not rows:
        return dict(asset_matrix_hash=state['matrix_hash'], asset_claim_ids=[])
    refs = payload.get('asset_claim_ids', [])
    if (not isinstance(refs, list) or len(refs) != len(set(refs))
            or set(refs) != {r['event_id'] for r in rows}
            or payload.get('asset_matrix_hash') != state['matrix_hash']):
        raise ValueError('尽调须面对所选资产全部资料与分歧，引用当前矩阵版本')
    if payload.get('acknowledge_gaps') != 'yes':
        raise ValueError('须确认全部资料、过期依据和缺失项仍未构成权利或交易认证')
    return dict(asset_matrix_hash=state['matrix_hash'], asset_claim_ids=refs,
                stale_claim_ids=[r['event_id'] for r in rows if not r['current']],
                gaps_acknowledged=True)


def export_matrix(store, pid, fmt):
    state = matrix(store, pid)
    if fmt == 'json':
        return canonical(state).encode()
    if fmt not in ('md', 'csv'):
        raise ValueError('仅支持 md、csv 或 json')
    columns = ('kind', 'asset', 'category', 'territory', 'indication', 'rights_type', 'coverage_start',
               'coverage_end', 'claim', 'direction', 'source', 'source_date', 'snapshot_date',
               'integrity', 'review', 'missing', 'next_action', 'record_id')
    records = []
    for r in state['rows']:
        source = r['source'] or {}
        records.append(('claim', r['asset_identity'], TYPES[r['category']], r['territory'], r['indication'],
                        r['rights_type'], r['coverage_start'], r['coverage_end'], r['claim'], DIRECTIONS[r['direction']],
                        source.get('url', ''), source.get('source_date', ''), source.get('snapshot_date', ''),
                        r['source_integrity'], r['review_state'] if r['current'] else 'stale', r['missing'], r['next_action'], r['event_id']))
    for g in state['gaps']:
        records.append(('gap', g['asset_identity'], TYPES[g['category']], '', '', '', '', '', '', '', '', '', '', '', '', g['reason'], g['next_action'], ''))
    if fmt == 'csv':
        stream = io.StringIO(newline=''); writer = csv.writer(stream); writer.writerow(columns)
        for record in records:
            writer.writerow(["'" + str(v) if str(v).lstrip().startswith(('=', '+', '-', '@')) else str(v) for v in record])
        return ('\ufeff' + stream.getvalue()).encode()
    def escape(v):
        import html
        return html.escape(str(v)).replace('|', '&#124;').replace('\r', ' ').replace('\n', '<br>')
    lines = ['# 资产资料与缺失矩阵', '', state['scope'], '', '矩阵版本：' + state['matrix_hash'], '',
             '| ' + ' | '.join(columns) + ' |', '| ' + ' | '.join('---' for _ in columns) + ' |']
    lines.extend('| ' + ' | '.join(escape(v) for v in record) + ' |' for record in records)
    lines += ['', '完整审阅历史与记录人见 JSON 矩阵和完整项目导出。未知截止时间不表示当前有效；外部网页未在本次请求中重新核验。']
    return '\n'.join(lines).encode()


def context_summary(store, pid):
    """Projection for model context: no local paths, authors or full event history."""
    state = matrix(store, pid)
    rows = []
    for row in state['rows']:
        data = {k: row[k] for k in ('asset_id', 'asset_identity', 'category', 'direction', 'territory',
                'indication', 'rights_type', 'coverage_start', 'coverage_end', 'claim', 'limitations',
                'missing', 'next_action', 'source', 'current', 'source_integrity', 'review_state')}
        data['reference_id'] = 'assetclaim:' + row['event_id']
        data['citation_eligible'] = row['current'] and row['source'] is not None
        data['review_declarations'] = [{k: r[k] for k in ('judgment', 'reason', 'scope', 'counterevidence', 'next_action', 'current')}
                                       for r in row['reviews']]
        rows.append(data)
    return dict(schema_version=1, matrix_hash=state['matrix_hash'], rows=rows, gaps=state['gaps'],
                asset_status=state['asset_status'], scope=state['scope'])
