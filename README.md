# EmerisBiotech

Research software for traceable evidence review, clinical-asset workflows and
synthetic patient–molecule exploration. **Engineering execution is not clinical
validation, scientific approval, customer acceptance or an investment advantage.**

## Product modules

| Module | Entry | What is available | Boundary |
| --- | --- | --- | --- |
| Patient research preview | Static site build below | English capability overview, three paired-assay cases and a 24-patient virtual cohort | Synthetic fixtures; no real assay integration or clinical efficacy model |
| Local research workbench | `vbt workbench` | Evidence cards, market/registry lookup, versioned snapshots and review history | Single-user local workflow; existing UI localization is incomplete |
| Predome | [apps/predome](apps/predome/README.md) | Readout calendar, frozen forecast records and result-review workflows | Separate environment/state; source checkout is not a production archive |

The modules do not share authentication or databases. Public preview data and
browser records do not become Predome clinical evidence. See the
[current multi-perspective product review](docs/PRODUCT_REVIEW_2026-10-27.md).

## Install and run the workbench

Python 3.11 is required; use the locked `uv` environment:

```sh
uv sync --extra dev --frozen
uv run vbt --workspace "$PWD" workbench --port 18764
```

Open `http://127.0.0.1:18764/`. Built-in retrospective examples require no model
key. Private historical runs are not included. The workbench starts its market
collection worker; use the standalone patient server for a preview without it.

```sh
uv run python scripts/serve_patient_lab.py --state-dir /tmp/emeris-patient-state --port 18766
```

Patient routes are `/patient-lab` (original mathematical archive), `/molecule-lab`
(paired-assay view), and `/cohort-lab` (read-only cohort summary). The interactive
cohort playback is provided by the static build below. Existing workbench screens
and historical documentation are not yet fully English; this remains an explicit
localization gap, not a completed project-wide migration.

## Build the English patient research preview

```sh
uv run python scripts/build_patient_site.py --out /tmp/emeris-patient-preview
uv run python -m http.server 18768 --bind 127.0.0.1 --directory /tmp/emeris-patient-preview
```

Open `http://127.0.0.1:18768/`. The entry page explains capability boundaries and
links to:

- `molecule-lab/`: automatic state summaries from matched patient/drug assay counts,
  raw evidence, data cutoffs, browser-only saved runs and JSON export.
- `cohort-lab/`: 120 synthetic mCRC patients under two 1L/2L/3L treatment pathways,
  a compact paired patient matrix, continuous playback and source-backed regimen names.
  All burden trajectories are fictional and uncalibrated; see [methods](docs/REGIMEN_COHORT.md).
- `assay-cohort-archive/`: the unchanged 24-patient paired-exposure cohort and exports.
- `scenario-archive/`: the original fictional A/B/A+B mathematical demonstration.
  Earlier browser records remain at the same origin/storage key. Its parameters
  and independent survival illustrations do not enter the newer assay workflow.

The archived assay cohort summaries use the same 22 complete-pair patients. Available-case
summaries use 23 patients per molecule with different missing patients. Three
technical wells are never counted as three patients. All linked measurements are
synthetic; clinical response, PFS, OS and toxicity remain unavailable. There is no
human dose equivalence, clinically calibrated combination-regimen model or treatment recommendation.
See the [design, equations and validation gates](docs/PATIENT_MOLECULE_LAB.md).

## Independently check an exported patient record

```sh
uv run python -m vbt.patient_exports /absolute/path/export.json
uv run python -m vbt.patient_exports /absolute/path/cohort-export.json --csv /tmp/cohort-review.csv
```

The offline verifier checks the canonical checksum, duplicate-result equality,
selected patient/molecule/cutoff or cohort-view consistency, and the installed
approved synthetic template. It exits nonzero on failure. CSV output never
overwrites a file; missing values stay blank. The cohort page also provides the
same analysis-ready CSV for all days at the selected cutoff. Integrity verification
is not a signature, user authentication or scientific review.

## Evidence, state and security

Research inputs and sealed runs are immutable. New analyses and revisions create
new IDs. Execution, record integrity, scientific review and human sign-off are
separate states. Missing endpoint definitions, mapping and sources stay unresolved.
The original macOS Seatbelt execution sandbox has no unrestricted Linux fallback.

Active SQLite must live on local disk, never `/Volumes`. Use `--state-dir` for a
controlled directory. The cloud configuration uses `/state`. Do not expose the
single-user research backend directly to the internet. Private deployment requires
separate authentication, authorization, backup/restore and target-environment
acceptance. The public synthetic preview provides no enterprise accounts or
regulated-use certification.

Source control excludes raw research datasets, actual run archives, SQLite,
private notes, credentials and local backups. Evidence manifests establish
integrity/provenance scope, not licenses to redistribute third-party materials.
No repository content establishes validated clinical forecasting or investment
returns. Current release and production state must be checked separately.

## Tests and deployment

```sh
uv run pytest -q tests
```

Full sandbox execution tests require macOS. GitHub Actions also checks Predome
in its own pinned environment and runs real-HTTP patient browser tests. Historical
test counts are not current coverage evidence; inspect the actual commit's CI and
test scope. Source fixtures do not exercise private production data or verify
remote deployment.

- [Workbench guide](docs/WORKBENCH.md)
- [Market and pipeline radar](docs/MARKET_RADAR.md)
- [Customer pilot workflow](docs/CUSTOMER_PILOT.md)
- [Architecture](docs/ARCHITECTURE.md) and [data handoff](docs/DATA_HANDOFF.md)
- [Snapshot integrity and restore](docs/PORTFOLIO_MVP.md)
- [Cloud deployment](deploy/cloud/README.md)
- [Predome setup and evidence requirements](apps/predome/README.md)
- [CI](.github/workflows/ci.yml)

Some linked historical documents retain their original language. Do not infer
that an old local acceptance report proves the current cloud deployment, shared
accounts, coverage, customer SLA or scientific validity.

## License

[MIT](LICENSE) applies to this repository's code. It does not grant rights to
third-party papers, datasets, models, evidence packages or drug assets.
