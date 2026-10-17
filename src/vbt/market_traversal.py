"""Traverse the current candidate universe through durable, bounded source jobs.

Readiness is a collection state, never a pharmaceutical classification approval,
an issuer-to-sponsor acceptance, or complete-pipeline coverage.
"""
import json
import uuid
from datetime import datetime, timedelta, timezone

from .io import now
from .market_jobs import init_schema as init_jobs, _enqueue_in_transaction
from .sponsor_resolution import init_schema as init_resolution, query_name_from_listing

CLASSES = ('drug_developer', 'candidate', 'uncertain', 'drug_developer_candidate')
LABELS = {'ready': '可排队检索', 'active': '已排队或正在检索', 'needs_name': '需要准确英文主体',
          'held_review': '已有建议、歧义或分页缺口待核对', 'retry_wait': '失败后等待重试',
          'zero_wait': '零结果等待下次复查', 'invalid_time': '来源时间待核对',
          'unknown_state': '检索状态待核对', 'source_running': '独立来源检索进行中'}


def _init(market):
    init_jobs(market)
    init_resolution(market)
    with market.db() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS market_traversal_batches (
          id TEXT PRIMARY KEY,created TEXT NOT NULL,market TEXT NOT NULL,
          batch_limit INTEGER NOT NULL,counts_json TEXT NOT NULL,jobs_json TEXT NOT NULL)''')


def _current(current):
    current = current or datetime.now(timezone.utc)
    if not isinstance(current, datetime) or current.tzinfo is None or current.utcoffset() is None:
        raise ValueError('Traversal clock requires an aware datetime')
    return current.astimezone(timezone.utc)


def _rows(market, market_filter):
    if market_filter not in ('', 'US', 'A', 'HK'):
        raise ValueError('Unknown market')
    _init(market)
    with market.db() as db:
        return [dict(r) for r in db.execute('''SELECT c.id,c.name,c.market,c.content_hash,
          r.id AS resolution_id,r.status AS resolution_status,r.started,
          j.id AS active_job,EXISTS(SELECT 1 FROM sponsor_resolution_runs old
              WHERE old.candidate_id=c.id) AS ever_queried
          FROM issuer_candidates c LEFT JOIN companies m ON m.id=c.id
          LEFT JOIN sponsor_resolution_runs r ON r.id=(SELECT r2.id FROM sponsor_resolution_runs r2
            WHERE r2.candidate_id=c.id AND r2.candidate_hash=c.content_hash
            ORDER BY r2.started DESC,r2.id DESC LIMIT 1)
          LEFT JOIN collection_jobs j ON j.kind='resolve' AND j.reference=c.id
            AND j.status IN ('queued','running','retry_wait')
          WHERE m.id IS NULL AND c.review_status!='rejected'
            AND c.classification IN ('drug_developer','candidate','uncertain','drug_developer_candidate')
            AND (?='' OR c.market=?) ORDER BY ever_queried,c.id''',
            (market_filter, market_filter))]


def _state(row, current):
    if row['active_job']:
        return 'active'
    if not query_name_from_listing(row['name'], row['market']):
        return 'needs_name'
    if not row['resolution_id']:
        return 'ready'
    status = row['resolution_status']
    if status in ('suggested', 'ambiguous', 'blocked', 'partial'):
        return 'held_review'
    if status == 'running':
        return 'source_running'
    if status not in ('failed', 'interrupted', 'cancelled', 'busy', 'zero'):
        return 'unknown_state'
    try:
        started = datetime.fromisoformat(row['started'].replace('Z', '+00:00'))
        if started.tzinfo is None or started.utcoffset() is None or started > current:
            return 'invalid_time'
    except (AttributeError, TypeError, ValueError):
        return 'invalid_time'
    interval = timedelta(days=30) if status == 'zero' else timedelta(minutes=5) if status == 'busy' else timedelta(hours=24)
    if current - started >= interval:
        return 'ready'
    return 'zero_wait' if status == 'zero' else 'retry_wait'


def readiness(market, market_filter='', *, current=None):
    current = _current(current)
    rows = _rows(market, market_filter)
    counts = {key: 0 for key in LABELS}
    for row in rows:
        row['readiness'] = _state(row, current)
        counts[row['readiness']] += 1
    return {'as_of': current.isoformat(), 'market': market_filter, 'total_unmapped': len(rows),
            'counts': counts, 'rows': rows}


def enqueue_traversal(market, limit=1000, market_filter='', *, current=None):
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
        raise ValueError('Traversal batch limit must be between 1 and 1000')
    snapshot = readiness(market, market_filter, current=current)
    selected = [r for r in snapshot['rows'] if r['readiness'] == 'ready']
    jobs, targets, changed = [], [], []
    batch_id = uuid.uuid4().hex
    with market.db() as db:
        db.execute('BEGIN IMMEDIATE')
        for row in selected:
            if len(jobs) >= limit:
                break
            candidate = db.execute('''SELECT c.content_hash,c.review_status,c.classification,
              EXISTS(SELECT 1 FROM companies m WHERE m.id=c.id) AS mapped
              FROM issuer_candidates c WHERE c.id=?''', (row['id'],)).fetchone()
            if (not candidate or candidate['content_hash'] != row['content_hash'] or candidate['mapped']
                    or candidate['review_status'] == 'rejected' or candidate['classification'] not in CLASSES):
                changed.append(row['id']); continue
            latest = db.execute('''SELECT id FROM sponsor_resolution_runs WHERE candidate_id=?
              AND candidate_hash=? ORDER BY started DESC,id DESC LIMIT 1''',
              (row['id'], row['content_hash'])).fetchone()
            if (latest['id'] if latest else None) != row['resolution_id']:
                changed.append(row['id']); continue
            if db.execute("SELECT 1 FROM collection_jobs WHERE kind='resolve' AND reference=? AND status IN ('queued','running','retry_wait')", (row['id'],)).fetchone():
                continue
            jobs.append(_enqueue_in_transaction(db, 'resolve', row['id']))
            targets.append(row['id'])
        payload = {'new_jobs': jobs, 'candidate_ids': targets, 'changed_candidates': changed}
        db.execute('INSERT INTO market_traversal_batches VALUES (?,?,?,?,?,?)',
                   (batch_id, now(), market_filter, limit, json.dumps(snapshot['counts']), json.dumps(payload)))
    after = readiness(market, market_filter, current=datetime.fromisoformat(snapshot['as_of']))['counts']
    return {'batch_id': batch_id, 'new_jobs': jobs, 'candidate_ids': targets,
            'changed_candidates': changed, 'before': snapshot['counts'],
            'after': after, 'remaining_ready': after['ready']}


def batches(market, limit=10):
    _init(market)
    with market.db() as db:
        return [dict(r) for r in db.execute('SELECT * FROM market_traversal_batches ORDER BY created DESC LIMIT ?',
                                           (min(max(int(limit), 1), 100),))]
