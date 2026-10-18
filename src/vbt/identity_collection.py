"""Fresh, archived, fenced US and sealed-directory A-share name reconciliation.

This collector verifies two independently parsed public sources before accepting
an exact sponsor-name suggestion. It does not verify drug ownership, a complete
portfolio, scientific validity, or a human signature. Uncertain classifications
and translated/subsidiary/fuzzy names remain available for explicit review.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
import uuid

from .io import now
from .sponsor_resolution import match_sponsor, query_name_from_listing
from .sponsor_review import (
    _archive, _NASDAQ_URL, _OTHER_URL, _validate_automated,
    exchange_identity, init_review_schema, review_in_transaction,
)

DIRECTORY_URLS = (_NASDAQ_URL, _OTHER_URL)
NCT_API = 'https://clinicaltrials.gov/api/v2/studies/'
ELIGIBLE_CLASSIFICATIONS = ('candidate', 'drug_developer')


class Cancelled(Exception):
    pass


class Superseded(Exception):
    pass


def validate_url(url):
    if url not in DIRECTORY_URLS and not re.fullmatch(re.escape(NCT_API) + r'NCT[0-9]{8}', str(url)):
        raise ValueError('Identity collection permits fixed official directories and canonical NCT records only')


def fetch(url):
    """System TLS, no redirects/config files/credentials, bounded body and time."""
    validate_url(url)
    env = {k: v for k, v in os.environ.items() if k in
           ('PATH', 'HOME', 'SSL_CERT_FILE', 'CURL_CA_BUNDLE', 'HTTPS_PROXY', 'HTTP_PROXY', 'NO_PROXY')}
    args = ['curl', '--disable', '--silent', '--show-error', '--fail', '--max-time', '30',
            '--max-filesize', '8388608', '--max-redirs', '0', '--proto', '=https',
            '--retry', '2', '--retry-max-time', '65', '--write-out', '\n%{http_code}',
            '--user-agent', 'EmerisBiotechResearch/0.3', url]
    result = subprocess.run(args, capture_output=True, timeout=100, env=env)
    if result.returncode:
        raise OSError('Identity source request failed: ' + result.stderr.decode(errors='replace')[-300:])
    raw, separator, status = result.stdout.rpartition(b'\n')
    if not separator or status != b'200' or not raw or len(raw) > 8 * 1024 * 1024:
        raise ValueError('Identity source response rejected; HTTP 200 and bounded bytes required')
    return raw


_DEFAULT_FETCH = fetch

def _event(db, run_id, event, details=''):
    db.execute('INSERT INTO identity_collection_events VALUES (?,?,?,?,?)',
               (uuid.uuid4().hex, run_id, now(), event, str(details)[:2000]))


def init_schema(market):
    init_review_schema(market)
    with market.db() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS identity_collection_runs (
          id TEXT PRIMARY KEY, proposal_id TEXT NOT NULL, candidate_id TEXT NOT NULL,
          candidate_hash TEXT NOT NULL, started TEXT NOT NULL, finished TEXT,
          status TEXT NOT NULL, error TEXT NOT NULL DEFAULT '', company TEXT,
          review_id TEXT, lease_owner TEXT NOT NULL DEFAULT '', evidence_path TEXT,
          evidence_sha256 TEXT, recovered INTEGER NOT NULL DEFAULT 0);
        CREATE INDEX IF NOT EXISTS identity_collection_proposal
          ON identity_collection_runs(proposal_id,started);
        CREATE TABLE IF NOT EXISTS identity_collection_events (
          id TEXT PRIMARY KEY, run_id TEXT NOT NULL, created TEXT NOT NULL,
          event TEXT NOT NULL, details TEXT NOT NULL);
        CREATE TRIGGER IF NOT EXISTS identity_events_no_update
          BEFORE UPDATE ON identity_collection_events BEGIN
            SELECT RAISE(ABORT,'Identity events are append only'); END;
        CREATE TRIGGER IF NOT EXISTS identity_events_no_delete
          BEFORE DELETE ON identity_collection_events BEGIN
            SELECT RAISE(ABORT,'Identity events are append only'); END;
        ''')
        db.execute('BEGIN IMMEDIATE')
        lost = db.execute('''SELECT * FROM identity_collection_runs r
          WHERE r.status='running' AND NOT EXISTS
           (SELECT 1 FROM leases l WHERE l.key='identity:' || r.proposal_id
             AND l.owner=r.lease_owner AND l.until>?)''', (time.time(),)).fetchall()
        for row in lost:
            # Publication and success are one transaction in this implementation.
            # Reconciliation also handles an older/interrupted caller that wrote
            # an accepted review before recording its run completion.
            accepted = _accepted_review(market, db, row)
            status = 'succeeded' if accepted else 'interrupted'
            error = '' if accepted else 'Identity lease expired/lost; fresh-source retry required'
            db.execute('''UPDATE identity_collection_runs SET status=?,finished=?,error=?,
              company=?,review_id=?,recovered=1 WHERE id=? AND status='running' ''',
                       (status, now(), error, accepted['company'] if accepted else None,
                        accepted['review_id'] if accepted else None, row['id']))
            _event(db, row['id'], 'recovered_' + status, error)


def _accepted_review(market, db, run):
    """Never infer success just from a proposal's accepted status."""
    if not run['evidence_sha256'] or not run['evidence_path']:
        return None
    try:
        path = _archive(market, run['evidence_path'])
        if path.stat().st_size > 1024 * 1024 or hashlib.sha256(path.read_bytes()).hexdigest() != run['evidence_sha256']:
            return None
    except (ValueError, OSError, TypeError):
        return None
    row = db.execute('''SELECT p.*,c.market,c.ticker,c.name,c.classification,
      c.content_hash AS current_hash,c.review_status,r.id AS review_id,
      e.evidence_json,e.evidence_sha256,e.reviewer_type
      FROM sponsor_proposals p JOIN issuer_candidates c ON c.id=p.candidate_id
      JOIN sponsor_proposal_reviews r ON r.proposal_id=p.id AND r.decision='accepted'
      JOIN sponsor_review_evidence e ON e.review_id=r.id
      JOIN companies m ON m.id=p.candidate_id
      WHERE p.id=? AND p.status='accepted' AND p.candidate_hash=?
        AND r.candidate_hash=p.candidate_hash
        AND c.content_hash=p.candidate_hash AND e.reviewer_type IN ('automated_source_review','a_share_directory_source_review')
        AND e.evidence_sha256=? AND EXISTS
          (SELECT 1 FROM json_each(m.aliases) WHERE value=p.alias)
      ORDER BY r.created DESC LIMIT 1''',
                     (run['proposal_id'], run['candidate_hash'], run['evidence_sha256'])).fetchone()
    if not row or (run['review_id'] and row['review_id'] != run['review_id']) or (run['company'] and row['candidate_id'] != run['company']):
        return None
    try:
        if hashlib.sha256(row['evidence_json'].encode()).hexdigest() != row['evidence_sha256']:
            return None
        evidence = json.loads(row['evidence_json'])
        if evidence.get('collector_run_id')!=run['id'] or row['candidate_id']!=run['candidate_id']:
            return None
        if row['reviewer_type']=='a_share_directory_source_review':
            from .a_share_identity import validate
            validate(market, db, row, run['proposal_id'], evidence, recovery=True)
        else:
            _validate_automated(market, row, run['proposal_id'], evidence)
    except (ValueError, OSError, KeyError, TypeError):
        return None
    return {'company': row['candidate_id'], 'review_id': row['review_id']}


def runs(market, proposal_id=None, *, limit=100, offset=0):
    if not 1 <= limit <= 500 or offset < 0:
        raise ValueError('Invalid identity run pagination')
    init_schema(market)
    clause, params = (' WHERE proposal_id=?', [proposal_id]) if proposal_id else ('', [])
    with market.db() as db:
        return [dict(r) for r in db.execute('SELECT * FROM identity_collection_runs' + clause +
                ' ORDER BY started DESC,id DESC LIMIT ? OFFSET ?', params + [limit, offset])]


def events(market, run_id):
    init_schema(market)
    with market.db() as db:
        return [dict(r) for r in db.execute('SELECT * FROM identity_collection_events WHERE run_id=? ORDER BY created,id', (run_id,))]


def eligible_proposals(market, *, limit=100, offset=0):
    """Conservative automation queue; returns IDs, not approval probabilities."""
    if not 1 <= limit <= 500 or offset < 0:
        raise ValueError('Invalid identity proposal pagination')
    init_schema(market)
    with market.db() as db:
        db.execute('BEGIN')
        rows = [dict(r) for r in db.execute('''SELECT p.*,c.name,c.ticker,c.classification,
          c.market,c.review_status,c.content_hash AS current_hash,r.status AS resolution_status,r.truncated FROM sponsor_proposals p
          JOIN issuer_candidates c ON c.id=p.candidate_id
          JOIN sponsor_resolution_runs r ON r.id=p.run_id
          WHERE p.status='pending' AND c.market IN ('US','A') AND c.review_status!='rejected'
            AND c.classification IN ('candidate','drug_developer')
            AND p.candidate_hash=c.content_hash AND p.match_type='normalized_exact'
            AND r.status='suggested' AND r.truncated=0
            AND NOT EXISTS (SELECT 1 FROM companies m WHERE m.id=c.id)
          ORDER BY p.created,p.id''')]
        from .sponsor_resolution import candidate_query_contexts
        from .a_share_identity import admission_context
        a_ids = {r['candidate_id'] for r in rows if r['market']=='A'}
        a_rows = [dict(r) for r in db.execute("SELECT * FROM issuer_candidates WHERE market='A'") if r['id'] in a_ids] if a_ids else []
        contexts = candidate_query_contexts(market,a_rows,db=db)
        admitted = []
        for row in rows:
            if row['market']=='A':
                try:admission_context(market,db,row,context=contexts.get(row['candidate_id'],{}))
                except (ValueError,KeyError,TypeError,OSError):continue
            admitted.append(row)
        return admitted[offset:offset+limit]


def sources(market, *, limit=100, offset=0):
    """Eligible proposal source IDs for collectors that use source-style APIs."""
    return [{'id': p['id'], 'proposal_id': p['id'], **p} for p in eligible_proposals(market, limit=limit, offset=offset)]


def _row(db, proposal_id):
    return db.execute('''SELECT p.*,c.market,c.ticker,c.name,c.classification,
      c.content_hash AS current_hash,c.review_status,r.status AS resolution_status,r.truncated
      FROM sponsor_proposals p JOIN issuer_candidates c ON c.id=p.candidate_id
      JOIN sponsor_resolution_runs r ON r.id=p.run_id WHERE p.id=?''', (proposal_id,)).fetchone()


def _gate(row):
    if row['status'] != 'pending':
        return 'Proposal already handled; inspect the attributed review history'
    if row['candidate_hash'] != row['current_hash']:
        return 'Candidate changed; fresh sponsor resolution required'
    if row['market'] != 'US':
        return 'Automatic identity parser currently supports US official directories only'
    if row['classification'] not in ELIGIBLE_CLASSIFICATIONS or row['review_status'] == 'rejected':
        return 'Uncertain/excluded classification requires explicit human review'
    if row['match_type'] != 'normalized_exact' or row['resolution_status'] != 'suggested' or row['truncated']:
        return 'Ambiguous, partial or non-exact sponsor suggestions require explicit human review'
    if match_sponsor(query_name_from_listing(row['name'], row['market']), row['alias']) != 'normalized_exact':
        return 'Current issuer name does not match the exact sponsor name'
    try:
        ncts = json.loads(row['evidence_nct'])
    except (ValueError, TypeError):
        return 'Invalid NCT evidence list'
    if not isinstance(ncts, list) or not ncts or any(not isinstance(n, str) or not re.fullmatch(r'NCT[0-9]{8}', n) for n in ncts):
        return 'Invalid NCT evidence list'
    if row['source_url'] not in {'https://clinicaltrials.gov/study/' + n for n in ncts}:
        return 'Proposal source does not identify its canonical evidence study'
    return ''


def _result(db, run_id):
    result = dict(db.execute('SELECT * FROM identity_collection_runs WHERE id=?', (run_id,)).fetchone())
    result['run_id'] = result['id']
    return result


def _finish(market, run_id, token, status, error=''):
    with market.db() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM identity_collection_runs WHERE id=?', (run_id,)).fetchone()
        if row['status'] == 'running' and row['lease_owner'] == token:
            db.execute("UPDATE identity_collection_runs SET status=?,finished=?,error=? WHERE id=? AND status='running' AND lease_owner=?",
                       (status, now(), str(error)[:1500], run_id, token))
            _event(db, run_id, status, error)
        return _result(db, run_id)


def collect(market, proposal_id, fetch=fetch, cancelled=lambda: False, on_start=lambda rid: None):
    """Capture both official sources, then atomically publish review and success.

 Every retry receives a new immutable capture folder and run record. A prior
 successful run is idempotently returned only if its typed review, raw archived
 proof, current candidate hash and mapping remain consistent. Failed, cancelled,
 stale, or superseded attempts never publish a mapping.
    """
    if not isinstance(proposal_id, str) or not re.fullmatch(r'[0-9a-f]{32}', proposal_id):
        raise ValueError('Invalid sponsor proposal ID')
    init_schema(market)
    with market.db() as db:
        db.execute('BEGIN')
        row = _row(db, proposal_id)
        if not row:
            raise ValueError('Unknown sponsor proposal')
        prior = db.execute("SELECT * FROM identity_collection_runs WHERE proposal_id=? AND status='succeeded' ORDER BY started DESC LIMIT 1", (proposal_id,)).fetchone()
        if prior and _accepted_review(market, db, prior):
            return _result(db, prior['id'])
        row = dict(row)
        a_context = None
        if row['market']=='A':
            from .a_share_identity import admission_context
            try:a_context=admission_context(market,db,row);blocked=''
            except (ValueError,KeyError,TypeError,OSError) as exc:blocked=str(exc)
        else:blocked=_gate(row)
    run_id, key = uuid.uuid4().hex, 'identity:' + proposal_id
    claimed = not blocked and market.claim(key)
    token = market.lease_tokens()[key] if claimed else ''
    status = 'blocked' if blocked else 'running' if claimed else 'busy'
    try:
        with market.db() as db:
            db.execute('''INSERT INTO identity_collection_runs
              (id,proposal_id,candidate_id,candidate_hash,started,finished,status,error,lease_owner)
              VALUES (?,?,?,?,?,?,?,?,?)''',
                       (run_id, proposal_id, row['candidate_id'], row['candidate_hash'], now(),
                        None if claimed else now(), status, blocked or ('' if claimed else 'Identity lease active'), token))
            _event(db, run_id, status, blocked)
            # Persist run-to-job linkage in the same transaction as creation.
            # A process killed before on_start must still be recoverable without
            # guessing that another same-proposal collector owns its lease.
            queued=getattr(market._job_context,'job',None)
            if queued and db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='collection_jobs'").fetchone():
                db.execute("UPDATE collection_jobs SET run_id=? WHERE id=? AND kind='identity' AND reference=? AND attempts=? AND owner_pid=? AND status='running'",
                           (run_id,queued['id'],proposal_id,queued['attempts'],os.getpid()))
    except Exception:
        if claimed:
            market.release(key, token)
        raise
    if not claimed:
        with market.db() as db:
            return _result(db, run_id)
    folder = market.root / 'identity_review' / run_id

    def guard(db):
        if cancelled():
            raise Cancelled('Cancelled before identity publication; no mapping published')
        if not db.execute('SELECT 1 FROM leases WHERE key=? AND owner=? AND until>?', (key, token, time.time())).fetchone():
            raise Superseded('Identity lease no longer owned; no mapping published')
        current = db.execute("SELECT status,lease_owner FROM identity_collection_runs WHERE id=?", (run_id,)).fetchone()
        if not current or current['status'] != 'running' or current['lease_owner'] != token:
            raise Superseded('Identity attempt no longer running; no mapping published')

    def save(filename, body, url=None, **extra):
        path = folder / filename
        with path.open('xb') as output:
            output.write(body)
            output.flush()
            os.fsync(output.fileno())
        record = {'path': 'artifacts/market/identity_review/' + run_id + '/' + filename,
                  'status': 200, 'bytes': len(body), 'sha256': hashlib.sha256(body).hexdigest(), **extra}
        if url:
            record.update(url=url, fetched_at=now())
        return record

    def capture(filename, url, **extra):
        validate_url(url)
        if cancelled():
            raise Cancelled('Cancelled before identity source fetch')
        market.touch(key, token)
        if fetch is _DEFAULT_FETCH:
            from .source_rate import wait_slot
            try:
                wait_slot(market.state, url, cancelled=cancelled)
            except InterruptedError as exc:
                raise Cancelled(str(exc)) from exc
        raw = fetch(url)
        if not isinstance(raw, bytes) or not raw or len(raw) > 8 * 1024 * 1024:
            raise ValueError('Identity source must be nonempty bytes within 8 MiB')
        record = save(filename, raw, url, **extra)
        with market.db() as db:
            _event(db, run_id, 'captured', json.dumps(record, ensure_ascii=False, sort_keys=True))
        if cancelled():
            raise Cancelled('Cancelled after identity capture; no mapping published')
        return raw, record

    try:
        on_start(run_id)
        if cancelled():
            raise Cancelled('Cancelled before identity source fetch')
        folder.mkdir(parents=True, exist_ok=False)
        selected = None
        if a_context:
            from .a_share_identity import directory_bytes
            proof=a_context['directory_evidence']
            raw=directory_bytes(market,proof)
            if cancelled():raise Cancelled('Cancelled before sealed directory capture')
            exchange=save('a-share-directory.bin',raw,kind='exchange',ticker=row['ticker'])
            exchange.update(url=proof['source_url'],fetched_at=proof['retrieved_at'])
            with market.db() as db:_event(db,run_id,'captured',json.dumps(exchange,ensure_ascii=False,sort_keys=True))
        else:
            for index, url in enumerate(DIRECTORY_URLS):
                raw, exchange = capture('directory-' + str(index) + '.txt', url, kind='exchange')
                selected = exchange_identity(raw, url, row['ticker'], row['alias'])
                if selected is not None:
                    break
            if selected is None:
                raise ValueError('Current ticker absent from both official security directories')
        exchange_manifest = save('exchange_manifest.json', json.dumps(exchange, ensure_ascii=False, sort_keys=True).encode())
        nct = row['source_url'].removeprefix('https://clinicaltrials.gov/study/')
        registry_url = NCT_API + nct
        registry_raw, registry = capture('registry.json', registry_url, kind='registry', ticker=row['ticker'])
        manifest = save('capture_manifest.json', json.dumps([registry], ensure_ascii=False, sort_keys=True).encode())
        checked = now()
        evidence = {'status': 'verified', 'candidate_id': row['candidate_id'], 'proposal_id': proposal_id,
                    'candidate_hash': row['candidate_hash'], 'alias': row['alias'],
                    'verified_company_name': proof['english_name'] if a_context else query_name_from_listing(selected['Security Name'], 'US'),
                    'market': row['market'], 'ticker': row['ticker'], 'official_source_url': exchange['url'],
                    'registry_source_url': row['source_url'], 'registry_api_url': registry_url,
                    'supporting_fact': 'Fresh official security directory matches the exact ticker and normalized legal issuer name; '
                                       'the fresh canonical NCT record names the exact proposed lead sponsor. '
                                       'This verifies source-name association only; human sign-off, drug ownership and full portfolio coverage remain unverified.',
                    'checked_at': checked, 'exchange_fetched_at': exchange['fetched_at'],
                    'registry_fetched_at': registry['fetched_at'], 'exchange_sha256': exchange['sha256'],
                    'registry_sha256': registry['sha256'], 'exchange_capture_manifest': exchange_manifest['path'],
                    'evidence_capture_manifest': manifest['path'], 'reviewer_type': 'automated_source_review',
                    'scope': 'listed_entity_to_exact_registry_sponsor_name_only',
                    'human_sign_off': False, 'drug_ownership_verified': False,
                    'exchange_security_name': proof['listing_name'] if a_context else selected['Security Name'], 'collector_run_id': run_id}
        if a_context:
            from .a_share_identity import REVIEWER_TYPE,SCOPE,VERSION,MAX_SOURCE_AGE_DAYS
            from .sponsor_review import _stamp
            evidence.update(reviewer_type=REVIEWER_TYPE,scope=SCOPE,adapter_version=VERSION,
                directory_name_receipt=proof,directory_age_limit_days=MAX_SOURCE_AGE_DAYS,
                directory_source_age_seconds=(_stamp(checked)-_stamp(proof['retrieved_at'])).total_seconds(),
                resolution_query_evidence_sha256=a_context['resolution_query_evidence_sha256'],
                drug_developer_status_verified=False,full_pipeline_verified=False,scientific_review='pending',
                supporting_fact='The current sealed SZ/SH/STAR directory names this exact listed entity and English legal name; '
                  'the newly fetched canonical NCT names the exact proposed lead sponsor. '
                  'This is bounded-age source-name association, not a real-time directory, business classification approval, '
                  'human sign-off, drug ownership, scientific review or complete pipeline coverage.')
        payload = json.dumps(evidence, ensure_ascii=False, sort_keys=True)
        saved = save('evidence.json', payload.encode())
        with market.db() as db:
            db.execute('BEGIN IMMEDIATE')
            guard(db)
            current = _row(db, proposal_id)
            if current and a_context:
                from .a_share_identity import admission_context
                admission_context(market,db,current);gap=''
            else:gap = _gate(current) if current else 'Proposal missing during publication'
            if gap:
                raise ValueError(gap)
            result = review_in_transaction(market, db, proposal_id, 'accepted', 'Official source identity collector',
                    evidence['supporting_fact'], reviewer_type=evidence['reviewer_type'], evidence=evidence, guard=guard)
            guard(db)
            db.execute("UPDATE identity_collection_runs SET status='succeeded',finished=?,company=?,review_id=?,evidence_path=?,evidence_sha256=? WHERE id=? AND lease_owner=? AND status='running'",
                       (now(), result['company'], result['review_id'], saved['path'], saved['sha256'], run_id, token))
            _event(db, run_id, 'succeeded', 'Atomic exact-name association and typed review; not human sign-off or drug ownership')
            return _result(db, run_id)
    except Cancelled as exc:
        return _finish(market, run_id, token, 'cancelled', str(exc))
    except Superseded as exc:
        return _finish(market, run_id, token, 'superseded', str(exc))
    except Exception as exc:
        return _finish(market, run_id, token, 'failed', type(exc).__name__ + ': ' + str(exc))
    finally:
        market.release(key, token)
