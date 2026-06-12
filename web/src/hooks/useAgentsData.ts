import { useCallback, useEffect, useMemo, useRef } from 'react'
import { useAgentsStore } from '@/stores/agents'
import { useWebSocket, type ChannelBinding } from '@/hooks/useWebSocket'
import { usePolling } from '@/hooks/usePolling'
import { filterAgents, sortAgents } from '@/utils/agents'
import { useFreshnessGate } from '@/hooks/useFreshnessGate'
import type { AgentConfig } from '@/api/types/agents'
import type { WsChannel } from '@/api/types/websocket'

const AGENTS_POLL_INTERVAL = 30_000
/** Shared across agent hooks -- change both if tuning. See useAgentDetailData.ts. */
const WS_DEBOUNCE_MS = 300
const AGENT_CHANNELS = ['agents'] as const satisfies readonly WsChannel[]

export interface UseAgentsDataReturn {
  agents: readonly AgentConfig[]
  filteredAgents: readonly AgentConfig[]
  totalAgents: number
  loading: boolean
  error: string | null
  wsConnected: boolean
  wsSetupError: string | null
}

export function useAgentsData(): UseAgentsDataReturn {
  const agents = useAgentsStore((s) => s.agents)
  const totalAgents = useAgentsStore((s) => s.totalAgents)
  const loading = useAgentsStore((s) => s.listLoading)
  const error = useAgentsStore((s) => s.listError)
  const searchQuery = useAgentsStore((s) => s.searchQuery)
  const departmentFilter = useAgentsStore((s) => s.departmentFilter)
  const levelFilter = useAgentsStore((s) => s.levelFilter)
  const statusFilter = useAgentsStore((s) => s.statusFilter)
  const sortBy = useAgentsStore((s) => s.sortBy)
  const sortDirection = useAgentsStore((s) => s.sortDirection)

  // Initial fetch
  useEffect(() => {
    void useAgentsStore.getState().fetchAgents()
  }, [])

  // Polling, gated so a WS-active session does not also REST-poll on cadence.
  const { skipIfFresh, markFresh } = useFreshnessGate()
  const pollFn = useCallback(async () => {
    await useAgentsStore.getState().fetchAgents()
  }, [])
  const polling = usePolling(pollFn, AGENTS_POLL_INTERVAL, { skipIfFresh })

  const { start, stop } = polling
  useEffect(() => {
    start()
    return () => stop()
  }, [start, stop])

  // WebSocket -- debounce to coalesce burst events into a single refetch
  const wsDebounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(() => () => {
    if (wsDebounceRef.current) clearTimeout(wsDebounceRef.current)
  }, [])

  const bindings: ChannelBinding[] = useMemo(
    () =>
      AGENT_CHANNELS.map((channel) => ({
        channel,
        handler: () => {
          markFresh()
          if (wsDebounceRef.current) clearTimeout(wsDebounceRef.current)
          wsDebounceRef.current = setTimeout(() => {
            void useAgentsStore.getState().fetchAgents()
          }, WS_DEBOUNCE_MS)
        },
      })),
    [markFresh],
  )

  const { connected: wsConnected, setupError: wsSetupError } = useWebSocket({ bindings })

  // Client-side filtering + sorting
  const filteredAgents = useMemo(() => {
    const filtered = filterAgents(agents, {
      search: searchQuery || undefined,
      department: departmentFilter ?? undefined,
      level: levelFilter ?? undefined,
      status: statusFilter ?? undefined,
    })
    return sortAgents(filtered, sortBy, sortDirection)
  }, [agents, searchQuery, departmentFilter, levelFilter, statusFilter, sortBy, sortDirection])

  return {
    agents,
    filteredAgents,
    totalAgents,
    loading,
    error,
    wsConnected,
    wsSetupError,
  }
}
