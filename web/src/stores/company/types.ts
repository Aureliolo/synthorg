import type { StoreApi } from 'zustand'
import type { AgentConfig } from '@/api/types/agents'
import type { DepartmentHealth } from '@/api/types/analytics'
import type { DepartmentName } from '@/api/types/enums'
import type {
  CompanyConfig,
  CreateAgentOrgRequest,
  CreateDepartmentRequest,
  CreateTeamRequest,
  Department,
  TeamConfig,
  UpdateAgentOrgRequest,
  UpdateCompanyRequest,
  UpdateDepartmentRequest,
  UpdateTeamRequest,
} from '@/api/types/org'
import type { WsEvent } from '@/api/types/websocket'

export interface CompanyState {
  config: CompanyConfig | null
  departmentHealths: readonly DepartmentHealth[]
  loading: boolean
  error: string | null
  healthError: string | null
  savingCount: number
  saveError: string | null
  _refreshVersion: number
  _healthRefreshVersion: number

  fetchCompanyData: () => Promise<void>
  fetchDepartmentHealths: () => Promise<void>
  updateFromWsEvent: (event: WsEvent) => void

  /**
   * Every mutation follows the canonical Zustand store error contract:
   * log + error toast + return a sentinel value (`null` for entity
   * returns, `false` for void / boolean returns) on failure. Callers
   * MUST NOT wrap mutation calls in try/catch; the store owns the
   * error UX.
   */
  updateCompany: (data: UpdateCompanyRequest) => Promise<boolean>
  createDepartment: (
    data: CreateDepartmentRequest,
  ) => Promise<Department | null>
  updateDepartment: (
    name: string,
    data: UpdateDepartmentRequest,
  ) => Promise<Department | null>
  deleteDepartment: (name: string) => Promise<boolean>
  reorderDepartments: (orderedNames: string[]) => Promise<boolean>
  createAgent: (data: CreateAgentOrgRequest) => Promise<AgentConfig | null>
  updateAgent: (
    agentId: string,
    data: UpdateAgentOrgRequest,
  ) => Promise<AgentConfig | null>
  deleteAgent: (agentId: string) => Promise<boolean>
  reorderAgents: (deptName: string, orderedIds: string[]) => Promise<boolean>

  createTeam: (
    deptName: string,
    data: CreateTeamRequest,
  ) => Promise<TeamConfig | null>
  updateTeam: (
    deptName: string,
    teamName: string,
    data: UpdateTeamRequest,
  ) => Promise<TeamConfig | null>
  deleteTeam: (
    deptName: string,
    teamName: string,
    reassignTo?: string,
  ) => Promise<boolean>
  reorderTeams: (
    deptName: string,
    orderedNames: string[],
  ) => Promise<boolean>

  optimisticReorderDepartments: (orderedNames: string[]) => () => void
  optimisticReorderAgents: (
    deptName: string,
    orderedIds: string[],
  ) => () => void
  optimisticReassignAgent: (
    agentId: string,
    newDepartment: DepartmentName,
  ) => () => void
}

export type CompanySet = StoreApi<CompanyState>['setState']
export type CompanyGet = StoreApi<CompanyState>['getState']
