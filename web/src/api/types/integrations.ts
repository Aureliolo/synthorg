/** External integrations: connections, OAuth apps, MCP catalog, tunnel. */

export type ConnectionType =
  | 'github'
  | 'slack'
  | 'smtp'
  | 'database'
  | 'generic_http'
  | 'oauth_app'
  | 'a2a_peer'

export const CONNECTION_TYPE_VALUES = [
  'github',
  'slack',
  'smtp',
  'database',
  'generic_http',
  'oauth_app',
  'a2a_peer',
] as const satisfies readonly ConnectionType[]

/**
 * Connection types that emit webhook receipts the retention sweep cleans up.
 * The `webhook_receipt_retention_days` column exists on every connection
 * row, but configuring it for a non-webhook type (smtp, database, a2a_peer)
 * is meaningless: those connections never produce receipts. The dashboard
 * only surfaces the field for the types below; backend validation accepts
 * the field on any type so a future webhook-emitting type only needs
 * adding to this list.
 */
export const WEBHOOK_RECEIPT_CONNECTION_TYPES = [
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

export type ConnectionAuthMethod =
  | 'api_key'
  | 'oauth2'
  | 'basic_auth'
  | 'bearer_token'
  | 'custom'

export type ConnectionHealthStatus =
  | 'healthy'
  | 'degraded'
  | 'unhealthy'
  | 'unknown'

export interface Connection {
  readonly id: string
  readonly name: string
  readonly connection_type: ConnectionType
  readonly auth_method: ConnectionAuthMethod
  readonly base_url: string | null
  readonly health_check_enabled: boolean
  readonly health_status: ConnectionHealthStatus
  readonly last_health_check_at: string | null
  readonly metadata: Record<string, string>
  /**
   * Per-connection override for the webhook-receipt retention window
   * (days). `null` falls back to the global
   * `integrations.webhooks.receipt_retention_days` setting; `0` opts
   * this connection out of the cleanup sweep entirely.
   */
  readonly webhook_receipt_retention_days: number | null
  readonly created_at: string
  readonly updated_at: string
}

export interface CreateConnectionRequest {
  readonly name: string
  readonly connection_type: ConnectionType
  readonly auth_method?: ConnectionAuthMethod
  readonly credentials: Record<string, string>
  readonly base_url?: string | null
  readonly metadata?: Record<string, string>
  readonly health_check_enabled?: boolean
  readonly webhook_receipt_retention_days?: number | null
}

export interface UpdateConnectionRequest {
  readonly base_url?: string | null
  readonly metadata?: Record<string, string>
  readonly health_check_enabled?: boolean
  readonly webhook_receipt_retention_days?: number | null
}

export interface HealthReport {
  readonly connection_name: string
  readonly status: ConnectionHealthStatus
  readonly latency_ms: number | null
  readonly error_detail: string | null
  readonly checked_at: string
  readonly consecutive_failures: number
}

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
