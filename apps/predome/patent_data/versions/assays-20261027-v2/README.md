# Patent assay evidence version 2

This explicitly selected research data directory adds two original-page-checked
experiments to the two existing experiments. It does not replace the default
`patent_data/catalog.json`, amend any frozen input or register a forecast.

| Publication | Existing experiment retained | Additional experiment |
|---|---|---|
| WO2019125938A1 | Example12 / SEQ17, Table3 FBS/BSA cAMP | Table4, 0.1% casein cAMP, PDF30–31 |
| WO2020240467A1 | Example7 unconfirmed phase1 ORR | Example7 observed human PK, PDF94; Figure23 context PDF129 |

Retatrutide's new casein EC50/SEM/n values are GcgR 0.122/0.00931/15,
GIPR 0.0529/0.00394/18 and GLP-1R 0.162/0.0100/18, in nM. Table4 does not
report its concentration series, incubation or exact medium details. Those values
remain null and are not inherited from Table3. The printed control name
`gGLP-1 amide` remains unresolved. Repeats do not create independent drug projects.

The Dato-DXd PK passage reports mean AUClast 914 μg·d/mL, Cmax 196 μg/mL and
terminal half-life 5.45 days in the repeated 8 mg/kg context. Its overall PK
population is 61 across doses; the 8 mg/kg parameter-specific n is unknown.
The ORR n=34 is not borrowed. Exact cycle, integration interval, dispersion and
explicit PK cutoff remain unknown. The older OCR omitted the microgram sign in
Cmax; the new card records the original-page unit without editing OCR.
Example4's monkey-to-human simulation is excluded from this observed-human card.

The new receipts record actual checks of these added experiments in two AI role
contexts. Existing identity and experiment checks are carried forward through
their original receipt IDs and hashes. Both roles share a base model;
**human scientific review count is zero**. Source checking does not establish a
current phase3 treatment effect, calibration, patent rights or clinical validity.

## Explicit selection

Existing library and CLI interfaces already support this separate directory:

```python
from emeris_live.patent_evidence import read_catalog, freeze_evidence_pack

root = "/absolute/app/patent_data/versions/assays-20261027-v2"
catalog = read_catalog(data_dir=root)
packet = freeze_evidence_pack("retatrutide", explicit_cutoff_utc, data_dir=root)
```

Use `--patent-root` with the existing `scripts/prepare_patent_pair.py` to prepare
a new, explicitly identified paired proposal from an already prepared single-target
clinical specification. The common cutoff must follow the new review completion
time. Both arms must still satisfy the real budget, target and evidence gates;
preparation is not registration or a model call.

`PREDOME_PATENT_DATA_DIR` can select this directory for a separately configured
internal process. No default environment or production configuration is changed
by adding this version. Ordinary public trial comparisons still use their
original frozen run packets, not whichever catalogue is currently selected.

Do not rebuild this prepared version in place. Further data changes require a new
version and fresh source-bound receipt. Raw PDFs, OCR and referenced originals
remain ignored deployment artifacts. They must be supplied as their own pinned
archive and checked before enabling this directory. See the
[versioning and installation instructions](../../../docs/PATENT_EVIDENCE_VERSIONS.md).

The prepared original-source archive contains exactly 25 referenced files,
11,483,252 bytes. Its SHA-256 is
`a29ca774328285fe8bb7b08c451c79ccf081d2a4542e5f0ee2e885450b2a2974`.
The existing installer successfully staged and rechecked that exact archive in
a new local version directory while preserving the default catalogue's bytes,
inode and modification time. This was offline installation QA, not deployment
or scientific approval.
