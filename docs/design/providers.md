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
        connection_name: "provider-example-provider"  # catalog connection holding the secret (api_key / custom_header auth)
        # subscription_token: "..."    # subscription token (subscription auth only; passed to LiteLLM as api_key; sensitive -- use env vars or secret management)
        # tos_accepted_at: "..."       # timestamp when subscription ToS was accepted
        models:                        # example entries -- real list loaded from provider
          - id: "example-expert-001"
            alias: "expert"
            cost_per_1k_input: 0.015   # illustrative, verify at implementation time
            cost_per_1k_output: 0.075
            max_context: 200000
            estimated_latency_ms: 1500 # optional, used by fastest strategy
          - id: "example-capable-001"
            alias: "capable"
            cost_per_1k_input: 0.003
            cost_per_1k_output: 0.015
            max_context: 200000
            estimated_latency_ms: 500
          - id: "example-basic-001"
            alias: "basic"
            cost_per_1k_input: 0.0008
            cost_per_1k_output: 0.004
            max_context: 200000
            estimated_latency_ms: 200
          - id: "example-image-001"
            alias: "image"
            cost_per_image: 0.04       # per-image billing for image-output models
            max_context: 1             # nominal; image models are not token-metered
            metadata:
              supports_image_generation: true

      openrouter:
        auth_type: api_key           # api_key | oauth | custom_header | subscription | none
        connection_name: "provider-openrouter"  # catalog connection holding the secret
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
        keep_alive: "5m"               # ollama-only: how long to keep a model
                                       # loaded after a request ("0" = unload
                                       # now, "-1" = keep forever; omit to use
                                       # ollama's own OLLAMA_KEEP_ALIVE default)
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

    **Catalog-only credentials.** `ProviderConfig` no longer carries an
    embedded `api_key:`. Secrets for the `api_key` and `custom_header` auth
    types live in the connection catalog (Fernet-encrypted at rest); the
    provider config references the catalog entry by `connection_name`, and the
    resolver reads the secret from there. A config that sets an `api_key` /
    `custom_header` auth type without a `connection_name` is rejected at
    validation time.

    **Operator migration.** Installs that previously persisted an embedded
    `api_key` are upgraded automatically: a one-time, idempotent boot hook runs
    after persistence connects (before the normal provider parse), reads each
    stored config through a transitional schema that tolerates the old
    `api_key`, mints a catalog connection (`provider-<name>`) for the secret,
    and re-persists the config on `connection_name`. The boot hook never logs
    the key. No operator action is required; the upgrade is transparent on the
    first start after the change.

### Reading the persisted blob

The stored `providers.configs` value is a map of independent connections, so
`config/provider_configs_read.py` reads it as one: an entry the current schema
will not accept costs that entry, never the set. That holds for an entry that is
not a usable mapping at all (a `null` or a string from a partial write) and for
a blank provider name, which are rejected by name in the same pass rather than
by the container's own type, since a type that judged entry shape would put
every entry back inside one validation.

The result carries a status, because "no providers" and "no readable providers"
are opposite conditions and only one of them is actionable:

| Status | Meaning | Boot behaviour |
|--------|---------|----------------|
| `OK` | Every entry read. An empty map here is a genuinely unconfigured deployment. | Registry built, or first-run empty company |
| `PARTIAL` | Some entries read. | Registry built from the survivors; every rejected entry logged and notified |
| `UNREADABLE` | Nothing usable, including an unknown `schema_version` (which a rollback to an earlier build reaches). | `ProviderConfigUnreadableError`, never the empty-company path |

The version check is why the distinction is not only about stale data: an
operator rolling back after a schema bump has a perfectly good config that this
build cannot read, and telling them their company is empty would be a lie about
data that is still there.

Nothing downstream is allowed to turn an absent registry into an outage.
`resolve_ref_provider` returns `None` when no registry is configured, exactly as
it does for an unregistered provider, so a feature bound to a model that cannot
be resolved is left unwired and the API still serves. The two callers of the
reload differ deliberately: `/setup/complete` propagates the raise to the
operator waiting on the request, while boot
(`reload_persisted_provider_registry_for_boot`) serves with no providers and
logs at ERROR, because refusing to start would take away the dashboard the
configuration gets corrected in.

An operator is told three ways, because each alone has a hole. Every rejected
entry is logged at ERROR; one notification per read goes to whatever sinks are
configured (ERROR for `UNREADABLE`, WARNING for `PARTIAL`); and the outcome is
recorded on the providers slice and served by
`GET /api/v1/providers/config-diagnostics`, which is the only one of the three
that survives the restart and needs no sink configured. A coercion is logged but
never notified: the setting is inert, and a notification that re-fires on every
restart for a condition that never changes trains an operator to dismiss the
channel.

**Reasons never quote the config.** A pydantic validation error echoes the input
it rejected, and a provider entry holds credentials, so `rejected[].reason` and
`detail` are built from the structured errors with the input excluded
(`observability/validation_redaction.py::describe_without_input`), not scrubbed
after the fact. Scrubbing cannot serve here: pydantic truncates the middle of a
long value, removing the `"key":` framing a pattern matcher keys on, and a
scrubber has to recognise a secret to redact one, while this product privileges
no vendor and an operator's key may look like nothing in particular.

Excluding the input is not on its own enough, because two of the fields that
remain can still carry it. `msg` is rendered when the error is raised, so a
validator that interpolated the value it rejected has already put it in the
string; every error is therefore reported by its type slug, never its message,
which is a rule rather than a list of the constructs known to do it. And `loc`
is schema-derived except for `extra_forbidden`, whose final component is a key
the blob supplied, so that one component is masked while the path above it still
names the entry.

## Cost Recording

Every successful **scoped** `provider.complete()` call attributes a `CostRecord` to the agent and task that originated the work. Attribution flows through a `ContextVar` middleware rather than through per-call kwargs, which keeps the provider interface uniform across cloud APIs, OpenRouter, Ollama, and custom adapters. Calls made outside any `cost_recording_scope` -- infrastructure probes, model discovery, the engine turn loop, tests -- read `None` for the active context and are intentionally **not** attributed: the engine's post-execution recorder owns engine turns, and probe / discovery traffic is not user spend.

- **Scope contract**: callers wrap a `provider.complete()` invocation in `cost_recording_scope(cost_tracker, agent_id, task_id, project_id, call_category, currency)` from `synthorg.providers.cost_recording`. The scope is an `@asynccontextmanager` that captures the current `ContextVar` value, sets the new context, yields, and restores the captured value on exit. It restores by plain `set(previous)` rather than `Token.reset` on purpose: a streaming or SSE body can drive the enter and the exit in different `asyncio` contexts, and `Token.reset` raises `ValueError` when the token is reset in a context other than the one that created it, whereas a plain set is always context-safe. Nested scopes shadow the outer one and are restored on exit; concurrent tasks see independent scopes.
- **Chokepoint**: `BaseCompletionProvider.complete()` reads the scope's context after a successful response, builds a `CostRecord` from `result.usage` + `result.provider_metadata` (`_synthorg_latency_ms`, `_synthorg_cache_hit`, `_synthorg_retry_count`, `_synthorg_retry_reason`) + `result.finish_reason`, and submits it via `cost_tracker.record(record)`. Calls outside any scope (probes, model discovery, tests) are no-ops.
- **Skip rule**: usage with both zero tokens and zero cost is skipped (matches the engine post-execution recorder). Free-tier providers with non-zero tokens still record.
- **Failure isolation**: any exception from `cost_tracker.record(...)` other than `MemoryError` / `RecursionError` is logged at WARNING (`PROVIDER_COST_FAILED`) and swallowed -- the user-visible provider response never depends on recording success.
- **Engine path**: the engine loop deliberately does NOT open a scope around its turn-level `provider.complete()` call. The post-execution `record_execution_costs(...)` recorder remains authoritative for engine turns because it accumulates per-turn metadata (turn number, retry counts, tool-response tokens for PTE) that the chokepoint cannot see synchronously. The chokepoint reads `None` and is a no-op for engine calls -- no double-counting.
- **Streaming**: `provider.stream()` also records cost, via a lazy pass-through wrapper (`BaseCompletionProvider._cost_recording_stream`, helper `record_stream_cost_if_in_scope`). The wrapper forwards every chunk unchanged and captures the terminal `StreamEventType.USAGE` chunk; once the stream is fully drained it emits a synthetic `CostRecord` through the same `record_cost_if_in_scope` chokepoint `complete()` uses. Because attribution happens on drain, an early `aclose()` or `break` that abandons the iterator before the USAGE chunk skips the record (the partial stream was not fully consumed).
- **AST gate**: `scripts/check_provider_complete_chokepoint.py` (pre-push + CI) walks `src/synthorg/` for `Await(Call(Attribute(_, "complete")))` nodes on `BaseCompletionProvider` instances and asserts each call site is either in an explicit allowlist (chokepoint itself, engine loop helpers, connection probes, health prober, registry docstring example) or has a `cost_recording_scope` opened in the same function.

This pattern mirrors `synthorg.observability.correlation.correlation_scope`, which is the established codebase precedent for cross-cutting per-call context bindings (`request_id` / `task_id` / `agent_id`).

### Model pricing (real cost, not $0.00)

A `CostRecord` is only meaningful when the model carries real per-token pricing.
Live-discovered models otherwise keep `cost_per_1k_*` at the `0.0` default forever,
recording `$0.00` for every call. Two back-fills close that gap, operator override
always winning:

- **litellm back-fill**: when enrichment finds a model with zero operator cost, it
  reads `input_cost_per_token` / `output_cost_per_token` from litellm's model info
  (`extract_model_pricing`, converted to per-1k) and sets `cost_per_1k_input` /
  `cost_per_1k_output`. A non-zero operator cost is never overwritten.
- **register unmapped ids**: at registry build, `register_operator_model_pricing`
  syncs each litellm-driver provider's operator-supplied costs into
  `litellm.model_cost` via `litellm.register_model`, so `get_model_info` resolves
  ids litellm does not ship (e.g. a gateway's own chat model) and downstream cost
  math is consistent. It runs once per build, not per request.

`prompt_class_id` is legitimately `None` on a raw agent-execution turn: that path
opens no `cost_recording_scope` and has no registered system-prompt purpose (the
engine post-execution recorder owns it, by design). The `$0.00` symptom is fixed by
real pricing, not by fabricating a purpose. Calls that *do* carry a registered
purpose (the capability-classifier LLM call, judging, etc.) attribute
`prompt_class_id` normally.

## Cassette Record / Replay

Recorded-LLM **cassettes** make a company run deterministic and free to re-execute: record the exact provider responses of a run keyed by request, then replay them for byte-identical re-execution with zero real LLM calls. Like cost recording, this is a provider-layer concern, not per-driver.

- **Seam**: `CassetteCompletionProvider` (`src/synthorg/providers/cassette/`) wraps an inner driver and overrides the **public** `complete()` / `stream()` / `get_model_capabilities()` / `batch_get_capabilities()`. It deliberately overrides the public methods, not the `_do_*` hooks: `BaseCompletionProvider.complete` merges fresh `_synthorg_latency_ms` / `_synthorg_retry_count` into `provider_metadata` after `_do_complete`, so replaying through `_do_complete` would clobber the recorded metadata and break byte-identical replay. The three `_do_*` hooks are unreachable guards raising `CassetteInternalError`.
- **Decoration chokepoint**: `ProviderRegistry.from_config(..., cassette=...)` wraps every driver in one shared `CassetteSession` before the registry is frozen, so no consumer (engine, coordinator, judge, runtime builder) can bypass record/replay. In **replay** the inner driver is **not built at all** (no factory call), so a pure replay run constructs no real provider.
- **Keying**: SHA-256 over the canonical request `(method, provider, model, messages, tools, config)` via `synthorg.versioning.hashing.compute_content_hash`. Repeated identical requests within a run are disambiguated by a **per-task FIFO lane**: each distinct asyncio task is assigned a stable monotonic lane on its first provider call. Replay matching is `(request_hash, lane, seq)`. This is stable across record and replay iff the first-call order of distinct tasks is identical, which the deterministic simulation harness provides; a cassette miss / sequence exhaustion fails loudly (`CassetteReplayMissError` / `CassetteReplayExhaustedError`) and never falls through to a real provider.
- **Storage**: a single canonical JSON document (filesystem, no DB / no yoyo revision: this is test infrastructure). The session auto-persists after every recorded interaction (crash-safe), written atomically (temp file + rename). `cassette_format_version` gates incompatible formats with `CassetteFormatError`.
- **Redaction boundary (SEC-1)**: the replay key is hashed on the **raw** request, and the **response / stream / capabilities outcome is stored verbatim** because it *is* the byte-identical replay artefact. Redaction (pluggable `CassetteRedactor`; default `PatternRedactor` scrubs bearer tokens, `sk-` keys, AWS keys, PEM blocks, labelled secrets) applies **only to the human-readable `request_repr`**, which is never consulted for replay. Provider credentials never reach `complete()` (they live in driver config); the residual exposure is a model echoing a prompt secret into its own output, which is accepted and documented (cassettes are dev/test artefacts; default cassette runs use scripted/seeded providers).
- **Configuration**: `providers.cassette_mode` (`off` / `record` / `replay`) + `providers.cassette_path`, resolved once at the boot site via the Cat-2 bootstrap resolver (env > code default, `compose_set`: switching mid-process would leave a half-recorded transcript); `off` is a structural no-op.
- **Scope**: the record/replay seam is complete and independently validated under the live engine harness (a recorded multi-turn agent run replays byte-identically with zero real provider calls). Wiring the cassette into the golden-company benchmark suite is owned by the benchmark child issue, not this seam.

## LiteLLM Integration

The framework uses **LiteLLM** as the provider abstraction layer:

- Unified API across <!--RS:providers_via_litellm-->95+<!--/RS--> providers
- Built-in cost tracking
- Automatic retries and fallbacks
- Load balancing across providers
- Chat completions-compatible interface (all providers normalised)
- **Model database**: `litellm.model_cost` provides pricing and context window data for all known models. Used at provider creation to dynamically populate model lists with up-to-date metadata. At discovery each model is enriched with a `ModelMetadata` record (capability flags -- tools / vision / reasoning / embeddings / prompt caching, `max_output_tokens`, and a parsed `family` + sortable `generation`) which is persisted on `ProviderModelConfig` so the capability-aware matcher works offline afterwards. **Ollama bypasses this DB entirely**: it has no entry for locally-pulled models and would overwrite the real `/api/show` probe capabilities with all-False guesses, so `build_capabilities` (in `providers/drivers/litellm_capabilities.py`) forces `info = {}` for the ollama routing key and resolves capabilities from the persisted probe metadata instead. Provider-specific version filters (`MODEL_VERSION_FILTERS`, keyed by LiteLLM provider) exclude older generations; family/generation parsing is driven by `MODEL_FAMILY_RULES` with a generic fallback. Deduplicates dated model variants (e.g. prefers `example-expert-002` over `example-expert-002-20260205`). Falls back to preset `default_models` when no models are found in the database.

### Completion controls (reasoning, caching, streaming)

Three model-behaviour controls tune the LiteLLM call, each gated on a capability
so a model that does not support the feature is left untouched. Two are
`CompletionConfig` fields the driver maps onto the call (`reasoning_effort` and
the `prompt_caching` flag); streaming is a loop-level behaviour driven by a
setting plus the model's streaming capability, not a `CompletionConfig` field:

- **`reasoning_effort`** (`ReasoningEffort` enum: `minimal` / `low` / `medium` /
  `high`): mapped 1:1 to LiteLLM's `reasoning_effort` kwarg, emitted only when the
  resolved model advertises `supports_reasoning`. Stakes routing drives it through
  a per-stakes `StakesReasoning` policy (sibling to `StakesCapabilityFloor`): the
  routing decision's effort is folded into the run's `CompletionConfig` while the
  agent's `temperature` / `max_tokens` are preserved. The policy is validated
  non-decreasing across the stakes ladder, so low-stakes work never requests deeper
  reasoning than high-stakes work.
- **Prompt caching** (`providers.prompt_caching_enabled`, default on): when the
  model advertises `supports_prompt_caching`, `drivers/litellm_cache.py` rewrites
  the stable prefix (system block, tools block, and a rolling breakpoint before the
  live tail) into the content-block form carrying
  `cache_control: {type: ephemeral}` before the call, so a multi-turn run stops
  re-billing the unchanged prefix at full input-token cost. Non-caching models
  (Ollama, unknown) default the flag false and are never rewritten.
- **Streaming work loop** (`engine.work_loop_streaming_enabled`, default on):
  when the model advertises `supports_streaming` the loops consume
  `provider.stream()` through one `run_provider_turn()` dispatcher, reassembling a
  `CompletionResponse` faithful to `complete()` (content, tool-call deltas, usage,
  and a `finish_reason` carried on the terminal `DONE` chunk) while polling
  cancellation and steering between chunks. See
  [Mid-Flight Steering](mid-flight-steering.md) for the mid-turn cancel /
  steer-interrupt semantics. The retry / rate-limit / cost chokepoints stay in
  `BaseCompletionProvider`; the loop falls back to `complete()` when streaming is
  off or unsupported.

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
    - **Cloud** (`CloudPreset`): hosted LLM APIs. Carries `supported_auth_types` (e.g. `["api_key"]`, `["api_key", "subscription"]`) and a fallback `default_models` list. No `candidate_urls` (cloud endpoints are known statically; nothing to probe). An OpenAI-compatible gateway whose live `/v1/models` is the source of truth sets `prefer_live_discovery: true` (with `auth_type=api_key`, enforced by a model validator): `from-preset` skips the static `litellm.model_cost` table (which would surface the wrong catalogue for a gateway) and runs an authenticated live discovery to populate the full catalogue. The Bearer key is sent only when the base URL still matches the preset's canonical `default_base_url`; a user-overridden host is never handed the key. A gateway that ships a curated `default_models` seed degrades to that seed when discovery fails (a transient blip need not fail the save); a **seedless** gateway has no fallback, so a failed discovery (after a bounded transient retry that honours `Retry-After`) surfaces the specific reason (bad key / rate limit / unreachable host) rather than persisting a provider with zero models. Ollama Cloud (`https://ollama.com/v1`, seeded) and Mammouth (`https://api.mammouth.ai/v1`, seedless) both use this path.
    - **Local** (`LocalPreset`): self-hosted servers (LM Studio, Ollama, vLLM). Carries `candidate_urls` for auto-detection and the local-management capability flags `supports_model_pull` / `supports_model_delete` / `supports_model_config` used by the UI to gate model lifecycle controls. Local presets may declare `candidate_urls=()` to opt out of auto-detection (vLLM uses this to dodge a port-8000 collision with the SynthOrg backend).
  - **Soft** (auto-derived from `litellm.model_cost`): one `CloudPreset` per chat-capable LiteLLM namespace not already covered by a featured preset and not denied by `_LITELLM_NAMESPACE_DENYLIST` / `_LITELLM_NAMESPACE_DENY_PREFIXES`. Soft presets default to `auth_type=api_key`, no logo (Lucide `Server` fallback in the picker), and a generic description. They surface every chat-capable LiteLLM provider out of the box without requiring a code change per release. Rendered in a collapsible "More providers via LiteLLM" section below the featured grid.
  - The `requires_base_url` flag is on both kinds (`true` for Azure on the cloud side; `true` for every local preset).
  - `POST /api/v1/providers/from-preset` creates a provider from any preset (featured or soft).
  - See [docs/guides/adding-a-provider.md](../guides/adding-a-provider.md) for the full add-a-provider workflow.
- **Preset auto-probe (batch)**: `POST /api/v1/providers/probe-local` -- probes every `LocalPreset` with non-empty `candidate_urls` in parallel (server-side `asyncio.TaskGroup`) using a 5-second timeout per URL and one rate-limit slot per call. Returns `{ results: { <preset_name>: ProbePresetResponse }, errors: { <preset_name>: <message> } }`. Used by the setup wizard and the Settings → Providers page on mount and on user-triggered re-scan. Per-preset failures land in `errors` without aborting the batch (cloud presets and vLLM are excluded by construction). SSRF validation is intentionally skipped because only hardcoded preset URLs are probed, never user input. The legacy single-preset `POST /api/v1/providers/probe-preset` endpoint has been removed; no replacement is offered for one-off single probes (the batch endpoint covers every wizard / settings call site).
- **Hot-reload**: On mutation, `ProviderManagementService` rebuilds `ProviderRegistry` + `ModelRouter` and atomically swaps both into `AppState` in a single field-level slice update -- no downtime, no partial swap. The persist-then-swap sequence is itself atomic with the DB write: a swap failure rolls the persisted `providers.configs` blob back to its prior value (re-serialised from the parsed snapshot, since the sensitive setting's stored blob is unrecoverable through the masked entry) and raises `ProviderPersistenceError` with an ERROR alert, so the database and the running registry never diverge. The validate / serialise / persist / swap stages each raise a distinct error (`ProviderValidationError` / `ProviderSerializationError` / `ProviderPersistenceError`) so the failing stage is unambiguous.
- **Auth types**: `api_key` (default), `subscription` (token-based auth for provider subscription plans, passed to LiteLLM as `api_key`, requires ToS acceptance), `oauth` (stores credentials, MVP uses pre-fetched token), `custom_header`, `none` (local providers)
- **Routing key**: Optional `litellm_provider` field decouples the provider display name from LiteLLM routing (e.g. a provider named "my-claude" can route to `anthropic` via `litellm_provider: anthropic`). Falls back to provider name when unset.
- **Credential safety**: Secrets are Fernet-encrypted at rest via the `providers.configs` sensitive setting; API responses use `ProviderResponse` DTO that strips all secrets and provides `has_api_key`/`has_oauth_credentials`/`has_custom_header`/`has_subscription_token` boolean indicators
- **Persisted-config envelope**: the `providers.configs` JSON value is wrapped in a versioned `ProvidersConfigEnvelope` (`{ "schema_version", "providers" }`). On read, the resolver validates the envelope and its `schema_version`, then each entry on its own; see [Reading the persisted blob](#reading-the-persisted-blob) above for what a rejected entry costs and how an unreadable blob is told apart from an unconfigured one. A one-time boot migration upgrades a pre-envelope bare provider dict into envelope form on the same pass that moves any embedded `api_key` into the connection catalog.
- **Health**: `GET /api/v1/providers/{name}/health` -- returns the health status (up/degraded/down/unknown), average response time, error rate percentage, call count, total tokens, and total cost. In-memory tracking via `ProviderHealthTracker` (concurrency-safe, append-only with periodic pruning). Token/cost totals are enriched from `CostTracker` at query time.
- **Liveness is not reliability**: "is this provider serving?" and "how has it behaved today?" are different questions on different timescales, and one number cannot answer both. `health_status` is derived from **liveness** alone: the newest `LIVENESS_SAMPLE_SIZE` outcomes, judged against the same 10% / 50% error rates. `error_rate_percent_24h` and `calls_last_24h` keep reporting the whole 24-hour window and feed the metrics panel. Sharing one 24-hour average between them meant a provider fixed a minute ago went on reporting `down`, because a day of failures outvoted every call since, and a clean day masked a provider that had just started failing.
- **A recheck is authoritative**: `POST /api/v1/providers/{name}/health/recheck` (and the all-providers sweep) marks a **liveness epoch** on the tracker before it calls, so the verdict is decided by what happens from that moment on. The cutoff is a point in time, not a claim on that one call: ordinary traffic and the periodic prober keep recording, and outcomes landing after the cutoff count too, deliberately, since a verdict that ignored every call but this one would report green while real requests were failing. Whether the past is still evidence is the operator's judgement, not an average's: they are the one who knows they restarted the endpoint or replaced the key, and without this the one control offered for exactly that moment could add a single sample to a losing sum and change nothing visible. No record is deleted, so the 24-hour reliability figures still report the outage. A recheck that finds the provider serving also triggers a reconcile pass (`retry_declined=True`), so subsystems that gave up on the provider (memory, whose embedder lives on one) are re-attempted immediately rather than at the next periodic sweep; one that finds it still down does not, because nothing has recovered for a dependent to activate on and the pass would re-probe every declined subsystem to reach the same answer. The sweep runs at most one pass, and only when some provider answered.
- **Reachability roll-up**: `/health` reports `providers` as `ok` / `degraded` / `down`, the worst verdict across every tracked provider. A provider reading `unknown` (nothing has called it yet) does not participate, so a fresh boot never claims trouble before the first call lands. Two further values are not verdicts about providers at all: the field is `unknown` when the read itself timed out or raised, and `null` when no provider health tracker is wired. Reporting a failed read as `down` would send an operator to check endpoints and credentials that may be serving perfectly. It is reported and never gates readiness: every replica reaches the same third-party endpoint, so draining on one would take down the dashboard an operator repoints it from. More than a boolean, which has to fold `degraded` into one side or the other and so renders a provider failing some calls identically to one failing none, or else identically to one that is down.
- **Health probing**: `ProviderHealthProber` background service pings providers with `base_url` (local/self-hosted) using lightweight HTTP requests (no model loading), on the `providers.health_probe_interval_seconds` cadence (default 300s, read live per cycle so an operator's change applies without a restart). Ollama: pings root URL; standard providers: `GET /models`. Skips providers with recent real API traffic. Results are recorded in `ProviderHealthTracker`. Cloud providers without `base_url` rely on real call outcomes for health status. A provider with no recorded calls reports `unknown`, so each cycle logs its provider and eligible counts (`PROVIDER_HEALTH_PROBER_CYCLE_COMPLETED`) and a skipped provider logs why (`base_url_required_but_missing` at WARNING for a preset that demands one, `no_base_url` at DEBUG otherwise): a sweep that probes nothing is never silent.
- **On-demand probing**: creating or re-pointing a provider probes it immediately, so a provider configured during setup does not sit `unknown` until the next periodic cycle up to a full interval later. `ProviderManagementService` calls `probe_provider(name)` through the `ProviderProbeRequester` protocol (`providers/probe_protocol.py`), which startup satisfies by handing the started prober back via `set_probe_requester` once both exist. The probe runs after the mutation lock is released and is failure-tolerant: the provider is already persisted, so a probe failure logs and leaves the status `unknown` rather than failing the mutation.
- **Model capabilities**: `GET /api/v1/providers/{name}/models` returns `ProviderModelResponse` DTOs enriched with runtime capability flags (`supports_tools`, `supports_vision`, `supports_streaming`, `supports_embeddings`, `supports_reasoning`) from the driver layer's `ModelCapabilities`. Embedding models are surfaced (so the UI tags them) and are excluded from chat-agent matching, since they produce vectors, not chat completions. Falls back to defaults when driver is unavailable. Each model also carries a `metadata_source` provenance flag (`litellm` / `preset` / `probe` / `unknown`) recording where its capability metadata came from; when it is `unknown` and no capability flags are set, the dashboard renders a muted "capabilities unverified" pill rather than implying the model has none. A provider-supplied context window (`max_input_tokens` from a live `/models` listing) is carried through as `max_context` when plausible, and dropped in favour of the safe default above a sanity ceiling (an untrusted gateway cannot inflate the window to skew model selection). The controller issues a single call per provider via `CompletionProvider.batch_get_capabilities(models)` -- one controller-side dispatch instead of one per model. The default `BaseCompletionProvider.batch_get_capabilities` implementation still fans out per model under the hood via `asyncio.TaskGroup` with per-model exception suppression (failures degrade to `None` entries via `PROVIDER_BATCH_CAPABILITIES_PARTIAL` warnings; `MemoryError`/`RecursionError` propagate); only specific driver overrides can collapse upstream I/O. The `LiteLLMDriver` overrides with a tight in-process loop over the static preset catalog, so every list-models request incurs zero network I/O regardless of catalog size.
- **Local model management**: Providers with `supports_model_pull`/`supports_model_delete`/`supports_model_config` capability flags expose model lifecycle operations. `POST /api/v1/providers/{name}/models/pull` streams download progress via SSE (Ollama `/api/pull`). `DELETE /api/v1/providers/{name}/models/{model_id}` removes models. `PUT /api/v1/providers/{name}/models/{model_id}/config` sets per-model launch parameters (`LocalModelParams`: `num_ctx`, `num_gpu_layers`, `num_threads`, `num_batch`, `repeat_penalty`). Implemented for Ollama; LM Studio support deferred (unstable API).
- **Manual model add**: `POST /api/v1/providers/{name}/models` adds a single `ModelSpec` to the persisted config. Bypasses provider discovery for cases where the model isn't in `litellm.model_cost`. Rejects duplicates within the provider with HTTP 409. Audited.
- **Bulk model sync**: `POST /api/v1/providers/{name}/models/sync` re-runs discovery + pricing + metadata enrichment and (when `replace_existing=true`) replaces the persisted model list. Returns `SyncModelsResponse` with `added` / `removed` / `updated` model id lists plus the post-sync model set. After persistence a failure-tolerant **model-presence probe** (`StaticPresenceProbe`, pluggable via the `ModelPresenceProbe` protocol) compares each persisted/baked id against the offline LiteLLM catalogue and logs `PROVIDER_MODEL_ABSENT` for any id no longer advertised (foundation for the staleness/refresh work); a probe failure never fails the already-persisted sync. Audited.
- **Rate-limit overrides**: `GET /api/v1/providers/{name}/rate-limits` returns the effective `RateLimiterConfig`; `PATCH /api/v1/providers/{name}/rate-limits` applies a partial update (any subset of `requests_per_minute`, `concurrent_requests`). Mutations hot-reload via `ProviderManagementService` and write an audit row. Empty patches are rejected. Tokens-per-minute and requests-per-hour are not yet exposed by the DTOs; the underlying `RateLimiterConfig` carries those fields but the `PATCH` surface intentionally narrows to the two operator-actionable knobs.
- **Credential rotation**: `POST /api/v1/providers/{name}/credentials/rotate` accepts a discriminated-union payload over `auth_type` (api_key / subscription / custom_header / oauth) and replaces the encrypted secret in `provider.configs` without downtime. Validates that the request's `auth_type` matches the provider's configured auth type. Audit payload carries only the masked credential (first 4 + last 4 chars; secrets of length 8 or shorter are masked entirely, since at exactly 8 chars the prefix and suffix windows already cover every byte) plus the actor; plaintext is never logged or persisted. Requires `provider_admin` guard.
- **Preset overrides**: `GET /api/v1/providers/presets/{preset_name}/override` returns the persisted override for one preset (or 404 if absent); `PATCH /api/v1/providers/presets/{preset_name}/override` upserts an override; `DELETE /api/v1/providers/presets/{preset_name}/override` removes it. Overrides apply globally; subsequent `from-preset` creations see the merged preset. Validation rejects infeasible combinations (e.g. `base_url` on a local preset, `candidate_urls` on a cloud preset). Audited.
- **Audit log**: `GET /api/v1/providers/{name}/audit?cursor=...&limit=...` returns the mutation history for one provider, newest first, keyset-paginated on the integer `id` column. Append-only; the only mutating operation is the retention sweeper `purge_before_id`. Every provider mutation (create / update / delete / model add / model remove / model config edit / bulk model sync / credential rotate / rate-limit edit / preset override edit) writes one row through `ProviderAuditService.record(...)`; audit failures never propagate out of a mutation (the persisted change is already committed by the time we reach the audit write).

## Model Refresh

The periodic model-refresh subsystem keeps the persisted model catalogue aligned
with what each provider actually advertises, and surfaces upgrade recommendations
when a newer in-family model appears. It is **off by default**; a normal boot skips
it entirely. Wiring (`wire_model_refresh`) is gated on
`providers.model_refresh_mode != off`, a built provider-management service, and a
connected persistence backend.

**Modes** (`RefreshMode`, the config discriminator):

| Mode | Behaviour |
|------|-----------|
| `off` | Disabled (safe default). Nothing scheduled. |
| `manual_only` | No cadence; only the explicit `POST /refresh` endpoint runs a cycle. |
| `detect_only` | Periodically probe providers and flag removed models stale; never persists new models or emits recommendations. |
| `reconcile_recommend` | Probe, persist refreshed metadata, flag removed models stale, and feed upgrade recommendations. |

**Settings** (namespace `providers`, DB > env > code): `model_refresh_mode`,
`model_refresh_interval_seconds` (default daily, clamped to 60s-7d), and
`model_refresh_auto_apply_within_family` (when set, strictly in-family upgrades are
auto-applied instead of parked for human approval). The scheduler re-reads the live
mode + auto-apply flag every tick and fails safe to `off` on any read error, so an
operator can change mode without a restart and a settings-backend hiccup never
silently runs a refresh.

**API** (`/api/v1/providers/model-refresh`, `require_write_access`):

- `GET /recommendations` -- list upgrade recommendations (filter by `status`).
- `POST /recommendations/{id}/approve` -- approve and reassign pinned agents.
- `POST /recommendations/{id}/reject` -- reject (no reassignment).
- `POST /refresh` -- run one reconcile+recommend cycle on demand (CEO/manager).
- `GET /status` -- current refresh mode, cadence, and auto-apply flag.

The recommendation store, scheduler, and service form a both-or-neither paired
invariant on `ModelRefreshStateSlice`; the controllers 503 when the store is unwired.
Recommendations only PROPOSE; human approval still gates apply unless a strictly
in-family upgrade matches the auto-apply flag.

**In-family selection** (`UpgradeRecommender`): models are grouped by
`(metadata.family, metadata.supports_embeddings)`, not by family alone. A family
label can span two incompatible classes -- an embedding model (vector output) and
a chat model are not drop-in replacements -- so grouping on the embedding flag
prevents a newer-generation chat model from being recommended as the upgrade for
an embedding model (or vice versa). Within a group, every model older than the
newest generation is a candidate; the recommendation targets the newest-generation
sibling with no capability regression (it must not drop a tool / vision / reasoning
capability the current model has). When several newest-generation candidates
qualify, the strongest is chosen by upgrade score (capability fit + context
headroom + generation delta, from the registered matcher weights), with model id
as a deterministic tie-break, so a larger / more capable variant is preferred over
an arbitrary alphabetical pick.

## Setup Model Assignment (cost + locality aware)

At org provisioning the template matcher (`templates/model_matcher.py`) assigns
each agent a concrete model across **all** configured providers. Selection is
driven by the demand a role declares (`priority` + `requires_*` mapped to a cost
tier), then domination pruning and family spread. Two provider-aware guards keep
the result sensible on a mixed local + cloud setup:

- **Prefer local when adequate** (`engine.matcher_prefer_local`, default on): when
  a locally-hosted model (loopback / private / localhost base URL) already sits in
  the adequate band for a role, it is chosen over a paid remote of equal fit before
  family spread applies (so a free local model wins even against a nominally
  stronger remote model that sits in the same adequate band). A role a free
  local model can serve never silently runs on a paid cloud model instead.
- **Cloud cost floor** (`engine.matcher_min_cloud_cost_tier`, default `2`): a
  remote provider is never auto-assigned a model whose *known* cost tier is below
  the floor, so a paid provider does not fill a role with a bottom-tier model when
  a stronger one exists. Local providers are exempt (free to run at any tier), and
  a remote model with no resolvable tier passes (optimistic); the floor relaxes if
  it would otherwise leave an agent unassigned.

Both are hot-reloadable (a change triggers a runtime-services rebuild via the
settings subscriber, no restart), so the defaults give a sensible allocation
with no operator input while remaining tunable per deployment.

**Tool calling is a floor, not a preference.** Every agent turn dispatches with
tool definitions attached, so a model that cannot call them can only emit prose
and fails any task that expects an artifact. `is_tool_capable` filters every
candidate, and an explicit reference does not exempt the pick from it: a
`family` or `model_pattern` ref pins the newest hard-filter *survivor*, and an
explicit `model_id` pin -- which does override the capability requirements the
role declared, since an operator naming a model has chosen it deliberately --
is still refused when the named model is *known* to lack tool calling. The
agent is left unassigned (a logged warning) rather than seeded onto a model
that cannot do its work. The rule stays optimistic throughout: an un-probed
model is admitted, and only a declared or runtime-proven incapacity excludes.

**Agent-eligible providers.** A provider carries `agent_eligible` (default
`true`). An `agent_eligible=false` provider stays fully usable for
explicitly-configured feature calls (the chat / judge / charter / narrative
models an operator sets), but contributes no models to the seeding pool and is
excluded from stakes routing, so no agent is ever *newly* seeded onto it or
routed to it. It does not immediately cut off existing traffic: an agent already
pinned to the provider keeps running on it because `resolve_for_pair` honours the
explicit `(provider, model)` binding, until that agent is reassigned. This lets an
operator stop new agents sourcing from a gateway (added deliberately, e.g. for a
specific feature model) without disrupting agents already bound to it. The flag
is a per-provider field on `ProviderConfig`, editable through provider CRUD.

**How the connection charges.** A provider carries `billing_model`: `per_token`,
`flat_rate` or `unknown` (the default). A preset declares it, `ProviderConfig` is
seeded from the preset at create time, and the operator can correct it afterwards,
because they know their own contract better than a shipped table does. The
connection's own declaration is the single owner: `CostTracker.record` stamps it onto
every cost row from a snapshot of the provider set, overwriting whatever the
dispatching path supplied, so a caller cannot make spend look measurable by asserting
it. `unknown` reads as unmeasurable rather than as metered, so an undeclared
connection errs toward saying less than it knows. What that costs downstream, and why
a money ceiling cannot bind a flat-rate connection at all, is in
[budget.md](budget.md).

## Model Routing Strategy

Model routing determines which LLM handles a given request. Four strategies are
registered in `STRATEGY_MAP` (`providers/routing/strategies.py`), selectable via
configuration. Role is a step inside `smart`'s cascade rather than a strategy of
its own; `role_based` names an *assignment* strategy, which is a different
subsystem (`engine/assignment/strategies.py`).

| Strategy | Behaviour |
|----------|----------|
| `manual` | Resolve an explicit model override; fails if not set |
| `cost_aware` | Match task-type rules, then pick cheapest model within budget |
| `fastest` | Match task-type rules, then pick the lowest-latency model (by `estimated_latency_ms`) within budget; falls back to cheapest when no latency data is available |
| `smart` | Priority cascade: override > task-type > role > cheapest > fallback chain |

```yaml
routing:
  strategy: "smart"              # smart, fastest, cost_aware, manual
  rules:
    - task_type: "architecture"
      preferred_model: "example-expert-001"
      fallback: "example-capable-001"
    - task_type: "development"
      preferred_model: "example-capable-001"
      fallback: "example-basic-001"
    - task_type: "code_review"
      preferred_model: "example-capable-001"
    - task_type: "documentation"
      preferred_model: "example-basic-001"
  fallback_chain:
    - "example-provider"
    - "openrouter"
    - "ollama"
```

### Capability routing: route the agent, never the horsepower

Each task (and subtask) carries a `stakes` level (`low` / `normal` / `high` /
`critical`), assessed by the `StakesAssessor`. Stakes set a **capability
floor** (`StakesCapabilityFloor`: low to `basic`, normal to `capable`,
high/critical to `expert`, validated non-decreasing, and every rung
operator-tunable through `engine.capability_floor_*`). Substantial complexity
(`complex` / `epic`) raises that floor one rung, because the work is harder
regardless of what it is worth.

What the requirement does is pick an **agent**, not a model. An agent is a fixed
`(role, model)` unit, so its capability is a property of the
employee; work that needs more of it goes to a different employee, exactly as
an organisation would handle it. The alternative the loop used to run,
re-dispatching a turn onto a stronger model under the same agent's name, made
every per-agent question unanswerable, because the runs were spread across
whatever the ladder reached for.

One `CapabilityPolicy` (`engine/routing_policy/capability_policy.py`) is built
at boot and shared by selection and dispatch, so the two cannot disagree about
what a task needs, what an agent has, or whether that agent may take it. Every
consumer reads the SAME `judge(...)` verdict:

- **Selection** (`engine/assignment/scoring_based.py` for solo work,
  `engine/routing/service.py` for a coordinated plan) walks the ladder above the
  existing scoring: the exact rung the work demands, else the nearest rung above,
  else the nearest rung below with `TASK_ASSIGNMENT_UNDER_CAPABILITY` logged.
  The existing ranker then decides *within* whichever band answers, so the score
  / workload / cost / auction axis is untouched. Preferring the exact rung over a
  stronger one is deliberate: it is the standing org-wide cost discipline, and
  because the band is chosen before cost orders the candidates, it buys the
  cheapest agent AT the demanded rung rather than the cheapest that clears it.
- **The park floor.** At or above `engine.capability_park_min_stakes` (default
  `high`) the lower band is refused rather than conceded, and selection returns
  no-eligible with `TASK_ASSIGNMENT_BELOW_CAPABILITY_FLOOR` naming the rung an
  operator has to staff. The measured reason is in
  [the A/B recording](../reference/model-capability-policy.md): complex and epic
  briefs on a basic model fail the correctness gate outright rather than
  degrading.
- **Dispatch** re-judges the agent that ended up holding the task, because a task
  can arrive assigned by hand, through the API, or by a `FAILED -> ASSIGNED`
  reassignment without ever passing selection. It asks the same policy instance
  and therefore cannot reach a different verdict; an unsanctioned pair raises
  `StakesModelUnavailableError` (`ErrorCode.STAKES_MODEL_UNAVAILABLE`, 503) and
  with an `ApprovalGate` wired the task parks (action
  `stakes:model_unavailable`, risk HIGH) so an operator can hire a qualifying
  agent or approve; otherwise it terminates `FAILED` with the typed error. A
  sanctioned lower fit logs and proceeds.

An agent's rung is read from the **registry** (`resolve_for_pair`), which is
where the evidence-graded ladder lives; the rung recorded on the roster
(`ModelConfig.capability`) is the fallback for a pair the registry does not
know.

One thing the policy still tunes on the call itself, because it changes how the
bound model works rather than which model runs: the per-stakes `reasoning_effort`
dial (`engine.reasoning_effort_*`). It also answers whether a deliverable needs
the red team (`engine.red_team_min_stakes`).

The capability requirement is read from the work alone: its stakes, and its
complexity. Nothing derived after assignment feeds back into it: multi-agent
quality is judged after the fact by the completion oracle and the red-team
gate, not by re-deciding who should have taken the work.

An agent likewise has exactly one bound model and no spare. `ModelConfig`
carries a `(provider, model_id)` pair and nothing beside it to fall back on,
which is what Explicit Provider Binding exists to protect: when a pair cannot
serve, the answer is another agent, not another model under the same name.

Capability judgement has no strategy discriminator and no opt-out: it is one
non-pluggable policy deciding which AGENT may take a piece of work, and every
one of its knobs is a live setting an operator can correct without a restart.
That is a separate question from model routing above, whose `routing.strategy`,
`routing.rules` and `fallback_chain` remain live inputs to `ModelRouter` for
resolving a provider and model.

**Where a rung comes from.** See [Capability grading](#capability-grading)
below: published evidence first, the deterministic heuristic behind it, and an
operator override over both.

**Per-task multi-provider routing.** System / infra services (decomposition,
evolution, compaction,
red-team, vision, the conflict judge, the security evaluators, the work
pipeline) each carry their own `MODEL_REF` setting and dispatch on the
`(provider, model)` pair it names. There is no shared house connection to
inherit: a provider is a registered *connection* carrying its own credentials,
endpoint and quota, so the same model id reached through two of them is two
different calls, billed and rate-limited separately, and a registry-level
default would spend the key of one feature on the work of another. A service whose pair
is unset stays off and says so, rather than borrowing a connection nobody chose
for it. Enforced by `check_no_provider_auto_pick.py` (no auto-pick, and the
whole `default_provider` accessor family stays removed) and
`check_explicit_model_binding.py` (no placeholder value, no bare model
default). A system feature is also the one place a *second* pair is
admissible, and only because an operator wrote it down: see
[Declared failover](#declared-failover).

There is exactly one carve-out, and it is narrow enough to state in full. The
agent engine holds a completion client for the case where **no registry is
wired at all** (`workers/runtime_builder.py`), and that client comes from
`coordination.decomposition_model`, a pair the runtime already requires before
it will build a coordinator. Every agent still dispatches on its own bound pair
through the registry; this is what the engine falls back to when there is no
registry to dispatch through, and taking it from a pair the operator has
already had to choose is what keeps it from being a connection nobody chose.

### Multi-Provider Model Resolution

An agent binds an **exclusive `(provider, model)` pair**: `ModelConfig` requires
both a `provider` and a `model_id`, and the agent's own model always resolves to
that provider, never re-derived across providers. Two gateways speaking the same
wire protocol can legitimately advertise an overlapping model id (each
live-discovers its own `/v1/models`), so a bare id can map to more than one
provider; the resolver keeps
all variants as a candidate tuple rather than raising a collision error, and the
binding decides which one an agent uses.

- **Provider-scoped resolution.** `ModelResolver.resolve_for_pair(provider, ref)`
  resolves a ref within one provider. Every caller that holds an agent's
  `identity.model.provider` (the capability policy grading a bound pair, the CFO
  downgrade / routing optimiser) resolves through it, so an overlapping id never
  silently moves the agent onto a different provider. The run-time client is resolved from
  `identity.model.provider` directly (`AgentEngine._dispatch_client_for`), so the
  API called and the `CostRecord.provider` always match the agent's binding.
- **No bare-ref auto-resolution.** There is no "resolve this model id against
  whichever provider happens to serve it" path. A model assignment always names
  its provider: a MODEL_REF setting rejects an unbound (provider-less) value at
  write-time, and feature builders resolve the ref's explicit provider, never a
  first-registered pick and never a shared default. The
  provider-agnostic archetype (`example-<capability>-001`) a pin records is still
  vendor-neutral; it is the *provider* that must be explicit, resolved once at
  dispatch, never auto-selected across gateways.
- **Eligibility-first selection.** When the config-selected routing strategies
  run over their explicit provider set, they prefer `agent_eligible` candidates:
  a provider kept out of agent work wins only when it is the sole provider for
  the ref. Stakes routing (`models_at_or_above_capability`) and agent seeding exclude
  ineligible providers outright.

Two built-in selectors are provided:

| Selector | Behaviour |
|----------|----------|
| `QuotaAwareSelector` (default) | Filter to providers with available quota first; within that pool (or all candidates when none have quota), prefer agent-eligible providers, then cheapest |
| `CheapestSelector` | Prefer agent-eligible providers, then pick the cheapest candidate by total cost per 1k tokens, ignoring quota state |

The selector is injected into `ModelResolver` (and transitively into `ModelRouter`)
at construction time.  `QuotaAwareSelector` is constructed with a snapshot from
`QuotaTracker.peek_quota_available()`, which returns a synchronous `dict[str, bool]`
of per-provider quota availability.

All routing strategies (`smart`, `cost_aware`, `fastest`, etc.) and the fallback chain
automatically use the injected selector when resolving model references, so multi-provider
selection is transparent to the strategy layer.

---

## Serviceability

Health answers "does this connection respond", over 24 hours, per provider,
counting a reachability probe as evidence. Serviceability answers "does this
model serve work", over a recent window, per `(provider, model)`, counting
only real calls. The two disagree exactly when it matters, and the incident
that motivated this surface is the shape of the disagreement: a model
returning 503 on most completions for an hour, taking up to 311 seconds to
refuse a five-token reply, while the prober reported it healthy and its
24-hour error rate stayed low because 24 hours is mostly not now.

Three consequences, each a decision rather than a detail:

- **The window is short** (`providers.serviceability_window_seconds`, 15
  minutes by default), because the question is "is this usable now".
- **The outcome split is by class**, because "queueing" and "balance empty"
  are different operator actions wearing the same error rate. The taxonomy
  gained `overloaded` (503) and `payment_required` (402) for exactly this;
  a retry-exhausted call is classified by the error it wrapped rather than
  filing the whole retried population under `other`.
- **Latency is a distribution** (p50 / p90 / p99 / max), because a mean over
  one fast call and one five-minute call reports a number neither call took.

A verdict needs a minimum sample (`providers.serviceability_min_calls`)
before it is anything but UNKNOWN, so one failure cannot take a pair out of
service. One outcome overrides that floor: a `payment_required` is not a
statistical signal that needs corroborating, it is a refusal that stands
until someone pays, so a window containing one reads DOWN however healthy
the rest of it looks.

That latch is honoured over `providers.serviceability_latch_lookback_seconds`
rather than over the rate window, and a later success does not clear it: a
provider may serve a cached or free request while refusing every billed one.
Expiry is the sole exit, which makes that setting the retry-after as well:
past it the pair is tried once more, so an operator who has topped up is
believed without a restart, and where nobody has, the pair latches again on
the refusal. It is capped at the record store's own 24-hour retention, because a
longer window would expire by eviction rather than by time.

Records are kept **in memory**. They sit on the hot path of every LLM call,
and a persisted row per call would double an already-accepted write volume
for a rolling window only this process reads. The durable halves that matter
(cost rows, failover events) are persisted separately.

**The latch is the exception, and it is durable.** Expiry being the sole exit
only held while the process lived: a restart cleared the records the latch is
read from, so an agent stood down under a verdict whose own text says *this
does not clear without an operator* was offered the same work on the same
refusing pair minutes later, and the operator who saw the warning had no way
to know it had been raised. `provider_latched_failures` holds one row per
`(provider, model)`, written through when a pair refuses and read back at boot
into the ordinary record path, so the verdict stays derived from one sequence
of outcomes. A row past that window is deleted rather than restored, which is
the retry-after doing its own housekeeping.

Surface: `GET /providers/serviceability` and `GET
/providers/{name}/serviceability`, rendered on the provider detail page and
beside the provider list.

## Capability grading

A rung (`basic` / `capable` / `expert`) is a claim about what a model can be
trusted with, which is why the ladder is no longer named after size: `large`
read as `best`, and that mis-framing is what let a mis-graded top rung hide.
The model pinned as the top tier benchmarked *below* the model already
sitting in the middle one, so every high-stakes task went to the worse of the
two. Locality is a separate axis with its own knob
(`engine.matcher_prefer_local`), not a fourth rung.

Precedence, highest first:

1. **Operator override** (`providers.capability_overrides`), as before.
2. **Published evidence**: per-axis scores ingested from a source registry
   (`providers/capability_sources/`), stored one row per
   `(source, model, axis)` with the date the SOURCE measured it. A model is
   graded on its *standing* among the models its own source measured, not on
   a shared numeric cutoff, because sources publish on different scales.
3. **The deterministic heuristic** (`HeuristicCapabilityClassifier`), from
   archetype id, then `cost_tier`, then parameter-count bands, then a cost
   proxy, falling back to `capable` at low confidence. Routing must always
   resolve a rung or escalate, never `None`.

A source qualifies on five bars: it MEASURES rather than restates vendor
numbers, its ground truth is objective, it publishes a stable
machine-readable feed, its licence permits both programmatic reading and the
redistribution a bundled snapshot amounts to, and its model identifiers
resolve to a configured model without guessing.

The first bar excludes head-to-head **preference** boards, which are
otherwise the obvious candidates: a vote on which of two replies a reader
liked executes no test and completes no task, it tracks presentation (length
and formatting above all), and it rewards agreeableness, which is precisely
the trait that makes an agent least safe to leave running. This product
routes work to agents, so a board of votes grades the wrong property however
many votes it holds.

That bar is about who produced a number, so it is applied per **row** rather
than per source. A published hub is typically a blend of evaluations its
owner ran, numbers restated from other leaderboards, and figures a vendor
reported about its own model; only the first is admitted, matched on the
feed's own provenance column. Treating such a hub as one uniform source lets
a vendor's self-assessment grade its own model, which is the proxy this
layer exists to replace.

The last bar is stricter than it sounds and is what keeps the source list
short: most published leaderboards key on a human display name, and a
display name resolves to a configured pair only by guessing. Matching is
exact, then once more with a leading routing prefix removed, and never
anything looser.

**Evidence age is counted from the read, not the run.** Sources do not date
individual measurements; a column that looks like a measurement date is
usually the model's release date, and using one makes evidence age read as
model age. So `as_of` records when the source last told us this, a bundled
row carries the date the release captured it, and the recency cut retires
the evidence of a feed that has quietly stopped answering.

Provenance is mandatory: every score renders with its source label and
`as_of` date, and the dashboard shows a staleness age, because a number with
no visible origin is not admissible evidence. A source that fails to fetch,
changes shape, or parses badly degrades to the heuristic and logs loudly; it
never breaks boot and never leaves a half-ingested source active. Whether a
source still answers is recorded separately from what it measured
(`capability_source_statuses`), because a feed that has been failing for a
month still has last month's rows in the table and the grading built on them
otherwise looks exactly as healthy as one refreshed an hour ago.

Operators inspect and adjust the map through the **Model Capability** panel
(Settings to Providers) backed by
`GET/PUT /api/v1/providers/capability-assignments`. An opt-in LLM recommender
(`LlmCapabilityRecommender`) offers per-model and bulk suggestions on the
operator-selected `providers.capability_classifier_model`, and returns a typed
unset state until one is picked.

## Agent availability

An agent whose bound pair is unserviceable is an employee who is out. That is
a state an organisation already knows how to handle, and far more explainable
than horsepower changing under a name.

Availability is **derived, never stored**: a read of the pair's recent
serviceability window, so it reverses itself the moment the window recovers
and nothing has to remember to un-set a flag. The one outcome that does not
decay is an empty balance; a 402 stands until an operator acts. The roster
read (`engine/roster.py`) filters unavailable agents out of assignment while
`get` stays unfiltered, so a project lead does not read as orphaned mid-run.
Transitions log `HR_AGENT_UNAVAILABLE_MODEL_UNSERVICEABLE` and
`HR_AGENT_AVAILABLE_MODEL_RECOVERED`, and the agent list and detail show the
state with its reason: which pair, which outcome class, since when, and
whether it needs an operator or will clear itself.

There are **two independent grounds, and the catalogue one dominates**. A pair
can be refusing calls, which the window measures; or it can be absent from the
provider's own catalogue, which no window can ever measure, because a pair
nobody can call makes no calls to fail. The catalogue moves under a roster
validated against it once at bind time (a provider retiring an untagged stem in
favour of dated tags is the ordinary way), and a binding left behind survives
selection, capability judging, plan review, and dispatch before failing at turn
1 of paid work. `unserved_binding` therefore checks membership on every
availability read and overrides the window verdict, reporting `NOT_FOUND` with
`needs_operator` set: an operator told "failing most recent calls" would go
looking at a status page instead of at the binding.

It **abstains rather than guesses** on two shapes that cannot be told apart
from a real absence: an empty catalogue, and a configured provider whose model
list is empty. Either reads the same whether nothing is configured or a
resolver handed back a partial view mid-boot, and on the wrong reading every
agent on that connection (or in the company) goes out at once. A provider
missing from a populated catalogue is not that case: the connection is gone,
and the read says so.

When unavailability leaves no agent clearing a task's floor, the task parks
through the same path as any other unmet floor.

## Declared failover

A system feature binds one `(provider, model)` pair and has **no employee to
mark out** when that pair stops serving, which is the one place a declared
fallback earns its keep. An agent has one (it goes unavailable and its work is
reassigned) and the gateway has one too (its pair is minted per run from
verified claims), so neither fails over.

The operator writes both halves into `providers.failover_routes`, keyed
`provider/model_id`, and resolution is an exact-key lookup: nothing is sorted,
indexed, ranked or scanned, so no arrangement of the provider registry can
produce a fallback nobody chose. A pair with no entry reads exactly like the
mechanism being off, which it is by default (`providers.failover_enabled`).
Both keys are governed writes: enabling widens what may answer a bound
request, and a route is guarded on **addition**, keyed
`declared -> alternate`, so repointing an existing pair at a different
connection is a fresh grant rather than an edit that slips past a toggle
somebody flipped months ago.

Two triggers, answering different halves of the same incident:

- **Pre-flight.** The declared pair's recent window already reads
  unserviceable, so it is not tried. This is the half that matters for cost
  and latency: paying the full retry ladder against a pair that takes 311
  seconds to refuse is the expensive way to learn nothing.
- **Retry once**, and only for `internal`, `overloaded`, `rate_limit`,
  `payment_required`, `timeout` and `connection`. An invalid request, a bad
  key, a content filter or an unknown model would fail identically on the
  alternate, so retrying there is pure latency on top of a failure the caller
  already has. Streaming gets pre-flight only: a stream that failed partway
  has already handed chunks to the caller, and replaying it elsewhere would
  deliver the opening of one response followed by the whole of another.

Never silent. Every engagement logs `PROVIDER_FAILOVER_ENGAGED` and persists a
`provider_failover_events` row recording **both pairs in full**, because "the
alternate" identifies nothing once the route map has been edited, and the log
does not survive the restart the question outlives. Cost needs no new code:
the alternate's own driver builds the `CostRecord`, so the row names what
actually served. Rows are read at `GET /providers/failover-events` (with the
declaration at `GET /providers/failover`) and rendered together, because a
route declared while the mechanism is off is inert and an engagement log with
no routes beside it cannot say whether what happened was what was asked for.

The carve-out is kept narrow by `check_declared_failover_pairs.py`: inside
`providers/failover*.py` it rejects an indexed computed sequence, `next(...)`,
a `.values()` / `list_providers` scan and any agent-identity reference; it
rejects importing any of them from `memory/`, the gateway package or
anything named `embedder`; and it allows `FailoverCompletionProvider` to be
constructed in `providers/model_binding.py` alone, which is what makes the
scope ruling structural rather than a convention.

Both of those last two rules answer a question about identity, not about
text, so both resolve what was written before deciding. An import is
resolved to an absolute module path first, because `from synthorg.providers
import failover` puts the package in one place and the module in another and
`from ..providers.failover import x` writes neither down; a construction is
matched on the name being called rather than the expression reaching it,
because importing the module and calling
`failover_dispatch.FailoverCompletionProvider(...)` builds the same object as
the bare name does. A rule that compared the written form would have been
answerable by an import style.

## Per-agent dispatch comparison

Because an agent is a fixed unit, "how did this agent, on this model, perform"
finally has an answer. `GET /agents/dispatch-profiles` reports every active
agent's own calls, grouped by role and bound pair so two agents on the same
model and the same role on two models both read off the page;
`GET /agents/{id}/dispatch-profile` reports one.

Two things keep it honest. Probe traffic is excluded, because a probe belongs
to no agent and letting a healthy probe cadence dilute a failing agent's
numbers is the same reporting defect serviceability exists to fix. And every
cell carries its sample size: one below `providers.agent_profile_min_calls`
renders as insufficient rather than as a number, since a rate over four calls
is not a measurement and rendering it beside one over four hundred invites a
decision the data cannot support.

Agent attributes (role, department) are joined at read
time from the live roster and never written onto a record: a row that copied
an agent's department would silently change meaning the day that agent moved,
which is exactly what makes historical numbers wrong.

## LLM Gateway (embedded-harness boundary)

An embedded coding harness (the [OpenHands loop](openhands-loop.md)) cannot
call the in-process `ProviderRegistry` directly, so the [LLM gateway](llm-gateway.md)
exposes an OpenAI-compatible HTTP surface that fronts the registry. Every
gateway call inherits the provider-layer governance described above: Explicit
Provider Binding (resolved from a per-run signed token, never the request's
`model`), cost and run attribution through `cost_recording_scope` (no single
prompt purpose applies to the harness's arbitrary prompts, so `purpose` is
`None`), and SEC-1 log redaction, plus a hard per-run token-budget kill. Provider
agnosticism thus becomes a property of the gateway, not the harness.

The gateway does **not** fail over. Its pair comes from the verified per-run
token claims, so there is no operator-declared route keyed on it and nothing
for [Declared failover](#declared-failover) to resolve; the claims stay a
single pair and `check_gateway_explicit_binding.py` needs no carve-out.

---

## See Also

- [LLM Gateway](llm-gateway.md) -- the OpenAI-compatible governance boundary over this provider layer
- [Budget & Cost Management](budget.md) -- token metering, cost tracking, CFO optimisation, quota degradation
- [Tools](tools.md) -- tool categories, sandboxing, MCP integration
- [Design Overview](index.md) -- full index
