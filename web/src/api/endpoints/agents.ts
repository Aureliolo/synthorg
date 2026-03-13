import { apiClient, unwrap, unwrapPaginated } from '../client'
import type { AgentConfig, ApiResponse, AutonomyLevelRequest, AutonomyLevelResponse, PaginatedResponse, PaginationParams } from '../types'

export async function listAgents(params?: PaginationParams) {
  const response = await apiClient.get<PaginatedResponse<AgentConfig>>('/agents', { params })
  return unwrapPaginated<AgentConfig>(response)
}

export async function getAgent(name: string): Promise<AgentConfig> {
  const response = await apiClient.get<ApiResponse<AgentConfig>>(`/agents/${encodeURIComponent(name)}`)
  return unwrap(response)
}

export async function getAutonomy(agentId: string): Promise<AutonomyLevelResponse> {
  const response = await apiClient.get<ApiResponse<AutonomyLevelResponse>>(`/agents/${encodeURIComponent(agentId)}/autonomy`)
  return unwrap(response)
}

export async function setAutonomy(
  agentId: string,
  data: AutonomyLevelRequest,
): Promise<AutonomyLevelResponse> {
  const response = await apiClient.post<ApiResponse<AutonomyLevelResponse>>(`/agents/${encodeURIComponent(agentId)}/autonomy`, data)
  return unwrap(response)
}
