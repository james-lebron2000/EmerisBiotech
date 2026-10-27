# Hosted browser QA

The public synthetic preview is hosted behind an edge that can inject an inline
Cloudflare JavaScript-detection bootstrap. The deployed application CSP blocks
that inline script. Its request-dependent hash and edge metadata are absent from
the source build. Do not add `unsafe-inline`, wildcard script sources or dynamic
allowlists to the application's CSP to make a browser receipt look warning-free.

`tests/hosting_console.cjs` classifies only the exact blocked hashes of observed
edge bootstrap scripts, on the selected `chatgpt.site` origin. The recognizer
requires the known wrapper, Cloudflare parameter assignment and challenge-platform
script path. Such messages are preserved under `hosting_warnings`. Unknown inline
script violations, resource failures and application exceptions remain fatal. The
classifier includes negative self-tests and does not change runtime policy or
permit script execution. This is observability classification, not a claim that
the hosting provider, a blocked script or an application is security-certified.

Run the actual cohort suite against the deployed root:

```sh
COHORT_PUBLIC_URL=https://emeris-patient-lab.lebrow.chatgpt.site node tests/ui_cohort_public.cjs
```

Provide the installed Playwright module/browser as documented by the CI or local
runtime when necessary. The browser receipt records actual JSON/CSV download,
offline verification, tampering rejection, denominator/population controls,
playback, keyboard/evidence retention, responsive layouts and corrupted-template
failure. It reports `errors`, `application_errors` and `hosting_warnings`
separately. Checks use synthetic fixtures and new browser contexts; they do not
inspect real customers' stored records or establish scientific validation.

Release v2 is pinned to repository merge
`6ff62ee56ca5b06e246a8d971a2e0578d20c32c4` (source head
`77e7e53caca0110011e60b35ac6294b167e6b9b8`). Its PR CI run
`40449220352` passed both jobs, including 1,695 workbench passes / 3 skips and four
real-HTTP patient browser suites. Site source commit is
`c3325bc96e8194c129aa107bab68180a164ffb1d`; live asset fingerprints are in
`release.json`. These are source/deployment receipts, not scientific acceptance.

The actual public cohort, single-patient and archive suites passed after v2
publication. The cohort receipt had zero application errors and separately
reported blocked hosting-edge bootstrap warnings. The public console is therefore
not described as warning-free. The QA-classification follow-up is tracked in
[#49](https://github.com/james-lebron2000/EmerisBiotech/issues/49).
