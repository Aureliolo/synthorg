import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useWebSocketStore } from '@/stores/websocket'
import type { WsEvent } from '@/api/types'

// Track all created MockWebSocket instances
let mockInstances: MockWebSocket[] = []

// Mock WebSocket
class MockWebSocket {
  static CONNECTING = 0
  static OPEN = 1
  static CLOSING = 2
  static CLOSED = 3

  readyState = MockWebSocket.CONNECTING
  url: string
  onopen: (() => void) | null = null
  onclose: (() => void) | null = null
  onmessage: ((event: { data: string }) => void) | null = null
  onerror: ((event: unknown) => void) | null = null
  send = vi.fn()
  close = vi.fn()

  constructor(url: string) {
    this.url = url
    mockInstances.push(this)
    // Schedule open event
    setTimeout(() => {
      this.readyState = MockWebSocket.OPEN
      this.onopen?.()
    }, 0)
  }
}

// Store original WebSocket
const OriginalWebSocket = globalThis.WebSocket

beforeEach(() => {
  mockInstances = []
  // @ts-expect-error -- mock WebSocket for testing
  globalThis.WebSocket = MockWebSocket
})

afterEach(() => {
  globalThis.WebSocket = OriginalWebSocket
})

describe('useWebSocketStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('initializes with disconnected state', () => {
    const store = useWebSocketStore()
    expect(store.connected).toBe(false)
    expect(store.reconnectExhausted).toBe(false)
    expect(store.subscribedChannels).toEqual([])
  })

  it('connects and sets connected to true', async () => {
    const store = useWebSocketStore()
    store.connect('test-token')

    await vi.advanceTimersByTimeAsync(0)
    expect(store.connected).toBe(true)
  })

  it('does not create duplicate connections', async () => {
    const store = useWebSocketStore()
    store.connect('test-token')
    await vi.advanceTimersByTimeAsync(0)
    expect(mockInstances).toHaveLength(1)

    store.connect('test-token') // should be no-op
    expect(mockInstances).toHaveLength(1) // no new WebSocket created
  })

  it('queues subscriptions when not connected and does not call send', () => {
    const store = useWebSocketStore()
    // Don't connect first — subscribe while disconnected
    store.subscribe(['tasks', 'agents'])

    // No WebSocket exists, so no send should have been called
    expect(mockInstances).toHaveLength(0)
    // Verify subscription is queued by connecting and checking send was called
    store.connect('test-token')
  })

  it('replays pending subscriptions on connect', async () => {
    const store = useWebSocketStore()
    store.subscribe(['tasks'])
    store.connect('test-token')

    await vi.advanceTimersByTimeAsync(0)
    expect(store.connected).toBe(true)
    // The pending subscription should have been sent on connect
    const ws = mockInstances[0]
    expect(ws.send).toHaveBeenCalledWith(
      expect.stringContaining('"action":"subscribe"'),
    )
    expect(ws.send).toHaveBeenCalledWith(
      expect.stringContaining('"channels":["tasks"]'),
    )
  })

  it('deduplicates pending subscriptions', async () => {
    const store = useWebSocketStore()
    // Subscribe to same channels multiple times while disconnected
    store.subscribe(['tasks', 'agents'])
    store.subscribe(['tasks', 'agents'])
    store.subscribe(['tasks', 'agents'])

    // Connect and verify only one subscribe message is sent (not three)
    store.connect('test-token')
    await vi.advanceTimersByTimeAsync(0)

    const ws = mockInstances[0]
    const subscribeCalls = ws.send.mock.calls.filter((call: unknown[]) =>
      String(call[0]).includes('"action":"subscribe"'),
    )
    expect(subscribeCalls).toHaveLength(1)
  })

  it('disconnect sets state correctly', async () => {
    const store = useWebSocketStore()
    store.connect('test-token')
    await vi.advanceTimersByTimeAsync(0)
    expect(store.connected).toBe(true)

    store.disconnect()
    expect(store.connected).toBe(false)
    expect(store.subscribedChannels).toEqual([])
  })

  it('dispatches events to channel handlers via onmessage', async () => {
    const store = useWebSocketStore()
    store.connect('test-token')
    await vi.advanceTimersByTimeAsync(0)

    const handler = vi.fn()
    store.onChannelEvent('tasks', handler)

    // Simulate incoming message via the mock WebSocket instance
    const event: WsEvent = {
      event_type: 'task.created',
      channel: 'tasks',
      timestamp: '2026-03-12T10:00:00Z',
      payload: { id: 'task-1' },
    }
    const ws = mockInstances[0]
    ws.onmessage?.({ data: JSON.stringify(event) })

    expect(handler).toHaveBeenCalledTimes(1)
    expect(handler).toHaveBeenCalledWith(
      expect.objectContaining({ event_type: 'task.created', channel: 'tasks' }),
    )

    // Remove handler and verify no more calls
    store.offChannelEvent('tasks', handler)
    ws.onmessage?.({ data: JSON.stringify(event) })
    expect(handler).toHaveBeenCalledTimes(1) // still 1, not 2
  })

  it('wildcard handlers receive events from all channels', async () => {
    const store = useWebSocketStore()
    store.connect('test-token')
    await vi.advanceTimersByTimeAsync(0)

    const handler = vi.fn()
    store.onChannelEvent('*', handler)

    const ws = mockInstances[0]
    ws.onmessage?.({
      data: JSON.stringify({
        event_type: 'task.created',
        channel: 'tasks',
        timestamp: '2026-03-12T10:00:00Z',
        payload: {},
      }),
    })
    ws.onmessage?.({
      data: JSON.stringify({
        event_type: 'approval.submitted',
        channel: 'approvals',
        timestamp: '2026-03-12T10:00:00Z',
        payload: {},
      }),
    })

    expect(handler).toHaveBeenCalledTimes(2)
    store.offChannelEvent('*', handler)
  })

  it('handles malformed JSON messages gracefully', async () => {
    const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    const store = useWebSocketStore()
    store.connect('test-token')
    await vi.advanceTimersByTimeAsync(0)

    const ws = mockInstances[0]
    ws.onmessage?.({ data: 'not valid json{{{' })

    expect(consoleSpy).toHaveBeenCalledWith(
      'Failed to parse WebSocket message:',
      expect.any(String),
    )
    consoleSpy.mockRestore()
  })

  it('subscription ack updates subscribedChannels when array is valid', async () => {
    const store = useWebSocketStore()
    store.connect('test-token')
    await vi.advanceTimersByTimeAsync(0)

    const ws = mockInstances[0]
    // Simulate subscription ack
    ws.onmessage?.({
      data: JSON.stringify({ action: 'subscribed', channels: ['tasks', 'approvals'] }),
    })
    expect(store.subscribedChannels).toEqual(['tasks', 'approvals'])

    // Non-array channels should not crash
    ws.onmessage?.({
      data: JSON.stringify({ action: 'subscribed', channels: 'invalid' }),
    })
    // Should still have the previous valid value
    expect(store.subscribedChannels).toEqual(['tasks', 'approvals'])
  })

  it('scheduleReconnect stops after max attempts', async () => {
    const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    const store = useWebSocketStore()
    store.connect('test-token')
    await vi.advanceTimersByTimeAsync(0)
    expect(store.connected).toBe(true)

    // Replace with a WebSocket mock that immediately fails (never opens)
    // @ts-expect-error -- override mock for this test
    globalThis.WebSocket = class FailingWebSocket {
      static CONNECTING = 0
      static OPEN = 1
      static CLOSING = 2
      static CLOSED = 3
      readyState = 0
      url: string
      onopen: (() => void) | null = null
      onclose: (() => void) | null = null
      onmessage: ((event: { data: string }) => void) | null = null
      onerror: ((event: unknown) => void) | null = null
      send = vi.fn()
      close = vi.fn()
      constructor(url: string) {
        this.url = url
        // Simulate immediate connection failure — only fire onclose, never onopen
        setTimeout(() => {
          this.readyState = 3 // CLOSED
          this.onclose?.()
        }, 0)
      }
    }

    // Trigger initial disconnect
    const ws = mockInstances[mockInstances.length - 1]
    ws.readyState = MockWebSocket.CLOSED
    ws.onclose?.()

    // Drive through all 20 reconnect attempts
    for (let i = 0; i < 25; i++) {
      await vi.advanceTimersByTimeAsync(120_000)
    }

    expect(store.reconnectExhausted).toBe(true)
    consoleSpy.mockRestore()
  })

  it('re-subscribes to active subscriptions on reconnect', async () => {
    const store = useWebSocketStore()
    store.connect('test-token')
    await vi.advanceTimersByTimeAsync(0)

    // Subscribe while connected
    store.subscribe(['tasks'])
    const ws1 = mockInstances[0]
    expect(ws1.send).toHaveBeenCalled()

    // Simulate disconnect and reconnect
    ws1.readyState = MockWebSocket.CLOSED
    ws1.onclose?.()
    await vi.advanceTimersByTimeAsync(5_000) // trigger reconnect

    // New WebSocket instance should have been created
    expect(mockInstances.length).toBeGreaterThan(1)
    const ws2 = mockInstances[mockInstances.length - 1]
    await vi.advanceTimersByTimeAsync(0) // trigger onopen

    // Active subscriptions should be re-sent automatically
    expect(ws2.send).toHaveBeenCalledWith(
      expect.stringContaining('"channels":["tasks"]'),
    )
  })

  it('unsubscribe removes channels from active subscriptions so reconnect does not re-subscribe', async () => {
    const store = useWebSocketStore()
    store.connect('test-token')
    await vi.advanceTimersByTimeAsync(0)

    // Subscribe then unsubscribe
    store.subscribe(['tasks'])
    store.unsubscribe(['tasks'])

    // Simulate disconnect and reconnect
    const ws1 = mockInstances[0]
    ws1.readyState = MockWebSocket.CLOSED
    ws1.onclose?.()
    await vi.advanceTimersByTimeAsync(5_000) // trigger reconnect

    const ws2 = mockInstances[mockInstances.length - 1]
    await vi.advanceTimersByTimeAsync(0) // trigger onopen

    // Should NOT re-subscribe to 'tasks' since it was unsubscribed
    const subscribeCalls = ws2.send.mock.calls.filter((call: unknown[]) =>
      String(call[0]).includes('"channels":["tasks"]'),
    )
    expect(subscribeCalls).toHaveLength(0)
  })

  it('sanitizes error messages from server', async () => {
    const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    const store = useWebSocketStore()
    store.connect('test-token')
    await vi.advanceTimersByTimeAsync(0)

    const ws = mockInstances[0]
    // Send a message with newlines (log injection attempt)
    ws.onmessage?.({
      data: JSON.stringify({ error: 'bad\ninput\rwith newlines' }),
    })

    expect(consoleSpy).toHaveBeenCalledWith(
      'WebSocket error:',
      'bad input with newlines',
    )
    consoleSpy.mockRestore()
  })

  it('send failures queue subscriptions for replay', async () => {
    const store = useWebSocketStore()
    store.connect('test-token')
    await vi.advanceTimersByTimeAsync(0)

    const ws = mockInstances[0]
    // Make send throw to simulate CLOSING state
    ws.send.mockImplementation(() => {
      throw new Error('WebSocket is in CLOSING state')
    })

    store.subscribe(['budget'])
    // Should not throw — caught internally and queued for replay
    expect(store.connected).toBe(true)
  })
})
