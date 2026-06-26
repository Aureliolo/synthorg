import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  activateSseFallback,
  closeSseFallback,
  isSseFallbackActive,
  recordAbnormalCloseDuringHandshake,
  resetProxyBlockSuspicion,
} from '@/stores/websocket/sse-fallback'

// The toast surface is irrelevant here and its auto-dismiss timer would leak a
// handle past the test, so stub it to a no-op.
vi.mock('@/stores/toast', () => ({
  useToastStore: { getState: () => ({ add: () => {} }) },
}))

type SseListener = (ev: MessageEvent) => void

class FakeEventSource {
  onopen: ((ev: Event) => void) | null = null
  onerror: ((ev: Event) => void) | null = null
  private readonly listeners = new Map<string, Set<SseListener>>()
  constructor(readonly url: string) {}
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
