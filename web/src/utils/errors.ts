/** Error utilities and user-friendly messages. */

import axios, { type AxiosError } from 'axios'
import { createLogger } from '@/lib/logger'
import type { ErrorCode, ErrorDetail } from '@/api/types/errors'

const log = createLogger('errors')

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

    // For 4xx errors, surface the backend's validation message
    if (data?.error && typeof data.error === 'string' && status !== undefined && status < 500) {
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
      case 422:
        return 'Validation error. Please check your input.'
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
    // Only surface messages from errors explicitly thrown by our own code.
    // Errors from unknown sources could contain backend internals.
    const msg = error.message
    if (msg && msg.length < 200 && !/^\{/.test(msg)) {
      return msg
    }
    log.warn('Error message suppressed (too long or JSON-shaped):', msg?.slice(0, 300))
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
  if (isAxiosError(error)) {
    const status = error.response?.status
    if (status === 401 || status === 403) return { title: 'Authentication failed' }
    if (status === 404) return { title: 'Not found' }
    if (status === 409) return { title: 'Resource conflict' }
    if (status === 422) return { title: 'Validation failed' }
    if (status === 429) return { title: 'Rate limit reached' }
  }
  return { title: fallback }
}
