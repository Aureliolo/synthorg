import type { AgentConfig } from '@/api/types/agents'
import type { DepartmentName } from '@/api/types/enums'
import type { Department } from '@/api/types/org'
import type { CompanyGet, CompanySet } from './types'

const agentIdOf = (a: AgentConfig): string => a.id ?? a.name

function reorderDepartmentsImpl(
  set: CompanySet,
  get: CompanyGet,
  orderedNames: string[],
): () => void {
  const prev = get().config
  if (!prev) return () => {}
  const prevOrder = prev.departments.map((d) => d.name)
  const deptMap = new Map(prev.departments.map((d) => [d.name, d]))
  const reordered = orderedNames
    .map((n) => deptMap.get(n))
    .filter((d): d is Department => d !== undefined)
  set({ config: { ...prev, departments: reordered } })
  // Targeted rollback: restore only department ordering, not entire config.
  return () => {
    const current = get().config
    if (!current) return
    const currentMap = new Map(current.departments.map((d) => [d.name, d]))
    const prevSet = new Set(prevOrder)
    // Restore previous ordering, then append any departments added concurrently.
    const restored = prevOrder
      .map((n) => currentMap.get(n))
      .filter((d): d is Department => d !== undefined)
    const added = current.departments.filter((d) => !prevSet.has(d.name))
    set({
      config: { ...current, departments: [...restored, ...added] },
    })
  }
}

function applyAgentReorder(
  agents: readonly AgentConfig[],
  deptName: string,
  idSet: ReadonlySet<string>,
  reorderedList: readonly AgentConfig[],
): AgentConfig[] {
  // Preserve original array positions: replace in-place instead of appending.
  let reorderIdx = 0
  return agents.map((a) => {
    if (a.department === deptName && idSet.has(agentIdOf(a))) {
      return reorderedList[reorderIdx++] ?? a
    }
    return a
  })
}

function reorderAgentsImpl(
  set: CompanySet,
  get: CompanyGet,
  deptName: string,
  orderedIds: string[],
): () => void {
  const prev = get().config
  if (!prev) return () => {}
  const idSet = new Set(orderedIds)
  const prevDeptAgentIds = prev.agents
    .filter((a) => a.department === deptName && idSet.has(agentIdOf(a)))
    .map(agentIdOf)
  const agentMap = new Map(
    prev.agents
      .filter((a) => a.department === deptName && idSet.has(agentIdOf(a)))
      .map((a) => [agentIdOf(a), a]),
  )
  // Walk ``orderedIds`` first so the caller-supplied ordering wins,
  // then append any matching agents the caller omitted in their
  // original positional order. Without this top-up,
  // ``applyAgentReorder``'s ``?? a`` fallback could reinsert an agent
  // that already appears in ``reorderedList`` at a different slot,
  // producing duplicate entries in the resulting array.
  const seen = new Set<string>()
  const ordered: AgentConfig[] = []
  for (const id of orderedIds) {
    const agent = agentMap.get(id)
    if (agent && !seen.has(id)) {
      ordered.push(agent)
      seen.add(id)
    }
  }
  for (const id of prevDeptAgentIds) {
    if (seen.has(id)) continue
    const agent = agentMap.get(id)
    if (agent) {
      ordered.push(agent)
      seen.add(id)
    }
  }
  const agents = applyAgentReorder(prev.agents, deptName, idSet, ordered)
  set({ config: { ...prev, agents } })
  // Targeted rollback: restore only this department's agent ordering.
  return () => {
    const current = get().config
    if (!current) return
    const currentAgentMap = new Map(
      current.agents
        .filter((a) => a.department === deptName)
        .map((a) => [agentIdOf(a), a]),
    )
    const restoredOrder = prevDeptAgentIds
      .map((id) => currentAgentMap.get(id))
      .filter((a): a is AgentConfig => a !== undefined)
    const restoredAgents = applyAgentReorder(
      current.agents,
      deptName,
      idSet,
      restoredOrder,
    )
    set({ config: { ...current, agents: restoredAgents } })
  }
}

function reassignAgentImpl(
  set: CompanySet,
  get: CompanyGet,
  agentName: string,
  newDepartment: DepartmentName,
): () => void {
  const prev = get().config
  if (!prev) return () => {}
  const agent = prev.agents.find((a) => a.name === agentName)
  if (!agent || agent.department === newDepartment) return () => {}
  const prevDepartment = agent.department
  const agents = prev.agents.map((a) =>
    a.name === agentName ? { ...a, department: newDepartment } : a,
  )
  set({ config: { ...prev, agents } })
  // Targeted rollback: restore only this agent's department if still on
  // the optimistic value.
  return () => {
    const current = get().config
    if (!current) return
    const currentAgent = current.agents.find((a) => a.name === agentName)
    // Only rollback if this exact optimistic change is still the active one.
    if (!currentAgent || currentAgent.department !== newDepartment) return
    const currentAgents = current.agents.map((a) =>
      a.name === agentName ? { ...a, department: prevDepartment } : a,
    )
    set({ config: { ...current, agents: currentAgents } })
  }
}

export function createOptimisticActions(set: CompanySet, get: CompanyGet) {
  return {
    optimisticReorderDepartments: (orderedNames: string[]) =>
      reorderDepartmentsImpl(set, get, orderedNames),
    optimisticReorderAgents: (deptName: string, orderedIds: string[]) =>
      reorderAgentsImpl(set, get, deptName, orderedIds),
    optimisticReassignAgent: (
      agentName: string,
      newDepartment: DepartmentName,
    ) => reassignAgentImpl(set, get, agentName, newDepartment),
  }
}
