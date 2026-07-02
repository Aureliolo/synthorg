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
  /** Provider the next start uses; tracks the select-provider setting PUT. */
  provider: string
}

const TUNNEL_MOCK_URLS: Record<string, string> = {
  cloudflare: 'https://mock-tunnel.trycloudflare.com',
  ngrok: 'https://mock-tunnel.ngrok-free.app',
  devtunnels: 'https://mock-tunnel.devtunnels.ms',
}

const tunnelProviders = [
  {
    provider_id: 'cloudflare',
    display_name: 'Cloudflare quick tunnel',
    credential_kind: 'none',
    available: true,
    detail: null,
    credential_configured: true,
  },
  {
    provider_id: 'ngrok',
    display_name: 'ngrok',
    credential_kind: 'token',
    available: true,
    detail: null,
    credential_configured: false,
  },
  {
    provider_id: 'devtunnels',
    display_name: 'GitHub Dev Tunnels',
    credential_kind: 'device_login',
    available: false,
    detail: 'The devtunnel CLI is not installed.',
    credential_configured: false,
  },
]

export async function mockIntegrationRoutes(page: Page): Promise<void> {
  const tunnel: TunnelState = { publicUrl: null, provider: 'cloudflare' }

  // Provider selection persists via the settings API; track it so the
  // status / start mocks reflect the provider a test actually picked.
  await page.route(
    '**/api/v1/settings/integrations/tunnel_provider',
    async (route) => {
      if (route.request().method() === 'PUT') {
        const body = route.request().postDataJSON() as { value?: unknown }
        if (typeof body.value === 'string' && body.value) {
          tunnel.provider = body.value
        }
      }
      await route.fulfill({
        json: apiSuccess({
          namespace: 'integrations',
          key: 'tunnel_provider',
          value: tunnel.provider,
        }),
      })
    },
  )

  // Broad ``connections**`` so the paginated list request
  // (``/connections?limit=50``) matches too; the narrower health route
  // below still wins for its URLs (Playwright matches routes LIFO).
  await page.route('**/api/v1/connections**', (route) =>
    route.fulfill({
      json: {
        ...apiSuccess(mockConnections),
        pagination: { total: mockConnections.length, offset: 0, limit: 50 },
      },
    }),
  )
  await page.route('**/api/v1/connections/*/health', (route) =>
    route.fulfill({ json: apiSuccess(mockHealthReports[0]) }),
  )
  await page.route('**/api/v1/integrations/health/', (route) =>
    route.fulfill({ json: apiSuccess(mockHealthReports) }),
  )
  // Broad ``catalog**`` so the paginated request (``?limit=``) matches;
  // the narrower search / install / installed routes below win (LIFO).
  await page.route('**/api/v1/integrations/mcp/catalog**', (route) =>
    route.fulfill({
      json: {
        ...apiSuccess(mockCatalog),
        pagination: { total: mockCatalog.length, offset: 0, limit: 50 },
      },
    }),
  )
  await page.route('**/api/v1/integrations/mcp/catalog/installed**', (route) =>
    route.fulfill({ json: apiSuccess([]) }),
  )
  await page.route('**/api/v1/integrations/mcp/catalog/search**', (route) => {
    const url = new URL(route.request().url())
    const q = (url.searchParams.get('q') ?? '').toLowerCase()
    const matches = mockCatalog.filter(
      (e) =>
        e.name.toLowerCase().includes(q) ||
        e.description.toLowerCase().includes(q),
    )
    return route.fulfill({
      json: {
        ...apiSuccess(matches),
        pagination: { total: matches.length, offset: 0, limit: 50 },
      },
    })
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
    route.fulfill({
      json: apiSuccess({
        public_url: tunnel.publicUrl,
        selected_provider: tunnel.provider,
        active_provider: tunnel.publicUrl ? tunnel.provider : null,
        providers: tunnelProviders,
      }),
    }),
  )
  await page.route('**/api/v1/integrations/tunnel/start', (route) => {
    tunnel.publicUrl =
      TUNNEL_MOCK_URLS[tunnel.provider] ?? TUNNEL_MOCK_URLS['cloudflare'] ?? null
    return route.fulfill({
      json: apiSuccess({ public_url: tunnel.publicUrl, provider: tunnel.provider }),
    })
  })
  await page.route('**/api/v1/integrations/tunnel/stop', (route) => {
    tunnel.publicUrl = null
    return route.fulfill({ json: apiSuccess(null) })
  })
  await page.route('**/api/v1/integrations/tunnel/credential', (route) =>
    route.fulfill({ json: apiSuccess(null) }),
  )
  await page.route('**/api/v1/integrations/tunnel/device-login', (route) =>
    route.fulfill({
      json: apiSuccess({
        verification_uri: 'https://github.com/login/device',
        user_code: 'MOCK-CODE',
        already_logged_in: false,
      }),
    }),
  )
}
