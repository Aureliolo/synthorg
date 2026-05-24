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
    agentTasks: useAgentsStore((s) => s.agentTasks),
    activity: useAgentsStore((s) => s.activity),
    activityTotal: useAgentsStore((s) => s.activityTotal),
    careerHistory: useAgentsStore((s) => s.careerHistory),
    loading: useAgentsStore((s) => s.detailLoading),
    error: useAgentsStore((s) => s.detailError),
  }
}

function useDetailLifecycle(agentName: string): void {
  // Initial fetch / cleanup. Skip when agentName is empty (missing route param).
  useEffect(() => {
    if (!agentName) {
      useAgentsStore.getState().clearDetail()
      return
    }
    void useAgentsStore.getState().fetchAgentDetail(agentName)
    return () => {
      useAgentsStore.getState().clearDetail()
    }
  }, [agentName])

  const pollFn = useCallback(async () => {
    if (!agentName) return
    await useAgentsStore.getState().fetchAgentDetail(agentName)
  }, [agentName])
  const polling = usePolling(pollFn, DETAIL_POLL_INTERVAL)
  useEffect(() => {
    if (!agentName) return
    polling.start()
    return () => polling.stop()
    // polling is a new object each render but start/stop are stable;
    // including it would restart polling on every render
    // eslint-disable-next-line @eslint-react/exhaustive-deps
  }, [agentName])
}

interface DetailWsState {
  readonly wsConnected: boolean
  readonly wsSetupError: string | null
}

function useDetailWebSocket(agentName: string): DetailWsState {
  const wsDebounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const agentNameRef = useRef(agentName)
  agentNameRef.current = agentName

  useEffect(() => {
    if (!agentName && wsDebounceRef.current) {
      clearTimeout(wsDebounceRef.current)
      wsDebounceRef.current = null
    }
    return () => {
      if (wsDebounceRef.current) {
        clearTimeout(wsDebounceRef.current)
        wsDebounceRef.current = null
      }
    }
  }, [agentName])

  const bindings: ChannelBinding[] = useMemo(
    () =>
      agentName
        ? DETAIL_CHANNELS.map((channel) => ({
            channel,
            handler: () => {
              if (wsDebounceRef.current) clearTimeout(wsDebounceRef.current)
              wsDebounceRef.current = setTimeout(() => {
                void useAgentsStore.getState().fetchAgentDetail(agentNameRef.current)
              }, WS_DEBOUNCE_MS)
            },
          }))
        : EMPTY_BINDINGS,
    [agentName],
  )

  const { connected, setupError } = useWebSocket({ bindings })
  return { wsConnected: connected, wsSetupError: setupError }
}

export function useAgentDetailData(agentName: string): UseAgentDetailDataReturn {
  const slice = useDetailStoreSlice()
  useDetailLifecycle(agentName)
  const { wsConnected, wsSetupError } = useDetailWebSocket(agentName)

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
    if (!agentName) return
    void useAgentsStore.getState().fetchMoreActivity(agentName)
  }, [agentName])

  if (!agentName) return EMPTY_RETURN

  return {
    ...slice,
    performanceCards,
    insights,
    wsConnected,
    wsSetupError,
    fetchMoreActivity,
  }
}
