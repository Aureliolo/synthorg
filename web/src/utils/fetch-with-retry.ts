/**
 * Raw-``fetch`` wrapper that respects RFC 9110 ``Retry-After`` on 429s.
 *
 * The axios client (``@/api/client``) handles 429 retries transparently
 * through a response interceptor, but request paths that bypass axios
 * (SSE streams, navigation-time POSTs that need ``credentials: include``
 * + ``signal`` plumbing) talk to ``window.fetch`` directly. Use this
 * helper at those sites so server back-pressure stays uniform across
 * HTTP layers.
 *
 * The retry budget mirrors the axios interceptor: at most
 * {@link MAX_RATE_LIMIT_RETRIES} attempts, each capped at
 * {@link MAX_RETRY_AFTER_MS} ms of waiting. A server-requested wait
 * exceeding that cap surfaces the 429 immediately so callers can show a
 * back-pressure UI rather than pegging the loop on a hostile delay.
 *
 * Mutation requests are NOT retried by default unless the caller has
 * supplied an ``Idempotency-Key`` header or explicitly opts in via
 * ``opts.idempotent``. Replaying a non-idempotent mutation could
 * double-apply state if the server already accepted the first attempt.
 */

import {
  DO_NOT_RETRY,
  MAX_RATE_LIMIT_RETRIES,
  parseRetryAfterMs,
} from './retry-after'

const HTTP_TOO_MANY_REQUESTS = 429
const IDEMPOTENT_METHODS = new Set(['GET', 'HEAD', 'OPTIONS'])
const IDEMPOTENCY_KEY_HEADER = 'idempotency-key'

export interface FetchWithRetryOptions {
  /**
   * Treat the request as idempotent regardless of HTTP method. Default:
   * derived from the method (GET/HEAD/OPTIONS) and the presence of an
   * ``Idempotency-Key`` header.
   */
  readonly idempotent?: boolean
  /**
   * Sleep helper. Defaults to ``window.setTimeout``; tests inject a fake
   * to avoid real waits.
   */
  readonly sleep?: (ms: number) => Promise<void>
  /** Override ``window.fetch`` for testing. */
  readonly fetchImpl?: typeof fetch
}

function defaultSleep(ms: number): Promise<void> {
  return new Promise((resolve) => {
    window.setTimeout(resolve, ms)
  })
}

function methodOf(init: RequestInit | undefined): string {
  return (init?.method ?? 'GET').toUpperCase()
}

function hasIdempotencyKey(init: RequestInit | undefined): boolean {
  const headers = init?.headers
  if (!headers) return false
  if (headers instanceof Headers) {
    return headers.has(IDEMPOTENCY_KEY_HEADER)
  }
  if (Array.isArray(headers)) {
    return headers.some(([k]) => k.toLowerCase() === IDEMPOTENCY_KEY_HEADER)
  }
  return Object.keys(headers).some((k) => k.toLowerCase() === IDEMPOTENCY_KEY_HEADER)
}

function isRetriable(
  init: RequestInit | undefined,
  opts: FetchWithRetryOptions | undefined,
): boolean {
  return (
    opts?.idempotent === true ||
    (opts?.idempotent !== false &&
      (IDEMPOTENT_METHODS.has(methodOf(init)) || hasIdempotencyKey(init)))
  )
}

/**
 * ``window.fetch``-compatible wrapper that retries 429s up to
 * {@link MAX_RATE_LIMIT_RETRIES} times, honouring ``Retry-After``.
 *
 * Returns the final ``Response`` -- successful, 4xx other than 429, or
 * 429 once the retry budget is exhausted (the caller decides what to
 * do with it). Network errors propagate as raw ``fetch`` rejections.
 *
 * If ``init.signal`` aborts during a retry sleep, the helper short-
 * circuits and returns the most recent 429 response immediately so the
 * caller's cancellation is observed without waiting out the full
 * Retry-After budget.
 */
export async function fetchWithRetryAfter(
  input: RequestInfo | URL,
  init?: RequestInit,
  opts?: FetchWithRetryOptions,
): Promise<Response> {
  const fetchImpl = opts?.fetchImpl ?? fetch
  const sleep = opts?.sleep ?? defaultSleep
  const retriable = isRetriable(init, opts)
  const signal = init?.signal
  let attempt = 0
  let response = await fetchImpl(input, init)
  while (
    response.status === HTTP_TOO_MANY_REQUESTS &&
    retriable &&
    attempt < MAX_RATE_LIMIT_RETRIES
  ) {
    const waitMs = parseRetryAfterMs(
      response.headers.get('Retry-After') ?? undefined,
      null,
    )
    if (waitMs === DO_NOT_RETRY) {
      return response
    }
    if (signal?.aborted) {
      return response
    }
    await sleep(waitMs)
    if (signal?.aborted) {
      return response
    }
    attempt += 1
    response = await fetchImpl(input, init)
  }
  return response
}
