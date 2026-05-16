import { create } from 'zustand'
import {
  getCompanyConfig,
  getDepartmentHealth,
  updateCompany as apiUpdateCompany,
  createDepartment as apiCreateDepartment,
  updateDepartment as apiUpdateDepartment,
  deleteDepartment as apiDeleteDepartment,
  reorderDepartments as apiReorderDepartments,
  createAgentOrg as apiCreateAgent,
  updateAgentOrg as apiUpdateAgent,
  deleteAgent as apiDeleteAgent,
  reorderAgents as apiReorderAgents,
  createTeam as apiCreateTeam,
  updateTeam as apiUpdateTeam,
  deleteTeam as apiDeleteTeam,
  reorderTeams as apiReorderTeams,
} from '@/api/endpoints/company'
import { getCrudErrorTitle, getErrorMessage } from '@/utils/errors'
import { sanitizeForLog } from '@/utils/logging'
import { createLogger } from '@/lib/logger'
import { useToastStore } from '@/stores/toast'
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

const log = createLogger('company')

interface CompanyState {
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
  optimisticReassignAgent: (agentName: string, newDepartment: DepartmentName) => () => void
}

const ORG_MUTATION_EVENTS: ReadonlySet<string> = new Set([
  'agent.hired', 'agent.fired',
  'company.updated',
  'department.created', 'department.updated', 'department.deleted', 'departments.reordered',
  'agent.created', 'agent.updated', 'agent.deleted', 'agents.reordered',
])

export const useCompanyStore = create<CompanyState>()((set, get) => ({
  config: null,
  departmentHealths: [],
  loading: false,
  error: null,
  healthError: null,
  savingCount: 0,
  saveError: null,
  _refreshVersion: 0,
  _healthRefreshVersion: 0,

  fetchCompanyData: async () => {
    const version = get()._refreshVersion + 1
    set({ _refreshVersion: version, loading: true, error: null })
    try {
      const config = await getCompanyConfig()
      if (get()._refreshVersion !== version) return // stale response
      set({ config, loading: false, error: null })
    } catch (err) {
      if (get()._refreshVersion !== version) return // stale error
      set({ loading: false, error: getErrorMessage(err) })
      throw err
    }
  },

  fetchDepartmentHealths: async () => {
    const version = get()._healthRefreshVersion + 1
    set({ _healthRefreshVersion: version })
    try {
      const config = useCompanyStore.getState().config
      if (!config) return
      const healthPromises = config.departments.map((dept) =>
        getDepartmentHealth(dept.name).catch((err: unknown) => {
          log.warn('Health fetch failed for dept:', dept.name, err)
          return null
        }),
      )
      const healthResults = await Promise.all(healthPromises)
      if (get()._healthRefreshVersion !== version) return // stale response
      const departmentHealths = healthResults.filter(
        (h): h is DepartmentHealth => h !== null,
      )
      if (departmentHealths.length === 0 && config.departments.length > 0) {
        set({ departmentHealths, healthError: 'Failed to fetch department health data' })
      } else {
        set({ departmentHealths, healthError: null })
      }
    } catch (err) {
      if (get()._healthRefreshVersion !== version) return // stale error
      set({ healthError: getErrorMessage(err) })
    }
  },

  updateFromWsEvent: (event) => {
    if (ORG_MUTATION_EVENTS.has(event.event_type)) {
      const store = useCompanyStore.getState()
      // Sequential: fetchDepartmentHealths needs the freshly fetched
      // config.departments list to know which deps to query, so it
      // must run AFTER fetchCompanyData completes. If fetchCompanyData
      // rejects, run fetchDepartmentHealths against the stale
      // department list rather than skipping it entirely -- a
      // transient config-fetch failure should not block the health
      // refresh, and each fetch sets its own error state so the user
      // still sees what failed. Each branch's catch logs the failure
      // for the diagnostic trail.
      ;(async () => {
        try {
          await store.fetchCompanyData()
        } catch (err) {
          log.warn('WS refresh: fetchCompanyData failed:', getErrorMessage(err))
        }
        try {
          await store.fetchDepartmentHealths()
        } catch (err) {
          log.warn('WS refresh: fetchDepartmentHealths failed:', getErrorMessage(err))
        }
      })()
    }
  },

  // ── Mutations ──────────────────────────────────────────────

  updateCompany: async (data) => {
    // Split the two phases so a successful PATCH never gets reported
    // as a failed save just because the follow-up refresh threw: the
    // update has already committed on the server, and treating the
    // refresh error as a mutation failure would leave the form dirty
    // and invite duplicate retries of the same change.
    set((s) => ({ savingCount: s.savingCount + 1, saveError: null }))
    try {
      await apiUpdateCompany(data)
    } catch (err) {
      log.error('Update company failed:', sanitizeForLog(err))
      set((s) => ({
        savingCount: Math.max(0, s.savingCount - 1),
        saveError: getErrorMessage(err),
      }))
      useToastStore.getState().add({
        variant: 'error',
        ...getCrudErrorTitle(err, 'Failed to update company'),
        description: getErrorMessage(err),
      })
      return false
    }
    // PATCH succeeded. Attempt to refetch the canonical config so the
    // UI reflects the server's post-update view, but do not undo the
    // success signal if the refetch itself fails -- ``fetchCompanyData``
    // already sets its own error state that page-level banners consume.
    try {
      await get().fetchCompanyData()
    } catch (refreshErr) {
      log.warn('Company updated but refresh failed:', sanitizeForLog(refreshErr))
    }
    set((s) => ({ savingCount: Math.max(0, s.savingCount - 1) }))
    useToastStore.getState().add({
      variant: 'success',
      title: 'Company updated',
    })
    return true
  },

  createDepartment: async (data) => {
    set((s) => ({ savingCount: s.savingCount + 1, saveError: null }))
    try {
      const dept = await apiCreateDepartment(data)
      const prev = get().config
      set((s) => ({
        savingCount: Math.max(0, s.savingCount - 1),
        ...(prev ? { config: { ...prev, departments: [...prev.departments, dept] } } : {}),
      }))
      useToastStore.getState().add({
        variant: 'success',
        title: `Department ${dept.name} created`,
      })
      return dept
    } catch (err) {
      log.error('Create department failed:', sanitizeForLog(err))
      set((s) => ({ savingCount: Math.max(0, s.savingCount - 1), saveError: getErrorMessage(err) }))
      useToastStore.getState().add({
        variant: 'error',
        ...getCrudErrorTitle(err, 'Failed to create department'),
        description: getErrorMessage(err),
      })
      return null
    }
  },

  updateDepartment: async (name, data) => {
    set((s) => ({ savingCount: s.savingCount + 1, saveError: null }))
    try {
      const dept = await apiUpdateDepartment(name, data)
      const prev = get().config
      set((s) => ({
        savingCount: Math.max(0, s.savingCount - 1),
        ...(prev ? { config: { ...prev, departments: prev.departments.map((d) => (d.name === name ? dept : d)) } } : {}),
      }))
      useToastStore.getState().add({
        variant: 'success',
        title: `Department ${dept.name} updated`,
      })
      return dept
    } catch (err) {
      log.error('Update department failed:', sanitizeForLog(err))
      set((s) => ({ savingCount: Math.max(0, s.savingCount - 1), saveError: getErrorMessage(err) }))
      useToastStore.getState().add({
        variant: 'error',
        ...getCrudErrorTitle(err, 'Failed to update department'),
        description: getErrorMessage(err),
      })
      return null
    }
  },

  deleteDepartment: async (name) => {
    set((s) => ({ savingCount: s.savingCount + 1, saveError: null }))
    try {
      await apiDeleteDepartment(name)
      const prev = get().config
      set((s) => ({
        savingCount: Math.max(0, s.savingCount - 1),
        ...(prev ? { config: { ...prev, departments: prev.departments.filter((d) => d.name !== name) } } : {}),
      }))
      useToastStore.getState().add({
        variant: 'success',
        title: `Department ${name} deleted`,
      })
      return true
    } catch (err) {
      log.error('Delete department failed:', sanitizeForLog(err))
      set((s) => ({ savingCount: Math.max(0, s.savingCount - 1), saveError: getErrorMessage(err) }))
      useToastStore.getState().add({
        variant: 'error',
        ...getCrudErrorTitle(err, 'Failed to delete department'),
        description: getErrorMessage(err),
      })
      return false
    }
  },

  reorderDepartments: async (orderedNames) => {
    set((s) => ({ savingCount: s.savingCount + 1, saveError: null }))
    try {
      const reordered = await apiReorderDepartments({ department_names: orderedNames })
      const prev = get().config
      set((s) => ({
        savingCount: Math.max(0, s.savingCount - 1),
        ...(prev ? { config: { ...prev, departments: [...reordered] } } : {}),
      }))
      return true
    } catch (err) {
      log.error('Reorder departments failed:', sanitizeForLog(err))
      set((s) => ({ savingCount: Math.max(0, s.savingCount - 1), saveError: getErrorMessage(err) }))
      useToastStore.getState().add({
        variant: 'error',
        ...getCrudErrorTitle(err, 'Failed to reorder departments'),
        description: getErrorMessage(err),
      })
      return false
    }
  },

  createAgent: async (data) => {
    set((s) => ({ savingCount: s.savingCount + 1, saveError: null }))
    try {
      const agent = await apiCreateAgent(data)
      const prev = get().config
      set((s) => ({
        savingCount: Math.max(0, s.savingCount - 1),
        ...(prev ? { config: { ...prev, agents: [...prev.agents, agent] } } : {}),
      }))
      useToastStore.getState().add({
        variant: 'success',
        title: `Agent ${agent.name} created`,
      })
      return agent
    } catch (err) {
      log.error('Create agent failed:', sanitizeForLog(err))
      set((s) => ({ savingCount: Math.max(0, s.savingCount - 1), saveError: getErrorMessage(err) }))
      useToastStore.getState().add({
        variant: 'error',
        ...getCrudErrorTitle(err, 'Failed to create agent'),
        description: getErrorMessage(err),
      })
      return null
    }
  },

  updateAgent: async (name, data) => {
    set((s) => ({ savingCount: s.savingCount + 1, saveError: null }))
    try {
      const agent = await apiUpdateAgent(name, data)
      const prev = get().config
      set((s) => ({
        savingCount: Math.max(0, s.savingCount - 1),
        ...(prev ? { config: { ...prev, agents: prev.agents.map((a) => (a.name === name ? agent : a)) } } : {}),
      }))
      useToastStore.getState().add({
        variant: 'success',
        title: `Agent ${agent.name} updated`,
      })
      return agent
    } catch (err) {
      log.error('Update agent failed:', sanitizeForLog(err))
      set((s) => ({ savingCount: Math.max(0, s.savingCount - 1), saveError: getErrorMessage(err) }))
      useToastStore.getState().add({
        variant: 'error',
        ...getCrudErrorTitle(err, 'Failed to update agent'),
        description: getErrorMessage(err),
      })
      return null
    }
  },

  deleteAgent: async (name) => {
    set((s) => ({ savingCount: s.savingCount + 1, saveError: null }))
    try {
      await apiDeleteAgent(name)
      const prev = get().config
      set((s) => ({
        savingCount: Math.max(0, s.savingCount - 1),
        ...(prev ? { config: { ...prev, agents: prev.agents.filter((a) => a.name !== name) } } : {}),
      }))
      useToastStore.getState().add({
        variant: 'success',
        title: `Agent ${name} deleted`,
      })
      return true
    } catch (err) {
      log.error('Delete agent failed:', sanitizeForLog(err))
      set((s) => ({ savingCount: Math.max(0, s.savingCount - 1), saveError: getErrorMessage(err) }))
      useToastStore.getState().add({
        variant: 'error',
        ...getCrudErrorTitle(err, 'Failed to delete agent'),
        description: getErrorMessage(err),
      })
      return false
    }
  },

  reorderAgents: async (deptName, orderedIds) => {
    set((s) => ({ savingCount: s.savingCount + 1, saveError: null }))
    try {
      // Callers pass `a.id ?? a.name` as identifiers, but the API
      // expects agent names.  Resolve each id back to its name so the
      // payload is always correct even when id differs from name.
      const prev = get().config
      const idToName = new Map(
        (prev?.agents ?? []).map((a) => [a.id ?? a.name, a.name]),
      )
      const orderedNames = orderedIds.map((id) => idToName.get(id) ?? id)
      await apiReorderAgents(deptName, { agent_names: orderedNames })
      // Refetch to pick up the reordered agents consistently
      await get().fetchCompanyData()
      set((s) => ({
        savingCount: Math.max(0, s.savingCount - 1),
      }))
      return true
    } catch (err) {
      log.error('Reorder agents failed:', sanitizeForLog(err))
      set((s) => ({ savingCount: Math.max(0, s.savingCount - 1), saveError: getErrorMessage(err) }))
      useToastStore.getState().add({
        variant: 'error',
        ...getCrudErrorTitle(err, 'Failed to reorder agents'),
        description: getErrorMessage(err),
      })
      return false
    }
  },

  // ── Team mutations ────────────────────────────────────────

  createTeam: async (deptName, data) => {
    set((s) => ({ savingCount: s.savingCount + 1, saveError: null }))
    try {
      const team = await apiCreateTeam(deptName, data)
      const prev = get().config
      set((s) => ({
        savingCount: Math.max(0, s.savingCount - 1),
        ...(prev ? {
          config: {
            ...prev,
            departments: prev.departments.map((d) =>
              d.name === deptName ? { ...d, teams: [...d.teams, team] } : d,
            ),
          },
        } : {}),
      }))
      useToastStore.getState().add({
        variant: 'success',
        title: `Team ${team.name} created`,
      })
      return team
    } catch (err) {
      log.error('Create team failed:', sanitizeForLog(err))
      set((s) => ({ savingCount: Math.max(0, s.savingCount - 1), saveError: getErrorMessage(err) }))
      useToastStore.getState().add({
        variant: 'error',
        ...getCrudErrorTitle(err, 'Failed to create team'),
        description: getErrorMessage(err),
      })
      return null
    }
  },

  updateTeam: async (deptName, teamName, data) => {
    set((s) => ({ savingCount: s.savingCount + 1, saveError: null }))
    try {
      const team = await apiUpdateTeam(deptName, teamName, data)
      const prev = get().config
      set((s) => ({
        savingCount: Math.max(0, s.savingCount - 1),
        ...(prev ? {
          config: {
            ...prev,
            departments: prev.departments.map((d) =>
              d.name === deptName
                ? { ...d, teams: d.teams.map((t) => (t.name === teamName ? team : t)) }
                : d,
            ),
          },
        } : {}),
      }))
      useToastStore.getState().add({
        variant: 'success',
        title: `Team ${team.name} updated`,
      })
      return team
    } catch (err) {
      log.error('Update team failed:', sanitizeForLog(err))
      set((s) => ({ savingCount: Math.max(0, s.savingCount - 1), saveError: getErrorMessage(err) }))
      useToastStore.getState().add({
        variant: 'error',
        ...getCrudErrorTitle(err, 'Failed to update team'),
        description: getErrorMessage(err),
      })
      return null
    }
  },

  deleteTeam: async (deptName, teamName, reassignTo) => {
    set((s) => ({ savingCount: s.savingCount + 1, saveError: null }))
    try {
      await apiDeleteTeam(deptName, teamName, reassignTo)
      if (reassignTo) {
        await get().fetchCompanyData()
        set((s) => ({ savingCount: Math.max(0, s.savingCount - 1) }))
      } else {
        const prev = get().config
        set((s) => ({
          savingCount: Math.max(0, s.savingCount - 1),
          ...(prev ? {
            config: {
              ...prev,
              departments: prev.departments.map((d) =>
                d.name === deptName
                  ? { ...d, teams: d.teams.filter((t) => t.name !== teamName) }
                  : d,
              ),
            },
          } : {}),
        }))
      }
      useToastStore.getState().add({
        variant: 'success',
        title: `Team ${teamName} deleted`,
      })
      return true
    } catch (err) {
      log.error('Delete team failed:', sanitizeForLog(err))
      set((s) => ({ savingCount: Math.max(0, s.savingCount - 1), saveError: getErrorMessage(err) }))
      useToastStore.getState().add({
        variant: 'error',
        ...getCrudErrorTitle(err, 'Failed to delete team'),
        description: getErrorMessage(err),
      })
      return false
    }
  },

  reorderTeams: async (deptName, orderedNames) => {
    set((s) => ({ savingCount: s.savingCount + 1, saveError: null }))
    try {
      const reordered = await apiReorderTeams(deptName, { team_names: orderedNames })
      const prev = get().config
      set((s) => ({
        savingCount: Math.max(0, s.savingCount - 1),
        ...(prev ? {
          config: {
            ...prev,
            departments: prev.departments.map((d) =>
              d.name === deptName ? { ...d, teams: reordered } : d,
            ),
          },
        } : {}),
      }))
      return true
    } catch (err) {
      log.error('Reorder teams failed:', sanitizeForLog(err))
      set((s) => ({ savingCount: Math.max(0, s.savingCount - 1), saveError: getErrorMessage(err) }))
      useToastStore.getState().add({
        variant: 'error',
        ...getCrudErrorTitle(err, 'Failed to reorder teams'),
        description: getErrorMessage(err),
      })
      return false
    }
  },

  // ── Optimistic helpers ─────────────────────────────────────

  optimisticReorderDepartments: (orderedNames) => {
    const prev = get().config
    if (!prev) return () => {}
    const prevOrder = prev.departments.map((d) => d.name)
    const deptMap = new Map(prev.departments.map((d) => [d.name, d]))
    const reordered = orderedNames
      .map((n) => deptMap.get(n as Department['name']))
      .filter((d): d is Department => d !== undefined)
    set({ config: { ...prev, departments: reordered } })
    // Targeted rollback: restore only department ordering, not entire config
    return () => {
      const current = get().config
      if (!current) return
      const currentMap = new Map(current.departments.map((d) => [d.name, d]))
      const prevSet = new Set(prevOrder)
      // Restore previous ordering, then append any departments added concurrently
      const restored = prevOrder
        .map((n) => currentMap.get(n as Department['name']))
        .filter((d): d is Department => d !== undefined)
      const added = current.departments.filter((d) => !prevSet.has(d.name))
      set({ config: { ...current, departments: [...restored, ...added] } })
    }
  },

  optimisticReorderAgents: (deptName, orderedIds) => {
    const prev = get().config
    if (!prev) return () => {}
    const idOf = (a: AgentConfig) => a.id ?? a.name
    const idSet = new Set(orderedIds)
    const prevDeptAgentIds = prev.agents
      .filter((a) => a.department === deptName && idSet.has(idOf(a)))
      .map(idOf)
    const agentMap = new Map(
      prev.agents
        .filter((a) => a.department === deptName && idSet.has(idOf(a)))
        .map((a) => [idOf(a), a]),
    )
    // Preserve original array positions: replace in-place instead of appending
    let reorderIdx = 0
    const reorderedList = orderedIds
      .map((id) => agentMap.get(id))
      .filter((a): a is AgentConfig => a !== undefined)
    const agents = prev.agents.map((a) => {
      if (a.department === deptName && idSet.has(idOf(a))) {
        return reorderedList[reorderIdx++] ?? a
      }
      return a
    })
    set({ config: { ...prev, agents } })
    // Targeted rollback: restore only this department's agent ordering
    return () => {
      const current = get().config
      if (!current) return
      const currentAgentMap = new Map(
        current.agents
          .filter((a) => a.department === deptName)
          .map((a) => [idOf(a), a]),
      )
      let restoreIdx = 0
      const restoredOrder = prevDeptAgentIds
        .map((id) => currentAgentMap.get(id))
        .filter((a): a is AgentConfig => a !== undefined)
      const restoredAgents = current.agents.map((a) => {
        if (a.department === deptName && idSet.has(idOf(a))) {
          return restoredOrder[restoreIdx++] ?? a
        }
        return a
      })
      set({ config: { ...current, agents: restoredAgents } })
    }
  },

  optimisticReassignAgent: (agentName, newDepartment) => {
    const prev = get().config
    if (!prev) return () => {}
    const agent = prev.agents.find((a) => a.name === agentName)
    if (!agent || agent.department === newDepartment) return () => {}
    const prevDepartment = agent.department
    const agents = prev.agents.map((a) =>
      a.name === agentName ? { ...a, department: newDepartment } : a,
    )
    set({ config: { ...prev, agents } })
    // Targeted rollback: restore only this agent's department if still on the optimistic value
    return () => {
      const current = get().config
      if (!current) return
      const currentAgent = current.agents.find((a) => a.name === agentName)
      // Only rollback if this exact optimistic change is still the active one
      if (!currentAgent || currentAgent.department !== newDepartment) return
      const currentAgents = current.agents.map((a) =>
        a.name === agentName ? { ...a, department: prevDepartment } : a,
      )
      set({ config: { ...current, agents: currentAgents } })
    }
  },
}))
