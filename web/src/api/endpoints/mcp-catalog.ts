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
