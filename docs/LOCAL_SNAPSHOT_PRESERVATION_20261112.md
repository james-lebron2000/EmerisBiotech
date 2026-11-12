# Local workbench snapshot preservation — 2026-11-12

This branch preserves previously uncommitted workbench source, documentation and tests from the local checkout based on `69c6b59`. It is a historical development snapshot, not a replacement for the newer `codex/ai-native-uc` implementation and not a production release.

Only source, tests and documentation are selected. Local `output/`, artifacts, SQLite databases, environments, credentials and backups remain outside this publication. Historical acceptance and research statements retain their original dates and scope.

Verification performed for this preservation:

- `uv run --offline --frozen pytest -q tests`: 1681 passed in 94.65 seconds.
- `git diff --check`: passed.
- Selected source and documentation scanned for credential/private-key/token literals; no matches detected.

These checks establish engineering behavior of this snapshot. They do not rerun historical scientific analyses, container acceptance, production deployment or independent clinical validation.
