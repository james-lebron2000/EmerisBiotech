#!/usr/bin/env python3
"""Offline, bounded patent ingestion. No network on import or during OCR.

Examples:
  python scripts/ingest_patents.py build
  python scripts/ingest_patents.py ocr --dpi 150
  python scripts/ingest_patents.py download --publication WO2019125938A1
  python scripts/ingest_patents.py epo --publication WO2019125938A1 --section biblio
  python scripts/ingest_patents.py odp --application 16123456

Raw PDFs and full-page OCR are deployment artifacts, excluded from Git. A signed-off
mapping and scientific review are separate from successful extraction.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from emeris_live.patent_evidence import (DEFAULT_DATA_DIR, SCHEMA_VERSION, EPOOPSClient,
    USPTOODPClient, canonical_sha256, download_pdf, file_sha256, normalize_publication,
    research_review_errors, connector_response_bytes, verified_ops_family)


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def seed_items(root):
    return json.loads((root / "sources" / "pilot_manifest.json").read_text())["items"]


def candidate_identifiers(item, publication_number):
    """Read declared discovery labels without granting a drug-identity review.

    The two original pilot records predate the aliases field. Preserve only
    those exact legacy mappings; an unrelated publication has no inferred drug.
    """
    def identifier(value):
        return (isinstance(value, str) and 0 < len(value) <= 200
                and value == value.strip()
                and not any(ord(char) < 32 or ord(char) == 127 for char in value))

    candidate = item.get("candidate_drug")
    if not identifier(candidate):
        raise ValueError("A bounded candidate_drug identifier is required")
    if "aliases" in item:
        aliases = item["aliases"]
        if (not isinstance(aliases, list) or len(aliases) > 30
                or any(not identifier(alias) for alias in aliases)):
            raise ValueError("Candidate aliases must be a bounded list of nonempty identifiers")
        return candidate, list(dict.fromkeys([candidate, *aliases]))
    legacy = {
        "WO2019125938A1": ["retatrutide", "LY3437943", "LY-3437943"],
        "WO2020240467A1": ["datopotamab deruxtecan", "Dato-DXd", "DS-1062", "DS-1062a", "datopotamab"],
    }
    return candidate, legacy.get(publication_number, [candidate])


def write_connector_receipt(root, response):
    """New acquisition only; retain exact original bytes inside the JSON receipt.

    The filename hashes the entire receipt, including acquisition time. Identical
    acquisition names are never replaced; a later acquisition gets a new file.
    """
    connector_response_bytes(response)
    if response.get("family") is not None:
        verified_ops_family(response, response.get("publication_number"))
    raw = (json.dumps(response, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    prefix = {"epo_ops": "epo", "uspto_odp": "odp"}[response["connector"]]
    directory = Path(root) / "receipts"
    directory.mkdir(parents=True, exist_ok=True)
    if directory.is_symlink():
        raise ValueError("Patent receipt directory must not be a symlink")
    target = directory / (prefix + "-" + hashlib.sha256(raw).hexdigest() + ".json")
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    return target


def official_families(root):
    """Unverified legacy family claims cannot enrich a newly built catalogue."""
    verified, legacy = {}, set()
    for path in sorted((root / "receipts").glob("epo-*.json")):
        if path.is_symlink() or not path.is_file():
            raise ValueError("OPS receipt must be a regular local file")
        receipt = json.loads(path.read_text())
        if not isinstance(receipt, dict):
            raise ValueError("OPS receipt must be an object")
        if receipt.get("family") is None:
            continue
        number = normalize_publication(receipt.get("publication_number"))
        if receipt.get("receipt_schema_version") is None and "raw_body_base64" not in receipt:
            legacy.add(number)
            continue
        family = verified_ops_family(receipt, number)
        acquired = datetime.fromisoformat(family["retrieved_at"].replace("Z", "+00:00"))
        previous = verified.get(number)
        if previous and acquired == previous[0] and family != previous[1]:
            raise ValueError("Conflicting OPS family receipts at the same acquisition time")
        if previous is None or acquired > previous[0]:
            verified[number] = (acquired, family)
    return {number: value[1] for number, value in verified.items()}, legacy


def extract_candidate_cards(item, pages):
    """Locate example headings without pretending to have interpreted the experiment."""
    candidates = []
    for page in pages:
        text = page.get("text", "")
        # Anchor at a line start to avoid treating references to Example 1 as results.
        matches = list(re.finditer(r"(?im)^\s*(?:\[\d+\]\s*)?(?:test\s+)?example\s+([0-9]+)\s*(?:[—–:-]|\n)([^\n]{0,140})", text))
        for match in matches:
            if len(candidates) >= 30:
                break
            start = match.start()
            excerpt = text[start:start + 1400].strip()
            candidates.append({
                "evidence_id": "%s-p%s-example-%s" % (item["publication_number"], page["pdf_page_1_based"], match.group(1)),
                "publication_number": item["publication_number"], "example_id": match.group(1),
                "title": ("Example " + match.group(1) + " " + match.group(2)).strip(),
                "section_type": "example_requires_completed_vs_prophetic_classification",
                "experiment_system": None, "species_or_cell": None, "sample_size": None,
                "dose": None, "comparator": None, "endpoint": None, "result": None, "unit": None,
                "missing_fields": ["experiment_system", "species_or_cell", "sample_size", "dose", "comparator", "endpoint", "result", "unit"],
                "source_url": item["source_url"], "pdf_url": item["pdf_url"] + "#page=" + str(page["pdf_page_1_based"]),
                "pdf_page_1_based": page["pdf_page_1_based"], "source_sha256": item["raw_sha256"],
                "text_sha256": page["text_sha256"], "public_available_at": item["publication_date"],
                "source_excerpt": excerpt, "extraction_method": "tesseract_ocr_heading_locator",
                "review": {"status": "unreviewed_ocr_candidate", "reviewer": None, "reviewed_at": None},
                "eligible_for_forecast": False,
            })
    return candidates


def build_catalog(root):
    records = []
    families, legacy_families = official_families(root)
    dates_path = root / "sources" / "mapping_public_dates.json"
    mapping_dates = json.loads(dates_path.read_text()).get("items", {}) if dates_path.exists() else {}
    for item in seed_items(root):
        number = normalize_publication(item["publication_number"])
        candidate, aliases = candidate_identifiers(item, number)
        pdf = root / "raw" / (number + ".pdf")
        if pdf.exists() and file_sha256(pdf) != item["raw_sha256"]:
            raise ValueError("Raw PDF hash mismatch: " + number)
        pages = []
        for path in sorted((root / "ocr" / number).glob("page-*.json")):
            page = json.loads(path.read_text())
            if page.get("source_sha256") != item["raw_sha256"]:
                raise ValueError("OCR page source hash mismatch")
            if canonical_sha256(page.get("text", "")) != page.get("text_sha256"):
                raise ValueError("OCR text hash mismatch")
            pages.append(page)
        numbers = [p["pdf_page_1_based"] for p in pages]
        complete = sorted(numbers) == list(range(1, item["page_count"] + 1))
        record = {key: item[key] for key in ("publication_number", "title", "publication_date", "original_assignee", "source_url", "pdf_url", "retrieved_at", "raw_sha256", "bytes", "page_count")}
        record.update({
            "public_available_at": item["publication_date"],
            "date_verification": (
                "PDF cover visually checked by AI assistant during pilot; date precision is one day"
                if number in {"WO2019125938A1", "WO2020240467A1"}
                else "Publication date declared in acquisition manifest; independent original-source date verification pending"),
            "drug_mapping": {"candidate_name": candidate, "aliases": aliases,
                "status": "candidate_cross_reference_requires_structure_or_sequence_review",
                "reviewer": None, "reviewed_at": None,
                "basis": "Later patent cross-reference; exact compound/sequence identity has not been verified",
                "sources": [{"url": item["mapping_reference"], "public_available_at": mapping_dates.get(item["mapping_reference"], {}).get("public_available_at"), "role": "later_cross_reference_identity_unreviewed"}]},
            "family": {"id": None, "status": "official_family_lookup_pending", "members": [number],
                       "deduplication_key": "publication:" + number, "deduplication_basis": "single publication only; no inferred family membership"},
            "extraction": {"method": "pdftoppm+tesseract_eng", "complete": complete,
                           "completed_pages": len(pages), "total_pages": item["page_count"],
                           "status": "full_ocr_completed_requires_scientific_review" if complete else "ocr_pending_or_partial",
                           "text_characters": sum(p.get("characters", 0) for p in pages),
                           "page_manifest_sha256": canonical_sha256([{k: p[k] for k in ("pdf_page_1_based", "text_sha256", "source_sha256")} for p in pages])},
            "page_references": [{"pdf_page_1_based": p["pdf_page_1_based"], "text_sha256": p["text_sha256"],
                "characters": p["characters"], "source_url": item["pdf_url"] + "#page=" + str(p["pdf_page_1_based"]),
                "status": "machine_ocr_unreviewed"} for p in pages],
            "experiment_cards": extract_candidate_cards(item, pages),
            "review_gaps": ["Exact drug identity/sequence not verified", "Scientific review of completed versus prophetic experiments incomplete",
                "Whole-document OCR numeric values and structures not fully verified against page images", "Official family membership not yet fetched"],
            "provenance": {"access_method": item["access_method"], "raw_file_verified_locally": pdf.exists(),
                           "download_receipt": "sources/pilot_manifest.json", "human_scientific_review": False},
            "usage_status": "Targeted public research evidence; no bulk PATENTSCOPE scraping; no efficacy or FTO conclusion",
        })
        if not record["drug_mapping"]["sources"][0]["public_available_at"]:
            record["review_gaps"].append("Later mapping source public date requires verification")
        curated = root / "sources" / (number + ".curated.json")
        if curated.exists():
            # Curated files may add machine-reviewed observations but cannot silently
            # promote drug identity or scientific-review state.
            additional = json.loads(curated.read_text())
            record["document_observations"] = additional.get("document_observations", [])
            record["drug_mapping"]["document_evidence"] = additional.get("mapping_observations", [])
            record["candidate_experiment_cards"] = record["experiment_cards"]
            if additional.get("experiment_cards"):
                record["experiment_cards"] = additional["experiment_cards"]
        review_path = root / "sources" / (number + ".identity-review.json")
        if review_path.exists():
            # Promotion requires a content-bound, explicit review receipt. Merely
            # adding machine-extracted observations cannot pass the research gate.
            receipt = json.loads(review_path.read_text())
            selection = receipt.get("experiment_cards_source")
            if selection is not None:
                if not isinstance(selection, dict) or set(selection) != {"filename", "sha256"}:
                    raise ValueError("Invalid versioned experiment-card source metadata")
                name = selection.get("filename", "")
                if not re.fullmatch(re.escape(number) + r"\.[A-Za-z0-9_-]{1,100}\.json", name):
                    raise ValueError("Invalid versioned experiment-card source")
                selected_path = root / "sources" / name
                if selected_path.is_symlink() or not selected_path.is_file() or selected_path.stat().st_size > 1024 * 1024:
                    raise ValueError("A bounded regular experiment-card source is required")
                selected_raw = selected_path.read_bytes()
                if hashlib.sha256(selected_raw).hexdigest() != selection.get("sha256"):
                    raise ValueError("Versioned experiment-card source hash mismatch")
                selected = json.loads(selected_raw)
                cards = selected.get("experiment_cards")
                if (selected.get("schema_version") != "predome-patent-experiment-cards-1.0"
                        or not isinstance(cards, list) or not 1 <= len(cards) <= 20
                        or any(not isinstance(c, dict) or c.get("publication_number") != number
                               or c.get("source_sha256") != item["raw_sha256"]
                               or type(c.get("pdf_page_1_based")) is not int
                               or not 1 <= c["pdf_page_1_based"] <= item["page_count"] for c in cards)):
                    raise ValueError("Invalid selected experiment cards")
                record["excluded_experiment_cards"] = record["experiment_cards"]
                record["card_selection_reason"] = receipt.get("card_selection_reason")
                record["experiment_cards"] = cards
            record["research_review"] = receipt
            if research_review_errors(record):
                raise ValueError("AI source review does not match the evidence: " + number)
            record["drug_mapping"] = receipt["drug_mapping"]
            for card in record["experiment_cards"]:
                card["review"] = {"status": "two_agent_visual_source_check",
                    "reviewer": [r["actor_id"] for r in receipt["reviewers"]],
                    "reviewed_at": receipt["reviewed_at"], "receipt_sha256": receipt["receipt_sha256"],
                    "human_review_count": 0, "human_scientific_review": False,
                    "same_base_model_correlation_disclosed": True}
                # Eligibility is recomputed at query/freeze time with a cutoff.
                card["eligible_for_forecast"] = False
            record["review_gaps"] = receipt["remaining_limitations"]
            record["extraction"]["status"] = "full_ocr_completed_selected_card_ai_source_checked"
        if number in families:
            record["family"] = families[number]
        elif number in legacy_families:
            record["family"]["receipt_verification"] = "legacy_receipt_without_original_response_unverified"
            record["review_gaps"].append("Legacy OPS family receipt has no original response bytes; family enrichment not verified")
        record["record_sha256"] = canonical_sha256(record)
        records.append(record)
    catalog = {"schema_version": SCHEMA_VERSION, "created_at": datetime.now(timezone.utc).isoformat(),
               "purpose": "Traceable patent evidence; extraction and scientific review are independent states", "items": records}
    catalog["catalog_sha256"] = canonical_sha256(catalog)
    write_json(root / "catalog.json", catalog)
    return catalog


def ocr_documents(root, publication=None, dpi=150):
    if not 100 <= dpi <= 300:
        raise ValueError("OCR DPI must be between 100 and 300")
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8", "OMP_THREAD_LIMIT": "1", "OMP_NUM_THREADS": "1"}
    # Deliberately do not pass cloud/model/API credentials into OCR subprocesses.
    for item in seed_items(root):
        number = item["publication_number"]
        if publication and number != normalize_publication(publication):
            continue
        pdf = root / "raw" / (number + ".pdf")
        if file_sha256(pdf) != item["raw_sha256"]:
            raise ValueError("Raw PDF failed source integrity check")
        folder = root / "ocr" / number
        folder.mkdir(parents=True, exist_ok=True)
        for page in range(1, item["page_count"] + 1):
            target = folder / ("page-%04d.json" % page)
            if target.exists():
                saved = json.loads(target.read_text())
                if saved.get("source_sha256") == item["raw_sha256"] and saved.get("text_sha256") == canonical_sha256(saved.get("text", "")):
                    continue
                raise ValueError("Existing OCR page failed integrity; use a new output directory")
            with tempfile.TemporaryDirectory(prefix="predome-patent-page-") as temp:
                image = Path(temp) / "page"
                subprocess.run(["pdftoppm", "-f", str(page), "-l", str(page), "-r", str(dpi), "-gray", "-singlefile", "-png", str(pdf), str(image)],
                               env=env, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=90)
                result = subprocess.run(["tesseract", str(image) + ".png", "stdout", "-l", "eng", "--psm", "3"],
                                        env=env, check=True, capture_output=True, text=True, timeout=90)
            text = result.stdout.strip()
            write_json(target, {"pdf_page_1_based": page, "source_sha256": item["raw_sha256"],
                "text": text, "text_sha256": canonical_sha256(text), "characters": len(text),
                "extractor": "tesseract_eng_psm3", "dpi": dpi, "extracted_at": datetime.now(timezone.utc).isoformat(),
                "review_status": "machine_ocr_unreviewed"})
            print(json.dumps({"publication": number, "page": page, "total_pages": item["page_count"], "characters": len(text)}), flush=True)
            if page % 10 == 0:
                build_catalog(root)
        build_catalog(root)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["build", "ocr", "download", "epo", "odp"])
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--publication")
    parser.add_argument("--application")
    parser.add_argument("--query")
    parser.add_argument("--section", default="biblio", choices=["biblio", "abstract", "fulltext", "description", "claims", "images", "family"])
    parser.add_argument("--dpi", type=int, default=150)
    args = parser.parse_args()
    root = args.data_dir.resolve()
    if args.command == "ocr":
        ocr_documents(root, args.publication, args.dpi)
    elif args.command == "download":
        if not args.publication:
            parser.error("download requires a single --publication from the source manifest")
        number = normalize_publication(args.publication)
        item = next((i for i in seed_items(root) if i["publication_number"] == number), None)
        if item is None:
            parser.error("publication is not in the approved targeted manifest")
        receipt = download_pdf(item["pdf_url"], root / "raw" / (number + ".pdf"), item["raw_sha256"])
        write_json(root / "receipts" / (number + ".download.json"), receipt)
    elif args.command == "epo":
        client = EPOOPSClient()
        if args.query:
            result = client.search(args.query)
        elif args.publication:
            result = client.family(args.publication) if args.section == "family" else client.publication(args.publication, args.section)
        else:
            parser.error("epo requires --query or --publication")
        write_connector_receipt(root, result)
    elif args.command == "odp":
        client = USPTOODPClient()
        if args.query:
            result = client.search(args.query)
        elif args.application:
            result = client.documents(args.application)
        else:
            parser.error("odp requires --query or --application")
        write_connector_receipt(root, result)
    catalog = build_catalog(root)
    print(json.dumps({"catalog_sha256": catalog["catalog_sha256"], "documents": len(catalog["items"]),
                      "ocr_pages": sum(i["extraction"]["completed_pages"] for i in catalog["items"])}))


if __name__ == "__main__":
    main()
