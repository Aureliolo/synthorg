import { apiClient, unwrap } from '../client'
import type { ApiResponse } from '../types/http'
import type {
  InitiateOAuthFlowRequest,
  OAuthInitiationResponse,
  OAuthTokenStatusResponse,
} from '../types/integrations'

export async function initiateOauth(
  data: InitiateOAuthFlowRequest,
): Promise<OAuthInitiationResponse> {
  const response = await apiClient.post<ApiResponse<OAuthInitiationResponse>>(
    '/oauth/initiate',
    data,
  )
  return unwrap(response)
}

export async function getOauthStatus(
  connectionName: string,
): Promise<OAuthTokenStatusResponse> {
  const response = await apiClient.get<ApiResponse<OAuthTokenStatusResponse>>(
    `/oauth/status/${encodeURIComponent(connectionName)}`,
  )
  return unwrap(response)
}
