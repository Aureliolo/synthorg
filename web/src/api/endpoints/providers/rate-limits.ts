import { apiClient, unwrap } from '../../client'
import type {
  ApiResponse,
  RateLimitsResponse,
  RateLimitsUpdateRequest,
} from '@/api/types'

export async function getProviderRateLimits(name: string): Promise<RateLimitsResponse> {
  const response = await apiClient.get<ApiResponse<RateLimitsResponse>>(
    `/providers/${encodeURIComponent(name)}/rate-limits`,
  )
  return unwrap(response)
}

export async function updateProviderRateLimits(
  name: string,
  data: RateLimitsUpdateRequest,
): Promise<RateLimitsResponse> {
  const response = await apiClient.patch<ApiResponse<RateLimitsResponse>>(
    `/providers/${encodeURIComponent(name)}/rate-limits`,
    data,
  )
  return unwrap(response)
}
