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

## Repository workflow

- The default branch is `main`. Start new work from the current `origin/main` and target PRs at `main`, unless the user specifies another base. Continuing an existing PR uses its current head; do not mix unrelated pending PRs.
- Inspect the worktree before editing. Preserve uncommitted changes and local state; use an isolated checkout when another task is active.
- Use short, descriptive branch names, for example `agent-workflow` or `forecast-recovery`. Use conventional commit messages and PR titles such as `docs: clarify source checks` or `fix(predome): preserve cutoff dates`.
- Use the existing dependency locks and release workflow. Dependency changes must update the owning environment's lock; do not regenerate unrelated locks or introduce a new toolchain for a small change.

## Application boundaries

- `src/vbt` is the research workbench; `apps/predome` is a separate application. Each owns its dependencies, configuration and runtime state. Do not import either application's internals into the other or merge their databases implicitly.
- Validate untrusted input at its owning API, file-import or persistence boundary using existing contracts. Pass validated values inward; do not repeat schema checks throughout internal code. Historical records without a proven schema remain untrusted.
- Keep HTTP and view code thin: reuse the owning domain operation rather than copying its calculations into a route, export or UI. UI rendering must not silently mutate research records.
- Keep durable records as source facts with provenance. Derive display summaries from those records rather than storing another independently editable outcome. Do not remove fields from existing sealed formats to pursue this rule; schema changes require compatibility and migration review.
- Keep submission, execution, record integrity, scientific review and result adjudication separate. A queued request or recovered process is not a completed model run. Do not claim exactly-once execution unless the implementation and tests establish it.

## Implementation style

- Follow the surrounding Python and JavaScript conventions. Keep changes focused; avoid unrelated formatting or renaming.
- Prefer clear early returns, precise domain names and small functions with a readable main path. Extract helpers for reused logic or a meaningful complex boundary, not every single-use expression.
- Use existing typed contracts where they help public interfaces. Do not add a parallel schema or dependency for values already represented by an owning model.
- Catch exceptions where they can be handled meaningfully. Do not turn parsing errors, unavailable models or missing evidence into default probabilities, successful jobs or empty verified results.
- Comment on non-obvious constraints and evidence semantics; avoid comments that repeat the code. Prefer context-managed resources and the existing connection lifecycle conventions.
- For UI changes, reuse the application's shared styles and semantic status labels. Preserve accessible labels, keyboard interaction and explicit unknown/error states. Check supported themes and narrow/wide layouts when affected.

## Verification and delivery

- Follow [CONTRIBUTING.md](CONTRIBUTING.md) for environment setup and checks. Read `src/vbt/AGENTS.md` or `apps/predome/AGENTS.md` when working in that application.
- Run focused regression checks for changed behavior, then the applicable application checks. Repository-root pytest covers the workbench only; it does not verify Predome. There is no repository-wide Bun or TypeScript check.
- Test the real domain operation or production component. Use temporary state and fixtures; replace external providers/network at their boundary when needed for deterministic, offline tests. Do not duplicate the implementation as a test oracle.
- Do not invoke paid models, production writes, downloads or deployments as an incidental test. Source-only CI uses isolated fixtures; release acceptance with verified evidence is a separate check.
- For HTTP, storage, prediction or job changes, cover the relevant failure/retry/restart and compatibility behavior. Add tests for a concrete changed contract, not a copy of a low-impact edit.
- In PRs, describe the user-visible problem, resulting behavior, checks actually run and remaining limitations. UI behavior changes should include an appropriate screenshot or browser receipt; do not require an unrelated decorative animation.
- Publishing a branch or passing CI does not deploy a service, approve scientific findings or validate clinical predictions. Report each state separately.
