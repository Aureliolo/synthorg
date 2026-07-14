import { renderHook, waitFor } from '@testing-library/react'
import { useAgentsStore } from '@/stores/agents'
import { useAgentsData } from '@/hooks/useAgentsData'
import { makeAgent } from '../helpers/factories'

const mockFetchAgents = vi.fn().mockResolvedValue(undefined)
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

function resetStore() {
  useAgentsStore.setState({
    agents: [],
    totalAgents: 0,
    listLoading: false,
    listError: null,
    searchQuery: '',
    departmentFilter: null,
    statusFilter: null,
    sortBy: 'name',
    sortDirection: 'asc',
    fetchAgents: mockFetchAgents,
  })
}

describe('useAgentsData', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    resetStore()
  })

  it('calls fetchAgents on mount', async () => {
    renderHook(() => useAgentsData())
    await waitFor(() => {
      expect(mockFetchAgents).toHaveBeenCalledTimes(1)
    })
  })

  it('returns loading state from store', () => {
    useAgentsStore.setState({ listLoading: true })
    const { result } = renderHook(() => useAgentsData())
    expect(result.current.loading).toBe(true)
  })

  it('returns agents from store', () => {
    useAgentsStore.setState({ agents: [makeAgent('alice'), makeAgent('bob')] })
    const { result } = renderHook(() => useAgentsData())
    expect(result.current.agents).toHaveLength(2)
  })

  it('returns error from store', () => {
    useAgentsStore.setState({ listError: 'Network error' })
    const { result } = renderHook(() => useAgentsData())
    expect(result.current.error).toBe('Network error')
  })

  it('sets up WebSocket with 1 channel binding (agents)', async () => {
    const { useWebSocket } = await import('@/hooks/useWebSocket')
    renderHook(() => useAgentsData())

    const callArgs = vi.mocked(useWebSocket).mock.calls[0]![0]
    const channels = callArgs.bindings.map((b) => b.channel)
    expect(channels).toEqual(['agents'])
  })

  it('starts polling on mount', async () => {
    renderHook(() => useAgentsData())
    await waitFor(() => {
      expect(mockPollingStart).toHaveBeenCalled()
    })
  })

  it('returns filtered agents based on store filters', () => {
    useAgentsStore.setState({
      agents: [
        makeAgent('alice', { department: 'engineering' }),
        makeAgent('bob', { department: 'product' }),
      ],
      departmentFilter: 'engineering',
    })
    const { result } = renderHook(() => useAgentsData())
    expect(result.current.filteredAgents).toHaveLength(1)
    expect(result.current.filteredAgents[0]!.name).toBe('alice')
  })

  describe('WebSocket debounce', () => {
    let wsHandler: (...args: unknown[]) => void

    async function setupHandler() {
      const { useWebSocket } = await import('@/hooks/useWebSocket')
      renderHook(() => useAgentsData())
      const bindings = vi.mocked(useWebSocket).mock.calls[0]![0].bindings
      wsHandler = bindings[0]!.handler as (...args: unknown[]) => void
      // Clear the initial fetchAgents call from mount
      mockFetchAgents.mockClear()
    }

    beforeEach(() => {
      vi.useFakeTimers()
    })

    afterEach(() => {
      vi.useRealTimers()
    })

    it('does not call fetchAgents synchronously on WS event', async () => {
      await setupHandler()
      wsHandler()
      expect(mockFetchAgents).not.toHaveBeenCalled()
    })

    it('calls fetchAgents after 300ms debounce', async () => {
      await setupHandler()
      wsHandler()
      vi.advanceTimersByTime(300)
      expect(mockFetchAgents).toHaveBeenCalledTimes(1)
    })

    it('coalesces burst events into a single fetch', async () => {
      await setupHandler()
      for (let i = 0; i < 5; i++) wsHandler()
      vi.advanceTimersByTime(300)
      expect(mockFetchAgents).toHaveBeenCalledTimes(1)
    })

    it('resets debounce timer on subsequent event within window', async () => {
      await setupHandler()
      wsHandler()
      vi.advanceTimersByTime(200)
      wsHandler() // resets the 300ms window
      vi.advanceTimersByTime(200)
      expect(mockFetchAgents).not.toHaveBeenCalled() // only 200ms since last event
      vi.advanceTimersByTime(100)
      expect(mockFetchAgents).toHaveBeenCalledTimes(1) // 300ms since last event
    })

    it('cleans up timeout on unmount', async () => {
      const { useWebSocket } = await import('@/hooks/useWebSocket')
      const { unmount } = renderHook(() => useAgentsData())
      const bindings = vi.mocked(useWebSocket).mock.calls[0]![0].bindings
      const handler = bindings[0]!.handler as (...args: unknown[]) => void
      mockFetchAgents.mockClear()

      handler()
      unmount()
      vi.advanceTimersByTime(300)
      expect(mockFetchAgents).not.toHaveBeenCalled()
    })
  })
})
