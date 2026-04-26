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
 */
export interface ProviderConfig {
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
