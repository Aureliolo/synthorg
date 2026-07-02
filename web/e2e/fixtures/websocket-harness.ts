/**
 * WebSocket event injection harness for Playwright E2E tests.
 *
 * The dashboard's WebSocket flow opens a long-lived connection to
 * ``/api/v1/ws`` and processes server-pushed events to update store
 * state in real time (task transitions, approval decisions, provider
 * health, etc.). E2E tests need to drive these events without
 * spinning up the full backend.
 *
 * ``installWebSocketHarness`` swaps the real ``WebSocket`` constructor
 * for a controllable stub at page load. Tests then call
 * ``injectEvent(page, event)`` to push synthetic frames; the dashboard
 * processes them as if they came from the server.
 *
 * The harness only activates inside the Playwright runtime; it relies
 * on ``page.addInitScript`` so the stub is in place before the SPA
 * runs and never reaches production code.
 */

import type { Page } from '@playwright/test'

/**
 * Install the harness; call once per test before ``page.goto``.
 *
 * Each ``new WebSocket(url)`` returns a stub whose ``send`` is a
 * no-op and whose ``readyState`` flips to ``OPEN`` synchronously.
 * The stub's ``onmessage`` handler is exposed on
 * ``window.__synthorgWsLatest`` so the harness's ``injectEvent`` call
 * can drive frames into it.
 */
export async function installWebSocketHarness(page: Page): Promise<void> {
  await page.addInitScript(() => {
    interface StubWebSocket extends WebSocket {
      __synthorgInjected: true
    }
    interface SynthorgWindow {
      __synthorgWsLatest: StubWebSocket | null
      __synthorgWsSubscribedChannels: string[]
      __synthorgWsInjectedFrames: { channel: string; data: string }[]
    }
    const win = window as unknown as SynthorgWindow

    win.__synthorgWsLatest = null
    win.__synthorgWsSubscribedChannels = []
    win.__synthorgWsInjectedFrames = []

    class HarnessWebSocket extends EventTarget {
      readonly url: string
      readonly protocol: string = ''
      readonly extensions: string = ''
      readonly bufferedAmount: number = 0
      readonly binaryType: BinaryType = 'blob'
      readyState: number = 0
      static CONNECTING = 0
      static OPEN = 1
      static CLOSING = 2
      static CLOSED = 3
      onopen: ((this: WebSocket, ev: Event) => unknown) | null = null
      onclose: ((this: WebSocket, ev: CloseEvent) => unknown) | null = null
      onerror: ((this: WebSocket, ev: Event) => unknown) | null = null
      onmessage: ((this: WebSocket, ev: MessageEvent) => unknown) | null = null
      __synthorgInjected: true = true as const

      constructor(url: string | URL) {
        super()
        this.url = typeof url === 'string' ? url : url.toString()
        win.__synthorgWsLatest = this as unknown as StubWebSocket
        // Open synchronously on next microtask so the SPA's
        // ``onopen`` handler runs after the constructor returns.
        queueMicrotask(() => {
          this.readyState = HarnessWebSocket.OPEN
          const evt = new Event('open')
          this.onopen?.call(this as unknown as WebSocket, evt)
          this.dispatchEvent(evt)
        })
      }

      send(data: string | ArrayBufferLike | Blob | ArrayBufferView): void {
        // The dashboard does not need to round-trip pings in tests
        // (harness-injected events arrive via injectEvent), but the
        // subscribe frames the SPA sends are recorded: handler
        // registration happens in the same synchronous block as the
        // ``subscribe`` send, so an observed subscribe frame is the
        // readiness signal ``injectEvent`` waits on before pushing.
        if (typeof data !== 'string') return
        try {
          const frame = JSON.parse(data) as {
            action?: unknown
            channels?: unknown
          }
          if (frame.action === 'subscribe' && Array.isArray(frame.channels)) {
            const channels = frame.channels.filter(
              (c): c is string => typeof c === 'string',
            )
            for (const channel of channels) {
              if (!win.__synthorgWsSubscribedChannels.includes(channel)) {
                win.__synthorgWsSubscribedChannels.push(channel)
              }
            }
            // Sticky redelivery: the SPA registers channel handlers in
            // the same synchronous block that sends this subscribe
            // frame (subscribe first, ``onChannelEvent`` right after),
            // so a frame injected before THIS subscriber attached was
            // silently dropped by the dispatcher. Replay buffered
            // frames for the newly-subscribed channels on a microtask
            // (after the registration statements have run). Handlers
            // attached earlier see the frame twice; specs assert with
            // ``.first()`` / idempotent state, so duplicates are safe,
            // while dropped frames are not.
            const replayable = win.__synthorgWsInjectedFrames.filter((f) =>
              channels.includes(f.channel),
            )
            if (replayable.length > 0) {
              queueMicrotask(() => {
                const ws = win.__synthorgWsLatest
                if (ws == null) return
                for (const f of replayable) {
                  const evt = new MessageEvent('message', { data: f.data })
                  ws.onmessage?.call(ws as unknown as WebSocket, evt)
                  ws.dispatchEvent(evt)
                }
              })
            }
          }
        } catch {
          // Non-JSON frames (none today) carry no subscription state.
        }
      }

      close(): void {
        this.readyState = HarnessWebSocket.CLOSED
        const evt = new CloseEvent('close', { code: 1000, reason: '' })
        this.onclose?.call(this as unknown as WebSocket, evt)
        this.dispatchEvent(evt)
      }
    }

    // Replace the global constructor.
    ;(window as unknown as { WebSocket: typeof WebSocket }).WebSocket =
      HarnessWebSocket as unknown as typeof WebSocket
  })
}

/**
 * Push a synthetic event frame into the latest WebSocket stub.
 *
 * The dashboard's WS layer JSON.parse's the data field, so events
 * are passed in as plain objects and serialised here.
 *
 * Args:
 *     page: Playwright page running under the harness.
 *     event: Event object matching the dashboard's expected wire shape.
 */
export async function injectEvent(
  page: Page,
  event: Record<string, unknown>,
): Promise<void> {
  // The SPA opens its WebSocket asynchronously (it first fetches an
  // auth ws-ticket) and registers its channel handlers later still
  // (a React effect subscribes after connect resolves), so a goto()
  // guarantees neither the stub nor the dispatch chain. An event
  // injected before ``onChannelEvent`` ran is silently dropped by the
  // dispatcher. The subscribe frame is sent in the same synchronous
  // block that registers the handlers, so wait until the SPA has
  // subscribed to this event's channel before pushing.
  const channel = typeof event['channel'] === 'string' ? event['channel'] : null
  await page.waitForFunction((wanted) => {
    const win = window as unknown as {
      __synthorgWsLatest?: WebSocket | null
      __synthorgWsSubscribedChannels?: string[]
    }
    if (win.__synthorgWsLatest == null) return false
    if (wanted === null) return true
    return win.__synthorgWsSubscribedChannels?.includes(wanted) ?? false
  }, channel)
  await page.evaluate((payload) => {
    interface SynthorgWindow {
      __synthorgWsLatest?: WebSocket | null
      __synthorgWsInjectedFrames?: { channel: string; data: string }[]
    }
    const win = window as unknown as SynthorgWindow
    const ws = win.__synthorgWsLatest
    if (ws == null) {
      throw new Error('No WebSocket stub registered; call installWebSocketHarness first')
    }
    const data = JSON.stringify(payload)
    // Buffer for sticky redelivery to handlers that register after this
    // injection (see the subscribe-frame replay in the harness stub).
    if (typeof payload['channel'] === 'string') {
      win.__synthorgWsInjectedFrames?.push({ channel: payload['channel'], data })
    }
    const evt = new MessageEvent('message', { data })
    ws.onmessage?.call(ws, evt)
    ws.dispatchEvent(evt)
  }, event)
}
