import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { openSseFallback } from '@/api/sse/client'
import {
  SSE_MAX_RECONNECT_ATTEMPTS,
  SSE_RECONNECT_BASE_DELAY,
  SSE_RECONNECT_MAX_DELAY,
} from '@/utils/ws-constants'
import type { WsEvent } from '@/api/types/websocket'

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

  /** Test helper: dispatch a named SSE frame to its registered listeners. */
  emit(type: string, ev: MessageEvent): void {
    for (const handler of this.listeners.get(type) ?? []) {
      handler(ev)
    }
  }

  /** Test helper: number of listeners registered across all event types. */
  listenerCount(): number {
    let total = 0
    for (const set of this.listeners.values()) total += set.size
    return total
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

describe('openSseFallback', () => {
  it('forwards mapped AG-UI events as internal WsEvent frames', () => {
    const events: WsEvent[] = []
    openSseFallback({
      onEvent: (e) => events.push(e),
      onError: () => {},
    })
    expect(lastEventSource).not.toBeNull()
    // The server names the frame with its AG-UI type (the SSE ``event:``
    // field), so the client receives it via the named listener.
    lastEventSource!.emit(
      'run_started',
      new MessageEvent('run_started', {
        data: JSON.stringify({
          id: 'evt-1',
          type: 'run_started',
          timestamp: '2026-05-13T12:00:00Z',
          payload: { task_id: 't-1' },
        }),
      }),
    )
    expect(events).toHaveLength(1)
    expect(events[0]!.event_type).toBe('task.status_changed')
    expect(events[0]!.channel).toBe('tasks')
    expect(events[0]!.timestamp).toBe('2026-05-13T12:00:00Z')
    expect(events[0]!.payload).toEqual({ task_id: 't-1' })
  })

  it('does not subscribe to AG-UI types that have no internal mapping', () => {
    const events: WsEvent[] = []
    openSseFallback({
      onEvent: (e) => events.push(e),
      onError: () => {},
    })
    // No listener is registered for an unmapped type, so even if the
    // server emitted it the read-only fallback would never receive it.
    lastEventSource!.emit(
      'tool_call_args',
      new MessageEvent('tool_call_args', {
        data: JSON.stringify({
          id: 'evt-2',
          type: 'tool_call_args',
          timestamp: '2026-05-13T12:00:00Z',
          payload: {},
        }),
      }),
    )
    expect(events).toHaveLength(0)
  })

  it('discards malformed frames without throwing', () => {
    const events: WsEvent[] = []
    openSseFallback({
      onEvent: (e) => events.push(e),
      onError: () => {},
    })
    lastEventSource!.emit(
      'run_started',
      new MessageEvent('run_started', { data: '{not-json' }),
    )
    expect(events).toHaveLength(0)
  })

  it('reports transport errors via onError', () => {
    const errors: Error[] = []
    const handle = openSseFallback({
      onEvent: () => {},
      onError: (err) => errors.push(err),
    })
    lastEventSource!.onerror?.(new Event('error'))
    expect(errors).toHaveLength(1)
    expect(errors[0]!.message).toMatch(/SSE/)
    // The error scheduled an app-level reconnect timer; close cancels it so
    // the active-handle gate stays clean.
    handle.close()
  })

  it('reconnects with an application-level backoff timer after an error', () => {
    vi.useFakeTimers()
    try {
      const handle = openSseFallback({ onEvent: () => {}, onError: () => {} })
      const first = lastEventSource
      // An error closes the current source and schedules a re-open; no new
      // EventSource is created synchronously (that would be the native flat
      // retry we are deliberately replacing).
      first!.onerror?.(new Event('error'))
      expect(lastEventSource).toBe(first)
      vi.advanceTimersByTime(SSE_RECONNECT_BASE_DELAY)
      expect(lastEventSource).not.toBe(first)
      handle.close()
    } finally {
      vi.useRealTimers()
    }
  })

  it('gives up and reports exhaustion after the reconnect budget', () => {
    vi.useFakeTimers()
    try {
      let exhausted = false
      const handle = openSseFallback({
        onEvent: () => {},
        onError: () => {},
        onExhausted: () => {
          exhausted = true
        },
      })
      for (let i = 0; i < SSE_MAX_RECONNECT_ATTEMPTS; i++) {
        lastEventSource!.onerror?.(new Event('error'))
        vi.advanceTimersByTime(SSE_RECONNECT_MAX_DELAY)
      }
      // The attempt that crosses the budget closes the transport.
      lastEventSource!.onerror?.(new Event('error'))
      expect(exhausted).toBe(true)
      handle.close()
    } finally {
      vi.useRealTimers()
    }
  })

  it('close() tears down the EventSource', () => {
    const closeSpy = vi.spyOn(FakeEventSource.prototype, 'close')
    const handle = openSseFallback({
      onEvent: () => {},
      onError: () => {},
    })
    handle.close()
    expect(closeSpy).toHaveBeenCalled()
  })

  it('invokes onOpen when the transport connects', () => {
    let opened = false
    openSseFallback({
      onEvent: () => {},
      onError: () => {},
      onOpen: () => {
        opened = true
      },
    })
    lastEventSource!.onopen?.(new Event('open'))
    expect(opened).toBe(true)
  })

  it('releases handlers + listeners on close()', () => {
    const handle = openSseFallback({
      onEvent: () => {},
      onError: () => {},
      onOpen: () => {},
    })
    // Sanity check: handlers + named listeners were wired up.
    expect(lastEventSource!.onopen).not.toBeNull()
    expect(lastEventSource!.onerror).not.toBeNull()
    expect(lastEventSource!.listenerCount()).toBeGreaterThan(0)
    handle.close()
    expect(lastEventSource!.onopen).toBeNull()
    expect(lastEventSource!.onerror).toBeNull()
    expect(lastEventSource!.listenerCount()).toBe(0)
  })
})
