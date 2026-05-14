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

export function useAgentDetailData(agentName: string): UseAgentDetailDataReturn {
  const agent = useAgentsStore((s) => s.selectedAgent)
  const performance = useAgentsStore((s) => s.performance)
  const agentTasks = useAgentsStore((s) => s.agentTasks)
  const activity = useAgentsStore((s) => s.activity)
  const activityTotal = useAgentsStore((s) => s.activityTotal)
  const careerHistory = useAgentsStore((s) => s.careerHistory)
  const loading = useAgentsStore((s) => s.detailLoading)
  const error = useAgentsStore((s) => s.detailError)

  // Initial fetch -- skip when agentName is empty (missing route param)
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

  // Polling for refreshes -- only when agentName is truthy
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

  // WebSocket -- debounce to coalesce burst events into a single refetch
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

  const { connected: wsConnected, setupError: wsSetupError } = useWebSocket({ bindings })

  // Derived data
  const performanceCards = useMemo(
    () => (performance ? computePerformanceCards(performance) : []),
    [performance],
  )

  const insights = useMemo(
    () => (agent ? generateInsights(agent, performance) : []),
    [agent, performance],
  )

  // Load more activity -- store-level activityLoading prevents duplicates.
  // Cursor state is held on the store (``activityNextCursor`` /
  // ``activityHasMore``); the hook just kicks off the fetch when
  // invoked by the UI.
  const fetchMoreActivity = useCallback(() => {
    if (!agentName) return
    void useAgentsStore.getState().fetchMoreActivity(agentName)
  }, [agentName])

  if (!agentName) return EMPTY_RETURN

  return {
    agent,
    performance,
    performanceCards,
    insights,
    agentTasks,
    activity,
    activityTotal,
    careerHistory,
    loading,
    error,
    wsConnected,
    wsSetupError,
    fetchMoreActivity,
  }
}
