import type { Page } from '@playwright/test'

const apiSuccess = <T>(data: T) => ({
  data,
  error: null,
  error_detail: null,
  success: true,
})

const NOW = '2026-04-12T08:00:00Z'

const mockConnections = [
  {
    name: 'primary-github',
    connection_type: 'github',
    auth_method: 'bearer_token',
    base_url: 'https://api.github.com',
    health_check_enabled: true,
    health: { status: 'healthy', last_check_at: NOW },
    metadata: {},
    created_at: '2026-04-01T09:00:00Z',
    updated_at: NOW,
  },
  {
    name: 'dev-slack',
    connection_type: 'slack',
    auth_method: 'bearer_token',
    base_url: null,
    health_check_enabled: true,
    health: { status: 'degraded', last_check_at: NOW },
    metadata: {},
    created_at: '2026-04-02T10:30:00Z',
    updated_at: NOW,
  },
]

const mockHealthReports = mockConnections.map((c) => ({
  connection_name: c.name,
  status: c.health.status,
  latency_ms: 42,
  error_detail: null,
  checked_at: NOW,
  consecutive_failures: 0,
}))

const mockCatalog = [
  {
    id: 'filesystem-mcp',
    name: 'Filesystem',
    description: 'Read, write, and manage files on the local filesystem',
    npm_package: '@modelcontextprotocol/server-filesystem',
    required_connection_type: null,
    transport: 'stdio',
    capabilities: ['file_read', 'file_write', 'directory_listing'],
    tags: ['filesystem', 'local'],
  },
  {
    id: 'github-mcp',
    name: 'GitHub',
    description: 'Read and write GitHub repositories',
    npm_package: '@modelcontextprotocol/server-github',
    required_connection_type: 'github',
    transport: 'stdio',
    capabilities: ['repository_access', 'issue_management'],
    tags: ['vcs'],
  },
]

interface TunnelState {
  publicUrl: string | null
}

export async function mockIntegrationRoutes(page: Page): Promise<void> {
  const tunnel: TunnelState = { publicUrl: null }

  await page.route('**/api/v1/connections/', (route) =>
    route.fulfill({ json: apiSuccess(mockConnections) }),
  )
  await page.route('**/api/v1/connections/*/health', (route) =>
    route.fulfill({ json: apiSuccess(mockHealthReports[0]) }),
  )
  await page.route('**/api/v1/integrations/health/', (route) =>
    route.fulfill({ json: apiSuccess(mockHealthReports) }),
  )
  await page.route('**/api/v1/integrations/mcp/catalog', (route) =>
    route.fulfill({ json: apiSuccess(mockCatalog) }),
  )
  await page.route('**/api/v1/integrations/mcp/catalog/search**', (route) => {
    const url = new URL(route.request().url())
    const q = (url.searchParams.get('q') ?? '').toLowerCase()
    const matches = mockCatalog.filter(
      (e) =>
        e.name.toLowerCase().includes(q) ||
        e.description.toLowerCase().includes(q),
    )
    return route.fulfill({ json: apiSuccess(matches) })
  })
  await page.route('**/api/v1/integrations/mcp/catalog/install', (route) =>
    route.fulfill({
      json: apiSuccess({
        status: 'installed',
        server_name: 'Filesystem',
        catalog_entry_id: 'filesystem-mcp',
        tool_count: 3,
      }),
    }),
  )
  await page.route('**/api/v1/integrations/tunnel/status', (route) =>
    route.fulfill({ json: apiSuccess({ public_url: tunnel.publicUrl }) }),
  )
  await page.route('**/api/v1/integrations/tunnel/start', (route) => {
    tunnel.publicUrl = 'https://mock-tunnel.ngrok.io'
    return route.fulfill({ json: apiSuccess({ public_url: tunnel.publicUrl }) })
  })
  await page.route('**/api/v1/integrations/tunnel/stop', (route) => {
    tunnel.publicUrl = null
    return route.fulfill({ json: apiSuccess(null) })
  })
}
