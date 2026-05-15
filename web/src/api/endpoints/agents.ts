import { apiClient, unwrap, unwrapPaginated, type PaginatedResult } from '../client'
import type {
  AgentActivityEvent,
  AgentPerformanceSummary,
  CareerEvent,
  DashboardAgentConfig,
} from '../types/agents'
import type { ApiResponse, PaginatedResponse, PaginationParams } from '../types/http'
import type { AutonomyLevelRequest, AutonomyLevelResponse } from '../types/system'

export async function listAgents(params?: PaginationParams): Promise<PaginatedResult<DashboardAgentConfig>> {
  const response = await apiClient.get<PaginatedResponse<DashboardAgentConfig>>('/agents', { params })
  return unwrapPaginated<DashboardAgentConfig>(response)
}

export async function getAgent(name: string): Promise<DashboardAgentConfig> {
  const response = await apiClient.get<ApiResponse<DashboardAgentConfig>>(`/agents/${encodeURIComponent(name)}`)
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

export async function getAgentPerformance(name: string): Promise<AgentPerformanceSummary> {
  const response = await apiClient.get<ApiResponse<AgentPerformanceSummary>>(
    `/agents/${encodeURIComponent(name)}/performance`,
  )
  return unwrap(response)
}

export async function getAgentActivity(
  name: string,
  params?: PaginationParams,
): Promise<PaginatedResult<AgentActivityEvent>> {
  const response = await apiClient.get<PaginatedResponse<AgentActivityEvent>>(
    `/agents/${encodeURIComponent(name)}/activity`,
    { params },
  )
  return unwrapPaginated<AgentActivityEvent>(response)
}

export async function getAgentHistory(name: string): Promise<readonly CareerEvent[]> {
  const response = await apiClient.get<ApiResponse<readonly CareerEvent[]>>(
    `/agents/${encodeURIComponent(name)}/history`,
  )
  return unwrap(response)
}
