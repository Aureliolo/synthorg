import { apiClient, unwrap } from '../../client'
import type { ApiResponse } from '../../types/http'
import type {
  CredentialsRotateRequest,
  ProviderConfig,
} from '../../types/providers'

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
