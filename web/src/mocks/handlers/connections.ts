import { http, HttpResponse } from 'msw'
import type {
  checkConnectionHealth,
  createConnection,
  getConnection,
  listConnections,
  revealConnectionSecret,
  updateConnection,
} from '@/api/endpoints/connections'
import type { Connection, ConnectionType } from '@/api/types/integrations'
import { apiError, successFor, voidSuccess } from './helpers'

const NOW = '2026-04-11T12:00:00Z'

export function buildConnection(
  overrides: Partial<Connection> = {},
): Connection {
  return {
    id: 'conn-default',
    name: 'default-connection',
    connection_type: 'github',
    auth_method: 'bearer_token',
    base_url: null,
    health_check_enabled: true,
    health_status: 'unknown',
    last_health_check_at: null,
    metadata: {},
    secret_refs: [],
    webhook_receipt_retention_days: null,
    created_at: NOW,
    updated_at: NOW,
    ...overrides,
  }
}

// ── Storybook-facing named exports (preserve for existing stories). ──

const mockConnections: Connection[] = [
  buildConnection({
    id: 'conn-000000000001',
    name: 'primary-github',
    connection_type: 'github',
    auth_method: 'bearer_token',
    base_url: 'https://api.github.com',
    health_status: 'healthy',
    last_health_check_at: NOW,
    created_at: '2026-04-01T09:00:00Z',
  }),
  buildConnection({
    id: 'conn-000000000002',
    name: 'dev-slack',
    connection_type: 'slack',
    auth_method: 'bearer_token',
    health_status: 'degraded',
    last_health_check_at: NOW,
    created_at: '2026-04-02T10:30:00Z',
  }),
  buildConnection({
    id: 'conn-000000000003',
    name: 'ops-smtp',
    connection_type: 'smtp',
    auth_method: 'basic_auth',
    health_status: 'unhealthy',
    last_health_check_at: NOW,
    created_at: '2026-04-03T11:15:00Z',
  }),
  buildConnection({
    id: 'conn-000000000004',
    name: 'reporting-db',
    connection_type: 'database',
    auth_method: 'basic_auth',
    health_status: 'healthy',
    last_health_check_at: NOW,
    created_at: '2026-04-04T08:00:00Z',
  }),
  buildConnection({
    id: 'conn-000000000005',
    name: 'billing-api',
    connection_type: 'generic_http',
    auth_method: 'api_key',
    base_url: 'https://billing.example.com',
    health_status: 'unknown',
    created_at: '2026-04-05T14:20:00Z',
  }),
  buildConnection({
    id: 'conn-000000000006',
    name: 'gh-oauth-app',
    connection_type: 'oauth_app',
    auth_method: 'oauth2',
    health_check_enabled: false,
    health_status: 'unknown',
    created_at: '2026-04-06T09:45:00Z',
  }),
]

export const connectionsList = [
  http.get('/api/v1/connections', () =>
    HttpResponse.json(successFor<typeof listConnections>(mockConnections)),
  ),
  http.get('/api/v1/connections/:name', ({ params }) => {
    const conn = mockConnections.find((c) => c.name === params.name)
    if (!conn) return HttpResponse.json(apiError('Connection not found'), { status: 404 })
    return HttpResponse.json(successFor<typeof getConnection>(conn))
  }),
  http.post('/api/v1/connections', async ({ request }) => {
    const body = (await request.json()) as Partial<Connection> & { connection_type?: string }
    if (!body.name) {
      return HttpResponse.json(apiError("Field 'name' is required"), { status: 400 })
    }
    return HttpResponse.json(
      successFor<typeof createConnection>(
        buildConnection({
          id: `conn-${String(body.name)}`,
          name: body.name as string,
          connection_type: (body.connection_type ?? 'github') as ConnectionType,
        }),
      ),
      { status: 201 },
    )
  }),
  http.patch('/api/v1/connections/:name', async ({ params }) => {
    const conn = mockConnections.find((c) => c.name === params.name)
    if (!conn) return HttpResponse.json(apiError('Connection not found'), { status: 404 })
    return HttpResponse.json(
      successFor<typeof updateConnection>({ ...conn, updated_at: NOW }),
    )
  }),
  http.delete('/api/v1/connections/:name', () => HttpResponse.json(voidSuccess())),
  http.get('/api/v1/connections/:name/health', ({ params }) => {
    const conn = mockConnections.find((c) => c.name === params.name)
    if (!conn) return HttpResponse.json(apiError('Connection not found'), { status: 404 })
    return HttpResponse.json(
      successFor<typeof checkConnectionHealth>({
        connection_name: conn.name,
        status: conn.health_status ?? 'unknown',
        latency_ms: conn.health_status === 'healthy' ? 42 : null,
        error_detail: conn.health_status === 'unhealthy' ? 'Connection refused' : null,
        checked_at: NOW,
        consecutive_failures: conn.health_status === 'unhealthy' ? 4 : 0,
      }),
    )
  }),
  http.get('/api/v1/connections/:name/secrets/:field', ({ params }) =>
    HttpResponse.json(
      successFor<typeof revealConnectionSecret>({
        field: String(params.field),
        value: 'revealed-secret-value',
      }),
    ),
  ),
]

export const emptyConnectionsList = [
  http.get('/api/v1/connections', () =>
    HttpResponse.json(successFor<typeof listConnections>([])),
  ),
]

// ── Default test handlers: empty list, minimal detail lookups. ──

export const connectionsHandlers = [
  http.get('/api/v1/connections', () =>
    HttpResponse.json(successFor<typeof listConnections>([])),
  ),
  http.get('/api/v1/connections/:name', ({ params }) =>
    HttpResponse.json(
      successFor<typeof getConnection>(buildConnection({ name: String(params.name) })),
    ),
  ),
  http.post('/api/v1/connections', async ({ request }) => {
    const body = (await request.json()) as {
      name?: string
      connection_type?: ConnectionType
    }
    if (!body.name) {
      return HttpResponse.json(apiError("Field 'name' is required"), { status: 400 })
    }
    return HttpResponse.json(
      successFor<typeof createConnection>(
        buildConnection({
          id: `conn-${body.name}`,
          name: body.name,
          connection_type: body.connection_type ?? 'generic_http',
        }),
      ),
      { status: 201 },
    )
  }),
  http.patch('/api/v1/connections/:name', async ({ params, request }) => {
    const body = (await request.json()) as Partial<Connection>
    return HttpResponse.json(
      successFor<typeof updateConnection>(
        buildConnection({ ...body, name: String(params.name), updated_at: NOW }),
      ),
    )
  }),
  http.delete('/api/v1/connections/:name', () => HttpResponse.json(voidSuccess())),
  http.get('/api/v1/connections/:name/health', ({ params }) =>
    HttpResponse.json(
      successFor<typeof checkConnectionHealth>({
        connection_name: String(params.name),
        status: 'healthy',
        latency_ms: 0,
        error_detail: null,
        checked_at: NOW,
        consecutive_failures: 0,
      }),
    ),
  ),
  http.get('/api/v1/connections/:name/secrets/:field', ({ params }) =>
    HttpResponse.json(
      successFor<typeof revealConnectionSecret>({
        field: String(params.field),
        value: 'mock-secret',
      }),
    ),
  ),
]
