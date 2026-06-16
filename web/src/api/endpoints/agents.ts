import { apiClient, unwrap, unwrapPaginated, type PaginatedResult } from '../client'
import type { VersionDiffResponse } from './version-history'
import type {
  ActiveAgentSummary,
  AgentHealthResponse,
  AgentIdentity,
  AgentIdentityDiff,
  RollbackAgentIdentityRequest,
} from '../types'
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

/**
 * Roll an agent identity back to a prior snapshot version.
 *
 * Posts to the agent-identity rollback route (``POST
 * /agents/{id}/versions/rollback``) and returns the freshly-restored
 * ``AgentIdentity`` (a new version N+1 whose content matches the
 * target). The backend audits the rollback under ``reason``.
 */
export async function rollbackAgentIdentity(
  agentId: string,
  data: RollbackAgentIdentityRequest,
): Promise<AgentIdentity> {
  const response = await apiClient.post<ApiResponse<AgentIdentity>>(
    `/agents/${encodeURIComponent(agentId)}/versions/rollback`,
    data,
  )
  return unwrap(response)
}

/**
 * Diff two agent-identity versions, normalised for the shared diff
 * drawer.
 *
 * Calls ``GET /agents/{id}/versions/diff`` (returns
 * ``AgentIdentityDiff`` with ``field_changes``) and flattens each field
 * change into the cross-domain ``VersionDiffResponse`` shape
 * (``entries[].{path, before, after}``) the drawer renders.
 */
export async function diffAgentIdentityVersions(
  agentId: string,
  fromVersion: number,
  toVersion: number,
): Promise<VersionDiffResponse> {
  const response = await apiClient.get<ApiResponse<AgentIdentityDiff>>(
    `/agents/${encodeURIComponent(agentId)}/versions/diff`,
    { params: { from_version: fromVersion, to_version: toVersion } },
  )
  const diff = unwrap(response)
  return {
    from_version: diff.from_version,
    to_version: diff.to_version,
    entries: diff.field_changes.map((change) => ({
      path: change.field_path,
      before: change.old_value,
      after: change.new_value,
    })),
  }
}
