"""Strict A-share legal-name association, not a business/rights determination.

Only current sealed SZ/SH/STAR legal-name receipts can establish the exchange
side. A separately archived canonical NCT response supplies the exact sponsor.
All admission checks run again inside the caller's publication transaction.
"""
import datetime
import hashlib
import json
import re
import sqlite3
from pathlib import Path

from . import issuer_directory_names as names, market_sources
from .market import universe_digest
from .sponsor_resolution import candidate_query_context, proposal_query_issue, match_sponsor

REVIEWER_TYPE = 'a_share_directory_source_review'
SCOPE = 'listed_a_share_entity_to_exact_registry_sponsor_name_only'
VERSION = 'a-share-sealed-directory-identity-1'
MAX_SOURCE_AGE_DAYS = 7


def _stamp(value):
    from .sponsor_review import _stamp as parse
    return parse(value)


def _window(value, checked):
    stamp = _stamp(value)
    if stamp > checked + datetime.timedelta(minutes=5) or stamp < checked - datetime.timedelta(days=MAX_SOURCE_AGE_DAYS):
        raise ValueError('A-share identity source is stale or future-dated')


def admission_context(market, db, row, *, context=None, expected_status='pending'):
    """Current source/hash/query proof, read in the caller's SQLite snapshot."""
    if expected_status not in ('pending','accepted') or row['status'] != expected_status:
        raise ValueError('A-share proposal is no longer in the expected review state')
    stored=db.execute('SELECT * FROM sponsor_proposals WHERE id=?',(row['id'],)).fetchone()
    if not stored or any(stored[key]!=row[key] for key in ('status','candidate_id','candidate_hash','alias','match_type','source_url','run_id','evidence_nct')):
        raise ValueError('A-share proposal changed since its admission snapshot')
    if row['market'] != 'A' or row['classification'] not in ('candidate', 'drug_developer') or row['review_status'] == 'rejected':
        raise ValueError('A-share automation requires an eligible candidate, not uncertain/excluded business')
    if row['candidate_hash'] != row['current_hash'] or row['match_type'] != 'normalized_exact':
        raise ValueError('A-share candidate changed or sponsor match is not normalized_exact')
    run = db.execute('SELECT * FROM sponsor_resolution_runs WHERE id=?', (row['run_id'],)).fetchone()
    if not run or run['status'] != 'suggested' or run['truncated']:
        raise ValueError('A-share sponsor resolution must be complete and suggested')
    candidate = db.execute('SELECT * FROM issuer_candidates WHERE id=?', (row['candidate_id'],)).fetchone()
    if not candidate:
        raise ValueError('A-share candidate no longer exists')
    if any(candidate[key]!=row[key] for key in ('market','ticker','name','classification','review_status')):
        raise ValueError('A-share candidate eligibility changed since its admission snapshot')
    context = context if context is not None else candidate_query_context(market, dict(candidate), db=db)
    issue = proposal_query_issue(market, db, row, context=context)
    if issue:
        raise ValueError(issue)
    frozen = json.loads(run['query_evidence_json'])
    proof = context.get('directory_evidence')
    if (context.get('origin') != 'verified_exchange_english_name'
            or frozen.get('origin') != 'verified_exchange_english_name' or not isinstance(proof, dict)
            or proof.get('source') not in names.SOURCES or proof.get('schema_version') != names.VERSION
            or match_sponsor(proof.get('english_name'), row['alias']) != 'normalized_exact'):
        raise ValueError('A-share association requires the current verified SZ/SH/STAR full legal name')
    _window(proof['retrieved_at'], datetime.datetime.now(datetime.timezone.utc))
    ncts = json.loads(row['evidence_nct'])
    if (not isinstance(ncts, list) or not ncts or any(not isinstance(n, str) or not re.fullmatch(r'NCT[0-9]{8}', n) for n in ncts)
            or row['source_url'] not in {'https://clinicaltrials.gov/study/' + n for n in ncts}):
        raise ValueError('A-share suggestion has no canonical NCT evidence')
    return {**context, 'resolution_query_evidence_sha256': hashlib.sha256(run['query_evidence_json'].encode()).hexdigest()}


def directory_bytes(market, proof):
    """Read only the already verified capture, with a second byte/hash check."""
    rid = proof.get('source_run_id')
    if not isinstance(rid, str) or not re.fullmatch(r'[a-f0-9]{32}', rid):
        raise ValueError('Invalid sealed directory run')
    expected = Path('captures') / rid / '0.bin'
    if proof.get('capture_reference') != str(expected):
        raise ValueError('Directory receipt path changed')
    path = market.root / expected
    if path.resolve() != path.absolute() or not path.is_file():
        raise ValueError('Directory source path is missing or uses an alias')
    with path.open('rb') as handle:
        raw = handle.read(names.MAX_BYTES + 1)
    if len(raw) != proof['raw_bytes'] or len(raw) > names.MAX_BYTES or hashlib.sha256(raw).hexdigest() != proof['raw_sha256']:
        raise ValueError('Sealed directory bytes changed before identity capture')
    return raw


def validate(market, db, row, proposal_id, evidence, *, recovery=False):
    """Recheck current inputs and independently replay this run's two archives."""
    from .sponsor_review import _archive, _document
    current = admission_context(market, db, row, expected_status='accepted' if recovery else 'pending')
    required = {'status': 'verified', 'candidate_id': row['candidate_id'], 'proposal_id': proposal_id,
        'candidate_hash': row['current_hash'], 'alias': row['alias'], 'market': 'A', 'ticker': row['ticker'],
        'reviewer_type': REVIEWER_TYPE, 'scope': SCOPE, 'adapter_version': VERSION,
        'human_sign_off': False, 'drug_ownership_verified': False, 'drug_developer_status_verified': False,
        'full_pipeline_verified': False, 'scientific_review': 'pending', 'registry_source_url': row['source_url']}
    required['resolution_query_evidence_sha256'] = current['resolution_query_evidence_sha256']
    if not isinstance(evidence, dict) or any(k not in evidence or type(evidence[k]) is not type(v) or evidence[k] != v for k,v in required.items()):
        raise ValueError('A-share identity evidence is not bound to this proposal and narrow review scope')
    if not isinstance(evidence.get('supporting_fact'), str) or not 10 <= len(evidence['supporting_fact']) <= 5000:
        raise ValueError('Missing A-share source-name reconciliation fact')
    checked = _stamp(evidence.get('checked_at')); now = datetime.datetime.now(datetime.timezone.utc)
    if checked > now + datetime.timedelta(minutes=5) or checked < now - datetime.timedelta(days=30):
        raise ValueError('A-share identity review is stale or future-dated')
    age=(_stamp(evidence['checked_at'])-_stamp(evidence.get('exchange_fetched_at'))).total_seconds()
    if (evidence.get('directory_age_limit_days') != MAX_SOURCE_AGE_DAYS
            or type(evidence.get('directory_source_age_seconds')) not in (int,float)
            or evidence['directory_source_age_seconds'] != age):
        raise ValueError('A-share directory age must use original source retrieval time')
    proof = evidence.get('directory_name_receipt')
    if not isinstance(proof, dict) or proof.get('candidate_hash') != row['current_hash'] or proof.get('candidate_id') != row['candidate_id']:
        raise ValueError('Archived directory receipt does not bind this candidate')
    receipt_id = proof.get('receipt_id')
    ledger = db.execute('SELECT * FROM issuer_directory_name_receipts WHERE id=?', (receipt_id,)).fetchone()
    serial = names._serial({k:v for k,v in proof.items() if k not in ('receipt_id','ledger_id')})
    if (not ledger or proof.get('ledger_id') != receipt_id or ledger['payload_json'] != serial
            or ledger['payload_sha256'] != receipt_id or hashlib.sha256(serial.encode()).hexdigest() != receipt_id
            or proof.get('source') not in names.SOURCES or proof.get('source_url') != market_sources.URLS[proof['source']]
            or match_sponsor(proof.get('english_name'), row['alias']) != 'normalized_exact'
            or match_sponsor(current['directory_evidence']['english_name'], proof['english_name']) != 'normalized_exact'
            or evidence.get('verified_company_name') != proof['english_name']
            or evidence.get('official_source_url') != proof['source_url']):
        raise ValueError('Archived directory receipt differs from its immutable ledger or current exact legal name')
    _window(proof['retrieved_at'], checked)
    attempt=db.execute('SELECT * FROM identity_collection_runs WHERE id=?',(evidence.get('collector_run_id'),)).fetchone()
    if (not attempt or attempt['proposal_id']!=proposal_id or attempt['candidate_id']!=row['candidate_id']
            or attempt['candidate_hash']!=row['current_hash'] or attempt['status'] not in ('running','succeeded')):
        raise ValueError('A-share source proof is not bound to this identity run/proposal/candidate')
    return _archive_sources(market,row,evidence,proof,checked)


def _archive_sources(market,row,evidence,proof,checked,*,historical=False):
    from .sponsor_review import _archive,_document
    rid = evidence.get('collector_run_id')
    if not isinstance(rid, str) or not re.fullmatch(r'[a-f0-9]{32}', rid):
        raise ValueError('A-share identity proof lacks its run binding')
    folder = market.root / 'identity_review' / rid
    if folder.resolve() != folder.absolute():
        raise ValueError('A-share identity run cannot use a directory alias')

    def manifest(key):
        path = _archive(market, evidence.get(key))
        if path.parent != folder or path.stat().st_size > 1024*1024:
            raise ValueError('A-share source manifest is outside this run')
        return names._json(path.read_bytes())

    exchange = manifest('exchange_capture_manifest')
    records = manifest('evidence_capture_manifest')
    if not isinstance(exchange, dict) or not isinstance(records, list) or len(records) != 1 or not isinstance(records[0], dict):
        raise ValueError('A-share source manifests are invalid')
    for record, kind, url, stamp in ((exchange, 'exchange', proof['source_url'], proof['retrieved_at']),
            (records[0], 'registry', evidence.get('registry_api_url'), evidence.get('registry_fetched_at'))):
        if (_archive(market, record.get('path')).parent != folder or record.get('kind') != kind
                or record.get('url') != url or record.get('fetched_at') != stamp
                or type(record.get('bytes')) is not int or record['bytes'] <= 0):
            raise ValueError('A-share source record is not bound to this run/source/time')
        if historical:_stamp(stamp)
        else:_window(stamp, checked)
    if evidence.get('exchange_fetched_at') != proof['retrieved_at'] or evidence.get('exchange_sha256') != proof['raw_sha256']:
        raise ValueError('A-share directory capture is not the sealed source version')
    raw = _document(market, exchange, evidence['exchange_sha256'], names.MAX_BYTES)
    rows,total = market_sources.parse_universe(proof['source'], raw)
    if (len(raw) != proof['raw_bytes'] or len(rows) != proof['directory_count'] or (total is not None and total != len(rows))
            or universe_digest(rows) != proof['rows_sha256']):
        raise ValueError('A-share archived directory does not replay its sealed count/hash')
    entry = names._extract(proof['source'], raw).get(row['ticker'])
    if (not entry or entry['field_error'] or entry['english_name'] != proof['english_name']
            or entry['listing_name'] != row['name'] or proof.get('listing_name') != row['name']):
        raise ValueError('A-share archived named fields do not prove this exact listed entity')
    registry = records[0]
    nct = row['source_url'].removeprefix('https://clinicaltrials.gov/study/')
    if (not re.fullmatch(r'NCT[0-9]{8}', nct) or evidence.get('registry_api_url') != 'https://clinicaltrials.gov/api/v2/studies/' + nct
            or registry.get('ticker') != row['ticker']):
        raise ValueError('A-share registry evidence is not the canonical proposal NCT')
    registry_raw = _document(market, registry, evidence.get('registry_sha256'))
    data = names._json(registry_raw)
    if not isinstance(data,dict) or not isinstance(data.get('protocolSection'),dict):
        raise ValueError('Canonical NCT response has no protocol object')
    protocol = data['protocolSection']
    identification=protocol.get('identificationModule');sponsors=protocol.get('sponsorCollaboratorsModule')
    if (not isinstance(identification,dict) or not isinstance(sponsors,dict) or not isinstance(sponsors.get('leadSponsor'),dict)
            or identification.get('nctId') != nct or sponsors['leadSponsor'].get('name') != row['alias']):
        raise ValueError('Canonical NCT does not name the exact proposed lead sponsor')
    return {'exchange':raw,'registry':registry_raw}


def _evidence_file(market,run_id,kind):
    """Read historical typed proof/bytes without re-approving a current mapping.

    No DDL, recovery, leases, network or current-candidate/name-snapshot lookup.
    The accepted typed review and immutable name receipt bind the old run.
    """
    from .sponsor_review import _archive
    if not re.fullmatch(r'[a-f0-9]{32}',str(run_id)) or kind not in ('proof','exchange','registry'):
        raise ValueError('Unknown A-share identity evidence attachment')
    with market.db() as db:
        db.execute('BEGIN')
        run=db.execute("SELECT * FROM identity_collection_runs WHERE id=? AND status='succeeded'",(run_id,)).fetchone()
        if not run or not run['review_id'] or not re.fullmatch(r'[a-f0-9]{64}',str(run['evidence_sha256'])):
            raise ValueError('No completed A-share identity proof')
        review=db.execute('''SELECT r.*,e.reviewer_type,e.evidence_json,e.evidence_sha256
          FROM sponsor_proposal_reviews r JOIN sponsor_review_evidence e ON e.review_id=r.id
          WHERE r.id=? AND r.decision='accepted' ''',(run['review_id'],)).fetchone()
        folder=market.root/'identity_review'/run_id
        if folder.resolve()!=folder.absolute():raise ValueError('A-share identity folder alias rejected')
        path=_archive(market,run['evidence_path'])
        if path.parent!=folder or path.stat().st_size>1024*1024:raise ValueError('A-share proof path exceeds its run')
        proof_raw=path.read_bytes()
        if (not review or review['reviewer_type']!=REVIEWER_TYPE or review['proposal_id']!=run['proposal_id']
                or review['candidate_hash']!=run['candidate_hash'] or review['evidence_sha256']!=run['evidence_sha256']
                or hashlib.sha256(proof_raw).hexdigest()!=run['evidence_sha256']
                or hashlib.sha256(review['evidence_json'].encode()).hexdigest()!=run['evidence_sha256']):
            raise ValueError('A-share historical proof lacks its matching accepted typed review')
        evidence=names._json(proof_raw)
        required={'status':'verified','reviewer_type':REVIEWER_TYPE,'scope':SCOPE,'adapter_version':VERSION,
            'collector_run_id':run_id,'candidate_id':run['candidate_id'],'candidate_hash':run['candidate_hash'],
            'proposal_id':run['proposal_id'],'market':'A','human_sign_off':False,'drug_ownership_verified':False,
            'drug_developer_status_verified':False,'full_pipeline_verified':False,'scientific_review':'pending'}
        if (not isinstance(evidence,dict) or any(k not in evidence or type(evidence[k]) is not type(v) or evidence[k]!=v for k,v in required.items())
                or not re.fullmatch(r'[0-9]{6}',str(evidence.get('ticker')))
                or evidence['candidate_id']!='A:'+evidence['ticker'] or run['company']!=run['candidate_id']):
            raise ValueError('A-share historical proof scope/run identity changed')
        receipt=evidence.get('directory_name_receipt')
        if not isinstance(receipt,dict):raise ValueError('A-share historical name receipt is missing')
        serial=names._serial({k:v for k,v in receipt.items() if k not in ('receipt_id','ledger_id')})
        rid=receipt.get('receipt_id')
        ledger=db.execute('SELECT * FROM issuer_directory_name_receipts WHERE id=?',(rid,)).fetchone()
        if (not ledger or ledger['payload_json']!=serial or ledger['payload_sha256']!=rid
                or hashlib.sha256(serial.encode()).hexdigest()!=rid or receipt.get('ledger_id')!=rid
                or receipt.get('candidate_id')!=run['candidate_id'] or receipt.get('candidate_hash')!=run['candidate_hash']
                or receipt.get('source') not in names.SOURCES or receipt.get('source_url')!=market_sources.URLS[receipt['source']]
                or evidence.get('official_source_url')!=receipt['source_url']
                or evidence.get('verified_company_name')!=receipt.get('english_name')
                or match_sponsor(receipt.get('english_name'),evidence.get('alias'))!='normalized_exact'):
            raise ValueError('A-share historical receipt differs from its immutable source record')
        checked=_stamp(evidence.get('checked_at'))
        row={'ticker':evidence['ticker'],'name':receipt['listing_name'],
            'source_url':evidence['registry_source_url'],'alias':evidence['alias']}
        sources=_archive_sources(market,row,evidence,receipt,checked,historical=True)
    if kind=='proof':return proof_raw,'application/json; charset=utf-8'
    if kind=='registry':return sources[kind],'application/json; charset=utf-8'
    mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' if receipt['source']=='SZ' else 'application/json; charset=utf-8'
    return sources[kind],mime


def evidence_file(market,run_id,kind):
    """Public fail-closed historical reader; never initializes missing storage."""
    try:
        return _evidence_file(market,run_id,kind)
    except (sqlite3.DatabaseError,KeyError,TypeError,AttributeError) as exc:
        raise ValueError('A-share historical proof is incomplete or malformed') from exc
