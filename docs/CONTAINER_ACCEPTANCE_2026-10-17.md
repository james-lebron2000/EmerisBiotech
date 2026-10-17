# Local container acceptance — 2026-10-17

This record covers local engineering behavior on Docker Engine 29.2.1 / Linux arm64 under Colima. It is not a public deployment, authenticated-proxy acceptance, complete market coverage, scientific validation, or customer production release.

## Later market/disclosure source checkpoint

The subsequent market/disclosure image `emeris-biotech:qa-market-20261017` passed **22 isolated runtime checks**. Image ID: `sha256:71952620f3855559fb7fa4766713a93108e40bc55f5043f9fb79816bdde52dd5`. All43 Python source files matched the source tree by SHA-256. It adds disclosure routes/queue and CSRF-protected, integrity-checked CSV exports. Fresh synthetic-only volumes and temporary loopback18865 were used; all dedicated runtime resources were cleaned, unrelated containers untouched. This is a later source tree than the38-file image below. Evidence: `/tmp/emeris-market-container-qa-412a377156/acceptance.json`; full checkpoint details are in `MARKET_DISCLOSURES_ACCEPTANCE_2026-10-17.md`. Remote authenticated-gateway and customer deployment remain unverified.

## Final committed-source image acceptance

The final local image **passed all 12 runtime checks plus the committed-source alignment gate**.

- Source commit: `bb188eb9819bc25d693f763ae529ca363a13748a`.
- Local image tag: `emeris-biotech:qa-20261017`.
- Image ID: `sha256:1a7d4bc550ea70046cbc20f50646795bef4f9cab04d306c7b56233c1cebd2099`.
- Source SHA-256 comparison: all **38 Python source files** matched the committed repository; `git status --porcelain -- src` was empty. All tracked files in this repository's `src` tree are Python source files.
- Lockfile SHA-256: `7965c85f720de01853afcc0ea67b19d552ef85a3b4f2c97c46d84b1392aad19d`.
- Runtime results: UID/GID 10001; both new named volumes writable; worker starts and exits 0 on SIGTERM; disabled collection produced zero runs; the synthetic queued job persisted unchanged across restart; the cloud homepage and coverage page returned 200 with the configured Host, while absent and incorrect Host returned 403; the temporary port was bound only to 127.0.0.1.
- Cleanup: both temporary containers, both volumes and the temporary network were removed successfully. The local image was retained.
- Raw local evidence: `/tmp/vbt-container-qa-24451981f3/acceptance.json`.

The final rebuild reused the frozen third-party dependency layer and installed only the application package after copying the frozen source. The HTTP test still uses the temporary localhost bridge described below; authenticated gateway and public deployment remain outside this acceptance.

## Initial build and runtime evidence

The frozen-lockfile build completed successfully using Python 3.11.17. The initial tested image was `sha256:74fcf7eeca64d5a4ba965d4f0436c68e75d579db533ad7a18722dc91290dc302` under the local tag `emeris-biotech:qa-20261017`.

Fresh randomly named `/workspace` and `/state` Docker volumes contained only synthetic acceptance data. No existing research database was mounted. The following real container checks passed:

| Check | Observed result |
|---|---|
| Effective process identity | UID 10001 / GID 10001 |
| Named-volume ownership | Process could write to both `/workspace` and `/state` |
| Worker launch | `Market collector ready` from the actual container process |
| Collection controls | `enabled=0`, network disabled, one synthetic future-due queue item |
| Accidental collection | `runs=0` before and after restart |
| SIGTERM shutdown | Worker exit status 0, no OOM kill |
| Queue persistence | The same job ID, queued status and next-at value survived restart |
| Cloud HTTP | HTTP 200 for `Host: qa.emeris.invalid` with `PUBLIC_ORIGIN=https://qa.emeris.invalid` |
| Missing Host | HTTP 403 |
| Incorrect Host | HTTP 403 |
| Coverage page | `/market/progress` returned HTTP 200 |
| Published port scope | Only `127.0.0.1:18864` was bound during the test |
| Cleanup | Both containers, both volumes and temporary bridge were removed |

The worker ran with `--network none`. The HTTP test used a temporary ordinary bridge and a localhost-only port. A direct host connection to an app published on an `internal` Docker bridge was not reachable on this Colima host, even after a bounded readiness retry. Production Compose retains its internal backend topology and uses a separate gateway; this acceptance does not replace an end-to-end authenticated gateway test.

Raw local acceptance evidence: `/tmp/vbt-container-qa-249a9921ef/acceptance.json`.

## Deployment wiring and build iteration

`tests/test_market_worker.py` completed **7 passing tests**, including real standalone `docker-compose` resolution because this host lacks the `docker compose` subcommand. The resolved configuration asserts app/worker port isolation, internal backend network, separate collection egress, volume sharing and gateway access-config secret wiring. Configuration resolution does not itself establish that the gateway has run.

The Dockerfile now installs frozen third-party dependencies before copying application source. This preserves the large scientific dependency layer across source-only changes. The intermediate optimized cache build completed successfully as image `87e0ab267ea3` under temporary tag `emeris-biotech:qa-cache-20261017`. The same 12 runtime checks also passed against that optimized image with fresh temporary volumes; raw local evidence is `/tmp/vbt-container-qa-7614c9b3ce/acceptance.json`. All temporary runtime resources were removed afterward.

## Repeating the source alignment gate

Rebuild the release image after any new source commit, compare every image `/app/src/**/*.py` SHA-256 with the committed repository source, require a clean `git status --porcelain -- src`, and repeat the runtime checks with fresh synthetic-only volumes. A later documentation-only commit does not change the source tree proven above.
