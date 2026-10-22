# Predome 1.2.2 — public clinical outcomes and source review

The public clinical page remains a three-module comparison: trial facts, actual same-evidence model records, and consensus versus published outcomes. The stored forecast, model execution, probability and original ledger events are not rewritten. Uncalibrated public probabilities and confidence remain N/A. Model assignment failures and abstentions stay visible.

## Outcome registration

`Mixed`, `Inconclusive` and `not_adjudicable` are now native outcome labels. They never create binary match badges or Brier scores. An announced source is displayed as **Awaiting adjudication**, with primary endpoint **Not Yet Determined**, until matching reviews resolve the exact registered hypothesis.

Each typed review binds the target ID, NCT, endpoint, population, comparator, success criterion and analysis timepoint. Reviewer IDs are stable and case-insensitive. Review kind must explicitly be `ai` or `human`; legacy unspecified reviews never become human reviews by inference. Two distinct typed reviews agreeing on the same source-bound outcome can produce a research adjudication. Unresolved typed disagreement prevents this. A reviewer can correct only their own latest vote through its hash; original events remain immutable. A new review correcting an adjudicated result suspends scoring until the replacement is resolved.

**Reviewer identities and qualifications are operator-declared, not independently authenticated.** Two AI reviews are labelled AI-only; declared human participation does not establish clinical credentials, independence, or clinical predictive validity. This release creates no real human reviews and registers no real trial results.

## Operator workflow (internal only)

Run from the existing app's `scripts/` directory with the service Python. Preparation is query-only; it cannot invent reviewer identities or fill missing results.

```sh
python arena_result_review.py --data-dir /var/lib/emeris-live prepare --target EXACT_TARGET_ID --output /private/path/review.json
```

Fill the worksheet from the actual original public source. Supply the existing original source file and a provenance manifest using schema `predome-outcome-acquisition-v1`. Its `assets` entries require the exact `source_url`, source `sha256`, integer `bytes` and ISO `retrieved_at`. Source bytes, URL and manifest must match; publication must precede acquisition and neither can be in the future. This is mechanical provenance checking, not proof that a source claim is scientifically correct.

```sh
python arena_result_review.py --data-dir /var/lib/emeris-live submit --review-file /private/path/review.json --source-file /private/path/original-source.html --acquisition-manifest /private/path/source-manifest.json
```

These are operator commands, not publicly available API writes. Do not run submit before source and exact endpoint review. A stale result version or correction pointer is rejected. Typed AI reviewers cannot relabel themselves as human within a correction sequence. Individual reviewer notes/IDs remain internal; the public projection contains only a short review provenance notice.

## Public verification

Public HTML uses only the sanitized public API. Historical replay records never become prospective hits. Actual result links and announcement dates are retained. A nonbinary or unresolved result remains unscored even if an upstream payload contains a premature match flag.

The code-only 1.2.2 deployment preserves the original Arena chain, sealed Workbench rows, model configuration, access controls and Nginx routing. It creates SQLite backups, verifies source manifests, atomically changes the release link and checks the active service. Rollback changes code only and never restores a database over concurrent user writes.

Synthetic test fixtures check behavior, not actual trial outcomes, model accuracy or clinical performance. No model calls, new forecasts, clinical adjudications or scientific validation are included in this release.
