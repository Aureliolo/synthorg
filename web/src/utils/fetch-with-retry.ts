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
  IDEMPOTENCY_KEY_HEADER,
  isIdempotentMethod,
  parseRetryAfterMs,
  retryAfterLoop,
} from './retry-after'

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

function defaultSleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    if (signal?.aborted) {
      resolve()
      return
    }
    const onAbort = (): void => {
      window.clearTimeout(timer)
      signal?.removeEventListener('abort', onAbort)
      resolve()
    }
    const timer = window.setTimeout(() => {
      signal?.removeEventListener('abort', onAbort)
      resolve()
    }, ms)
    signal?.addEventListener('abort', onAbort, { once: true })
  })
}

function methodOf(
  input: RequestInfo | URL,
  init: RequestInit | undefined,
): string {
  if (init?.method) return init.method.toUpperCase()
  return input instanceof Request ? input.method.toUpperCase() : 'GET'
}

function headersOf(
  input: RequestInfo | URL,
  init: RequestInit | undefined,
): HeadersInit | undefined {
  if (init?.headers) return init.headers
  return input instanceof Request ? input.headers : undefined
}

function hasIdempotencyKey(
  input: RequestInfo | URL,
  init: RequestInit | undefined,
): boolean {
  const headers = headersOf(input, init)
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
  input: RequestInfo | URL,
  init: RequestInit | undefined,
  opts: FetchWithRetryOptions | undefined,
): boolean {
  return (
    opts?.idempotent === true ||
    (opts?.idempotent !== false &&
      (isIdempotentMethod(methodOf(input, init)) ||
        hasIdempotencyKey(input, init)))
  )
}

/**
 * Pick the active AbortSignal: explicit `init.signal` wins, otherwise
 * the `Request` object's own signal so callers passing a pre-built
 * Request still see cancellation propagate into the retry sleep.
 */
function _resolveSignal(
  input: RequestInfo | URL,
  init: RequestInit | undefined,
): AbortSignal | undefined {
  return init?.signal ?? (input instanceof Request ? input.signal : undefined)
}

/**
 * `window.fetch`-compatible wrapper that retries 429s up to
 * {@link MAX_RATE_LIMIT_RETRIES} times, honouring `Retry-After`.
 *
 * Returns the final `Response`: successful, 4xx other than 429, or
 * 429 once the retry budget is exhausted (the caller decides what to
 * do with it). Network errors propagate as raw `fetch` rejections.
 *
 * If `init.signal` aborts during a retry sleep, the helper short-
 * circuits and returns the most recent 429 response immediately so the
 * caller's cancellation is observed without waiting out the full
 * Retry-After budget.
 *
 * The retry policy itself lives in {@link retryAfterLoop} (shared with the
 * axios interceptor); this wrapper only supplies the fetch-specific concerns:
 * `RequestInfo | URL` overloading, idempotency derivation, AbortSignal
 * integration, and the default abort-aware sleep.
 */
export async function fetchWithRetryAfter(
  input: RequestInfo | URL,
  init?: RequestInit,
  opts?: FetchWithRetryOptions,
): Promise<Response> {
  const fetchImpl = opts?.fetchImpl ?? fetch
  const signal = _resolveSignal(input, init)
  const sleep = opts?.sleep ?? ((ms: number) => defaultSleep(ms, signal))
  return retryAfterLoop<Response>({
    first: await fetchImpl(input, init),
    send: () => fetchImpl(input, init),
    getRetryAfterMs: (response) =>
      parseRetryAfterMs(response.headers.get('Retry-After') ?? undefined, null),
    retriable: isRetriable(input, init, opts),
    sleep,
    isAborted: () => signal?.aborted ?? false,
  })
}
