---
title: Providers
description: LLM provider abstraction, LiteLLM integration, model routing, multi-provider resolution, and runtime provider management.
---

# Providers

The provider layer is how SynthOrg reaches every LLM -- cloud APIs, OpenRouter, Ollama, LM Studio, vLLM, or any custom endpoint -- through a single unified interface. It handles authentication, model discovery, cost metering, health probing, and runtime hot-reload without restarting the engine.

---

## Provider Abstraction

The framework provides a unified interface for all LLM interactions. The provider layer
abstracts away vendor differences, exposing a single `completion()` method regardless of
whether the backend is a cloud API, OpenRouter, Ollama, or a custom endpoint.

**Unified Model Interface:** `completion(messages, tools, config) -> resp`

| | Cloud API Adapter | OpenRouter Adapter | Ollama Adapter | Custom Adapter |
|---|---|---|---|---|
| **Method** | Direct API call | 400+ LLMs via OR | Local LLMs, self-host | Any API |

## Provider Configuration

???+ note "Provider Configuration (YAML)"

    Model IDs, pricing, and provider examples below are **illustrative**. Actual models, costs,
    and provider availability are determined during implementation and loaded dynamically from
    provider APIs where possible.

    ```yaml
    providers:
      example-provider:
        litellm_provider: "anthropic"  # LiteLLM routing identifier (optional, defaults to provider name)
        family: "example-family"       # cross-validation grouping (optional)
        auth_type: api_key             # api_key | oauth | custom_header | subscription | none
        api_key: "${PROVIDER_API_KEY}"
        # subscription_token: "..."    # subscription token (subscription auth only; passed to LiteLLM as api_key; sensitive -- use env vars or secret management)
        # tos_accepted_at: "..."       # timestamp when subscription ToS was accepted
        models:                        # example entries -- real list loaded from provider
          - id: "example-large-001"
            alias: "large"
            cost_per_1k_input: 0.015   # illustrative, verify at implementation time
            cost_per_1k_output: 0.075
            max_context: 200000
            estimated_latency_ms: 1500 # optional, used by fastest strategy
          - id: "example-medium-001"
            alias: "medium"
            cost_per_1k_input: 0.003
            cost_per_1k_output: 0.015
            max_context: 200000
            estimated_latency_ms: 500
          - id: "example-small-001"
            alias: "small"
            cost_per_1k_input: 0.0008
            cost_per_1k_output: 0.004
            max_context: 200000
            estimated_latency_ms: 200

      openrouter:
        auth_type: api_key           # api_key | oauth | custom_header | subscription | none
        api_key: "${OPENROUTER_API_KEY}"
        base_url: "https://openrouter.ai/api/v1"
        models:                        # example entries
          - id: "vendor-a/model-medium"
            alias: "or-medium"
          - id: "vendor-b/model-pro"
            alias: "or-pro"
          - id: "vendor-c/model-reasoning"
            alias: "or-reasoning"

      ollama:
        auth_type: none
        base_url: "http://localhost:11434"
        models:                        # example entries
          - id: "llama3.3:70b"
            alias: "local-llama"
            cost_per_1k_input: 0.0    # free, local
            cost_per_1k_output: 0.0
          - id: "qwen2.5-coder:32b"
            alias: "local-coder"
            cost_per_1k_input: 0.0
            cost_per_1k_output: 0.0
    ```

## Cost Recording

Every successful **scoped** `provider.complete()` call attributes a `CostRecord` to the agent and task that originated the work. Attribution flows through a `ContextVar` middleware rather than through per-call kwargs, which keeps the provider interface uniform across cloud APIs, OpenRouter, Ollama, and custom adapters. Calls made outside any `cost_recording_scope` -- infrastructure probes, model discovery, the engine turn loop, tests -- read `None` for the active context and are intentionally **not** attributed: the engine's post-execution recorder owns engine turns, and probe / discovery traffic is not user spend.

- **Scope contract**: callers wrap a `provider.complete()` invocation in `cost_recording_scope(cost_tracker, agent_id, task_id, project_id, call_category, currency)` from `synthorg.providers.cost_recording`. The scope is an `@asynccontextmanager` that sets a per-`asyncio.Task` `ContextVar`, yields, and resets on exit. Nested scopes shadow the outer one and are restored on exit; concurrent tasks see independent scopes.
- **Chokepoint**: `BaseCompletionProvider.complete()` reads the scope's context after a successful response, builds a `CostRecord` from `result.usage` + `result.provider_metadata` (`_synthorg_latency_ms`, `_synthorg_cache_hit`, `_synthorg_retry_count`, `_synthorg_retry_reason`) + `result.finish_reason`, and submits it via `cost_tracker.record(record)`. Calls outside any scope (probes, model discovery, tests) are no-ops.
- **Skip rule**: usage with both zero tokens and zero cost is skipped (matches the engine post-execution recorder). Free-tier providers with non-zero tokens still record.
- **Failure isolation**: any exception from `cost_tracker.record(...)` other than `MemoryError` / `RecursionError` is logged at WARNING (`PROVIDER_COST_FAILED`) and swallowed -- the user-visible provider response never depends on recording success.
- **Engine path**: the engine loop deliberately does NOT open a scope around its turn-level `provider.complete()` call. The post-execution `record_execution_costs(...)` recorder remains authoritative for engine turns because it accumulates per-turn metadata (turn number, retry counts, tool-response tokens for PTE) that the chokepoint cannot see synchronously. The chokepoint reads `None` and is a no-op for engine calls -- no double-counting.
- **Streaming gap**: `provider.stream()` does not fire the chokepoint. Streaming responses surface usage as a terminal `StreamEventType.USAGE` chunk, which the chokepoint cannot inspect synchronously without consuming the iterator and conflating recording with the stream-consumption contract. All cost-attributable LLM call sites in SynthOrg use `complete()`; a future PR can extend recording to `stream()` by hooking the terminal usage chunk.
- **AST gate**: `scripts/check_provider_complete_chokepoint.py` (pre-push + CI) walks `src/synthorg/` for `Await(Call(Attribute(_, "complete")))` nodes on `BaseCompletionProvider` instances and asserts each call site is either in an explicit allowlist (chokepoint itself, engine loop helpers, connection probes, health prober, registry docstring example) or has a `cost_recording_scope` opened in the same function.

This pattern mirrors `synthorg.observability.correlation.correlation_scope`, which is the established codebase precedent for cross-cutting per-call context bindings (`request_id` / `task_id` / `agent_id`).

## Cassette Record / Replay

Recorded-LLM **cassettes** make a company run deterministic and free to re-execute: record the exact provider responses of a run keyed by request, then replay them for byte-identical re-execution with zero real LLM calls. Like cost recording, this is a provider-layer concern, not per-driver.

- **Seam**: `CassetteCompletionProvider` (`src/synthorg/providers/cassette/`) wraps an inner driver and overrides the **public** `complete()` / `stream()` / `get_model_capabilities()` / `batch_get_capabilities()`. It deliberately overrides the public methods, not the `_do_*` hooks: `BaseCompletionProvider.complete` merges fresh `_synthorg_latency_ms` / `_synthorg_retry_count` into `provider_metadata` after `_do_complete`, so replaying through `_do_complete` would clobber the recorded metadata and break byte-identical replay. The three `_do_*` hooks are unreachable guards raising `CassetteInternalError`.
- **Decoration chokepoint**: `ProviderRegistry.from_config(..., cassette=...)` wraps every driver in one shared `CassetteSession` before the registry is frozen, so no consumer (engine, coordinator, judge, runtime builder) can bypass record/replay. In **replay** the inner driver is **not built at all** (no factory call), so a pure replay run constructs no real provider.
- **Keying**: SHA-256 over the canonical request `(method, provider, model, messages, tools, config)` via `synthorg.versioning.hashing.compute_content_hash`. Repeated identical requests within a run are disambiguated by a **per-task FIFO lane**: each distinct asyncio task is assigned a stable monotonic lane on its first provider call. Replay matching is `(request_hash, lane, seq)`. This is stable across record and replay iff the first-call order of distinct tasks is identical, which the deterministic simulation harness provides; a cassette miss / sequence exhaustion fails loudly (`CassetteReplayMissError` / `CassetteReplayExhaustedError`) and never falls through to a real provider.
- **Storage**: a single canonical JSON document (filesystem, no DB / no yoyo revision: this is test infrastructure). The session auto-persists after every recorded interaction (crash-safe), written atomically (temp file + rename). `cassette_format_version` gates incompatible formats with `CassetteFormatError`.
- **Redaction boundary (SEC-1)**: the replay key is hashed on the **raw** request, and the **response / stream / capabilities outcome is stored verbatim** because it *is* the byte-identical replay artefact. Redaction (pluggable `CassetteRedactor`; default `PatternRedactor` scrubs bearer tokens, `sk-` keys, AWS keys, PEM blocks, labelled secrets) applies **only to the human-readable `request_repr`**, which is never consulted for replay. Provider credentials never reach `complete()` (they live in driver config); the residual exposure is a model echoing a prompt secret into its own output, which is accepted and documented (cassettes are dev/test artefacts; default cassette runs use scripted/seeded providers).
- **Configuration**: `providers.cassette_mode` (`off` / `record` / `replay`) + `providers.cassette_path`, resolved once at the boot site via the Cat-2 bootstrap resolver (env > code default, `read_only_post_init`, `restart_required`); `off` is a structural no-op.
- **Scope**: the record/replay seam is complete and independently validated under the live engine harness (a recorded multi-turn agent run replays byte-identically with zero real provider calls). Wiring the cassette into the golden-company benchmark suite is owned by the benchmark child issue, not this seam.

## LiteLLM Integration

The framework uses **LiteLLM** as the provider abstraction layer:

- Unified API across <!--RS:providers_via_litellm-->90+<!--/RS--> providers
- Built-in cost tracking
- Automatic retries and fallbacks
- Load balancing across providers
- Chat completions-compatible interface (all providers normalised)
- **Model database**: `litellm.model_cost` provides pricing and context window data for all known models. Used at provider creation to dynamically populate model lists with up-to-date metadata. Provider-specific version filters (for example, a newer generation filter applied per provider) exclude older generations. Deduplicates dated model variants (e.g. prefers `example-large-002` over `example-large-002-20260205`). Falls back to preset `default_models` when no models are found in the database.

## Provider Management

Providers can be managed at runtime through the API without restarting:

- **CRUD**: `POST /api/v1/providers` (create), `PUT /api/v1/providers/{name}` (update), `DELETE /api/v1/providers/{name}` (delete)
- **Connection test**: `POST /api/v1/providers/{name}/test` -- sends a minimal probe and reports latency
- **Model discovery**: `POST /api/v1/providers/{name}/discover-models`
  - Queries the provider endpoint for available models (Ollama `/api/tags`, standard `/models`) and updates the provider config.
  - Accepts an optional `preset_hint` query parameter (`?preset_hint={preset_name}`) that guides endpoint selection (Ollama vs standard API path). The `preset_hint` is no longer used for SSRF trust decisions.
  - Auto-triggered on preset creation for no-auth providers with empty model lists.
  - SSRF trust is determined by a dynamic `host:port` allowlist (`ProviderDiscoveryPolicy`), seeded from preset `candidate_urls` at startup and auto-updated on provider create/update/delete. Trusted URLs bypass SSRF validation; untrusted URLs go through full private-IP/DNS-rebinding checks. Bypasses are logged at WARNING level (`PROVIDER_DISCOVERY_SSRF_BYPASSED`).
- **Discovery allowlist**: `GET /api/v1/providers/discovery-policy` (read), `POST /api/v1/providers/discovery-policy/entries` (add entry), `POST /api/v1/providers/discovery-policy/remove-entry` (remove entry); manage the dynamic SSRF allowlist of trusted `host:port` pairs for provider discovery. Persisted in the settings system (DB > env > code).
- **Presets**: `GET /api/v1/providers/presets` lists built-in cloud and local provider templates as a discriminated union (`kind: "cloud" | "local"`). Presets ship in **two tiers**, distinguished by an `is_featured: bool` field on the base shape:
  - **Featured** (hand-curated, branded): a curated set of cloud and local entries, each carrying a logo, vetted description, and -- where useful -- a `default_models` fallback list used when `litellm.model_cost` returns no entries. Listed first in the response and rendered in the wizard's primary grid. The current featured roster lives in `_FEATURED_PRESETS` in `src/synthorg/providers/presets.py`.
    - **Cloud** (`CloudPreset`): hosted LLM APIs. Carries `supported_auth_types` (e.g. `["api_key"]`, `["api_key", "subscription"]`) and a fallback `default_models` list. No `candidate_urls` (cloud endpoints are known statically; nothing to probe).
    - **Local** (`LocalPreset`): self-hosted servers (LM Studio, Ollama, vLLM). Carries `candidate_urls` for auto-detection and the local-management capability flags `supports_model_pull` / `supports_model_delete` / `supports_model_config` used by the UI to gate model lifecycle controls. Local presets may declare `candidate_urls=()` to opt out of auto-detection (vLLM uses this to dodge a port-8000 collision with the SynthOrg backend).
  - **Soft** (auto-derived from `litellm.model_cost`): one `CloudPreset` per chat-capable LiteLLM namespace not already covered by a featured preset and not denied by `_LITELLM_NAMESPACE_DENYLIST` / `_LITELLM_NAMESPACE_DENY_PREFIXES`. Soft presets default to `auth_type=api_key`, no logo (Lucide `Server` fallback in the picker), and a generic description. They surface every chat-capable LiteLLM provider out of the box without requiring a code change per release. Rendered in a collapsible "More providers via LiteLLM" section below the featured grid.
  - The `requires_base_url` flag is on both kinds (`true` for Azure on the cloud side; `true` for every local preset).
  - `POST /api/v1/providers/from-preset` creates a provider from any preset (featured or soft).
  - See [docs/guides/adding-a-provider.md](../guides/adding-a-provider.md) for the full add-a-provider workflow.
- **Preset auto-probe (batch)**: `POST /api/v1/providers/probe-local` -- probes every `LocalPreset` with non-empty `candidate_urls` in parallel (server-side `asyncio.TaskGroup`) using a 5-second timeout per URL and one rate-limit slot per call. Returns `{ results: { <preset_name>: ProbePresetResponse }, errors: { <preset_name>: <message> } }`. Used by the setup wizard and the Settings → Providers page on mount and on user-triggered re-scan. Per-preset failures land in `errors` without aborting the batch (cloud presets and vLLM are excluded by construction). SSRF validation is intentionally skipped because only hardcoded preset URLs are probed, never user input. The legacy single-preset `POST /api/v1/providers/probe-preset` endpoint has been removed; no replacement is offered for one-off single probes (the batch endpoint covers every wizard / settings call site).
- **Hot-reload**: On mutation, `ProviderManagementService` rebuilds `ProviderRegistry` + `ModelRouter` and atomically swaps them in `AppState` -- no downtime
- **Auth types**: `api_key` (default), `subscription` (token-based auth for provider subscription plans, passed to LiteLLM as `api_key`, requires ToS acceptance), `oauth` (stores credentials, MVP uses pre-fetched token), `custom_header`, `none` (local providers)
- **Routing key**: Optional `litellm_provider` field decouples the provider display name from LiteLLM routing (e.g. a provider named "my-claude" can route to `anthropic` via `litellm_provider: anthropic`). Falls back to provider name when unset.
- **Credential safety**: Secrets are Fernet-encrypted at rest via the `providers.configs` sensitive setting; API responses use `ProviderResponse` DTO that strips all secrets and provides `has_api_key`/`has_oauth_credentials`/`has_custom_header`/`has_subscription_token` boolean indicators
- **Health**: `GET /api/v1/providers/{name}/health` -- returns health status (up/degraded/down/unknown derived from 24h call count and error rate; unknown when no calls recorded), average response time, error rate percentage, call count, total tokens, and total cost. In-memory tracking via `ProviderHealthTracker` (concurrency-safe, append-only with periodic pruning). Token/cost totals are enriched from `CostTracker` at query time
- **Health probing**: `ProviderHealthProber` background service pings providers with `base_url` (local/self-hosted) every 30 minutes using lightweight HTTP requests (no model loading). Ollama: pings root URL; standard providers: `GET /models`. Skips providers with recent real API traffic. Results are recorded in `ProviderHealthTracker`. Cloud providers without `base_url` rely on real call outcomes for health status
- **Model capabilities**: `GET /api/v1/providers/{name}/models` returns `ProviderModelResponse` DTOs enriched with runtime capability flags (`supports_tools`, `supports_vision`, `supports_streaming`) from the driver layer's `ModelCapabilities`. Falls back to defaults when driver is unavailable. The controller issues a single call per provider via `CompletionProvider.batch_get_capabilities(models)` -- one controller-side dispatch instead of one per model. The default `BaseCompletionProvider.batch_get_capabilities` implementation still fans out per model under the hood via `asyncio.TaskGroup` with per-model exception suppression (failures degrade to `None` entries via `PROVIDER_BATCH_CAPABILITIES_PARTIAL` warnings; `MemoryError`/`RecursionError` propagate); only specific driver overrides can collapse upstream I/O. The `LiteLLMDriver` overrides with a tight in-process loop over the static preset catalog, so every list-models request incurs zero network I/O regardless of catalog size.
- **Local model management**: Providers with `supports_model_pull`/`supports_model_delete`/`supports_model_config` capability flags expose model lifecycle operations. `POST /api/v1/providers/{name}/models/pull` streams download progress via SSE (Ollama `/api/pull`). `DELETE /api/v1/providers/{name}/models/{model_id}` removes models. `PUT /api/v1/providers/{name}/models/{model_id}/config` sets per-model launch parameters (`LocalModelParams`: `num_ctx`, `num_gpu_layers`, `num_threads`, `num_batch`, `repeat_penalty`). Currently implemented for Ollama; LM Studio support deferred (unstable API).
- **Manual model add**: `POST /api/v1/providers/{name}/models` adds a single `ModelSpec` to the persisted config. Bypasses provider discovery for cases where the model isn't in `litellm.model_cost`. Rejects duplicates within the provider with HTTP 409. Audited.
- **Bulk model sync**: `POST /api/v1/providers/{name}/models/sync` re-runs discovery + pricing enrichment and (when `replace_existing=true`) replaces the persisted model list. Returns `SyncModelsResponse` with `added` / `removed` / `updated` model id lists plus the post-sync model set. Audited.
- **Rate-limit overrides**: `GET /api/v1/providers/{name}/rate-limits` returns the effective `RateLimiterConfig`; `PATCH /api/v1/providers/{name}/rate-limits` applies a partial update (any subset of `requests_per_minute`, `concurrent_requests`). Mutations hot-reload via `ProviderManagementService` and write an audit row. Empty patches are rejected. Tokens-per-minute and requests-per-hour are not yet exposed by the DTOs; the underlying `RateLimiterConfig` carries those fields but the `PATCH` surface intentionally narrows to the two operator-actionable knobs.
- **Credential rotation**: `POST /api/v1/providers/{name}/credentials/rotate` accepts a discriminated-union payload over `auth_type` (api_key / subscription / custom_header / oauth) and replaces the encrypted secret in `provider.configs` without downtime. Validates that the request's `auth_type` matches the provider's configured auth type. Audit payload carries only the masked credential (first 4 + last 4 chars; secrets of length 8 or shorter are masked entirely, since at exactly 8 chars the prefix and suffix windows already cover every byte) plus the actor; plaintext is never logged or persisted. Requires `provider_admin` guard.
- **Preset overrides**: `GET /api/v1/providers/presets/{preset_name}/override` returns the persisted override for one preset (or 404 if absent); `PATCH /api/v1/providers/presets/{preset_name}/override` upserts an override; `DELETE /api/v1/providers/presets/{preset_name}/override` removes it. Overrides apply globally; subsequent `from-preset` creations see the merged preset. Validation rejects infeasible combinations (e.g. `base_url` on a local preset, `candidate_urls` on a cloud preset). Audited.
- **Audit log**: `GET /api/v1/providers/{name}/audit?cursor=...&limit=...` returns the mutation history for one provider, newest first, keyset-paginated on the integer `id` column. Append-only; the only mutating operation is the retention sweeper `purge_before_id`. Every provider mutation (create / update / delete / model add / model remove / model config edit / bulk model sync / credential rotate / rate-limit edit / preset override edit) writes one row through `ProviderAuditService.record(...)`; audit failures never propagate out of a mutation (the persisted change is already committed by the time we reach the audit write).

## Model Routing Strategy

Model routing determines which LLM handles a given request. Six strategies are available,
selectable via configuration:

| Strategy | Behaviour |
|----------|----------|
| `manual` | Resolve an explicit model override; fails if not set |
| `role_based` | Match agent seniority level to routing rules, then catalog default |
| `cost_aware` | Match task-type rules, then pick cheapest model within budget |
| `fastest` | Match task-type rules, then pick fastest model (by `estimated_latency_ms`) within budget; falls back to cheapest when no latency data is available |
| `smart` | Priority cascade: override > task-type > role > seniority > cheapest > fallback chain |

```yaml
routing:
  strategy: "smart"              # smart, fastest, role_based, cost_aware, manual
  rules:
    - role_level: "C-Suite"
      preferred_model: "large"
      fallback: "medium"
    - role_level: "Senior"
      preferred_model: "medium"
      fallback: "small"
    - role_level: "Junior"
      preferred_model: "small"
      fallback: "local-coder"
    - task_type: "code_review"
      preferred_model: "medium"
    - task_type: "documentation"
      preferred_model: "small"
    - task_type: "architecture"
      preferred_model: "large"
  fallback_chain:
    - "example-provider"
    - "openrouter"
    - "ollama"
```

### Stakes-aware routing (orthogonal layer)

Model routing above selects *which provider/model* serves a request. **Stakes-aware
routing** is a separate, pluggable layer that re-tiers that selection based on how
consequential the work is. Each task (and subtask) carries a `stakes` level
(`low` / `normal` / `high` / `critical`), assessed by the `StakesAssessor`. The
`StakesRoutingStrategy` then picks the cheapest model tier whose benchmark score
clears the per-stakes quality floor, bumps one tier when coordination metrics are
unhealthy, and marks high/critical work for the red-team gate. High/critical work
is never routed below the agent's configured tier; low/normal work may drop to a
cheaper tier (still clearing the floor) to save cost. It is config-selectable via
`stakes_routing.strategy` (`stakes_aware` default, `flat` to opt out) and applied in
the engine *before* the budget auto-downgrade, so a hard budget ceiling still wins
over a stakes upgrade. See [Pluggable Subsystems](../reference/pluggable-subsystems.md).

### Multi-Provider Model Resolution

When multiple providers register the same model ID or alias, the `ModelResolver`
stores all variants as a candidate tuple rather than raising a collision error.
At resolution time, a `ModelCandidateSelector` picks the best candidate from the
tuple.

Two built-in selectors are provided:

| Selector | Behaviour |
|----------|----------|
| `QuotaAwareSelector` (default) | Prefer providers with available quota, then cheapest among those; falls back to cheapest overall when all providers are exhausted |
| `CheapestSelector` | Always pick the cheapest candidate by total cost per 1k tokens, ignoring quota state |

The selector is injected into `ModelResolver` (and transitively into `ModelRouter`)
at construction time.  `QuotaAwareSelector` is constructed with a snapshot from
`QuotaTracker.peek_quota_available()`, which returns a synchronous `dict[str, bool]`
of per-provider quota availability.

All routing strategies (`smart`, `cost_aware`, `fastest`, etc.) and the fallback chain
automatically use the injected selector when resolving model references, so multi-provider
selection is transparent to the strategy layer.

---

## See Also

- [Budget & Cost Management](budget.md) -- token metering, cost tracking, CFO optimisation, quota degradation
- [Tools](tools.md) -- tool categories, sandboxing, MCP integration
- [Design Overview](index.md) -- full index
