# Issuer discovery and coverage API

This module records observable discovery and collection progress. It does not estimate an independently verified full-market drug-company denominator, clinical success, drug ownership, or complete drug pipelines. Tests use synthetic fixtures only.

## Additive storage

`vbt.market_coverage.init_schema(market)` is idempotent and creates:

- `issuer_candidates`: one canonical `market:ticker` classification observation, with independent human review state.
- `issuer_candidate_observations`: immutable content-hashed historical classification evidence.
- `issuer_candidate_reviews`: append-only attributed human classification decisions, bound to the exact reviewed content hash.

Existing `Market.companies` is still the authority for configured sponsor collection targets. Accepting a classification does not create or modify a sponsor mapping. Local reviewer names are declared identities, not authenticated independent scientific approval.

## Ingest

`refresh_candidates(market, candidates)` requires `market`, `ticker`, `name`, `reason`, `source`, and nonempty `evidence`. `classification` defaults to `drug_developer_candidate`; `observed` defaults to current UTC. Timestamps must include a timezone. `classification_reason`/`sources`/`observed_at` are accepted aliases for the relevant fields. Extra fields such as `industry`, `aliases`, `method`, `confidence`, and `issuer_group` survive in the JSON payload.

Candidates are validated as a complete batch before writing. Duplicate canonical securities in one batch are rejected. A caller cannot set an approved review state. Refresh preserves manual acceptance/rejection; if evidence changes after the latest review, `review_needs_refresh=true`. Historical observations arriving late are archived without replacing a newer snapshot. An issuer missing from a later batch is preserved rather than silently deleted. Return values are `inserted`, `updated`, `unchanged`, and `observed` counts.

Classification labels used by the discovery module:

| Label | Meaning in the summary |
|---|---|
| `drug_developer` | Source-supported drug-development classification, still independently reviewable |
| `candidate` / `drug_developer_candidate` | Name/industry classification hint requiring review |
| `uncertain` | Ambiguous business scope requiring review |
| `excluded` | Excluded by the current screening rules |
| `unclassified` | Current screen provides no classification |

`candidate_count` counts the first three groups. `classified_listing_count` counts all stored screen observations, including exclusions and unclassified securities; it does not claim that all businesses have been resolved.

## Read

`candidate_rows(market, q='', market_filter='', status='', offset=0, limit=50, classification='')` returns `(rows, total)`. Limits are 1–200. The query first filters and pages in SQLite, then deserializes only the selected candidate evidence. It also includes existing configured companies without a classification candidate.

Important row fields are `id`, `market`, `ticker`, `name`, `classification`, `reason`, `evidence`, `payload`, `review_status`, `last_review`, `review_needs_refresh`, `company_id`, `aliases`, `mapping_provenance`, `collection_status`, `latest_run`, `latest_result_count`, `latest_error`, `last_success_at`, `trial_count`, `stale`, `listing_sources`, and `listing_exchanges`. Raw strings must be HTML escaped by a caller.

`classification='active'` selects drug-development candidates and existing mapped companies. The product view defaults to this filter; `all`, `excluded`, `unclassified`, and individual classifier labels are available explicitly. API calls without a classification filter remain unfiltered for compatibility.

`status` filters include `pending`, `accepted`, `rejected`, `needs_review`, `mapped`, `unmapped`, `uncollected`, `running`, `failed`, `partial`, `interrupted`, `cancelled`, `succeeded_with_trials`, `succeeded_zero`, and `stale`.

`coverage_summary(market, stale_hours=24, as_of=None)` returns a `markets` dictionary keyed by `US`, `A`, and `HK`, plus cross-market workflow totals. Securities deduplicate on market and normalized ticker, so overlapping US directory sources do not inflate the count. Unsupported ticker identities (for example preferred-share `$` symbols) are retained in raw directory counts and reported separately as `unsupported_identity_count`; they are not stripped into a potentially wrong issuer identity. `raw_unique_security_count` and `securities_count` include these quarantined keys, while `normalized_security_count` counts only supported canonical keys. Securities across markets remain distinct listing keys; they are not a unique global corporate-entity count.

Each market exposes directory counts, classification counts, configured mappings, companies with retained trial observations, latest zero-result queries, uncollected/failed/partial/interrupted/running states, stale observations, unmapped candidates, source snapshots and source gaps. `eligible_issuer_denominator` and `full_market_coverage_rate` remain null. `full_pipeline_verified_count` remains zero because this release does not provide an independent full-pipeline reconciliation acceptance gate.

Latest-query zero results and retained historical trials can coexist. A new failure likewise retains earlier trials. `stale` means the latest successful collection was more than the configured 24 hours ago; a never-successful collection is handled by its own uncollected or failure status. Collector queue states are a separate durable workflow (`market_jobs`), not substituted for observed clinical data states.

## Human review

`review_candidate(market, candidate_id, decision, reviewer, reason)` accepts `accepted` or `rejected`, a declared reviewer of 1–100 characters and a 10–5000 character reason. It appends a classification decision, not a scientific sign-off.

## Product view

`vbt.market_progress_view.render(app, q)` renders the progress page for `/market/progress`. Query fields are `q`, `market`, `status`, `classification`, `offset`. All forms carry `app.token`; the owning HTTP handler must retain same-origin and CSRF checks.

| POST path | Additional form fields |
|---|---|
| `/market/discover` | none |
| `/market/batch` | none |
| `/market/review` | `candidate_id`, `decision`, `reviewer`, `reason` |
| `/market/cancel` | `jid` |
