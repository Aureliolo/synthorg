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

type CompanyMutations = Pick<
  UseOrgEditDataReturn,
  | 'updateCompany'
  | 'createDepartment' | 'updateDepartment' | 'deleteDepartment' | 'reorderDepartments'
  | 'createAgent' | 'updateAgent' | 'deleteAgent' | 'reorderAgents'
  | 'createTeam' | 'updateTeam' | 'deleteTeam' | 'reorderTeams'
  | 'optimisticReorderDepartments' | 'optimisticReorderAgents'
>

function useCompanyMutations(): CompanyMutations {
  return {
    updateCompany: useCompanyStore((s) => s.updateCompany),
    createDepartment: useCompanyStore((s) => s.createDepartment),
    updateDepartment: useCompanyStore((s) => s.updateDepartment),
    deleteDepartment: useCompanyStore((s) => s.deleteDepartment),
    reorderDepartments: useCompanyStore((s) => s.reorderDepartments),
    createAgent: useCompanyStore((s) => s.createAgent),
    updateAgent: useCompanyStore((s) => s.updateAgent),
    deleteAgent: useCompanyStore((s) => s.deleteAgent),
    reorderAgents: useCompanyStore((s) => s.reorderAgents),
    createTeam: useCompanyStore((s) => s.createTeam),
    updateTeam: useCompanyStore((s) => s.updateTeam),
    deleteTeam: useCompanyStore((s) => s.deleteTeam),
    reorderTeams: useCompanyStore((s) => s.reorderTeams),
    optimisticReorderDepartments: useCompanyStore((s) => s.optimisticReorderDepartments),
    optimisticReorderAgents: useCompanyStore((s) => s.optimisticReorderAgents),
  }
}

/** Sequential initial fetch: department health depends on config being loaded. */
function useCompanyInitialFetch(start: () => void, stop: () => void): void {
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
        if (mounted) start()
      })
      .catch(() => {
        // Errors are set in store state by the respective fetch methods
      })
    return () => {
      mounted = false
      stop()
    }
  }, [start, stop])
}

export function useOrgEditData(): UseOrgEditDataReturn {
  const config = useCompanyStore((s) => s.config)
  const departmentHealths = useCompanyStore((s) => s.departmentHealths)
  const loading = useCompanyStore((s) => s.loading)
  const error = useCompanyStore((s) => s.error)
  const saving = useCompanyStore((s) => s.savingCount > 0)
  const saveError = useCompanyStore((s) => s.saveError)
  const mutations = useCompanyMutations()

  const pollFn = useCallback(async () => {
    await useCompanyStore.getState().fetchDepartmentHealths()
  }, [])
  const polling = usePolling(pollFn, ORG_EDIT_POLL_INTERVAL)
  useCompanyInitialFetch(polling.start, polling.stop)

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
  const { connected: wsConnected, setupError: wsSetupError } = useWebSocket({ bindings })

  return {
    config,
    departmentHealths,
    loading,
    error,
    saving,
    saveError,
    wsConnected,
    wsSetupError,
    ...mutations,
  }
}
