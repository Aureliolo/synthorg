import { http, HttpResponse } from 'msw'
// Value import (not `import type`): the test files in this suite
// consistently value-import endpoint symbols when they appear inside
// `paginatedFor<typeof X>(...)` calls. Both forms compile with TS6
// in this project, but the value import keeps the convention
// consistent with users.test.ts / agents.test.ts / etc.
import { listIntegrationHealth } from '@/api/endpoints/integration-health'
import type { Connection, HealthReport } from '@/api/types/integrations'
import { useConnectionsStore } from '@/stores/connections'
import {
  apiError,
  apiSuccess,
  emptyPage,
  pageEnvelope,
  paginatedFor,
  voidSuccess,
} from '@/mocks/handlers'
import type { PaginatedResult } from '@/api/client'
import { server } from '@/test-setup'

function singlePage(reports: readonly HealthReport[]): PaginatedResult<HealthReport> {
  // Match the endpoint default page size + the MSW mock's default
  // (web/src/mocks/handlers/integration-health.ts) so test fixtures
  // do not drift from the wire contract.
  const limit = 50
  return {
    data: [...reports],
    limit,
    nextCursor: null,
    hasMore: false,
    degradedSources: [],
    pagination: {
      limit,
      next_cursor: null,
      has_more: false,
    },
  }
}

const sampleConnection: Connection = {
  id: 'conn-primary-github',
  name: 'primary-github',
  connection_type: 'github',
  auth_method: 'bearer_token',
  base_url: 'https://api.github.com',
  health_check_enabled: true,
  health: { status: 'healthy', last_check_at: '2026-04-12T08:00:00Z' },
  metadata: {},
  rate_limiter: null,
  secret_refs: [],
  webhook_receipt_retention_days: null,
  sensitive: false,
  allowed_repos: [],
  created_at: '2026-04-01T09:00:00Z',
  updated_at: '2026-04-12T08:00:00Z',
}

const sampleReport: HealthReport = {
  connection_name: 'primary-github',
  status: 'healthy',
  latency_ms: 42,
  error_detail: null,
  checked_at: '2026-04-12T08:00:00Z',
  consecutive_failures: 0,
  webhook_ingest: 'ready',
}

describe('useConnectionsStore', () => {
  beforeEach(() => {
    useConnectionsStore.getState().reset()
  })

  it('fetches connections and merges health reports', async () => {
    server.use(
      http.get('/api/v1/connections', () =>
        HttpResponse.json(pageEnvelope([sampleConnection])),
      ),
      http.get('/api/v1/integrations/health', () =>
        HttpResponse.json(
          paginatedFor<typeof listIntegrationHealth>(singlePage([sampleReport])),
        ),
      ),
    )

    await useConnectionsStore.getState().fetchConnections()

    const state = useConnectionsStore.getState()
    expect(state.connections).toHaveLength(1)
    expect(state.healthMap['primary-github']).toEqual(sampleReport)
    expect(state.listLoading).toBe(false)
  })

  it('records an error message when the list call fails', async () => {
    server.use(
      http.get('/api/v1/connections', () =>
        HttpResponse.json(apiError('Network down')),
      ),
      http.get('/api/v1/integrations/health', () =>
        HttpResponse.json(
          paginatedFor<typeof listIntegrationHealth>(emptyPage<HealthReport>()),
        ),
      ),
    )

    await useConnectionsStore.getState().fetchConnections()

    expect(useConnectionsStore.getState().listError).toBe('Network down')
    expect(useConnectionsStore.getState().listLoading).toBe(false)
  })

  it('appends a new connection on create and forwards body', async () => {
    let capturedBody: unknown = null
    server.use(
      http.post('/api/v1/connections', async ({ request }) => {
        capturedBody = await request.json()
        return HttpResponse.json(apiSuccess(sampleConnection), { status: 201 })
      }),
    )

    const result = await useConnectionsStore.getState().createConnection({
      name: 'primary-github',
      connection_type: 'github',
      auth_method: 'bearer_token',
      credentials: { token: 'abc' },
      health_check_enabled: true,
      sensitive: false,
      allowed_repos: [],
    })

    expect(result).toEqual(sampleConnection)
    expect(capturedBody).toEqual({
      name: 'primary-github',
      connection_type: 'github',
      auth_method: 'bearer_token',
      credentials: { token: 'abc' },
      health_check_enabled: true,
      sensitive: false,
      allowed_repos: [],
    })
    expect(useConnectionsStore.getState().connections).toHaveLength(1)
  })

  it('fetches the connection-type registry once and caches it', async () => {
    let calls = 0
    server.use(
      http.get('/api/v1/connections/types', () => {
        calls += 1
        return HttpResponse.json(
          apiSuccess([
            {
              connection_type: 'github',
              default_auth_method: 'bearer_token',
              label: 'GitHub',
              description: '',
              required_field_names: ['token'],
              secret_field_names: ['token'],
              fields: [],
            },
          ]),
        )
      }),
    )

    await useConnectionsStore.getState().fetchConnectionTypes()
    await useConnectionsStore.getState().fetchConnectionTypes()

    expect(calls).toBe(1)
    expect(useConnectionsStore.getState().connectionTypes).toHaveLength(1)
    expect(useConnectionsStore.getState().connectionTypes[0]?.label).toBe('GitHub')
  })

  it('captures a secret out of band and returns only the opaque handle', async () => {
    let capturedBody: unknown = null
    server.use(
      http.post(
        '/api/v1/connections/drafts/:draftId/fields/:field/capture',
        async ({ request }) => {
          capturedBody = await request.json()
          return HttpResponse.json(apiSuccess({ handle: 'sech_abc123' }), {
            status: 201,
          })
        },
      ),
    )

    const handle = await useConnectionsStore
      .getState()
      .captureSecret('draft-1', 'token', 'ghp_secret_value', 'token')

    expect(handle).toBe('sech_abc123')
    // The raw value goes to the write-only endpoint, never into the store.
    expect(capturedBody).toEqual({ value: 'ghp_secret_value', secret_kind: 'token' })
  })

  it('returns null and toasts when a secret capture fails', async () => {
    const { useToastStore } = await import('@/stores/toast')
    useToastStore.getState().dismissAll()
    server.use(
      http.post(
        '/api/v1/connections/drafts/:draftId/fields/:field/capture',
        () => HttpResponse.json(apiError('capture unavailable'), { status: 503 }),
      ),
    )

    const handle = await useConnectionsStore
      .getState()
      .captureSecret('draft-1', 'token', 'ghp_secret_value', 'token')

    expect(handle).toBeNull()
    expect(useToastStore.getState().toasts).toHaveLength(1)
  })

  it('optimistically removes a connection on delete and rolls back on failure', async () => {
    useConnectionsStore.setState({ connections: [sampleConnection] })
    server.use(
      http.delete('/api/v1/connections/:name', () =>
        HttpResponse.json(apiError('boom')),
      ),
    )

    const result = await useConnectionsStore
      .getState()
      .deleteConnection('primary-github')

    expect(result).toBe(false)
    expect(useConnectionsStore.getState().connections).toHaveLength(1)
  })

  it('optimistically removes and keeps removed on delete success', async () => {
    useConnectionsStore.setState({ connections: [sampleConnection] })
    server.use(
      http.delete('/api/v1/connections/:name', () =>
        HttpResponse.json(voidSuccess()),
      ),
    )

    const result = await useConnectionsStore
      .getState()
      .deleteConnection('primary-github')

    expect(result).toBe(true)
    expect(useConnectionsStore.getState().connections).toHaveLength(0)
  })

  it('runs a health check and stores the latest report', async () => {
    server.use(
      http.get('/api/v1/connections/:name/health', () =>
        HttpResponse.json(
          apiSuccess({
            ...sampleReport,
            status: 'degraded',
            latency_ms: 900,
          }),
        ),
      ),
    )

    await useConnectionsStore.getState().runHealthCheck('primary-github')

    expect(
      useConnectionsStore.getState().healthMap['primary-github']?.status,
    ).toBe('degraded')
    expect(useConnectionsStore.getState().checkingHealth).not.toContain(
      'primary-github',
    )
  })

  it('emits an error toast and clears the spinner when the health probe fails', async () => {
    const { useToastStore } = await import('@/stores/toast')
    useToastStore.getState().dismissAll()
    server.use(
      http.get('/api/v1/connections/:name/health', () =>
        HttpResponse.json(apiError('Connection timeout'), { status: 504 }),
      ),
    )

    await useConnectionsStore.getState().runHealthCheck('primary-github')

    // Spinner cleared so the row is interactive again.
    expect(useConnectionsStore.getState().checkingHealth).not.toContain(
      'primary-github',
    )
    // Health map untouched so a stale prior report stays visible.
    expect(useConnectionsStore.getState().healthMap['primary-github']).toBeUndefined()
    // Toast surfaces the failure so the operator does not see only the
    // spinner disappear.
    const toasts = useToastStore.getState().toasts
    expect(toasts).toHaveLength(1)
    expect(toasts[0]!.variant).toBe('error')
    expect(toasts[0]!.title).toBe('Health check failed')
    expect(toasts[0]!.description).toContain('primary-github')
  })

  it('updates filters without touching list state', () => {
    useConnectionsStore.getState().setSearchQuery('github')
    useConnectionsStore.getState().setTypeFilter('github')
    useConnectionsStore.getState().setHealthFilter('healthy')

    const state = useConnectionsStore.getState()
    expect(state.searchQuery).toBe('github')
    expect(state.typeFilter).toBe('github')
    expect(state.healthFilter).toBe('healthy')
  })
})
