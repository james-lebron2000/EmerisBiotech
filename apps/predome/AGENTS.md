# Predome application development guide

The repository-root evidence, credential and sandbox requirements also apply to this application.

## Runtime and evidence boundaries

- `emeris_live/` contains reusable runtime implementations. `scripts/` contains command entry points, and `static/` contains the shipped browser interface. Scripts may import runtime code; runtime code must not import scripts.
- Keep command execution behind `main()`. Importing a library must not collect evidence, invoke a model, start a task or create runtime state.
- Shared public-content rules belong in `arena_public_contract.py`, which uses only the standard library. HTTP projections and offline brief builders consume it; the brief builder must remain usable without importing FastAPI, provider or database modules.
- Static checks parse and compile Python in `emeris_live/`, `scripts/` and `deploy/`, rejecting parser errors, compiler-context errors, production `assert` statements and direct Python imports that reverse the runtime-to-CLI boundary. They do not execute the modules, create `.pyc` files or interpret Python embedded in strings; the shipped remote deployment programs have separate normal/optimized subprocess tests. This is not a sandbox for arbitrary dynamic imports or executable code.
- Validate incoming API/provider/file data at its owning boundary. Do not remove later checks that enforce another boundary, such as source-byte hashes, authenticated access, frozen-target identity or result-publication eligibility.
- Keep exact clinical target, source time, forecast time, model version and original result distinguishable. Missing calibration remains unknown; model confidence must not become a fabricated success probability.
- Preserve sealed inputs, ledger history, failed calls and old forecasts. A changed input or analysis requires a new version/run; a workflow cleanup does not authorize rewriting them.

## Package checks

Run these commands from `apps/predome` with Python 3.11, Node 22 for JavaScript syntax checks, and the dependencies in the existing requirement files:

```sh
python scripts/check.py
python scripts/check.py --suite static
python scripts/check.py --suite full
python scripts/check.py --browser
```

- The default `source` suite runs static checks and the source-fixture tests selected by `ci-tests.txt`. It requires a source-only checkout without the guarded local frozen evidence paths. Git-published Arena and patent design/configuration files remain allowed.
- `--suite static` runs the static checks only. It does not execute the application tests or validate clinical predictions.
- `--suite full` runs static checks and the full application pytest suite. Use it only with authorized access to the existing evidence required by those tests. It must not fetch or regenerate missing evidence.
- `--browser` additionally runs eight controlled offline browser fixture scripts. Page and API requests use intercepted fixtures; the public-download test also serves a fixture ZIP from a temporary loopback-only HTTP listener because the browser's download channel bypasses page interception. Node 22, Playwright 1.56.1 and its Chromium browser must already be installed; the check does not install them. These fixtures render shipped assets and must not reach providers, production state or external services.
- `python scripts/test_source.py` remains a compatible source-fixture entry point. Focused iteration may use `python -m pytest -q tests/test_<area>.py`; run the canonical applicable check before publishing.
- Do not remove evidence guards or enable paid providers to make CI pass. Report unavailable prerequisites explicitly. Full application tests remain separate from the root workbench tests.

## Testing and changes

- Exercise the actual ledger, API, source validator and browser assets. Use temporary files/databases and HTTP transport fixtures rather than replacing the whole implementation or duplicating its algorithm in tests.
- Test a real failure mode or contract change. Do not add tests that merely restate a low-impact edit.
- When a public API field or output contract changes, update the runtime projection, static consumer, relevant tests and documentation together. Keep public projections free of internal prompts, credentials, raw reasoning and private review details.
- For publication filtering changes, test overview, selected detail, JSON and brief downloads against the same actual producer. Withhold unsafe summaries without changing the original model record, direction or generation time; avoid silently repairing the frozen input.
- Keep helpers close to their owning code and extract them when they express a reused operation or a real boundary. Required runtime checks use explicit exceptions, not bare `assert`.
- Preserve semantic visual roles: outcome colors communicate outcome/status, and pending or unreviewed results must not acquire a winner through styling. Validate the actual interface at desktop and mobile sizes when changing it.
- PR verification states the suite and its limits. Synthetic fixtures establish engineering behavior; real-record checks establish only the checked record properties. Neither establishes independent adjudication, prospective predictive validity or clinical benefit.

See [workflow adaptation and verification scopes](docs/AGENT_WORKFLOW.md) for the rationale and command matrix.
