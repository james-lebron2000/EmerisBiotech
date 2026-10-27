# Product review — 2026-10-27

## Review scope and evidence

This is a role-based engineering/product review by the coding assistant, not
independent scientific review, a real MNC interview, procurement approval or
clinical validation. It covers the current `patient-molecule-response` worktree,
PR #39, current GitHub issue/CI state, a fresh anonymous browser inspection of
the deployed patient site and the documented boundaries of the other modules.
Production Predome data, authentication and server deployment were not audited
in this pass. Open issue descriptions are evidence of recorded work/gates, not
proof that their implementations or external acceptance are complete.

The previous goal turn delivered concrete cohort code and test evidence; it was
progress. This review continues that work rather than restating a plan.

## Product manager review

| Finding | Evidence / effect | Action and acceptance |
| --- | --- | --- |
| New workflows are absent from the live entry | Anonymous Chrome found one manual resistance selector, zero cohort links and zero molecule links at the public root | [#47](https://github.com/james-lebron2000/EmerisBiotech/issues/47): current-head CI, immutable release provenance and anonymous public acceptance required |
| Capability boundaries and customer entry are unclear | Separate workbench, Predome and synthetic patient modules have different state, evidence and deployment requirements | [#45](https://github.com/james-lebron2000/EmerisBiotech/issues/45): English capability entry, supported input/output, data residency and explicit remaining gates |
| English migration is incomplete | Legacy workbench shell uses zh-CN; active navigation/errors and historical docs retain Chinese | [#46](https://github.com/james-lebron2000/EmerisBiotech/issues/46): full route/export inventory and rendered English migration; README translation is insufficient |
| Customer value is unproven | No actual comparative task/user observations were supplied | Reuse [#34](https://github.com/james-lebron2000/EmerisBiotech/issues/34): prespecified equal-information/budget trial, real independent users, separate R&D and BD scoring |

The preview's candidate task is **inspect paired patient–molecule evidence and
handoff reproducible assay trajectories**. Its current output is not a clinical
treatment-selection result. The product should not add unrelated simulation
options until the evidence path, missingness, usability and reproducibility of
this task are dependable.

## QA review

| Finding | Reproduction / impact | Action and acceptance |
| --- | --- | --- |
| Cohort updates destroy keyboard/evidence state | Focus P001, press Enter: activeElement becomes BODY. Open evidence then change day: open detail count 1 → 0 | [#42](https://github.com/james-lebron2000/EmerisBiotech/issues/42): retain card focus and open evidence across playback, scrubbing and population changes; reset when patient/cutoff actually changes |
| Declared synthetic-only boundary is incomplete | Changing patient.source_kind to real still returned estimable | [#43](https://github.com/james-lebron2000/EmerisBiotech/issues/43): reject origin/identity/time/source/count violations, preserve missing-pair abstention and exact existing hashes |
| Export checks are not independently reproducible | Browser validates template bytes but recipient has no supported patient-export verifier; duplicate result and view metadata can be changed | [#44](https://github.com/james-lebron2000/EmerisBiotech/issues/44): offline canonical/approved-template/selection verification, nonzero failure, explicit unsigned-metadata boundary |
| Existing green CI is scoped to an older head | 40447174771 succeeded for d2a2bb0, before the audit fixes | [#47](https://github.com/james-lebron2000/EmerisBiotech/issues/47): run current-head CI; never reuse an earlier run as release proof |

The revised browser checks include keyboard activation, open evidence during
playback, real downloaded JSON verification, deliberate tampering rejection and
CSV equality against independently reproduced Python output. Existing checks
still cover paired denominators, missing values, cutoff exclusion, synchronized
updates, responsive layouts and corrupt-template rejection. Their scope remains
synthetic engineering behavior; real devices, large real cohorts and clinical
outcomes are not implied.

## Simulated MNC customer review

| Buyer/reviewer question | Current answer | Remaining gate |
| --- | --- | --- |
| What decision can my team make with this? | Inspect a synthetic assay-review workflow and its traceability | Actual task/value evaluation in #34; no clinical benefit claim |
| Which data drive each result? | Explicit patient/sample/molecule mapping, matched drug/vehicle counts, availability cutoff and source IDs | Real assay QC, biological replication and approved input adapter remain unstarted |
| Can an analyst inspect outputs outside the page? | #44 adds patient/time/molecule CSV and offline JSON verification | Verification is integrity/reproduction only; browser metadata is unsigned |
| Are model uncertainty and clinical outcomes supported? | Technical ranges and descriptive patient IQR only; response/PFS/OS/toxicity unavailable | Scientific/clinical validation remains separate; do not convert IQR to a confidence interval |
| Can we upload confidential patient data or collaborate? | No uploads, shared accounts or enterprise patient workspace in this public preview | Reuse deployment/access-control work in #32; patient-specific enterprise scope still needs agreement |
| Is the supplier's scientific claim accepted? | No such acceptance follows from code, browser tests or hashes | Actual authorized reviewers in #11 / #28 and a separately specified patient-model review |
| Is the whole product English and production-ready? | The new preview is English; legacy localization and target-environment acceptance are incomplete | #46 / #47 and module-specific deployment checks |

No certification, regulatory approval, procurement eligibility, SLA, intellectual
property clearance, real human reviewer or customer endorsement is asserted.

## Issue-by-issue implementation receipts

- #42: `b605308` retains patient-card focus and updates the evidence DOM only when
  patient/input changes. A real-browser regression checks focus and open evidence
  during playback and summary switching.
- #43: `97ef428` validates synthetic patient/assay origin, required mapping and
  version metadata, unique ordered times and source IDs, measurement availability,
  and paired finite positive count arrays. Focused paired/cohort tests passed;
  all single-patient and cohort bundle hashes before/after were identical.
- #44: `b196517` adds `python -m vbt.patient_exports`, exact supported-template
  reproduction and CSV generation. The cohort browser downloads real JSON and
  CSV, runs the independent verifier, compares CSV bytes and rejects changed JSON.
- #45: current work adds the English repository guide and public capability entry.
  The current paired/cohort workflows become the primary entry; the older
  mathematical scenarios move to `scenario-archive/` and retain their original
  same-origin browser storage. This does not complete #46.

Implementation on a branch, merge, deployment and scientific/customer acceptance
are separate states. Issues remain open until their stated gates are met; a test
receipt must not silently close an external dependency. Current CI/release status
is recorded on PR #39 and #47 rather than hard-coded as evergreen documentation.

## Ordered remaining work

1. Complete current-head regression, browser checks and release provenance for the
   revised entry, archives, verifier and cohort; resolve #47 only after public QA.
2. Inventory and migrate active legacy English flows under #46 without rewriting
   immutable original evidence or silently merging databases/users.
3. Prepare executable real-assay input/QC contracts and a frozen task evaluation;
   obtain actual approved data and independent users before reporting observations.
4. Continue existing scientific-review, deployment and evidence gates (#11, #28,
   #32, #34). Those issues already distinguish deliverable engineering artifacts
   from real reviewers, source access, deployment and customer results.

This review is one completed engineering audit pass with active follow-up work.
It is not a claim that the full product optimization goal or MNC adoption is done.
