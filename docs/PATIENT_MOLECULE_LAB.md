# Patient × Molecule Lab

Development branch: `patient-molecule-response`. The English successor workflow is
available at `/molecule-lab` in the workbench and `molecule-lab/` in the public
build. It has no manual resistance input. The existing v1 scenario archive and
its immutable records remain reproducible. This branch is not a production release.

## Product decision

Replace an arbitrary low/high resistance choice with a read-only patient–molecule
evidence summary. A scalar cannot honestly be inferred from absent DNA, RNA or
single-cell measurements. Every requested patient–molecule pair therefore has an
explicit state: paired assay estimable, paired assay missing, or input rejected.
Residual viability and resistance are different endpoints. The current estimator
reports the former and leaves resistant-cell fraction unresolved.

## Current executable scope

Three synthetic patients (P001–P003), two named molecules (fluorouracil and
oxaliplatin), and three available-data cutoffs (assay days 3, 5 and 7).
P001 and P002 deliberately have different response patterns. P003 has no
oxaliplatin assay and must return **not estimable**. Molecule names resolve to
[NCI fluorouracil](https://www.cancer.gov/publications/dictionaries/cancer-drug/def/fluorouracil)
and [NCI oxaliplatin](https://www.cancer.gov/publications/dictionaries/cancer-drug/def/oxaliplatin).
These links support molecule identity only. They are not sources for any simulated
measurement or a claim about a treatment's clinical efficacy.

No real patient measurements have been imported. No clinical prediction model is
trained. No real regimen, administration schedule or human dose is represented.
Exposure X1 is a synthetic fixed assay condition; it cannot be translated into
human exposure or used to compare clinical regimens.

## Data contract and computation

Each assay records patient ID, sample ID, molecule ID, exposure, source kind,
assay version, measurement day, availability day and three matched technical
replicate counts for drug and vehicle. Patient/sample/molecule mismatches and
nonpositive or nonfinite counts fail closed. At least baseline plus two
post-exposure timepoints are required. Missing assay pairs abstain without borrowing
another patient's data or using a default fraction.

For matched replicate j at time t:

`viability_j(t) = 100 × (drug_j(t) / vehicle_j(t)) / (drug_j(0) / vehicle_j(0))`

The displayed point is the replicate mean; min–max is technical measurement
spread, not independent-patient uncertainty or a predictive confidence interval.
Values between measurements use linear interpolation. Beyond the cutoff, at most
three assay days are extrapolated with the slope between the last two log means:

`k = log(mean_last / mean_previous) / (day_last - day_previous)`

`forecast(t) = mean_last × exp(k × (t - day_last))`

This is an explicitly unvalidated short-term assay forecast, not a mechanistic
resistance model. An assay day is never relabeled as a clinical week. No synthetic
survival curve is shown. The output fields for objective response, tumor burden,
PFS, OS and toxicity remain null. Clinical and omics context are visible but not
fitted as predictive inputs. There is no automated drug ranking or recommendation.

## Presentation and reproducibility

1. Select a patient and a molecule; the state panel recalculates automatically.
2. Read residual viability, unresolved resistance status and missing evidence.
3. Inspect observed points, solid interpolated curve and dashed three-day tail.
4. Open raw matched counts and source identifiers.
5. Switch the availability cutoff; future rows are removed before hashing/export.
6. Save a new immutable browser record, then export the exact canonical template,
   its SHA-256 and the selected patient/molecule/cutoff. Prior browser records are
   preserved; visitor data stays in that browser. Export metadata is not signed.

The authoritative engine is Python. The public build precomputes 18 templates;
JavaScript verifies their checksums and renders them without implementing another
response model. Browser records use a separate v1 molecule-lab storage key and
cannot overwrite earlier scenario records. SHA-256 detects accidental template
corruption; it is not authentication against a malicious server or browser owner.
The workbench provides a script-free read-only view of the same engine.

## Development and validation gates

| Stage | Deliverable | Acceptance gate | Status |
| --- | --- | --- | --- |
| 1 | Paired patient–molecule workflow | Distinct paired results; missing pairs abstain; no resistance selector; raw evidence and cutoff reproducible | Synthetic prototype in this branch |
| 2 | Real assay adapter | Approved de-identified mapping, assay/exposure metadata, QC, batch controls, biological replication and locked unit tests | Not started |
| 3 | Drug-specific cellular state model | Time-resolved cell-state evidence, identifiable parameters, held-out assay trajectories, calibrated predictive intervals and baseline comparison | Not started |
| 4 | Clinical translation | Human exposure/PK–PD bridge, clinical endpoint definitions, independent patient-level outcomes and leakage-free external validation | Not started |
| 5 | Clinical deployment | Approved scope, prospective evaluation, qualified oversight and monitored performance | Not started |

Macro information (organ function, disease sites, prior therapies and imaging) and
micro information (DNA, RNA, protein, spatial/single-cell state and functional
assays) should eventually enter a versioned feature manifest. Every field must
state whether it is context, eligibility, a measured model input or unavailable.
Future models must specify their training cohort, applicable tumor/drug/exposure,
endpoint, data cutoff, calibration and abstention rules before showing patient
clinical probabilities. Genotype alone must not be assigned an invented universal
resistance percentage. Combination treatments need their own paired measurements
and model; never add single-molecule effects to manufacture combination efficacy.

## Run locally

Build with `uv run python scripts/build_patient_site.py --out /tmp/patient-molecule-site`.
Serve that directory on loopback and open `/molecule-lab/`. For the integrated
workbench use the existing patient lab server with a fresh local state directory,
then open `/molecule-lab`. The deployed production link is unchanged until this
branch is reviewed and an explicit release is made.

## Virtual Trial Cohort extension

The new `cohort-lab/` public page (and read-only `/cohort-lab` workbench route)
displays **24 synthetic patients under both molecule exposures simultaneously**.
This is a paired virtual assay cohort, not a randomized clinical trial. Treatment
arms, RECIST outcomes, survival and drug combinations are not manufactured.

The cohort includes the original three cases and 21 additional synthetic cases.
Their paired count fixtures are authored deterministically using recipe
`synthetic-paired-cohort-24-v1`, seed `20261027`. The seed is reproducibility metadata,
not a source of predictive uncertainty. Added cases include decline, persistent
viability and rebound patterns. Patient P003 lacks oxaliplatin measurements and
P014 lacks fluorouracil measurements. The original single-patient engine and the
cohort use the same matched-count normalization and short-tail estimator.

The English interface provides:

- Two side-by-side molecule panels with shared axes, all individual trajectories,
  a median curve, descriptive patient interquartile band and synchronized cursor.
- Play, pause, restart and a shared assay-day slider. Current group summaries and
  all 24 patient cards update together. Measured/interpolated and extrapolated
  timepoints are explicitly distinguished.
- Patient highlighting across both panels, with raw paired counts and source IDs
  in the evidence panel. All patient cards remain visible even when the patient
  is excluded from a summary because a paired measurement is missing.
- A default complete-pair summary using the **same 22 patients for each molecule**.
  An optional available-case summary uses **23 patients per molecule** with
  different missing patients; a warning prohibits causal interpretation.
- Three data cutoffs (days 3, 5, 7). Future observations are removed before result
  generation, input hashing and export. Forecast horizon is cutoff + three days.
- JSON export of the full cohort, raw evidence, model and cohort versions,
  canonical-template checksum, selected population, assay day and highlighted
  patient. Each download gets a fresh run ID; no existing record is overwritten.

Patients are the independent descriptive unit. Three technical wells never become
three patients. Median and 25th–75th patient percentiles are computed in Python;
JavaScript only renders precomputed values. Percentiles use linear interpolation
at `(n - 1) × p`. The band is neither a confidence interval nor a calibrated
prediction interval. Available-case summaries do not establish a treatment effect.
The complete-pair selection is explicit and can itself be unrepresentative.

The browser verifies all three cohort templates before enabling playback/export.
The page makes no real patient uploads, creates no real trial enrollment and does
not change clinical or experimental treatment assignments.

## Independent export review and CSV

The public entry now describes supported capabilities; the original mathematical
scenario page lives at `scenario-archive/`. Its browser storage key is unchanged.
The paired/cohort routes and earlier immutable Python fixture outputs are preserved.

Use `uv run python -m vbt.patient_exports /absolute/export.json` to verify a
single-patient or cohort export offline. It checks the canonical checksum,
duplicate result, installed approved synthetic fixture and selected view/record
consistency. Unsupported or altered exports fail with a nonzero exit. This does
not authenticate unsigned browser metadata, establish a signature or perform
scientific review. A different future model needs its own versioned verifier;
unknown models must not be silently accepted.

For a cohort, add `--csv /tmp/new-cohort.csv`; existing output files are never
overwritten. The browser also links the same precomputed CSV at each cutoff.
Each row is one patient × molecule × assay day, retaining sample, cutoff, source
kind, model/cohort versions, input fingerprint, paired-summary inclusion,
estimation status and phase. Missing values are empty, never zero. All patients
remain in the CSV regardless of the selected summary population. The 24-patient
cutoff-7 CSV therefore has 528 rows, including 22 missing patient–molecule time rows.
The cohort UI's selected day and population remain in the JSON view metadata.


## Progressive cohort drawing

The initial cohort chart shows baseline markers only. Play advances the shared
assay-day timeline; all individual curves, the highlighted patient, the population
median and the patient IQR band are drawn only through that day. Dashed assay
extrapolations appear progressively after the cutoff. Rewinding removes later
geometry, and changing the available-data cutoff resets the drawing to baseline.
Playback from the final day restarts at day 0. Axes remain fixed to support
comparison. This changes visual disclosure only; model outputs and complete
JSON/CSV exports remain unchanged. The selected timeline day is not a replacement
for the data-availability cutoff.
