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
    }
    const win = window as unknown as SynthorgWindow

    win.__synthorgWsLatest = null

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

      send(_data: string | ArrayBufferLike | Blob | ArrayBufferView): void {
        // No-op: the dashboard does not need to round-trip pings in
        // tests, and harness-injected events arrive via injectEvent.
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
  // auth ws-ticket), so a goto() does not guarantee the stub exists by
  // the time a test injects. Wait for the constructor to register the
  // stub before pushing, so callers never race the connection.
  await page.waitForFunction(() => {
    const ws = (
      window as unknown as { __synthorgWsLatest?: WebSocket | null }
    ).__synthorgWsLatest
    return ws !== undefined && ws !== null
  })
  await page.evaluate((payload) => {
    interface SynthorgWindow {
      __synthorgWsLatest?: WebSocket | null
    }
    const win = window as unknown as SynthorgWindow
    const ws = win.__synthorgWsLatest
    if (ws == null) {
      throw new Error('No WebSocket stub registered; call installWebSocketHarness first')
    }
    const data = JSON.stringify(payload)
    const evt = new MessageEvent('message', { data })
    ws.onmessage?.call(ws, evt)
    ws.dispatchEvent(evt)
  }, event)
}
