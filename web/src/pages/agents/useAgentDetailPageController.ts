import { useCallback, useMemo } from 'react'

import { rollbackAgentIdentity } from '@/api/endpoints/agents'
import { createVersionHistoryClient } from '@/api/endpoints/version-history'
import { useAgentDetailData } from '@/hooks/useAgentDetailData'
import {
  useDetailNavigation,
  useDetailNavigationCallbacks,
} from '@/hooks/use-detail-navigation'
import { useCompanyStore } from '@/stores/company'
import { ROUTES } from '@/router/routes'

export interface AgentDetailPageController {
  resolvedAgentName: string
  data: ReturnType<typeof useAgentDetailData>
  versionsClient: ReturnType<typeof createVersionHistoryClient<Record<string, unknown>>> | null
  nav: ReturnType<typeof useDetailNavigation>
  goPrev: () => void
  goNext: () => void
}

export function useAgentDetailPageController(
  agentId: string | undefined,
): AgentDetailPageController {
  const configAgent = useCompanyStore((s) =>
    s.config?.agents.find((a) => a.id === agentId),
  )
  const resolvedAgentName = configAgent?.name ?? ''
  const resolvedAgentId = agentId ?? ''
  const data = useAgentDetailData(resolvedAgentId)

  // Build the version-history client lazily once per agent. The agent
  // identity / version API is id-keyed and ``agent.id`` is always present.
  const versionsClient = useMemo(
    () =>
      resolvedAgentId !== ''
        ? createVersionHistoryClient<Record<string, unknown>>(
            `/agents/${encodeURIComponent(resolvedAgentId)}`,
            (input) =>
              rollbackAgentIdentity(resolvedAgentId, {
                target_version: input.targetVersion,
                reason: input.reason,
              }),
          )
        : null,
    [resolvedAgentId],
  )

  // Walk the company config's agent roster so prev/next on this detail page
  // steps through the same agents AgentsPage shows.
  const configAgents = useCompanyStore((s) => s.config?.agents)
  const routeForAgent = useCallback(
    (item: { id: string }) =>
      ROUTES.AGENT_DETAIL.replace(':agentId', encodeURIComponent(item.id)),
    [],
  )
  const navItems = useMemo(
    () => (configAgents ?? []).map((a) => ({ id: a.id })),
    [configAgents],
  )
  const nav = useDetailNavigation({
    items: navItems,
    currentId: agentId,
    routeFor: routeForAgent,
  })
  const { goPrev, goNext } = useDetailNavigationCallbacks(nav)

  return { resolvedAgentName, data, versionsClient, nav, goPrev, goNext }
}
