/** Error utilities and user-friendly messages. */

import axios, { type AxiosError } from 'axios'

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
    const data = error.response?.data as { error?: string; success?: boolean } | undefined

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
      case 503:
        return 'Service temporarily unavailable. Please try again later.'
      default:
        break
    }

    if (!error.response) {
      return 'Network error. Please check your connection.'
    }

    // For 5xx, use generic message instead of leaking server internals
    return 'A server error occurred. Please try again later.'
  }

  if (error instanceof Error) {
    // Only surface messages from errors explicitly thrown by our own code.
    // Errors from unknown sources could contain backend internals.
    const msg = error.message
    if (msg && msg.length < 200 && !/^\{/.test(msg)) {
      return msg
    }
    return 'An unexpected error occurred.'
  }

  return 'An unexpected error occurred.'
}
