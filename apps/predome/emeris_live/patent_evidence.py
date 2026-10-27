"""Patent provenance, conservative evidence eligibility and opt-in official clients.

Importing or reading the catalogue never performs network access. Connector clients
are for the offline ingestion CLI; they are not exposed as anonymous download APIs.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import math
import os
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, time as daytime, timezone
from pathlib import Path
from .patent_identity import structure_review_errors

SCHEMA_VERSION = "predome-patent-evidence-1.0"
CONNECTOR_RECEIPT_VERSION = "predome-patent-connector-response-1"
MAX_CONNECTOR_BYTES = 20 * 1024 * 1024
DEFAULT_DATA_DIR = Path(__file__).resolve().parents[1] / "patent_data"
PUBLICATION_RE = re.compile(r"^([A-Z]{2})([0-9]{5,14})([A-Z][0-9]?)$")
OPS_DOCS = "https://link.epo.org/web/searching-for-patents/data/en-ops-v3.2-documentation-version-1.3.20.pdf"
ODP_DOCS = "https://data.uspto.gov/apis/patent-file-wrapper/search"
PDF_HOSTS = frozenset({"patentimages.storage.googleapis.com", "ops.epo.org", "api.uspto.gov"})


def _normalized(value):
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("Canonical JSON requires string keys")
            key = _normalized(key)
            if key in result:
                raise ValueError("Duplicate normalized JSON key")
            result[key] = _normalized(item)
        return result
    if isinstance(value, (list, tuple)):
        return [_normalized(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Non-finite numbers are not evidence")
    return value


def canonical_bytes(value):
    """Versioned local canonical format: NFC, sorted keys, compact UTF-8, no NaN."""
    return json.dumps(_normalized(value), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def canonical_sha256(value):
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def normalize_publication(value):
    value = re.sub(r"[ .-]", "", str(value).upper())
    if not PUBLICATION_RE.fullmatch(value):
        raise ValueError("A complete publication number with country and kind code is required")
    return value


def _instant(value, day_end=True):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Missing dated public availability")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return datetime.combine(date.fromisoformat(value), daytime.max if day_end else daytime.min,
                                tzinfo=timezone.utc)
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("Timestamp must include a timezone")
    return stamp.astimezone(timezone.utc)


def experiment_content_sha256(card):
    """Bind a source check to scientific content, separately from review metadata."""
    return canonical_sha256({k: v for k, v in card.items()
                             if k not in {"review", "eligible_for_forecast"}})


def research_review_errors(item, cutoff=None):
    """Validate an explicit two-AI source check; it is never a human review."""
    receipt = item.get("research_review", {})
    errors = []
    if receipt.get("schema_version") not in {"predome-patent-ai-source-review-1.0", "predome-patent-ai-source-review-1.1"}:
        errors.append("ai_research_review_schema_unsupported")
    if receipt.get("review_type") != "two_agent_visual_source_check":
        return ["ai_research_review_missing"]
    if receipt.get("receipt_sha256") != canonical_sha256({k: v for k, v in receipt.items() if k != "receipt_sha256"}):
        errors.append("ai_review_receipt_integrity_mismatch")
    if receipt.get("publication_number") != item.get("publication_number") or receipt.get("source_sha256") != item.get("raw_sha256"):
        errors.append("ai_review_source_mismatch")
    reviewers = receipt.get("reviewers", [])
    if (len(reviewers) < 2 or len({r.get("actor_id") for r in reviewers if r.get("actor_id")}) < 2
            or any(r.get("actor_type") != "ai" or r.get("status") != "completed" for r in reviewers)):
        errors.append("two_completed_ai_source_checks_required")
    if (receipt.get("human_review_count") != 0 or receipt.get("clinical_prediction_validated") is not False
            or receipt.get("same_base_model_correlation_disclosed") is not True):
        errors.append("ai_review_disclosure_incomplete")
    decision = receipt.get("identity_decision")
    sources = receipt.get("drug_mapping", {}).get("sources", [])
    structural = (receipt.get("schema_version") == "predome-patent-ai-source-review-1.1"
        or "identity_analysis" in receipt or "experiment_cards_source" in receipt
        or any(s.get("role") == "official_inn_modified_peptide_identity" for s in sources))
    if structural:
        if (receipt.get("drug_mapping", {}).get("status") != "ai_verified_modified_peptide_structure"
                or decision != "supported_by_ai_modified_peptide_structure_check"):
            errors.append("modified_peptide_identity_decision_required")
        errors.extend(structure_review_errors(item, receipt))
    elif decision != "supported_by_explicit_development_code_and_trial_id":
        errors.append("ai_identity_source_check_incomplete")
    elif not {"official_canonical_drug_and_development_aliases", "official_trial_id_program_id_and_intervention_aliases"}.issubset({s.get("role") for s in sources}):
        errors.append("explicit_code_and_trial_identity_sources_missing")
    if (item.get("drug_mapping", {}).get("status") in {"ai_verified_explicit_code_and_trial_identity", "ai_verified_modified_peptide_structure"}
            and item["drug_mapping"] != receipt.get("drug_mapping")):
        errors.append("ai_review_mapping_mismatch")
    checked = {c.get("evidence_id"): c for c in receipt.get("experiment_card_checks", [])}
    for card in item.get("experiment_cards", []):
        check = checked.get(card.get("evidence_id"), {})
        if (check.get("content_sha256") != experiment_content_sha256(card)
                or check.get("numerical_source_check") is not True
                or check.get("completed_experiment_check") is not True):
            errors.append("experiment_content_not_bound_to_ai_review")
    if cutoff:
        try:
            if _instant(receipt.get("reviewed_at")) > _instant(cutoff):
                errors.append("ai_review_after_cutoff")
        except ValueError:
            errors.append("ai_review_time_unknown")
    return sorted(set(errors))


def evidence_eligibility(item, cutoff=None):
    """A pending mapping, undated secondary source or unreviewed card fails closed."""
    reasons = []
    temporal = False
    mapping = item.get("drug_mapping", {})
    cards = item.get("experiment_cards", [])
    if cutoff is None:
        reasons.append("forecast_cutoff_not_supplied")
    else:
        limit = _instant(cutoff)
        try:
            # Never substitute priority_date or filing_date for public availability.
            availability = _instant(item.get("public_available_at") or item.get("publication_date"))
            temporal = availability <= limit
            if not temporal:
                reasons.append("publication_after_cutoff")
        except ValueError:
            reasons.append("publication_availability_unknown")
        for source in mapping.get("sources", []) + mapping.get("document_evidence", []):
            try:
                if _instant(source.get("public_available_at")) > limit:
                    reasons.append("mapping_source_after_cutoff")
                    temporal = False
            except ValueError:
                reasons.append("mapping_source_availability_unknown")
                temporal = False
        for card in cards:
            try:
                if _instant(card.get("public_available_at")) > limit:
                    reasons.append("experiment_source_after_cutoff")
                    temporal = False
            except ValueError:
                reasons.append("experiment_source_availability_unknown")
                temporal = False
    uses_ai_review = mapping.get("status") in {"ai_verified_explicit_code_and_trial_identity", "ai_verified_modified_peptide_structure"}
    ai_errors = research_review_errors(item, cutoff) if uses_ai_review else []
    if (mapping.get("status") not in {"verified_exact_identity", "ai_verified_explicit_code_and_trial_identity", "ai_verified_modified_peptide_structure"}
            or not mapping.get("sources")):
        reasons.append("drug_identity_not_verified")
    reasons.extend(ai_errors)
    if not mapping.get("reviewer") or not mapping.get("reviewed_at"):
        reasons.append("drug_mapping_review_missing")
    if item.get("extraction", {}).get("complete") is not True:
        reasons.append("full_document_extraction_incomplete")
    if not cards:
        reasons.append("experiment_cards_missing")
    if any(not ((c.get("review", {}).get("status") == "scientifically_reviewed"
                 and c.get("review", {}).get("reviewer"))
                or (uses_ai_review and not ai_errors
                    and c.get("review", {}).get("status") == "two_agent_visual_source_check"
                    and c.get("review", {}).get("receipt_sha256") == item.get("research_review", {}).get("receipt_sha256")))
           for c in cards):
        reasons.append("experiment_scientific_review_missing")
    if any(c.get("section_type") not in {"completed_experiment", "preclinical_experiment", "clinical_study"} for c in cards):
        reasons.append("non_experimental_claim_in_efficacy_packet")
    if not re.fullmatch(r"[0-9a-f]{64}", item.get("raw_sha256", "")):
        reasons.append("raw_document_hash_missing")
    reasons = sorted(set(reasons))
    return {"eligible_for_forecast": not reasons, "temporal_eligible": temporal,
            "cutoff": cutoff, "reasons": reasons,
            "review_basis": "two_agent_visual_source_check" if uses_ai_review else "scientific_review_required",
            "research_use_only": True, "clinical_prediction_validated": False,
            "human_review_count": item.get("research_review", {}).get("human_review_count") if uses_ai_review else None,
            "date_policy": "Date-only availability is conservatively end-of-day UTC; priority dates are excluded."}


def connector_status():
    return [
        {"id": "epo_ops", "name": "EPO OPS", "configured": bool(os.getenv("EPO_OPS_KEY") and os.getenv("EPO_OPS_SECRET")),
         "credentials": ["EPO_OPS_KEY", "EPO_OPS_SECRET"], "documentation_url": OPS_DOCS,
         "live_authorized_call_verified": False, "purpose": "bibliography, family, claims/description and image discovery"},
        {"id": "uspto_odp", "name": "USPTO Open Data Portal", "configured": bool(os.getenv("USPTO_ODP_API_KEY")),
         "credentials": ["USPTO_ODP_API_KEY"], "documentation_url": ODP_DOCS,
         "live_authorized_call_verified": False, "purpose": "application search and document download-option discovery"},
        {"id": "wipo", "name": "WIPO PATENTSCOPE", "configured": False,
         "status": "licensed_connector_required_no_website_scraping",
         "documentation_url": "https://www.wipo.int/en/web/patentscope/data/terms_patentscope"},
    ]


def _data_dir(data_dir=None):
    return Path(data_dir or os.getenv("PREDOME_PATENT_DATA_DIR") or DEFAULT_DATA_DIR)


def _public_item(item, cutoff=None):
    item = copy.deepcopy(item)
    # Data origin paths and credentials never belong in the public API.
    for key in ("local_path", "native_text_path", "ocr_directory", "source_directory"):
        item.pop(key, None)
    item["eligibility"] = evidence_eligibility(item, cutoff)
    item["eligible_for_forecast"] = item["eligibility"]["eligible_for_forecast"]
    for card in item.get("experiment_cards", []):
        card["eligible_for_forecast"] = item["eligible_for_forecast"]
    return item


def read_catalog(cutoff=None, drug=None, data_dir=None):
    path = _data_dir(data_dir) / "catalog.json"
    payload = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"items": []}
    expected = payload.get("catalog_sha256")
    actual = canonical_sha256({k: v for k, v in payload.items() if k != "catalog_sha256"})
    if path.exists() and expected != actual:
        raise ValueError("Patent catalogue integrity mismatch")
    items = payload.get("items", [])
    if drug:
        needle = re.sub(r"[^a-z0-9]", "", drug.lower())
        items = [item for item in items if any(needle == re.sub(r"[^a-z0-9]", "", alias.lower())
                 for alias in item.get("drug_mapping", {}).get("aliases", []))]
    items = [_public_item(item, cutoff) for item in items]
    return {"schema_version": SCHEMA_VERSION, "items": items, "catalog_sha256": actual,
            "hash_format": "NFC-sorted-compact-UTF8-JSON-v1", "created_at": payload.get("created_at"),
            "summary": {"documents": len(items), "pages": sum(i.get("page_count", 0) for i in items),
                        "ocr_pages": sum(i.get("extraction", {}).get("completed_pages", 0) for i in items),
                        "eligible_documents": sum(i["eligible_for_forecast"] for i in items),
                        "experiment_cards": sum(len(i.get("experiment_cards", [])) for i in items)},
            "connectors": connector_status(), "clinical_prediction_validated": False}


def get_patent(publication_number, cutoff=None, data_dir=None):
    number = normalize_publication(publication_number)
    return next((item for item in read_catalog(cutoff=cutoff, data_dir=data_dir)["items"]
                 if item["publication_number"] == number), None)


def freeze_evidence_pack(drug, cutoff, data_dir=None):
    catalog = read_catalog(cutoff=cutoff, drug=drug, data_dir=data_dir)
    # Only explicitly reviewed scientific content enters the model. Current legal
    # events, later family members and other unreviewed candidate headings stay out.
    fields = {"publication_number", "title", "publication_date", "public_available_at", "source_url", "pdf_url",
              "raw_sha256", "drug_mapping", "experiment_cards", "eligibility", "eligible_for_forecast",
              "research_review", "review_gaps"}
    accepted = [{key: value for key, value in item.items() if key in fields}
                for item in catalog["items"] if item["eligible_for_forecast"]]
    packet = {"schema_version": SCHEMA_VERSION, "drug": drug, "cutoff": cutoff,
              "catalog_sha256": catalog["catalog_sha256"], "items": accepted,
              "excluded": [{"publication_number": i["publication_number"], "reasons": i["eligibility"]["reasons"]}
                           for i in catalog["items"] if not i["eligible_for_forecast"]],
              "status": "ready" if accepted else "blocked_no_eligible_patent_evidence"}
    packet["packet_sha256"] = canonical_sha256(packet)
    return packet


def _safe_url(url, hosts):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in hosts or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ValueError("Only allowlisted HTTPS patent sources are permitted")
    return url


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Fail closed: never forward API credentials to a redirected host.
        raise ValueError("Patent source redirect requires explicit source review")


def _http(url, headers=None, data=None, maximum=20 * 1024 * 1024):
    opener = urllib.request.build_opener(_NoRedirect())
    request = urllib.request.Request(url, headers=headers or {}, data=data)
    for attempt in range(3):
        try:
            with opener.open(request, timeout=45) as response:
                body = response.read(maximum + 1)
                if len(body) > maximum:
                    raise ValueError("Patent response exceeded configured byte limit")
                return body, response.headers.get("Content-Type", "")
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 503) and attempt < 2:
                delay = exc.headers.get("Retry-After", "")
                time.sleep(min(30, int(delay) if delay.isdigit() else 2 ** (attempt + 1)))
                continue
            # Do not expose request headers, server error bodies or credentials.
            raise RuntimeError("Patent source returned HTTP %s" % exc.code) from None
        except urllib.error.URLError:
            raise RuntimeError("Patent source connection failed") from None


def download_pdf(url, destination, expected_sha256=None, maximum=40 * 1024 * 1024):
    _safe_url(url, PDF_HOSTS)
    body, mime = _http(url, {"User-Agent": "PredomePatentEvidence/1.0", "Accept": "application/pdf"}, maximum=maximum)
    if not body.startswith(b"%PDF-"):
        raise ValueError("Source did not return a PDF")
    digest = hashlib.sha256(body).hexdigest()
    if expected_sha256 and digest != expected_sha256:
        raise ValueError("Downloaded PDF does not match the frozen source hash")
    target = Path(destination)
    if target.exists() and file_sha256(target) != digest:
        raise ValueError("Refusing to overwrite a different version of a patent PDF")
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_bytes(body)
        temporary.replace(target)
    return {"source_url": url, "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "raw_sha256": digest, "bytes": len(body), "content_type": mime,
            "access_method": "targeted_allowlisted_public_pdf"}


def _connector_content_type(connector, content_type):
    if not isinstance(content_type, str) or not 0 < len(content_type) <= 200:
        raise ValueError("Patent connector response content type is required")
    mime = content_type.split(";", 1)[0].strip().lower()
    allowed = {"epo_ops": {"application/xml", "text/xml"},
               "uspto_odp": {"application/json"}}
    suffix = {"epo_ops": "+xml", "uspto_odp": "+json"}.get(connector)
    if connector not in allowed or (mime not in allowed[connector]
            and not (mime.startswith("application/") and mime.endswith(suffix))):
        raise ValueError("Unexpected patent connector response content type")


def _connector_url(connector, url):
    hosts = {"epo_ops": "ops.epo.org", "uspto_odp": "api.uspto.gov"}
    prefixes = {"epo_ops": "/3.2/rest-services/", "uspto_odp": "/api/v1/patent/applications/"}
    if connector not in hosts or not isinstance(url, str):
        raise ValueError("Unknown patent connector response source")
    _safe_url(url, {hosts[connector]})
    parsed = urllib.parse.urlsplit(url)
    if not parsed.path.startswith(prefixes[connector]) or parsed.fragment:
        raise ValueError("Unexpected patent connector response source path")


def _connector_response(connector, url, body, content_type):
    """Retain exact response bytes; a parsed JSON/XML value is not the original."""
    _connector_url(connector, url)
    _connector_content_type(connector, content_type)
    if not isinstance(body, bytes) or not 0 < len(body) <= MAX_CONNECTOR_BYTES:
        raise ValueError("A bounded original patent connector response is required")
    return {"receipt_schema_version": CONNECTOR_RECEIPT_VERSION, "connector": connector,
            "source_url": url, "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "raw_sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body),
            "content_type": content_type, "raw_body_base64": base64.b64encode(body).decode("ascii")}


def _connector_json(body):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("Duplicate patent connector JSON key")
            value[key] = item
        return value
    value = json.loads(body, object_pairs_hook=unique,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite patent connector JSON")))
    if not isinstance(value, dict):
        raise ValueError("Patent connector JSON must be an object")
    return value


def connector_response_bytes(receipt):
    """Mechanically verify an archived response; this is not scientific approval.

    Legacy receipts without original bytes cannot pass. Parsed fields are checked
    against the original as well, so they cannot silently diverge from its hash.
    """
    if not isinstance(receipt, dict) or receipt.get("receipt_schema_version") != CONNECTOR_RECEIPT_VERSION:
        raise ValueError("Versioned original patent connector response required")
    connector = receipt.get("connector")
    _connector_url(connector, receipt.get("source_url"))
    _connector_content_type(connector, receipt.get("content_type"))
    encoded = receipt.get("raw_body_base64")
    if not isinstance(encoded, str) or not 0 < len(encoded) <= 4 * ((MAX_CONNECTOR_BYTES + 2) // 3):
        raise ValueError("Bounded original patent response bytes required")
    try:
        body = base64.b64decode(encoded, validate=True)
    except (ValueError, UnicodeError):
        raise ValueError("Invalid encoded patent response bytes") from None
    if (type(receipt.get("bytes")) is not int or not 0 < len(body) <= MAX_CONNECTOR_BYTES
            or len(body) != receipt["bytes"]
            or hashlib.sha256(body).hexdigest() != receipt.get("raw_sha256")):
        raise ValueError("Original patent connector response integrity mismatch")
    acquired = receipt.get("retrieved_at")
    if not isinstance(acquired, str) or "T" not in acquired:
        raise ValueError("Patent response acquisition requires a timezone-aware timestamp")
    if _instant(acquired) > datetime.now(timezone.utc):
        raise ValueError("Patent response acquisition cannot be in the future")
    if connector == "epo_ops":
        if b"<!DOCTYPE" in body.upper() or b"<!ENTITY" in body.upper():
            raise ValueError("Unsafe XML declaration in patent source")
        ET.fromstring(body)
        if receipt.get("xml") != body.decode("utf-8"):
            raise ValueError("Derived patent XML differs from the original response")
    elif canonical_bytes(receipt.get("data")) != canonical_bytes(_connector_json(body)):
        raise ValueError("Derived patent JSON differs from the original response")
    return body


def verified_ops_family(receipt, publication_number):
    """Re-derive a family from source bytes bound to the exact OPS request."""
    number = normalize_publication(publication_number)
    body = connector_response_bytes(receipt)
    expected_url = EPOOPSClient.BASE + "/family/publication/docdb/" + EPOOPSClient._docdb(number) + "/biblio"
    if (receipt.get("connector") != "epo_ops" or receipt.get("publication_number") != number
            or receipt.get("source_url") != expected_url):
        raise ValueError("OPS family receipt does not bind the requested publication")
    family = parse_ops_family(body.decode("utf-8"))
    if number not in {m["publication_number"] for m in family["members"]}:
        raise ValueError("Requested publication is absent from the original OPS family")
    if canonical_bytes(receipt.get("family")) != canonical_bytes(family):
        raise ValueError("Derived OPS family differs from the original response")
    return {**family, "source_url": receipt["source_url"], "retrieved_at": receipt["retrieved_at"],
            "raw_sha256": receipt["raw_sha256"], "bytes": receipt["bytes"],
            "content_type": receipt["content_type"],
            "receipt_verification": "original_response_bytes_verified_not_scientific_review"}


class EPOOPSClient:
    """Documented OPS v3.2 endpoints; credentials are only read when instantiated."""
    BASE = "https://ops.epo.org/3.2/rest-services"

    def __init__(self, key=None, secret=None, request=None, min_interval=6.0):
        self.key, self.secret = key or os.getenv("EPO_OPS_KEY"), secret or os.getenv("EPO_OPS_SECRET")
        if not self.key or not self.secret:
            raise ValueError("EPO OPS credentials are not configured")
        self.request = request or _http
        self.min_interval = max(0, min_interval)
        self.last_call = 0.0
        self.token, self.expires_at = None, 0.0

    def _auth(self):
        if self.token and time.monotonic() < self.expires_at:
            return
        basic = base64.b64encode((self.key + ":" + self.secret).encode()).decode()
        raw, _ = self.request("https://ops.epo.org/3.2/auth/accesstoken",
                              {"Authorization": "Basic " + basic, "Content-Type": "application/x-www-form-urlencoded"},
                              b"grant_type=client_credentials")
        data = json.loads(raw)
        self.token = data["access_token"]
        self.expires_at = time.monotonic() + max(0, int(data.get("expires_in", 1200)) - 60)

    def _get(self, path):
        self._auth()
        delay = self.min_interval - (time.monotonic() - self.last_call)
        if delay > 0:
            time.sleep(delay)
        self.last_call = time.monotonic()
        url = self.BASE + path
        body, mime = self.request(url, {"Authorization": "Bearer " + self.token, "Accept": "application/xml"})
        receipt = _connector_response("epo_ops", url, body, mime)
        if b"<!DOCTYPE" in body.upper() or b"<!ENTITY" in body.upper():
            raise ValueError("Unsafe XML declaration in patent source")
        ET.fromstring(body)
        return {**receipt, "documentation_url": OPS_DOCS, "xml": body.decode("utf-8")}

    @staticmethod
    def _docdb(publication_number):
        match = PUBLICATION_RE.fullmatch(normalize_publication(publication_number))
        return ".".join(match.groups())

    def search(self, cql, limit=10):
        if not cql or len(cql) > 1000 or not 1 <= limit <= 25:
            raise ValueError("A bounded OPS CQL query is required")
        return self._get("/published-data/search?" + urllib.parse.urlencode({"q": cql, "Range": "1-%s" % limit}))

    def publication(self, publication_number, section="biblio"):
        if section not in {"biblio", "abstract", "fulltext", "description", "claims", "images"}:
            raise ValueError("Unsupported OPS section")
        return self._get("/published-data/publication/docdb/" + self._docdb(publication_number) + "/" + section)

    def family(self, publication_number):
        response = self._get("/family/publication/docdb/" + self._docdb(publication_number) + "/biblio")
        response["family"] = parse_ops_family(response["xml"])
        response["publication_number"] = normalize_publication(publication_number)
        return response


def parse_ops_family(xml):
    if "<!DOCTYPE" in xml.upper() or "<!ENTITY" in xml.upper():
        raise ValueError("Unsafe XML declaration in family data")
    root = ET.fromstring(xml)
    members = []
    family_ids = set()
    for member in root.findall(".//{*}family-member"):
        if member.get("family-id"):
            family_ids.add(member.get("family-id"))
        for doc in member.findall(".//{*}publication-reference/{*}document-id"):
            if doc.get("document-id-type") != "docdb":
                continue
            country, number, kind = (doc.findtext("{*}" + part, "") for part in ("country", "doc-number", "kind"))
            publication = normalize_publication(country + number + kind)
            raw_date = doc.findtext("{*}date")
            published = datetime.strptime(raw_date, "%Y%m%d").date().isoformat() if raw_date else None
            members.append({"publication_number": publication, "publication_date": published})
    return {"id": next(iter(family_ids)) if len(family_ids) == 1 else None,
            "reported_family_ids": sorted(family_ids), "status": "official_ops_family_response",
            "members": sorted(members, key=lambda p: p["publication_number"]),
            "note": "Retrieved family relations are not automatically historical evidence; filter each publication by its own public date."}


class USPTOODPClient:
    """ODP PFW search and document list. Download URLs come from official responses."""
    BASE = "https://api.uspto.gov/api/v1/patent/applications"

    def __init__(self, api_key=None, request=None, min_interval=1.1):
        self.api_key = api_key or os.getenv("USPTO_ODP_API_KEY")
        if not self.api_key:
            raise ValueError("USPTO ODP API key is not configured")
        self.request, self.last_call = request or _http, 0.0
        self.min_interval = max(0, min_interval)

    def _get(self, path):
        delay = self.min_interval - (time.monotonic() - self.last_call)
        if delay > 0:
            time.sleep(delay)
        self.last_call = time.monotonic()
        url = self.BASE + path
        body, mime = self.request(url, {"X-API-KEY": self.api_key, "Accept": "application/json"})
        return {**_connector_response("uspto_odp", url, body, mime),
                "documentation_url": ODP_DOCS, "data": _connector_json(body)}

    def search(self, query, limit=10):
        if not query or len(query) > 1000 or not 1 <= limit <= 25:
            raise ValueError("A bounded ODP query is required")
        return self._get("/search?" + urllib.parse.urlencode({"q": query, "limit": limit}))

    def documents(self, application_number):
        if not re.fullmatch(r"[0-9]{8}", str(application_number)):
            raise ValueError("ODP requires an eight-digit US application number")
        return self._get("/" + str(application_number) + "/documents")

    def application(self, application_number):
        if not re.fullmatch(r"[0-9]{8}", str(application_number)):
            raise ValueError("ODP requires an eight-digit US application number")
        return self._get("/" + str(application_number))
