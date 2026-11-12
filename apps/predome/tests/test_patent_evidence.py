import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from emeris_live import patent_evidence as patents


def load_ingester():
    path = Path(__file__).resolve().parents[1] / "scripts" / "ingest_patents.py"
    spec = importlib.util.spec_from_file_location("patent_candidate_ingest_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def candidate_manifest(root, **updates):
    """Synthetic discovery metadata, with no identity or scientific approval."""
    item = {"publication_number": "WO2026000001A1", "title": "Synthetic candidate",
            "publication_date": "2026-01-01", "original_assignee": "Fixture only",
            "source_url": "https://patents.google.com/patent/WO2026000001A1",
            "pdf_url": "https://patentimages.storage.googleapis.com/fixture.pdf",
            "retrieved_at": "2026-01-02T00:00:00Z", "raw_sha256": "a" * 64,
            "bytes": 10, "page_count": 1, "candidate_drug": "Fixture candidate III",
            "mapping_reference": "https://example.org/fixture", "access_method": "synthetic_test"}
    item.update(updates)
    sources = root / "sources"
    sources.mkdir(exist_ok=True)
    (sources / "pilot_manifest.json").write_text(json.dumps({"items": [item]}))
    return item


def reviewed_item():
    # Synthetic control record exercises gates; it is not a real scientific review.
    return {"publication_number": "WO2019125938A1", "publication_date": "2019-06-27",
            "priority_date": "2017-01-01", "raw_sha256": "a" * 64,
            "drug_mapping": {"status": "verified_exact_identity", "reviewer": "test-reviewer",
                             "reviewed_at": "2026-01-01T00:00:00Z", "aliases": ["Example drug"],
                             "sources": [{"public_available_at": "2020-06-01"}]},
            "extraction": {"complete": True},
            "experiment_cards": [{"section_type": "completed_experiment", "public_available_at": "2019-06-27",
                                  "review": {"status": "scientifically_reviewed", "reviewer": "test-reviewer"}}]}


class PatentEvidenceTests(unittest.TestCase):
    def test_new_candidate_uses_only_declared_name_when_aliases_are_absent(self):
        ingest = load_ingester()
        with tempfile.TemporaryDirectory() as directory, patch.object(patents, "_http", side_effect=AssertionError("network")):
            root = Path(directory)
            source = candidate_manifest(root)
            item = ingest.build_catalog(root)["items"][0]
            self.assertEqual(item["drug_mapping"]["aliases"], [source["candidate_drug"]])
            self.assertEqual(len(patents.read_catalog(drug=source["candidate_drug"], data_dir=root)["items"]), 1)
            for unrelated in ("Dato-DXd", "DS-1062", "datopotamab", "retatrutide", "LY3437943"):
                self.assertEqual(patents.read_catalog(drug=unrelated, data_dir=root)["items"], [])
            self.assertEqual(item["family"]["status"], "official_family_lookup_pending")
            self.assertEqual(item["drug_mapping"]["status"], "candidate_cross_reference_requires_structure_or_sequence_review")
            gate = patents.evidence_eligibility(item, "2026-10-27")
            self.assertFalse(gate["eligible_for_forecast"])
            self.assertIn("drug_identity_not_verified", gate["reasons"])
            self.assertEqual(patents.freeze_evidence_pack(source["candidate_drug"], "2026-10-27", root)["items"], [])

    def test_new_candidate_aliases_are_discovery_labels_not_an_identity_review(self):
        ingest = load_ingester()
        for aliases in ([], ["FC-003", "Fixture candidate III", "FC-003"]):
            with self.subTest(aliases=aliases), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source = candidate_manifest(root, aliases=aliases)
                item = ingest.build_catalog(root)["items"][0]
                self.assertEqual(item["drug_mapping"]["aliases"],
                                 ["Fixture candidate III", "FC-003"] if aliases else ["Fixture candidate III"])
                for alias in [source["candidate_drug"], *aliases]:
                    self.assertEqual(len(patents.read_catalog(drug=alias, data_dir=root)["items"]), 1)
                    self.assertEqual(patents.freeze_evidence_pack(alias, "2026-10-27", root)["items"], [])
                self.assertIsNone(item["drug_mapping"]["reviewer"])
                self.assertIsNone(item["drug_mapping"]["reviewed_at"])
                self.assertFalse(item["provenance"]["human_scientific_review"])
                self.assertEqual(item["family"]["status"], "official_family_lookup_pending")

    def test_new_candidate_familiar_drug_labels_do_not_inherit_pilot_cover_review(self):
        ingest = load_ingester()
        for name in ("retatrutide", "datopotamab deruxtecan"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                candidate_manifest(root, candidate_drug=name, aliases=[name],
                                   visual_review={"scope": "cover", "verified_fields": ["publication_date"]})
                item = ingest.build_catalog(root)["items"][0]
                self.assertEqual(item["publication_date"], "2026-01-01")
                self.assertIn("declared in acquisition manifest", item["date_verification"])
                self.assertIn("independent original-source date verification pending", item["date_verification"])
                self.assertNotIn("visually checked", item["date_verification"])
                self.assertIsNone(item["drug_mapping"]["reviewer"])
                self.assertFalse(patents.evidence_eligibility(item, "2026-10-27")["eligible_for_forecast"])

    def test_two_exact_legacy_publications_keep_their_previous_aliases(self):
        ingest = load_ingester()
        legacy = {
            "WO2019125938A1": ["retatrutide", "LY3437943", "LY-3437943"],
            "WO2020240467A1": ["datopotamab deruxtecan", "Dato-DXd", "DS-1062", "DS-1062a", "datopotamab"],
        }
        for publication, aliases in legacy.items():
            with self.subTest(publication=publication), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                candidate_manifest(root, publication_number=publication)
                item = ingest.build_catalog(root)["items"][0]
                self.assertEqual(item["drug_mapping"]["aliases"], aliases)
                self.assertEqual(item["date_verification"], "PDF cover visually checked by AI assistant during pilot; date precision is one day")
                self.assertFalse(patents.evidence_eligibility(item, "2026-10-27")["eligible_for_forecast"])
                # An explicit new manifest replaces only discovery labels, without
                # inheriting the pilot defaults or granting a verified identity.
                candidate_manifest(root, publication_number=publication, aliases=["Declared-only"])
                item = ingest.build_catalog(root)["items"][0]
                self.assertEqual(item["drug_mapping"]["aliases"], ["Fixture candidate III", "Declared-only"])
                self.assertEqual(item["drug_mapping"]["status"], "candidate_cross_reference_requires_structure_or_sequence_review")

    def test_bad_candidate_identifiers_fail_before_replacing_catalog(self):
        ingest = load_ingester()
        invalid = [
            {"candidate_drug": value} for value in (None, False, 1, [], {}, "", " ", " leading", "trailing ", "line\nbreak", "x" * 201)
        ] + [
            {"aliases": value} for value in (None, False, 1, {}, "FC-003", [None], [1], [False], [[]], [""], [" "],
                [" leading"], ["trailing "], ["line\nbreak"], ["control\x7f"], ["x" * 201], ["FC"] * 31)
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate_manifest(root)
            ingest.build_catalog(root)
            existing = (root / "catalog.json").read_bytes()
            for fields in invalid:
                with self.subTest(fields=fields):
                    candidate_manifest(root, **fields)
                    with self.assertRaises(ValueError):
                        ingest.build_catalog(root)
                    self.assertEqual((root / "catalog.json").read_bytes(), existing)

    def test_canonical_hash_key_order_unicode_and_nan(self):
        self.assertEqual(patents.canonical_sha256({"b": 1, "a": "e\u0301"}), patents.canonical_sha256({"a": "é", "b": 1}))
        with self.assertRaises(ValueError):
            patents.canonical_sha256({"value": float("nan")})
        with self.assertRaises(ValueError):
            patents.canonical_sha256({"é": 1, "e\u0301": 2})

    def test_publication_normalization_and_path_traversal(self):
        self.assertEqual(patents.normalize_publication("WO 2019125938 A1"), "WO2019125938A1")
        for bad in ("../../etc/passwd", "WO2019125938", "https://evil.test", "WO2019A1/claims"):
            with self.assertRaises(ValueError):
                patents.normalize_publication(bad)

    def test_reviewed_control_and_required_cutoff(self):
        self.assertTrue(patents.evidence_eligibility(reviewed_item(), "2021-01-01")["eligible_for_forecast"])
        self.assertFalse(patents.evidence_eligibility(reviewed_item())["eligible_for_forecast"])

    def test_priority_does_not_override_publication_date(self):
        gate = patents.evidence_eligibility(reviewed_item(), "2018-01-01")
        self.assertIn("publication_after_cutoff", gate["reasons"])
        self.assertFalse(gate["temporal_eligible"])

    def test_later_mapping_and_unknown_date_cannot_enter_earlier_packet(self):
        for available in (None, "2025-01-01"):
            item = reviewed_item()
            item["drug_mapping"]["sources"][0]["public_available_at"] = available
            gate = patents.evidence_eligibility(item, "2021-01-01")
            self.assertFalse(gate["eligible_for_forecast"])
            self.assertFalse(gate["temporal_eligible"])

    def test_same_day_timestamp_is_conservative(self):
        item = reviewed_item()
        item["drug_mapping"]["sources"][0]["public_available_at"] = "2019-06-26"
        self.assertFalse(patents.evidence_eligibility(item, "2019-06-27T08:00:00Z")["temporal_eligible"])
        self.assertTrue(patents.evidence_eligibility(item, "2019-06-28T00:00:00Z")["temporal_eligible"])

    def test_naive_timestamps_rejected(self):
        with self.assertRaises(ValueError):
            patents.evidence_eligibility(reviewed_item(), "2026-10-22T12:00:00")

    def test_unknown_mapping_incomplete_ocr_and_unreviewed_cards_fail_closed(self):
        item = reviewed_item()
        item["drug_mapping"]["status"] = "candidate"
        item["extraction"]["complete"] = False
        item["experiment_cards"][0]["review"]["status"] = "machine_extracted"
        gate = patents.evidence_eligibility(item, "2021-01-01")
        self.assertFalse(gate["eligible_for_forecast"])
        self.assertIn("drug_identity_not_verified", gate["reasons"])
        self.assertIn("experiment_scientific_review_missing", gate["reasons"])

    def test_claim_and_prophetic_examples_do_not_become_efficacy(self):
        for section in ("claim", "prophetic_example", "background"):
            item = reviewed_item()
            item["experiment_cards"][0]["section_type"] = section
            self.assertFalse(patents.evidence_eligibility(item, "2021-01-01")["eligible_for_forecast"])

    def test_freeze_filters_and_hashes_the_actual_packet(self):
        with tempfile.TemporaryDirectory() as directory:
            item = reviewed_item()
            item["family"] = {"members": [{"publication_date": "2028-01-01"}]}
            item["candidate_experiment_cards"] = [{"unreviewed": True}]
            data = {"items": [item]}
            data["catalog_sha256"] = patents.canonical_sha256(data)
            path = Path(directory) / "catalog.json"
            path.write_text(json.dumps(data))
            blocked = patents.freeze_evidence_pack("Example drug", "2018-01-01", directory)
            self.assertEqual(blocked["status"], "blocked_no_eligible_patent_evidence")
            self.assertEqual(blocked["items"], [])
            ready = patents.freeze_evidence_pack("Example drug", "2021-01-01", directory)
            self.assertEqual(len(ready["items"]), 1)
            self.assertNotIn("family", ready["items"][0])
            self.assertNotIn("candidate_experiment_cards", ready["items"][0])
            self.assertEqual(ready["packet_sha256"], patents.canonical_sha256({k: v for k, v in ready.items() if k != "packet_sha256"}))

    def test_catalog_tampering_and_unsigned_files_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.json"
            for data in ({"items": []}, {"items": [], "catalog_sha256": "bad"}):
                path.write_text(json.dumps(data))
                with self.assertRaises(ValueError):
                    patents.read_catalog(data_dir=directory)

    def test_read_only_catalog_does_not_network_or_expose_paths(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(patents, "_http", side_effect=AssertionError("network")):
            item = reviewed_item()
            item["local_path"] = "/private/secret/file.pdf"
            data = {"items": [item]}
            data["catalog_sha256"] = patents.canonical_sha256(data)
            (Path(directory) / "catalog.json").write_text(json.dumps(data))
            result = patents.get_patent(item["publication_number"], data_dir=directory)
            self.assertNotIn("local_path", result)
            self.assertIsNone(patents.get_patent("WO2020240467A1", data_dir=directory))

    def test_allowlist_and_credentials_in_url_are_rejected(self):
        for url in ("http://patentimages.storage.googleapis.com/x.pdf", "https://127.0.0.1/x.pdf", "https://evil.test/x.pdf", "https://secret@api.uspto.gov/x.pdf"):
            with self.assertRaises(ValueError):
                patents.download_pdf(url, "/unused")

    def test_pdf_content_hash_and_immutable_target(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "source.pdf"
            with patch.object(patents, "_http", return_value=(b"<html>denied</html>", "text/html")):
                with self.assertRaises(ValueError):
                    patents.download_pdf("https://patentimages.storage.googleapis.com/x.pdf", target)
            with patch.object(patents, "_http", return_value=(b"%PDF-test", "application/pdf")):
                with self.assertRaises(ValueError):
                    patents.download_pdf("https://patentimages.storage.googleapis.com/x.pdf", target, "0" * 64)
                receipt = patents.download_pdf("https://patentimages.storage.googleapis.com/x.pdf", target)
                self.assertEqual(receipt["raw_sha256"], patents.file_sha256(target))
                target.write_bytes(b"%PDF-different")
                with self.assertRaises(ValueError):
                    patents.download_pdf("https://patentimages.storage.googleapis.com/x.pdf", target)

    def test_missing_connector_keys_fail_without_network(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(ValueError):
                patents.EPOOPSClient()
            with self.assertRaises(ValueError):
                patents.USPTOODPClient()

    def test_ops_auth_and_documented_docdb_path(self):
        calls = []
        def request(url, headers, data=None):
            calls.append((url, headers, data))
            return (b'{"access_token":"token","expires_in":1200}', "application/json") if url.endswith("accesstoken") else (b"<root/>", "application/xml")
        client = patents.EPOOPSClient("key", "secret", request=request, min_interval=0)
        response = client.publication("WO2019125938A1")
        self.assertEqual(calls[0][0], "https://ops.epo.org/3.2/auth/accesstoken")
        self.assertTrue(calls[1][0].endswith("/published-data/publication/docdb/WO.2019125938.A1/biblio"))
        self.assertNotIn("secret", json.dumps(response))

    def test_ops_dtd_rejected(self):
        def request(url, headers, data=None):
            return (b'{"access_token":"t"}', "application/json") if url.endswith("accesstoken") else (b'<!DOCTYPE x [<!ENTITY a "b">]><x/>', "application/xml")
        with self.assertRaises(ValueError):
            patents.EPOOPSClient("key", "secret", request=request, min_interval=0).publication("WO2019125938A1")

    def test_family_parser_preserves_publication_dates(self):
        xml = '<root xmlns="urn:test"><family-member family-id="123"><publication-reference><document-id document-id-type="docdb"><country>WO</country><doc-number>2019125938</doc-number><kind>A1</kind><date>20190627</date></document-id></publication-reference></family-member></root>'
        family = patents.parse_ops_family(xml)
        self.assertEqual(family["id"], "123")
        self.assertEqual(family["members"][0]["publication_date"], "2019-06-27")

    def test_document_mapping_observation_also_respects_cutoff(self):
        item = reviewed_item()
        item["drug_mapping"]["document_evidence"] = [{"public_available_at": "2025-01-01"}]
        self.assertIn("mapping_source_after_cutoff", patents.evidence_eligibility(item, "2021-01-01")["reasons"])

    def test_odp_application_not_publication_number(self):
        calls = []
        def request(url, headers):
            calls.append((url, headers))
            return b'{"documentBag":[]}', "application/json"
        client = patents.USPTOODPClient("secret", request=request, min_interval=0)
        response = client.documents("16123456")
        self.assertEqual(calls[0][0], "https://api.uspto.gov/api/v1/patent/applications/16123456/documents")
        self.assertNotIn("secret", json.dumps(response))
        with self.assertRaises(ValueError):
            client.documents("US20200123456A1")

    def test_real_dato_ai_review_is_research_only_and_retatrutide_stays_blocked(self):
        item = patents.get_patent("WO2020240467A1", "2026-10-23")
        self.assertTrue(item["eligible_for_forecast"])
        self.assertEqual(item["eligibility"]["human_review_count"], 0)
        self.assertEqual(item["eligibility"]["review_basis"], "two_agent_visual_source_check")
        self.assertFalse(item["eligibility"]["clinical_prediction_validated"])
        self.assertEqual(item["experiment_cards"][0]["result"]["groups"][0]["denominator"], 18)
        self.assertEqual(item["experiment_cards"][0]["result"]["groups"][1]["denominator"], 34)
        self.assertFalse(patents.get_patent("WO2019125938A1", "2026-10-23")["eligible_for_forecast"])

    def test_current_identity_page_cannot_be_backdated_into_historical_forecast(self):
        item = patents.get_patent("WO2020240467A1", "2026-10-22T15:40:00Z")
        self.assertFalse(item["eligible_for_forecast"])
        self.assertIn("mapping_source_after_cutoff", item["eligibility"]["reasons"])
        self.assertIn("ai_review_after_cutoff", item["eligibility"]["reasons"])

    def test_ai_review_binds_numeric_card_and_mapping(self):
        item = patents.get_patent("WO2020240467A1", "2026-10-23")
        item["experiment_cards"][0]["result"]["groups"][0]["denominator"] = 88
        self.assertIn("experiment_content_not_bound_to_ai_review", patents.evidence_eligibility(item, "2026-10-23")["reasons"])
        item = patents.get_patent("WO2020240467A1", "2026-10-23")
        item["drug_mapping"]["sources"] = [{"public_available_at": "2020-01-01"}]
        self.assertIn("ai_review_mapping_mismatch", patents.evidence_eligibility(item, "2026-10-23")["reasons"])

    def test_ai_receipt_cannot_be_silently_changed_or_use_single_role(self):
        item = patents.get_patent("WO2020240467A1", "2026-10-23")
        item["research_review"]["reviewers"].pop()
        errors = patents.evidence_eligibility(item, "2026-10-23")["reasons"]
        self.assertIn("two_completed_ai_source_checks_required", errors)
        self.assertIn("ai_review_receipt_integrity_mismatch", errors)

    def test_frozen_dato_packet_discloses_review_limit_and_has_primary_sources(self):
        packet = patents.freeze_evidence_pack("Dato-DXd", "2026-10-23")
        self.assertEqual(packet["status"], "ready")
        item = packet["items"][0]
        self.assertEqual(item["research_review"]["human_review_count"], 0)
        self.assertTrue(item["research_review"]["same_base_model_correlation_disclosed"])
        self.assertEqual({s["source_id"] for s in item["drug_mapping"]["sources"]}, {"patent", "nci", "trial"})
        self.assertNotIn("candidate_experiment_cards", item)
        self.assertNotIn("family", item)


if __name__ == "__main__":
    unittest.main()
