/** Error utilities and user-friendly messages. */

import axios, { type AxiosError } from 'axios'
import { createLogger } from '@/lib/logger'
import type { ErrorDetail } from '@/api/types/errors'

const log = createLogger('errors')

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
        return 'Conflict: the resource was modified by another user. Please refresh and try again.'
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
  if (!isAxiosError(error)) return null
  const data = error.response?.data as
    | { error_detail?: ErrorDetail }
    | undefined
  return data?.error_detail ?? null
}
