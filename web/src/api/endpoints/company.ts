import { apiClient, unwrap, unwrapPaginated, unwrapVoid, type PaginatedResult } from '../client'
import type { AgentConfig } from '../types/agents'
import type { DepartmentHealth } from '../types/analytics'
import type { ApiResponse, PaginatedResponse, PaginationParams } from '../types/http'
import type {
  CompanyConfig,
  CreateAgentOrgRequest,
  CreateDepartmentRequest,
  CreateTeamRequest,
  Department,
  ReorderAgentsRequest,
  ReorderDepartmentsRequest,
  ReorderTeamsRequest,
  TeamConfig,
  UpdateAgentOrgRequest,
  UpdateCompanyRequest,
  UpdateDepartmentRequest,
  UpdateTeamRequest,
} from '../types/org'

export async function getCompanyConfig(): Promise<CompanyConfig> {
  const response = await apiClient.get<ApiResponse<CompanyConfig>>('/company')
  return unwrap(response)
}

export async function listDepartments(params?: PaginationParams): Promise<PaginatedResult<Department>> {
  const response = await apiClient.get<PaginatedResponse<Department>>('/departments', { params })
  return unwrapPaginated<Department>(response)
}

export async function getDepartment(name: string): Promise<Department> {
  const response = await apiClient.get<ApiResponse<Department>>(`/departments/${encodeURIComponent(name)}`)
  return unwrap(response)
}

export async function getDepartmentHealth(name: string): Promise<DepartmentHealth> {
  const response = await apiClient.get<ApiResponse<DepartmentHealth>>(
    `/departments/${encodeURIComponent(name)}/health`,
  )
  return unwrap(response)
}

// ── Mutations ────────────────────────────────────────────────

export async function updateCompany(data: UpdateCompanyRequest): Promise<Partial<CompanyConfig>> {
  const response = await apiClient.patch<ApiResponse<Partial<CompanyConfig>>>('/company', data)
  return unwrap(response)
}

export async function createDepartment(data: CreateDepartmentRequest): Promise<Department> {
  const response = await apiClient.post<ApiResponse<Department>>('/departments', data)
  return unwrap(response)
}

export async function updateDepartment(name: string, data: UpdateDepartmentRequest): Promise<Department> {
  const response = await apiClient.patch<ApiResponse<Department>>(
    `/departments/${encodeURIComponent(name)}`,
    data,
  )
  return unwrap(response)
}

export async function deleteDepartment(name: string): Promise<void> {
  const response = await apiClient.delete<ApiResponse<null>>(
    `/departments/${encodeURIComponent(name)}`,
  )
  unwrapVoid(response)
}

export async function reorderDepartments(data: ReorderDepartmentsRequest): Promise<readonly Department[]> {
  const response = await apiClient.post<ApiResponse<readonly Department[]>>(
    '/company/reorder-departments',
    data,
  )
  return unwrap(response)
}

export async function createAgentOrg(data: CreateAgentOrgRequest): Promise<AgentConfig> {
  const response = await apiClient.post<ApiResponse<AgentConfig>>('/agents', data)
  return unwrap(response)
}

export async function updateAgentOrg(agentId: string, data: UpdateAgentOrgRequest): Promise<AgentConfig> {
  const response = await apiClient.patch<ApiResponse<AgentConfig>>(
    `/agents/${encodeURIComponent(agentId)}`,
    data,
  )
  return unwrap(response)
}

export async function deleteAgent(agentId: string): Promise<void> {
  const response = await apiClient.delete<ApiResponse<null>>(
    `/agents/${encodeURIComponent(agentId)}`,
  )
  unwrapVoid(response)
}

export async function reorderAgents(departmentName: string, data: ReorderAgentsRequest): Promise<readonly AgentConfig[]> {
  const response = await apiClient.post<ApiResponse<readonly AgentConfig[]>>(
    `/departments/${encodeURIComponent(departmentName)}/reorder-agents`,
    data,
  )
  return unwrap(response)
}

// ── Team CRUD ──────────────────────────────────────────────

export async function createTeam(deptName: string, data: CreateTeamRequest): Promise<TeamConfig> {
  const response = await apiClient.post<ApiResponse<TeamConfig>>(
    `/departments/${encodeURIComponent(deptName)}/teams`,
    data,
  )
  return unwrap(response)
}

export async function updateTeam(
  deptName: string,
  teamName: string,
  data: UpdateTeamRequest,
): Promise<TeamConfig> {
  const response = await apiClient.patch<ApiResponse<TeamConfig>>(
    `/departments/${encodeURIComponent(deptName)}/teams/${encodeURIComponent(teamName)}`,
    data,
  )
  return unwrap(response)
}

export async function deleteTeam(
  deptName: string,
  teamName: string,
  reassignTo?: string,
): Promise<void> {
  const params = reassignTo ? { reassign_to: reassignTo } : undefined
  const response = await apiClient.delete<ApiResponse<null>>(
    `/departments/${encodeURIComponent(deptName)}/teams/${encodeURIComponent(teamName)}`,
    { params },
  )
  unwrapVoid(response)
}

export async function reorderTeams(
  deptName: string,
  data: ReorderTeamsRequest,
): Promise<readonly TeamConfig[]> {
  const response = await apiClient.patch<ApiResponse<readonly TeamConfig[]>>(
    `/departments/${encodeURIComponent(deptName)}/teams/reorder`,
    data,
  )
  return unwrap(response)
}
