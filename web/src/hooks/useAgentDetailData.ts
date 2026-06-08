import { useCallback, useEffect, useMemo, useRef } from 'react'
import { useAgentsStore } from '@/stores/agents'
import { useWebSocket, type ChannelBinding } from '@/hooks/useWebSocket'
import { usePolling } from '@/hooks/usePolling'
import { computePerformanceCards, generateInsights } from '@/utils/agents'
import type {
  AgentActivityEvent,
  AgentConfig,
  AgentPerformanceSummary,
  CareerEvent,
} from '@/api/types/agents'
import type { AgentHealthResponse } from '@/api/types'
import type { Task } from '@/api/types/tasks'
import type { WsChannel } from '@/api/types/websocket'
import type { MetricCardProps } from '@/components/ui/metric-card'

const DETAIL_POLL_INTERVAL = 30_000
/** Shared across agent hooks -- change both if tuning. See useAgentsData.ts. */
const WS_DEBOUNCE_MS = 300
const DETAIL_CHANNELS = ['agents', 'tasks'] as const satisfies readonly WsChannel[]
const EMPTY_BINDINGS: ChannelBinding[] = []

const EMPTY_RETURN: UseAgentDetailDataReturn = {
  agent: null,
  performance: null,
  health: null,
  performanceCards: [],
  insights: [],
  agentTasks: [],
  activity: [],
  activityTotal: 0,
  careerHistory: [],
  loading: false,
  error: null,
  wsConnected: false,
  wsSetupError: null,
  fetchMoreActivity: () => {},
}

export interface UseAgentDetailDataReturn {
  agent: AgentConfig | null
  performance: AgentPerformanceSummary | null
  health: AgentHealthResponse | null
  performanceCards: Omit<MetricCardProps, 'className'>[]
  insights: string[]
  agentTasks: readonly Task[]
  activity: readonly AgentActivityEvent[]
  activityTotal: number
  careerHistory: readonly CareerEvent[]
  loading: boolean
  error: string | null
  wsConnected: boolean
  wsSetupError: string | null
  fetchMoreActivity: () => void
}

interface DetailStoreSlice {
  readonly agent: AgentConfig | null
  readonly performance: AgentPerformanceSummary | null
  readonly health: AgentHealthResponse | null
  readonly agentTasks: readonly Task[]
  readonly activity: readonly AgentActivityEvent[]
  readonly activityTotal: number
  readonly careerHistory: readonly CareerEvent[]
  readonly loading: boolean
  readonly error: string | null
}

function useDetailStoreSlice(): DetailStoreSlice {
  return {
    agent: useAgentsStore((s) => s.selectedAgent),
    performance: useAgentsStore((s) => s.performance),
    health: useAgentsStore((s) => s.health),
    agentTasks: useAgentsStore((s) => s.agentTasks),
    activity: useAgentsStore((s) => s.activity),
    activityTotal: useAgentsStore((s) => s.activityTotal),
    careerHistory: useAgentsStore((s) => s.careerHistory),
    loading: useAgentsStore((s) => s.detailLoading),
    error: useAgentsStore((s) => s.detailError),
  }
}

function useDetailLifecycle(agentId: string): void {
  // Initial fetch / cleanup. Skip when agentId is empty (missing route param).
  useEffect(() => {
    if (!agentId) {
      useAgentsStore.getState().clearDetail()
      return
    }
    void useAgentsStore.getState().fetchAgentDetail(agentId)
    return () => {
      useAgentsStore.getState().clearDetail()
    }
  }, [agentId])

  const pollFn = useCallback(async () => {
    if (!agentId) return
    await useAgentsStore.getState().fetchAgentDetail(agentId)
  }, [agentId])
  const polling = usePolling(pollFn, DETAIL_POLL_INTERVAL)
  const { start, stop } = polling
  useEffect(() => {
    if (!agentId) return
    start()
    return () => stop()
  }, [agentId, start, stop])
}

interface DetailWsState {
  readonly wsConnected: boolean
  readonly wsSetupError: string | null
}

function useDetailWebSocket(agentId: string): DetailWsState {
  const wsDebounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const agentIdRef = useRef(agentId)
  agentIdRef.current = agentId

  useEffect(() => {
    if (!agentId && wsDebounceRef.current) {
      clearTimeout(wsDebounceRef.current)
      wsDebounceRef.current = null
    }
    return () => {
      if (wsDebounceRef.current) {
        clearTimeout(wsDebounceRef.current)
        wsDebounceRef.current = null
      }
    }
  }, [agentId])

  const bindings: ChannelBinding[] = useMemo(
    () =>
      agentId
        ? DETAIL_CHANNELS.map((channel) => ({
            channel,
            handler: () => {
              if (wsDebounceRef.current) clearTimeout(wsDebounceRef.current)
              wsDebounceRef.current = setTimeout(() => {
                void useAgentsStore.getState().fetchAgentDetail(agentIdRef.current)
              }, WS_DEBOUNCE_MS)
            },
          }))
        : EMPTY_BINDINGS,
    [agentId],
  )

  const { connected, setupError } = useWebSocket({ bindings })
  return { wsConnected: connected, wsSetupError: setupError }
}

export function useAgentDetailData(agentId: string): UseAgentDetailDataReturn {
  const slice = useDetailStoreSlice()
  useDetailLifecycle(agentId)
  const { wsConnected, wsSetupError } = useDetailWebSocket(agentId)

  const performanceCards = useMemo(
    () => (slice.performance ? computePerformanceCards(slice.performance) : []),
    [slice.performance],
  )
  const insights = useMemo(
    () => (slice.agent ? generateInsights(slice.agent, slice.performance) : []),
    [slice.agent, slice.performance],
  )

  // Load more activity. Store-level activityLoading prevents duplicates.
  // Cursor state is held on the store (`activityNextCursor` /
  // `activityHasMore`); the hook just kicks off the fetch when invoked.
  const fetchMoreActivity = useCallback(() => {
    if (!agentId) return
    void useAgentsStore.getState().fetchMoreActivity(agentId)
  }, [agentId])

  if (!agentId) return EMPTY_RETURN

  return {
    ...slice,
    performanceCards,
    insights,
    wsConnected,
    wsSetupError,
    fetchMoreActivity,
  }
}
