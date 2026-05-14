import { apiClient, unwrap, unwrapPaginated, unwrapVoid, type PaginatedResult } from '../client'
import type { ApiResponse, PaginatedResponse, PaginationParams } from '../types/http'
import type {
  McpCatalogEntry,
  McpInstallRequest,
  McpInstallResponse,
} from '../types/integrations'

export async function browseMcpCatalog(
  params?: PaginationParams,
): Promise<PaginatedResult<McpCatalogEntry>> {
  const response = await apiClient.get<PaginatedResponse<McpCatalogEntry>>(
    '/integrations/mcp/catalog',
    { params },
  )
  return unwrapPaginated<McpCatalogEntry>(response)
}

export async function searchMcpCatalog(
  query: string,
  params?: PaginationParams,
): Promise<PaginatedResult<McpCatalogEntry>> {
  const response = await apiClient.get<PaginatedResponse<McpCatalogEntry>>(
    '/integrations/mcp/catalog/search',
    { params: { q: query, ...params } },
  )
  return unwrapPaginated<McpCatalogEntry>(response)
}

export async function getMcpCatalogEntry(entryId: string): Promise<McpCatalogEntry> {
  const response = await apiClient.get<ApiResponse<McpCatalogEntry>>(
    `/integrations/mcp/catalog/${encodeURIComponent(entryId)}`,
  )
  return unwrap(response)
}

export interface InstalledMcpEntry {
  readonly catalog_entry_id: string
  readonly connection_name: string | null
  readonly installed_at: string
}

/**
 * List MCP catalog entries currently installed on the backend.
 *
 * Walks the cursor pages so callers see every installed row in one
 * call -- the installed list is bounded by the bundled catalog
 * (~20-50 entries) so a single call covers every deployment.
 */
export async function listInstalledMcp(): Promise<readonly InstalledMcpEntry[]> {
  const collected: InstalledMcpEntry[] = []
  let cursor: string | null = null
  do {
    const params: Record<string, string> | undefined = cursor ? { cursor } : undefined
    const response = await apiClient.get<PaginatedResponse<InstalledMcpEntry>>(
      '/integrations/mcp/catalog/installed',
      { params },
    )
    const page: PaginatedResult<InstalledMcpEntry> = unwrapPaginated<InstalledMcpEntry>(response)
    collected.push(...page.data)
    cursor = page.hasMore ? page.nextCursor : null
  } while (cursor !== null)
  return collected
}

export async function installMcpServer(
  data: McpInstallRequest,
): Promise<McpInstallResponse> {
  const response = await apiClient.post<ApiResponse<McpInstallResponse>>(
    '/integrations/mcp/catalog/install',
    data,
  )
  return unwrap(response)
}

export async function uninstallMcpServer(entryId: string): Promise<void> {
  const response = await apiClient.delete<ApiResponse<null>>(
    `/integrations/mcp/catalog/install/${encodeURIComponent(entryId)}`,
  )
  unwrapVoid(response)
}
