import { renderHook, waitFor } from '@testing-library/react'
import { useApprovalsStore } from '@/stores/approvals'
import { useApprovalsData } from '@/hooks/useApprovalsData'
import { makeApproval } from '../helpers/factories'

const mockFetchApprovals = vi.fn().mockResolvedValue(undefined)
const mockHandleWsEvent = vi.fn()
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
  useApprovalsStore.setState({
    approvals: [],
    selectedApproval: null,
    total: 0,
    loading: false,
    loadingDetail: false,
    error: null,
    detailError: null,
    selectedIds: new Set(),
    fetchApprovals: mockFetchApprovals,
    handleWsEvent: mockHandleWsEvent,
  })
}

describe('useApprovalsData', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    resetStore()
  })

  it('calls fetchApprovals on mount', async () => {
    renderHook(() => useApprovalsData())
    await waitFor(() => {
      expect(mockFetchApprovals).toHaveBeenCalledWith({ limit: 200 })
    })
  })

  it('returns loading state from store', () => {
    useApprovalsStore.setState({ loading: true })
    const { result } = renderHook(() => useApprovalsData())
    expect(result.current.loading).toBe(true)
  })

  it('returns approvals from store', () => {
    const items = [makeApproval('1'), makeApproval('2')]
    useApprovalsStore.setState({ approvals: items })
    const { result } = renderHook(() => useApprovalsData())
    expect(result.current.approvals).toHaveLength(2)
  })

  it('returns error from store', () => {
    useApprovalsStore.setState({ error: 'Something went wrong' })
    const { result } = renderHook(() => useApprovalsData())
    expect(result.current.error).toBe('Something went wrong')
  })

  it('starts polling on mount and stops on unmount', () => {
    const { unmount } = renderHook(() => useApprovalsData())
    expect(mockPollingStart).toHaveBeenCalledTimes(1)

    unmount()
    expect(mockPollingStop).toHaveBeenCalledTimes(1)
  })

  it('sets up WebSocket with approvals channel', async () => {
    const { useWebSocket } = await import('@/hooks/useWebSocket')
    renderHook(() => useApprovalsData())
    const callArgs = vi.mocked(useWebSocket).mock.calls[0]![0]
    const channels = callArgs.bindings.map((b) => b.channel)
    expect(channels).toEqual(['approvals'])
  })

  it('returns wsConnected from WebSocket hook', () => {
    const { result } = renderHook(() => useApprovalsData())
    expect(result.current.wsConnected).toBe(true)
  })

  it('returns selectedIds from store', () => {
    useApprovalsStore.setState({ selectedIds: new Set(['1', '2']) })
    const { result } = renderHook(() => useApprovalsData())
    expect(result.current.selectedIds.size).toBe(2)
  })

  it('returns total from store', () => {
    useApprovalsStore.setState({ total: 42 })
    const { result } = renderHook(() => useApprovalsData())
    expect(result.current.total).toBe(42)
  })
})
