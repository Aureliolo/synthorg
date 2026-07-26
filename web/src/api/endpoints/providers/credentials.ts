import { apiClient, unwrap } from '../../client'
import type { ApiResponse } from '@/api/types/http'
import type { CredentialsRotateRequest, ProviderConfig } from '@/api/types/providers'

export async function rotateProviderCredentials(
  name: string,
  data: CredentialsRotateRequest,
): Promise<ProviderConfig> {
  const response = await apiClient.post<ApiResponse<ProviderConfig>>(
    `/providers/${encodeURIComponent(name)}/credentials/rotate`,
    data,
  )
  return unwrap(response)
}
