/** External integrations: connections, OAuth apps, MCP catalog, tunnel. */

export type {
  CatalogEntry,
  Connection,
  ConnectionFieldMetadata,
  ConnectionTypeMetadata,
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

import type { CatalogEntry, InstallEntryRequest, InstallEntryResponse } from './dtos.gen'
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
 * Connection types that emit webhook receipts the retention sweep cleans up.
 * The `webhook_receipt_retention_days` column exists on every connection row,
 * but configuring it for a non-webhook type (smtp, database, a2a_peer) is
 * meaningless: those connections never produce receipts. The dashboard only
 * surfaces the field for the types below; backend validation accepts the
 * field on any type so a future webhook-emitting type only needs adding to
 * this list.
 */
const WEBHOOK_RECEIPT_CONNECTION_TYPES = [
  'github',
  'slack',
  'generic_http',
  'oauth_app',
] as const satisfies readonly ConnectionType[]

export function connectionTypeUsesWebhookReceipts(
  type: ConnectionType,
): boolean {
  return (WEBHOOK_RECEIPT_CONNECTION_TYPES as readonly ConnectionType[]).includes(type)
}


/**
 * Tunnel provider ids the backend ships. Mirrors the
 * ``integrations.tunnel_provider`` settings enum; the snapshot's
 * ``providers`` list is the runtime source of truth, this union only
 * types the write paths (provider selection, credential endpoints).
 */
export type TunnelProviderId = 'cloudflare' | 'ngrok' | 'devtunnels'
