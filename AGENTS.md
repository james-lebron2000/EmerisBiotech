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
