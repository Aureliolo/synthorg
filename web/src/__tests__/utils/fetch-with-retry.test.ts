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
    const fetchImpl = vi.fn(async () => makeResponse(200))
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
    const fetchImpl = vi.fn(async () => makeResponse(429, '1'))
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
    const fetchImpl = vi.fn(async () => makeResponse(429, '99'))
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
    const fetchImpl = vi.fn(async () => makeResponse(429, '0'))
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
    const fetchImpl = vi.fn(async () => makeResponse(500))
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
})
