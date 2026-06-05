import { beforeEach, describe, expect, it, vi } from 'vitest'
import { renderHook } from '@testing-library/react'
import type { OverviewMetrics } from '@/api/types/analytics'
import { useBudgetStore } from '@/stores/budget'

const mockFetchBudgetData = vi.fn()
const mockFetchOverview = vi.fn()
const mockUpdateFromWsEvent = vi.fn()
const mockSetAggregationPeriod = vi.fn()
const mockFetchTrends = vi.fn()

const { mockPollingStart, mockPollingStop } = vi.hoisted(() => ({
  mockPollingStart: vi.fn(),
  mockPollingStop: vi.fn(),
}))

vi.mock('@/hooks/useWebSocket', () => ({
  useWebSocket: vi.fn().mockReturnValue({
    connected: true,
    reconnectExhausted: false,
    setupError: null,
  }),
}))

vi.mock('@/hooks/usePolling', () => ({
  usePolling: vi.fn().mockReturnValue({
    active: false,
    error: null,
    start: mockPollingStart,
    stop: mockPollingStop,
  }),
}))

import { useWebSocket } from '@/hooks/useWebSocket'
import { usePolling } from '@/hooks/usePolling'
import { useBudgetData } from '@/hooks/useBudgetData'

function resetStore() {
  useBudgetStore.setState({
    budgetConfig: null,
    overview: null,
    forecast: null,
    costRecords: [],
    trends: null,
    activities: [],
    agentNameMap: new Map(),
    agentDeptMap: new Map(),
    aggregationPeriod: 'daily',
    loading: false,
    error: null,
    fetchBudgetData: mockFetchBudgetData,
    fetchOverview: mockFetchOverview,
    fetchTrends: mockFetchTrends,
    setAggregationPeriod: mockSetAggregationPeriod,
    pushActivity: vi.fn(),
    updateFromWsEvent: mockUpdateFromWsEvent,
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  resetStore()
})

describe('useBudgetData', () => {
  it('calls fetchBudgetData on mount', () => {
    renderHook(() => useBudgetData())
    expect(mockFetchBudgetData).toHaveBeenCalledOnce()
  })

  it('returns loading state from store', () => {
    useBudgetStore.setState({ loading: true })
    const { result } = renderHook(() => useBudgetData())
    expect(result.current.loading).toBe(true)
  })

  it('returns error from store', () => {
    useBudgetStore.setState({ error: 'test error' })
    const { result } = renderHook(() => useBudgetData())
    expect(result.current.error).toBe('test error')
  })

  it('returns overview from store', () => {
    const overview: OverviewMetrics = {
      total_tasks: 10,
      tasks_by_status: {},
      total_agents: 5,
      total_cost: 42,
      budget_remaining: 58,
      budget_used_percent: 42,
      cost_7d_trend: [],
      active_agents_count: 3,
      idle_agents_count: 2,
      currency: 'EUR',
    }
    useBudgetStore.setState({ overview })
    const { result } = renderHook(() => useBudgetData())
    expect(result.current.overview).toBe(overview)
  })

  it('returns aggregationPeriod and setAggregationPeriod', () => {
    const { result } = renderHook(() => useBudgetData())
    expect(result.current.aggregationPeriod).toBe('daily')
    expect(result.current.setAggregationPeriod).toBe(mockSetAggregationPeriod)
  })

  it('sets up WebSocket with budget channel', () => {
    renderHook(() => useBudgetData())
    const wsCall = vi.mocked(useWebSocket).mock.calls[0]![0]
    const channels = wsCall.bindings.map((b) => b.channel)
    expect(channels).toEqual(['budget'])
  })

  it('returns wsConnected from useWebSocket', () => {
    const { result } = renderHook(() => useBudgetData())
    expect(result.current.wsConnected).toBe(true)
  })

  it('starts polling on mount', () => {
    renderHook(() => useBudgetData())
    expect(mockPollingStart).toHaveBeenCalledOnce()
  })

  it('passes 30s interval to usePolling', () => {
    renderHook(() => useBudgetData())
    const pollCall = vi.mocked(usePolling).mock.calls[0]!
    expect(pollCall[1]).toBe(30_000)
  })

  it('stops polling on unmount', () => {
    const { unmount } = renderHook(() => useBudgetData())
    unmount()
    expect(mockPollingStop).toHaveBeenCalledOnce()
  })
})
