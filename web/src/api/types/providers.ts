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
  RateLimitsResponse,
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
import type { components } from './openapi.gen'

/** Mirrors the inline string union on the wire ProviderAuditEvent.event_type
 *  (openapi-typescript inlines anonymous unions rather than emitting a named
 *  type), aliased to the generated parent so backend additions are picked up
 *  without hand-editing this file. */
export type ProviderAuditEventType = WireProviderAuditEvent['event_type']

/** Discriminated-union rotation payload keyed by ``auth_type``. Aliased from
 *  the generated underscore-prefixed component schemas (the generator's
 *  PascalCase filter excludes them from ``dtos.gen.ts``, so they are pulled
 *  directly here) to stay in lockstep with the backend rotation models. */
export type CredentialsRotateRequest =
  | components['schemas']['_ApiKeyRotation']
  | components['schemas']['_SubscriptionRotation']
  | components['schemas']['_CustomHeaderRotation']
  | components['schemas']['_OAuthRotation']

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
