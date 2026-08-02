import { renderHook, waitFor } from '@testing-library/react'

import type { WsEvent } from '@/api/types/websocket'
import { useOrgQuestions } from '@/pages/chat/use-org-questions'
import { useOrgQuestionsStore } from '@/stores/org-questions'

/**
 * The hook's whole job is wiring: hydrate on mount, poll as the fallback, and
 * hand every approvals frame to the store. A channel typo or a dropped
 * ``useWebSocket`` call leaves the page silently waiting on the poll interval,
 * which no page-level test can tell from a working socket.
 */

const mockFetchQuestions = vi.fn().mockResolvedValue(undefined)
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
    start: mockPollingStart,
    stop: mockPollingStop,
  }),
}))

function wsEvent(actionType: string): WsEvent {
  return {
    event_type: 'approval.submitted',
    channel: 'approvals',
    timestamp: '2026-08-02T10:00:00Z',
    payload: { approval: { id: 'question-1', action_type: actionType } },
  }
}

describe('useOrgQuestions', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useOrgQuestionsStore.getState().reset()
    useOrgQuestionsStore.setState({
      fetchQuestions: mockFetchQuestions,
      handleWsEvent: mockHandleWsEvent,
    })
  })

  it('hydrates on mount, so a reload does not lose a waiting question', async () => {
    renderHook(() => useOrgQuestions())
    await waitFor(() => {
      expect(mockFetchQuestions).toHaveBeenCalledTimes(1)
    })
  })

  it('starts polling on mount and stops on unmount', () => {
    const { unmount } = renderHook(() => useOrgQuestions())
    expect(mockPollingStart).toHaveBeenCalledTimes(1)

    unmount()
    expect(mockPollingStop).toHaveBeenCalledTimes(1)
  })

  it('binds the approvals channel and forwards every frame to the store', async () => {
    const { useWebSocket } = await import('@/hooks/useWebSocket')
    renderHook(() => useOrgQuestions())

    const callArgs = vi.mocked(useWebSocket).mock.calls[0]?.[0]
    expect(callArgs?.bindings.map((b) => b.channel)).toEqual(['approvals'])

    const binding = callArgs?.bindings[0]
    const event = wsEvent('clarify:question')
    binding?.handler(event)
    expect(mockHandleWsEvent).toHaveBeenCalledWith(event)
  })

  it('forwards a non-question approval frame too, and lets the store decide', async () => {
    // The channel carries every approval in the org. Filtering here would put
    // the question predicate in two places; the store owns it.
    const { useWebSocket } = await import('@/hooks/useWebSocket')
    renderHook(() => useOrgQuestions())

    const binding = vi.mocked(useWebSocket).mock.calls[0]?.[0].bindings[0]
    const event = wsEvent('comms:external')
    binding?.handler(event)

    expect(mockHandleWsEvent).toHaveBeenCalledWith(event)
  })

  it('surfaces the store list, its load error and its truncation flag', () => {
    useOrgQuestionsStore.setState({ error: 'Connection lost', hasMore: true })
    const { result } = renderHook(() => useOrgQuestions())
    expect(result.current.error).toBe('Connection lost')
    expect(result.current.hasMore).toBe(true)
    expect(result.current.questions).toEqual([])
  })
})
