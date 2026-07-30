/** External integrations: connections, OAuth apps, MCP catalog, tunnel. */

export type {
  CatalogEntry,
  Connection,
  ConnectionFieldMetadata,
  ConnectionTypeMetadata,
  FieldCondition,
  CreateConnectionRequest,
  DeviceLoginPrompt,
  ForgeAccessibleRepo,
  HealthReport,
  InitiateOAuthFlowRequest,
  InstalledEntry,
  InstallEntryRequest,
  InstallEntryResponse,
  OAuthInitiationResponse,
  OAuthTokenStatusResponse,
  RevealedSecretResponse,
  SecretCaptureRequest,
  SecretCaptureResponse,
  TunnelProviderStatus,
  TunnelSnapshot,
  TunnelStartResponse,
  UpdateConnectionRequest,
  WebhookReceipt,
} from './dtos.gen'

export type {
  ConnectionStatus,
  ConnectionType,
} from './enum-values.gen'
export { CONNECTION_TYPE_VALUES } from './enum-values.gen'

import type {
  CatalogEntry,
  ConnectionTypeMetadata,
  InstallEntryRequest,
  InstallEntryResponse,
} from './dtos.gen'
import type { ConnectionStatus, ConnectionType } from './enum-values.gen'

/**
 * Stable domain-named aliases onto the generated DTOs / enum values so call
 * sites importing ``McpCatalogEntry`` etc. stay insulated from rename churn in
 * the generated barrel.
 */
export type McpCatalogEntry = CatalogEntry
export type McpInstallRequest = InstallEntryRequest
export type McpInstallResponse = InstallEntryResponse
export type ConnectionHealthStatus = ConnectionStatus

/**
 * The credential field this connection type's webhook signing secret goes in,
 * or `null` when the type declares no such field and so can never receive a
 * webhook. `null` is a fact about the *type*, never about whether some
 * connection's secret happens to be set.
 *
 * Read from the backend's own `webhook_secret_field`, which derives it from the
 * one condition that decides it: inbound ingest rejects any delivery it cannot
 * authenticate, so a type exposing no signing-secret credential has no
 * reachable ingest path and can never accumulate a receipt. Asking the registry
 * rather than keeping a list here is what stops the dashboard drifting from the
 * backend's verifier coverage in either direction.
 *
 * Prefer `useWebhookSecretField` in a component: it sources the registry itself,
 * so a caller cannot pass an array that has not loaded yet and read the answer
 * as "no webhooks".
 */
export function webhookSecretFieldFor(
  type: ConnectionType,
  metadata: readonly ConnectionTypeMetadata[],
): string | null {
  return metadata.find((m) => m.connection_type === type)?.webhook_secret_field ?? null
}

/**
 * Tunnel provider ids the backend ships. Mirrors the
 * ``integrations.tunnel_provider`` settings enum; the snapshot's
 * ``providers`` list is the runtime source of truth, this union only
 * types the write paths (provider selection, credential endpoints).
 */
export type TunnelProviderId = 'cloudflare' | 'ngrok' | 'devtunnels'
