"""Observable issuer discovery and collection coverage, independent of prediction.

Candidates are reviewable classification observations, never a full drug-company
universe or an automatic sponsor/ownership mapping. Existing ``companies`` rows
remain the authority for configured collection targets.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone

from .io import now

MARKETS = ("US", "A", "HK")
REVIEW_STATES = ("pending", "accepted", "rejected")
COLLECTION_STATES = (
    "unmapped", "uncollected", "running", "failed", "partial", "interrupted",
    "succeeded_with_trials", "succeeded_zero", "cancelled",
)


def init_schema(market):
    """Create additive tables. Safe to call against an existing market database."""
    with market.db() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS issuer_candidates (
          id TEXT PRIMARY KEY, market TEXT NOT NULL, ticker TEXT NOT NULL,
          name TEXT NOT NULL, classification TEXT NOT NULL, reason TEXT NOT NULL,
          source TEXT NOT NULL, evidence_json TEXT NOT NULL, payload_json TEXT NOT NULL,
          review_status TEXT NOT NULL DEFAULT 'pending'
            CHECK(review_status IN ('pending','accepted','rejected')),
          first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, updated TEXT NOT NULL,
          content_hash TEXT NOT NULL, UNIQUE(market,ticker)
        );
        CREATE TABLE IF NOT EXISTS issuer_candidate_observations (
          id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL, observed TEXT NOT NULL,
          source TEXT NOT NULL, content_hash TEXT NOT NULL, payload_json TEXT NOT NULL,
          UNIQUE(candidate_id,content_hash)
        );
        CREATE TABLE IF NOT EXISTS issuer_candidate_reviews (
          id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL, created TEXT NOT NULL,
          reviewer TEXT NOT NULL, decision TEXT NOT NULL,
          reason TEXT NOT NULL, candidate_hash TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_issuer_candidates_market_status
          ON issuer_candidates(market,review_status);
        CREATE INDEX IF NOT EXISTS idx_issuer_reviews_candidate
          ON issuer_candidate_reviews(candidate_id,created);
        CREATE INDEX IF NOT EXISTS idx_coverage_runs_company_source_started
          ON runs(company,source,started);
        """)


def _identity(market_code, ticker):
    market_code = str(market_code).upper().strip()
    ticker = str(ticker).upper().strip()
    if market_code not in MARKETS or not re.fullmatch(r"[A-Z0-9.\-]{1,16}", ticker):
        raise ValueError("Invalid market or ticker")
    if market_code in ("A", "HK"):
        if not ticker.isdigit() or len(ticker) > (6 if market_code == "A" else 5):
            raise ValueError("Invalid numeric ticker")
        ticker = ticker.zfill(6 if market_code == "A" else 5)
    return market_code, ticker, market_code + ":" + ticker


def _serial(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


def _identity_payload(value):
    """A fresh directory retrieval alone does not change reviewed identity.

    Keep observation times in the actual payload and source archives. Only the
    volatile directory retrieval timestamp is excluded from semantic comparison.
    Names, classifications, source URLs and every other evidence field remain.
    """
    row=json.loads(_serial(value))
    evidence=row.get('evidence')
    if isinstance(evidence,list):
        for item in evidence:
            if isinstance(item,dict) and item.get('kind')=='listing_metadata':
                item.pop('observed',None)
    return _serial(row)


def refresh_candidates(market, candidates):
    """Upsert observed classification candidates, preserving all manual decisions.

    Input dictionaries require market/ticker/name and a nonempty reason (or
    ``classification_reason``), source, and evidence (or ``sources``). Evidence is
    a list/dict/string of source-grounded observations; no download occurs here.
    Additional fields such as aliases, industry, method and confidence survive in
    payload. A caller-supplied review_status/approved/confirmed field has no effect.
    Entire batches are validated before writing, so one invalid row cannot apply
    a partial refresh. A source's absence does not delete earlier observations.
    """
    init_schema(market)
    prepared = []
    seen = set()
    for value in candidates:
        if not isinstance(value, dict):
            raise ValueError("Candidate must be a dictionary")
        row = dict(value)
        market_code, ticker, cid = _identity(row.get("market", ""), row.get("ticker", ""))
        name = str(row.get("name", "")).strip()
        reason = str(row.get("reason", row.get("classification_reason", ""))).strip()
        source = str(row.get("source", "")).strip()
        evidence = row.get("evidence", row.get("sources"))
        classification = str(row.get("classification", "drug_developer_candidate")).strip()
        if not name or not reason or not source or not evidence or not classification:
            raise ValueError("Candidate requires name, classification, reason, source and evidence")
        if len(name) > 300 or len(reason) > 5000 or len(source) > 2000:
            raise ValueError("Candidate fields too long")
        # Observations are not allowed to self-approve, nor to smuggle review state
        # into the preserved classification payload.
        for field in ("review_status", "approved", "confirmed", "reviewer", "decision"):
            row.pop(field, None)
        row.update(market=market_code, ticker=ticker, name=name, reason=reason,
                   source=source, evidence=evidence, classification=classification)
        observed = str(row.pop("observed", row.pop("observed_at", now())))
        observed = _parse_time(observed, required=True).isoformat()
        payload = _serial(row)
        if len(payload.encode()) > 100_000:
            raise ValueError("Candidate observation too large")
        digest = hashlib.sha256(_identity_payload(row).encode()).hexdigest()
        # One canonical candidate per batch avoids ambiguous source precedence.
        if cid in seen:
            raise ValueError("Duplicate candidate in refresh batch: " + cid)
        seen.add(cid)
        prepared.append((cid, market_code, ticker, name, classification, reason,
                         source, _serial(evidence), payload, observed, digest))
    inserted = updated = unchanged = 0
    with market.db() as db:
        for cid, market_code, ticker, name, classification, reason, source, evidence, payload, observed, digest in prepared:
            previous = db.execute("SELECT content_hash,last_seen,payload_json FROM issuer_candidates WHERE id=?", (cid,)).fetchone()
            # Preserve existing review bindings when introducing this stable
            # comparison to an older database with timestamp-bearing hashes.
            if previous and _identity_payload(json.loads(previous['payload_json']))==_identity_payload(json.loads(payload)):
                digest=previous['content_hash']
            db.execute("""INSERT OR IGNORE INTO issuer_candidate_observations
                VALUES (?,?,?,?,?,?)""", (uuid.uuid4().hex, cid, observed, source, digest, payload))
            # Historical observations remain inspectable but do not replace a
            # newer classification snapshot during delayed/incremental imports.
            if previous and _parse_time(observed) < _parse_time(previous["last_seen"]):
                unchanged += 1
                continue
            if previous is None:
                inserted += 1
            elif previous["content_hash"] != digest:
                updated += 1
            else:
                unchanged += 1
            db.execute("""INSERT INTO issuer_candidates
                (id,market,ticker,name,classification,reason,source,evidence_json,
                 payload_json,review_status,first_seen,last_seen,updated,content_hash)
                VALUES (?,?,?,?,?,?,?,?,?,'pending',?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                 name=excluded.name,classification=excluded.classification,
                 reason=excluded.reason,source=excluded.source,
                 evidence_json=excluded.evidence_json,payload_json=excluded.payload_json,
                 last_seen=MAX(issuer_candidates.last_seen,excluded.last_seen),
                 updated=CASE WHEN issuer_candidates.content_hash!=excluded.content_hash
                   THEN excluded.updated ELSE issuer_candidates.updated END,
                 content_hash=excluded.content_hash""",
                       (cid, market_code, ticker, name, classification, reason, source,
                        evidence, payload, observed, observed, now(), digest))
    return {"inserted": inserted, "updated": updated, "unchanged": unchanged,
            "observed": len(prepared)}


def review_candidate(market, candidate_id, decision, reviewer, reason):
    """Append a declared human classification decision. Does not add a company.

    Identity is an attributed local statement, not authenticated independent
    review or scientific approval. Every decision binds the reviewed content hash.
    """
    init_schema(market)
    if decision not in ("accepted", "rejected"):
        raise ValueError("Decision must be accepted or rejected")
    reviewer, reason = str(reviewer).strip(), str(reason).strip()
    if not 1 <= len(reviewer) <= 100 or not 10 <= len(reason) <= 5000:
        raise ValueError("Reviewer and a 10-5000 character reason are required")
    with market.db() as db:
        candidate = db.execute("SELECT content_hash FROM issuer_candidates WHERE id=?", (candidate_id,)).fetchone()
        if not candidate:
            raise ValueError("Unknown candidate")
        rid = uuid.uuid4().hex
        db.execute("INSERT INTO issuer_candidate_reviews VALUES (?,?,?,?,?,?,?)",
                   (rid, candidate_id, now(), reviewer, decision, reason, candidate["content_hash"]))
        db.execute("UPDATE issuer_candidates SET review_status=? WHERE id=?", (decision, candidate_id))
    return rid


def _parse_time(value, required=False):
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError("Timestamp must include timezone")
        return result.astimezone(timezone.utc)
    except (ValueError, TypeError):
        if required:
            raise ValueError("Observed timestamp must be a timezone-aware ISO timestamp")
        return None


def _all_rows(market, stale_hours=24, as_of=None, ids=None, light=False):
    init_schema(market)
    reference_time = _parse_time(as_of, required=True) if as_of else datetime.now(timezone.utc)
    with market.db() as db:
        if ids is not None and not ids:
            return []
        suffix = " WHERE id IN (" + ",".join("?" for _ in ids) + ")" if ids is not None else ""
        params = ids or []
        candidate_fields = "id,market,ticker,name,classification,reason,source,review_status,content_hash" if light else "*"
        candidates = {r["id"]: dict(r) for r in db.execute("SELECT " + candidate_fields + " FROM issuer_candidates" + suffix, params)}
        companies = {r["id"]: dict(r) for r in db.execute("SELECT * FROM companies" + suffix, params)}
        counts = {r["company"]: r["n"] for r in db.execute("SELECT company,count(*) AS n FROM trials GROUP BY company")}
        listings = {}
        for record in ([] if light else db.execute("SELECT * FROM listings")):
            try:
                _, _, key = _identity(record["market"], record["ticker"])
            except ValueError:
                continue
            listings.setdefault(key, []).append(dict(record))
        runs = {}
        successes = {}
        for record in db.execute("SELECT * FROM runs WHERE company!='' ORDER BY started,rowid"):
            if record["source"] != "ClinicalTrials.gov":
                continue
            current = dict(record)
            key = current["company"]
            runs[key] = current
            if current["status"] == "succeeded":
                successes[key] = current
        reviews = {}
        for record in db.execute("SELECT * FROM issuer_candidate_reviews ORDER BY created,rowid"):
            reviews[record["candidate_id"]] = dict(record)
    result = []
    for cid in sorted(set(candidates) | set(companies)):
        candidate = candidates.get(cid)
        company = companies.get(cid)
        latest = runs.get(cid)
        success = successes.get(cid)
        listing = listings.get(cid, [])
        row = dict(candidate or {})
        base = candidate or company
        row.update(id=cid, market=base["market"], ticker=base["ticker"], name=base["name"],
                   classification=candidate["classification"] if candidate else "configured_company",
                   reason=candidate["reason"] if candidate else company["provenance"],
                   company_id=company["id"] if company else None,
                   company_name=company["name"] if company else None,
                   mapping_status="mapped" if company else "unmapped",
                   mapping_provenance=company["provenance"] if company else None,
                   candidate_present=bool(candidate), review_status=candidate["review_status"] if candidate else "not_a_candidate",
                   listing_present=bool(listing), listing_sources=sorted({r["source"] for r in listing}),
                   listing_exchanges=sorted({r["exchange"] for r in listing}),
                   trial_count=counts.get(cid, 0), source="ClinicalTrials.gov" if not candidate else candidate["source"],
                   pipeline_completeness="unknown", full_pipeline_verified=False)
        if candidate and not light:
            row["evidence"] = json.loads(candidate["evidence_json"])
            row["payload"] = json.loads(candidate["payload_json"])
        else:
            row["evidence"] = []
            row["payload"] = {}
        row["aliases"] = json.loads(company["aliases"]) if company else row["payload"].get("aliases", [])
        row["last_review"] = reviews.get(cid)
        row["review_needs_refresh"] = bool(candidate and cid in reviews and reviews[cid]["candidate_hash"] != candidate["content_hash"])
        if not company:
            collection_state = "unmapped"
        elif not latest:
            collection_state = "uncollected"
        elif latest["status"] == "succeeded":
            collection_state = "succeeded_with_trials" if latest["count"] else "succeeded_zero"
        elif latest["status"] in ("running", "failed", "partial", "interrupted", "cancelled"):
            collection_state = latest["status"]
        else:
            collection_state = "failed"
        success_time = _parse_time(success["finished"] or success["started"]) if success else None
        row.update(collection_status=collection_state, latest_run=latest,
                   latest_result_count=latest["count"] if latest else None,
                   latest_error=latest["error"] if latest else None,
                   last_success_at=(success["finished"] or success["started"]) if success else None,
                   stale=bool(company and success_time and (reference_time - success_time).total_seconds() > stale_hours * 3600))
        result.append(row)
    return result


def candidate_rows(market, q="", market_filter="", status="", offset=0, limit=50, classification=""):

    """Return (rows,total), joining candidate, configured company and collection.

    ``status`` accepts a review state, collection state, mapped/unmapped, or stale.
    Rows include configured companies absent from classification candidates. Raw
    source snippets remain text and must be HTML-escaped by any presentation.
    """
    if market_filter and market_filter not in MARKETS:
        raise ValueError("Unknown market filter")
    if classification and classification not in ("all", "active", "drug_developer", "candidate", "drug_developer_candidate", "uncertain", "excluded", "unclassified", "configured_company"):
        raise ValueError("Unknown classification filter")
    if status and status not in set(REVIEW_STATES + COLLECTION_STATES + ("mapped", "stale", "needs_review")):
        raise ValueError("Unknown status filter")
    if not isinstance(offset, int) or not isinstance(limit, int) or offset < 0 or not 1 <= limit <= 200:
        raise ValueError("Invalid page bounds")
    init_schema(market)
    query = str(q).strip().casefold()
    conditions, params = [], []
    base = """FROM (SELECT id FROM issuer_candidates UNION SELECT id FROM companies) i
      LEFT JOIN issuer_candidates c ON c.id=i.id
      LEFT JOIN companies co ON co.id=i.id
      LEFT JOIN runs r ON r.rowid=(SELECT rr.rowid FROM runs rr
        WHERE rr.company=i.id AND rr.source='ClinicalTrials.gov'
        ORDER BY rr.started DESC,rr.rowid DESC LIMIT 1)"""
    if market_filter:
        conditions.append("COALESCE(c.market,co.market)=?")
        params.append(market_filter)
    if classification == "active":
        conditions.append("(c.classification IN ('drug_developer','candidate','drug_developer_candidate','uncertain') OR co.id IS NOT NULL)")
    elif classification == "configured_company":
        conditions.append("co.id IS NOT NULL AND c.id IS NULL")
    elif classification and classification != "all":
        conditions.append("c.classification=?")
        params.append(classification)
    if query:
        conditions.append("(lower(COALESCE(c.name,'')||' '||COALESCE(c.ticker,'')||' '||COALESCE(c.payload_json,'')||' '||COALESCE(co.name,'')||' '||COALESCE(co.aliases,'')) LIKE ?)")
        # SQLite LIKE is ASCII case-insensitive; payload search remains exact for
        # non-ASCII text while preventing wildcard input from broadening queries.
        params.append('%' + query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%')
        conditions[-1] = conditions[-1][:-1] + " ESCAPE '\\')"
    if status in REVIEW_STATES:
        conditions.append("c.review_status=?")
        params.append(status)
    elif status == "mapped":
        conditions.append("co.id IS NOT NULL")
    elif status == "unmapped":
        conditions.append("co.id IS NULL")
    elif status == "uncollected":
        conditions.append("co.id IS NOT NULL AND r.id IS NULL")
    elif status in ("running", "failed", "partial", "interrupted", "cancelled"):
        conditions.append("co.id IS NOT NULL AND r.status=?")
        params.append(status)
    elif status in ("succeeded_with_trials", "succeeded_zero"):
        conditions.append("co.id IS NOT NULL AND r.status='succeeded' AND COALESCE(r.count,0)" + (">0" if status == "succeeded_with_trials" else "=0"))
    elif status == "stale":
        conditions.append("co.id IS NOT NULL AND julianday('now')-julianday((SELECT COALESCE(rr.finished,rr.started) FROM runs rr WHERE rr.company=i.id AND rr.source='ClinicalTrials.gov' AND rr.status='succeeded' ORDER BY rr.started DESC,rr.rowid DESC LIMIT 1))>1")
    elif status == "needs_review":
        conditions.append("(c.review_status='pending' OR c.content_hash!=(SELECT candidate_hash FROM issuer_candidate_reviews cr WHERE cr.candidate_id=i.id ORDER BY cr.created DESC,cr.rowid DESC LIMIT 1))")
    where = " WHERE " + " AND ".join("(" + value + ")" for value in conditions) if conditions else ""
    with market.db() as db:
        total = db.execute("SELECT count(*) " + base + where, params).fetchone()[0]
        ids = [r[0] for r in db.execute("SELECT i.id " + base + where + " ORDER BY i.id LIMIT ? OFFSET ?", params + [limit, offset])]
    return _all_rows(market, ids=ids), total


def coverage_summary(market, stale_hours=24, as_of=None):
    """Count observable workflow states, without a false full-market denominator."""
    if stale_hours <= 0:
        raise ValueError("Stale threshold must be positive")
    rows = _all_rows(market, stale_hours=stale_hours, as_of=as_of, light=True)
    with market.db() as db:
        listings = [dict(r) for r in db.execute("SELECT market,ticker,source,observed FROM listings")]
        universe_runs = [dict(r) for r in db.execute("SELECT * FROM runs WHERE company='' ORDER BY started,id")]
    summaries = {}
    from .market_sources import URLS
    expected_sources = {"US": {k for k in URLS if k in ("US", "USNASDAQ", "USOTHER")},
                        "A": {k for k in URLS if k in ("SH", "STAR", "SZ", "BJ", "BSE")},
                        "HK": {"HK"}}
    for market_code in MARKETS:
        subset = [r for r in rows if r["market"] == market_code]
        securities = set()
        unsupported = set()
        source_rows = {}
        source_observed = {}
        for row in listings:
            if row["market"] != market_code:
                continue
            source_rows[row["source"]] = source_rows.get(row["source"], 0) + 1
            source_observed[row["source"]] = max(source_observed.get(row["source"], ""), row["observed"] or "")
            try:
                securities.add(_identity(row["market"], row["ticker"])[2])
            except ValueError:
                unsupported.add(str(row["market"]) + ":" + str(row["ticker"]).strip().upper())
        state_counts = {state: sum(r["collection_status"] == state for r in subset) for state in COLLECTION_STATES}
        review_counts = {state: sum(r["review_status"] == state for r in subset) for state in REVIEW_STATES}
        universe_status = []
        for source in sorted(expected_sources[market_code]):
            latest = next((r for r in reversed(universe_runs) if r["source"] == source), None)
            universe_status.append({"source": source, "records": source_rows.get(source, 0), "last_observed": source_observed.get(source), "latest_run": latest})
        gaps = [
            "Drug-company identification is a candidate screen; the total number of eligible issuers has not been independently established.",
            "ClinicalTrials.gov observations do not establish complete pipelines, preclinical programs, China-only registrations, drug ownership or licensing rights.",
            "Issuer IR/annual-report pipeline reconciliation and ownership review remain outstanding unless separately evidenced.",
        ]
        if market_code == "A" and not any(k in URLS for k in ("BJ", "BSE")):
            gaps.append("Beijing Stock Exchange directory is not connected.")
        missing = sorted(expected_sources[market_code] - set(source_rows))
        if unsupported:
            gaps.append(str(len(unsupported)) + " securities use unsupported identities and require identity review; they are preserved in the raw directory.")
        if missing:
            gaps.append("No usable listing snapshot for source(s): " + ", ".join(missing))
        classification_counts = {}
        for row in subset:
            if row["candidate_present"]:
                label = row["classification"]
                classification_counts[label] = classification_counts.get(label, 0) + 1
        candidate_count = sum(classification_counts.get(label, 0) for label in
                              ("drug_developer", "candidate", "uncertain", "drug_developer_candidate"))
        summaries[market_code] = {
            "market": market_code,
            "securities_count": len(securities) + len(unsupported),
            "raw_unique_security_count": len(securities) + len(unsupported),
            "normalized_security_count": len(securities),
            "unsupported_identity_count": len(unsupported),
            "listing_records": sum(source_rows.values()),
            "candidate_count": candidate_count,
            "classified_listing_count": sum(classification_counts.values()),
            "classification_counts": classification_counts,
            "excluded_count": classification_counts.get("excluded", 0),
            "unclassified_count": classification_counts.get("unclassified", 0),
            "mapped_company_count": sum(r["mapping_status"] == "mapped" for r in subset),
            "companies_with_trials": sum(r["mapping_status"] == "mapped" and r["trial_count"] > 0 for r in subset),
            "trial_records": sum(r["trial_count"] for r in subset),
            "success_zero_count": state_counts["succeeded_zero"],
            "uncollected_count": state_counts["uncollected"],
            "failed_count": state_counts["failed"], "partial_count": state_counts["partial"],
            "interrupted_count": state_counts["interrupted"], "running_count": state_counts["running"],
            "cancelled_count": state_counts["cancelled"],
            "stale_count": sum(r["stale"] for r in subset),
            "unmapped_candidate_count": sum(r["candidate_present"] and r["classification"] in ("drug_developer", "candidate", "uncertain", "drug_developer_candidate") and r["mapping_status"] == "unmapped" for r in subset),
            "review_needs_refresh_count": sum(r["review_needs_refresh"] for r in subset),
            "review_counts": review_counts, "collection_counts": state_counts,
            "universe_sources": universe_status, "source_gaps": gaps,
            "eligible_issuer_denominator": None, "full_market_coverage_rate": None,
            "full_pipeline_verified_count": 0,
        }
    return {
        "as_of": as_of or now(), "stale_after_hours": stale_hours,
        "markets": summaries,
        "scope": "Observed securities, classification candidates and configured sponsor collection targets; not verified complete drug pipelines.",
        "totals": {field: sum(s[field] for s in summaries.values()) for field in (
            "securities_count", "raw_unique_security_count", "normalized_security_count", "unsupported_identity_count", "candidate_count", "mapped_company_count", "companies_with_trials",
            "trial_records", "success_zero_count", "uncollected_count", "failed_count", "partial_count",
            "interrupted_count", "running_count", "cancelled_count", "stale_count", "unmapped_candidate_count",
            "review_needs_refresh_count", "full_pipeline_verified_count")},
    }
