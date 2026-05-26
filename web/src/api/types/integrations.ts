/** External integrations: connections, OAuth apps, MCP catalog, tunnel. */

export type {
  Connection,
  CreateConnectionRequest,
  HealthReport,
  UpdateConnectionRequest,
} from './dtos.gen'

export type { ConnectionType } from './enum-values.gen'
export { CONNECTION_TYPE_VALUES } from './enum-values.gen'

import type { ConnectionType } from './enum-values.gen'

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

/** Inline string unions on Connection / Connection responses. The
 *  values are not surfaced as named enum schemas by Pydantic, so the
 *  unions stay hand-maintained until the backend promotes them. */

export type ConnectionHealthStatus =
  | 'healthy'
  | 'degraded'
  | 'unhealthy'
  | 'unknown'

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

export type McpTransport = 'stdio' | 'streamable_http'

export interface McpCatalogEntry {
  readonly id: string
  readonly name: string
  readonly description: string
  readonly npm_package: string | null
  readonly required_connection_type: ConnectionType | null
  readonly transport: McpTransport
  readonly capabilities: readonly string[]
  readonly tags: readonly string[]
}

export interface McpInstallRequest {
  readonly catalog_entry_id: string
  readonly connection_name?: string | null
}

export interface McpInstallResponse {
  readonly status: 'installed'
  readonly server_name: string
  readonly catalog_entry_id: string
  readonly tool_count: number
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
