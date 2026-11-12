# Comparison kit source validation — 2026-11-12

The existing comparison-kit implementation was reviewed before preserving the local changes in Git. The ZIP endpoint derives the clinical baseline and comparison arms from one native snapshot, exports only closed public fields, and retains original result/timing eligibility. This review did not change production source code.

## Checks

- `python scripts/check.py` in a source-only copy: **2,405 passed, 15 skipped** in 109.72 seconds; static Python and JavaScript checks passed. Runtime: Python 3.11.14 and Node 22.22.3. The copy contained tracked source plus the five new implementation/document/test files, without local frozen evidence or virtual environments. Existing system Python supplied the application dependencies; no packages were installed.
- `.venv/bin/python -m pytest -q tests` at the repository root: **1,640 passed, 1 skipped** in 96.99 seconds, using the existing workbench environment.
- `node tests/ui_arena_public.cjs`: **passed**, with external requests intercepted and attachment downloads served only by a temporary loopback fixture. This is supplemental browser verification: installed Playwright **1.62.1** and Chromium **141.0.7390.37** were used with Node 22.22.3. The guide's pinned Playwright 1.56.1 package was unavailable, so this does not claim an exact pinned-toolchain run. No browser was downloaded.
- `git diff --check`: passed. Changed/new source and documentation were checked for credential patterns; the matched credential examples were explicit synthetic rejection fixtures, not usable secrets.

The public UI fixture checks exact-target download links and bytes, keeps the old brief download contract, preserves pending/failed/abstained states, and rejects correctness labels for unaccepted results, incompatible timing, replay records and inconsistent match metadata. The API/export tests use temporary ledgers and exercise archive bounds, unsafe contents, report consistency and source-snapshot preservation.

## Synthetic screenshots

These images render the shipped assets with explicitly synthetic accepted-result fixtures. The displayed matches and outcomes are test data, not actual clinical results or scientific acceptance. Both layouts were visually inspected after the browser assertions passed.

[Desktop screenshot](comparison-kit-ui-20261112/synthetic-accepted-kit-desktop.png)

[Mobile screenshot](comparison-kit-ui-20261112/synthetic-accepted-kit-mobile.png)

No production server was changed, no provider/model call was made, and no frozen evidence or historical prediction was regenerated. These checks establish engineering behavior only; they do not establish clinical validity, prospective accuracy, or Agent/patent benefit. Full evidence-suite validation was not run as part of this storage-maintenance task.
