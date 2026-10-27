# Predome Open Forecast provider configuration

The adapter is `emeris_live.arena_providers`. It performs real HTTP calls only
when explicitly enabled and configured. Its isolated tests do not establish live
provider availability or clinical prediction validity. Existing prediction and
sealed records are unchanged.

## Configure server-side credentials and model identities

Set `EMERIS_ENABLE_ARENA_LLM=true` and `EMERIS_ARENA_MODELS_FILE` to an operator-owned
JSON file outside the repository. The file contains `{"models": [...]}`. Use
environment variable references for credentials; literal credentials are rejected
as unknown registry fields. Do not expose the configuration file through static
routes or put secrets in shell arguments, logs, artifacts, Git, or the browser.

For the explicitly requested gateway, set:

```text
EMERIS_ARENA_ALLOWED_PROVIDER_HOSTS=sem.ensureok.ai
EMERIS_ARENA_MODELS_FILE=/opt/emeris-live/config/arena-models.json
```

An entry for a model independently confirmed in the gateway's `/v1/models`:

```json
{
  "models": [
    {
      "id": "glm-52",
      "name": "GLM 5.2",
      "provider": "openai_compatible",
      "model": "glm-5.2",
      "base_url": "https://sem.ensureok.ai/v1",
      "api_key_env": "SEM_API_KEY",
      "max_output_tokens": 1024,
      "timeout_seconds": 90,
      "json_mode": true
    }
  ]
}
```

The `id` is Predome's stable entry ID. `model` is the exact requested API ID.
`returned_model` is recorded separately and can be null when absent. A gateway
does not independently attest to the model weights behind an alias. Four model
IDs served by one gateway do not establish four independent providers.

Supported protocols are `openai`, `openai_compatible`, `deepseek`, `anthropic`,
and `gemini`. Default official endpoints are fixed. Custom endpoint hostnames
must be explicitly allowlisted by an operator; HTTPS is required and IP literals,
credentials in URLs, queries, fragments, redirects and local hostnames are rejected.
Browser submissions must never set these environment variables or configuration.

Without a configuration file, four unconfigured slots read:

| Provider | Exact model variable | Credential variable |
|---|---|---|
| OpenAI | `EMERIS_ARENA_OPENAI_MODEL` | `OPENAI_API_KEY` |
| Anthropic | `EMERIS_ARENA_ANTHROPIC_MODEL` | `ANTHROPIC_API_KEY` |
| Gemini | `EMERIS_ARENA_GEMINI_MODEL` | `GEMINI_API_KEY` |
| DeepSeek | `EMERIS_ARENA_DEEPSEEK_MODEL` | `DEEPSEEK_API_KEY` |

No model ID is invented when these variables are absent. `list_models()` returns
safe metadata; `available=true` means configuration permits a call, not that a live
probe succeeded. `registry_status()` also provides a safe configuration error code.
Both omit credential names, values, endpoint URLs and configuration paths.

The registry also permits `enabled`, `temperature`, `reasoning_effort`,
`api_version`, and `pricing`. Only explicitly configured sampling parameters are
sent. `reasoning_effort` applies to chat-completion protocols; `api_version`
selects the Anthropic API header, defaulting to `2023-06-01`. `json_mode=false`
omits the compatible chat API's response-format option for a protocol known not
to support it; local validation always requires JSON. Never change these options
after observing a poor forecast within the same registered attempt.

## Frozen input contract

```python
forecast(model_id, packet, workflow="single_pass", budget={...})
```

`packet` requires:

- `target`: nonempty `endpoint`, `population`, `comparison`, `success_definition`.
- `data_cutoff` (or `information_cutoff_utc`): ISO date or timezone-aware timestamp.
- `evidence`: 1–100 objects with unique `id`, `text`, HTTPS `source_url`, and
  `availability_date`, `available_at`, or `publication_date`.

Dates after the cutoff, missing dates/sources, duplicated IDs and evidence with
`eligible_for_forecast=false` are rejected before any HTTP request. Date-only
values do not establish an intraday publication time; the packet builder remains
responsible for scientific review, exact source timestamps where needed, and
confirming drug–patent identity. The adapter preserves the original input.

Only target and a whitelist of evidence fields are transmitted. Top-level peer
forecasts, actual outcomes, profiles and arbitrary metadata are excluded. This
reduces accidental leakage but cannot detect a future outcome embedded inside
supplied evidence text, or exclude pretrained memory in retrospective tasks.

Provider1.3.0 also preserves optional checked `page_references`, `source_anchor`
and `document_version` in both evidence-review and forecast requests. Missing,
null and empty-page-list values remain distinct. Recorded page numbers do not
establish a universal PDF numbering scheme; no location or version is inferred.
Invalid locations fail before transport. The shared standard-library contract
and its raw implementation hash are included in new frozen execution identities.
This does not establish source correctness or upgrade historical inputs. See
[recorded evidence locations](RECORDED_EVIDENCE_LOCATIONS.md).

`single_pass` makes one request. `evidence_review` makes a structured evidence
review followed by a forecast using the same model and original evidence. Review
findings must reference original evidence IDs. Both calls are retained, counted
and charged in the workflow result. No model reads a competing model's output.

## Budget policy and currency

Unknown prices are not zero cost. A missing price configuration requires an
explicit token-only policy, for example an operator-approved small smoke run:

```python
budget = {
    "budget_policy": "token_only_unpriced",
    "max_requests": 1,
    "max_output_tokens": 1024,
    "max_input_chars": 4000,
    "timeout_seconds": 90,
}
```

`max_input_chars` bounds the canonical evidence packet before any review step;
system instructions and provider framing are additional input. There is no
monetary guarantee under this policy. The returned `cost_usd`, `cost_amount` and
`cost_currency` remain null when rates are unknown. Provider token usage is still
recorded when present. The orchestrator must separately cap registered calls and
must not expand an unpriced smoke run automatically.

Once rates have been verified, a pricing object can contain:

```text
currency: USD or CNY
input_per_million: verified numeric input rate
output_per_million: verified numeric output rate
cache_read_per_million: optional verified cached-input rate
cache_write_per_million: optional verified cache-creation rate
source_url: HTTPS pricing source
verified_at: source verification date
```

Legacy `input_usd_per_million` and `output_usd_per_million` are accepted only for
USD. Mixed or ambiguous units are rejected. Use `max_cost_usd` for a USD estimate
ceiling, or `max_cost_amount` with `cost_currency` for an explicitly matched
currency. Unknown prices or mismatched currencies fail before a request.

`cost_amount` is usage multiplied by configured rates, with
`cost_basis="configured_rates_estimate"`. It is not an invoice. CNY costs are
never copied into `cost_usd`; no exchange rate is assumed. A nonzero cache count
without its corresponding configured rate leaves the cost unknown. Missing usage
also leaves the cost unknown rather than zero. Known partial costs remain in
`known_cost_amount` when a later step has unknown usage.

The preflight estimate conservatively uses packet bytes and bounded output,
plus space for the review stage. Provider tokenization, hidden reasoning, gateway
tariffs and actual billing can differ. Provider account spending limits are the
authoritative billing protection. Operator config must use the gateway's own
verified prices when a gateway is used, not a vendor list price.

## Output and failure records

The response contains status, probability, concise Chinese explanation, exact
target definition, supporting/opposing citation IDs, assumptions, unknowns,
requested/returned model, config hash, adapter/workflow versions, input/request/
prompt/response hashes, usage, costs, latency and `attempts`.

All citations must be in the supplied packet. A prediction must cite at least one
item. Booleans, strings, NaN, infinite values and probabilities outside [0,1] are
rejected. `abstain` requires null probability. Refusals, truncation, non-JSON,
unknown fields and malformed provider responses do not become forecasts.

Every actual request has its own record, including failures. There is no automatic
transport retry, semantic retry, repair prompt, model substitution or averaging.
Provider errors are reduced to fixed codes; response bodies, headers and secrets
are not returned. Hidden thinking/reasoning fields are not retained or published.
The raw HTTP response is hashed without exposing its contents.

The caller must preregister tasks before execution, freeze exact configurations,
persist results atomically, separate forecast versions, apply public visibility
rules, and enforce study-level budgets. These duties are not replaced by the
adapter or its passing tests.

## Verification and primary interface references

Run isolated tests from the repository root:

```sh
PYTHONPATH=apps/predome python -m pytest apps/predome/tests/test_arena_providers.py -q
```

Tests use `httpx.MockTransport`, fixture-only credentials and fixture model IDs.
They verify protocol shapes, evidence/time/citation gates, failure sanitization,
budget policy, currency separation and workflow provenance. No paid request is
made by the test suite.

Interface documentation checked on 2026-10-22:

- [OpenAI Chat Completions](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)
- [Anthropic Messages](https://platform.claude.com/docs/en/api/messages/create)
- [Gemini generateContent](https://ai.google.dev/api/generate-content)
- [DeepSeek Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/)

Account access, model availability and gateway prices must be verified at runtime.
