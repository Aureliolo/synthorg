import { describe, expect, it, vi } from 'vitest'
import { fetchWithRetryAfter } from '@/utils/fetch-with-retry'

function makeResponse(
  status: number,
  retryAfter?: string | null,
): Response {
  const headers = new Headers()
  if (retryAfter !== undefined && retryAfter !== null) {
    headers.set('Retry-After', retryAfter)
  }
  return new Response('{}', { status, headers })
}

describe('fetchWithRetryAfter', () => {
  it('returns the response on first 200 without retrying', async () => {
    const fetchImpl = vi.fn(() => Promise.resolve(makeResponse(200)))
    const sleep = vi.fn(async () => {})
    const resp = await fetchWithRetryAfter('/x', undefined, {
      fetchImpl,
      sleep,
    })
    expect(resp.status).toBe(200)
    expect(fetchImpl).toHaveBeenCalledTimes(1)
    expect(sleep).not.toHaveBeenCalled()
  })

  it('retries idempotent GET on 429 with Retry-After=1', async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(makeResponse(429, '1'))
      .mockResolvedValueOnce(makeResponse(200))
    const sleep = vi.fn(async () => {})
    const resp = await fetchWithRetryAfter(
      '/x',
      { method: 'GET' },
      { fetchImpl, sleep },
    )
    expect(resp.status).toBe(200)
    expect(fetchImpl).toHaveBeenCalledTimes(2)
    expect(sleep).toHaveBeenCalledExactlyOnceWith(1000)
  })

  it('does NOT retry POST without idempotent opt-in or Idempotency-Key', async () => {
    const fetchImpl = vi.fn(() => Promise.resolve(makeResponse(429, '1')))
    const sleep = vi.fn(async () => {})
    const resp = await fetchWithRetryAfter(
      '/x',
      { method: 'POST' },
      { fetchImpl, sleep },
    )
    expect(resp.status).toBe(429)
    expect(fetchImpl).toHaveBeenCalledTimes(1)
    expect(sleep).not.toHaveBeenCalled()
  })

  it('retries POST when caller opts into idempotent', async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(makeResponse(429, '1'))
      .mockResolvedValueOnce(makeResponse(200))
    const sleep = vi.fn(async () => {})
    await fetchWithRetryAfter(
      '/x',
      { method: 'POST' },
      { fetchImpl, sleep, idempotent: true },
    )
    expect(fetchImpl).toHaveBeenCalledTimes(2)
  })

  it('retries POST when an Idempotency-Key header is present', async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(makeResponse(429, '1'))
      .mockResolvedValueOnce(makeResponse(200))
    const sleep = vi.fn(async () => {})
    await fetchWithRetryAfter(
      '/x',
      {
        method: 'POST',
        headers: { 'Idempotency-Key': 'op-1' },
      },
      { fetchImpl, sleep },
    )
    expect(fetchImpl).toHaveBeenCalledTimes(2)
  })

  it('surfaces the 429 when Retry-After exceeds the budget', async () => {
    // 99 seconds is well above the 5_000ms ceiling.
    const fetchImpl = vi.fn(() => Promise.resolve(makeResponse(429, '99')))
    const sleep = vi.fn(async () => {})
    const resp = await fetchWithRetryAfter(
      '/x',
      { method: 'GET' },
      { fetchImpl, sleep },
    )
    expect(resp.status).toBe(429)
    expect(fetchImpl).toHaveBeenCalledTimes(1)
    expect(sleep).not.toHaveBeenCalled()
  })

  it('exhausts retry budget after MAX_RATE_LIMIT_RETRIES attempts', async () => {
    const fetchImpl = vi.fn(() => Promise.resolve(makeResponse(429, '0')))
    const sleep = vi.fn(async () => {})
    const resp = await fetchWithRetryAfter(
      '/x',
      { method: 'GET' },
      { fetchImpl, sleep },
    )
    expect(resp.status).toBe(429)
    // 1 initial + 2 retries (MAX_RATE_LIMIT_RETRIES) = 3 total fetches.
    expect(fetchImpl).toHaveBeenCalledTimes(3)
    expect(sleep).toHaveBeenCalledTimes(2)
  })

  it('treats malformed Retry-After as an immediate retry (0ms wait)', async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(makeResponse(429, 'not-a-date'))
      .mockResolvedValueOnce(makeResponse(200))
    const sleep = vi.fn(async () => {})
    const resp = await fetchWithRetryAfter(
      '/x',
      { method: 'GET' },
      { fetchImpl, sleep },
    )
    expect(resp.status).toBe(200)
    expect(sleep).toHaveBeenCalledExactlyOnceWith(0)
  })

  it('does not retry on non-429 error responses', async () => {
    const fetchImpl = vi.fn(() => Promise.resolve(makeResponse(500)))
    const sleep = vi.fn(async () => {})
    const resp = await fetchWithRetryAfter(
      '/x',
      { method: 'GET' },
      { fetchImpl, sleep },
    )
    expect(resp.status).toBe(500)
    expect(fetchImpl).toHaveBeenCalledTimes(1)
    expect(sleep).not.toHaveBeenCalled()
  })

  it.each([
    { method: 'DELETE', idempotent: true, expectStatus: 200, expectFetches: 2 },
    { method: 'PUT', idempotent: true, expectStatus: 200, expectFetches: 2 },
    { method: 'PATCH', idempotent: undefined, expectStatus: 429, expectFetches: 1 },
  ])(
    '$method on 429 (idempotent=$idempotent) honours retry policy',
    async ({ method, idempotent, expectStatus, expectFetches }) => {
      const fetchImpl =
        expectFetches === 2
          ? vi
              .fn()
              .mockResolvedValueOnce(makeResponse(429, '0'))
              .mockResolvedValueOnce(makeResponse(200))
          : vi.fn(() => Promise.resolve(makeResponse(429, '0')))
      const sleep = vi.fn(async () => {})
      const opts = idempotent === undefined ? { fetchImpl, sleep } : { fetchImpl, sleep, idempotent }
      const resp = await fetchWithRetryAfter('/x', { method }, opts)
      expect(resp.status).toBe(expectStatus)
      expect(fetchImpl).toHaveBeenCalledTimes(expectFetches)
    },
  )

  it('short-circuits when AbortSignal aborts during retry sleep', async () => {
    const controller = new AbortController()
    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(makeResponse(429, '1'))
      .mockResolvedValueOnce(makeResponse(200))
    // Abort while we're "sleeping" for the Retry-After window so the
    // helper observes the cancellation before issuing the next fetch.
    const sleep = vi.fn(() => {
      controller.abort()
      return Promise.resolve()
    })
    const resp = await fetchWithRetryAfter(
      '/x',
      { method: 'GET', signal: controller.signal },
      { fetchImpl, sleep },
    )
    expect(resp.status).toBe(429)
    // Initial fetch + sleep ran; the post-sleep abort check skipped
    // the second fetch entirely.
    expect(fetchImpl).toHaveBeenCalledTimes(1)
    expect(sleep).toHaveBeenCalledTimes(1)
  })

  it('short-circuits when AbortSignal is already aborted before sleep', async () => {
    const controller = new AbortController()
    controller.abort()
    const fetchImpl = vi.fn(() => Promise.resolve(makeResponse(429, '1')))
    const sleep = vi.fn(async () => {})
    const resp = await fetchWithRetryAfter(
      '/x',
      { method: 'GET', signal: controller.signal },
      { fetchImpl, sleep },
    )
    expect(resp.status).toBe(429)
    // First fetch always runs (caller's responsibility to check signal);
    // the pre-sleep abort check then skips the retry path.
    expect(fetchImpl).toHaveBeenCalledTimes(1)
    expect(sleep).not.toHaveBeenCalled()
  })

  it('reads idempotency-key header from Request input when init is omitted', async () => {
    // POST is not auto-retriable, so without the Idempotency-Key the
    // helper would surface the 429 directly. The Request carries the
    // header on its own ``headers`` collection (init is undefined),
    // proving the helper reads request metadata from the Request
    // input rather than only from ``init``.
    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(makeResponse(429, '0'))
      .mockResolvedValueOnce(makeResponse(200))
    const sleep = vi.fn(async () => {})
    const request = new Request('http://example.test/x', {
      method: 'POST',
      headers: { 'Idempotency-Key': 'op-1' },
    })
    const resp = await fetchWithRetryAfter(request, undefined, {
      fetchImpl,
      sleep,
    })
    expect(resp.status).toBe(200)
    expect(fetchImpl).toHaveBeenCalledTimes(2)
  })

  it('skips retry on Request POST input without idempotent opt-in', async () => {
    // A Request whose method is POST must NOT retry by default.
    // Earlier the method was read off ``init`` only, so a Request POST
    // with no ``init`` was misclassified as GET and retried unsafely.
    const fetchImpl = vi.fn(() => Promise.resolve(makeResponse(429, '0')))
    const sleep = vi.fn(async () => {})
    const request = new Request('http://example.test/x', { method: 'POST' })
    const resp = await fetchWithRetryAfter(request, undefined, {
      fetchImpl,
      sleep,
    })
    expect(resp.status).toBe(429)
    expect(fetchImpl).toHaveBeenCalledTimes(1)
    expect(sleep).not.toHaveBeenCalled()
  })

  it('default sleep cancels immediately when AbortSignal fires mid-wait', async () => {
    // Exercise the built-in defaultSleep path (no ``sleep`` option) and
    // verify that aborting during the timer interval short-circuits
    // the retry without waiting out the full Retry-After budget.
    const controller = new AbortController()
    const fetchImpl = vi.fn(() => Promise.resolve(makeResponse(429, '1')))
    const start = performance.now()
    const promise = fetchWithRetryAfter(
      'http://example.test/x',
      { method: 'GET', signal: controller.signal },
      { fetchImpl },
    )
    // Abort well before the 1000ms Retry-After window expires.
    setTimeout(() => controller.abort(), 20)
    const resp = await promise
    const elapsed = performance.now() - start
    expect(resp.status).toBe(429)
    expect(fetchImpl).toHaveBeenCalledTimes(1)
    // Round-up tolerance for scheduler jitter; we just need to prove
    // the helper did NOT wait the full second.
    expect(elapsed).toBeLessThan(500)
  })
})
