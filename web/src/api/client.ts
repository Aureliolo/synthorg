/**
 * Axios client with cookie-based auth and ApiResponse envelope unwrapping.
 */

import axios, {
  type AxiosError,
  type AxiosRequestConfig,
  type AxiosResponse,
  type InternalAxiosRequestConfig,
} from 'axios'
import { createLogger } from '@/lib/logger'
import { IS_DEV_AUTH_BYPASS } from '@/utils/dev'
import { getCsrfToken } from '@/utils/csrf'
import {
  IDEMPOTENCY_KEY_HEADER,
  isIdempotentMethod,
  parseRetryAfterMs,
  retryAfterLoop,
} from '@/utils/retry-after'
import { notifyUnauthorized } from './unauthorized-handler'
import type { ErrorDetail } from './types/errors'
import type { ApiResponse, PaginatedResponse } from './types/http'

const log = createLogger('api-client')

// Normalize: strip trailing slashes and any existing /api/v1 suffix
const RAW_BASE = import.meta.env.VITE_API_BASE_URL ?? ''
const BASE_URL = RAW_BASE.replace(/\/+$/, '').replace(/\/api\/v1\/?$/, '')

/** CSRF-protected HTTP methods that require the X-CSRF-Token header. */
const CSRF_METHODS = new Set(['post', 'put', 'patch', 'delete'])

/** HTTP status that triggers the transparent retry policy. */
const HTTP_TOO_MANY_REQUESTS = 429

/**
 * Extra header attached to each replay so the backend can observe the
 * client-side retry count (the count itself is owned by {@link retryAfterLoop}).
 *
 * Idempotent reads (GET/HEAD/OPTIONS) are replayed without risk. Mutating verbs
 * are NOT replayed automatically -- replaying a decision submission after the
 * server already accepted it could double-apply the mutation -- unless the
 * caller attaches a non-blank ``Idempotency-Key`` header to opt in.
 */
const RETRY_COUNT_HEADER = 'X-SynthOrg-Retry-Count'

interface RetriableConfig extends InternalAxiosRequestConfig {
  /**
   * Marks a request issued by {@link retryAfterLoop} so the response
   * interceptor does not start a second, nested retry loop for it: the loop
   * owns retries, the inner request just reports its 429 back up.
   */
  _retryDriven?: boolean
}

export const apiClient = axios.create({
  baseURL: `${BASE_URL}/api/v1`,
  headers: { 'Content-Type': 'application/json' },
  timeout: 30_000,
  withCredentials: true,
  // Disable axios's built-in XSRF-cookie handling. We implement CSRF
  // ourselves in a request interceptor (reads `csrf_token`, not
  // `XSRF-TOKEN`), so axios's read of `document.cookie` on every
  // same-origin request is dead code. In jsdom it also routes
  // through tough-cookie's Promise wrapper, which the cookie shim
  // avoids; leaving this disabled keeps the test fast path clean.
  xsrfCookieName: '',
})


async function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => {
    window.setTimeout(resolve, ms)
  })
}

// ── Request interceptor: attach CSRF token ─────────────────
// SECURITY NOTE: Authentication uses HttpOnly session cookies set by the
// backend. The browser sends them automatically on every request (via
// withCredentials: true). The csrf_token cookie is non-HttpOnly so JS can
// read it and attach it as the X-CSRF-Token header on mutating requests.
// This eliminates the XSS token-theft attack surface that existed with
// sessionStorage-based JWT management.

// `synchronous: true` is an axios fast-path: when no async interceptor
// is registered, `Axios.prototype._request` skips the
// `.then(chain[i++], chain[i++])` loop at
// `node_modules/axios/lib/core/Axios.js:196` and calls `dispatchRequest`
// in-line. The CSRF interceptor is genuinely synchronous (read a
// cookie, set a header, return the config -- no awaits, no Promise
// allocation), so the annotation is safe.
apiClient.interceptors.request.use(
  (config) => {
    const method = (config.method ?? '').toLowerCase()
    if (CSRF_METHODS.has(method)) {
      const csrfToken = getCsrfToken()
      if (csrfToken) {
        config.headers['X-CSRF-Token'] = csrfToken
      }
    }
    return config
  },
  undefined,
  { synchronous: true },
)

// ── Response interceptor: 401 redirect + error passthrough ──

type ApiAxiosError = AxiosError<{
  error?: string
  success?: boolean
  error_detail?: ErrorDetail | null
}>

function _normalizeHeaders(headers: unknown): Record<string, string> {
  const result: Record<string, string> = {}
  if (!headers || typeof headers !== 'object') return result
  for (const [k, v] of Object.entries(headers as Record<string, unknown>)) {
    if (typeof v === 'string') result[k.toLowerCase()] = v
  }
  return result
}

/**
 * Whether a 429 retry is permitted: idempotent verbs always, or an
 * explicit non-blank ``Idempotency-Key`` header on a mutation. An
 * empty or whitespace-only header is a client bug, not an opt-in,
 * and must not license replaying an accepted mutation.
 */
function _isReplayableRequest(config: RetriableConfig): boolean {
  if (isIdempotentMethod(config.method ?? '')) return true
  const normalized = _normalizeHeaders(config.headers)
  const key = normalized[IDEMPOTENCY_KEY_HEADER]
  return typeof key === 'string' && key.trim().length > 0
}

/**
 * Wait for *response* (an AxiosResponse, possibly a 429 captured from a
 * rejected replay). ``AxiosResponse.headers`` / ``.data`` are typed non-null,
 * but coerced or faked response objects can omit them; keep the optional
 * chains so a non-standard 429 still parses.
 */
function _retryAfterMsFor(response: AxiosResponse): number {
  // eslint-disable-next-line @typescript-eslint/no-unnecessary-condition -- headers may be absent on non-standard response objects
  const retryAfter = response.headers?.['retry-after'] as string | undefined
  const data = response.data as { error_detail?: ErrorDetail | null } | undefined
  const detail = data?.error_detail ?? null
  return parseRetryAfterMs(retryAfter, detail)
}

/**
 * Transparent, bounded 429 retry for a replayable request. Drives the shared
 * {@link retryAfterLoop}: each replay re-issues the original request marked
 * ``_retryDriven`` (so this interceptor does not recurse on its own retries),
 * captures the inner 429 response from the rejection, and feeds it back to the
 * loop. Resolves with the first non-429 response, or rejects with the most
 * recent error once the budget is exhausted or the wait exceeds the ceiling.
 */
async function _retryRateLimit(
  error: ApiAxiosError,
  config: RetriableConfig,
  firstResponse: AxiosResponse,
): Promise<AxiosResponse> {
  const method = (config.method ?? '').toLowerCase()
  let lastError: ApiAxiosError = error
  let retryCount = 0
  const final = await retryAfterLoop<AxiosResponse>({
    first: firstResponse,
    send: async () => {
      const headers: Record<string, string> = {
        ...(config.headers as Record<string, string>),
      }
      headers[RETRY_COUNT_HEADER] = String(retryCount)
      const retryConfig = {
        ...config,
        headers,
        _retryDriven: true,
      } as AxiosRequestConfig & { _retryDriven: boolean }
      try {
        return await apiClient.request(retryConfig)
      } catch (e) {
        lastError = e as ApiAxiosError
        if (lastError.response) return lastError.response
        throw e
      }
    },
    getRetryAfterMs: _retryAfterMsFor,
    retriable: true,
    sleep,
    onBeforeRetry: (attempt) => {
      retryCount = attempt
      // Surfaces backend rate-limit pressure to dashboards / operators.
      // ``log.warn`` (not ``info``) because the web logger ships only
      // ``debug | warn | error`` levels; a silent absorbed 429 is a
      // signal an SRE wants to see, not a debug-stripped trace.
      log.warn('http.rate_limited', {
        retry_count: attempt,
        method,
        status: 429,
      })
    },
  })
  if (final.status !== HTTP_TOO_MANY_REQUESTS) return final
  throw lastError
}

/**
 * Whether a rejected request is an eligible 429 to retry: a 429 response on a
 * replayable request that the loop did not itself issue (the ``_retryDriven``
 * guard stops a replay from starting its own nested retry loop).
 */
function _isRetryable429(error: ApiAxiosError): boolean {
  const config = error.config as RetriableConfig | undefined
  return (
    error.response?.status === HTTP_TOO_MANY_REQUESTS &&
    config !== undefined &&
    config._retryDriven !== true &&
    _isReplayableRequest(config)
  )
}

apiClient.interceptors.response.use(
  (response: AxiosResponse) => response,
  async (error: ApiAxiosError) => {
    if (error.response?.status === 401 && !IS_DEV_AUTH_BYPASS) {
      // The server clears the session cookie via Set-Cookie: Max-Age=0.
      // We only need to sync the Zustand auth state. Routed through the
      // leaf `unauthorized-handler` module so the client has no static
      // dependency on the auth store.
      notifyUnauthorized()
    }
    // Transparent retry for 429 responses when the backend surfaces a
    // Retry-After. Bounded so a hostile or mis-tuned server can't hang
    // the UI; surfaces the error to the caller after retries exhaust
    // so per-endpoint UX (toasts, disabled buttons) can take over.
    if (_isRetryable429(error)) {
      const config = error.config as RetriableConfig
      // error.response is non-null here: _isRetryable429 checked its status.
      return _retryRateLimit(error, config, error.response as AxiosResponse)
    }
    return Promise.reject(error)
  },
)

/**
 * Error thrown when the API returns an error response.
 * Carries the structured RFC 9457 error detail when available.
 */
export class ApiRequestError extends Error {
  readonly errorDetail: ErrorDetail | null

  constructor(message: string, errorDetail: ErrorDetail | null = null) {
    super(message)
    this.name = 'ApiRequestError'
    this.errorDetail = errorDetail
  }

  /**
   * Convenience accessor for ``error_detail.retry_after``. Returns the
   * number of seconds the server suggests waiting before retrying, or
   * ``null`` when the server didn't provide one or it was malformed.
   * Use this with ``ErrorBanner``'s ``retryAfterSeconds`` prop to surface
   * a live "Retry in Ns" countdown to the operator.
   */
  get retryAfter(): number | null {
    const value = this.errorDetail?.retry_after
    return typeof value === 'number' && Number.isFinite(value) && value > 0
      ? value
      : null
  }

  /**
   * RFC 9457 ``instance`` field projected as a correlation ID. Threads
   * through every 5xx response and the server's logs so an operator
   * pasting it into a support ticket can find the originating request
   * end-to-end. Returns ``null`` when the server didn't include one
   * (e.g. a 4xx validation response that the server treats as the
   * client's fault).
   */
  get correlationId(): string | null {
    const value = this.errorDetail?.instance
    if (typeof value !== 'string') return null
    const trimmed = value.trim()
    return trimmed.length > 0 ? trimmed : null
  }
}

/**
 * Extract data from an ApiResponse envelope.
 * Throws if the response indicates an error.
 */
export function unwrap<T>(response: AxiosResponse<ApiResponse<T>>): T {
  // Axios types ``response.data`` as the declared envelope, but the server
  // can return a malformed / empty body at runtime; annotate the boundary
  // honestly so the guards below are real, not dead.
  const body = response.data as ApiResponse<T> | null | undefined
  if (!body || typeof body !== 'object') {
    throw new ApiRequestError('Unknown API error')
  }
  if (!body.success || body.data === null || body.data === undefined) {
    const detail = 'error_detail' in body ? (body.error_detail) : null
    throw new ApiRequestError(body.error ?? 'Unknown API error', detail)
  }
  return body.data
}

/**
 * Extract nullable data from an ApiResponse envelope.
 *
 * Returns `null` when the server reports success with `data: null` -- a
 * legitimate "no such resource" result (e.g. a run with no recorded
 * red-team verdict), not an error. Throws only when the envelope itself
 * reports failure (`success: false`). Use this instead of {@link unwrap}
 * for endpoints whose response type is `ApiResponse<T | null>`.
 */
export function unwrapNullable<T>(
  response: AxiosResponse<ApiResponse<T | null>>,
): T | null {
  const body = response.data as ApiResponse<T | null> | null | undefined
  if (!body || typeof body !== 'object') {
    throw new ApiRequestError('Unknown API error')
  }
  if (!body.success) {
    const detail =
      'error_detail' in body ? (body.error_detail) : null
    throw new ApiRequestError(body.error ?? 'Unknown API error', detail)
  }
  // Distinguish an explicit `data: null` (a valid "no resource" response)
  // from a success envelope that omits `data` entirely (malformed); the
  // latter is a server contract violation, not a null resource.
  if (!('data' in body)) {
    throw new ApiRequestError('Malformed API response: success envelope missing data')
  }
  return body.data ?? null
}

/**
 * Validate an ApiResponse envelope without extracting data.
 * Use for endpoints that return `ApiResponse<null>` (including 204 No Content).
 */
export function unwrapVoid(response: AxiosResponse<ApiResponse<null>>): void {
  // 204 No Content: empty body is expected and valid
  if (response.status === 204) return
  const body = response.data as ApiResponse<null> | null | undefined
  if (!body || typeof body !== 'object') {
    throw new ApiRequestError('Unknown API error')
  }
  if (!body.success) {
    const detail = 'error_detail' in body ? (body.error_detail) : null
    throw new ApiRequestError(body.error ?? 'Unknown API error', detail)
  }
}

/** Return type for paginated API calls. */
export interface PaginatedResult<T> {
  readonly data: T[]
  /** Maximum items per page. */
  readonly limit: number
  /** Opaque cursor for the next page; ``null`` on the final page. */
  readonly nextCursor: string | null
  /** Whether more items follow the current page. */
  readonly hasMore: boolean
  /** Raw pagination envelope for callers that need direct access. */
  readonly pagination: {
    readonly limit: number
    readonly next_cursor: string | null
    readonly has_more: boolean
  }
}

/**
 * Extract data from a paginated response.
 * Validates the response structure to avoid cryptic TypeErrors.
 */
export function unwrapPaginated<T>(
  response: AxiosResponse<PaginatedResponse<T>>,
): PaginatedResult<T> {
  const body = response.data as PaginatedResponse<T> | null | undefined
  if (!body || typeof body !== 'object') {
    throw new ApiRequestError('Unknown API error')
  }
  if (!body.success) {
    const detail = 'error_detail' in body ? (body.error_detail) : null
    throw new ApiRequestError(body.error ?? 'Unknown API error', detail)
  }
  // The success-branch type declares ``pagination`` always present, but a
  // malformed backend envelope could omit it; validate before dereferencing.
  // eslint-disable-next-line @typescript-eslint/no-unnecessary-condition -- runtime boundary guard against malformed envelope
  if (!body.pagination || !Array.isArray(body.data)) {
    throw new ApiRequestError('Unexpected API response format')
  }
  return {
    data: body.data,
    limit: body.pagination.limit,
    nextCursor: body.pagination.next_cursor,
    hasMore: body.pagination.has_more,
    pagination: {
      limit: body.pagination.limit,
      next_cursor: body.pagination.next_cursor,
      has_more: body.pagination.has_more,
    },
  }
}

/**
 * Maximum pages walked by ``paginateAll``. Sized for the bounded list
 * endpoints that need a single-call snapshot (settings, integration
 * health, subworkflows). With the page cap of 200 set by callers this
 * safely covers up to 10,000 items before tripping the safety stop.
 */
const PAGINATE_ALL_MAX_PAGES = 50

/**
 * Walk every page of a cursor-paginated endpoint and concatenate the
 * results. Use only when the consuming UI needs the full snapshot
 * (single-fetch, no fetchMore* UX). Stops at ``PAGINATE_ALL_MAX_PAGES``
 * iterations and throws to prevent runaway loops if the backend keeps
 * returning ``has_more=true``.
 */
export async function paginateAll<T>(
  fetchPage: (cursor: string | null) => Promise<PaginatedResult<T>>,
): Promise<T[]> {
  const collected: T[] = []
  let cursor: string | null = null
  for (let page = 0; page < PAGINATE_ALL_MAX_PAGES; page++) {
    const result = await fetchPage(cursor)
    collected.push(...result.data)
    if (!result.hasMore) {
      return collected
    }
    if (!result.nextCursor) {
      // hasMore=true but nextCursor=null is a malformed envelope;
      // the backend's PaginationMeta._validate_cursor_consistency
      // rejects this on the wire, but a misbehaving proxy or stub
      // could still smuggle it through. Throwing here surfaces the
      // bug instead of silently truncating the snapshot.
      throw new ApiRequestError(
        'Invalid paginated response: hasMore=true but nextCursor is missing',
      )
    }
    cursor = result.nextCursor
  }
  throw new ApiRequestError(
    `paginateAll exceeded ${PAGINATE_ALL_MAX_PAGES} pages without exhausting cursor`,
  )
}
