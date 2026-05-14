/** LLM provider config, model registry and discovery types. */

export type {
  AddAllowlistEntryRequest,
  AddModelRequest,
  CloudPreset,
  CreateFromPresetRequest,
  CreateProviderRequest,
  DiscoverModelsResponse,
  DiscoveryPolicyResponse,
  LocalModelParams,
  LocalPreset,
  PresetOverride,
  PresetOverrideUpdateRequest,
  ProbeLocalResponse,
  ProbePresetResponse,
  ProviderAuditActor,
  ProviderAuditEvent,
  ProviderHealthSummary,
  ProviderModelConfig,
  ProviderModelResponse,
  PullModelRequest,
  RateLimitsUpdateRequest,
  RemoveAllowlistEntryRequest,
  SyncModelsRequest,
  SyncModelsResponse,
  TestConnectionRequest,
  TestConnectionResponse,
  UpdateModelConfigRequest,
  UpdateProviderRequest,
} from './dtos.gen'

export type { AuthType, ProviderHealthStatus } from './enum-values.gen'
export { AUTH_TYPE_VALUES, PROVIDER_HEALTH_STATUS_VALUES } from './enum-values.gen'

import type {
  AddModelRequest as WireAddModelRequest,
  CloudPreset as WireCloudPreset,
  LocalPreset as WireLocalPreset,
  PresetOverride as WirePresetOverride,
  ProviderAuditEvent as WireProviderAuditEvent,
  ProviderHealthSummary as WireProviderHealthSummary,
  ProviderModelConfig as WireProviderModelConfig,
} from './dtos.gen'
import type { AuthType } from './enum-values.gen'

/** Frontend-only union mirroring the inline string union on the wire
 *  ProviderAuditEvent.event_type. */
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

/** Discriminated-union rotation payload keyed by ``auth_type``. The
 *  wire validates this via a server-side discriminated model; OpenAPI
 *  emits the variants inline rather than as a named union. */
export type CredentialsRotateRequest =
  | { auth_type: 'api_key'; api_key: string }
  | { auth_type: 'subscription'; subscription_token: string; tos_accepted: boolean }
  | { auth_type: 'custom_header'; custom_header_name: string; custom_header_value: string }
  | {
      auth_type: 'oauth'
      oauth_token_url: string
      oauth_client_id: string
      oauth_client_secret: string
      oauth_scope?: string
    }

/** Streamed pull progress (Server-Sent Events). The wire frames each
 *  event as JSON without surfacing a named schema. */
export interface PullProgressEvent {
  status: string
  progress_percent: number | null
  total_bytes: number | null
  completed_bytes: number | null
  error: string | null
  done: boolean
}

/** Effective rate-limit configuration for one provider. Not surfaced
 *  as a named OpenAPI component schema. */
export interface RateLimitsConfig {
  readonly requests_per_minute: number
  readonly concurrent_requests: number
}

/**
 * Provider response DTO (no named OpenAPI schema; the controller
 * returns a hand-built ``dict``). Mirrors the
 * ``synthorg.api.dto_providers.ProviderConfig`` Pydantic model.
 */
export interface ProviderConfig {
  name?: string | null
  driver: string
  litellm_provider: string | null
  auth_type: AuthType
  base_url: string | null
  readonly models: readonly WireProviderModelConfig[]
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

/** Discriminated union of every preset kind, keyed by ``kind``. */
export type ProviderPreset = WireCloudPreset | WireLocalPreset

/** Re-exports of internal-only wire types kept here so consumer
 *  imports do not need to reach into ``dtos.gen`` directly. */
export type { WireAddModelRequest, WirePresetOverride, WireProviderAuditEvent, WireProviderHealthSummary }
