/**
 * Connection (integration peer) mock-data builders.
 */

export interface MockConnection {
  name: string
  connection_type: 'github' | 'slack' | 'jira' | 'a2a_peer' | 'custom'
  auth_method: 'api_key' | 'oauth2' | 'jwt'
  base_url: string
  health_status: 'unknown' | 'healthy' | 'degraded' | 'unhealthy'
  metadata: Record<string, string>
}

export function makeConnection(
  overrides: Partial<MockConnection> = {},
): MockConnection {
  return {
    name: 'a2a-peer-001',
    connection_type: 'a2a_peer',
    auth_method: 'jwt',
    base_url: 'https://peer.example.com',
    health_status: 'unknown',
    metadata: {},
    ...overrides,
  }
}
