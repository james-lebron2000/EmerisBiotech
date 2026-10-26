# Research workbench

These instructions supplement the repository-root `AGENTS.md` for `src/vbt`.

## Ownership and state

- `models.py` and `portfolio_models.py` own their typed contracts. Preserve distinctions among execution, integrity, review and clinical release when adding fields or views.
- Keep analysis/execution in the owning domain modules; `workbench.py`, `*_view.py` and `ui_shell.py` present and route those operations. Do not trigger analysis or mutate records during page rendering.
- Research inputs and sealed snapshots remain immutable. A changed analysis creates a new run rather than editing an old result.
- Use temporary workspace and state directories in tests. Active SQLite stays on local disk. Preserve each store's connection lifecycle: short-lived factories and persistent Registry/PortfolioStore connections have different owners.
- An event or queue record describes what actually happened. Restart recovery must preserve interrupted/unknown states unless completion evidence exists. Review existing `market_jobs.py` and related tests before changing job recovery.
- The macOS Seatbelt executor fails closed. Linux/cloud must not acquire an unsandboxed analysis fallback. Do not pass parent-process credentials to analysis workers.

## Checks

Run from the repository root using Python 3.11 and the locked workbench environment:

```sh
uv sync --extra dev --frozen
uv run pytest -q tests/test_workbench.py tests/test_cloud.py
```

Add the test file matching the changed module. Shared views have focused checks such as `tests/test_ui_shell.py` and `tests/test_dashboard_view.py`; storage, analysis and execution changes need their corresponding tests as well. For full workbench regression on macOS:

```sh
uv run pytest -q tests
```

This suite does not test Predome. Cloud/interface checks do not replace macOS execution tests. Report unavailable platform checks as unrun rather than passing.
