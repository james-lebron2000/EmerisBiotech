# MSI-H/dMMR immunotherapy scene

The public `/cohort-lab/` offers two **independent** authored 120-patient cohorts. The original MSS chemotherapy payload, sequencing model and CSV remain unchanged. The new `immune-cohort-v1` uses `I001–I120`, first-line, ICI-naive adults with unresectable/metastatic MSI-H/dMMR colorectal cancer. Clinical suitability is assumed in the fixture, never inferred from absent history.

## Catalogue and eligibility

A: pembrolizumab. B: nivolumab + ipilimumab. FDA approval notifications (2020 and 2025), checked 2026-10-28, constrain regimen names and molecular/disease scope only. The regulatory scope is US-specific. No trial response rates, PFS, OS, or cross-trial efficacy ordering are imported. This is not a head-to-head clinical estimate.

`eligibility()` returns eligible, not_eligible, or insufficient_data. MSS/pMMR is outside this immune catalogue. Missing adult/disease/prior-ICI/suitability information and discordant MSI/MMR results fail closed. There is no immune simulation for an unresolved or ineligible patient. This is a bounded scenario gate, not a comprehensive prescribing engine.

## Authored response model

A fixed seed generates four equally populated **input** patterns: early activation, delayed activation, low effect, and effect escape. These are not measured immune phenotypes or clinical prevalence estimates. Growth is derived from synthetic pretreatment indices. Age, ECOG and metastatic sites are descriptors, not fitted response modifiers.

For each weekly midpoint:

`index[t+1] = index[t] * exp(growth - effect * logistic_activation * escape_persistence)`

Each patient has per-pathway effect strength, activation delay and escape onset, and a shared escape rate. Effects are drawn symmetrically; no global treatment winner is imposed. All coefficients are exported and visible in patient detail. No uncertainty calibration is available.

Trajectories assume continued exposure over 72 weeks. There is no dosing schedule, pseudoprogression diagnosis, immune toxicity, discontinuation, subsequent therapy, mortality, RECIST, PFS, OS or clinical response probability. Descriptive bands use current index: below 95, near 95–105, above 105; rebound takes precedence when index is at least 30% and 15 units above its running nadir. These are display categories, never clinical progression or treatment-switch rules.

## Display and exports

The default atlas shows all 120 patients, two curves each, with fixed positions, shared `log(1 + index)` vertical scaling, fractional-time drawing, pause and replay. This nonlinear scale is stated on the page, remains fixed across input filters and is also used in immune detail charts. The shaded cohort band is the 25th–75th patient percentile, **not** a confidence interval. Five index units is a descriptive paired-gap display band.

Each scene has its own 120 patients; comparing scene medians is not a causal therapy comparison. The original 20 larger chemotherapy charts and strata remain available on demand. A patient's two simulated potential trajectories are counterfactual scenarios, not two observed clinical courses.

Immune JSON exports use a canonical SHA-256 envelope and include unsigned view metadata (week, input pattern, highlighted patient). The offline verifier reproduces the installed fixture, rejects rehashed modified trajectories, and can regenerate the CSV. The CSV always contains 120 × 2 × 73 = 17,520 records, even when filtering the view. Integrity is not clinical validation or authentication of unsigned metadata.

## Verification

`tests/test_immunotherapy_cohort.py`: input dependence, symmetric equal-input response, heterogeneous outputs, independent one-step reproduction, eligibility rejection, reproducible exports and tampering rejection.

`tests/ui_immunotherapy_public.cjs`: all 120 visible at 1440×900 and 1366×768, scene switching, continuous stable-DOM drawing, pause/replay, independent patient/gap checks, input filters, both language routes, responsive widths, reduced motion, CSV reproduction and corrupted bundle rejection. Both public browser suites run in CI.
