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

Every successful `provider.complete()` call attributes a `CostRecord` to the agent and task that originated the work. Attribution flows through a `ContextVar` middleware rather than through per-call kwargs, which keeps the provider interface uniform across cloud APIs, OpenRouter, Ollama, and custom adapters.

- **Scope contract**: callers wrap a `provider.complete()` invocation in `cost_recording_scope(cost_tracker, agent_id, task_id, project_id, call_category, currency)` from `synthorg.providers.cost_recording`. The scope is an `@asynccontextmanager` that sets a per-`asyncio.Task` `ContextVar`, yields, and resets on exit. Nested scopes shadow the outer one and are restored on exit; concurrent tasks see independent scopes.
- **Chokepoint**: `BaseCompletionProvider.complete()` reads the scope's context after a successful response, builds a `CostRecord` from `result.usage` + `result.provider_metadata` (`_synthorg_latency_ms`, `_synthorg_cache_hit`, `_synthorg_retry_count`, `_synthorg_retry_reason`) + `result.finish_reason`, and submits it via `cost_tracker.record(record)`. Calls outside any scope (probes, model discovery, tests) are no-ops.
- **Skip rule**: usage with both zero tokens and zero cost is skipped (matches the engine post-execution recorder). Free-tier providers with non-zero tokens still record.
- **Failure isolation**: any exception from `cost_tracker.record(...)` other than `MemoryError` / `RecursionError` is logged at WARNING (`PROVIDER_COST_FAILED`) and swallowed -- the user-visible provider response never depends on recording success.
- **Engine path**: the engine loop deliberately does NOT open a scope around its turn-level `provider.complete()` call. The post-execution `record_execution_costs(...)` recorder remains authoritative for engine turns because it accumulates per-turn metadata (turn number, retry counts, tool-response tokens for PTE) that the chokepoint cannot see synchronously. The chokepoint reads `None` and is a no-op for engine calls -- no double-counting.
- **Streaming gap**: `provider.stream()` does not fire the chokepoint. Streaming responses surface usage as a terminal `StreamEventType.USAGE` chunk, which the chokepoint cannot inspect synchronously without consuming the iterator and conflating recording with the stream-consumption contract. All cost-attributable LLM call sites in SynthOrg use `complete()`; a future PR can extend recording to `stream()` by hooking the terminal usage chunk.
- **AST gate**: `scripts/check_provider_complete_chokepoint.py` (pre-push + CI) walks `src/synthorg/` for `Await(Call(Attribute(_, "complete")))` nodes on `BaseCompletionProvider` instances and asserts each call site is either in an explicit allowlist (chokepoint itself, engine loop helpers, connection probes, health prober, registry docstring example) or has a `cost_recording_scope` opened in the same function.

This pattern mirrors `synthorg.observability.correlation.correlation_scope`, which is the established codebase precedent for cross-cutting per-call context bindings (`request_id` / `task_id` / `agent_id`).

## LiteLLM Integration

The framework uses **LiteLLM** as the provider abstraction layer:

- Unified API across 100+ providers
- Built-in cost tracking
- Automatic retries and fallbacks
- Load balancing across providers
- Chat completions-compatible interface (all providers normalized)
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
- **Discovery allowlist**: `GET /api/v1/providers/discovery-policy` (read), `POST /api/v1/providers/discovery-policy/entries` (add entry), `POST /api/v1/providers/discovery-policy/remove-entry` (remove entry) -- manage the dynamic SSRF allowlist of trusted `host:port` pairs for provider discovery. Persisted in the settings system (DB > env > YAML > code).
- **Presets**: `GET /api/v1/providers/presets` lists built-in cloud and local provider templates as a discriminated union (`kind: "cloud" | "local"`) of 12 presets:
  - **Cloud** (`CloudPreset`, 9 entries): Anthropic, Azure OpenAI, DeepSeek, Google AI Studio (Gemini), Groq, Mistral AI, Ollama Cloud, OpenAI, OpenRouter. Carries `supported_auth_types` (e.g. `["api_key"]`, `["api_key", "subscription"]`) and a fallback `default_models` list used when `litellm.model_cost` returns no entries. No `candidate_urls` (cloud endpoints are known statically; nothing to probe).
  - **Local** (`LocalPreset`, 3 entries): LM Studio, Ollama, vLLM. Carries `candidate_urls` for auto-detection and the local-management capability flags `supports_model_pull` / `supports_model_delete` / `supports_model_config` used by the UI to gate model lifecycle controls. vLLM's `candidate_urls` is intentionally empty (port 8000 collision risk with the SynthOrg backend) -- it cannot be auto-detected and is configured manually only.
  - The `requires_base_url` flag is on both kinds (`true` for Azure on the cloud side; `true` for every local preset).
  - `POST /api/v1/providers/from-preset` creates a provider from any preset.
- **Preset auto-probe (batch)**: `POST /api/v1/providers/probe-local` -- probes every `LocalPreset` with non-empty `candidate_urls` in parallel (server-side `asyncio.TaskGroup`) using a 5-second timeout per URL and one rate-limit slot per call. Returns `{ results: { <preset_name>: ProbePresetResponse }, errors: { <preset_name>: <message> } }`. Used by the setup wizard and the Settings → Providers page on mount and on user-triggered re-scan. Per-preset failures land in `errors` without aborting the batch (cloud presets and vLLM are excluded by construction). SSRF validation is intentionally skipped because only hardcoded preset URLs are probed, never user input. The legacy single-preset `POST /api/v1/providers/probe-preset` endpoint has been removed; no replacement is offered for one-off single probes (the batch endpoint covers every wizard / settings call site).
- **Hot-reload**: On mutation, `ProviderManagementService` rebuilds `ProviderRegistry` + `ModelRouter` and atomically swaps them in `AppState` -- no downtime
- **Auth types**: `api_key` (default), `subscription` (token-based auth for provider subscription plans, passed to LiteLLM as `api_key`, requires ToS acceptance), `oauth` (stores credentials, MVP uses pre-fetched token), `custom_header`, `none` (local providers)
- **Routing key**: Optional `litellm_provider` field decouples the provider display name from LiteLLM routing (e.g. a provider named "my-claude" can route to `anthropic` via `litellm_provider: anthropic`). Falls back to provider name when unset.
- **Credential safety**: Secrets are Fernet-encrypted at rest via the `providers.configs` sensitive setting; API responses use `ProviderResponse` DTO that strips all secrets and provides `has_api_key`/`has_oauth_credentials`/`has_custom_header`/`has_subscription_token` boolean indicators
- **Health**: `GET /api/v1/providers/{name}/health` -- returns health status (up/degraded/down/unknown derived from 24h call count and error rate; unknown when no calls recorded), average response time, error rate percentage, call count, total tokens, and total cost. In-memory tracking via `ProviderHealthTracker` (concurrency-safe, append-only with periodic pruning). Token/cost totals are enriched from `CostTracker` at query time
- **Health probing**: `ProviderHealthProber` background service pings providers with `base_url` (local/self-hosted) every 30 minutes using lightweight HTTP requests (no model loading). Ollama: pings root URL; standard providers: `GET /models`. Skips providers with recent real API traffic. Results are recorded in `ProviderHealthTracker`. Cloud providers without `base_url` rely on real call outcomes for health status
- **Model capabilities**: `GET /api/v1/providers/{name}/models` returns `ProviderModelResponse` DTOs enriched with runtime capability flags (`supports_tools`, `supports_vision`, `supports_streaming`) from the driver layer's `ModelCapabilities`. Falls back to defaults when driver is unavailable. The controller issues a single call per provider via `CompletionProvider.batch_get_capabilities(models)` -- one controller-side dispatch instead of one per model. The default `BaseCompletionProvider.batch_get_capabilities` implementation still fans out per model under the hood via `asyncio.TaskGroup` with per-model exception suppression (failures degrade to `None` entries via `PROVIDER_BATCH_CAPABILITIES_PARTIAL` warnings; `MemoryError`/`RecursionError` propagate); only specific driver overrides can collapse upstream I/O. The `LiteLLMDriver` overrides with a tight in-process loop over the static preset catalog, so every list-models request incurs zero network I/O regardless of catalog size.
- **Local model management**: Providers with `supports_model_pull`/`supports_model_delete`/`supports_model_config` capability flags expose model lifecycle operations. `POST /api/v1/providers/{name}/models/pull` streams download progress via SSE (Ollama `/api/pull`). `DELETE /api/v1/providers/{name}/models/{model_id}` removes models. `PUT /api/v1/providers/{name}/models/{model_id}/config` sets per-model launch parameters (`LocalModelParams`: `num_ctx`, `num_gpu_layers`, `num_threads`, `num_batch`, `repeat_penalty`). Currently implemented for Ollama; LM Studio support deferred (unstable API).

## Model Routing Strategy

Model routing determines which LLM handles a given request. Six strategies are available,
selectable via configuration:

| Strategy | Behavior |
|----------|----------|
| `manual` | Resolve an explicit model override; fails if not set |
| `role_based` | Match agent seniority level to routing rules, then catalog default |
| `cost_aware` | Match task-type rules, then pick cheapest model within budget |
| `cheapest` | Alias for `cost_aware` |
| `fastest` | Match task-type rules, then pick fastest model (by `estimated_latency_ms`) within budget; falls back to cheapest when no latency data is available |
| `smart` | Priority cascade: override > task-type > role > seniority > cheapest > fallback chain |

```yaml
routing:
  strategy: "smart"              # smart, cheapest, fastest, role_based, cost_aware, manual
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

### Multi-Provider Model Resolution

When multiple providers register the same model ID or alias, the `ModelResolver`
stores all variants as a candidate tuple rather than raising a collision error.
At resolution time, a `ModelCandidateSelector` picks the best candidate from the
tuple.

Two built-in selectors are provided:

| Selector | Behavior |
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

- [Budget & Cost Management](budget.md) -- token metering, cost tracking, CFO optimization, quota degradation
- [Tools](tools.md) -- tool categories, sandboxing, MCP integration
- [Design Overview](index.md) -- full index
