import { apiClient, unwrap, unwrapVoid } from '../client'
import type { ApiResponse } from '../types/http'
import type {
  DeviceLoginPrompt,
  TunnelSnapshot,
  TunnelStartResponse,
} from '../types/integrations'

export async function getTunnelStatus(): Promise<TunnelSnapshot> {
  const response = await apiClient.get<ApiResponse<TunnelSnapshot>>(
    '/integrations/tunnel/status',
  )
  return unwrap(response)
}

export async function startTunnel(): Promise<TunnelStartResponse> {
  const response = await apiClient.post<ApiResponse<TunnelStartResponse>>(
    '/integrations/tunnel/start',
  )
  return unwrap(response)
}

export async function stopTunnel(): Promise<void> {
  const response = await apiClient.post<ApiResponse<null>>(
    '/integrations/tunnel/stop',
  )
  unwrapVoid(response)
}

export async function putTunnelCredential(
  provider: string,
  token: string,
): Promise<void> {
  const response = await apiClient.put<ApiResponse<null>>(
    '/integrations/tunnel/credential',
    { provider, token },
  )
  unwrapVoid(response)
}

export async function deleteTunnelCredential(provider: string): Promise<void> {
  const response = await apiClient.delete<ApiResponse<null>>(
    `/integrations/tunnel/credential/${encodeURIComponent(provider)}`,
  )
  unwrapVoid(response)
}

export async function beginTunnelDeviceLogin(
  provider: string,
): Promise<DeviceLoginPrompt> {
  const response = await apiClient.post<ApiResponse<DeviceLoginPrompt>>(
    '/integrations/tunnel/device-login',
    { provider },
  )
  return unwrap(response)
}
