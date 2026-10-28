# 120-patient treatment atlas

The third section of public `cohort-lab/` shows the same 120 authored synthetic patients under
two sequential pathways. It replaces the default presentation, not the prior assay
engine. The frozen 24-patient paired-exposure model and its verifier remain unchanged;
its public UI now lives at `assay-cohort-archive/`. The local workbench route remains
its separate read-only assay summary.

## Clinical catalogue and eligibility

Scope: initially unresectable metastatic colorectal cancer, MSS/pMMR, RAS mutant,
BRAF V600E wild-type, ECOG 0–1, fit adults; absence of anti-VEGF contraindications is
assumed. This is a deliberate narrow fixture, not a general mCRC treatment algorithm.

| Pathway | First line | Second line | Third line |
| --- | --- | --- | --- |
| A | FOLFOX + bevacizumab | FOLFIRI + ziv-aflibercept | Trifluridine/tipiracil + bevacizumab |
| B | FOLFIRI + bevacizumab | FOLFOX + bevacizumab | Trifluridine/tipiracil + bevacizumab |

Catalogue sources checked 2026-10-27:

- [ASCO mCRC guideline](https://ascopubs.org/doi/10.1200/JCO.22.01690): doublet chemotherapy with the bevacizumab evidence base in initially unresectable MSS/pMMR disease; anti-EGFR is not recommended for RAS-mutant disease.
- [ESMO mCRC guideline, 2026](https://www.annalsofoncology.org/article/S0923-7534(26)00108-0/abstract): doublet/anti-VEGF options and change of chemotherapy backbone after prior treatment. Triplet options also exist and are outside this selected catalogue.
- [NCI Colon Cancer Treatment PDQ](https://www.cancer.gov/types/colorectal/hp/colon-treatment-pdq): FOLFIRI/ziv-aflibercept after prior oxaliplatin-based treatment.
- [FDA approval](https://www.fda.gov/drugs/drug-approvals-and-databases/fda-approves-trifluridine-and-tipiracil-bevacizumab-previously-treated-metastatic-colorectal-cancer): FTD/TPI with bevacizumab after fluoropyrimidine, oxaliplatin, irinotecan and anti-VEGF exposure. The RAS-mutant scope avoids an unfulfilled anti-EGFR prerequisite for RAS-wild-type patients.

These sources support regimen identities and selected sequencing, not any trajectory,
effect size, time to switch, or comparative efficacy displayed by this application.
Local approvals, comorbidities, contraindications and individual treatment choices
require clinical review. Dosing and adverse-event management are outside this model.

## Fictional model

`src/vbt/regimen_cohort.py` is the sole model implementation. Seed 20261028 and version
`synthetic-regimen-cohort-120-v1` define a new reproducible fixture, not uncertainty.
Each patient has authored pre-treatment indices at weeks -8/-4/0, normalized to 100
at baseline. Growth `g = log(index_0 / index_-8) / 8`. Authored backbone coefficients
and an escape coefficient control weekly index evolution:

`index[w+1] = index[w] * exp(g - effect * exp(-escape * time_on_current_line))`.

Both pathways use the exact same patient inputs and coefficients; no pathway gets a
hidden benefit multiplier. Anti-VEGF components are not separately calibrated.
At an 8-week toy review, a rise of at least 30% AND 15 index units above the current
line's nadir advances 1L→2L or 2L→3L. Switching resets the modeled escape clock, an
explicit illustrative assumption. No further line decision is modeled after 3L.
This index-based toy rule is not RECIST. It has no lesion measurements, absolute-mm
criterion, new-lesion assessment, symptom assessment or toxicity assessment.

All 73 weekly outputs (weeks 0–72), line events and descriptive summaries are frozen
in the Python-built canonical bundle. The browser only displays those results and
linearly interpolates indices between weekly outputs. Treatment-line badges and
counts change at discrete modeled review events, while curves move continuously.
Age, ECOG, metastatic sites and molecular scope describe the synthetic fixture;
they are not fitted covariates. No resistance slider or real assay-to-clinical bridge
is present. Clinical response, PFS, OS and toxicity stay null.

## Display and export

The current presentation shows ten fixed cases beside all-120 median/IQR curves.
At 1440×900 and 1366×768 the ten cases and cohort overview fit together. Each individual cell contains the paired A/B sparklines, change from baseline,
treatment-line badges and an absolute A–B index gap. Sparkline axes are shared over
the whole cohort; no per-cell auto scaling or clipping of the burden range is used.
Sorting is applied on request, not each animation frame. Patient details and methods
open in dialogs. Mobile uses a readable scrolling grid rather than hiding patients.
Autoplay, pause/resume, replay, fractional scrubbing, speed and reduced-motion support
are preserved. Full screen uses the browser API where available.

JSON exports include the canonical fixture, checksum and unsigned view metadata.
`uv run python -m vbt.patient_exports export.json --csv new-output.csv` independently
reproduces the installed fixture and rejects altered outputs or invalid view fields.
CSV contains 120×2×73 = 17,520 rows. The older export schemas remain supported.
This integrity check is not scientific review, a signature, identity verification or
clinical validation. No real patients, trial participants or clinical benefit are
claimed.

The company/product/simulation narrative and Chinese edition are documented in [COHORT_PRESENTATION.md](COHORT_PRESENTATION.md).

## Paired pathway contrasts and input-defined strata

Overview schema `regimen-cohort-overview-v2` adds a descriptive paired comparison
without changing `synthetic-regimen-cohort-120-v1`, its canonical outputs or CSV.
For every patient and week, compute A burden index minus B burden index. Negative
means lower in A. Count gaps below -5, above +5 and within ±5 index units. The five-unit
band is a display convention, not clinical equivalence or statistical significance.
The displayed median is the median of patient-level paired gaps, not a subtraction
of two marginal medians. Counts use completed weeks; intermediate median labels use
linear display interpolation.

Before examining simulated outcomes, partition the authored baseline coefficients:
oxaliplatin/irinotecan ≥1.35 (41 patients), ≤1/1.35 (42 patients), or between (37).
These are synthetic input strata, not measured patient sensitivities or validated
biomarker groups. They are not selected by observed response. In the frozen week-24
outputs, the opposing strata have median paired gaps -36.279696 and +32.922972 index
units, while the full cohort has +2.819980. The aggregate similarity masks opposing
patient-level behavior; it does not establish equivalent clinical treatment efficacy.

The all-cohort comparison strip stays at n=120. A selected stratum changes the
cohort curves, denominators and first fixed patient IDs displayed. Axes stay shared
across stratum selections. JSON always exports all 120 patients and records the
optional `view.comparison_stratum`; older three-field view exports remain valid.
The comparison concerns complete sequential scenarios, not head-to-head causal
comparisons of individual 1L, 2L or 3L regimens.
