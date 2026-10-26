# Predome application

These instructions supplement the repository-root `AGENTS.md` for `apps/predome`.

## Ownership and scientific semantics

- Keep Predome dependencies and runtime state separate from the root workbench environment. Do not import `src/vbt` internals to share a calculation.
- Reuse the owning service/store operation from API, export and UI code. Treat imported registry text, uploaded documents and historical JSON as untrusted at their entry boundary.
- Preserve the specified trial, population, comparator, endpoint, cutoff and statistical success definition. Registry completion or termination is not a result label.
- Keep frozen predictions, later outcome reviews, source integrity and human adjudication distinct. Never rewrite an old prediction to match a new result or count historical replay as a prospective hit.
- Provider failure, refusal and missing evidence stay explicit. Do not fabricate fallback probabilities or calibrated confidence. A successful HTTP response alone does not establish a successful prediction.
- Test restart/retry behavior when changing queues or ledger operations. Do not assume provider invocation and local persistence form an exactly-once transaction.

## Source-only checks

Run from this directory in a clean checkout, using Python 3.11:

```sh
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock.txt -r requirements-research-v4.txt
.venv/bin/python scripts/test_source.py
```

- `ci-tests.txt` is the explicit public source-test selection. A new source-only regression belongs there when it is safe without private evidence. Keep evidence-dependent tests outside this selection.
- `scripts/test_source.py` refuses a checkout containing evidence directories. Use another clean checkout; do not delete evidence to make a test run.
- Tests use temporary stores and synthetic fixtures. Stub external model/network boundaries; do not call production or paid providers from a regression test.
- Browser checks must load production HTML/JavaScript and intercept external effects. Reuse the existing fixtures; check empty, unavailable, error and delayed-response states when affected. Commands are in [CONTRIBUTING.md](../../CONTRIBUTING.md).

## Evidence and release

- Public source tests do not establish that frozen evidence is installed or that a release is accepted. Full evidence-dependent testing requires a separately verified, authorized bundle; follow [README.md](README.md) and [DEPLOYMENT.md](DEPLOYMENT.md).
- Keep source manifests and frozen evidence manifests as historical records. Do not regenerate trusted hashes from unknown inputs to make verification pass.
- Preserve existing ledger events, database backups and rollback checks when changing release scripts. CI does not perform deployment or result adjudication.
