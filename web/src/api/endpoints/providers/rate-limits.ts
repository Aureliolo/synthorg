import { apiClient, unwrap } from '../../client'
import type {
  ApiResponse,
  RateLimitsConfig,
  RateLimitsUpdateRequest,
} from '@/api/types'

export async function getProviderRateLimits(name: string): Promise<RateLimitsConfig> {
  const response = await apiClient.get<ApiResponse<RateLimitsConfig>>(
    `/providers/${encodeURIComponent(name)}/rate-limits`,
  )
  return unwrap(response)
}

export async function updateProviderRateLimits(
  name: string,
  data: RateLimitsUpdateRequest,
): Promise<RateLimitsConfig> {
  const response = await apiClient.patch<ApiResponse<RateLimitsConfig>>(
    `/providers/${encodeURIComponent(name)}/rate-limits`,
    data,
  )
  return unwrap(response)
}
