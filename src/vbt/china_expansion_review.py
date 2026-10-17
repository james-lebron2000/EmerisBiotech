"""Independent, capture-bound gate for the second A/HK source-name batch.

The reviewed row digests below bind a specific configuration to primary sources
read by a separate agent.  A receipt is not a human signature, scientific review,
rights determination, or evidence of complete pipeline coverage.  Deployments
without the original captured evidence fail closed and return actionable gaps.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .china_issuer_sources import matches_listing
from .io import now, within

VERSION = 'china-v2-independent-source-name-audit-2026-10-17'
CAPTURE_PREFIX = Path('artifacts/market/china_expansion_v2/2026-10-17')
MAX_CAPTURE_BYTES = 20_000_000

# These receipts are version-specific: changing any mapping/evidence field
# requires another independent review, never regeneration at runtime.
_REVIEWED_ROWS = {'HK:06660': {'row_sha256': '40816776a007c36887f51c6175faccd7bb5bb4737eaab9db9237e38b6fa7dfc2',
              'relationship_to_issuer': 'issuer_legal_name',
              'note': 'Official issuer site identifies AIM Vaccine Co., Ltd. and 06660.HK. Existing pending '
                      'proposal must remain pending until its explicit review flow completes.'},
 'HK:09939': {'row_sha256': '2c785b0bc92c63fbbbf877ef32f52abb264e465b7bd8618541016cf675475d03',
              'relationship_to_issuer': 'subsidiary',
              'note': '2026-03-17 HKEX filing is issued by counterparty Shanghai Chicmax (2145), not Kintor. '
                      'It expressly names Suzhou Kintor Pharmaceutical Inc. as a wholly-owned subsidiary of '
                      'Kintor (9939). Kintor 2021 results definitions independently confirm the singular '
                      'Pharmaceutical spelling; only punctuation differs from the exact registry alias.'},
 'HK:09966': {'row_sha256': 'fb2b8d0ef644a61f6e758474dbbe6d40b155658b55956cbd4aad529102af5263',
              'relationship_to_issuer': 'subsidiary',
              'note': '2025 annual report definitions identify Jiangsu Alphamab Biopharmaceuticals Co., Ltd. '
                      'as wholly owned. This is the named operating subsidiary, not any generic Alphamab '
                      'collaborator.'},
 'HK:01167': {'row_sha256': 'ca33160cb5d691b854aab022c5b1ac7af89dd0472326c480795a4b6a77777c22',
              'relationship_to_issuer': 'subsidiary',
              'note': '2025 annual report defines Beijing Jacobio as an indirect wholly-owned subsidiary. Do '
                      'not substitute the divested Jacoray company.'},
 'HK:06855': {'row_sha256': '816114d5eb6477b92e84de0f5e773063acf688ab520c63fdd9075a2b54d7e216',
              'relationship_to_issuer': 'subsidiary',
              'note': '2025 annual report definitions and subsidiary table identify Ascentage Pharma Group '
                      'Inc. (United States) as an indirect wholly-owned subsidiary conducting clinical '
                      'trials.'},
 'HK:02137': {'row_sha256': 'da49c31a2a8dbc70635949aefc8c2456a93367213fde07d83fd22f45882b875c',
              'relationship_to_issuer': 'issuer_legal_name',
              'note': 'Issuer corporate overview identifies Brii Biosciences Limited and 2137.HK.'},
 'HK:02157': {'row_sha256': '3792208796356f051b4da21502e8fc8883a61144c714eaf2c4f90f2c41354154',
              'relationship_to_issuer': 'issuer_legal_name',
              'note': 'Official issuer profile and announcement identify Lepu Biopharma Co., Ltd. and '
                      '02157.HK.'},
 'HK:02171': {'row_sha256': 'e2d365d75ca573322fc66de125b3677f60f48a1afb2517199ab8ced75d4b8b7c',
              'relationship_to_issuer': 'consolidated_affiliated_entity',
              'note': '2024 annual report contractual-arrangements section and entity definitions identify '
                      'CARsgen Therapeutics Co., Ltd. as a consolidated affiliated entity under contractual '
                      'control. Its registered shareholders hold the equity; do not label it an equity-owned '
                      'subsidiary or infer drug rights.'},
 'HK:00013': {'row_sha256': 'c56d4d1af2c19a2d911d2f866202e0bb353c7b2c4dbadf9701f666c73f16fef1',
              'relationship_to_issuer': 'issuer_brand',
              'note': '2026-04-09 issuer HKEX notice explicitly defines HUTCHMED (China) Limited as HUTCHMED '
                      'and identifies stock 13. Only the fresh exact registry brand alias Hutchmed is '
                      'included.'},
 'HK:06996': {'row_sha256': 'dc17145e74321c7e43d39d8fe0e7ad000eb6737807ff7e7b7460b6f0c7dd0e4a',
              'relationship_to_issuer': 'issuer_brand',
              'note': 'Official investor page names Antengene Corporation Limited (6996); issuer own listing '
                      'release title uses Antengene Corporation. This approves the registry brand, not '
                      'additional subsidiaries.'},
 'HK:01477': {'row_sha256': '7b2cf8e70fcab237ab5dd3e5836d4980ab8c3289a5a44dcf3650e2d6e369d4c4',
              'relationship_to_issuer': 'subsidiary',
              'note': '2025 annual report definitions identify Ocumension (Hong Kong) Limited as a '
                      'wholly-owned subsidiary of the listed company.'},
 'HK:06955': {'row_sha256': '3fbcad50e58be0d107b1368e7da5f027d62461657cd1e31d3676f8ed70d0fde2',
              'relationship_to_issuer': 'issuer_legal_name',
              'note': '2023 interim report identifies Shandong Boan Biotechnology Co., Ltd. and 6955; '
                      'current listing exact-name match and current issuer site retain the identity. Report '
                      'date is historical and is not a fresh pipeline date.'},
 'HK:02105': {'row_sha256': '315d6a843581c845d00140076b3ec03b6f1ca3193cbdbd516b2b7f39670c0f98',
              'relationship_to_issuer': 'subsidiary',
              'note': '2025 annual report definitions and subsidiary table identify Laekna Limited (Hong '
                      'Kong) as a subsidiary, distinct from the listed holding-company legal name.'},
 'HK:03681': {'row_sha256': '9a111e615f558538294eacde4ad22325a40563aedc4cd1ad5da91f6be933d754',
              'relationship_to_issuer': 'issuer_legal_name',
              'note': 'Official corporate information identifies SinoMab BioScience Limited and 3681.HK. '
                      'Registry uses the customary Ltd abbreviation; exact collected sponsor string is '
                      'preserved.'},
 'A:600267': {'row_sha256': '27d24a61dc8a0476b21488602187d7a3bf531aec857a88e35bfacdf7586a5bd8',
              'relationship_to_issuer': 'issuer_legal_name',
              'note': 'Official English site identifies Zhejiang Hisun Pharmaceutical Co., Ltd.; Chinese '
                      'site links the Chinese listed company name to 600267.'},
 'A:002793': {'row_sha256': '5617710c34da082fbcf19fa8354f176a3c6b5adc9e2958d81de00e6f7bd052f7',
              'relationship_to_issuer': 'subsidiary',
              'note': 'Official basic information links listed Luoxin group to 002793. Issuer English '
                      'licensing notices explicitly identify Shandong Luoxin Pharmaceutical Group Stock Co., '
                      'Ltd. as its subsidiary. Source-name association does not transfer licence rights.'},
 'A:688382': {'row_sha256': 'cdfda704782967334fcf153f4f58dfcb766fdba2f4f2a95830b2e08e50d8702c',
              'relationship_to_issuer': 'issuer_legal_name',
              'note': 'Official English issuer site identifies InventisBio Co., Ltd.; SSE English newsletter '
                      'lists 688382.SH InventisBio Co., Ltd. Filename dizal0 is incidental and does not '
                      'identify Dizal.'},
 'A:688062': {'row_sha256': 'bedb0616001085d8fb04dd3a515bcc9940b23925a2f36316f72f587723dbd8b7',
              'relationship_to_issuer': 'issuer_legal_name',
              'note': 'Issuer own STAR-listing release identifies exact Mabwell legal name and 688062; '
                      'current official site explicitly lists both 688062.SH and 02493.HK. Two securities '
                      'share one issuer group, not two independent drug companies.'},
 'A:688373': {'row_sha256': '4278584f9ff41d541487cbae31556ae509e68c249bd353c7eb5338d419727d0f',
              'relationship_to_issuer': 'issuer_brand',
              'note': 'Issuer investor document identifies Shanghai MicuRx Pharmaceutical Co., Ltd., 688373 '
                      'and Chinese name. Its own English STAR listing release ties the security to the '
                      'registry MicuRx brand.'},
 'A:688302': {'row_sha256': 'ef3ad8c5482a9d65d2dac904ccdae88cfbd38f778d01c67e15593c1c6478ef97',
              'relationship_to_issuer': 'issuer_legal_name',
              'note': 'Official English website identifies Hinova Pharmaceuticals Inc. and Haichuang '
                      'Pharmaceutical (688302.SH).'},
 'A:688221': {'row_sha256': '0fd8a5afa5a0cefc50ff5b809b6d70ab127772b91aef7501919eeae7483b660e',
              'relationship_to_issuer': 'issuer_legal_name',
              'note': 'Official SSE English company profile explicitly links Frontier Biotechnologies Inc. '
                      'to 688221; issuer English website corroborates.'},
 'A:002755': {'row_sha256': 'feccfe0551799d9e214c64d9d6fde757cf3144acf8049e1db07087fecc52a8f8',
              'relationship_to_issuer': 'subsidiary',
              'note': 'Current official English website identifies Jiangsu Aosaikang and ASK Pharm (002755). '
                      'CNINFO profile identifies listed Beijing parent and its wholly-owned Jiangsu '
                      'operating company; business relationship section is dated 2021-12-14, so ongoing '
                      'equity/rights are not freshly adjudicated.'},
 'A:600380': {'row_sha256': '96a3ccde5b5655567c063e30f1466a4c8a6dd5b25015c8bc92607a0131afdfba',
              'relationship_to_issuer': 'issuer_legal_name',
              'note': 'Issuer English 2026 first-quarter report on CNINFO identifies Joincare Pharmaceutical '
                      'Group Industry Co., Ltd., Chinese name and 600380.'},
 'A:300601': {'row_sha256': 'dc9882d145e22f35445e29d99987489a2a75fe8f3fd03ca62e16094cf8fa2909',
              'relationship_to_issuer': 'issuer_legal_name',
              'note': 'Issuer English 2025 ESG report on CNINFO identifies Shenzhen Kangtai Biological '
                      'Products Co., Ltd. and 300601.'},
 'HK:02493': {'row_sha256': '54bfb4aa52ecb792f0a446c9c765c42788e9d7a4602b1d33e151314e6e3d48f1',
              'relationship_to_issuer': 'issuer_legal_name',
              'note': 'Current official Mabwell website explicitly identifies 688062.SH and 02493.HK; '
                      'STAR-listing release supplies the exact legal name. Preserve the shared Mabwell '
                      'issuer group and both securities.'}}


def row_digest(row):
    return hashlib.sha256(json.dumps(row, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':'), allow_nan=False).encode()).hexdigest()


class EvidenceFailure(ValueError):
    def __init__(self, code, message, reference=''):
        self.code, self.reference = code, reference
        super().__init__(message)


def _url(url, host=None, path=None):
    p = urlsplit(str(url))
    if p.scheme != 'https' or not p.hostname or p.username or p.password or p.port not in (None, 443) or p.fragment:
        raise EvidenceFailure('invalid_source_url', 'Evidence URL is outside its reviewed HTTPS scope')
    if host and p.hostname != host or path and p.path != path:
        raise EvidenceFailure('invalid_source_url', 'Registry source URL does not identify the reviewed endpoint')
    return p


def _read(workspace, reference, expected_hash, expected_bytes=None, cache=None):
    if not isinstance(reference, str) or not isinstance(expected_hash, str) or not re.fullmatch(r'[a-f0-9]{64}', expected_hash):
        raise EvidenceFailure('invalid_capture_reference', 'Capture reference or SHA-256 is invalid')
    rel = Path(reference)
    if rel.is_absolute() or '..' in rel.parts or not rel.is_relative_to(CAPTURE_PREFIX):
        raise EvidenceFailure('capture_path_escape', 'Capture reference is outside the reviewed evidence folder', reference)
    try:
        base = Path(workspace).resolve()
        path = within(base / rel, base)
        within(path, base / CAPTURE_PREFIX)
    except (OSError, ValueError):
        raise EvidenceFailure('capture_path_escape', 'Capture path escapes the permitted workspace', reference) from None
    key = (reference, expected_hash)
    if cache is not None and key in cache:
        raw = cache[key]
    else:
        try:
            with path.open('rb') as handle:
                raw = handle.read(MAX_CAPTURE_BYTES + 1)
        except OSError:
            raise EvidenceFailure('capture_missing', 'Restore this original source capture before installing the association', reference) from None
        if cache is not None:
            cache[key] = raw
    if not isinstance(raw, bytes) or len(raw) > MAX_CAPTURE_BYTES:
        raise EvidenceFailure('capture_oversized', 'Evidence exceeds the bounded capture size', reference)
    if hashlib.sha256(raw).hexdigest() != expected_hash:
        raise EvidenceFailure('capture_hash_mismatch', 'Evidence SHA-256 does not match the independently reviewed capture', reference)
    if expected_bytes is not None and (type(expected_bytes) is not int or len(raw) != expected_bytes):
        raise EvidenceFailure('capture_size_mismatch', 'Evidence size differs from the reviewed capture', reference)
    return raw


def review_mapping(row, workspace, listings, *, cache=None, candidate=None):
    """Validate configuration, current listing, original captures and exact leads.

    This function is offline/read-only.  Its returned status never changes an
    existing proposal or supplies human/scientific/ownership approval.
    """
    ident = str(row.get('id', ''))
    result = {'id': ident, 'status': 'blocked', 'review_version': VERSION,
              'reviewer_type': 'automated_source_review', 'human_sign_off': False,
              'scientific_review': 'pending', 'drug_ownership_verified': False,
              'full_pipeline_verified': False, 'errors': [], 'checked_at': now()}
    try:
        reviewed = _REVIEWED_ROWS.get(ident)
        if not reviewed or row_digest(row) != reviewed['row_sha256']:
            raise EvidenceFailure('configuration_not_independently_reviewed', 'This exact source-name configuration has not passed independent review')
        if row.get('review_status') != 'pending' or row.get('human_sign_off') is not False or row.get('drug_ownership_verified') is not False:
            raise EvidenceFailure('invalid_review_boundary', 'Source-name evidence cannot supply scientific, human or ownership approval')
        matching = [r for r in listings if matches_listing(row, r)]
        if not matching:
            raise EvidenceFailure('current_listing_mismatch', 'Current exchange name/code does not match the independently reviewed security')
        if candidate is not None:
            if candidate.get('id') != ident or not matches_listing(row, candidate) or not re.fullmatch(r'[a-f0-9]{64}', str(candidate.get('content_hash', ''))):
                raise EvidenceFailure('current_candidate_mismatch', 'Current candidate identity/hash does not match the independently reviewed security')
            if candidate.get('review_status') == 'rejected':
                raise EvidenceFailure('candidate_rejected', 'An explicit candidate rejection must be resolved before installing this association')
            result.update(candidate_id=ident, candidate_hash=candidate['content_hash'])
        evidence = row.get('evidence', [])
        exchange = [e for e in evidence if e.get('kind') == 'exchange_listing_observation']
        official = [e for e in evidence if e.get('kind') in ('official_identity_source', 'official_entity_reference')]
        registry = [e for e in evidence if e.get('kind') == 'registry_lead_sponsor']
        if len(exchange) != 1 or not official or not registry:
            raise EvidenceFailure('missing_evidence_role', 'Exchange, official identity and registry lead evidence are all required')
        ex = exchange[0]
        ex_raw = _read(workspace, ex['snapshot_reference'], ex['snapshot_sha256'], cache=cache)
        archived_listings = json.loads(ex_raw)
        if not isinstance(archived_listings, list) or not any(matches_listing(row, r) for r in archived_listings):
            raise EvidenceFailure('archived_listing_mismatch', 'Archived exchange observation does not identify this security')
        captures = [{'reference': ex['snapshot_reference'], 'sha256': ex['snapshot_sha256'], 'role': 'exchange_listing_observation'}]
        for entry in official:
            _url(entry['url'])
            _read(workspace, entry['capture_reference'], entry['sha256'], entry.get('bytes'), cache=cache)
            captures.append({'reference': entry['capture_reference'], 'sha256': entry['sha256'], 'role': entry['kind'], 'url': entry['url']})
        leads = []
        for entry in registry:
            alias, nct = entry['sponsor'], entry['nct_id']
            if not re.fullmatch(r'NCT\d{8}', nct) or alias not in row['aliases']:
                raise EvidenceFailure('registry_alias_mismatch', 'Registry evidence does not identify an included exact sponsor alias')
            _url(entry['url'], 'clinicaltrials.gov', '/study/' + nct)
            _url(entry['api_url'], 'clinicaltrials.gov', '/api/v2/studies/' + nct)
            raw = _read(workspace, entry['capture_reference'], entry['sha256'], cache=cache)
            protocol = json.loads(raw)['protocolSection']
            if protocol['identificationModule']['nctId'] != nct or protocol['sponsorCollaboratorsModule']['leadSponsor']['name'] != alias:
                raise EvidenceFailure('registry_exact_lead_mismatch', 'Single-study capture does not contain the exact included lead sponsor')
            query_url = _url(entry['query_url'], 'clinicaltrials.gov', '/api/v2/studies')
            query = parse_qs(query_url.query)
            if query.get('query.spons') != ['"' + alias + '"']:
                raise EvidenceFailure('registry_query_scope_mismatch', 'Captured sponsor query is not the reviewed exact alias query')
            queried = json.loads(_read(workspace, entry['query_capture_reference'], entry['query_sha256'], cache=cache))
            exact = []
            for study in queried['studies']:
                p = study['protocolSection']
                if p.get('sponsorCollaboratorsModule', {}).get('leadSponsor', {}).get('name') == alias:
                    exact.append(p['identificationModule']['nctId'])
            if not exact or type(queried.get('totalCount')) is not int or queried.get('totalCount') != entry['query_total'] or not set(entry['query_exact_lead_examples']).issubset(exact):
                raise EvidenceFailure('registry_query_evidence_mismatch', 'Bounded query capture does not support the recorded exact-lead examples and source total')
            leads.append({'alias': alias, 'nct_id': nct, 'bounded_query_total': queried['totalCount'], 'exact_lead_examples': exact})
            captures.extend([{'reference': entry['capture_reference'], 'sha256': entry['sha256'], 'role': 'registry_single_study', 'url': entry['api_url']},
                             {'reference': entry['query_capture_reference'], 'sha256': entry['query_sha256'], 'role': 'registry_bounded_query', 'url': entry['query_url']}])
        if {e['alias'] for e in leads} != set(row['aliases']):
            raise EvidenceFailure('unverified_alias', 'Every installed alias needs its own exact lead and official identity evidence')
        result.update(status='verified_source_name', row_sha256=reviewed['row_sha256'],
                      relationship_to_issuer=reviewed['relationship_to_issuer'], review_note=reviewed['note'],
                      current_listing_names=sorted({r['name'] for r in matching}), captures=captures, registry_leads=leads,
                      scope='listed_security_to_exact_registry_source_name_only')
    except EvidenceFailure as exc:
        result['errors'].append({'code': exc.code, 'message': str(exc), 'reference': exc.reference})
    except (KeyError, TypeError, ValueError, OSError) as exc:
        result['errors'].append({'code': 'evidence_schema_invalid', 'message': 'Evidence structure could not be independently verified: ' + type(exc).__name__, 'reference': ''})
    return result


def curated_proposal_plan(market, db, mapping, expected_candidate_hash):
    """Read-only eligibility plan for the dedicated curated-source transaction.

    This path rechecks frozen configuration and raw evidence itself.  It never
    accepts a caller-supplied verified flag, changes reviewer types accepted by
    the public review API, or interprets source-name review as human approval.
    """
    if not db.in_transaction:
        raise ValueError('Curated reconciliation requires a caller-owned transaction')
    ident = mapping['id']
    listings = [dict(r) for r in db.execute('SELECT * FROM listings WHERE market=? AND ticker=?',
                                          (mapping['market'], mapping['ticker']))]
    record = db.execute('SELECT * FROM issuer_candidates WHERE id=?', (ident,)).fetchone()
    candidate = dict(record) if record else None
    pending = [dict(r) for r in db.execute("SELECT * FROM sponsor_proposals WHERE candidate_id=? AND status='pending'", (ident,))]
    plan = {'eligible': [], 'conflicts': [], 'review': None}

    def conflict(proposal, code, message):
        plan['conflicts'].append({'proposal_id': proposal['id'], 'code': code, 'reason': message})

    if not candidate or candidate['content_hash'] != expected_candidate_hash:
        for proposal in pending:
            conflict(proposal, 'current_candidate_changed', 'Candidate hash changed after source validation; refresh the proposal and review')
        return plan
    if candidate['classification'] not in ('drug_developer', 'candidate', 'uncertain', 'drug_developer_candidate'):
        for proposal in pending:
            conflict(proposal, 'candidate_not_eligible', 'Current candidate classification does not permit automated source-name reconciliation')
        return plan
    # No cache: the transaction independently rereads the authoritative raw
    # captures before it can change any pending proposal.
    source_review = review_mapping(mapping, market.root.parent.parent, listings, candidate=candidate)
    plan['review'] = source_review
    if source_review['status'] != 'verified_source_name':
        for proposal in pending:
            conflict(proposal, 'source_evidence_no_longer_verified', 'Original source evidence no longer passes the independent capture-bound gate')
        return plan
    for proposal in pending:
        if not {'candidate_hash', 'alias', 'run_id', 'source_url', 'evidence_nct'}.issubset(proposal):
            conflict(proposal, 'proposal_schema_invalid', 'Proposal is missing the original resolution and candidate binding')
            continue
        if proposal['candidate_hash'] != candidate['content_hash']:
            conflict(proposal, 'proposal_candidate_changed', 'Proposal candidate hash differs from the current reviewed candidate')
            continue
        from .sponsor_resolution import match_sponsor
        aliases = [alias for alias in mapping['aliases'] if match_sponsor(proposal['alias'], alias) == 'normalized_exact']
        if len(aliases) != 1:
            conflict(proposal, 'alias_not_curated_exact', 'Pending alias does not normalize exactly to one independently curated alias')
            continue
        resolution = db.execute('SELECT * FROM sponsor_resolution_runs WHERE id=?', (proposal['run_id'],)).fetchone()
        if not resolution:
            conflict(proposal, 'resolution_missing', 'The original sponsor-resolution run is missing')
            continue
        resolution = dict(resolution)
        if (resolution['candidate_id'] != ident or resolution['candidate_hash'] != candidate['content_hash'] or
                resolution['status'] != 'suggested' or resolution['truncated'] != 0 or not resolution['finished']):
            conflict(proposal, 'resolution_not_complete_suggestion', 'Original resolution must be suggested, untruncated, finished, and bound to the same candidate hash')
            continue
        try:
            ncts = json.loads(proposal['evidence_nct'])
            parsed = _url(proposal['source_url'], 'clinicaltrials.gov')
            source_nct = parsed.path.removeprefix('/study/')
            if parsed.path != '/study/' + source_nct or not re.fullmatch(r'NCT\d{8}', source_nct):
                raise ValueError('Invalid proposal source')
            approved_proofs = [proof for proof in source_review['registry_leads'] if proof['alias'] == aliases[0]]
            known_ncts = {nct for proof in approved_proofs for nct in [proof['nct_id'], *proof['exact_lead_examples']]}
            if (not isinstance(ncts, list) or not ncts or not all(isinstance(nct, str) and re.fullmatch(r'NCT\d{8}', nct) for nct in ncts) or
                    source_nct not in ncts or source_nct not in known_ncts or not known_ncts.intersection(ncts)):
                raise ValueError('Proposal NCTs are not linked to independently captured exact-lead evidence')
        except (KeyError, TypeError, ValueError):
            conflict(proposal, 'proposal_registry_proof_mismatch', 'Proposal source and NCT references are not supported by the independently captured exact-lead evidence')
            continue
        plan['eligible'].append({'proposal': proposal, 'resolution': resolution, 'approved_alias': aliases[0]})
    return plan
