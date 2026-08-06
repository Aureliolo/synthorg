import { useEffect, useMemo } from 'react'

import { useAgentsStore } from '@/stores/agents'

/**
 * The roles the org currently staffs, fetched from the agents endpoint.
 *
 * Plan review needs it to tell an owner that names somebody from one that
 * names nobody: a plan item owned by a role no agent holds cannot be
 * dispatched, and counting it under "all assigned" is what let a plan reach
 * review with most of its items unroutable.
 *
 * Empty while the fetch is in flight, and for an org with no agents. Callers
 * treat empty as "unknown" and judge no owner on it, rather than flagging
 * every item because the list has not arrived.
 */
export function useOrgRoster(): ReadonlySet<string> {
  const agents = useAgentsStore((s) => s.agents)

  useEffect(() => {
    void useAgentsStore.getState().fetchAgents()
  }, [])

  return useMemo(() => new Set(agents.map((agent) => agent.role)), [agents])
}
