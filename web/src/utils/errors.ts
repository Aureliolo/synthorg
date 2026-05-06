/** Error utilities and user-friendly messages. */

import axios, { type AxiosError } from 'axios'
import { createLogger } from '@/lib/logger'
import { sanitizeForLog } from '@/utils/logging'
import type { ErrorCode, ErrorDetail } from '@/api/types/errors'

const log = createLogger('errors')

/**
 * Cap on prose error messages reaching the user surface. Backend validators
 * can emit very long descriptions (e.g. enumerating every invalid field on
 * a bulk import); without a ceiling a multi-kilobyte string would blow up
 * toast and banner layouts. The truncation marker keeps the message
 * recognisably incomplete so users know to ask for the full detail in
 * support.
 */
const MAX_ERROR_MESSAGE_LEN = 1000

/**
 * Duck-typed check for ``ApiRequestError`` instances without importing
 * the class. Importing ``@/api/client`` here would pull ``axios.create()``
 * into utility modules that test code mocks ``axios`` for, breaking
 * the property-based tests in ``errors.property.test.ts``.
 *
 * The class lives in ``@/api/client`` and sets ``this.name = 'ApiRequestError'``
 * in its constructor; matching on the name plus the public
 * ``errorDetail`` field is sufficient for the read-only access path.
 */
function isApiRequestError(error: unknown): error is { errorDetail: ErrorDetail | null } {
  return (
    error instanceof Error
    && error.name === 'ApiRequestError'
    && 'errorDetail' in error
  )
}

/**
 * Check if an error is an Axios error.
 */
export function isAxiosError(error: unknown): error is AxiosError {
  return axios.isAxiosError(error)
}

/**
 * Extract a user-friendly error message from any error.
 * Filters raw 5xx backend error strings to prevent leaking internal details.
 */
export function getErrorMessage(error: unknown): string {
  if (isAxiosError(error)) {
    const status = error.response?.status
    const data = error.response?.data as
      | { error?: string; error_detail?: ErrorDetail; success?: boolean }
      | undefined

    // For 4xx errors, surface the backend's validation message.
    // 422 is excluded so the structured ``error_detail.detail`` branch
    // below can prefer the field-specific RFC 9457 detail over the
    // generic ``data.error`` string when both are present.
    if (
      data?.error
      && typeof data.error === 'string'
      && status !== undefined
      && status < 500
      && status !== 422
    ) {
      return data.error
    }

    switch (status) {
      case 400:
        return 'Invalid request. Please check your input.'
      case 401:
        return 'Authentication required. Please log in.'
      case 403:
        return 'You do not have permission to perform this action.'
      case 404:
        return 'The requested resource was not found.'
      case 409:
        // 409 covers optimistic-concurrency races, duplicate-resource
        // attempts, and version-mismatch conflicts. The earlier copy
        // assumed concurrency only, which misled users when the cause
        // was a duplicate or version skew.
        return 'The resource state changed. Refresh the page and try again.'
      case 422: {
        // Prefer the structured ``error_detail.detail`` envelope (RFC
        // 9457) over the plain ``data.error`` string so the user sees
        // the specific field or rule that failed when the backend
        // sends both.
        const structuredDetail = data?.error_detail?.detail
        if (typeof structuredDetail === 'string' && structuredDetail.trim() !== '') {
          return structuredDetail
        }
        if (typeof data?.error === 'string' && data.error.trim() !== '') {
          return data.error
        }
        return 'Validation error. Please check your input.'
      }
      case 429:
        return 'Too many requests. Please try again in a moment.'
      case 502:
      case 504:
        return 'Temporary connectivity issue. Please retry shortly.'
      case 503:
        return 'The service is temporarily unavailable. Try again in a moment.'
      default:
        break
    }

    if (!error.response) {
      return 'Network error. Please check your connection.'
    }

    // For unhandled 4xx, surface a generic client error
    if (status !== undefined && status >= 400 && status < 500) {
      return `Request failed (${status}). Please check your input.`
    }

    // For other 5xx (500, 505, ...): generic message + escalation hint.
    // Avoids leaking server internals while signalling to the operator
    // whether to retry transiently or escalate.
    return (
      'An unexpected server error occurred. Please try again later or '
      + 'contact support if this persists.'
    )
  }

  if (error instanceof Error) {
    // JSON-shaped messages are suppressed because they typically carry a
    // backend stack trace or structured envelope leaked through to the
    // client. Plain prose passes through up to ``MAX_ERROR_MESSAGE_LEN``
    // characters so genuine long validation messages reach the user
    // without breaking toast / banner layouts when an upstream emits a
    // multi-kilobyte description.
    const msg = error.message
    if (msg && !/^\{/.test(msg)) {
      if (msg.length <= MAX_ERROR_MESSAGE_LEN) {
        return msg
      }
      return `${msg.slice(0, MAX_ERROR_MESSAGE_LEN)}…`
    }
    log.warn(
      'Error message suppressed (JSON-shaped)',
      sanitizeForLog({ preview: msg?.slice(0, 300) }),
    )
    return 'An unexpected error occurred. Please refresh the page or contact support if this persists.'
  }

  return 'An unexpected error occurred. Please refresh the page or contact support if this persists.'
}

/**
 * Extract structured error detail from an Axios error, if present.
 * Returns null for non-API errors or when the backend did not
 * include structured error metadata.
 */
export function getErrorDetail(error: unknown): ErrorDetail | null {
  if (isApiRequestError(error)) {
    return error.errorDetail
  }
  if (!isAxiosError(error)) return null
  const data = error.response?.data as
    | { error_detail?: ErrorDetail }
    | undefined
  return data?.error_detail ?? null
}

/**
 * Convenience accessor: pull ``error_detail.error_code`` from any
 * thrown error shape the API surface produces (Axios 4xx/5xx,
 * ``ApiRequestError`` from ``unwrap``). Returns ``null`` when the
 * envelope did not carry a structured code -- callers fall back to
 * the human-readable message in that case.
 *
 * Use this when the UI wants to discriminate on a specific code
 * (e.g. ``ERROR_CODE_PROVIDER_TIER_COVERAGE_INSUFFICIENT``) to surface
 * a targeted action instead of a generic Retry button.
 */
export function getErrorCode(error: unknown): ErrorCode | null {
  return getErrorDetail(error)?.error_code ?? null
}

/**
 * Pick a category-aware toast title prefix for CRUD failures.
 *
 * Auth / validation / conflict / rate-limit failures get specific
 * titles; everything else falls back to the caller's generic
 * ``Failed to {action} {entity}`` shape. The category derives from
 * ``error_category`` on the structured envelope, with HTTP-status
 * fallbacks for network errors that never carry a structured
 * ErrorDetail.
 *
 * Returns an object so callers can keep their existing
 * ``description: getErrorMessage(err)`` line and only swap the title.
 */
export function getCrudErrorTitle(
  error: unknown,
  fallback: string,
): { title: string } {
  // 403 (authorization) is a distinct title from 401 (authentication)
  // -- the user IS authenticated, just not allowed. Resolve the
  // status FIRST so 403 short-circuits the structured-detail switch
  // (the structured envelope's "auth" category covers both 401 and
  // 403, but at the toast-title layer the user needs to know which).
  const status = isAxiosError(error) ? error.response?.status : undefined
  if (status === 403) return { title: 'Permission denied' }
  const detail = getErrorDetail(error)
  if (detail) {
    switch (detail.error_category) {
      case 'auth':
        return { title: 'Authentication failed' }
      case 'validation':
        return { title: 'Validation failed' }
      case 'conflict':
        return { title: 'Resource conflict' }
      case 'rate_limit':
        return { title: 'Rate limit reached' }
      case 'not_found':
        return { title: 'Not found' }
      case 'budget_exhausted':
        return { title: 'Budget exhausted' }
      default:
        break
    }
  }
  if (status !== undefined) {
    if (status === 401) return { title: 'Authentication failed' }
    if (status === 404) return { title: 'Not found' }
    if (status === 409) return { title: 'Resource conflict' }
    if (status === 422) return { title: 'Validation failed' }
    if (status === 429) return { title: 'Rate limit reached' }
  }
  return { title: fallback }
}

/**
 * Group an array of per-item failure reasons by identical text so a
 * batch operation surfaces "5× version mismatch; 2× not found" instead
 * of repeating the same line for every failed id.
 *
 * Ordering is insertion order of the first occurrence, which keeps the
 * most-recent reason visible at the head when callers feed reasons in
 * the order results came back.
 */
export function formatBatchErrors(reasons: readonly string[]): string {
  if (reasons.length === 0) return ''
  const counts = new Map<string, number>()
  for (const reason of reasons) {
    counts.set(reason, (counts.get(reason) ?? 0) + 1)
  }
  return Array.from(counts.entries())
    .map(([reason, count]) => (count === 1 ? reason : `${count}× ${reason}`))
    .join('; ')
}
