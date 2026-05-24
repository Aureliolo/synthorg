import {
  createAgentOrg as apiCreateAgent,
  deleteAgent as apiDeleteAgent,
  reorderAgents as apiReorderAgents,
  updateAgentOrg as apiUpdateAgent,
} from '@/api/endpoints/company'
import { getErrorMessage } from '@/utils/errors'
import type { AgentConfig } from '@/api/types/agents'
import type {
  CreateAgentOrgRequest,
  UpdateAgentOrgRequest,
} from '@/api/types/org'
import {
  beginMutation,
  emitErrorToast,
  emitSuccessToast,
  endMutation,
  patchConfig,
} from './_helpers'
import type { CompanyGet, CompanySet } from './types'

async function createAgentImpl(
  set: CompanySet,
  get: CompanyGet,
  data: CreateAgentOrgRequest,
): Promise<AgentConfig | null> {
  beginMutation(set)
  try {
    const agent = await apiCreateAgent(data)
    set((s) => ({
      savingCount: Math.max(0, s.savingCount - 1),
      ...patchConfig(get, (prev) => ({
        ...prev,
        agents: [...prev.agents, agent],
      })),
    }))
    emitSuccessToast(`Agent ${agent.name} created`)
    return agent
  } catch (err) {
    endMutation(set, getErrorMessage(err))
    emitErrorToast(err, 'Failed to create agent', 'Create agent failed')
    return null
  }
}

async function updateAgentImpl(
  set: CompanySet,
  get: CompanyGet,
  name: string,
  data: UpdateAgentOrgRequest,
): Promise<AgentConfig | null> {
  beginMutation(set)
  try {
    const agent = await apiUpdateAgent(name, data)
    set((s) => ({
      savingCount: Math.max(0, s.savingCount - 1),
      ...patchConfig(get, (prev) => ({
        ...prev,
        agents: prev.agents.map((a) => (a.name === name ? agent : a)),
      })),
    }))
    emitSuccessToast(`Agent ${agent.name} updated`)
    return agent
  } catch (err) {
    endMutation(set, getErrorMessage(err))
    emitErrorToast(err, 'Failed to update agent', 'Update agent failed')
    return null
  }
}

async function deleteAgentImpl(
  set: CompanySet,
  get: CompanyGet,
  name: string,
): Promise<boolean> {
  beginMutation(set)
  try {
    await apiDeleteAgent(name)
    set((s) => ({
      savingCount: Math.max(0, s.savingCount - 1),
      ...patchConfig(get, (prev) => ({
        ...prev,
        agents: prev.agents.filter((a) => a.name !== name),
      })),
    }))
    emitSuccessToast(`Agent ${name} deleted`)
    return true
  } catch (err) {
    endMutation(set, getErrorMessage(err))
    emitErrorToast(err, 'Failed to delete agent', 'Delete agent failed')
    return false
  }
}

function resolveAgentNamesFromIds(
  agents: readonly AgentConfig[],
  orderedIds: readonly string[],
): string[] {
  // Callers pass `a.id ?? a.name` as identifiers, but the API
  // expects agent names. Resolve each id back to its name so the
  // payload is always correct even when id differs from name.
  const idToName = new Map(agents.map((a) => [a.id ?? a.name, a.name]))
  return orderedIds.map((id) => idToName.get(id) ?? id)
}

async function reorderAgentsImpl(
  set: CompanySet,
  get: CompanyGet,
  deptName: string,
  orderedIds: string[],
): Promise<boolean> {
  beginMutation(set)
  try {
    const orderedNames = resolveAgentNamesFromIds(
      get().config?.agents ?? [],
      orderedIds,
    )
    await apiReorderAgents(deptName, { agent_names: orderedNames })
    // Refetch to pick up the reordered agents consistently.
    await get().fetchCompanyData()
    endMutation(set)
    return true
  } catch (err) {
    endMutation(set, getErrorMessage(err))
    emitErrorToast(err, 'Failed to reorder agents', 'Reorder agents failed')
    return false
  }
}

export function createAgentActions(set: CompanySet, get: CompanyGet) {
  return {
    createAgent: (data: CreateAgentOrgRequest) =>
      createAgentImpl(set, get, data),
    updateAgent: (name: string, data: UpdateAgentOrgRequest) =>
      updateAgentImpl(set, get, name, data),
    deleteAgent: (name: string) => deleteAgentImpl(set, get, name),
    reorderAgents: (deptName: string, orderedIds: string[]) =>
      reorderAgentsImpl(set, get, deptName, orderedIds),
  }
}
