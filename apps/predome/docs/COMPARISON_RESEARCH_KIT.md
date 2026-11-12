# Trial comparison research kit

The public page shows a clinical-only model matrix and, on request, separate patent-condition and Agent records. The **Download comparison kit** link inside that expanded section saves the selected exact trial question for offline reading and discussion. It does not start a prediction, retrieve a source document, write an outcome or change an earlier record.

## Contents and scope

The independently versioned ZIP contains five files:

| File | Purpose |
| --- | --- |
| `clinical-record.json` | The existing public record 1.4, including the clinical-only baseline, consensus and current result state. |
| `comparisons.json` | The existing `predome-public-trial-comparisons-1` projection with every assigned patent arm and Agent system for this exact target/cohort. |
| `comparison-report.html` | A self-contained report, without scripts or external page resources, suitable for offline reading and printing. |
| `comparison-report.md` | A text report for discussion and version review. |
| `manifest.json` | Exact target/cohort/NCT, both public record versions, export time, original recorded model times, and member byte sizes/SHA-256. |

Schema: `predome-public-comparison-research-kit-1`. The HTTP entry point is `GET /api/arena/public/targets/{target_id}/comparisons/download`. Both JSON objects derive from one verified native ledger snapshot. A prediction finishing during export cannot silently join an older baseline. The route keeps historical targets distinct and preserves source/database checks; unavailable records are not replaced with examples.

The report separates three questions: baseline model judgments, changes after adding patent evidence, and observations from complete Agent systems. Each record retains its own model/version, status, generation and lock times, brief public explanation and recorded references. Failed, pending and abstained assignments remain visible. Missing or ambiguous controls and unknown matching flags stay unresolved. Recorded source pages/sections/document versions do not establish that a model read the full original or that a human verified the experiment.

## Actual outcome and individual matches

The report preserves the current public actual-outcome state and official source. It does not adjudicate a result. The page and report can show an individual match only when the upstream public producer permits it under the existing independent scientific-acceptance and timing gates. Pending, unaccepted, mixed, inconclusive, historical, failed or timing-ineligible records receive no hit/miss. A result announcement or matching source alone is not scientific acceptance.

Baseline consensus remains separate. Patent and Agent rows add no votes or aggregate accuracy ranking. A changed judgment is not evidence of improved prediction; matching budget caps do not establish equal actual computation. Success probability and confidence remain N/A because calibration has not been established. Private prompts, reasoning traces, execution/configuration receipts, costs and raw model estimates are excluded.

## Offline verification

From `apps/predome`, use the standard-library CLI with the downloaded path:

```sh
python3.11 scripts/verify_comparison_kit.py /absolute/path/to/predome-comparison-research-kit.zip
```

An expected ZIP SHA-256 can be supplied with `--expected-sha256` when obtained through a separately trusted channel. The verifier checks bounded ZIP membership, duplicate entries/JSON keys, closed public fields, exact target/cohort associations, file hashes/sizes, and deterministic report reconstruction from the two JSON objects. It does not modify or extract into the original prediction store.

These checks establish the consistency of this exported package. They do not independently authenticate its publisher, official sources, model weights, hidden registration bindings, human qualifications, omitted deadline/TSA facts or clinical validity. Public matching flags remain declarations derived by the upstream producer; the private conditions cannot be reconstructed from this public subset. Export time describes the report, not a new prediction or an independent attestation of the stored model times. An untrusted package can be internally consistent.

## Compatibility and verification

The old `/record`, `/brief/download`, Clinical Brief schema, Campaign ZIPs and historical proof validators keep their contracts. This kit has its own builder and verifier instead of weakening those older formats. It contains public source links and metadata, not third-party PDF originals or private evidence packets.

Regression uses real temporary SQLite/ledger/API operations and controlled browser fixtures. A synthetic accepted-result example tests the engineering gate; it is not an actual adjudication. Existing real-record acceptance is recorded separately and retains failed attempts, original values and times. SQLite query-only reads may create ordinary WAL/SHM sidecars; ledger writes, main database integrity and logical records are verified separately from transient storage artifacts.

Implementation: [Issue #48](https://github.com/james-lebron2000/EmerisBiotech/issues/48), [PR #3](https://github.com/james-lebron2000/EmerisBiotech/pull/3). Current work remains GitHub-only, with no server operation, deployment or new model call. Jake's next-forecast scientific review remains pending under [Issue #41](https://github.com/james-lebron2000/EmerisBiotech/issues/41).
