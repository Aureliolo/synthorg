import { renderHook, waitFor } from '@testing-library/react'
import { useAnalyticsStore } from '@/stores/analytics'
import { useDashboardData } from '@/hooks/useDashboardData'

const mockFetchDashboardData = vi.fn().mockResolvedValue(undefined)
const mockFetchOverview = vi.fn().mockResolvedValue(undefined)
const mockUpdateFromWsEvent = vi.fn()
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
    isRefetching: false,
    start: mockPollingStart,
    stop: mockPollingStop,
  }),
}))

function resetStore() {
  useAnalyticsStore.setState({
    overview: null,
    forecast: null,
    departmentHealths: [],
    activities: [],
    budgetConfig: null,
    orgHealthPercent: null,
    loading: false,
    error: null,
    fetchDashboardData: mockFetchDashboardData,
    fetchOverview: mockFetchOverview,
    updateFromWsEvent: mockUpdateFromWsEvent,
  })
}

describe('useDashboardData', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    resetStore()
  })

  it('calls fetchDashboardData on mount', async () => {
    renderHook(() => useDashboardData())
    await waitFor(() => {
      expect(mockFetchDashboardData).toHaveBeenCalledTimes(1)
    })
  })

  it('returns loading state from store', () => {
    useAnalyticsStore.setState({ loading: true })
    const { result } = renderHook(() => useDashboardData())
    expect(result.current.loading).toBe(true)
  })

  it('returns overview from store', () => {
    const mockOverview = {
      total_tasks: 10,
      tasks_by_status: {
        created: 0, assigned: 0, in_progress: 0, in_review: 0, completed: 0,
        blocked: 0, failed: 0, interrupted: 0, suspended: 0, cancelled: 0, rejected: 0, auth_required: 0,
      },
      total_agents: 5,
      total_cost: 50, budget_remaining: 450, budget_used_percent: 10,
      cost_7d_trend: [], active_agents_count: 3, idle_agents_count: 2,
      currency: 'EUR',
    }
    useAnalyticsStore.setState({ overview: mockOverview })
    const { result } = renderHook(() => useDashboardData())
    expect(result.current.overview).toEqual(mockOverview)
  })

  it('returns error from store', () => {
    useAnalyticsStore.setState({ error: 'Something broke' })
    const { result } = renderHook(() => useDashboardData())
    expect(result.current.error).toBe('Something broke')
  })

  it('sets up WebSocket with exactly 5 channel bindings', async () => {
    const { useWebSocket } = await import('@/hooks/useWebSocket')
    renderHook(() => useDashboardData())

    const callArgs = vi.mocked(useWebSocket).mock.calls[0]![0]
    const channels = callArgs.bindings.map((b) => b.channel)
    expect(channels).toEqual(['tasks', 'agents', 'budget', 'system', 'approvals'])
  })

  it('returns wsConnected from useWebSocket', () => {
    const { result } = renderHook(() => useDashboardData())
    expect(result.current.wsConnected).toBe(true)
  })

  it('starts polling on mount', async () => {
    renderHook(() => useDashboardData())
    await waitFor(() => {
      expect(mockPollingStart).toHaveBeenCalled()
    })
  })
})
