import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { openTaskProgressStream } from '@/api/sse/task-progress-client'
import {
  SSE_MAX_RECONNECT_ATTEMPTS,
  SSE_RECONNECT_MAX_DELAY,
} from '@/utils/ws-constants'

type SseListener = (ev: MessageEvent) => void

let lastEventSource: FakeEventSource | null = null

class FakeEventSource {
  readonly url: string
  onopen: ((ev: Event) => void) | null = null
  onerror: ((ev: Event) => void) | null = null
  private readonly listeners = new Map<string, Set<SseListener>>()

  constructor(url: string) {
    this.url = url
    // eslint-disable-next-line @typescript-eslint/no-this-alias -- test spy needs the latest instance
    lastEventSource = this
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
}

let originalEventSource: typeof globalThis.EventSource | undefined

beforeEach(() => {
  lastEventSource = null
  originalEventSource = (globalThis as { EventSource?: typeof globalThis.EventSource })
    .EventSource
  Object.defineProperty(globalThis, 'EventSource', {
    configurable: true,
    writable: true,
    value: FakeEventSource,
  })
})

afterEach(() => {
  if (originalEventSource === undefined) {
    delete (globalThis as { EventSource?: typeof globalThis.EventSource }).EventSource
  } else {
    Object.defineProperty(globalThis, 'EventSource', {
      configurable: true,
      writable: true,
      value: originalEventSource,
    })
  }
  vi.restoreAllMocks()
})

describe('openTaskProgressStream reconnect budget', () => {
  it('does not refill the budget on a short-lived open (flap exhausts)', () => {
    vi.useFakeTimers()
    try {
      let exhausted = false
      const handle = openTaskProgressStream('task-1', {
        onEvent: () => {},
        onExhausted: () => {
          exhausted = true
        },
      })
      // Each cycle opens then errors with no time in between, so the open is
      // never stable and the budget is never reset: it must still exhaust.
      for (let i = 0; i < SSE_MAX_RECONNECT_ATTEMPTS; i++) {
        lastEventSource!.onopen?.(new Event('open'))
        lastEventSource!.onerror?.(new Event('error'))
        vi.advanceTimersByTime(SSE_RECONNECT_MAX_DELAY)
      }
      lastEventSource!.onopen?.(new Event('open'))
      lastEventSource!.onerror?.(new Event('error'))
      expect(exhausted).toBe(true)
      handle.close()
    } finally {
      vi.useRealTimers()
    }
  })

  it('resets the budget after a connection stays open long enough', () => {
    vi.useFakeTimers()
    try {
      let exhausted = false
      const handle = openTaskProgressStream('task-1', {
        onEvent: () => {},
        onExhausted: () => {
          exhausted = true
        },
      })
      // Burn all but one attempt with short-lived flaps.
      for (let i = 0; i < SSE_MAX_RECONNECT_ATTEMPTS - 1; i++) {
        lastEventSource!.onopen?.(new Event('open'))
        lastEventSource!.onerror?.(new Event('error'))
        vi.advanceTimersByTime(SSE_RECONNECT_MAX_DELAY)
      }
      // A stable open (held past SSE_RECONNECT_MAX_DELAY) resets the budget,
      // so the next error does NOT exhaust it.
      lastEventSource!.onopen?.(new Event('open'))
      vi.advanceTimersByTime(SSE_RECONNECT_MAX_DELAY)
      lastEventSource!.onerror?.(new Event('error'))
      expect(exhausted).toBe(false)
      handle.close()
    } finally {
      vi.useRealTimers()
    }
  })

  it('close() closes the live source and cancels a pending reconnect', () => {
    vi.useFakeTimers()
    try {
      const closeSpy = vi.spyOn(FakeEventSource.prototype, 'close')
      const onEvent = vi.fn()
      const handle = openTaskProgressStream('task-1', { onEvent })
      const firstSource = lastEventSource
      expect(firstSource).not.toBeNull()
      // An error schedules a reconnect timer. Closing before it fires must
      // cancel the timer AND close the live source, so advancing past the max
      // delay never spins up a replacement source (a leaked timer would).
      firstSource!.onerror?.(new Event('error'))
      handle.close()
      expect(closeSpy).toHaveBeenCalled()
      vi.advanceTimersByTime(SSE_RECONNECT_MAX_DELAY * 2)
      expect(lastEventSource).toBe(firstSource)
      expect(onEvent).not.toHaveBeenCalled()
    } finally {
      vi.useRealTimers()
    }
  })
})
