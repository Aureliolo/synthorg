import { useCallback, useEffect, useMemo } from 'react'
import { useCompanyStore } from '@/stores/company'
import { useWebSocket, type ChannelBinding } from '@/hooks/useWebSocket'
import { usePolling } from '@/hooks/usePolling'
import type { AgentConfig } from '@/api/types/agents'
import type { DepartmentHealth } from '@/api/types/analytics'
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
import type { WsChannel } from '@/api/types/websocket'

const ORG_EDIT_POLL_INTERVAL = 30_000
const ORG_EDIT_CHANNELS = ['agents'] as const satisfies readonly WsChannel[]

export interface UseOrgEditDataReturn {
  config: CompanyConfig | null
  departmentHealths: readonly DepartmentHealth[]
  loading: boolean
  error: string | null
  saving: boolean
  saveError: string | null
  wsConnected: boolean
  wsSetupError: string | null

  // Mirrors the canonical store contract: resolves to `false` on
  // failure. Callers must check the boolean result rather than
  // try/catch.
  updateCompany: (data: UpdateCompanyRequest) => Promise<boolean>
  createDepartment: (data: CreateDepartmentRequest) => Promise<Department | null>
  updateDepartment: (name: string, data: UpdateDepartmentRequest) => Promise<Department | null>
  deleteDepartment: (name: string) => Promise<boolean>
  reorderDepartments: (orderedNames: string[]) => Promise<boolean>
  createAgent: (data: CreateAgentOrgRequest) => Promise<AgentConfig | null>
  updateAgent: (name: string, data: UpdateAgentOrgRequest) => Promise<AgentConfig | null>
  deleteAgent: (name: string) => Promise<boolean>
  reorderAgents: (deptName: string, orderedIds: string[]) => Promise<boolean>
  createTeam: (deptName: string, data: CreateTeamRequest) => Promise<TeamConfig | null>
  updateTeam: (deptName: string, teamName: string, data: UpdateTeamRequest) => Promise<TeamConfig | null>
  deleteTeam: (deptName: string, teamName: string, reassignTo?: string) => Promise<boolean>
  reorderTeams: (deptName: string, orderedNames: string[]) => Promise<boolean>
  optimisticReorderDepartments: (orderedNames: string[]) => () => void
  optimisticReorderAgents: (deptName: string, orderedIds: string[]) => () => void
}

export function useOrgEditData(): UseOrgEditDataReturn {
  const config = useCompanyStore((s) => s.config)
  const departmentHealths = useCompanyStore((s) => s.departmentHealths)
  const loading = useCompanyStore((s) => s.loading)
  const error = useCompanyStore((s) => s.error)
  const saving = useCompanyStore((s) => s.savingCount > 0)
  const saveError = useCompanyStore((s) => s.saveError)

  // Mutations (stable references via store actions)
  const updateCompany = useCompanyStore((s) => s.updateCompany)
  const createDepartment = useCompanyStore((s) => s.createDepartment)
  const updateDepartment = useCompanyStore((s) => s.updateDepartment)
  const deleteDepartment = useCompanyStore((s) => s.deleteDepartment)
  const reorderDepartments = useCompanyStore((s) => s.reorderDepartments)
  const createAgent = useCompanyStore((s) => s.createAgent)
  const updateAgent = useCompanyStore((s) => s.updateAgent)
  const deleteAgent = useCompanyStore((s) => s.deleteAgent)
  const reorderAgents = useCompanyStore((s) => s.reorderAgents)
  const createTeam = useCompanyStore((s) => s.createTeam)
  const updateTeam = useCompanyStore((s) => s.updateTeam)
  const deleteTeam = useCompanyStore((s) => s.deleteTeam)
  const reorderTeams = useCompanyStore((s) => s.reorderTeams)
  const optimisticReorderDepartments = useCompanyStore((s) => s.optimisticReorderDepartments)
  const optimisticReorderAgents = useCompanyStore((s) => s.optimisticReorderAgents)

  // Polling for department health refresh
  const pollFn = useCallback(async () => {
    await useCompanyStore.getState().fetchDepartmentHealths()
  }, [])
  const polling = usePolling(pollFn, ORG_EDIT_POLL_INTERVAL)

  // Initial data fetch (sequential: health depends on config)
  useEffect(() => {
    let mounted = true
    const store = useCompanyStore.getState()
    store.fetchCompanyData()
      .then(() => {
        if (!mounted) return
        if (useCompanyStore.getState().config) {
          return store.fetchDepartmentHealths()
        }
      })
      .then(() => {
        if (mounted) polling.start()
      })
      .catch(() => {
        // Errors are set in store state by the respective fetch methods
      })
    return () => {
      mounted = false
      polling.stop()
    }
    // eslint-disable-next-line @eslint-react/exhaustive-deps -- mount-only effect; polling ref identity is stable
  }, [])

  // WebSocket bindings for real-time updates
  const bindings: ChannelBinding[] = useMemo(
    () =>
      ORG_EDIT_CHANNELS.map((channel) => ({
        channel,
        handler: (event) => {
          useCompanyStore.getState().updateFromWsEvent(event)
        },
      })),
    [],
  )

  const { connected: wsConnected, setupError: wsSetupError } = useWebSocket({
    bindings,
  })

  return {
    config,
    departmentHealths,
    loading,
    error,
    saving,
    saveError,
    wsConnected,
    wsSetupError,
    updateCompany,
    createDepartment,
    updateDepartment,
    deleteDepartment,
    reorderDepartments,
    createAgent,
    updateAgent,
    deleteAgent,
    reorderAgents,
    createTeam,
    updateTeam,
    deleteTeam,
    reorderTeams,
    optimisticReorderDepartments,
    optimisticReorderAgents,
  }
}
