"""Injected official-client responses test provenance, never network or science."""
import base64
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from emeris_live import patent_evidence as patents


NUMBER = "WO2019125938A1"
XML = b'''<?xml version="1.0" encoding="UTF-8"?>
<root xmlns="urn:fixture"><family-member family-id="123">
 <publication-reference><document-id document-id-type="docdb"><country>WO</country>
 <doc-number>2019125938</doc-number><kind>A1</kind><date>20190627</date>
 </document-id></publication-reference></family-member></root>\n'''


@pytest.fixture
def ingest():
    path = Path(__file__).resolve().parents[1] / "scripts/ingest_patents.py"
    spec = importlib.util.spec_from_file_location("patent_ingest_provenance_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def ops_response(body=XML, mime="application/xml; charset=UTF-8"):
    calls = []
    def request(url, headers, data=None):
        calls.append((url, headers, data))
        if url.endswith("accesstoken"):
            return b'{"access_token":"fixture-token","expires_in":1200}', "application/json"
        return body, mime
    response = patents.EPOOPSClient("fixture-key", "fixture-secret", request=request,
                                   min_interval=0).family(NUMBER)
    assert len(calls) == 2
    return response


def seed(root):
    sources = root / "sources"
    sources.mkdir()
    item = {"publication_number": NUMBER, "title": "Synthetic provenance fixture",
        "publication_date": "2019-06-27", "original_assignee": "Fixture only",
        "source_url": "https://patents.google.com/patent/" + NUMBER,
        "pdf_url": "https://patentimages.storage.googleapis.com/fixture.pdf",
        "retrieved_at": "2026-01-01T00:00:00Z", "raw_sha256": "a" * 64,
        "bytes": 10, "page_count": 1, "candidate_drug": "Fixture drug",
        "mapping_reference": "https://example.org/fixture", "access_method": "synthetic_test"}
    (sources / "pilot_manifest.json").write_text(json.dumps({"items": [item]}))


def store_untrusted(root, response):
    directory = root / "receipts"
    directory.mkdir(exist_ok=True)
    (directory / "epo-untrusted-fixture.json").write_text(json.dumps(response))


def test_clients_preserve_exact_raw_bytes_and_derived_values():
    response = ops_response()
    assert patents.connector_response_bytes(response) == XML
    assert response["bytes"] == len(XML)
    assert response["content_type"] == "application/xml; charset=UTF-8"
    assert response["raw_sha256"] == hashlib.sha256(XML).hexdigest()
    assert patents.verified_ops_family(response, NUMBER)["id"] == "123"
    assert "fixture-secret" not in json.dumps(response)
    raw = b'{ "documentBag" : [], "label" : "e\\u0301" }\r\n'
    calls = []
    def request(url, headers):
        calls.append((url, headers))
        return raw, "application/json; charset=utf-8"
    odp = patents.USPTOODPClient("fixture-key", request=request, min_interval=0)
    response = odp.documents("16123456")
    assert patents.connector_response_bytes(response) == raw
    assert response["bytes"] == len(raw)
    assert response["content_type"] == "application/json; charset=utf-8"
    assert response["raw_sha256"] != patents.canonical_sha256(response["data"])
    assert calls[0][0].endswith("/16123456/documents")
    assert "fixture-key" not in json.dumps(response)


@pytest.mark.parametrize("connector,body,mime", [
    ("epo", XML, "text/html"), ("epo", b"<html/>", "text/html"),
    ("epo", b"<!DOCTYPE root><root/>", "application/xml"),
    ("odp", b"{}", "text/html"), ("odp", b'{"n":NaN}', "application/json"),
    ("odp", b'{"n":1,"n":2}', "application/json"),
    ("odp", b"[]", "application/json"), ("odp", b"", "application/json"),
])
def test_injected_client_responses_fail_closed(connector, body, mime):
    with pytest.raises(ValueError):
        if connector == "epo":
            ops_response(body, mime)
        else:
            patents.USPTOODPClient("fixture-key", request=lambda *_: (body, mime),
                                   min_interval=0).documents("16123456")


def test_response_size_bound_also_applies_to_injected_transport(monkeypatch):
    monkeypatch.setattr(patents, "MAX_CONNECTOR_BYTES", 4)
    with pytest.raises(ValueError, match="bounded"):
        ops_response()


def test_receipt_roundtrip_does_not_overwrite_identical_acquisition(tmp_path, ingest):
    response = ops_response()
    path = ingest.write_connector_receipt(tmp_path, response)
    before = path.read_bytes()
    assert patents.connector_response_bytes(json.loads(before)) == XML
    with pytest.raises(FileExistsError):
        ingest.write_connector_receipt(tmp_path, response)
    assert path.read_bytes() == before
    newer = deepcopy(response)
    newer["retrieved_at"] = "2026-01-01T00:00:00Z"
    second = ingest.write_connector_receipt(tmp_path, newer)
    assert second != path and path.read_bytes() == before
    assert len(list((tmp_path / "receipts").glob("epo-*.json"))) == 2


def test_real_build_enriches_only_from_reparsed_original_and_keeps_review_separate(tmp_path, ingest):
    seed(tmp_path)
    response = ops_response()
    path = ingest.write_connector_receipt(tmp_path, response)
    receipt_before = path.read_bytes()
    item = ingest.build_catalog(tmp_path)["items"][0]
    family = item["family"]
    assert family["id"] == "123" and family["members"][0]["publication_number"] == NUMBER
    assert family["raw_sha256"] == hashlib.sha256(XML).hexdigest()
    assert family["bytes"] == len(XML) and family["content_type"].startswith("application/xml")
    assert family["receipt_verification"] == "original_response_bytes_verified_not_scientific_review"
    assert item["provenance"]["human_scientific_review"] is False
    assert item["drug_mapping"]["status"] == "candidate_cross_reference_requires_structure_or_sequence_review"
    assert patents.evidence_eligibility(item, "2026-01-01")["eligible_for_forecast"] is False
    assert path.read_bytes() == receipt_before


@pytest.mark.parametrize("mutation", ["raw", "hash", "bytes", "mime", "family", "target", "url_target",
    "url_host", "url_query", "derived_xml", "missing_raw", "invalid_base64", "acquisition_time"])
def test_real_build_rejects_tampered_receipt_before_replacing_catalog(tmp_path, ingest, mutation):
    seed(tmp_path)
    response = ops_response()
    if mutation == "raw": response["raw_body_base64"] = base64.b64encode(XML + b" ").decode()
    elif mutation == "hash": response["raw_sha256"] = "0" * 64
    elif mutation == "bytes": response["bytes"] += 1
    elif mutation == "mime": response["content_type"] = "text/html"
    elif mutation == "family": response["family"]["id"] = "invented-family"
    elif mutation == "target": response["publication_number"] = "WO2020240467A1"
    elif mutation == "url_target": response["source_url"] = response["source_url"].replace("2019125938", "2020240467")
    elif mutation == "url_host": response["source_url"] = response["source_url"].replace("ops.epo.org", "evil.example")
    elif mutation == "url_query": response["source_url"] += "?unbound=1"
    elif mutation == "derived_xml": response["xml"] += " "
    elif mutation == "missing_raw": del response["raw_body_base64"]
    elif mutation == "invalid_base64": response["raw_body_base64"] = "!!not-base64!!"
    elif mutation == "acquisition_time": response["retrieved_at"] = "2026-01-01"
    store_untrusted(tmp_path, response)
    original = b'{"existing_catalog":"preserve verbatim"}\n'
    (tmp_path / "catalog.json").write_bytes(original)
    with pytest.raises(ValueError):
        ingest.build_catalog(tmp_path)
    assert (tmp_path / "catalog.json").read_bytes() == original


def test_original_xml_must_contain_requested_publication(tmp_path, ingest):
    seed(tmp_path)
    raw = XML.replace(b"2019125938", b"2020240467")
    response = ops_response(raw)
    store_untrusted(tmp_path, response)
    with pytest.raises(ValueError, match="absent"):
        ingest.build_catalog(tmp_path)


def test_legacy_family_receipt_stays_unverified_and_unchanged(tmp_path, ingest):
    seed(tmp_path)
    legacy = {"publication_number": NUMBER, "family": {"id": "unverified-claim", "members": [NUMBER]},
        "source_url": "https://ops.epo.org/fixture", "raw_sha256": "a" * 64,
        "retrieved_at": "2026-01-01T00:00:00Z"}
    store_untrusted(tmp_path, legacy)
    path = tmp_path / "receipts/epo-untrusted-fixture.json"
    before = path.read_bytes()
    item = ingest.build_catalog(tmp_path)["items"][0]
    assert item["family"]["id"] is None
    assert item["family"]["status"] == "official_family_lookup_pending"
    assert item["family"]["receipt_verification"] == "legacy_receipt_without_original_response_unverified"
    assert any("Legacy OPS" in gap for gap in item["review_gaps"])
    assert path.read_bytes() == before
    with pytest.raises(ValueError, match="Versioned original"):
        patents.connector_response_bytes(legacy)


def test_odp_derived_json_cannot_diverge_from_original(tmp_path, ingest):
    response = patents.USPTOODPClient("fixture-key", request=lambda *_: (b'{ "documentBag" : [] }\n', "application/json"),
                                    min_interval=0).documents("16123456")
    response["data"]["documentBag"] = [{"invented": True}]
    with pytest.raises(ValueError, match="Derived patent JSON"):
        ingest.write_connector_receipt(tmp_path, response)
    assert not (tmp_path / "receipts").exists()


@pytest.mark.parametrize("connector", ["epo", "odp"])
def test_cli_archives_original_response_through_real_build(tmp_path, ingest, monkeypatch, connector):
    seed(tmp_path)
    if connector == "epo":
        client = patents.EPOOPSClient("fixture-key", "fixture-secret", min_interval=0,
            request=lambda url, *_: (b'{"access_token":"fixture-token"}', "application/json")
                if url.endswith("accesstoken") else (XML, "application/xml"))
        monkeypatch.setattr(ingest, "EPOOPSClient", lambda: client)
        arguments = ["epo", "--publication", NUMBER, "--section", "family"]
    else:
        client = patents.USPTOODPClient("fixture-key", min_interval=0,
            request=lambda *_: (b'{ "documentBag" : [] }\n', "application/json"))
        monkeypatch.setattr(ingest, "USPTOODPClient", lambda: client)
        arguments = ["odp", "--application", "16123456"]
    monkeypatch.setattr(ingest.sys, "argv", ["ingest_patents.py", *arguments, "--data-dir", str(tmp_path)])
    ingest.main()
    paths = list((tmp_path / "receipts").glob(connector + "-*.json"))
    assert len(paths) == 1
    receipt = json.loads(paths[0].read_text())
    assert len(patents.connector_response_bytes(receipt)) == receipt["bytes"]
    catalog = json.loads((tmp_path / "catalog.json").read_text())
    assert catalog["items"][0]["family"]["id"] == ("123" if connector == "epo" else None)
