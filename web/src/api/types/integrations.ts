/** External integrations: connections, OAuth apps, MCP catalog, tunnel. */

export type {
  CatalogEntry,
  Connection,
  CreateConnectionRequest,
  HealthReport,
  InitiateOAuthFlowRequest,
  InstallEntryRequest,
  InstallEntryResponse,
  OAuthInitiationResponse,
  OAuthTokenStatusResponse,
  RevealedSecretResponse,
  UpdateConnectionRequest,
} from './dtos.gen'

export type { ConnectionStatus, ConnectionType } from './enum-values.gen'
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


export interface TunnelStatus {
  readonly public_url: string | null
  /**
   * Whether the backend has an ngrok auth token configured (via the
   * NGROK_AUTHTOKEN env var). ngrok no longer permits anonymous
   * tunnels (ERR_NGROK_4018), so when this is false no tunnel can
   * start at all; the dashboard disables Start and surfaces a hint to
   * configure a (free) account authtoken.
   */
  readonly has_auth_token: boolean
}
