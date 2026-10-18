"""Read-only keyword screening from independently revalidated legal-name receipts.

This produces classification observations, never sponsor mappings, drug ownership,
human decisions, or a verified drug-developer census. Candidate-bound receipt IDs
and hashes deliberately stay out of the returned semantic candidate payload.
"""
from __future__ import annotations

from copy import deepcopy
import re
import sqlite3

from . import issuer_directory_names as names
from . import issuer_discovery as discovery
from .market_sources import URLS

VERSION = 'issuer-directory-screening-1'
_HEX = re.compile(r'[a-f0-9]{64}')


def _eligible(row):
    return (isinstance(row, dict) and row.get('market') == 'A'
            and isinstance(row.get('ticker'), str) and bool(re.fullmatch('[0-9]{6}', row['ticker']))
            and row.get('id') == 'A:' + row['ticker']
            and row.get('classification') in ('unclassified', 'uncertain')
            and isinstance(row.get('name'), str) and bool(row['name'].strip())
            and isinstance(row.get('reason'), str) and bool(row['reason'].strip())
            and not row.get('issuer_group') and not row.get('suggested_aliases')
            and isinstance(row.get('evidence'), list)
            and not any(isinstance(e, dict) and e.get('kind') == 'seed_identity_conflict' for e in row['evidence']))


def _matches(pattern, text):
    hits = {}
    for match in pattern.finditer(text):
        hits.setdefault(match[0].casefold(), match[0])
    return [hits[key] for key in sorted(hits)]


def _valid_proof(proof, current, base):
    return (isinstance(proof, dict) and proof.get('schema_version') == names.VERSION
            and proof.get('candidate_id') == base['id']
            and proof.get('candidate_hash') == current['content_hash']
            and proof.get('ticker') == base['ticker'] and proof.get('listing_name') == base['name']
            and proof.get('source') in names.SOURCES
            and proof.get('source_url') == URLS.get(proof.get('source'))
            and isinstance(proof.get('raw_sha256'), str) and bool(_HEX.fullmatch(proof['raw_sha256']))
            and type(proof.get('raw_bytes')) is int and proof['raw_bytes'] > 0
            and isinstance(proof.get('source_locator'), str) and bool(proof['source_locator'].strip())
            and isinstance(proof.get('english_name'), str) and bool(proof['english_name'].strip())
            and isinstance(proof.get('chinese_name'), str)
            and proof.get('scope') == 'exchange_published_issuer_legal_name_query_seed'
            and proof.get('human_sign_off') is False and proof.get('registry_alias_proven') is False
            and proof.get('drug_ownership_verified') is False)


def supplement_candidates(market, base_candidates):
    """Return independent observations; no DDL, candidate writes or collection.

    Bootstrap: refresh base candidates once, enrich directory receipts, supplement
    and refresh, then enrich for the new candidate hash. Once current receipts
    exist, read them before refreshing; never first downgrade to the base payload.
    """
    result = deepcopy(list(base_candidates))
    requested = {row['id']: row for row in result if _eligible(row)}
    if len(requested) != sum(_eligible(row) for row in result):
        # Duplicate semantic identities cannot select an unambiguous source row;
        # the existing refresh contract will reject that batch without mutation.
        return result
    if not requested:
        return result
    try:
        with market.db() as db:
            db.execute('PRAGMA query_only=ON')
            db.execute('BEGIN')
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='issuer_candidates'").fetchone():
                return result
            current = {}
            ids = list(requested)
            for offset in range(0, len(ids), 400):
                part = ids[offset:offset+400]
                for stored in db.execute('SELECT * FROM issuer_candidates WHERE id IN (' + ','.join('?' for _ in part) + ')', part):
                    row = dict(stored)
                    base = requested[row['id']]
                    if (row['market'] == 'A' and row['ticker'] == base['ticker'] and row['name'] == base['name']
                            and row['classification'] not in ('excluded', 'drug_developer')):
                        current[row['id']] = row
            # Exactly one batch call. The independent provider replays each source
            # once inside this same read snapshot, and rejects stale/conflicting raw.
            proofs = names.query_evidences(market, list(current.values()), db=db)
    except (ValueError, TypeError, KeyError, OSError, sqlite3.DatabaseError):
        return result
    for row in result:
        stored = current.get(row.get('id')) if isinstance(row, dict) else None
        proof = proofs.get(row.get('id')) if stored else None
        if not stored or not _valid_proof(proof, stored, row):
            continue
        english = proof['english_name']
        drug, broad = _matches(discovery._DRUG, english), _matches(discovery._BROAD, english)
        text = ' '.join((english, proof['chinese_name'], row['name'], str(row.get('industry') or '')))
        negative = {'service_business': _matches(discovery._CRO, text),
                    'devices_or_distribution': _matches(discovery._DEVICE, text)}
        mixed = any(negative.values())
        if not drug and not broad and not mixed:
            continue
        classification = 'candidate' if drug and not mixed else 'uncertain'
        reason = ('交易所英文法定名称含药物或疫苗关键词；仅增加待核候选线索，需另查研发管线和申办方。'
                  if classification == 'candidate' else
                  '交易所法定名称含生物医疗或服务/器械等混合业务线索；保持不确定，独立药物资产须另核。')
        # Explicit allowlist: never copy candidate_hash/receipt_id/source_run_id
        # into the candidate's own evidence, which would create a hash cycle.
        evidence = {'kind': 'issuer_directory_english_name_screening', 'method_version': VERSION,
                    'source_name_version': names.VERSION, 'source': proof['source'],
                    'source_url': proof['source_url'], 'raw_sha256': proof['raw_sha256'],
                    'raw_bytes': proof['raw_bytes'], 'source_locator': proof['source_locator'],
                    'english_name_original': english, 'chinese_name_original': proof['chinese_name'],
                    'keyword_hits': {'drug': drug, 'broad': broad, **negative},
                    'base_classification': row['classification'], 'base_reason': row['reason'],
                    'scope': 'legal_name_keyword_screening_only_not_sponsor_or_pipeline_attribution',
                    'review_status': 'pending', 'human_sign_off': False, 'scientific_review': 'pending',
                    'registry_alias_proven': False, 'drug_ownership_verified': False}
        row.update(classification=classification, reason=reason, rationale=reason,
                   directory_screening_version=VERSION, review_status='pending')
        row['evidence'].append(evidence)
    return result
