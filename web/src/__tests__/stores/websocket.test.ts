import { http, HttpResponse } from 'msw'
import { useWebSocketStore } from '@/stores/websocket'
import { apiError, successFor } from '@/mocks/handlers'
import type { getWsTicket } from '@/api/endpoints/auth'
import { server } from '@/test-setup'
import type { WsEvent } from '@/api/types/websocket'
import {
  WS_HEARTBEAT_INTERVAL_MS,
  WS_HEARTBEAT_JITTER_MAX,
  WS_MAX_RECONNECT_ATTEMPTS,
  WS_PONG_TIMEOUT_MS,
  WS_PROTOCOL_VERSION,
  WS_RECONNECT_BASE_DELAY,
} from '@/utils/ws-constants'

// The heartbeat scheduler arms its timer at a jittered delay in
// ``WS_HEARTBEAT_INTERVAL_MS * [JITTER_MIN, JITTER_MAX]``. A test that
// advances exactly ``WS_HEARTBEAT_INTERVAL_MS`` misses the first tick
// whenever the random factor exceeds 1.0. Advancing the worst-case
// jittered delay guarantees the tick has fired regardless of
// ``Math.random()``, eliminating that race deterministically.
const WS_HEARTBEAT_MAX_DELAY_MS = Math.ceil(
  WS_HEARTBEAT_INTERVAL_MS * WS_HEARTBEAT_JITTER_MAX,
)

// Shared ticket-exchange controller: tests set `ticketMode` before
// triggering connect() to decide whether the handler returns a
// successful ticket, an envelope error, or an HTTP 401. `ticketCalls`
// records how many exchanges were attempted so tests can assert on
// request coalescing.
type TicketMode =
  | { kind: 'success'; ticket: string; expires_in: number }
  | { kind: 'envelope_error'; message: string }
  | { kind: 'http_401'; message: string }
const ticketState = {
  calls: 0,
  mode: { kind: 'success', ticket: 'test-ticket', expires_in: 30 } as TicketMode,
}

function installTicketHandler() {
  server.use(
    http.post('/api/v1/auth/ws-ticket', () => {
      ticketState.calls += 1
      const mode = ticketState.mode
      if (mode.kind === 'success') {
        return HttpResponse.json(
          successFor<typeof getWsTicket>({
            ticket: mode.ticket,
            expires_in: mode.expires_in,
          }),
        )
      }
      if (mode.kind === 'envelope_error') {
        return HttpResponse.json(apiError(mode.message))
      }
      return HttpResponse.json(apiError(mode.message), { status: 401 })
    }),
  )
}

// ── MockWebSocket ───────────────────────────────────────────

type WsListener = ((event: { data: string }) => void) | null
type WsCloseListener = ((event: { code: number; reason: string }) => void) | null

class MockWebSocket {
  static readonly CONNECTING = 0
  static readonly OPEN = 1
  static readonly CLOSING = 2
  static readonly CLOSED = 3

  readonly CONNECTING = 0
  readonly OPEN = 1
  readonly CLOSING = 2
  readonly CLOSED = 3

  url: string
  readyState = MockWebSocket.CONNECTING
  onopen: (() => void) | null = null
  onclose: WsCloseListener = null
  onerror: (() => void) | null = null
  onmessage: WsListener = null
  sentMessages: string[] = []
  closed = false

  constructor(url: string) {
    this.url = url
    MockWebSocket.instances.push(this)
  }

  send(data: string) {
    this.sentMessages.push(data)
  }

  close(code = 1000, reason = '') {
    this.closed = true
    this.readyState = MockWebSocket.CLOSED
    this.onclose?.({ code, reason })
  }

  // Test helpers
  simulateOpen() {
    this.readyState = MockWebSocket.OPEN
    this.onopen?.()
  }

  simulateMessage(data: unknown) {
    this.onmessage?.({ data: JSON.stringify(data) })
  }

  simulateClose(code = 1006, reason = '') {
    this.readyState = MockWebSocket.CLOSED
    this.onclose?.({ code, reason })
  }

  static instances: MockWebSocket[] = []
  static clear() {
    MockWebSocket.instances = []
  }
  static latest(): MockWebSocket | undefined {
    return MockWebSocket.instances[MockWebSocket.instances.length - 1]
  }
}

// Install MockWebSocket globally. MSW's Node interceptor replaces
// `globalThis.WebSocket` with a non-writable property, so we use
// `Object.defineProperty` to force the swap and restore the original
// descriptor after the suite finishes.
const originalDescriptor = Object.getOwnPropertyDescriptor(globalThis, 'WebSocket')
beforeAll(() => {
  Object.defineProperty(globalThis, 'WebSocket', {
    value: MockWebSocket,
    writable: true,
    configurable: true,
  })
})
afterAll(() => {
  if (originalDescriptor) {
    Object.defineProperty(globalThis, 'WebSocket', originalDescriptor)
  } else {
    // No original descriptor means `WebSocket` was not an own property
    // of the global before this file ran. Delete the property we set so
    // we don't leave behind a faux own-property entry that masks the
    // prototype chain (e.g. polluting later tests that read WebSocket).
    delete (globalThis as { WebSocket?: unknown }).WebSocket
  }
})

function resetStore() {
  // ``teardown()`` is the canonical "fresh test" hook: it clears every
  // module-scope handle (heartbeat / pong / reconnect timers, socket,
  // generation counter, subscriptions, channel handlers) and resets
  // observable state including ``reconnectExhausted`` (which
  // ``disconnect()`` deliberately leaves alone).
  useWebSocketStore.getState().teardown()
  MockWebSocket.clear()
}

// Pump fake-timer backoff ticks AND real macrotasks until the store
// reports exhaustion. The naive ``for (i=0; i<20)`` shape races MSW's
// undici-backed ticket-fetch rejection chain, which settles on REAL
// microtasks + setImmediate hops (``queueMicrotask`` is intentionally
// excluded from ``toFake`` so undici can flush). Without a real-
// macrotask drain between iterations, a fast iteration can advance
// past the 30s backoff ceiling before the previous rejection has
// reached ``scheduleReconnect()``, so the loop runs all 20 iterations
// while ``reconnectAttempts`` lags behind and exhaustion never flips.
// The cap is derived from ``WS_MAX_RECONNECT_ATTEMPTS`` so a future
// bump of the store-side limit drags the test budget along with it
// instead of silently exiting short or capping at a stale literal.
// Throwing on budget exhaustion turns an actual scheduler bug into a
// loud test failure rather than a silent hang or a false negative
// from a downstream assertion.
const RECONNECT_EXHAUSTION_DRAIN_PASSES = WS_MAX_RECONNECT_ATTEMPTS * 3

async function drainUntilReconnectExhausted(): Promise<void> {
  for (let i = 0; i < RECONNECT_EXHAUSTION_DRAIN_PASSES; i++) {
    if (useWebSocketStore.getState().reconnectExhausted) return
    await vi.advanceTimersByTimeAsync(30_000)
    await vi.runAllTimersAsync()
    await new Promise<void>((resolve) => setImmediate(resolve))
    // Re-check after the async drain: exhaustion can flip during the
    // final pass, and a check only at the top of the loop would miss it
    // and throw even though the budget was sufficient.
    if (useWebSocketStore.getState().reconnectExhausted) return
  }
  throw new Error(
    `reconnectExhausted did not flip after ${RECONNECT_EXHAUSTION_DRAIN_PASSES} drain passes`,
  )
}

describe('websocket store', () => {
  beforeEach(async () => {
    // Drain any real macrotask / microtask chain still in flight from a
    // prior test's MSW + undici ticket fetch BEFORE installing fake
    // timers. ``queueMicrotask`` is intentionally excluded from
    // ``toFake`` (so undici can settle), so without this a prior test's
    // response-cleanup hop survives into this test's fake-timer window
    // and can call ``close()`` on the brand-new socket -- the documented
    // residual cross-test race the sibling heartbeat tests annotate
    // their ``retry`` for. Timers are still real here (the previous
    // ``afterEach`` restored them via ``vi.useRealTimers()``), so
    // ``setTimeout(0)`` genuinely yields the macrotask queue and the
    // interleaved ``Promise.resolve()`` flushes microtasks/setImmediate
    // hops between each macrotask turn.
    for (let drain = 0; drain < 4; drain++) {
      await new Promise((resolve) => {
        setTimeout(resolve, 0)
      })
      await Promise.resolve()
    }
    resetStore()
    ticketState.calls = 0
    ticketState.mode = {
      kind: 'success',
      ticket: 'test-ticket',
      expires_in: 30,
    }
    installTicketHandler()
    // Exclude queueMicrotask so undici/MSW internals can flush their
    // promise chains without being held by the fake scheduler.
    vi.useFakeTimers({
      toFake: [
        'setTimeout',
        'clearTimeout',
        'setInterval',
        'clearInterval',
        'Date',
        'requestAnimationFrame',
        'cancelAnimationFrame',
      ],
    })
  })

  afterEach(() => {
    // Global afterEach in test-setup.tsx already runs dismissAll +
    // cancelPendingPersist; duplicating here measurably raised the
    // async-leak count on CI. Keep only the timer-restore, which is
    // specific to this file's fake-timer setup.
    vi.useRealTimers()
  })

  describe('connect', () => {
    it('fetches ticket and creates WebSocket connection without ticket in URL', async () => {
      const connectPromise = useWebSocketStore.getState().connect()
      await vi.runAllTimersAsync()
      await connectPromise

      const ws = MockWebSocket.latest()
      expect(ws).toBeDefined()
      // Ticket should NOT be in the URL (first-message auth)
      expect(ws!.url).not.toContain('ticket=')
    })

    it('keeps connected=false until server sends auth_ok', async () => {
      const connectPromise = useWebSocketStore.getState().connect()
      await vi.runAllTimersAsync()
      await connectPromise

      const ws = MockWebSocket.latest()!
      ws.simulateOpen()
      // The auth message has been sent but the server has not yet
      // ack'd. Closing the flash gap means we wait.
      expect(useWebSocketStore.getState().connected).toBe(false)

      ws.simulateMessage({ action: 'auth_ok' })
      expect(useWebSocketStore.getState().connected).toBe(true)
    })

    it('keeps connected=false when socket closes before auth_ok arrives', async () => {
      const connectPromise = useWebSocketStore.getState().connect()
      await vi.runAllTimersAsync()
      await connectPromise

      const ws = MockWebSocket.latest()!
      ws.simulateOpen()
      ws.simulateClose(4001, 'auth failed')

      expect(useWebSocketStore.getState().connected).toBe(false)
    })

    it('deduplicates concurrent connect calls', async () => {
      const p1 = useWebSocketStore.getState().connect()
      const p2 = useWebSocketStore.getState().connect()

      await vi.runAllTimersAsync()
      await Promise.all([p1, p2])

      expect(ticketState.calls).toBe(1)
    })
  })

  describe('disconnect', () => {
    it('closes socket and resets state', async () => {
      const connectPromise = useWebSocketStore.getState().connect()
      await vi.runAllTimersAsync()
      await connectPromise

      const ws = MockWebSocket.latest()!
      ws.simulateOpen()

      useWebSocketStore.getState().disconnect()

      expect(ws.closed).toBe(true)
      expect(useWebSocketStore.getState().connected).toBe(false)
      expect(useWebSocketStore.getState().subscribedChannels).toEqual([])
    })
  })

  describe('subscribe', () => {
    it('sends subscribe message when connected', async () => {
      const connectPromise = useWebSocketStore.getState().connect()
      await vi.runAllTimersAsync()
      await connectPromise

      const ws = MockWebSocket.latest()!
      ws.simulateOpen()

      useWebSocketStore.getState().subscribe(['tasks', 'approvals'])

      const sent = ws.sentMessages.filter((m) => {
        const parsed = JSON.parse(m) as { action: string }
        return parsed.action === 'subscribe'
      })
      // The subscribe call should have sent exactly one subscribe message
      expect(sent).toHaveLength(1)
      const sub = JSON.parse(sent[0]!) as { channels: string[] }
      expect(sub.channels).toEqual(['tasks', 'approvals'])
    })

    it('queues subscription when not connected and replays on connect', async () => {
      // Subscribe while not connected
      useWebSocketStore.getState().subscribe(['tasks'])

      // Now connect -- the subscription should be replayed
      const connectPromise = useWebSocketStore.getState().connect()
      await vi.runAllTimersAsync()
      await connectPromise

      const ws = MockWebSocket.latest()!
      ws.simulateOpen()

      // Should see auth message + replayed subscription
      const subscribeMsgs = ws.sentMessages.filter((m) => {
        const parsed = JSON.parse(m) as { action: string }
        return parsed.action === 'subscribe'
      })
      expect(subscribeMsgs.length).toBeGreaterThanOrEqual(1)
      const sub = JSON.parse(subscribeMsgs[0]!) as { channels: string[] }
      expect(sub.channels).toEqual(['tasks'])
    })
  })

  describe('unsubscribe', () => {
    it('sends unsubscribe message when connected', async () => {
      const connectPromise = useWebSocketStore.getState().connect()
      await vi.runAllTimersAsync()
      await connectPromise

      const ws = MockWebSocket.latest()!
      ws.simulateOpen()

      useWebSocketStore.getState().subscribe(['tasks'])
      useWebSocketStore.getState().unsubscribe(['tasks'])

      const unsubMessages = ws.sentMessages.filter((m) => {
        const parsed = JSON.parse(m) as { action: string }
        return parsed.action === 'unsubscribe'
      })
      expect(unsubMessages).toHaveLength(1)
    })
  })

  describe('event dispatch', () => {
    it('dispatches events to channel handlers', async () => {
      const handler = vi.fn()
      useWebSocketStore.getState().onChannelEvent('tasks', handler)

      const connectPromise = useWebSocketStore.getState().connect()
      await vi.runAllTimersAsync()
      await connectPromise

      const ws = MockWebSocket.latest()!
      ws.simulateOpen()

      const event: WsEvent = {
        event_type: 'task.created',
        channel: 'tasks',
        timestamp: new Date().toISOString(),
        payload: { task_id: 'test-1' },
      }
      ws.simulateMessage(event)

      expect(handler).toHaveBeenCalledWith(event)
    })

    it('dispatches to wildcard handlers', async () => {
      const wildcardHandler = vi.fn()
      useWebSocketStore.getState().onChannelEvent('*', wildcardHandler)

      const connectPromise = useWebSocketStore.getState().connect()
      await vi.runAllTimersAsync()
      await connectPromise

      const ws = MockWebSocket.latest()!
      ws.simulateOpen()

      const event: WsEvent = {
        event_type: 'task.created',
        channel: 'tasks',
        timestamp: new Date().toISOString(),
        payload: {},
      }
      ws.simulateMessage(event)

      expect(wildcardHandler).toHaveBeenCalledWith(event)
    })

    it('removes handler with offChannelEvent', async () => {
      const handler = vi.fn()
      useWebSocketStore.getState().onChannelEvent('tasks', handler)
      useWebSocketStore.getState().offChannelEvent('tasks', handler)

      const connectPromise = useWebSocketStore.getState().connect()
      await vi.runAllTimersAsync()
      await connectPromise

      const ws = MockWebSocket.latest()!
      ws.simulateOpen()

      ws.simulateMessage({
        event_type: 'task.created',
        channel: 'tasks',
        timestamp: new Date().toISOString(),
        payload: {},
      })

      expect(handler).not.toHaveBeenCalled()
    })

    it('rejects malformed messages that fail isWsEvent validation', async () => {
      const handler = vi.fn()
      useWebSocketStore.getState().onChannelEvent('tasks', handler)

      const connectPromise = useWebSocketStore.getState().connect()
      await vi.runAllTimersAsync()
      await connectPromise

      const ws = MockWebSocket.latest()!
      ws.simulateOpen()

      // Missing required fields
      ws.simulateMessage({ event_type: 'task.created' })
      expect(handler).not.toHaveBeenCalled()

      // Payload is an array (not an object)
      ws.simulateMessage({
        event_type: 'task.created',
        channel: 'tasks',
        timestamp: new Date().toISOString(),
        payload: [1, 2, 3],
      })
      expect(handler).not.toHaveBeenCalled()

      // Payload is null
      ws.simulateMessage({
        event_type: 'task.created',
        channel: 'tasks',
        timestamp: new Date().toISOString(),
        payload: null,
      })
      expect(handler).not.toHaveBeenCalled()
    })
  })

  describe('reconnection', () => {
    it('schedules reconnect on unexpected close', async () => {
      const connectPromise = useWebSocketStore.getState().connect()
      await vi.runAllTimersAsync()
      await connectPromise

      const ws = MockWebSocket.latest()!
      ws.simulateOpen()

      // Simulate unexpected close
      ws.simulateClose()
      expect(useWebSocketStore.getState().connected).toBe(false)

      // Advance timer past the +/-20% jitter ceiling on top of the
      // 1000ms base delay so the reconnect timer fires regardless of
      // which value Math.random produced.
      await vi.advanceTimersByTimeAsync(1200)

      // A new WebSocket should have been created
      expect(MockWebSocket.instances.length).toBeGreaterThan(1)
    })

    it.each([
      // Math.random() result -> expected delay (ms) for base=1000ms, jitter [0.8, 1.2)
      { random: 0, expected: 800, label: 'lower bound' },
      { random: 0.5, expected: 1000, label: 'midpoint' },
      // Math.random() returns values in [0, 1); use 0.999 to lock in the
      // upper-bound multiplier without overshooting the spec.
      { random: 0.999, expected: 1200, label: 'upper bound' },
    ])(
      'applies +/-20% jitter to the reconnect delay ($label)',
      async ({ random, expected }) => {
        const mathRandom = vi.spyOn(Math, 'random').mockReturnValue(random)
        // Hoist the spy declaration above the ``try`` so the
        // ``finally`` block can restore it; the previous shape left
        // the spy in scope only inside ``try`` and never undid it,
        // which leaked across tests because vitest config does not
        // enable ``restoreMocks`` and the global ``afterEach`` does
        // not call ``vi.restoreAllMocks()``.
        const setTimeoutSpy = vi.spyOn(window, 'setTimeout')
        try {
          const connectPromise = useWebSocketStore.getState().connect()
          await vi.runAllTimersAsync()
          await connectPromise

          const ws = MockWebSocket.latest()!
          ws.simulateOpen()

          setTimeoutSpy.mockClear()
          ws.simulateClose()

          // The first ``setTimeout`` after the close is the reconnect
          // timer; subsequent calls (toast queue, etc.) are not the
          // one we care about. With Math.random stubbed, the delay is
          // deterministic at base*(MIN + random * (MAX - MIN)).
          const reconnectCall = setTimeoutSpy.mock.calls.find(
            ([, ms]) => typeof ms === 'number' && ms >= 700 && ms <= 1300,
          )
          expect(reconnectCall).toBeDefined()
          const delay = reconnectCall?.[1] as number
          // Allow +/-1ms slack for floating-point rounding through
          // Math.round() and the post-clamp Math.max(1, ...).
          expect(delay).toBeGreaterThanOrEqual(expected - 1)
          expect(delay).toBeLessThanOrEqual(expected + 1)
        } finally {
          setTimeoutSpy.mockRestore()
          mathRandom.mockRestore()
        }
      },
    )

    it('does not reconnect on intentional disconnect', async () => {
      const connectPromise = useWebSocketStore.getState().connect()
      await vi.runAllTimersAsync()
      await connectPromise

      const ws = MockWebSocket.latest()!
      ws.simulateOpen()

      const instanceCountBefore = MockWebSocket.instances.length
      useWebSocketStore.getState().disconnect()

      await vi.advanceTimersByTimeAsync(5000)

      // No new connections should be made
      expect(MockWebSocket.instances.length).toBe(instanceCountBefore)
    })
  })

  describe('message size gating', () => {
    it('discards oversized messages', async () => {
      const handler = vi.fn()
      useWebSocketStore.getState().onChannelEvent('tasks', handler)

      const connectPromise = useWebSocketStore.getState().connect()
      await vi.runAllTimersAsync()
      await connectPromise

      const ws = MockWebSocket.latest()!
      ws.simulateOpen()

      // Build a full, valid WsEvent envelope (event_type/channel/
      // timestamp/payload/version) whose encoded byte length exceeds
      // the 32 KiB inbound cap. If the test used a trimmed-down shape
      // the store could drop the frame for failing ``isWsEvent``
      // instead of the size gate, and the assertion would pass for
      // the wrong reason.
      const oversized = JSON.stringify({
        version: 1,
        event_type: 'task.created',
        channel: 'tasks',
        timestamp: '2026-04-21T00:00:00Z',
        payload: { task_id: 'stub', text: 'x'.repeat(33_000) },
      })
      expect(new TextEncoder().encode(oversized).byteLength).toBeGreaterThan(
        32_768,
      )
      ws.onmessage?.({ data: oversized })

      expect(handler).not.toHaveBeenCalled()
    })
  })

  describe('reconnect exhaustion', () => {
    it('sets reconnectExhausted after max attempts', async () => {
      // Ticket exchange always fails with non-401 envelope error,
      // triggering reconnect attempts.
      ticketState.mode = { kind: 'envelope_error', message: 'connection refused' }

      await expect(
        useWebSocketStore.getState().connect(),
      ).rejects.toThrow('connection refused')

      // Each failed ticket exchange triggers scheduleReconnect.
      // Advance through every attempt (exponential backoff capped at 30s).
      // The loop bound tracks ``WS_MAX_RECONNECT_ATTEMPTS`` so a future
      // bump of the constant cannot silently exit the loop short.
      for (let i = 0; i < WS_MAX_RECONNECT_ATTEMPTS; i++) {
        await vi.advanceTimersByTimeAsync(30_000)
        await vi.runAllTimersAsync()
      }

      expect(useWebSocketStore.getState().reconnectExhausted).toBe(true)
    })
  })

  describe('ticket 401 handling', () => {
    it('does not reconnect on ticket 401', async () => {
      // Ticket exchange responds with HTTP 401 -- the axios interceptor
      // rejects with AxiosError whose message is the generic
      // "Request failed with status code 401". The store branches on
      // err.response?.status and must not schedule a reconnect.
      ticketState.mode = { kind: 'http_401', message: 'Unauthorized' }

      await expect(
        useWebSocketStore.getState().connect(),
      ).rejects.toThrow(/status code 401|Unauthorized/)

      // Advance time -- no reconnect should be scheduled on 401
      const instancesBefore = MockWebSocket.instances.length
      await vi.advanceTimersByTimeAsync(5000)
      expect(MockWebSocket.instances.length).toBe(instancesBefore)
    })
  })

  describe('first-message auth', () => {
    // Rare Linux-CI flake: diagnostic runs in earlier rounds confirmed
    // `onopenCalled=true, sendCalled=false, instances=1, latestIsSame=true`,
    // i.e. the store's `socket !== thisSocket` guard trips intermittently
    // on Linux runners (unreproducible across 5 local full-suite runs).
    // Root cause remains unclear (likely a microtask race between a prior
    // test's axios+tough-cookie settling chain and this test's `connect`).
    // `retry(3)` keeps CI deterministic until the race is properly isolated;
    // the test itself still exercises the real flow on every attempt.
    it('sends auth ticket as first message on open', { retry: 3 }, async () => {
      ticketState.mode = {
        kind: 'success',
        ticket: 'my-secret-ticket',
        expires_in: 30,
      }

      const connectPromise = useWebSocketStore.getState().connect()
      await vi.runAllTimersAsync()
      await connectPromise

      const ws = MockWebSocket.latest()!
      expect(ws.url).not.toContain('ticket=')

      ws.simulateOpen()

      // First message should be the auth action (exactly 1 message before subscriptions)
      expect(ws.sentMessages).toHaveLength(1)
      const authMsg = JSON.parse(ws.sentMessages[0]!) as { action: string; ticket: string }
      expect(authMsg.action).toBe('auth')
      expect(authMsg.ticket).toBe('my-secret-ticket')
    })
  })

  describe('ack messages', () => {
    it('updates subscribedChannels on ack', async () => {
      const connectPromise = useWebSocketStore.getState().connect()
      await vi.runAllTimersAsync()
      await connectPromise

      const ws = MockWebSocket.latest()!
      ws.simulateOpen()

      ws.simulateMessage({ action: 'subscribed', channels: ['tasks', 'approvals'] })
      expect(useWebSocketStore.getState().subscribedChannels).toEqual(['tasks', 'approvals'])
    })
  })

  describe('heartbeat', () => {
    async function connectAndAuth() {
      const connectPromise = useWebSocketStore.getState().connect()
      await vi.runAllTimersAsync()
      await connectPromise
      const ws = MockWebSocket.latest()!
      ws.simulateOpen()
      ws.simulateMessage({ action: 'auth_ok' })
      // Single microtask yield ensures Zustand subscriber notifications
      // scheduled synchronously inside the auth_ok handler complete
      // before the test body advances fake timers. Do NOT use a real
      // macrotask drain here (e.g. setImmediate) -- that opens a
      // window for unrelated real-macrotask work to run between
      // auth_ok and the heartbeat assertions, including stale MSW
      // response-cleanup chains that can call close() on our
      // brand-new socket.
      await Promise.resolve()
      expect(useWebSocketStore.getState().connected).toBe(true)
      return ws
    }

    // ``retry`` here covers ONLY the residual MSW/undici macrotask
    // race: MSW's ticket fetch settles on REAL microtasks +
    // setImmediate hops (queueMicrotask is intentionally excluded from
    // ``toFake`` so undici can flush; see beforeEach). When the FAKE
    // heartbeat interval fires inside ``advanceTimersByTimeAsync``,
    // a stale macrotask from a prior test's ticket chain can preempt
    // and call ``close()`` on the brand-new socket, fooling identity
    // guards. The teardown-first afterEach in ``test-setup.tsx`` plus
    // the generation-bumping ``teardown()`` action bound this to ~5%;
    // it cannot be fully eliminated without replacing MSW for these
    // tests. The separate (and previously dominant) jitter race --
    // advancing exactly ``WS_HEARTBEAT_INTERVAL_MS`` while the timer
    // is armed at a jittered delay up to ``* JITTER_MAX`` -- is now
    // eliminated deterministically by advancing
    // ``WS_HEARTBEAT_MAX_DELAY_MS``; ``retry`` no longer covers it.
    it('sends a ping every 20s after auth_ok', { retry: 3 }, async () => {
      const ws = await connectAndAuth()
      const beforePings = ws.sentMessages.length

      await vi.advanceTimersByTimeAsync(WS_HEARTBEAT_MAX_DELAY_MS)

      const pings = ws.sentMessages.slice(beforePings).filter((m) => {
        const parsed = JSON.parse(m) as { action?: string }
        return parsed.action === 'ping'
      })
      expect(pings).toHaveLength(1)
    })

    it('does not send pings before auth_ok arrives', async () => {
      const connectPromise = useWebSocketStore.getState().connect()
      await vi.runAllTimersAsync()
      await connectPromise

      const ws = MockWebSocket.latest()!
      ws.simulateOpen()
      // No auth_ok yet.
      await vi.advanceTimersByTimeAsync(WS_HEARTBEAT_INTERVAL_MS + 5_000)

      const pings = ws.sentMessages.filter((m) => {
        const parsed = JSON.parse(m) as { action?: string }
        return parsed.action === 'ping'
      })
      expect(pings).toHaveLength(0)
    })

    // Same residual MSW-vs-fake-timer race as ``sends a ping`` above;
    // see that comment for the ``retry`` rationale. Advance the
    // worst-case jittered delay so the ping has deterministically
    // fired (the pong-timeout timer is only armed after the tick).
    it('clears pong timeout when pong arrives in time', { retry: 3 }, async () => {
      const ws = await connectAndAuth()
      await vi.advanceTimersByTimeAsync(WS_HEARTBEAT_MAX_DELAY_MS)
      ws.simulateMessage({ action: 'pong' })
      // Advance past the pong timeout window; if the timer was cleared
      // the socket stays open.
      await vi.advanceTimersByTimeAsync(WS_PONG_TIMEOUT_MS + 1_000)

      expect(ws.closed).toBe(false)
    })

    // Same residual MSW-vs-fake-timer race as ``sends a ping`` above;
    // see that comment for the ``retry`` rationale. The previously
    // dominant jitter race (advancing exactly the interval while the
    // timer is jittered up to ``* JITTER_MAX``) is gone: advancing
    // ``WS_HEARTBEAT_MAX_DELAY_MS`` guarantees the ping has fired and
    // the pong-timeout timer is armed before the second advance.
    it('closes the socket when no pong arrives within 10s', { retry: 3 }, async () => {
      const ws = await connectAndAuth()

      await vi.advanceTimersByTimeAsync(WS_HEARTBEAT_MAX_DELAY_MS) // ping fired
      await vi.advanceTimersByTimeAsync(WS_PONG_TIMEOUT_MS) // pong timeout

      // After the pong timeout the socket is closed and the store is
      // disconnected. The actual reconnect-after-close path is exercised
      // by ``reconnection > schedules reconnect on unexpected close``
      // which avoids the heartbeat scheduler entirely; combining the two
      // here would force the assertion to navigate doConnect's
      // ticket-fetch chain (settled on real microtasks + setImmediate
      // hops, intentionally outside the fake-timer scheduler -- see
      // ``toFake`` in the beforeEach) and is structurally racy.
      expect(ws.closed).toBe(true)
      expect(useWebSocketStore.getState().connected).toBe(false)
    })

    it('stops the heartbeat on disconnect', async () => {
      const ws = await connectAndAuth()
      const sentBefore = ws.sentMessages.length

      useWebSocketStore.getState().disconnect()
      await vi.advanceTimersByTimeAsync(WS_HEARTBEAT_INTERVAL_MS * 3)

      expect(ws.sentMessages.length).toBe(sentBefore)
    })

    it('teardown() clears every armed timer', async () => {
      const ws = await connectAndAuth()
      // Heartbeat is now armed; arm the pong timer too by advancing
      // through one ping cycle without responding with a pong.
      // ``WS_HEARTBEAT_MAX_DELAY_MS`` is the canonical worst-case
      // jittered delay (ceil(interval * JITTER_MAX)); advancing by it
      // guarantees the tick fired without re-deriving the bound here.
      await vi.advanceTimersByTimeAsync(WS_HEARTBEAT_MAX_DELAY_MS)
      // Sanity: with a heartbeat interval + an unanswered pong timer
      // armed, vitest's fake-timer scheduler must report at least two
      // pending timers. If teardown silently fails to clear them,
      // ``getTimerCount()`` will still be > 0 after the call below.
      expect(vi.getTimerCount()).toBeGreaterThanOrEqual(2)

      const beforeTeardown = ws.sentMessages.length
      const ticketCallsBefore = ticketState.calls
      useWebSocketStore.getState().teardown()

      // Direct assertion: every armed module-scope timer is gone.
      // ``getTimerCount()`` is the unambiguous signal that the
      // teardown's ``stopHeartbeat`` + ``clearTimeout(reconnectTimer)``
      // calls did the right thing -- the indirect ``no further
      // sentMessages / no new instance`` checks below remain as
      // belt-and-braces invariants but cannot, on their own,
      // distinguish "timer cleared" from "timer fired but its
      // closure no-op'd via the socket-identity guard".
      expect(vi.getTimerCount()).toBe(0)

      // Belt-and-braces: advancing well past the heartbeat + pong +
      // reconnect windows must produce zero outbound messages, zero
      // new MockWebSocket instances, AND zero new ticket fetches
      // (the latter is the deterministic signal that no ghost
      // doConnect was kicked off).
      const instancesBefore = MockWebSocket.instances.length
      await vi.advanceTimersByTimeAsync(
        WS_HEARTBEAT_INTERVAL_MS * 5 + WS_RECONNECT_BASE_DELAY * 5,
      )
      expect(ws.sentMessages.length).toBe(beforeTeardown)
      expect(MockWebSocket.instances.length).toBe(instancesBefore)
      expect(ticketState.calls).toBe(ticketCallsBefore)
      expect(useWebSocketStore.getState().connected).toBe(false)
      expect(useWebSocketStore.getState().reconnectExhausted).toBe(false)
    })
  })

  describe('protocol version handling', () => {
    // Refactor-safety invariant for the ``WS_PROTOCOL_VERSION`` and
    // ``WS_PROTOCOL_VERSION + 1`` cases: the version-check uses the
    // imported constant rather than a hardcoded literal. If a future
    // refactor inlines the literal (e.g. ``if (version !== 1)``) and the
    // constant is later bumped to 2, the rejector would let v2 events
    // through while still rejecting the bumped value at acceptance.
    // Pinning both boundary cases to the imported constant keeps the
    // dispatch boundary tied to ``WS_PROTOCOL_VERSION`` rather than to
    // any specific numeric value.
    const versionCases: ReadonlyArray<{
      label: string
      version: number | 'absent'
      shouldDispatch: boolean
    }> = [
      { label: 'mismatched version (999) is discarded', version: 999, shouldDispatch: false },
      { label: 'absent version is treated as v1', version: 'absent', shouldDispatch: true },
      { label: 'explicit version=1 is accepted', version: 1, shouldDispatch: true },
      { label: 'WS_PROTOCOL_VERSION + 1 is rejected', version: WS_PROTOCOL_VERSION + 1, shouldDispatch: false },
      { label: 'exactly WS_PROTOCOL_VERSION is accepted', version: WS_PROTOCOL_VERSION, shouldDispatch: true },
    ]

    it.each(versionCases)('$label', async ({ version, shouldDispatch }) => {
      const handler = vi.fn()
      useWebSocketStore.getState().onChannelEvent('tasks', handler)

      const connectPromise = useWebSocketStore.getState().connect()
      await vi.runAllTimersAsync()
      await connectPromise
      const ws = MockWebSocket.latest()!
      ws.simulateOpen()
      ws.simulateMessage({ action: 'auth_ok' })

      const event: WsEvent = {
        event_type: 'task.created',
        channel: 'tasks',
        timestamp: new Date().toISOString(),
        payload: { task_id: 'x' },
      }
      const dispatched =
        version === 'absent' ? event : { ...event, version }
      ws.simulateMessage(dispatched)

      if (shouldDispatch) {
        expect(handler).toHaveBeenCalledTimes(1)
        expect(handler).toHaveBeenCalledWith(dispatched)
      } else {
        expect(handler).not.toHaveBeenCalled()
      }
    })
  })

  describe('subscription replay across reconnect (slice-boundary integrity)', () => {
    it('replays an active subscription on the new socket after an unintentional close', async () => {
      // Slice-boundary regression for the upcoming websocket package
      // split (transport / subscriptions slices): a subscription that
      // was active on the first socket MUST be re-sent on the second
      // socket's auth_ok when the close was server-initiated (1006).
      // If the split accidentally re-binds the subscriptions module's
      // state, this test catches it. Explicit `disconnect()` is a
      // separate, documented contract (it clears subscriptions); see
      // the disconnect-clears-subscriptions test for that pinning.
      const firstConnect = useWebSocketStore.getState().connect()
      await vi.runAllTimersAsync()
      await firstConnect
      const firstWs = MockWebSocket.latest()!
      firstWs.simulateOpen()
      firstWs.simulateMessage({ action: 'auth_ok' })
      useWebSocketStore.getState().subscribe(['tasks'])
      const firstSubFrames = firstWs.sentMessages.filter((m) => {
        const parsed = JSON.parse(m) as { action: string }
        return parsed.action === 'subscribe'
      })
      expect(firstSubFrames.length).toBeGreaterThanOrEqual(1)

      // Simulate a server-initiated close (1006) so the reconnect path
      // fires and a new MockWebSocket instance is created.
      const instancesBefore = MockWebSocket.instances.length
      firstWs.simulateClose(1006, '')
      // Drive the reconnect: advance through the backoff window and
      // flush every armed timer + the ticket-fetch microtask chain.
      await vi.advanceTimersByTimeAsync(WS_RECONNECT_BASE_DELAY * 2)
      await vi.runAllTimersAsync()
      await new Promise<void>((resolve) => setImmediate(resolve))
      await vi.runAllTimersAsync()

      const secondWs = MockWebSocket.latest()!
      expect(MockWebSocket.instances.length).toBeGreaterThan(instancesBefore)
      expect(secondWs).not.toBe(firstWs)

      secondWs.simulateOpen()
      secondWs.simulateMessage({ action: 'auth_ok' })

      const secondSubFrames = secondWs.sentMessages.filter((m) => {
        const parsed = JSON.parse(m) as { action: string }
        return parsed.action === 'subscribe'
      })
      expect(secondSubFrames.length).toBeGreaterThanOrEqual(1)
      const replayed = JSON.parse(secondSubFrames[0]!) as { channels: string[] }
      expect(replayed.channels).toContain('tasks')
    })

    it('disconnect() clears all subscriptions (separate contract from server-initiated close)', async () => {
      // Paired with the replay test above: deliberate disconnect()
      // erases subscriptions, so a subsequent connect() starts clean.
      // The split MUST preserve this contract -- if it accidentally
      // routes disconnect() through the reconnect-replay path, the
      // user would see "ghost" subscriptions resurrect on every
      // post-disconnect connect cycle.
      const firstConnect = useWebSocketStore.getState().connect()
      await vi.runAllTimersAsync()
      await firstConnect
      const firstWs = MockWebSocket.latest()!
      firstWs.simulateOpen()
      firstWs.simulateMessage({ action: 'auth_ok' })
      useWebSocketStore.getState().subscribe(['tasks'])

      useWebSocketStore.getState().disconnect()
      expect(useWebSocketStore.getState().subscribedChannels).toEqual([])

      // Fresh connect: no re-subscribe should be sent, because the
      // disconnect cleared the active-subscription bookkeeping.
      const secondConnect = useWebSocketStore.getState().connect()
      await vi.runAllTimersAsync()
      await secondConnect
      const secondWs = MockWebSocket.latest()!
      secondWs.simulateOpen()
      secondWs.simulateMessage({ action: 'auth_ok' })

      const subFrames = secondWs.sentMessages.filter((m) => {
        const parsed = JSON.parse(m) as { action: string }
        return parsed.action === 'subscribe'
      })
      expect(subFrames).toHaveLength(0)
    })
  })

  describe('retry()', () => {
    it('resets reconnectExhausted and triggers a fresh connect attempt', async () => {
      // Force exhaustion via repeated ticket failures.
      ticketState.mode = { kind: 'envelope_error', message: 'connection refused' }
      await expect(
        useWebSocketStore.getState().connect(),
      ).rejects.toThrow('connection refused')
      await drainUntilReconnectExhausted()
      expect(useWebSocketStore.getState().reconnectExhausted).toBe(true)

      const callsBefore = ticketState.calls
      ticketState.mode = {
        kind: 'success',
        ticket: 'recovery-ticket',
        expires_in: 30,
      }

      const retryPromise = useWebSocketStore.getState().retry()
      await vi.runAllTimersAsync()
      await retryPromise

      expect(useWebSocketStore.getState().reconnectExhausted).toBe(false)
      expect(ticketState.calls).toBeGreaterThan(callsBefore)
    })
  })
})
