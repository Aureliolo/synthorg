import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  activateSseFallback,
  closeSseFallback,
  isSseFallbackActive,
  recordAbnormalCloseDuringHandshake,
  resetProxyBlockSuspicion,
} from '@/stores/websocket/sse-fallback'
import { dispatchEvent } from '@/stores/websocket/subscriptions'

// The toast surface is irrelevant here and its auto-dismiss timer would leak a
// handle past the test, so stub it to a no-op.
vi.mock('@/stores/toast', () => ({
  useToastStore: { getState: () => ({ add: () => {} }) },
}))

// Capture dispatched events without touching the real subscription registry,
// so the validation path's "reject before dispatch" contract is observable.
vi.mock('@/stores/websocket/subscriptions', () => ({
  dispatchEvent: vi.fn(),
}))

type SseListener = (ev: MessageEvent) => void

// The most recently constructed fake, so a test can drive ``ws`` frames into
// the live SSE client the store opened.
let lastSource: FakeEventSource | null = null

class FakeEventSource {
  onopen: ((ev: Event) => void) | null = null
  onerror: ((ev: Event) => void) | null = null
  private readonly listeners = new Map<string, Set<SseListener>>()
  constructor(readonly url: string) {
    lastSource = this
  }
  addEventListener(type: string, handler: SseListener): void {
    const set = this.listeners.get(type) ?? new Set<SseListener>()
    set.add(handler)
    this.listeners.set(type, set)
  }
  removeEventListener(type: string, handler: SseListener): void {
    this.listeners.get(type)?.delete(handler)
  }
  close(): void {
    /* no-op */
  }
  /** Drive a ``ws`` frame to every registered listener. */
  emit(data: string, lastEventId = ''): void {
    const ev = new MessageEvent('ws', { data, lastEventId })
    for (const listener of this.listeners.get('ws') ?? []) listener(ev)
  }
}

const set = vi.fn()
let originalEventSource: typeof globalThis.EventSource | undefined

beforeEach(() => {
  originalEventSource = (globalThis as { EventSource?: typeof globalThis.EventSource })
    .EventSource
  Object.defineProperty(globalThis, 'EventSource', {
    configurable: true,
    writable: true,
    value: FakeEventSource,
  })
  resetProxyBlockSuspicion()
  set.mockClear()
  lastSource = null
  vi.mocked(dispatchEvent).mockClear()
})

afterEach(() => {
  // Always tear the singleton down so no fallback leaks into the next test.
  closeSseFallback()
  if (originalEventSource === undefined) {
    delete (globalThis as { EventSource?: typeof globalThis.EventSource }).EventSource
  } else {
    Object.defineProperty(globalThis, 'EventSource', {
      configurable: true,
      writable: true,
      value: originalEventSource,
    })
  }
})

describe('sse-fallback activation bookkeeping', () => {
  it('signals activation only after the second abnormal handshake close', () => {
    expect(recordAbnormalCloseDuringHandshake()).toBe(false)
    expect(recordAbnormalCloseDuringHandshake()).toBe(true)
  })

  it('does not re-signal activation while a fallback is already active', () => {
    activateSseFallback(set)
    expect(isSseFallbackActive()).toBe(true)
    // The threshold is met, but an active client must suppress re-activation.
    recordAbnormalCloseDuringHandshake()
    expect(recordAbnormalCloseDuringHandshake()).toBe(false)
  })

  it('resetProxyBlockSuspicion clears the counter', () => {
    recordAbnormalCloseDuringHandshake()
    resetProxyBlockSuspicion()
    // After a reset it again takes two closes to reach the threshold.
    expect(recordAbnormalCloseDuringHandshake()).toBe(false)
  })

  it('closeSseFallback tears the active fallback down', () => {
    activateSseFallback(set)
    expect(isSseFallbackActive()).toBe(true)
    closeSseFallback(set)
    expect(isSseFallbackActive()).toBe(false)
  })

  it('activateSseFallback is idempotent while a client is live', () => {
    activateSseFallback(set)
    const calls = set.mock.calls.length
    activateSseFallback(set)
    // The guard returns early, so no further state writes occur.
    expect(set.mock.calls.length).toBe(calls)
  })
})

describe('sse-fallback event validation', () => {
  // The dashboard SSE feed is untrusted wire data, so every frame is validated
  // and version-gated before it reaches the dispatch chain. These tests pin
  // that contract: only a well-formed, supported-version WsEvent dispatches.
  function validFrame(overrides: Record<string, unknown> = {}): string {
    return JSON.stringify({
      event_type: 'personality.trimmed',
      channel: 'agents',
      timestamp: '2026-04-01T12:00:00Z',
      payload: { agent_id: 'agent-001', agent_name: 'Alice' },
      ...overrides,
    })
  }

  function emit(data: string): void {
    activateSseFallback(set)
    expect(lastSource).not.toBeNull()
    lastSource?.emit(data)
  }

  it('dispatches a well-formed, supported-version frame', () => {
    emit(validFrame())
    expect(vi.mocked(dispatchEvent)).toHaveBeenCalledTimes(1)
  })

  it('discards a non-object payload before dispatch', () => {
    emit('42')
    emit('null')
    emit(JSON.stringify(['not', 'an', 'object']))
    expect(vi.mocked(dispatchEvent)).not.toHaveBeenCalled()
  })

  it('discards a schema-invalid frame before dispatch', () => {
    // Unknown event_type / channel and a missing timestamp all fail isWsEvent.
    emit(validFrame({ event_type: 'totally.unknown' }))
    emit(validFrame({ channel: 'no-such-channel' }))
    emit(JSON.stringify({ event_type: 'personality.trimmed', channel: 'agents' }))
    expect(vi.mocked(dispatchEvent)).not.toHaveBeenCalled()
  })

  it('discards an unsupported wire version before dispatch', () => {
    emit(validFrame({ version: 999 }))
    expect(vi.mocked(dispatchEvent)).not.toHaveBeenCalled()
  })

  it('dispatches a replayed duplicate (same event_id) only once', () => {
    // Reconnect replays the backlog; the client must dedupe by event_id so a
    // re-delivered event is dispatched exactly once.
    emit(validFrame({ event_id: 'evt-1' }))
    emit(validFrame({ event_id: 'evt-1' }))
    expect(vi.mocked(dispatchEvent)).toHaveBeenCalledTimes(1)
  })

  it('dispatches distinct event_ids each time', () => {
    emit(validFrame({ event_id: 'evt-1' }))
    emit(validFrame({ event_id: 'evt-2' }))
    expect(vi.mocked(dispatchEvent)).toHaveBeenCalledTimes(2)
  })
})
