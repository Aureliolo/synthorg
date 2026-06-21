/**
 * Parity tests for the shared 429 retry policy.
 *
 * Both the axios interceptor (``@/api/client``) and the raw-``fetch`` helper
 * (``@/utils/fetch-with-retry``) delegate to {@link retryAfterLoop}, so this
 * suite pins the policy once: budget ceiling, ``DO_NOT_RETRY`` bail, abort
 * gating, the always-sleep contract, and idempotency gating. A regression
 * here changes behaviour for every HTTP layer at once, which is the whole
 * point of having a single loop.
 */
import { describe, expect, it, vi } from 'vitest'
import {
  DO_NOT_RETRY,
  MAX_RATE_LIMIT_RETRIES,
  MIN_RETRY_BACKOFF_MS,
  isIdempotentMethod,
  retryAfterLoop,
  type RetryableResponse,
} from '@/utils/retry-after'

interface FakeResponse extends RetryableResponse {
  readonly status: number
}

function res(status: number): FakeResponse {
  return { status }
}

/**
 * Drive ``retryAfterLoop`` over a scripted sequence of responses. The first
 * element seeds ``first``; the rest are returned by successive ``send`` calls.
 */
function runLoop(
  statuses: number[],
  opts: {
    waitMs?: number
    retriable?: boolean
    isAborted?: () => boolean
    sleep?: (ms: number) => Promise<void>
  } = {},
): Promise<{
  result: FakeResponse
  sends: number
  retries: number
}> {
  const [first, ...rest] = statuses
  let cursor = 0
  let sends = 0
  let retries = 0
  return retryAfterLoop<FakeResponse>({
    first: res(first ?? 200),
    send: () => {
      sends += 1
      const status = rest[cursor] ?? rest[rest.length - 1] ?? 200
      cursor += 1
      return Promise.resolve(res(status))
    },
    getRetryAfterMs: () => opts.waitMs ?? 0,
    retriable: opts.retriable ?? true,
    sleep: opts.sleep ?? (async () => {}),
    isAborted: opts.isAborted,
    onBeforeRetry: () => {
      retries += 1
    },
  }).then((result) => ({ result, sends, retries }))
}

describe('retryAfterLoop', () => {
  it('returns the first response unchanged when it is not a 429', async () => {
    const { result, sends } = await runLoop([200])
    expect(result.status).toBe(200)
    expect(sends).toBe(0)
  })

  it('stops at the first non-429 response', async () => {
    const { result, sends, retries } = await runLoop([429, 200])
    expect(result.status).toBe(200)
    expect(sends).toBe(1)
    expect(retries).toBe(1)
  })

  it('exhausts after MAX_RATE_LIMIT_RETRIES retries and surfaces the 429', async () => {
    const { result, sends, retries } = await runLoop([429, 429, 429, 429])
    expect(result.status).toBe(429)
    expect(sends).toBe(MAX_RATE_LIMIT_RETRIES)
    expect(retries).toBe(MAX_RATE_LIMIT_RETRIES)
  })

  it('does not retry when the request is not replayable', async () => {
    const { result, sends } = await runLoop([429, 200], { retriable: false })
    expect(result.status).toBe(429)
    expect(sends).toBe(0)
  })

  it('bails immediately on the DO_NOT_RETRY sentinel without sleeping', async () => {
    const sleep = vi.fn(async () => {})
    const { result, sends } = await runLoop([429, 200], {
      waitMs: DO_NOT_RETRY,
      sleep,
    })
    expect(result.status).toBe(429)
    expect(sends).toBe(0)
    expect(sleep).not.toHaveBeenCalled()
  })

  it('floors a 0ms wait at MIN_RETRY_BACKOFF_MS so a missing Retry-After cannot tight-loop', async () => {
    const sleep = vi.fn(async () => {})
    await runLoop([429, 200], { waitMs: 0, sleep })
    expect(sleep).toHaveBeenCalledExactlyOnceWith(MIN_RETRY_BACKOFF_MS)
  })

  it('honours a server wait larger than the floor', async () => {
    const sleep = vi.fn(async () => {})
    await runLoop([429, 200], { waitMs: 1_000, sleep })
    expect(sleep).toHaveBeenCalledExactlyOnceWith(1_000)
  })

  it('returns the 429 without sleeping when already aborted', async () => {
    const sleep = vi.fn(async () => {})
    const { result, sends } = await runLoop([429, 200], {
      isAborted: () => true,
      sleep,
    })
    expect(result.status).toBe(429)
    expect(sends).toBe(0)
    expect(sleep).not.toHaveBeenCalled()
  })

  it('returns the 429 when the signal aborts during the retry sleep', async () => {
    let aborted = false
    const sleep = vi.fn((): Promise<void> => {
      aborted = true
      return Promise.resolve()
    })
    const { result, sends } = await runLoop([429, 200], {
      isAborted: () => aborted,
      sleep,
    })
    expect(result.status).toBe(429)
    // Sleep ran once; the post-sleep abort check skipped the resend.
    expect(sends).toBe(0)
    expect(sleep).toHaveBeenCalledOnce()
  })
})

describe('isIdempotentMethod', () => {
  it.each(['get', 'GET', 'Head', 'OPTIONS'])('treats %s as replayable', (m) => {
    expect(isIdempotentMethod(m)).toBe(true)
  })

  it.each(['post', 'PUT', 'patch', 'DELETE', ''])(
    'treats %s as not replayable',
    (m) => {
      expect(isIdempotentMethod(m)).toBe(false)
    },
  )
})
