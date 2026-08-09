import { renderHook, waitFor } from '@testing-library/react'
import { useAgentsStore } from '@/stores/agents'
import { useCompanyStore } from '@/stores/company'
import { useOrgChartData } from '@/hooks/useOrgChartData'
import type { WsEvent } from '@/api/types/websocket'

const mockFetchCompanyData = vi.fn().mockResolvedValue(undefined)
const mockFetchDepartmentHealths = vi.fn().mockResolvedValue(undefined)
const mockCompanyWsEvent = vi.fn<(event: WsEvent) => boolean>()
const mockAgentsWsEvent = vi.fn()

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

function frame(eventType: WsEvent['event_type']): WsEvent {
  return {
    event_type: eventType,
    channel: 'agents',
    timestamp: '2026-08-09T10:00:00Z',
    payload: {},
  }
}

/** Deliver a frame to every channel handler the hook registered. */
async function deliver(event: WsEvent): Promise<void> {
  const { useWebSocket } = await import('@/hooks/useWebSocket')
  for (const binding of vi.mocked(useWebSocket).mock.calls[0]![0].bindings) {
    binding.handler(event)
  }
}

/** The freshness predicate the hook handed to its polling loop. */
async function skipIfFresh(): Promise<boolean> {
  const { usePolling } = await import('@/hooks/usePolling')
  return vi.mocked(usePolling).mock.calls[0]![2]!.skipIfFresh!()
}

describe('useOrgChartData live sync', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockCompanyWsEvent.mockReturnValue(false)
    useCompanyStore.setState({
      config: null,
      departmentHealths: [],
      loading: false,
      error: null,
      fetchCompanyData: mockFetchCompanyData,
      fetchDepartmentHealths: mockFetchDepartmentHealths,
      updateFromWsEvent: mockCompanyWsEvent,
    })
    useAgentsStore.setState({ updateFromWsEvent: mockAgentsWsEvent })
  })

  it('skips the health poll after a frame that refetched company data', async () => {
    mockCompanyWsEvent.mockReturnValue(true)
    renderHook(() => useOrgChartData())
    await waitFor(() => {
      expect(mockPollingStart).toHaveBeenCalled()
    })

    expect(await skipIfFresh()).toBe(false)
    await deliver(frame('agent.hired'))
    expect(await skipIfFresh()).toBe(true)
  })

  it('keeps polling health through a status frame the company store ignores', async () => {
    // Runtime status arrives over the socket on the same channel and refetches
    // nothing. Counting it as freshness would let a busy org suppress the
    // department-health poll for as long as its agents keep changing status.
    renderHook(() => useOrgChartData())
    await waitFor(() => {
      expect(mockPollingStart).toHaveBeenCalled()
    })

    await deliver(frame('agent.status_changed'))
    expect(await skipIfFresh()).toBe(false)
    expect(mockAgentsWsEvent).toHaveBeenCalledWith(frame('agent.status_changed'))
  })
})
