/** LLM provider config, model registry and discovery types. */

export type AuthType = 'api_key' | 'oauth' | 'custom_header' | 'subscription' | 'none'

export type ProviderHealthStatus = 'up' | 'degraded' | 'down' | 'unknown'

export interface ProviderHealthSummary {
  last_check_timestamp: string | null
  avg_response_time_ms: number | null
  error_rate_percent_24h: number
  calls_last_24h: number
  health_status: ProviderHealthStatus
  total_tokens_24h: number
  total_cost_24h: number
}

export interface LocalModelParams {
  num_ctx: number | null
  num_gpu_layers: number | null
  num_threads: number | null
  num_batch: number | null
  repeat_penalty: number | null
}

/**
 * Payload for pulling a model on a local provider. Mirrors
 * `synthorg.api.dto_providers.PullModelRequest`.
 */
export interface PullModelRequest {
  /**
   * Model name/tag to pull (e.g. ``"test-local-001:latest"``). Must
   * match ``^[a-zA-Z0-9._:/@-]+$`` and be at most 256 characters.
   */
  model_name: string
}

/**
 * Payload for updating per-model launch parameters. Mirrors
 * `synthorg.api.dto_providers.UpdateModelConfigRequest`.
 */
export interface UpdateModelConfigRequest {
  local_params: LocalModelParams
}

export interface PullProgressEvent {
  status: string
  progress_percent: number | null
  total_bytes: number | null
  completed_bytes: number | null
  error: string | null
  done: boolean
}

export interface ProviderModelConfig {
  id: string
  alias: string | null
  cost_per_1k_input: number
  cost_per_1k_output: number
  max_context: number
  estimated_latency_ms: number | null
  local_params: LocalModelParams | null
}

export interface ProviderModelResponse {
  id: string
  alias: string | null
  cost_per_1k_input: number
  cost_per_1k_output: number
  max_context: number
  estimated_latency_ms: number | null
  local_params: LocalModelParams | null
  supports_tools: boolean
  supports_vision: boolean
  supports_streaming: boolean
}

/**
 * Provider response DTO -- secrets stripped, credential indicators provided.
 *
 * ``name`` is populated by paginated list endpoints; it is ``null`` on
 * single-provider GETs because the URL already carries the identifier.
 */
export interface ProviderConfig {
  name?: string | null
  driver: string
  litellm_provider: string | null
  auth_type: AuthType
  base_url: string | null
  readonly models: readonly ProviderModelConfig[]
  has_api_key: boolean
  has_oauth_credentials: boolean
  has_custom_header: boolean
  has_subscription_token: boolean
  tos_accepted_at: string | null
  oauth_token_url: string | null
  oauth_client_id: string | null
  oauth_scope: string | null
  custom_header_name: string | null
  preset_name: string | null
  supports_model_pull: boolean
  supports_model_delete: boolean
  supports_model_config: boolean
}

export interface CreateProviderRequest {
  name: string
  driver?: string
  litellm_provider?: string
  auth_type?: AuthType
  api_key?: string
  subscription_token?: string
  tos_accepted?: boolean
  base_url?: string
  oauth_token_url?: string
  oauth_client_id?: string
  oauth_client_secret?: string
  oauth_scope?: string
  custom_header_name?: string
  custom_header_value?: string
  preset_name?: string
  models?: readonly ProviderModelConfig[]
}

export interface UpdateProviderRequest {
  driver?: string
  litellm_provider?: string
  auth_type?: AuthType
  api_key?: string
  clear_api_key?: boolean
  subscription_token?: string
  clear_subscription_token?: boolean
  tos_accepted?: boolean
  base_url?: string | null
  oauth_token_url?: string | null
  oauth_client_id?: string | null
  oauth_client_secret?: string | null
  oauth_scope?: string | null
  custom_header_name?: string | null
  custom_header_value?: string | null
  models?: readonly ProviderModelConfig[]
}

export interface TestConnectionRequest {
  model?: string
}

export interface TestConnectionResponse {
  success: boolean
  latency_ms: number | null
  error: string | null
  model_tested: string | null
}

/** Fields shared by every preset kind. */
interface BasePresetFields {
  name: string
  display_name: string
  description: string
  driver: string
  litellm_provider: string
  auth_type: AuthType
  default_base_url: string | null
  requires_base_url: boolean
  /**
   * ``true`` for hand-curated presets (logo, vetted description,
   * default-model fallbacks); ``false`` for soft presets auto-derived
   * from ``litellm.model_cost``.  Drives the wizard's split between
   * the primary grid and the "More providers" section.
   */
  is_featured: boolean
}

/** Hosted LLM provider (no auto-detect, prefilled model list). */
export interface CloudPreset extends BasePresetFields {
  kind: 'cloud'
  readonly supported_auth_types: readonly AuthType[]
  readonly default_models: readonly ProviderModelConfig[]
}

/** Self-hosted LLM server (auto-detect via candidate URLs). */
export interface LocalPreset extends BasePresetFields {
  kind: 'local'
  readonly candidate_urls: readonly string[]
  readonly supports_model_pull: boolean
  readonly supports_model_delete: boolean
  readonly supports_model_config: boolean
}

/** Discriminated union of every preset kind, keyed by ``kind``. */
export type ProviderPreset = CloudPreset | LocalPreset

/** Per-preset probe outcome (used as a value inside ``ProbeLocalResponse``). */
export interface ProbePresetResponse {
  url: string | null
  model_count: number
  candidates_tried: number
}

/** Batch result of ``POST /providers/probe-local``. */
export interface ProbeLocalResponse {
  /**
   * Map of preset name to per-preset probe result.  Only local presets
   * with non-empty ``candidate_urls`` are probed and appear here.
   * Cloud presets and vLLM (intentionally empty candidates) are
   * excluded from both maps.
   */
  readonly results: Readonly<Partial<Record<string, ProbePresetResponse>>>
  /**
   * Map of preset name to error message for presets whose probes
   * raised.  Disjoint with ``results``.
   */
  readonly errors: Readonly<Partial<Record<string, string>>>
}

export interface CreateFromPresetRequest {
  preset_name: string
  name: string
  auth_type?: AuthType
  api_key?: string
  subscription_token?: string
  tos_accepted?: boolean
  base_url?: string
  models?: readonly ProviderModelConfig[]
}

export interface DiscoverModelsResponse {
  readonly discovered_models: readonly ProviderModelConfig[]
  provider_name: string
}

export interface DiscoveryPolicyResponse {
  readonly host_port_allowlist: readonly string[]
  block_private_ips: boolean
  entry_count: number
}

export interface AddAllowlistEntryRequest {
  host_port: string
}

export interface RemoveAllowlistEntryRequest {
  host_port: string
}

// ── Post-CRUD provider capabilities (audit log, rate-limits, ──────────
//    preset overrides, credentials rotate, manual model add, sync) ────

/** Stable string set for ProviderAuditEvent.event_type. */
export type ProviderAuditEventType =
  | 'provider_created'
  | 'provider_updated'
  | 'provider_deleted'
  | 'provider_credentials_rotated'
  | 'provider_rate_limits_updated'
  | 'preset_override_updated'
  | 'model_added'
  | 'model_removed'
  | 'model_config_updated'
  | 'model_pulled'
  | 'models_synced'

export interface ProviderAuditActor {
  readonly id: string
  readonly label: string
}

/** One row in the provider mutation audit log (append-only). */
export interface ProviderAuditEvent {
  /**
   * Repo-assigned monotonic id.  Null for events constructed in memory
   * before persistence; always non-null on rows returned by the audit
   * list endpoint.  Mirrors the backend ``int | None`` contract.
   */
  readonly id: number | null
  readonly provider_name: string
  readonly event_type: ProviderAuditEventType
  readonly actor: ProviderAuditActor
  readonly payload: Readonly<Record<string, unknown>>
  /** ISO 8601 UTC timestamp. */
  readonly occurred_at: string
}

/**
 * Effective rate-limit configuration for one provider.
 *
 * ``0`` means "unlimited" on both fields, matching the persisted
 * ``RateLimiterConfig`` semantics.  Display layers should render
 * "Unlimited" when the value is exactly ``0``.
 */
export interface RateLimitsConfig {
  readonly requests_per_minute: number
  readonly concurrent_requests: number
}

/**
 * Partial-update payload for the rate-limit PATCH endpoint.
 *
 * Either field may be omitted; at least one must be present (the
 * backend rejects empty patches with HTTP 422).  Pass ``0`` to set
 * a cap to "unlimited"; pass a positive int to apply a new cap.
 */
export interface RateLimitsUpdateRequest {
  requests_per_minute?: number
  concurrent_requests?: number
}

/**
 * Persisted operator override on top of an in-code provider preset.
 *
 * ``null`` fields fall back to the in-code preset; non-``null`` fields
 * replace the preset's corresponding field at read time.
 */
export interface PresetOverride {
  readonly preset_name: string
  readonly default_models: readonly ProviderModelConfig[] | null
  readonly supported_auth_types: readonly AuthType[] | null
  readonly candidate_urls: readonly string[] | null
  readonly base_url: string | null
  /** ISO 8601 UTC timestamp; null when the row was never written. */
  readonly updated_at: string | null
  readonly updated_by: string | null
}

/**
 * Partial-update payload for the preset-override PATCH endpoint.
 *
 * ``undefined`` (omitted) means "leave unchanged"; ``null`` means
 * "clear the override and inherit from the base preset".
 */
export interface PresetOverrideUpdateRequest {
  default_models?: readonly ProviderModelConfig[] | null
  supported_auth_types?: readonly AuthType[] | null
  candidate_urls?: readonly string[] | null
  base_url?: string | null
}

/**
 * Discriminated-union rotation payload keyed by ``auth_type``.
 *
 * The variant must match the provider's persisted ``auth_type``;
 * the backend rejects mismatches with HTTP 422.
 */
export type CredentialsRotateRequest =
  | {
      auth_type: 'api_key'
      api_key: string
    }
  | {
      auth_type: 'subscription'
      subscription_token: string
      tos_accepted: boolean
    }
  | {
      auth_type: 'custom_header'
      custom_header_name: string
      custom_header_value: string
    }
  | {
      auth_type: 'oauth'
      oauth_token_url: string
      oauth_client_id: string
      oauth_client_secret: string
      oauth_scope?: string
    }

export interface AddModelRequest {
  model: ProviderModelConfig
}

export interface SyncModelsRequest {
  /**
   * When ``true`` (default), the persisted list is replaced with the
   * merged discovered+enriched set.  When ``false``, only new ids are
   * appended; existing models keep their persisted config verbatim.
   */
  replace_existing?: boolean
  /** Optional preset hint for discovery shape (Ollama vs standard). */
  preset_hint?: string
}

export interface SyncModelsResponse {
  readonly added: readonly string[]
  readonly removed: readonly string[]
  readonly updated: readonly string[]
  readonly models: readonly ProviderModelConfig[]
}
