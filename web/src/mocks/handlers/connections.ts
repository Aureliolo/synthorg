import { http, HttpResponse } from 'msw'
import type {
  captureConnectionSecret,
  checkConnectionHealth,
  createConnection,
  getConnection,
  getConnectionTypes,
  revealConnectionSecret,
  scanAccessibleRepos,
  updateConnection,
} from '@/api/endpoints/connections'
import type {
  Connection,
  ConnectionType,
  ConnectionTypeMetadata,
} from '@/api/types/integrations'
import {
  apiError,
  emptyPageEnvelope,
  pageEnvelope,
  successFor,
  voidSuccess,
} from './helpers'

const NOW = '2026-04-11T12:00:00Z'

// A small representative slice of the backend connection-type registry: a
// bearer-token type (github, one secret field) and a database type (a select
// dialect plus a masked password). Enough to exercise the metadata-driven form
// and the out-of-band capture path without duplicating the full backend map.
const mockConnectionTypes: ConnectionTypeMetadata[] = [
  {
    connection_type: 'github',
    default_auth_method: 'bearer_token',
    label: 'GitHub',
    description: 'Access GitHub repositories, issues, and pull requests.',
    required_field_names: ['token'],
    secret_field_names: ['token', 'signing_secret'],
    // Matches the backend registry, where github declares an optional
    // signing_secret so inbound ingest can authenticate a delivery to it.
    webhook_secret_field: 'signing_secret',
    fields: [
      {
        name: 'base_url',
        label: 'API URL',
        input_type: 'url',
        placement: 'base_url',
        required: false,
        secret: false,
        options: [],
        placeholder: 'https://api.github.com',
        help_text: 'Leave blank for github.com',
        capture_mode: null,
        visible_when: null,
        required_when: null,
      },
      {
        name: 'token',
        label: 'Personal Access Token',
        input_type: 'password',
        placement: 'credential',
        required: true,
        secret: true,
        options: [],
        placeholder: 'ghp_...',
        help_text: '',
        capture_mode: 'masked_field',
        visible_when: null,
        required_when: null,
      },
      {
        name: 'signing_secret',
        label: 'Webhook Secret',
        input_type: 'password',
        placement: 'credential',
        required: false,
        secret: true,
        options: [],
        placeholder: '',
        help_text: 'Set to receive inbound webhooks.',
        capture_mode: 'masked_field',
        visible_when: null,
        required_when: null,
      },
    ],
  },
  {
    connection_type: 'database',
    default_auth_method: 'basic_auth',
    label: 'Database',
    description: 'Connect to a SQL database.',
    required_field_names: ['dialect'],
    secret_field_names: ['password'],
    webhook_secret_field: null,
    fields: [
      {
        name: 'dialect',
        label: 'Dialect',
        input_type: 'select',
        placement: 'credential',
        required: true,
        secret: false,
        options: ['postgres', 'mysql', 'sqlite'],
        placeholder: '',
        help_text: '',
        capture_mode: null,
        visible_when: null,
        required_when: null,
      },
      {
        name: 'password',
        label: 'Password',
        input_type: 'password',
        placement: 'credential',
        required: false,
        secret: true,
        options: [],
        placeholder: '',
        help_text: '',
        capture_mode: 'masked_field',
        visible_when: null,
        required_when: null,
      },
    ],
  },
]

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
    health: { status: 'unknown', last_check_at: null },
    metadata: {},
    rate_limiter: null,
    secret_refs: [],
    webhook_receipt_retention_days: null,
    sensitive: false,
    allowed_repos: [],
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
    health: { status: 'healthy', last_check_at: NOW },
    created_at: '2026-04-01T09:00:00Z',
  }),
  buildConnection({
    id: 'conn-000000000002',
    name: 'dev-slack',
    connection_type: 'slack',
    auth_method: 'bearer_token',
    health: { status: 'degraded', last_check_at: NOW },
    created_at: '2026-04-02T10:30:00Z',
  }),
  buildConnection({
    id: 'conn-000000000003',
    name: 'ops-smtp',
    connection_type: 'smtp',
    auth_method: 'basic_auth',
    health: { status: 'unhealthy', last_check_at: NOW },
    created_at: '2026-04-03T11:15:00Z',
  }),
  buildConnection({
    id: 'conn-000000000004',
    name: 'reporting-db',
    connection_type: 'database',
    auth_method: 'basic_auth',
    health: { status: 'healthy', last_check_at: NOW },
    created_at: '2026-04-04T08:00:00Z',
  }),
  buildConnection({
    id: 'conn-000000000005',
    name: 'billing-api',
    connection_type: 'generic_http',
    auth_method: 'api_key',
    base_url: 'https://billing.example.com',
    health: { status: 'unknown', last_check_at: null },
    created_at: '2026-04-05T14:20:00Z',
  }),
  buildConnection({
    id: 'conn-000000000006',
    name: 'gh-oauth-app',
    connection_type: 'oauth_app',
    auth_method: 'oauth2',
    health_check_enabled: false,
    health: { status: 'unknown', last_check_at: null },
    created_at: '2026-04-06T09:45:00Z',
  }),
]

export const connectionsList = [
  http.get('/api/v1/connections', () =>
    HttpResponse.json(pageEnvelope(mockConnections)),
  ),
  // Registered before ``/connections/:name`` so ``types`` is not captured as a
  // connection name.
  http.get('/api/v1/connections/types', () =>
    HttpResponse.json(successFor<typeof getConnectionTypes>(mockConnectionTypes)),
  ),
  http.post(
    '/api/v1/connections/drafts/:draftId/fields/:field/capture',
    () =>
      HttpResponse.json(
        successFor<typeof captureConnectionSecret>({ handle: 'sech_mock_handle_0001' }),
        { status: 201 },
      ),
  ),
  http.get('/api/v1/connections/:name/accessible-repos', () =>
    HttpResponse.json(
      successFor<typeof scanAccessibleRepos>([
        { owner: 'acme', repo: 'proj-1', permission: 'admin', private: true },
        { owner: 'acme', repo: 'proj-2', permission: 'write', private: false },
      ]),
    ),
  ),
  http.get('/api/v1/connections/:name', ({ params }) => {
    const conn = mockConnections.find((c) => c.name === params['name'])
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
          id: `conn-${body.name}`,
          name: body.name,
          connection_type: (body.connection_type ?? 'github'),
        }),
      ),
      { status: 201 },
    )
  }),
  http.patch('/api/v1/connections/:name', ({ params }) => {
    const conn = mockConnections.find((c) => c.name === params['name'])
    if (!conn) return HttpResponse.json(apiError('Connection not found'), { status: 404 })
    return HttpResponse.json(
      successFor<typeof updateConnection>({ ...conn, updated_at: NOW }),
    )
  }),
  http.delete('/api/v1/connections/:name', () => HttpResponse.json(voidSuccess())),
  http.get('/api/v1/connections/:name/health', ({ params }) => {
    const conn = mockConnections.find((c) => c.name === params['name'])
    if (!conn) return HttpResponse.json(apiError('Connection not found'), { status: 404 })
    return HttpResponse.json(
      successFor<typeof checkConnectionHealth>({
        connection_name: conn.name,
        status: conn.health.status,
        latency_ms: conn.health.status === 'healthy' ? 42 : null,
        error_detail: conn.health.status === 'unhealthy' ? 'Connection refused' : null,
        checked_at: NOW,
        consecutive_failures: conn.health.status === 'unhealthy' ? 4 : 0,
      }),
    )
  }),
  http.get('/api/v1/connections/:name/secrets/:field', ({ params }) =>
    HttpResponse.json(
      successFor<typeof revealConnectionSecret>({
        field: String(params['field']),
        value: 'revealed-secret-value',
      }),
    ),
  ),
]

// ── Default test handlers: empty list, minimal detail lookups. ──

export const connectionsHandlers = [
  http.get('/api/v1/connections', () =>
    HttpResponse.json(emptyPageEnvelope<Connection>()),
  ),
  http.get('/api/v1/connections/types', () =>
    HttpResponse.json(successFor<typeof getConnectionTypes>(mockConnectionTypes)),
  ),
  http.post(
    '/api/v1/connections/drafts/:draftId/fields/:field/capture',
    () =>
      HttpResponse.json(
        successFor<typeof captureConnectionSecret>({ handle: 'sech_mock_handle_0001' }),
        { status: 201 },
      ),
  ),
  http.get('/api/v1/connections/:name/accessible-repos', () =>
    HttpResponse.json(
      successFor<typeof scanAccessibleRepos>([
        { owner: 'acme', repo: 'proj-1', permission: 'admin', private: true },
        { owner: 'acme', repo: 'proj-2', permission: 'write', private: false },
      ]),
    ),
  ),
  http.get('/api/v1/connections/:name', ({ params }) =>
    HttpResponse.json(
      successFor<typeof getConnection>(buildConnection({ name: String(params['name']) })),
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
        buildConnection({ ...body, name: String(params['name']), updated_at: NOW }),
      ),
    )
  }),
  http.delete('/api/v1/connections/:name', () => HttpResponse.json(voidSuccess())),
  http.get('/api/v1/connections/:name/health', ({ params }) =>
    HttpResponse.json(
      successFor<typeof checkConnectionHealth>({
        connection_name: String(params['name']),
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
        field: String(params['field']),
        value: 'mock-secret',
      }),
    ),
  ),
]
