/**
 * Shared ``Retry-After`` parser + retry budget constants.
 *
 * Used by both the axios request interceptor (``@/api/client``) and the
 * raw-``fetch`` retry helper (``@/utils/fetch-with-retry``) so 429
 * handling stays consistent across HTTP layers. Returns the wait
 * duration in milliseconds, or the ``DO_NOT_RETRY`` sentinel when the
 * server's requested wait exceeds our local budget.
 */

import { createLogger } from '@/lib/logger'
import { sanitizeForLog } from '@/utils/logging'
import type { ErrorDetail } from '@/api/types/errors'

const log = createLogger('retry-after')

/** Maximum transparent retries on 429 responses. */
export const MAX_RATE_LIMIT_RETRIES = 2

/** Upper bound on Retry-After wait per retry so a hostile backend can't hang the UI. */
const MAX_RETRY_AFTER_MS = 5_000

/** Sentinel returned by {@link parseRetryAfterMs} when we must NOT auto-retry. */
export const DO_NOT_RETRY = -1

/** HTTP status that triggers the transparent retry policy. */
const HTTP_TOO_MANY_REQUESTS = 429

/** Lower-case header name carrying an explicit replay opt-in for mutations. */
export const IDEMPOTENCY_KEY_HEADER = 'idempotency-key'

/** HTTP verbs whose replay is inherently safe (no state mutation). */
const IDEMPOTENT_METHODS = new Set(['get', 'head', 'options'])

/**
 * Whether *method* names an inherently-replayable verb. Case-insensitive
 * so the axios (lower-case) and raw-``fetch`` (upper-case) call sites share
 * one predicate instead of each maintaining a cased set.
 */
export function isIdempotentMethod(method: string): boolean {
  return IDEMPOTENT_METHODS.has(method.toLowerCase())
}

/** Pick the raw header / envelope string to parse, or null when absent. */
function _resolveRetryAfterRaw(
  headerValue: string | undefined,
  errorDetail: ErrorDetail | null | undefined,
): string | null {
  if (headerValue !== undefined) return headerValue
  const fromDetail = errorDetail?.retry_after
  if (fromDetail == null) return null
  return String(fromDetail)
}

/** Parse a delta-seconds Retry-After value into milliseconds, or null. */
function _parseRetryAfterDelta(trimmed: string): number | null {
  if (!/^\d+$/.test(trimmed)) return null
  const seconds = Number.parseInt(trimmed, 10)
  return Number.isFinite(seconds) && seconds >= 0 ? seconds * 1000 : null
}

/** Parse an HTTP-date Retry-After value into milliseconds, or null. */
function _parseRetryAfterDate(trimmed: string): number | null {
  const parsedDate = Date.parse(trimmed)
  if (!Number.isFinite(parsedDate)) return null
  return Math.max(0, parsedDate - Date.now())
}

/**
 * Parse an RFC 9110 `Retry-After` header (delta-seconds or HTTP-date)
 * with the RFC 9457 envelope's `retry_after` field as a fallback.
 *
 * Returns `0` when neither source is present (caller may retry
 * immediately) or {@link DO_NOT_RETRY} when the requested wait exceeds
 * {@link MAX_RETRY_AFTER_MS} so the caller surfaces the 429 to the user
 * instead of pegging the back-off loop on a hostile delay.
 */
export function parseRetryAfterMs(
  headerValue: string | undefined,
  errorDetail: ErrorDetail | null | undefined,
): number {
  const raw = _resolveRetryAfterRaw(headerValue, errorDetail)
  if (raw === null) return 0
  const trimmed = raw.trim()
  if (trimmed === '') return 0
  const ms = _parseRetryAfterDelta(trimmed) ?? _parseRetryAfterDate(trimmed)
  if (ms === null) {
    // A malformed Retry-After header (neither delta-seconds nor a
    // valid HTTP-date) is silently treated as "retry now" per the
    // long-standing behaviour. Surface it as a structured warn so a
    // misconfigured backend or hostile proxy is visible in ops
    // dashboards instead of merely producing tight retry loops.
    log.warn('Malformed Retry-After header', sanitizeForLog({ value: trimmed }))
    return 0
  }
  if (ms > MAX_RETRY_AFTER_MS) return DO_NOT_RETRY
  return ms
}

/** A minimal HTTP response the retry loop can inspect across transports. */
export interface RetryableResponse {
  readonly status: number
}

/** Inputs to {@link retryAfterLoop}; transport-specifics arrive as callbacks. */
export interface RetryAfterLoopParams<R extends RetryableResponse> {
  /** The already-issued first response (axios: the 429 error's response). */
  readonly first: R
  /** Re-issue the request for a retry attempt; resolves to the response. */
  readonly send: () => Promise<R>
  /** Compute the wait for *response* (header source + envelope fallback vary). */
  readonly getRetryAfterMs: (response: R) => number
  /** Whether this request may be replayed at all (idempotency gate). */
  readonly retriable: boolean
  /** Sleep helper; tests inject a fake. Always invoked, even for a 0ms wait. */
  readonly sleep: (ms: number) => Promise<void>
  /** Cancellation probe (raw-fetch AbortSignal); axios omits it. */
  readonly isAborted?: () => boolean
  /** Side-effect before each retry (axios logs ``http.rate_limited``). */
  readonly onBeforeRetry?: (attempt: number, waitMs: number) => void
}

/**
 * The single 429 retry policy shared by the axios interceptor and the
 * raw-``fetch`` helper. Re-issues *send* up to {@link MAX_RATE_LIMIT_RETRIES}
 * times while the response is a 429 and the request is replayable, honouring
 * ``Retry-After`` (via *getRetryAfterMs*), the {@link DO_NOT_RETRY} ceiling,
 * and an optional abort signal.
 *
 * Returns the first non-429 response, or the most recent 429 once the budget
 * is exhausted / the wait exceeds the ceiling / the caller aborts -- the
 * consumer decides how to surface that terminal 429.
 */
/** Whether *response* still warrants a retry under the budget + replay gate. */
function _canRetry(
  response: RetryableResponse,
  retriable: boolean,
  attempt: number,
): boolean {
  return (
    response.status === HTTP_TOO_MANY_REQUESTS &&
    retriable &&
    attempt < MAX_RATE_LIMIT_RETRIES
  )
}

export async function retryAfterLoop<R extends RetryableResponse>(
  params: RetryAfterLoopParams<R>,
): Promise<R> {
  const { first, send, getRetryAfterMs, retriable, sleep } = params
  const aborted = params.isAborted ?? (() => false)
  const beforeRetry = params.onBeforeRetry ?? (() => undefined)
  let attempt = 0
  let response = first
  while (_canRetry(response, retriable, attempt)) {
    const waitMs = getRetryAfterMs(response)
    if (waitMs === DO_NOT_RETRY || aborted()) return response
    attempt += 1
    beforeRetry(attempt, waitMs)
    await sleep(waitMs)
    if (aborted()) return response
    response = await send()
  }
  return response
}
