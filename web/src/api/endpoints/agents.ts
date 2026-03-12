import { apiClient, unwrap, unwrapPaginated } from '../client'
import type { AgentConfig, AutonomyLevelRequest, AutonomyLevelResponse, PaginationParams } from '../types'

export async function listAgents(params?: PaginationParams) {
  const response = await apiClient.get('/agents', { params })
  return unwrapPaginated<AgentConfig>(response)
}

export async function getAgent(name: string): Promise<AgentConfig> {
  const response = await apiClient.get(`/agents/${name}`)
  return unwrap(response)
}

export async function getAutonomy(agentId: string): Promise<AutonomyLevelResponse> {
  const response = await apiClient.get(`/agents/${agentId}/autonomy`)
  return unwrap(response)
}

export async function setAutonomy(
  agentId: string,
  data: AutonomyLevelRequest,
): Promise<AutonomyLevelResponse> {
  const response = await apiClient.post(`/agents/${agentId}/autonomy`, data)
  return unwrap(response)
}
