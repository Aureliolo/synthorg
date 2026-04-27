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
import type { ErrorDetail } from './types/errors'
import type { ApiResponse, PaginatedResponse } from './types/http'

const log = createLogger('api-client')

// Normalize: strip trailing slashes and any existing /api/v1 suffix
const RAW_BASE = import.meta.env.VITE_API_BASE_URL ?? ''
const BASE_URL = RAW_BASE.replace(/\/+$/, '').replace(/\/api\/v1\/?$/, '')

/** CSRF-protected HTTP methods that require the X-CSRF-Token header. */
const CSRF_METHODS = new Set(['post', 'put', 'patch', 'delete'])

/** Maximum transparent retries on 429 responses. */
const MAX_RATE_LIMIT_RETRIES = 2
/** Upper bound on Retry-After wait per retry so a hostile backend can't hang the UI. */
const MAX_RETRY_AFTER_MS = 5_000
/** Extra header we attach to retry requests so the interceptor can count. */
const RETRY_COUNT_HEADER = 'X-SynthOrg-Retry-Count'
/**
 * HTTP methods safe to auto-retry on 429.
 *
 * Idempotent reads (GET/HEAD/OPTIONS) can be replayed without risk.  Mutating
 * verbs (POST/PUT/PATCH/DELETE) are NOT replayed automatically: replaying a
 * decision submission or a cancellation after the server already accepted it
 * could double-apply the mutation.  Callers that need retry for a mutation
 * must attach an ``Idempotency-Key`` header; the interceptor then treats the
 * request as idempotent and retries it.
 */
const IDEMPOTENT_METHODS = new Set(['get', 'head', 'options'])
const IDEMPOTENCY_KEY_HEADER = 'idempotency-key'

interface RetriableConfig extends InternalAxiosRequestConfig {
  _rateLimitRetries?: number
}

export const apiClient = axios.create({
  baseURL: `${BASE_URL}/api/v1`,
  headers: { 'Content-Type': 'application/json' },
  timeout: 30_000,
  withCredentials: true,
  // Disable axios's built-in XSRF-cookie handling. We implement CSRF
  // ourselves in a request interceptor (reads `csrf_token`, not
  // `XSRF-TOKEN`), so axios's read of `document.cookie` on every
  // same-origin request is dead code. In jsdom under MSW it's also
  // the source of the `@mswjs/interceptors` + tough-cookie
  // "PROMISE leaking" chain flagged by `--detect-async-leaks`.
  xsrfCookieName: '',
})

/** Sentinel returned by {@link parseRetryAfterMs} when we must NOT auto-retry. */
const DO_NOT_RETRY = -1

function parseRetryAfterMs(
  headerValue: string | undefined,
  errorDetail: ErrorDetail | null | undefined,
): number {
  // Prefer the Retry-After header (RFC 9110: either delta-seconds or an
  // HTTP-date), fall back to the ``retry_after`` field from the
  // RFC 9457 envelope which is always delta-seconds.
  const raw = headerValue ?? (errorDetail?.retry_after != null
    ? String(errorDetail.retry_after)
    : undefined)
  if (!raw) return 0
  let ms: number
  const trimmed = raw.trim()
  const seconds = Number.parseInt(trimmed, 10)
  // RFC 9110 delta-seconds must be a run of digits.  Anything else
  // (e.g. ``Wed, 21 Oct 2015 07:28:00 GMT``) is treated as an HTTP-date.
  if (/^\d+$/.test(trimmed) && Number.isFinite(seconds) && seconds >= 0) {
    ms = seconds * 1000
  } else {
    const parsedDate = Date.parse(trimmed)
    if (!Number.isFinite(parsedDate)) return 0
    ms = Math.max(0, parsedDate - Date.now())
  }
  // If the server wants us to wait longer than our bounded budget,
  // surface the error to the caller instead of silently truncating;
  // a truncated retry would hit the same 429 immediately and waste
  // the backend's budget.
  if (ms > MAX_RETRY_AFTER_MS) return DO_NOT_RETRY
  return ms
}

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

// `synchronous: true` tells axios this request interceptor runs
// synchronously and must not return a Promise (header mutation is fine,
// the option is about execution mode, not purity). That lets
// `Axios.prototype._request` skip the `.then(chain[i++], chain[i++])` loop
// at `node_modules/axios/lib/core/Axios.js:196` and call `dispatchRequest`
// in-line. That loop creates a tracked Promise per chain entry that Node's
// `async_hooks` flags under Vitest's `--detect-async-leaks`; skipping it
// removes the 15 "Axios._request :196" top-frame entries from the leak
// report, though 14 re-attribute to MSW's XHR interceptor (net -1). The
// interceptor is synchronous as required: read a cookie, set a header,
// return the config -- no awaits, no Promise allocation. See
// `docs/design/web-http-adapter.md`.
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

apiClient.interceptors.response.use(
  (response: AxiosResponse) => response,
  async (
    error: AxiosError<{ error?: string; success?: boolean; error_detail?: ErrorDetail | null }>,
  ) => {
    if (error.response?.status === 401 && !IS_DEV_AUTH_BYPASS) {
      // The server clears the session cookie via Set-Cookie: Max-Age=0.
      // We only need to sync the Zustand auth state.
      import('@/stores/auth').then(({ useAuthStore }) => {
        useAuthStore.getState().handleUnauthorized()
      }).catch((importErr: unknown) => {
        log.error('Auth store cleanup failed during 401 handling:', importErr)
        // Fallback if store import fails: redirect directly
        if (window.location.pathname !== '/login' && window.location.pathname !== '/setup') {
          window.location.href = '/login'
        }
      })
    }
    // Transparent retry for 429 responses when the backend surfaces
    // a Retry-After.  Bounded so a hostile or mis-tuned server can't
    // hang the UI; surfaces the error to the caller after retries
    // exhaust so per-endpoint UX (toasts, disabled buttons) can take
    // over from there.
    const status = error.response?.status
    const config = error.config as RetriableConfig | undefined
    if (status === 429 && config) {
      const method = (config.method ?? '').toLowerCase()
      const rawHeaders = (config.headers ?? {}) as Record<string, string>
      const normalizedHeaders: Record<string, string> = {}
      for (const [k, v] of Object.entries(rawHeaders)) {
        if (typeof v === 'string') normalizedHeaders[k.toLowerCase()] = v
      }
      // An idempotency key only enables retry when it's a non-empty,
      // non-whitespace value -- an empty or blank header is a client
      // bug, not an opt-in, and must not license replaying an
      // accepted mutation.
      const idempotencyKey = normalizedHeaders[IDEMPOTENCY_KEY_HEADER]
      const isIdempotent =
        IDEMPOTENT_METHODS.has(method) ||
        (typeof idempotencyKey === 'string' && idempotencyKey.trim().length > 0)
      const retries = config._rateLimitRetries ?? 0
      if (isIdempotent && retries < MAX_RATE_LIMIT_RETRIES) {
        const waitMs = parseRetryAfterMs(
          error.response?.headers?.['retry-after'] as string | undefined,
          error.response?.data?.error_detail ?? null,
        )
        // ``waitMs === DO_NOT_RETRY`` means the server wants us to wait
        // longer than our bounded budget -- propagate the 429 to the
        // caller instead of hammering the backend with a shortened retry
        // that would just 429 again.
        if (waitMs > 0 && waitMs !== DO_NOT_RETRY) {
          config._rateLimitRetries = retries + 1
          const nextHeaders = { ...(config.headers ?? {}) } as Record<string, string>
          nextHeaders[RETRY_COUNT_HEADER] = String(retries + 1)
          const retryConfig: AxiosRequestConfig = {
            ...config,
            headers: nextHeaders,
          }
          await sleep(waitMs)
          return apiClient.request(retryConfig)
        }
      }
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
   * ``null`` when the server didn't provide one. Use this with
   * ``ErrorBanner``'s ``retryAfterSeconds`` prop to surface a live
   * "Retry in Ns" countdown to the operator.
   */
  get retryAfter(): number | null {
    return this.errorDetail?.retry_after ?? null
  }
}

/**
 * Extract data from an ApiResponse envelope.
 * Throws if the response indicates an error.
 */
export function unwrap<T>(response: AxiosResponse<ApiResponse<T>>): T {
  const body = response.data
  if (!body || typeof body !== 'object') {
    throw new ApiRequestError('Unknown API error')
  }
  if (!body.success || body.data === null || body.data === undefined) {
    const detail = 'error_detail' in body ? (body.error_detail as ErrorDetail | null) : null
    throw new ApiRequestError(body.error ?? 'Unknown API error', detail)
  }
  return body.data
}

/**
 * Validate an ApiResponse envelope without extracting data.
 * Use for endpoints that return `ApiResponse<null>` (including 204 No Content).
 */
export function unwrapVoid(response: AxiosResponse<ApiResponse<null>>): void {
  // 204 No Content: empty body is expected and valid
  if (response.status === 204) return
  const body = response.data
  if (!body || typeof body !== 'object') {
    throw new ApiRequestError('Unknown API error')
  }
  if (!body.success) {
    const detail = 'error_detail' in body ? (body.error_detail as ErrorDetail | null) : null
    throw new ApiRequestError(body.error ?? 'Unknown API error', detail)
  }
}

/** Return type for paginated API calls. */
export interface PaginatedResult<T> {
  data: T[]
  /**
   * Total matching items. ``null`` when the backend chose to skip the
   * extra ``COUNT(*)`` round-trip (repo-backed endpoints).
   */
  total: number | null
  /** Starting offset of the current page (decoded from the cursor). */
  offset: number
  /** Maximum items per page. */
  limit: number
  /** Opaque cursor for the next page; ``null`` on the final page. */
  nextCursor: string | null
  /** Whether more items follow the current page. */
  hasMore: boolean
  /** Raw pagination envelope for callers that need direct access. */
  pagination: {
    total: number | null
    offset: number
    limit: number
    next_cursor: string | null
    has_more: boolean
  }
}

/**
 * Extract data from a paginated response.
 * Validates the response structure to avoid cryptic TypeErrors.
 */
export function unwrapPaginated<T>(
  response: AxiosResponse<PaginatedResponse<T>>,
): PaginatedResult<T> {
  const body = response.data
  if (!body || typeof body !== 'object') {
    throw new ApiRequestError('Unknown API error')
  }
  if (!body.success) {
    const detail = 'error_detail' in body ? (body.error_detail as ErrorDetail | null) : null
    throw new ApiRequestError(body.error ?? 'Unknown API error', detail)
  }
  if (!body.pagination || !Array.isArray(body.data)) {
    throw new ApiRequestError('Unexpected API response format')
  }
  return {
    data: body.data,
    total: body.pagination.total,
    offset: body.pagination.offset,
    limit: body.pagination.limit,
    nextCursor: body.pagination.next_cursor,
    hasMore: body.pagination.has_more,
    pagination: {
      total: body.pagination.total,
      offset: body.pagination.offset,
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
