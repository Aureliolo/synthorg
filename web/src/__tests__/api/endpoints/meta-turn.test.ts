import { afterEach, describe, expect, it, vi } from 'vitest'

import { streamTurn, type StreamTurnHandlers } from '@/api/endpoints/meta-turn'
import { ErrorCategory, ErrorCode } from '@/api/types/errors'

const NOOP_HANDLERS: StreamTurnHandlers = {
  onDelta: () => undefined,
  onComplete: () => undefined,
  onChime: () => undefined,
}

// A native ReadableStream that emits one SSE frame then closes, so the
// reader's `cancel()` (run when a frame throws mid-stream) resolves cleanly.
// MSW's string-body mock stream hangs that cancel, so these tests drive the
// real fetch path directly rather than through the MSW server.
function sseErrorResponse(payload: unknown): Response {
  const body = `event: error\ndata: ${JSON.stringify(payload)}\n\n`
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(body))
      controller.close()
    },
  })
  return new Response(stream, {
    status: 200,
    headers: { 'Content-Type': 'text/event-stream' },
  })
}

function stubFetch(payload: unknown): void {
  vi.stubGlobal(
    'fetch',
    vi.fn(() => Promise.resolve(sseErrorResponse(payload))),
  )
}

describe('streamTurn error frames', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('reconstructs an ApiRequestError carrying the domain error detail', async () => {
    stubFetch({
      error: 'Chat is disabled for this deployment.',
      error_detail: {
        detail: 'Chat is disabled for this deployment.',
        error_code: ErrorCode.SERVICE_UNAVAILABLE,
        error_category: ErrorCategory.INTERNAL,
        retryable: false,
        retry_after: null,
        instance: 'req-1',
        title: 'Service Unavailable',
        type: 'about:blank',
      },
    })
    await expect(
      streamTurn('explain the roadmap', NOOP_HANDLERS),
    ).rejects.toMatchObject({
      name: 'ApiRequestError',
      message: 'Chat is disabled for this deployment.',
      errorDetail: { error_code: ErrorCode.SERVICE_UNAVAILABLE, retryable: false },
    })
  })

  it('falls back to a null detail for an unstructured error frame', async () => {
    stubFetch({ error: 'Internal error: RuntimeError' })
    await expect(
      streamTurn('explain the roadmap', NOOP_HANDLERS),
    ).rejects.toMatchObject({
      name: 'ApiRequestError',
      message: 'Internal error: RuntimeError',
      errorDetail: null,
    })
  })
})
