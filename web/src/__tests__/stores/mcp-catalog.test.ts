import { http, HttpResponse } from 'msw'
import type { McpCatalogEntry } from '@/api/types/integrations'
import { useMcpCatalogStore } from '@/stores/mcp-catalog'
import { apiError, apiSuccess, paginatedFor, voidSuccess } from '@/mocks/handlers'
import type { browseMcpCatalog } from '@/api/endpoints/mcp-catalog'
import { server } from '@/test-setup'

const githubEntry: McpCatalogEntry = {
  id: 'github-mcp',
  name: 'GitHub',
  description: 'desc',
  npm_package: '@modelcontextprotocol/server-github',
  required_connection_type: 'github',
  transport: 'stdio',
  capabilities: ['repository_access', 'issue_management'],
  tags: ['vcs'],
}

const filesystemEntry: McpCatalogEntry = {
  id: 'filesystem-mcp',
  name: 'Filesystem',
  description: 'desc',
  npm_package: '@modelcontextprotocol/server-filesystem',
  required_connection_type: null,
  transport: 'stdio',
  capabilities: ['file_read'],
  tags: ['local'],
}

describe('useMcpCatalogStore', () => {
  beforeEach(() => {
    useMcpCatalogStore.getState().reset()
  })

  it('loads the catalog on fetchCatalog', async () => {
    const entries = [githubEntry, filesystemEntry]
    server.use(
      http.get('/api/v1/integrations/mcp/catalog', () =>
        HttpResponse.json(
          paginatedFor<typeof browseMcpCatalog>({
            data: entries,
            limit: entries.length,
            nextCursor: null,
            hasMore: false,
            pagination: {
              limit: entries.length,
              next_cursor: null,
              has_more: false,
            },
          }),
        ),
      ),
    )
    await useMcpCatalogStore.getState().fetchCatalog()
    expect(useMcpCatalogStore.getState().entries).toHaveLength(2)
  })

  it('startInstall moves the wizard to picking-connection for entries that need one', () => {
    useMcpCatalogStore.setState({ entries: [githubEntry] })
    useMcpCatalogStore.getState().startInstall('github-mcp')
    expect(useMcpCatalogStore.getState().installFlow).toBe('picking-connection')
    expect(useMcpCatalogStore.getState().installContext.entryId).toBe('github-mcp')
  })

  it('startInstall skips straight to installing for connectionless entries', () => {
    useMcpCatalogStore.setState({ entries: [filesystemEntry] })
    useMcpCatalogStore.getState().startInstall('filesystem-mcp')
    expect(useMcpCatalogStore.getState().installFlow).toBe('installing')
  })

  it('confirmInstall transitions to done on success and remembers the entry', async () => {
    useMcpCatalogStore.setState({ entries: [filesystemEntry] })
    useMcpCatalogStore.getState().startInstall('filesystem-mcp')
    server.use(
      http.post('/api/v1/integrations/mcp/catalog/install', () =>
        HttpResponse.json(
          apiSuccess({
            status: 'installed',
            server_name: 'Filesystem',
            catalog_entry_id: 'filesystem-mcp',
            tool_count: 1,
          }),
        ),
      ),
    )

    await useMcpCatalogStore.getState().confirmInstall()

    const state = useMcpCatalogStore.getState()
    expect(state.installFlow).toBe('done')
    expect(state.installedEntryIds.has('filesystem-mcp')).toBe(true)
  })

  it('confirmInstall transitions to error on failure', async () => {
    useMcpCatalogStore.setState({ entries: [filesystemEntry] })
    useMcpCatalogStore.getState().startInstall('filesystem-mcp')
    server.use(
      http.post('/api/v1/integrations/mcp/catalog/install', () =>
        HttpResponse.json(apiError('no connection')),
      ),
    )

    await useMcpCatalogStore.getState().confirmInstall()

    const state = useMcpCatalogStore.getState()
    expect(state.installFlow).toBe('error')
    expect(state.installContext.errorMessage).toBe('no connection')
  })

  it('uninstall clears the installed marker on success', async () => {
    useMcpCatalogStore.setState({
      installedEntryIds: new Set(['github-mcp']),
    })
    server.use(
      http.delete('/api/v1/integrations/mcp/catalog/install/:id', () =>
        HttpResponse.json(voidSuccess()),
      ),
    )

    const result = await useMcpCatalogStore
      .getState()
      .uninstall('github-mcp')

    expect(result).toBe(true)
    expect(
      useMcpCatalogStore.getState().installedEntryIds.has('github-mcp'),
    ).toBe(false)
  })

  it('resetInstall returns the wizard to idle', () => {
    useMcpCatalogStore.setState({ entries: [filesystemEntry] })
    useMcpCatalogStore.getState().startInstall('filesystem-mcp')
    useMcpCatalogStore.getState().resetInstall()
    expect(useMcpCatalogStore.getState().installFlow).toBe('idle')
  })
})
