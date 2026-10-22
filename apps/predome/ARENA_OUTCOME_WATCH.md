# Formal forecast source watch and private review inbox

The daily intake now prioritizes the current active `registered_12_project_season` cohort from the verified Arena event chain. It then retains the legacy upcoming trials and public locked prediction snapshots. The existing 30-trial bound remains; job reports now list requested and deferred NCTs explicitly. `all_requested_ids_scheduled` describes assignment only; separate `registry_checked_ncts`, `literature_checked_ncts` and failures show actual source access. A scheduled ID is never reported as a successful retrieval solely because it was assigned. Invalid identities, including those beyond the limit, fail before network access. Withdrawn registrations and workflow cohorts cannot replace the current formal season.

The existing downloader collects ClinicalTrials.gov registry updates and bounded Europe PMC discovery records. Official-source collection remains confined to its configured domains and trial/source pairs. Its report explicitly lists trials without configured official coverage. Registry availability, text matching, completion dates and literature discovery are not an exact primary-endpoint result or clinical efficacy label.

Version 1.2.5 also derives official watch seeds from the current verified formal registrations. All twelve current targets have an approved registered readout-source URL; this closes the former configuration gap of ten targets. The collector reads the existing Arena chain without initializing or altering it, prioritizes the requested formal NCT order, and retains compatible legacy sources. A changed identity never inherits a conflicting legacy drug's sources. Host approvals are exact TLS destinations, including named sponsor document hosts and SEC filings; arbitrary subdomains, credentials, other ports and off-list redirects remain rejected. Publication guidance remains guidance, including VITALIZE's program-level qualification.

The 16-document bound is unchanged. `scheduled_document_urls` and `deferred_document_urls` expose the complete discovered queue. `checked_document_urls` records successful fetch and parse only; `trials_with_retrieved_configured_source` requires at least one configured document to have actually been retrieved and parsed. Its complement exposes missing acquisition. Neither field claims every sponsor release was covered or that an exact endpoint result was found. First-level news listings for Roche, Pfizer, BMS, Biogen and Sanofi supplement the earlier listings. An index can omit articles, be delayed or deny access; failures are reported without bypassing access controls or assigning efficacy labels. See `docs/OPEN_FORECAST_RESULT_SOURCE_AUDIT_20261023.md` for excluded related-trial announcements.

## Read acquired sources without new downloads

```sh
python scripts/arena_outcome_inbox.py --data-dir /var/lib/emeris-live list --output /private/path/inbox.json
python scripts/arena_outcome_inbox.py --data-dir /var/lib/emeris-live export --target EXACT_TARGET_ID --candidate EXISTING_CANDIDATE_ID --output-dir /private/path/new-review-kit
```

The inbox uses query-only SQLite connections and the existing runtime key to decrypt only selected public intake records. It does not construct a Store, create schemas, inspect user datasets or sessions, make network/model calls, or write a forecast/result. It checks candidate identity, the exact source URL, original bytes, SHA-256 and acquisition time. Registry metadata is rederived from the original captured response. Per-source, selected-corpus and record-count limits bound memory. `list --target EXACT_TARGET_ID` partitions inspection by one formal hypothesis; export reads only the selected target’s sources. Missing matching sources mean acquisition coverage is missing, not that no clinical result exists.

Export produces an original `source.bin`, a compatible acquisition manifest, an editable exact-target review worksheet and a file-hash receipt in a new private directory. Existing directories are never overwritten. Labels, first-result publication time, reviewer IDs/types and endpoint correspondence stay null. A literature search response cannot be exported as an official outcome source. A guidance release or registry update can be inspected but cannot establish target success without the review process in `ARENA_RESULT_REVIEW.md`.

The worksheet's source fields bind the actual acquired bytes; the source's retrieval date is never substituted for first public result time. Filling a worksheet does not submit it. Operator submission still requires explicit AI/human type, actual publication timing, exact target correspondence and independently recorded agreeing reviews. AI-only agreement is never described as authenticated human scientific review.

## Deployment and scope

This integration retains the original daily timer, service limits and unrelated workloads. It changes application code only, preserves the Arena chain and sealed clinical records, and does not import a replacement database. The public three-module page and existing frozen predictions remain unchanged. New intake records are operational review candidates, not new forecast evidence retroactively inserted before a historical cutoff.

Tests use isolated synthetic responses. Real acceptance must inspect the actual current cohort, scheduled selection, acquired-source coverage, original source hashes and a private exported review kit. No isolated test or completed acquisition proves clinical predictive validity, official full-news coverage, scientific review or trial success.

## Bounded downloader check

```sh
python -m emeris_live.intake --data-dir /var/lib/emeris-live --arena-only --skip-discovery --skip-official
```

This explicitly runs the existing downloader for the current formal NCTs only, writes acquired candidates to the intake store and makes no model calls or outcome adjudications. It does not change the scheduled service's normal profile. Run under the existing service user/resource limits, only when a competing intake job is not active. An empty or absent formal cohort fails before creating a new runtime. Download/access failures remain failures, never negative clinical outcomes.
