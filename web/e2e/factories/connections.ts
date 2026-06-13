/**
 * Connection (integration peer) mock-data builders.
 */

/**
 * Connection, mirroring ``Connection`` from ``@/api/types`` (the wire
 * shape ``/connections`` returns). The connections page reads
 * ``secret_refs`` / ``rate_limiter`` / the ``health_*`` fields, so the
 * earlier minimal shape left the row crashing or unrendered.
 */
export interface MockConnection {
  id: string
  name: string
  connection_type: 'github' | 'slack' | 'jira' | 'a2a_peer' | 'custom'
  auth_method: 'bearer_token' | 'api_key' | 'oauth2' | 'jwt'
  base_url: string | null
  health_check_enabled: boolean
  health_status: 'unknown' | 'healthy' | 'degraded' | 'unhealthy'
  last_health_check_at: string | null
  metadata: Record<string, string>
  rate_limiter: unknown | null
  secret_refs: unknown[]
  webhook_receipt_retention_days: number | null
  sensitive: boolean
  created_at: string
  updated_at: string
}

export function makeConnection(
  overrides: Partial<MockConnection> = {},
): MockConnection {
  return {
    id: 'conn-001',
    name: 'a2a-peer-001',
    connection_type: 'a2a_peer',
    auth_method: 'jwt',
    base_url: 'https://peer.example.com',
    health_check_enabled: true,
    health_status: 'unknown',
    last_health_check_at: null,
    metadata: {},
    rate_limiter: null,
    secret_refs: [],
    webhook_receipt_retention_days: null,
    sensitive: false,
    created_at: '2026-04-01T12:00:00Z',
    updated_at: '2026-04-01T12:00:00Z',
    ...overrides,
  }
}
