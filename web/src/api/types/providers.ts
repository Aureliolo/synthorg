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

import type {
  CloudPreset as WireCloudPreset,
  LocalPreset as WireLocalPreset,
  ProviderAuditEvent as WireProviderAuditEvent,
  ProviderResponse as WireProviderResponse,
} from './dtos.gen'

/** Mirrors the inline string union on the wire ProviderAuditEvent.event_type
 *  (openapi-typescript inlines anonymous unions rather than emitting a named
 *  type), aliased to the generated parent so backend additions are picked up
 *  without hand-editing this file. */
export type ProviderAuditEventType = WireProviderAuditEvent['event_type']

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

// Overlay the wire's ``ProviderResponse`` shape: the dashboard treats
// every nullable field as always present (the Pydantic serialiser
// emits them on every response), so we promote the optional-nullable
// fields to required-nullable. The boolean and required fields stay
// as-typed by the wire.
export type ProviderConfig = Omit<
  WireProviderResponse,
  | 'base_url'
  | 'custom_header_name'
  | 'litellm_provider'
  | 'oauth_client_id'
  | 'oauth_scope'
  | 'oauth_token_url'
  | 'preset_name'
  | 'tos_accepted_at'
> & {
  readonly base_url: string | null
  readonly custom_header_name: string | null
  readonly litellm_provider: string | null
  readonly oauth_client_id: string | null
  readonly oauth_scope: string | null
  readonly oauth_token_url: string | null
  readonly preset_name: string | null
  readonly tos_accepted_at: string | null
}

/** Discriminated union of every preset kind, keyed by ``kind``. */
export type ProviderPreset = WireCloudPreset | WireLocalPreset
