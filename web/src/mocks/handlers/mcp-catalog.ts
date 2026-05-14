import { http, HttpResponse } from 'msw'
import type {
  browseMcpCatalog,
  getMcpCatalogEntry,
  installMcpServer,
  searchMcpCatalog,
  InstalledMcpEntry,
} from '@/api/endpoints/mcp-catalog'
import type { McpCatalogEntry } from '@/api/types/integrations'
import type { PaginatedResponse } from '@/api/types/http'
import { apiError, emptyPage, paginatedFor, successFor, voidSuccess } from './helpers'

export function buildMcpCatalogEntry(
  overrides: Partial<McpCatalogEntry> = {},
): McpCatalogEntry {
  return {
    id: 'mcp-default',
    name: 'Default MCP',
    description: 'Default MCP catalog entry',
    npm_package: null,
    required_connection_type: null,
    transport: 'stdio',
    capabilities: [],
    tags: [],
    ...overrides,
  }
}

// ── Storybook-facing named export (preserve populated catalog). ──
const mockCatalogEntries: McpCatalogEntry[] = [
  buildMcpCatalogEntry({
    id: 'github-mcp',
    name: 'GitHub',
    description: 'Read and write GitHub repositories, issues, pull requests, and actions',
    npm_package: '@modelcontextprotocol/server-github',
    required_connection_type: 'github',
    capabilities: ['repository_access', 'issue_management'],
    tags: ['vcs'],
  }),
  buildMcpCatalogEntry({
    id: 'slack-mcp',
    name: 'Slack',
    description: 'Send and receive Slack messages, manage channels and users',
    npm_package: '@modelcontextprotocol/server-slack',
    required_connection_type: 'slack',
    capabilities: ['messaging'],
    tags: ['communication'],
  }),
  buildMcpCatalogEntry({
    id: 'filesystem-mcp',
    name: 'Filesystem',
    description: 'Read, write, and manage files on the local filesystem',
    npm_package: '@modelcontextprotocol/server-filesystem',
    capabilities: ['file_read', 'file_write'],
    tags: ['filesystem'],
  }),
]

function _catalogPage(
  rows: readonly McpCatalogEntry[],
): import('@/api/client').PaginatedResult<McpCatalogEntry> {
  return {
    data: [...rows],
    limit: rows.length || 1,
    nextCursor: null,
    hasMore: false,
    pagination: {
      limit: rows.length || 1,
      next_cursor: null,
      has_more: false,
    },
  }
}

function _installedPage(
  rows: readonly InstalledMcpEntry[],
): PaginatedResponse<InstalledMcpEntry> {
  return {
    success: true,
    error: null,
    error_detail: null,
    data: [...rows],
    pagination: {
      limit: rows.length || 1,
      next_cursor: null,
      has_more: false,
    },
  }
}

export const mcpCatalogHandlers = [
  http.get('/api/v1/integrations/mcp/catalog/installed', () =>
    HttpResponse.json(_installedPage([])),
  ),
  http.get('/api/v1/integrations/mcp/catalog', () =>
    HttpResponse.json(paginatedFor<typeof browseMcpCatalog>(_catalogPage(mockCatalogEntries))),
  ),
  http.get('/api/v1/integrations/mcp/catalog/search', ({ request }) => {
    const url = new URL(request.url)
    const q = (url.searchParams.get('q') ?? '').toLowerCase()
    const matches = mockCatalogEntries.filter(
      (e) =>
        e.name.toLowerCase().includes(q) ||
        e.description.toLowerCase().includes(q) ||
        e.tags.some((t) => t.toLowerCase().includes(q)),
    )
    return HttpResponse.json(paginatedFor<typeof searchMcpCatalog>(_catalogPage(matches)))
  }),
  http.get('/api/v1/integrations/mcp/catalog/:entryId', ({ params }) => {
    const entry = mockCatalogEntries.find((e) => e.id === params.entryId)
    if (!entry) return HttpResponse.json(apiError('Catalog entry not found'), { status: 404 })
    return HttpResponse.json(successFor<typeof getMcpCatalogEntry>(entry))
  }),
  http.post('/api/v1/integrations/mcp/catalog/install', async ({ request }) => {
    const body = (await request.json()) as { catalog_entry_id?: string }
    if (!body.catalog_entry_id) {
      return HttpResponse.json(apiError("Field 'catalog_entry_id' is required"), {
        status: 400,
      })
    }
    const entry = mockCatalogEntries.find((e) => e.id === body.catalog_entry_id)
    if (!entry) return HttpResponse.json(apiError('Catalog entry not found'), { status: 404 })
    return HttpResponse.json(
      successFor<typeof installMcpServer>({
        status: 'installed',
        server_name: entry.name,
        catalog_entry_id: entry.id,
        tool_count: entry.capabilities.length,
      }),
    )
  }),
  http.delete('/api/v1/integrations/mcp/catalog/install/:entryId', () =>
    HttpResponse.json(voidSuccess()),
  ),
]

// ── Default test handlers: empty catalog + minimal entry lookups. ──

export const mcpCatalogDefaultHandlers = [
  http.get('/api/v1/integrations/mcp/catalog/installed', () =>
    HttpResponse.json(_installedPage([])),
  ),
  http.get('/api/v1/integrations/mcp/catalog', () =>
    HttpResponse.json(paginatedFor<typeof browseMcpCatalog>(emptyPage<McpCatalogEntry>())),
  ),
  http.get('/api/v1/integrations/mcp/catalog/search', () =>
    HttpResponse.json(paginatedFor<typeof searchMcpCatalog>(emptyPage<McpCatalogEntry>())),
  ),
  http.get('/api/v1/integrations/mcp/catalog/:entryId', ({ params }) =>
    HttpResponse.json(
      successFor<typeof getMcpCatalogEntry>(
        buildMcpCatalogEntry({ id: String(params.entryId), name: String(params.entryId) }),
      ),
    ),
  ),
  http.post('/api/v1/integrations/mcp/catalog/install', async ({ request }) => {
    const body = (await request.json()) as { catalog_entry_id?: string }
    return HttpResponse.json(
      successFor<typeof installMcpServer>({
        status: 'installed',
        server_name: body.catalog_entry_id ?? 'mcp-default',
        catalog_entry_id: body.catalog_entry_id ?? 'mcp-default',
        tool_count: 0,
      }),
    )
  }),
  http.delete('/api/v1/integrations/mcp/catalog/install/:entryId', () =>
    HttpResponse.json(voidSuccess()),
  ),
]
