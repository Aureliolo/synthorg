import { renderHook } from '@testing-library/react'
import { useWebSocket } from '@/hooks/useWebSocket'
import { useWebSocketStore } from '@/stores/websocket'
import { useAuthStore } from '@/stores/auth'

function resetStores() {
  sessionStorage.clear()
  localStorage.clear()
  useAuthStore.setState({
    authStatus: 'unauthenticated',
    user: null,
    loading: false,
  })
  useWebSocketStore.getState().disconnect()
  useWebSocketStore.setState({
    connected: false,
    reconnectExhausted: false,
    subscribedChannels: [],
  })
}

describe('useWebSocket', () => {
  beforeEach(() => {
    resetStores()
    vi.clearAllMocks()
  })

  it('does not connect when not authenticated', () => {
    const connectSpy = vi.spyOn(useWebSocketStore.getState(), 'connect')
    const handler = vi.fn()

    renderHook(() =>
      useWebSocket({
        bindings: [{ channel: 'tasks', handler }],
      }),
    )

    expect(connectSpy).not.toHaveBeenCalled()
  })

  it('connects and subscribes when authenticated', () => {
    useAuthStore.setState({ authStatus: 'authenticated' })
    useWebSocketStore.setState({ connected: true })

    const subscribeSpy = vi.spyOn(useWebSocketStore.getState(), 'subscribe')
    const onChannelSpy = vi.spyOn(
      useWebSocketStore.getState(),
      'onChannelEvent',
    )
    const handler = vi.fn()

    renderHook(() =>
      useWebSocket({
        bindings: [{ channel: 'tasks', handler }],
      }),
    )

    expect(subscribeSpy).toHaveBeenCalledWith(['tasks'], undefined)
    expect(onChannelSpy).toHaveBeenCalledWith('tasks', handler)
  })

  it('removes handlers AND unsubscribes channels on unmount', async () => {
    // Contract: cleanup must remove per-handler registrations AND
    // call wsStore.unsubscribe for the channels the hook subscribed
    // to. Without this, cleanup only unwinds handlers, leaking the
    // channel subscriptions in the store's subscribedChannels set
    // and keeping stale traffic routed to the unmounted view.
    useAuthStore.setState({ authStatus: 'authenticated' })
    useWebSocketStore.setState({ connected: true })

    const subscribeSpy = vi.spyOn(useWebSocketStore.getState(), 'subscribe')
    const unsubscribeSpy = vi.spyOn(useWebSocketStore.getState(), 'unsubscribe')
    const offChannelSpy = vi.spyOn(
      useWebSocketStore.getState(),
      'offChannelEvent',
    )
    const handler = vi.fn()

    const { unmount } = renderHook(() =>
      useWebSocket({
        bindings: [{ channel: 'tasks', handler }],
      }),
    )

    // Wait for the effect's setup() to complete the subscribe step
    // so the local ``subscribed`` flag is true before we unmount;
    // otherwise the cleanup races setup and the unsubscribe branch
    // is skipped. Using ``vi.waitFor`` instead of counting microtask
    // flushes keeps the test robust if the hook's async flow grows
    // more await steps later.
    await vi.waitFor(() => {
      expect(subscribeSpy).toHaveBeenCalledWith(['tasks'], undefined)
    })

    unmount()

    expect(offChannelSpy).toHaveBeenCalledWith('tasks', handler)
    expect(unsubscribeSpy).toHaveBeenCalledWith(['tasks'])
  })

  it('deduplicates channels from multiple bindings', () => {
    useAuthStore.setState({ authStatus: 'authenticated' })
    useWebSocketStore.setState({ connected: true })

    const subscribeSpy = vi.spyOn(useWebSocketStore.getState(), 'subscribe')
    const handler1 = vi.fn()
    const handler2 = vi.fn()

    renderHook(() =>
      useWebSocket({
        bindings: [
          { channel: 'tasks', handler: handler1 },
          { channel: 'tasks', handler: handler2 },
        ],
      }),
    )

    expect(subscribeSpy).toHaveBeenCalledWith(['tasks'], undefined)
  })

  it('skips setup when explicitly disabled', () => {
    useAuthStore.setState({ authStatus: 'authenticated' })
    const connectSpy = vi.spyOn(useWebSocketStore.getState(), 'connect')
    const handler = vi.fn()

    renderHook(() =>
      useWebSocket({
        bindings: [{ channel: 'tasks', handler }],
        enabled: false,
      }),
    )

    expect(connectSpy).not.toHaveBeenCalled()
  })

  it('returns connection status from store', () => {
    useWebSocketStore.setState({ connected: true, reconnectExhausted: false })

    const handler = vi.fn()
    const { result } = renderHook(() =>
      useWebSocket({
        bindings: [{ channel: 'tasks', handler }],
        enabled: false,
      }),
    )

    expect(result.current.connected).toBe(true)
    expect(result.current.reconnectExhausted).toBe(false)
    expect(result.current.setupError).toBeNull()
  })
})
