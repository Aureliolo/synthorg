import { apiClient, unwrap, unwrapPaginated, type PaginatedResult } from '../client'
import type { ActiveAgentSummary, AgentHealthResponse } from '../types'
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

// Active registered agents WITH their stable runtime UUIDs. The
// config-sourced ``listAgents`` now carries the same id, so both
// surfaces address agents by one UUID. Backs the group-chat
// participant picker, which sends the selected ids to /meta/chat/group.
export async function listActiveAgents(): Promise<readonly ActiveAgentSummary[]> {
  const response =
    await apiClient.get<ApiResponse<readonly ActiveAgentSummary[]>>('/agents/active')
  return unwrap(response)
}

export async function getAgent(agentId: string): Promise<DashboardAgentConfig> {
  const response = await apiClient.get<ApiResponse<DashboardAgentConfig>>(`/agents/${encodeURIComponent(agentId)}`)
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

export async function getAgentPerformance(agentId: string): Promise<AgentPerformanceSummary> {
  const response = await apiClient.get<ApiResponse<AgentPerformanceSummary>>(
    `/agents/${encodeURIComponent(agentId)}/performance`,
  )
  return unwrap(response)
}

export async function getAgentActivity(
  agentId: string,
  params?: PaginationParams,
): Promise<PaginatedResult<AgentActivityEvent>> {
  const response = await apiClient.get<PaginatedResponse<AgentActivityEvent>>(
    `/agents/${encodeURIComponent(agentId)}/activity`,
    { params },
  )
  return unwrapPaginated<AgentActivityEvent>(response)
}

export async function getAgentHistory(agentId: string): Promise<readonly CareerEvent[]> {
  const response = await apiClient.get<ApiResponse<readonly CareerEvent[]>>(
    `/agents/${encodeURIComponent(agentId)}/history`,
  )
  return unwrap(response)
}

export async function getAgentHealth(agentId: string): Promise<AgentHealthResponse> {
  const response = await apiClient.get<ApiResponse<AgentHealthResponse>>(
    `/agents/${encodeURIComponent(agentId)}/health`,
  )
  return unwrap(response)
}
