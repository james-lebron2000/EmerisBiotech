# Platform scope

- Do not download research datasets. Read the downloader's files and manifests only.
- Write code/artifacts under this platform directory; active SQLite belongs on local disk, never /Volumes.
- Treat versioned inputs and sealed runs as immutable. New analysis => new run_id.
- Preserve execution, record integrity and scientific review as independent states.
- Do not turn synthetic tests, source-label analyses or code execution into clinical validation.
- Run meaningful regression tests for data/analysis/execution changes. Sandbox must fail closed.
- Never add unsandboxed fallback for proposed code. Do not inherit credentials into analysis workers.
- Explicitly label missing endpoint definitions, patient mapping or probe annotation as unresolved.
- Keep private credentials out of code, manifests, logs and git.

## Repository map and dependency direction

- `src/vbt` is the existing research workbench. `apps/predome` is the separate Predome application; it has its own dependencies, state and package guide. Sharing this repository does not merge their databases or identities.
- Keep reusable application code in its runtime package and command execution in `scripts/`, behind `main()`. Runtime libraries must not import CLI scripts. Imports must not start jobs, make network calls or write state.
- Keep shared publication rules below their HTTP and export consumers. Pure document builders must not import server routes, providers or storage to reuse a validation helper.
- Validate untrusted inputs at the boundary that owns the contract. Pass checked structures inward; preserve verification at distinct trust boundaries such as stored-byte integrity, permissions and publication eligibility.
- Test production implementations rather than copying their logic into tests. Use real temporary files, SQLite and Git repositories where relevant; replace external network/provider boundaries when tests must be offline.
- Extract helpers for reused logic or meaningful boundaries. Prefer explicit failure paths and required runtime checks that remain active under optimized Python. Predome production Python (`emeris_live/`, `scripts/`, `deploy/`) uses explicit exceptions rather than `assert`; test assertions remain appropriate.

## Checks and scope

- From the repository root, preserve the workbench regression with `uv run pytest -q tests` in its locked development environment.
- From `apps/predome`, use `python scripts/check.py` as the canonical Predome check; see its package guide for suites, browser prerequisites and full-evidence checks. Focused tests may run from the owning package while iterating.
- A source-fixture check, authorized frozen-record check, read-only deployment check and independent scientific review answer different questions. Report the command, scope, outcome and missing prerequisites; do not substitute one for another.
- When a public contract changes, update its producer, consumer, version where applicable, documentation and relevant tests together. Code checks must not regenerate frozen evidence or previous predictions.

## Collaboration and review

- The default branch is `main`. Continue authorized work on the existing `codex/predome-open-forecast` branch and PR #3 without resetting or renaming its history. Resolve the actual default branch before starting unrelated future work.
- Parallelize independent work with explicit file ownership; keep dependent edits and mutations sequential. Review the final combined diff and the results of relevant checks.
- Use concise conventional-style messages for new commits and PR titles. Describe the concrete behavior, verification and remaining limits; UI changes should include screenshots of the actual implementation.
- Keep durable records about new facts with explicit IDs and versions. A repeated read is not a new execution, and a local append-only log does not establish distributed exactly-once execution.

These workflow rules adapt selected principles from the [versioned OpenCode guide](https://github.com/anomalyco/opencode/blob/bcd713e2b71b433474d99fb1d29900c492c319a3/AGENTS.md). Its Bun/TUI commands, default branch and package-specific test restrictions do not apply here. See the [Predome adaptation notes](apps/predome/docs/AGENT_WORKFLOW.md).
