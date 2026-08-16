import { useEffect, useMemo } from 'react'

import { useAgentsStore } from '@/stores/agents'

/**
 * Resolve an agent id to the name the operator knows them by.
 *
 * Resolved at the read boundary rather than denormalised onto the rows that
 * carry the id, because those ids are repointed while the org runs: a project's
 * lead changes with staffing, a task's assignee changes with reassignment, and
 * a stored copy of the name would go stale exactly when it mattered. Reading it
 * from the roster the dashboard already loads means the next read is correct.
 *
 * A value that resolves to nothing is returned unchanged. That covers two
 * cases and both want it: a system actor is already a readable word
 * (`coordinator`), and an id the roster has not loaded is better shown than
 * replaced by a placeholder that hides which row is which.
 */
export interface AgentNameResolver {
  /** Whether the roster has loaded, so a caller can wait rather than show ids. */
  ready: boolean
  /** The agent's name, or *id* unchanged when nothing resolves it. */
  nameOf: (id: string | null | undefined) => string
}

export function useAgentNames(): AgentNameResolver {
  const agents = useAgentsStore((s) => s.agents)
  const fetchAgents = useAgentsStore((s) => s.fetchAgents)

  // A surface that shows an agent's name is often not the agents page, so the
  // roster may never have been fetched. Asked for once, and only when empty.
  useEffect(() => {
    if (agents.length === 0) void fetchAgents()
  }, [agents.length, fetchAgents])

  const byId = useMemo(
    () => new Map(agents.map((a) => [a.id, a.name] as const)),
    [agents],
  )

  return useMemo(
    () => ({
      ready: byId.size > 0,
      nameOf: (id) => (id == null ? '' : (byId.get(id) ?? id)),
    }),
    [byId],
  )
}
