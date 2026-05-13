import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { openSseFallback } from '@/api/sse/client'
import type { WsEvent } from '@/api/types/websocket'

interface MockEventSource {
  url: string
  onopen: ((ev: Event) => void) | null
  onmessage: ((ev: MessageEvent) => void) | null
  onerror: ((ev: Event) => void) | null
  close: () => void
}

let lastEventSource: MockEventSource | null = null

class FakeEventSource implements MockEventSource {
  readonly url: string
  onopen: ((ev: Event) => void) | null = null
  onmessage: ((ev: MessageEvent) => void) | null = null
  onerror: ((ev: Event) => void) | null = null

  constructor(url: string, _init?: EventSourceInit) {
    this.url = url
    lastEventSource = this
  }

  close(): void {
    /* no-op */
  }
}

beforeEach(() => {
  lastEventSource = null
  Object.defineProperty(globalThis, 'EventSource', {
    configurable: true,
    writable: true,
    value: FakeEventSource,
  })
})

afterEach(() => {
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
    lastEventSource!.onmessage?.(
      new MessageEvent('message', {
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

  it('drops AG-UI events that have no internal mapping', () => {
    const events: WsEvent[] = []
    openSseFallback({
      onEvent: (e) => events.push(e),
      onError: () => {},
    })
    lastEventSource!.onmessage?.(
      new MessageEvent('message', {
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
    lastEventSource!.onmessage?.(
      new MessageEvent('message', { data: '{not-json' }),
    )
    expect(events).toHaveLength(0)
  })

  it('reports transport errors via onError', () => {
    const errors: Error[] = []
    openSseFallback({
      onEvent: () => {},
      onError: (err) => errors.push(err),
    })
    lastEventSource!.onerror?.(new Event('error'))
    expect(errors).toHaveLength(1)
    expect(errors[0]!.message).toMatch(/SSE/)
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
})
