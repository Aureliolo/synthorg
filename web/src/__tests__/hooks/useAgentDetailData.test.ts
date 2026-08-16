import { renderHook, waitFor } from '@testing-library/react'
import { useAgentsStore } from '@/stores/agents'
import { useAgentDetailData } from '@/hooks/useAgentDetailData'
import { makeAgent, makeActivityEvent, makePerformanceSummary } from '../helpers/factories'

const mockFetchAgentDetail = vi.fn().mockResolvedValue(undefined)
const mockClearDetail = vi.fn()
const mockFetchMoreActivity = vi.fn().mockResolvedValue(undefined)
// The real ``usePolling.start()`` invokes the poll function once immediately
// before arming its timer, and that first invocation IS the hook's initial
// fetch. A double that only records the call would let a second mount-time
// fetch reappear unnoticed, which is the defect these tests pin.
const { mockPollingStart, mockPollingStop, pollFnRef } = vi.hoisted(() => {
  const ref: { current: (() => Promise<void>) | null } = { current: null }
  return {
    pollFnRef: ref,
    mockPollingStart: vi.fn(() => {
      void ref.current?.()
    }),
    mockPollingStop: vi.fn(),
  }
})

vi.mock('@/hooks/useWebSocket', () => ({
  useWebSocket: vi.fn().mockReturnValue({
    connected: true,
    reconnectExhausted: false,
    setupError: null,
  }),
}))

vi.mock('@/hooks/usePolling', () => ({
  usePolling: vi.fn((fn: () => Promise<void>) => {
    pollFnRef.current = fn
    return {
      active: false,
      error: null,
      start: mockPollingStart,
      stop: mockPollingStop,
    }
  }),
}))

function resetStore() {
  useAgentsStore.setState({
    selectedAgent: null,
    performance: null,
    agentTasks: [],
    activity: [],
    activityTotal: 0,
    activityLoading: false,
    careerHistory: [],
    detailLoading: false,
    detailError: null,
    fetchAgentDetail: mockFetchAgentDetail,
    clearDetail: mockClearDetail,
    fetchMoreActivity: mockFetchMoreActivity,
  })
}

describe('useAgentDetailData', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    resetStore()
  })

  it('calls fetchAgentDetail on mount with agentName', async () => {
    renderHook(() => useAgentDetailData('alice'))
    await waitFor(() => {
      expect(mockFetchAgentDetail).toHaveBeenCalledWith('alice')
    })
  })

  it('loads the detail block exactly once per mount', async () => {
    // A mount fetch beside the poller's own first invocation loaded the whole
    // block twice on every page open: the agent, its performance, its health,
    // its activity and its history, five requests each time.
    renderHook(() => useAgentDetailData('alice'))
    await waitFor(() => {
      expect(mockFetchAgentDetail).toHaveBeenCalled()
    })
    expect(mockFetchAgentDetail).toHaveBeenCalledTimes(1)
  })

  it('calls clearDetail on unmount', () => {
    const { unmount } = renderHook(() => useAgentDetailData('alice'))
    unmount()
    expect(mockClearDetail).toHaveBeenCalled()
  })

  it('returns empty data when agentName is empty', () => {
    const { result } = renderHook(() => useAgentDetailData(''))
    expect(result.current.agent).toBeNull()
    expect(result.current.loading).toBe(false)
    expect(result.current.wsConnected).toBe(false)
    expect(result.current.fetchMoreActivity).toBeTypeOf('function')
    expect(mockFetchAgentDetail).not.toHaveBeenCalled()
  })

  it('returns agent from store', () => {
    useAgentsStore.setState({ selectedAgent: makeAgent('alice') })
    const { result } = renderHook(() => useAgentDetailData('alice'))
    expect(result.current.agent?.name).toBe('alice')
  })

  it('returns loading and error from store', () => {
    useAgentsStore.setState({ detailLoading: true, detailError: 'Not found' })
    const { result } = renderHook(() => useAgentDetailData('alice'))
    expect(result.current.loading).toBe(true)
    expect(result.current.error).toBe('Not found')
  })

  it('sets up WebSocket with agents and tasks channels', async () => {
    const { useWebSocket } = await import('@/hooks/useWebSocket')
    renderHook(() => useAgentDetailData('alice'))

    const callArgs = vi.mocked(useWebSocket).mock.calls[0]![0]
    const channels = callArgs.bindings.map((b) => b.channel)
    expect(channels).toEqual(['agents', 'tasks'])
  })

  it('uses empty bindings when agentName is empty', async () => {
    const { useWebSocket } = await import('@/hooks/useWebSocket')
    renderHook(() => useAgentDetailData(''))

    const callArgs = vi.mocked(useWebSocket).mock.calls[0]![0]
    expect(callArgs.bindings).toHaveLength(0)
  })

  it('starts polling on mount', async () => {
    renderHook(() => useAgentDetailData('alice'))
    await waitFor(() => {
      expect(mockPollingStart).toHaveBeenCalled()
    })
  })

  it('does not start polling when agentName is empty', () => {
    renderHook(() => useAgentDetailData(''))
    expect(mockPollingStart).not.toHaveBeenCalled()
  })

  it('fetchMoreActivity calls the store action with the agent name', () => {
    useAgentsStore.setState({
      activity: [makeActivityEvent(), makeActivityEvent()],
    })
    const { result } = renderHook(() => useAgentDetailData('alice'))
    result.current.fetchMoreActivity()
    expect(mockFetchMoreActivity).toHaveBeenCalledWith('alice')
  })

  it('returns wsConnected from useWebSocket', () => {
    const { result } = renderHook(() => useAgentDetailData('alice'))
    expect(result.current.wsConnected).toBe(true)
  })

  it('computes performanceCards from store performance', () => {
    useAgentsStore.setState({
      selectedAgent: makeAgent('alice'),
      performance: makePerformanceSummary('alice'),
    })
    const { result } = renderHook(() => useAgentDetailData('alice'))
    expect(result.current.performanceCards).toHaveLength(4)
    expect(
      result.current.performanceCards.map((c) => c.label),
    ).toEqual([
      'TASKS COMPLETED',
      'AVG COMPLETION TIME',
      'SUCCESS RATE',
      'COST PER TASK',
    ])
  })

  it('computes insights from agent and performance', () => {
    useAgentsStore.setState({
      selectedAgent: makeAgent('alice'),
      performance: makePerformanceSummary('alice'),
    })
    const { result } = renderHook(() => useAgentDetailData('alice'))
    expect(result.current.insights).toHaveLength(2)
    expect(result.current.insights[0]).toContain('90.0%')
    expect(result.current.insights[1]).toContain('8.5')
  })

  it('returns wsConnected false when WebSocket disconnected', async () => {
    const { useWebSocket } = await import('@/hooks/useWebSocket')
    vi.mocked(useWebSocket).mockReturnValueOnce({
      connected: false,
      reconnectExhausted: false,
      setupError: null,
    })
    const { result } = renderHook(
      () => useAgentDetailData('alice'),
    )
    expect(result.current.wsConnected).toBe(false)
  })

  it('does not call store fetchMoreActivity when agentName empty', () => {
    const { result } = renderHook(() => useAgentDetailData(''))
    result.current.fetchMoreActivity()
    expect(mockFetchMoreActivity).not.toHaveBeenCalled()
  })

  describe('WebSocket debounce', () => {
    let wsHandler: (...args: unknown[]) => void

    async function setupHandler() {
      const { useWebSocket } = await import('@/hooks/useWebSocket')
      renderHook(() => useAgentDetailData('alice'))
      const bindings = vi.mocked(useWebSocket).mock.calls[0]![0].bindings
      wsHandler = bindings[0]!.handler as (...args: unknown[]) => void
      // Clear the initial fetchAgentDetail call from mount
      mockFetchAgentDetail.mockClear()
    }

    beforeEach(() => {
      vi.useFakeTimers()
    })

    afterEach(() => {
      vi.useRealTimers()
    })

    it('does not call fetchAgentDetail synchronously on WS event', async () => {
      await setupHandler()
      wsHandler()
      expect(mockFetchAgentDetail).not.toHaveBeenCalled()
    })

    it('calls fetchAgentDetail after 300ms debounce', async () => {
      await setupHandler()
      wsHandler()
      vi.advanceTimersByTime(300)
      expect(mockFetchAgentDetail).toHaveBeenCalledTimes(1)
      expect(mockFetchAgentDetail).toHaveBeenCalledWith('alice')
    })

    it('coalesces burst events into a single fetch', async () => {
      await setupHandler()
      for (let i = 0; i < 5; i++) wsHandler()
      vi.advanceTimersByTime(300)
      expect(mockFetchAgentDetail).toHaveBeenCalledTimes(1)
    })

    it('resets debounce timer on subsequent event within window', async () => {
      await setupHandler()
      wsHandler()
      vi.advanceTimersByTime(200)
      wsHandler() // resets the 300ms window
      vi.advanceTimersByTime(200)
      expect(mockFetchAgentDetail).not.toHaveBeenCalled() // only 200ms since last event
      vi.advanceTimersByTime(100)
      expect(mockFetchAgentDetail).toHaveBeenCalledTimes(1)
      expect(mockFetchAgentDetail).toHaveBeenCalledWith('alice')
    })

    it('cleans up timeout on unmount', async () => {
      const { useWebSocket } = await import('@/hooks/useWebSocket')
      const { unmount } = renderHook(() => useAgentDetailData('alice'))
      const bindings = vi.mocked(useWebSocket).mock.calls[0]![0].bindings
      const handler = bindings[0]!.handler as (...args: unknown[]) => void
      mockFetchAgentDetail.mockClear()

      handler()
      unmount()
      vi.advanceTimersByTime(300)
      expect(mockFetchAgentDetail).not.toHaveBeenCalled()
    })

    it('coalesces events across agents and tasks channels', async () => {
      const { useWebSocket } = await import('@/hooks/useWebSocket')
      renderHook(() => useAgentDetailData('alice'))
      const bindings = vi.mocked(useWebSocket).mock.calls[0]![0].bindings
      const agentsHandler = bindings[0]!.handler as (...args: unknown[]) => void
      const tasksHandler = bindings[1]!.handler as (...args: unknown[]) => void
      mockFetchAgentDetail.mockClear()

      agentsHandler()
      tasksHandler()
      agentsHandler()
      vi.advanceTimersByTime(300)
      expect(mockFetchAgentDetail).toHaveBeenCalledTimes(1)
    })
  })
})
