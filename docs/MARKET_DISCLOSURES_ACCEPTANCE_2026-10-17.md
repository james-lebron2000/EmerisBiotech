# Market discovery and official disclosure acceptance — 2026-10-17

This is a local research-product checkpoint. It does not establish all-market completeness, verified drug rights, clinical predictive performance, profitable investment decisions, or public customer deployment.

## Real collection completed

The live registry database contains **93 configured securities**: US 41, A-share 24, Hong Kong 28. All 37 additions this iteration completed their configured ClinicalTrials.gov sponsor queries: 14 US exact-name associations and 23 A/HK source-configured securities. There are **27,067 security–trial associations** and **25,895 distinct NCT identifiers**. These are historical/current registry observations, not counts of owned drugs.

Current exchange directories contain 15,543 raw unique security identities; 15,167 supported normalized identities and 376 unsupported symbols are reported separately. Screening identifies 847 candidates (US492/A284/HK71); 12,898 remain unclassified and 1,422 are excluded by the current rules. The full-market drug-company denominator remains unknown. Full-pipeline verification count remains zero.

The 14 US mappings carry `automated_source_review`, archived Nasdaq/CTG evidence and `human_sign_off=false`. The strengthened validator independently rechecked all 14 records successfully. It binds the candidate and proposal hashes, raw current security record, exact registry sponsor, NCT, source URLs, timestamps and SHA-256. It does not grant human, scientific, ownership or investment approval. The A/HK source configuration retains independent mapping-review gaps.

## Official pipeline observations

| Source | Actual result | Boundary |
|---|---|---|
| Hengrui official English clinical development chart | 274 drug–indication–phase observations, 113 distinct disclosed names; 392,327 bytes | As of 2026-07; publication date unknown; not a portfolio census |
| Pfizer official pipeline page | HTTP403, `fetch_failed` | No invented rows or access bypass |
| Innovent official historical interim PDF | 476,031 bytes archived, `parse_blocked` | Layout extraction and page-level reconciliation remain pending |

Hengrui source SHA-256: `bed0d0dbd5d9c1fe62c6625fd2fd223230b97aec65c0de9007b5d4adba579768`.

Innovent source SHA-256: `2d22b993371d66047b1acbd1e26a3b1a27150e346db8a6e611788301f0c90e2a`.

`/disclosures` separates source content date, publication date, retrieval time, parser outcome and registry name association. Matching is confined to exact normalized intervention names/codes of configured companies; registry indication/phase and comparator/experimental roles remain separate. Absence of an exact match is unresolved, not evidence that a trial or drug does not exist.

## Persistent traversal and analyst workflow

With collection enabled and a worker running, directories are checked every24 hours; configured sponsor queries and official disclosures every6 hours. Directory changes trigger candidate screening and idempotent source mapping expansion. A persistent, leased task schedules up to20 name-eligible candidates per hour, prioritizing those never queried. Changed identity hashes re-enter discovery; timestamp-only directory refresh preserves existing review bindings. Failed/interrupted/cancelled lookups can be revisited after24 hours and zero-result lookups after30 days. The first real automatic batch completed20 lookups and added9 pending proposals. Suggestions do not silently become mappings. There are60 resolution runs overall:23 suggested,32 zero,4 ambiguous,1 blocked;9 proposals remain pending after the earlier14 accepted source reviews.

Search supports market/company/name/phase/status filters. CSV export is a CSRF-protected POST followed by read-only GET replay. A SQLite read transaction fixes the exported input snapshot. CSV and manifest hashes, source links, run IDs, record hashes and formula escaping are retained. Maximum300,000 rows/512MiB. Serving verifies a frozen spool rather than re-opening the checked source path, preventing a verify/serve race.

Source implementation commit: `6286f05be885832ffa8f66b00fb7e9cd5f5e294a`. Later documentation-only commits do not alter the43-source-file image alignment gate.

## Engineering and restore evidence

- Complete macOS suite: **362 passed**, including Seatbelt sandbox execution, raw-write/network/secret denial, interruption and restore. Tests ran in the project's Python3.11 environment. An initial base-interpreter bootstrap failed to expose sandbox dependencies; a second bootstrap had the wrong prefix. Correct venv executable/prefix restored the intended test environment. A standard-library collector subprocess now skips unrelated editable site hooks, avoiding dependence on a removable disk.
- Local Docker image: `emeris-biotech:qa-market-20261017`, ID `sha256:71952620f3855559fb7fa4766713a93108e40bc55f5043f9fb79816bdde52dd5`.
- **22 isolated container checks passed**; all43 Python source files match the working tree by SHA-256. Fresh synthetic-only volumes, UID/GID10001 writes, SIGTERM/restart persistence, disabled scheduler producing no real captures, source/CSRF guards and filtered CSV/manifest checks passed. QA containers/network/volumes were removed; unrelated containers were untouched.
- Real registry/disclosure source check: **301 source files verified**, no size/hash errors; SQLite integrity `ok`, zero lineage references to missing or unsuccessful published runs.
- SQLite backup restored to a separate local file: integrity `ok`,93 securities and274 disclosure observations preserved. Market restore requires the state backup plus the entire `artifacts/market` tree; the original research `vbt restore` does not migrate this database.

Raw local evidence remains outside public Git:

- `artifacts/market/acceptance/2026-10-17/acceptance.json` in the local runtime.
- `/tmp/emeris-market-container-qa-412a377156/acceptance.json` and `/tmp/emeris-market-container-qa.py`.
- Local backup `market-with-disclosures.sqlite3` under the application backup directory.

## Actual browser acceptance

The live workbench was checked in Chrome and the in-app browser. Coverage showed41/24/28 mapped securities. Disclosure cards showed the actual274/403/parse-blocked outcomes. A filtered Camrelizumab view displayed3 disclosure observations, each linked by exact intervention name to11 A-share security–trial observations; expanding the association exposed NCT source links, conditions, registry phases and arm roles. These remain name links, not indication-matched or ownership-verified assets.

The automated review history explicitly displayed “自动双源名称核对，非人工签署”. A real analyst workflow selected Hengrui A:600276, submitted the export POST and downloaded CSV+manifest: **570 rows,287,075 bytes**, SHA-256 matched; every row matched the selected company and retained run/hash/source provenance. Reloading the export page is read-only. Local export ID: `49490f4e0a3c43f992c2504ab77f9cb3`.

Chrome's extension debugger disconnected during initial inspection; bounded recovery stopped and native/IAB inspection completed the remaining checks. A real five-column width problem was found and corrected; narrow windows retain horizontal browsing, confirmed by expanding the registry column. Source cards, table and export screenshots are retained at `artifacts/market/ui_acceptance/2026-10-17/`. No human/scientific decisions were submitted during UI QA.

## Remaining product gates

Continue identity/entity expansion, unclassified-company investigation, Beijing exchange intake, official pipeline/annual-report coverage, China registry integration, drug aliases and rights reconciliation. Define and audit company/source-level denominators before promising comprehensive coverage or an update SLA. A remote host/domain/customer authentication configuration and an end-to-end HTTPS gateway/restore acceptance are still required for cloud customer access.
