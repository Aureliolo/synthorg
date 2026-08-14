import { apiClient, unwrap } from '../../client'
import type { ApiResponse } from '@/api/types/http'
import type {
  CapabilitySourceRefreshRequest,
  CapabilitySourceRowsRequest,
  CapabilitySourceSettingRequest,
  CapabilitySourcesResponse,
} from '@/api/types/providers'

const BASE = '/providers/capability-sources'

export async function listCapabilitySources(): Promise<CapabilitySourcesResponse> {
  const response = await apiClient.get<ApiResponse<CapabilitySourcesResponse>>(BASE)
  return unwrap(response)
}

export async function setCapabilitySource(
  label: string,
  data: CapabilitySourceSettingRequest,
): Promise<CapabilitySourcesResponse> {
  const response = await apiClient.put<ApiResponse<CapabilitySourcesResponse>>(
    `${BASE}/${encodeURIComponent(label)}`,
    data,
  )
  return unwrap(response)
}

export async function refreshCapabilitySource(
  label: string,
): Promise<CapabilitySourcesResponse> {
  const response = await apiClient.post<ApiResponse<CapabilitySourcesResponse>>(
    `${BASE}/${encodeURIComponent(label)}/refresh`,
    {},
  )
  return unwrap(response)
}

export async function refreshDueCapabilitySources(
  data: CapabilitySourceRefreshRequest,
): Promise<CapabilitySourcesResponse> {
  const response = await apiClient.post<ApiResponse<CapabilitySourcesResponse>>(
    `${BASE}/refresh`,
    data,
  )
  return unwrap(response)
}

export async function ingestCapabilitySourceRows(
  label: string,
  data: CapabilitySourceRowsRequest,
): Promise<CapabilitySourcesResponse> {
  const response = await apiClient.post<ApiResponse<CapabilitySourcesResponse>>(
    `${BASE}/${encodeURIComponent(label)}/rows`,
    data,
  )
  return unwrap(response)
}
