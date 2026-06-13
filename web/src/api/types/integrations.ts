/** External integrations: connections, OAuth apps, MCP catalog, tunnel. */

export type {
  CatalogEntry,
  Connection,
  CreateConnectionRequest,
  HealthReport,
  InstallEntryRequest,
  InstallEntryResponse,
  UpdateConnectionRequest,
} from './dtos.gen'

export type { ConnectionStatus, ConnectionType } from './enum-values.gen'
export { CONNECTION_TYPE_VALUES } from './enum-values.gen'

import type { CatalogEntry, InstallEntryRequest, InstallEntryResponse } from './dtos.gen'
import type { ConnectionStatus, ConnectionType } from './enum-values.gen'

/**
 * Aliases onto the generated DTOs / enum values. These previously duplicated
 * the backend Pydantic shapes by hand; they now derive from the generator so a
 * backend change flows through without a second edit. The original names are
 * kept as aliases to avoid churning every call site.
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

/** Endpoint-only shapes that the controller layer uses but Pydantic
 *  models surface inline (no top-level ``components.schemas`` entry). */
export interface RevealSecretResponse {
  readonly field: string
  readonly value: string
}

export type OauthInitiateRequest = {
  readonly connection_name: string
  readonly scopes?: readonly string[]
}

export interface OauthInitiateResponse {
  readonly authorization_url: string
  readonly state_token: string
}

export interface OauthTokenStatus {
  readonly connection_name: string
  readonly has_token: boolean | null
  readonly token_expires_at: string | null
}

export interface TunnelStatus {
  readonly public_url: string | null
  /**
   * Whether the backend has an ngrok auth token configured (via the
   * NGROK_AUTHTOKEN env var). When false, the tunnel runs on ngrok's
   * free tier: random URLs, low bandwidth caps, short session limits.
   * The dashboard surfaces a hint about setting the token so the
   * operator can opt into the paid tier when they need static URLs.
   */
  readonly has_auth_token: boolean
}
