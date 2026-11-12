# Versioned patent experiments

`patent_data/versions/assays-20261027-v2` is a separately selected evidence directory,
built by the existing `build_catalog()` implementation from copies of the two
original PDFs, all 180 archived OCR pages, source metadata and new content-bound
AI source receipts. It contains four selected experiments, two per drug.
The default catalogue and its original one-card-per-drug inputs remain intact.

The new experiments add a distinct cell-assay condition and observed human
exposure parameters. They do not add independent phase3 projects or establish
exposure-response, efficacy or improved prediction accuracy. See the
[version README](../patent_data/versions/assays-20261027-v2/README.md) for exact
values, missing fields, original-page locations and review limits.

## Source and time boundaries

Each catalogue, selected scientific card and receipt has its own integrity digest.
Changing a number, unit, denominator or assay condition without a new matching
receipt fails existing eligibility checks, even if the outside catalogue digest
is recalculated. Publication dates, original identity-source availability and the
new receipt time remain separate. A cutoff before completion of the new review
does not admit this version. Prior frozen forecasts are never regenerated.

Two same-base AI contexts actually inspected the added experiments. Earlier
identity and existing-card checks are named as inherited checks through their
old receipt; they are not presented as newly repeated scientific review.
Human review remains zero, and independent human scientific acceptance is not
conferred by a matching hash or different AI role name.

## Read and prepare

Existing `read_catalog(..., data_dir=...)`, `get_patent(..., data_dir=...)`,
`freeze_evidence_pack(..., data_dir=...)` and the paired-proposal CLI's
`--patent-root` perform explicit version selection. No new forecast algorithm or
automatic directory selection is introduced. A new packet must retain its own
cutoff, catalogue/packet digest, run/cohort identity and original inputs.

The internal patent browser renders each card independently. Receptor EC50 and
SEM stay in the existing cAMP table. An explicit `observed_human_pk` result uses a
parameter/mean/original-unit table with the source's contextual and analyte-label
limitations. Unmarked or incomplete results retain their ordinary representation;
no dose, n, probability or missing value is inferred by the renderer.

## Offline original installation

The original installer accepts the default `patent_data` directory or one explicit
`patent_data/versions/<version-id>` directory under a new release:

```sh
python scripts/install_patent_review_bundle.py \
  --release-root /absolute/new-release \
  --patent-directory patent_data/versions/assays-20261027-v2 \
  --archive /absolute/assays-20261027-v2-originals.tar.gz \
  --sha256 EXPLICIT_ARCHIVE_SHA256
```

The catalogue supplies the exact required original set. The installer preserves
its normal digest, regular-file, bounded-size, no-link and no-overwrite rules.
It does not create a missing version metadata directory or fall back to the
default. Its returned manifest prefixes identify the chosen version.

The cumulative 1.2.9 deployer transports the version metadata and requires its
additional pinned original archive whenever that catalogue has source references.
`--patent-evidence-archive` / `--patent-evidence-sha256` still identify default
catalogue originals; `--assay-evidence-archive` / `--assay-evidence-sha256` identify
this version's originals. They are not interchangeable. Both file sets enter the
release manifest; activation does not change the configured selected directory,
replace a database or authorize a new forecast. Rollback remains code-only.

## Real HTTP layout correction

The first authenticated loopback check found that the real app's `style-src self`
policy rejected inline table styles. The earlier intercepted fixture had omitted
that policy, allowing a misleading narrow-screen pass. PK and cAMP result styles
now use classes in the shipped `arena.css`; the policy is unchanged. Browser
fixtures read the actual policy from `main.py` and reject unexpected console/CSP
errors. The stylesheet is included in the fixed-base deployment allowlist.

A subsequent normal-session HTTP check displays the two new experiments without
inline styles, CSP errors or table/cell horizontal scrolling at desktop and 375px.
The test account is explicitly synthetic local QA in a fresh runtime copy; it has
no scientific review role. The original Arena ledger and all144 public model
records remain unchanged. These captures are local implementation acceptance,
not production or real-user adoption.

## Acceptance scope

The local version build compared 191 existing default scientific files before and
after; all bytes were unchanged. Existing gate implementations freeze two cards
per drug only after the new review time. Metadata-only regression exercises the
real gates without requiring ignored raw files in CI; original-file acquisition,
hash inspection and browser/HTTP checks are recorded separately.

This version has not been used for a new actual model call or forecast. Its
availability does not complete the 12-project patent comparison, scientific
review, prospective outcome validation, live patent-API authorization or production
deployment.

Actual authenticated loopback captures: [desktop PK](images/patent-assays-20261027/dato-observed-pk-desktop.png),
[mobile PK](images/patent-assays-20261027/dato-observed-pk-mobile.png),
[mobile casein assay](images/patent-assays-20261027/retatrutide-casein-mobile.png),
and [provenance](images/patent-assays-20261027/provenance.json).
These use actual API data and normal local QA authentication, with no fixture
response substitution. Private browser session state is not part of these files.
