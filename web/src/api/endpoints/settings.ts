import { apiClient, paginateAll, unwrap, unwrapPaginated, unwrapVoid, type PaginatedResult } from '../client'
import type { ApiResponse, PaginatedResponse, PaginationParams } from '../types/http'
import type {
  SecurityConfigExportResponse,
  SecurityConfigImportRequest,
  SettingDefinition,
  SettingEntry,
  SettingNamespace,
  SinkInfo,
  TestSinkResult,
  UpdateSettingRequest,
} from '../types/settings'

export async function getSchema(): Promise<SettingDefinition[]> {
  const response = await apiClient.get<ApiResponse<SettingDefinition[]>>('/settings/_schema')
  return unwrap(response)
}

export async function getNamespaceSchema(namespace: SettingNamespace): Promise<SettingDefinition[]> {
  const response = await apiClient.get<ApiResponse<SettingDefinition[]>>(
    `/settings/_schema/${encodeURIComponent(namespace)}`,
  )
  return unwrap(response)
}

export async function getAllSettings(
  params?: PaginationParams,
): Promise<PaginatedResult<SettingEntry>> {
  const response = await apiClient.get<PaginatedResponse<SettingEntry>>('/settings', { params })
  return unwrapPaginated<SettingEntry>(response)
}

export async function getNamespaceSettings(namespace: SettingNamespace): Promise<SettingEntry[]> {
  const response = await apiClient.get<ApiResponse<SettingEntry[]>>(
    `/settings/${encodeURIComponent(namespace)}`,
  )
  return unwrap(response)
}

export async function updateSetting(
  namespace: SettingNamespace,
  key: string,
  data: UpdateSettingRequest,
): Promise<SettingEntry> {
  const response = await apiClient.put<ApiResponse<SettingEntry>>(
    `/settings/${encodeURIComponent(namespace)}/${encodeURIComponent(key)}`,
    data,
  )
  return unwrap(response)
}

export async function resetSetting(namespace: SettingNamespace, key: string): Promise<void> {
  const response = await apiClient.delete<ApiResponse<null>>(
    `/settings/${encodeURIComponent(namespace)}/${encodeURIComponent(key)}`,
  )
  unwrapVoid(response)
}

export async function listSinks(): Promise<SinkInfo[]> {
  return paginateAll<SinkInfo>(async (cursor) => {
    const params = new URLSearchParams()
    if (cursor) params.set('cursor', cursor)
    const qs = params.toString()
    const url = qs ? `/settings/observability/sinks?${qs}` : '/settings/observability/sinks'
    const response = await apiClient.get<PaginatedResponse<SinkInfo>>(url)
    return unwrapPaginated<SinkInfo>(response)
  })
}

export async function testSinkConfig(data: {
  sink_overrides: string
  custom_sinks: string
}): Promise<TestSinkResult> {
  const response = await apiClient.post<ApiResponse<TestSinkResult>>(
    '/settings/observability/sinks/_test',
    data,
  )
  return unwrap(response)
}

export async function exportSecurityConfig(): Promise<SecurityConfigExportResponse> {
  const response = await apiClient.get<ApiResponse<SecurityConfigExportResponse>>(
    '/settings/security/export',
  )
  return unwrap(response)
}

export async function importSecurityConfig(
  data: SecurityConfigImportRequest,
): Promise<SecurityConfigExportResponse> {
  const response = await apiClient.post<ApiResponse<SecurityConfigExportResponse>>(
    '/settings/security/import',
    data,
  )
  return unwrap(response)
}
