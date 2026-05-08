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
import type { ErrorDetail } from '@/api/types/errors'

const log = createLogger('retry-after')

/** Maximum transparent retries on 429 responses. */
export const MAX_RATE_LIMIT_RETRIES = 2

/** Upper bound on Retry-After wait per retry so a hostile backend can't hang the UI. */
export const MAX_RETRY_AFTER_MS = 5_000

/** Sentinel returned by {@link parseRetryAfterMs} when we must NOT auto-retry. */
export const DO_NOT_RETRY = -1

/**
 * Parse an RFC 9110 ``Retry-After`` header (delta-seconds or HTTP-date)
 * with the RFC 9457 envelope's ``retry_after`` field as a fallback.
 *
 * Returns ``0`` when neither source is present (caller may retry
 * immediately) or {@link DO_NOT_RETRY} when the requested wait exceeds
 * {@link MAX_RETRY_AFTER_MS} so the caller surfaces the 429 to the user
 * instead of pegging the back-off loop on a hostile delay.
 */
export function parseRetryAfterMs(
  headerValue: string | undefined,
  errorDetail: ErrorDetail | null | undefined,
): number {
  const raw =
    headerValue ??
    (errorDetail?.retry_after != null ? String(errorDetail.retry_after) : undefined)
  if (!raw) return 0
  let ms: number
  const trimmed = raw.trim()
  const seconds = Number.parseInt(trimmed, 10)
  if (/^\d+$/.test(trimmed) && Number.isFinite(seconds) && seconds >= 0) {
    ms = seconds * 1000
  } else {
    const parsedDate = Date.parse(trimmed)
    if (!Number.isFinite(parsedDate)) {
      // A malformed Retry-After header (neither delta-seconds nor a
      // valid HTTP-date) is silently treated as ``retry now`` per
      // the long-standing behaviour. Surface it as a structured warn
      // so a misconfigured backend or hostile proxy is visible in
      // ops dashboards instead of merely producing tight retry loops.
      log.warn('Malformed Retry-After header', { value: trimmed })
      return 0
    }
    ms = Math.max(0, parsedDate - Date.now())
  }
  if (ms > MAX_RETRY_AFTER_MS) return DO_NOT_RETRY
  return ms
}
